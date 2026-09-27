"""Real-browser coverage for safe Markdown rendering and streaming snapshots."""
import os
from pathlib import Path
import unittest


class MarkdownUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '0')
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional Playwright dependency is not installed')
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not os.path.exists(cls.playwright.chromium.executable_path):
            raise unittest.SkipTest('Full Chromium is not installed')
        cls.browser = cls.playwright.chromium.launch(headless=True, channel='chromium', chromium_sandbox=True)
        cls.addClassCleanup(cls.browser.close)
        cls.ui = Path(__file__).resolve().parents[1] / 'src/orion/api/ui'

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 900, 'height': 800})
        self.addCleanup(self.page.close)
        self.page.set_content('<main id="chat-area"></main>')
        self.page.add_style_tag(path=str(self.ui / 'css/markdown.css'))
        for file in ('vendor/marked.js', 'vendor/purify.js', 'markdown.js', 'ui.js'):
            self.page.add_script_tag(path=str(self.ui / 'js' / file))
        self.page.evaluate('''() => {
            window.AppState = {currentChatId:'test', getGenerationMetrics: () => ({})};
            UI.setStopButtonVisible = () => {};
            window.ToolsUI = {renderActivity: (parent, item) => {
                const card = document.createElement('details'); card.className='tool-activity';
                card.textContent=item.call_id; parent.appendChild(card);
            }};
        }''')

    def test_history_formatting_and_untrusted_html(self):
        source = ('## Heading\n\n**Bold** and *italic*\n\n- Item\n\n'
                  '| A | B |\n| --- | --- |\n| 1 | 2 |\n\n'
                  '```python\nprint("hello")\n```\n\n'
                  '[Source](https://example.org) [Bad](javascript:alert(1))\n\n'
                  '<img src=x onerror="window.pwned=1"><svg onload="window.pwned=1"></svg>')
        self.page.evaluate('s => UI.appendStaticBotMessage(s)', source)
        self.assertEqual(self.page.locator('h2').inner_text(), 'Heading')
        self.assertEqual(self.page.locator('strong').inner_text(), 'Bold')
        self.assertEqual(self.page.locator('li').inner_text(), 'Item')
        self.assertEqual(self.page.locator('.markdown-table-scroll td').count(), 2)
        self.assertIn('print("hello")', self.page.locator('pre code').inner_text())
        self.assertEqual(self.page.locator('img,svg,[onerror],a[href^="javascript:"]').count(), 0)
        self.assertIsNone(self.page.evaluate('window.pwned'))
        self.assertEqual(self.page.locator('a[href]').get_attribute('rel'), 'noopener noreferrer')

    def test_stream_revisions_use_raw_offsets_and_keep_tool_cards(self):
        self.page.evaluate(r'''() => {
            UI.appendToken('test', '**Before**\n\n');
            const state = UI._chatDivs.test;
            ToolsUI.renderActivity(state.botDiv, {call_id:'call-1'});
            state.currentContentNode = null;
            UI.appendToken('test', '## Par');
            UI.replaceMessageContent('test', '**Before**\n\n## Final\n\n***Name***');
            UI.finishGeneration('test', true);
        }''')
        self.assertEqual(self.page.locator('h2').inner_text(), 'Final')
        self.assertEqual(self.page.locator('.tool-activity').count(), 1)
        self.assertEqual(self.page.locator('.markdown-content').count(), 2)
        self.assertEqual(self.page.locator('strong em, em strong').inner_text(), 'Name')
        self.assertEqual(self.page.evaluate('UI._chatDivs.test.contentNodes.map(n => n.markdownSource).join("")'),
                         '**Before**\n\n## Final\n\n***Name***')
        self.page.evaluate('''() => UI.appendStaticBotMessage('', null, null, null,
            [{call_id:'call-1'}], [{type:'content',content:'**Before**\\n\\n'},
            {type:'tool',call_id:'call-1'}, {type:'content',content:'## Final\\n\\n***Name***'}])''')
        self.assertEqual(self.page.locator('h2').count(), 2)
        self.assertEqual(self.page.locator('.tool-activity').count(), 2)

    def test_pending_stream_is_rendered_without_finishing_generation(self):
        self.page.evaluate("UI.appendToken('test', '## Live\\n\\n**hello**')")
        self.page.wait_for_selector('h2')
        self.assertEqual(self.page.locator('strong').inner_text(), 'hello')
