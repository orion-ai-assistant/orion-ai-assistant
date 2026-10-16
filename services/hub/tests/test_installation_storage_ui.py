"""Real isolated headless Chrome exercises the installer storage controls."""
import asyncio
import base64
import http.server
import json
from pathlib import Path
import threading

from raw_browser_fixture import RawBrowserFixture

ROOT = Path(__file__).resolve().parents[3]


class InstallationStorageUITests(RawBrowserFixture):
    def test_move_payload_error_display_and_success_refresh(self):
        self.run_async(self.exercise())

    async def exercise(self):
        requests = []
        index = (ROOT / 'installer/app/ui/index.html').read_text(encoding='utf-8')
        panel = index[index.index('<details '):index.index('</details>') + len('</details>')]
        html = ('<!doctype html><html lang="en"><head><link rel="stylesheet" href="/ui/style.css"></head><body>'
                '<div class="container">' + panel + '</div><script src="/ui/js/locales/tr.js"></script>'
                '<script src="/ui/js/locales/en.js"></script><script src="/ui/js/i18n.js"></script>'
                '<script src="/ui/js/installation-locations.js" defer></script></body></html>')
        class Handler(http.server.BaseHTTPRequestHandler):
            def send(self, status, body, mime='application/json'):
                self.send_response(status)
                self.send_header('Content-Type', mime)
                self.end_headers()
                self.wfile.write(body.encode('utf-8'))
            def do_GET(self):
                if self.path == '/':
                    return self.send(200, html, 'text/html; charset=utf-8')
                if self.path == '/api/installation/locations':
                    return self.send(200, json.dumps({'revision': 'fixture-revision', 'locations': {
                        'models': {'path': 'C:/Orion/Models', 'available': True}}}))
                allowed = {'/ui/style.css', '/ui/js/locales/tr.js', '/ui/js/locales/en.js',
                           '/ui/js/i18n.js', '/ui/js/installation-locations.js'}
                if self.path in allowed:
                    path = ROOT / 'installer/app' / self.path.lstrip('/')
                    return self.send(200, path.read_text(encoding='utf-8'), 'text/css' if path.suffix == '.css' else 'text/javascript')
                self.send(404, '{}')
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                if self.path == '/api/installation/locations/move':
                    requests.append(value)
                    if len(requests) == 1:
                        return self.send(409, json.dumps({'detail': '<busy> consumer running'}))
                    return self.send(200, '{"phase":"completed"}')
                self.send(200, '{}')
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            async with self.manager.tab('storage-ui-fixture') as page:
                await page.navigate(f'http://127.0.0.1:{server.server_port}')
                for _ in range(100):
                    if await page.evaluate('document.querySelector("#storage-move").disabled === false'):
                        break
                    await asyncio.sleep(.05)
                self.assertFalse(await page.evaluate('document.querySelector("#storage-move").disabled'))
                await page.click('summary')
                await page.type_text('#storage-destination', 'D:/Models fixture')
                await page.click('#storage-move')
                for _ in range(100):
                    if '<busy>' in (await page.evaluate('document.querySelector("#storage-result").textContent')):
                        break
                    await asyncio.sleep(.05)
                self.assertEqual('<busy> consumer running', await page.evaluate('document.querySelector("#storage-result").textContent'))
                self.assertEqual([{'name': 'models', 'destination': 'D:/Models fixture', 'expected_revision': 'fixture-revision'}], requests)
                self.assertFalse(await page.evaluate('Boolean(document.querySelector("#storage-result busy"))'))
                await page.click('#storage-move')
                for _ in range(100):
                    if await page.evaluate('document.querySelector("#storage-result").textContent === window.t("storage_done")'):
                        break
                    await asyncio.sleep(.05)
                self.assertEqual(2, len(requests))
                self.assertTrue(await page.evaluate('document.querySelector("#storage-result").textContent === window.t("storage_done")'))
                capture = await page.send('Page.captureScreenshot', {'format': 'png'})
                (self.root / 'storage-ui.png').write_bytes(base64.b64decode(capture['data']))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
