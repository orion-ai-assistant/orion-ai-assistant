from unittest.mock import patch
from raw_browser_fixture import RawBrowserFixture
from orion.browser.manager import get_connection

class BrowserLiveTests(RawBrowserFixture):
    def test_hidden_page_shares_cookies_and_is_cleaned_without_closing_browser(self):
        self.run_async(self.exercise())

    async def exercise(self):
        async with self.manager.tab('visible-fixture') as page:
            await page.send('Network.setCookie', {'name': 'orion_fixture', 'value': 'shared',
                                                 'domain': 'example.com', 'path': '/'})
            visible = page.target_id
            window = await page.connection.send('Browser.getWindowForTarget', {'targetId': visible})
        async with self.manager.tab('google_ai', background=True) as page:
            hidden = page.target_id
            cookies = await page.send('Network.getCookies', {'urls': ['https://example.com/']})
            self.assertTrue(any(c['name'] == 'orion_fixture' for c in cookies['cookies']))
            frame = (await page.send('Page.getFrameTree'))['frameTree']['frame']['id']
            await page.send('Page.setDocumentContent', {'frameId': frame, 'html': '<p id="answer">Hidden answer</p><input id="entry">'})
            self.assertEqual(await page.evaluate('document.querySelector("#answer").innerText'), 'Hidden answer')
            await page.type_text('#entry', 'test input')
            self.assertEqual(await page.evaluate('document.querySelector("#entry").value'), 'test input')
            current = await page.connection.send('Browser.getWindowForTarget', {'targetId': visible})
            self.assertEqual(current, window)
            await page.require_verification()
        await self.manager.resume()
        _, endpoint = await self.manager.endpoint()
        connection = await get_connection(endpoint)
        targets = (await connection.send('Target.getTargets'))['targetInfos']
        self.assertNotIn(hidden, [t['targetId'] for t in targets])
        self.assertIn(visible, [t['targetId'] for t in targets])
        from orion.browser.shutdown import close_all
        with patch('orion.browser.shutdown.browser_root', return_value=self.root):
            self.assertEqual(await close_all(), [])
        (self.root / '.stopping').unlink()
        connection = await get_connection(endpoint, authorize=True)
        self.assertIn('product', await connection.send('Browser.getVersion'))
