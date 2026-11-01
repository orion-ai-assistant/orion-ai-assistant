"""The window's relay to another Hub: pairing like the phone, certificate pinning, and HTTP/SSE/WS relay."""
import asyncio
import datetime
import ssl
import threading
import uuid

import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from orion.api import relay as relay_module
from orion.api.mobile_security import pairing_token
from orion.api.relay import RelayGateway
from orion.kernel import remote_hubs
from orion.kernel.local_security import spki_fingerprint

LINK = 'https://orion-ai-assistant.github.io/p#'
WINDOW = 'http://localhost:8090'
CHANGE = {'X-Orion-Relay': '1', 'Origin': WINDOW}


def certificate(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'fake hub')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    (directory / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    return spki_fingerprint(cert.public_bytes(serialization.Encoding.DER))


class FakeHub:
    """Just enough of a main Hub's mobile listener."""
    def __init__(self, directory):
        self.fp = certificate(directory)
        self.id = str(uuid.uuid4())
        self.code = 'Y4DF23XL'
        self.seen = []
        self.pair_requests = []
        app = web.Application()
        app.router.add_get('/api/v1/tls/identity', self.identity)
        app.router.add_post('/api/v1/pair/claim', self.claim)
        app.router.add_post('/api/v1/pair/request', self.request)
        app.router.add_delete('/api/v1/devices/self', self.forget)
        app.router.add_route('*', '/api/v1/echo', self.echo)
        app.router.add_get('/api/v1/stream', self.stream)
        app.router.add_get('/api/v1/socket', self.socket)
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(directory / 'cert.pem', directory / 'key.pem')
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def start():
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, '127.0.0.1', 0, ssl_context=context)
            await site.start()
            self.port = site._server.sockets[0].getsockname()[1]
            ready.set()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(start(), self.loop)
        ready.wait(10)
        self.url = f'https://127.0.0.1:{self.port}'

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)

    def link(self, code=None, fp=None):
        # A real link names a LAN address; the test Hub listens on loopback (see the patched parse_link).
        return LINK + pairing_token('192.168.50.7', self.port, code or self.code, fp or self.fp)

    async def identity(self, request):
        return web.json_response({'v': 1, 'id': self.id, 'name': 'Hiro', 'fp': self.fp})

    async def claim(self, request):
        body = await request.json()
        if body.get('code') != self.code:
            return web.json_response({'detail': 'pair_code_invalid'}, status=403)
        self.code = None
        self.seen.append(('claim', body))
        return web.json_response({'hub_id': self.id, 'fp': self.fp, 'hub_name': 'Hiro',
                                  'device_id': 'device-1', 'device_token': 'orion_device_secret'})

    async def request(self, request):
        self.pair_requests.append(await request.json())
        return web.json_response({'ok': True, 'expires_at': 1})

    async def forget(self, request):
        self.seen.append(('forget', request.headers.get('X-Device-Token')))
        return web.json_response({'ok': True})

    async def echo(self, request):
        self.seen.append(('echo', dict(request.headers), await request.read()))
        return web.json_response({'method': request.method, 'query': request.query_string},
                                 headers={'Set-Cookie': 'remote=1', 'Access-Control-Allow-Origin': '*'})

    async def stream(self, request):
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
        await response.prepare(request)
        for index in range(3):
            await response.write(f'data: {index}\n\n'.encode())
            await asyncio.sleep(0.01)
        return response

    async def socket(self, request):
        self.seen.append(('socket', request.headers.get('Authorization'), request.headers.get('X-Device-Token')))
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        async for message in socket:
            await socket.send_str('echo:' + message.data)
        return socket


class Directory:
    def __init__(self, hubs=()):
        self.hubs = list(hubs)

    async def list(self):
        return self.hubs


@pytest.fixture
def hub(tmp_path):
    fake = FakeHub(tmp_path)
    yield fake
    fake.stop()


