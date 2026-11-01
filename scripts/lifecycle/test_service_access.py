"""Local model services answer the Router and their own page, not other web pages."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.shared import service_access

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ['services/tts/api.py', 'services/stt/whisper/api.py',
            'services/llm/llama-cpp/api.py', 'services/embedding/llama-cpp-embed/api.py']


async def speech(request):
    return PlainTextResponse('audio')


async def stream(websocket):
    await websocket.accept()
    await websocket.send_text('ready')
    await websocket.close()


def client(host='127.0.0.1:8810'):
    app = Starlette(routes=[Route('/v1/audio/speech', speech, methods=['GET', 'POST']),
                            WebSocketRoute('/v1/stream', stream)])
    app.add_middleware(service_access.BrowserGuard)
    return TestClient(app, base_url=f'http://{host}')


class BindHostTests(unittest.TestCase):
    def test_default_is_this_pc_only(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(service_access.bind_host(), '127.0.0.1')
        with patch.dict(os.environ, {'ORION_SERVICES_BIND_HOST': ' '}):
            self.assertEqual(service_access.bind_host(), '127.0.0.1')

    def test_one_setting_opens_every_service(self):
        with patch.dict(os.environ, {'ORION_SERVICES_BIND_HOST': '0.0.0.0'}):
            self.assertEqual(service_access.bind_host(), '0.0.0.0')

    def test_every_service_listens_where_the_setting_says(self):
        for name in SERVICES:
            source = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('bind_host()', source, name)
            self.assertNotIn('0.0.0.0', source, name)
        stt = (ROOT / 'services/stt/whisper/api.py').read_text(encoding='utf-8')
        self.assertNotIn('CORSMiddleware', stt)
        for name in SERVICES[2:]:
            source = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('"--cors-origins", own_origins(port)', source)
            self.assertIn('"--no-slots"', source)

    def test_docker_publishes_on_the_same_setting(self):
        files = [path for folder in ('tts', 'stt/whisper', 'llm/llama-cpp', 'embedding/llama-cpp-embed')
                 for path in (ROOT / 'services' / folder).glob('docker-compose*.yml')]
        self.assertEqual(len(files), 13)
        for path in files:
            text = path.read_text(encoding='utf-8')
            self.assertIn('"${ORION_SERVICES_BIND_HOST:-127.0.0.1}:${', text, path)

    def test_own_origins_list_only_the_service_itself(self):
        self.assertEqual(service_access.own_origins('8085'), 'http://127.0.0.1:8085,http://localhost:8085')


class BrowserGuardTests(unittest.TestCase):
    def test_router_requests_pass(self):
        for host in ('127.0.0.1:8810', 'localhost:8810', '[::1]:8810', 'tts:8810', '192.168.1.20:8810'):
            with self.subTest(host=host):
                response = client(host).post('/v1/audio/speech', content=b'{}')
                self.assertEqual(response.status_code, 200)

    def test_dns_rebinding_name_is_rejected(self):
        response = client('attacker.example.com:8810').get('/v1/audio/speech')
        self.assertEqual(response.status_code, 403)

    def test_other_web_pages_are_rejected(self):
        for origin in ('https://attacker.example', 'http://localhost:8090', 'http://127.0.0.1:20128', 'null'):
            with self.subTest(origin=origin):
                response = client().post('/v1/audio/speech', headers={'Origin': origin})
                self.assertEqual(response.status_code, 403)

    def test_service_dashboard_on_its_own_origin_passes(self):
        response = client().post('/v1/audio/speech', headers={'Origin': 'http://127.0.0.1:8810'})
        self.assertEqual(response.status_code, 200)

    def test_websocket_from_another_page_is_refused(self):
        with self.assertRaises(WebSocketDisconnect):
            with client().websocket_connect('/v1/stream', headers={'Origin': 'https://attacker.example'}) as ws:
                ws.receive_text()
        with client().websocket_connect('/v1/stream') as ws:
            self.assertEqual(ws.receive_text(), 'ready')


if __name__ == '__main__':
    unittest.main()
