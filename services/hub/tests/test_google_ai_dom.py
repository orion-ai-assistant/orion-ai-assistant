"""Browser-backed fixtures distilled from the user's September 2026 AI Mode DOM."""

import os
import tempfile
import unittest

from orion.worker.tools.google_ai.tools import ANSWER_SELECTOR, SNAPSHOT, _sources


class GoogleDOMTests(unittest.TestCase):
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

    def snapshot(self, html):
        page = self.browser.new_page()
        try:
            page.set_content(html)
            return page.evaluate(SNAPSHOT, ANSWER_SELECTOR)
        finally:
            page.close()

    def test_session_cookies_survive_browser_restart_with_restore(self):
        # No Google requests, actual account data, or exported cookie files.
        with tempfile.TemporaryDirectory() as directory:
            options = dict(headless=True, channel='chromium', chromium_sandbox=True,
                           args=['--restore-last-session'])
            context = self.playwright.chromium.launch_persistent_context(directory, **options)
            try:
                context.add_cookies([{'name': 'synthetic_session', 'value': 'test-only',
                                     'domain': 'example.com', 'path': '/'}])
                context.pages[0].goto('about:blank')
            finally:
                context.close()
            context = self.playwright.chromium.launch_persistent_context(directory, **options)
            try:
                self.assertTrue(any(c['name'] == 'synthetic_session' and c['expires'] == -1
                                    for c in context.cookies()))
                self.assertTrue(all(p.url == 'about:blank' for p in context.pages))
            finally:
                context.close()

    def test_current_main_col_excludes_hidden_ui_and_reads_citation_record(self):
        data = self.snapshot('''<nav>Google navigation</nav>
          <div data-container-id="main-col">
            <div data-complete="true"><mark><strong><span data-subtree="aimfl">Ve aleyküm selâm!</span></strong> Size de huzur dilerim.</mark></div>
            <div style="display:contents"><ul><li data-complete="true">Genel bilgi ve araştırmalar</li></ul></div>
            <table><tr><td><span role="button">Posidonia australis</span></td><td>200 km²</td></tr></table>
            <div style="display:none">HIDDEN PRIVATE METADATA</div>
            <div class="invisible">CSS HIDDEN</div><style>.invisible {display:none}</style>
            <span><a href="https://www.google.com/goto?url=opaque">Diyanet</a>
              <button data-icl-uuid="citation-1">İlgili sonuçlar</button>
              <!--TgQPHd|||[[&quot;citation-1&quot;,null,null,0,&quot;favicon&quot;,&quot;Diyanet&quot;,1,&quot;https://example.org/source.pdf&quot;]]-->
              <!--TgQPHd|||[["unrelated",null,null,0,"favicon","Ignore",1,"https://example.org/unrelated"]]-->
            </span>
            <div jsaction="aimRenderComplete:finish" data-complete="true">
              <div aria-busy="true">Paylaşım işlemi</div><button>Metni kopyala</button><span>Bu yanıt hakkında</span>
            </div>
          </div>''')
        self.assertTrue(data['root_found'])
        self.assertTrue(data['completed'])
        self.assertFalse(data['busy'])
        self.assertIn('**Ve aleyküm selâm\\!** Size de huzur dilerim.', data['answer'])
        self.assertIn('Genel bilgi ve araştırmalar', data['answer'])
        self.assertIn('| Posidonia australis | 200 km² |', data['answer'])
        for hidden in ('HIDDEN', 'PRIVATE', 'METADATA', 'Paylaşım', 'Metni kopyala', 'Bu yanıt hakkında', 'İlgili sonuçlar', 'navigation'):
            self.assertNotIn(hidden, data['answer'])
        self.assertEqual(_sources(data['links']), [{'url': 'https://example.org/source.pdf', 'title': 'Diyanet'}])

    def test_latest_response_short_answer_and_visible_streaming(self):
        data = self.snapshot('''<div data-container-id="main-col">Old long answer from a previous question</div>
          <div data-container-id="main-col"><div data-complete="false">Ankara.</div>
          <div hidden aria-busy="true">hidden spinner</div></div>''')
        self.assertEqual(data['answer'], 'Ankara.')
        self.assertTrue(data['busy'])
        data = self.snapshot('<div data-subtree="aimc">Ankara.<div hidden aria-busy="true">spinner</div></div>')
        self.assertEqual(data['answer'], 'Ankara.')
        self.assertFalse(data['busy'])

    def test_generic_search_results_are_never_an_ai_answer(self):
        data = self.snapshot('<main><h1>Search results</h1><p>A very plausible answer in a normal search snippet.</p></main>')
        self.assertFalse(data['root_found'])
        self.assertEqual(data['answer'], '')

    def test_inline_links_and_citations_keep_their_position(self):
        data = self.snapshot('''<div data-container-id="main-col">
          <p>First claim.<button data-icl-uuid="first">Source chip</button></p>
          <p><a href="https://example.org/biography"><strong>Biography</strong></a>: second claim.</p>
          <p><a href="javascript:alert(1)">Unsafe label</a></p>
          <div hidden><!--TgQPHd|||[["first",null,null,0,"favicon","Archive",1,"https://example.org/archive"]]--></div>
          <button>Share</button></div>''')
        answer = data['answer']
        self.assertIn('First claim. [Archive](<https://example.org/archive>)', answer)
        self.assertIn('[**Biography**](<https://example.org/biography>): second claim.', answer)
        self.assertLess(answer.index('[Archive]'), answer.index('[**Biography**]'))
        self.assertNotIn('javascript:', answer)
        self.assertNotIn('Share', answer)
        self.assertEqual(len(_sources(data['links'])), 2)

    def test_nested_formatting_is_applied_once_without_rewriting_words(self):
        data = self.snapshot('''<div data-container-id="main-col"><p>
          <strong><b><em><i>Armillaria ostoyae</i></em></b></strong>
          and <span style="font-weight:700"><strong>Pando</strong></span>.
          Literal ***** stays literal.
        </p></div>''')
        self.assertIn('***Armillaria ostoyae***', data['answer'])
        self.assertIn('**Pando**', data['answer'])
        self.assertNotIn('*****Armillaria', data['answer'])
        self.assertIn(r'Literal \*\*\*\*\* stays literal.', data['answer'])

    def test_markdown_headings_emphasis_nested_lists_and_tables(self):
        data = self.snapshot('''<div data-container-id="main-col">
          <h2>1. En Büyük Hayvan</h2>
          <p><strong>Mavi balina</strong> ve <em>Balaenoptera musculus</em>.</p>
          <ul><li><div></div></li><li><b>Boyut:</b> 33 metre<ul><li>Denizde yaşar</li></ul></li><li>Ağırlık: 180 ton</li></ul>
          <ol start="3"><li>Üçüncü</li><li>Dördüncü</li></ol>
          <div role="heading" aria-level="3">Karşılaştırma</div>
          <table><tr><th>Canlı</th><th>Alan</th></tr><tr><td><strong>Deniz çayırı</strong></td><td>200 km²</td></tr></table>
          <p><span style="font-weight:700">Önemli</span>: &lt;script&gt; ve [sahte](javascript:test).</p>
          <div hidden><h1>Gizli başlık</h1></div></div>''')
        answer = data['answer']
        for expected in ('## 1. En Büyük Hayvan', '**Mavi balina**', '*Balaenoptera musculus*',
                         '- **Boyut:** 33 metre', '\n  - Denizde yaşar', '3. Üçüncü', '4. Dördüncü',
                         '### Karşılaştırma', '| Canlı | Alan |\n| --- | --- |',
                         '| **Deniz çayırı** | 200 km² |', '**Önemli**', '&lt;script&gt;', '\\[sahte\\]'):
            self.assertIn(expected, answer)
        self.assertNotIn('Gizli başlık', answer)
        self.assertNotIn('<script>', answer)
        self.assertNotIn('\n-\n', answer)