@pytest.fixture
def gateway(tmp_path, monkeypatch, hub):
    parse = remote_hubs.parse_link

    def loopback_link(text):
        link = parse(text)
        return {**link, 'url': link['url'].replace('192.168.50.7', '127.0.0.1')}
    monkeypatch.setattr(remote_hubs, 'parse_link', loopback_link)
    monkeypatch.setattr('orion.api.hub_directory.local_identity', lambda: {'id': str(uuid.uuid4()), 'name': 'Laptop', 'fp': '0' * 64})
    inner = Starlette()
    inner.add_route('/other', lambda request: PlainTextResponse('inner'))
    store = remote_hubs.RemoteHubStore(tmp_path / 'remote.sqlite3')
    relay = RelayGateway(inner, store=store, directory=Directory())
    with TestClient(relay, base_url='http://localhost:8910') as client:
        client.relay = relay
        client.store = store
        yield client


def pair(client, hub):
    response = client.post('/api/v1/remote/pair', json={'link': hub.link()}, headers=CHANGE)
    assert response.status_code == 200, response.text
    return response.json()


def test_links_read_back_like_the_phone_app():
    fp = 'ab' * 32
    link = remote_hubs.parse_link(LINK + pairing_token('192.168.1.106', 8443, 'Y4DF23XL', fp))
    assert link == {'url': 'https://192.168.1.106:8443', 'code': 'Y4DF23XL', 'fp_prefix': fp[:32]}
    token = pairing_token('192.168.1.106', 8443, 'Y4DF23XL', fp)
    assert remote_hubs.parse_link('orion://pair?l=' + token) == link
    assert remote_hubs.parse_link(token) == link
    for bad in ['https://example.com/p#' + token, LINK + token[1:], LINK, 'orion://pair?l=AAAA',
                LINK + pairing_token('8.8.8.8', 8443, 'Y4DF23XL', fp),
                LINK + pairing_token('127.0.0.1', 8443, 'Y4DF23XL', fp)]:
        with pytest.raises(ValueError):
            remote_hubs.parse_link(bad)


def test_pairs_like_a_phone_and_never_shows_the_device_token(gateway, hub):
    paired = pair(gateway, hub)
    assert paired['id'] == hub.id and paired['name'] == 'Hiro' and paired['fp'] == hub.fp
    assert 'device_token' not in gateway.get('/api/v1/remote/hubs').text
    assert gateway.store.get(hub.id)['device_token'] == 'orion_device_secret'
    assert hub.seen[0][1]['platform'] == 'windows'
    # The code is used up: the same link again says so.
    again = gateway.post('/api/v1/remote/pair', json={'link': hub.link(code='Y4DF23XL')}, headers=CHANGE)
    assert again.status_code == 403 and again.json()['detail'] == 'pair_code_invalid'


def test_a_link_with_another_certificate_pairs_nothing(gateway, hub):
    response = gateway.post('/api/v1/remote/pair', json={'link': hub.link(fp='cd' * 32)}, headers=CHANGE)
    assert response.status_code == 502
    assert gateway.store.hubs() == [] and hub.code == 'Y4DF23XL'


def test_other_sites_cannot_pair_or_relay(gateway, hub):
    assert gateway.post('/api/v1/remote/pair', json={'link': hub.link()}).status_code == 403
    assert gateway.post('/api/v1/remote/pair', json={'link': hub.link()},
                        headers={'X-Orion-Relay': '1', 'Origin': 'https://evil.example'}).status_code == 403
    pair(gateway, hub)
    assert gateway.get(f'/remote/{hub.id}/api/v1/echo', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert gateway.get(f'/remote/{hub.id}/api/v1/echo', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    window = {'Origin': 'http://127.0.0.1:8090', 'Sec-Fetch-Site': 'cross-site'}
    assert gateway.get(f'/remote/{hub.id}/api/v1/echo', headers=window).status_code == 200
    preflight = gateway.options(f'/remote/{hub.id}/api/v1/echo', headers={
        'Origin': WINDOW, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization'})
    assert preflight.headers['access-control-allow-origin'] == WINDOW
    assert gateway.get('/other').text == 'inner'


def test_relays_requests_with_the_device_token_and_the_window_account(gateway, hub):
    pair(gateway, hub)
    response = gateway.post(f'/remote/{hub.id}/api/v1/echo?x=1', content=b'{"a":1}', headers={
        'Origin': WINDOW, 'Authorization': 'Bearer account', 'Content-Type': 'application/json',
        'Cookie': 'orion_owner_local=secret', 'X-Device-Token': 'forged', 'X-Forwarded-For': '1.2.3.4'})
    assert response.status_code == 200 and response.json() == {'method': 'POST', 'query': 'x=1'}
    assert response.headers['access-control-allow-origin'] == WINDOW
    assert 'set-cookie' not in response.headers
    _, headers, body = hub.seen[-1]
    assert body == b'{"a":1}'
    assert headers['X-Device-Token'] == 'orion_device_secret' and headers['Authorization'] == 'Bearer account'
    assert not {'Cookie', 'Origin', 'X-Forwarded-For'} & set(headers)
    assert gateway.get(f'/remote/{uuid.uuid4()}/api/v1/echo').status_code == 404


def test_relays_event_streams_and_websockets(gateway, hub):
    pair(gateway, hub)
    with gateway.stream('GET', f'/remote/{hub.id}/api/v1/stream') as response:
        assert b''.join(response.iter_bytes()) == b'data: 0\n\ndata: 1\n\ndata: 2\n\n'
    with gateway.websocket_connect(f'/remote/{hub.id}/api/v1/socket?token=account') as socket:
        socket.send_text('hi')
        assert socket.receive_text() == 'echo:hi'
    assert ('socket', 'Bearer account', 'orion_device_secret') in hub.seen


def test_finds_the_hub_again_when_its_address_changes(gateway, hub):
    pair(gateway, hub)
    gateway.store.move(hub.id, 'https://127.0.0.1:9')
    gateway.relay._directory.hubs = [{'id': hub.id, 'name': 'Hiro', 'url': hub.url + '/', 'fp': hub.fp}]
    assert gateway.get(f'/remote/{hub.id}/api/v1/echo').status_code == 200
    assert gateway.store.get(hub.id)['url'] == hub.url


def test_asks_only_hubs_on_the_network_to_show_a_qr(gateway, hub):
    assert gateway.post('/api/v1/remote/request', json={'id': hub.id}, headers=CHANGE).status_code == 404
    gateway.relay._directory.hubs = [{'id': hub.id, 'name': 'Hiro', 'url': hub.url + '/', 'fp': hub.fp}]
    assert gateway.post('/api/v1/remote/request', json={'id': hub.id}, headers=CHANGE).status_code == 200
    assert hub.pair_requests[0]['platform'] == 'windows'
    found = gateway.get('/api/v1/remote/discover').json()['hubs']
    assert found == [{'id': hub.id, 'name': 'Hiro', 'paired': False}]


def test_forgetting_tells_the_main_hub(gateway, hub):
    pair(gateway, hub)
    assert gateway.delete(f'/api/v1/remote/hubs/{hub.id}', headers=CHANGE).status_code == 200
    assert ('forget', 'orion_device_secret') in hub.seen and gateway.store.hubs() == []


def test_the_light_hub_serves_only_the_window_and_the_relay(tmp_path, monkeypatch):
    from orion.api.relay_server import create_relay_app
    store_class = remote_hubs.RemoteHubStore
    monkeypatch.setattr('orion.api.hub_directory.local_identity', lambda: {'id': 'laptop', 'name': 'Laptop', 'fp': '0' * 64})
    monkeypatch.setattr(relay_module.remote_hubs, 'RemoteHubStore', lambda path=None: store_class(tmp_path / 'r.sqlite3'))
    app = create_relay_app()
    with TestClient(app, base_url='http://127.0.0.1:8910', client=('127.0.0.1', 50000)) as client:
        assert client.get('/health').json()['mode'] == 'relay'
        assert client.get('/api/v1/discovery/self').json()['mode'] == 'relay'
        assert client.get('/api/v1/remote/hubs').json() == {'hubs': []}
        # Nothing of a full Hub: no accounts, chats or pairing of phones here.
        for path in ['/api/v1/auth/login', '/api/v1/chats', '/api/v1/pair/claim']:
            assert client.get(path).status_code == 404
    with TestClient(app, base_url='http://127.0.0.1:8910', client=('192.168.1.20', 50000)) as client:
        assert client.get('/api/v1/remote/hubs').status_code == 403
    with TestClient(app, base_url='http://evil.example', client=('127.0.0.1', 50000)) as client:
        assert client.get('/api/v1/remote/hubs').status_code == 403


def test_finds_hubs_by_the_phones_advertisement():
    # The mobile listener's advertisement names no API path (orion.api.mobile_server).
    assert RelayGateway(None).directory.api_path is None
