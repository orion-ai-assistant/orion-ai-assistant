"""One transport policy for every Hub → Router HTTP and WebSocket path.

aiohttp's Fingerprint hook runs on the actual TLS connection before any HTTP
headers/body, including a WebSocket upgrade, are written to that connection.
"""
import asyncio
import hmac
from urllib.parse import urlsplit

import aiohttp
from orion.kernel.local_security import SecurityStore, spki_fingerprint


class SPKIPin(aiohttp.Fingerprint):
    def __init__(self, fingerprint):
        if len(fingerprint) != 64 or fingerprint != fingerprint.lower():
            raise ValueError('SPKI fingerprint must be lowercase SHA-256 hex')
        super().__init__(bytes.fromhex(fingerprint))

    def check(self, transport):
        ssl_object = transport.get_extra_info('ssl_object')
        if ssl_object is None:
            raise aiohttp.ClientConnectionError('Router connection requires TLS')
        actual = bytes.fromhex(spki_fingerprint(ssl_object.getpeercert(binary_form=True)))
        if not hmac.compare_digest(self.fingerprint, actual):
            peer = transport.get_extra_info('peername') or ('unknown', 0)
            raise aiohttp.ServerFingerprintMismatch(self.fingerprint, actual, peer[0], peer[1])


def discover_router(identity, timeout=1.5):
    """Resolve addresses by UUID; ignore all mDNS trust/pin claims."""
    from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
    found = []
    import threading
    ready = threading.Event()
    class Listener(ServiceListener):
        def add_service(self, zc, kind, name):
            info = zc.get_service_info(kind, name, timeout=500)
            if info and info.properties.get(b'id', b'').decode() == identity:
                for address in info.parsed_addresses():
                    host = '[' + address + ']' if ':' in address else address
                    found.append(f'https://{host}:{info.port}')
                ready.set()
        update_service = add_service
        def remove_service(self, *args):
            pass
    with Zeroconf() as zc:
        browser = ServiceBrowser(zc, '_orion-router-tls._tcp.local.', Listener())
        ready.wait(timeout)
        browser.cancel()
    return found


class RouterConnector(aiohttp.TCPConnector):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pinned_transports = []
        self._trust_watcher = None

    async def _create_connection(self, request, traces, timeout):
        if not request.is_ssl():
            raise aiohttp.ClientConnectionError('Plain HTTP is forbidden for Router communication')
        pin = self._get_fingerprint(request)
        router_id = self._router_for(request)['id']
        protocol = await super()._create_connection(request, traces, timeout)
        self._pinned_transports.append((protocol.transport, router_id, pin.fingerprint))
        if self._trust_watcher is None:
            self._trust_watcher = asyncio.create_task(self._watch_trust())
        return protocol

    async def _watch_trust(self):
        while not self.closed:
            await asyncio.sleep(0.25)
            pins = {(router['id'], bytes.fromhex(router['fp'])) for router in SecurityStore().routers()}
            remaining = []
            for transport, router_id, pin in self._pinned_transports:
                if transport is None or transport.is_closing():
                    continue
                if (router_id, pin) not in pins:
                    transport.close()
                else:
                    remaining.append((transport, router_id, pin))
            self._pinned_transports = remaining

    async def close(self, **kwargs):
        if self._trust_watcher is not None:
            self._trust_watcher.cancel()
            await asyncio.gather(self._trust_watcher, return_exceptions=True)
        await super().close(**kwargs)
    def _router_for(self, request):
        # Select the current owner-approved pin on every connection; never TOFU.
        origin = f'https://{request.url.host}:{request.url.port}'
        for router in SecurityStore().routers():
            parsed = urlsplit(router['url'])
            if parsed.hostname == request.url.host and (parsed.port or 443) == request.url.port:
                return router
        raise aiohttp.ClientConnectionError(f'Router is not trusted: {origin}')

    def _get_fingerprint(self, request):
        return SPKIPin(self._router_for(request)['fp'])

    def _get_ssl_context(self, request):
        if request.is_ssl():
            # Fingerprint's connection hook is the validation authority. Passing
            # a Fingerprint activates aiohttp's explicit pin-validation path.
            pin = self._get_fingerprint(request)
            previous = self._ssl
            self._ssl = pin
            try:
                return super()._get_ssl_context(request)
            finally:
                self._ssl = previous
        raise aiohttp.ClientConnectionError('Plain HTTP is forbidden for Router communication')


class RouterSession(aiohttp.ClientSession):
    async def _request(self, method, url, **kwargs):
        kwargs['allow_redirects'] = False
        return await super()._request(method, url, **kwargs)


def router_session(**kwargs):
    return RouterSession(connector=RouterConnector(force_close=True, limit=100, limit_per_host=20), trust_env=False, **kwargs)


async def resolve_trusted_routers():
    store = SecurityStore()
    for router in store.routers():
        addresses = await asyncio.to_thread(discover_router, router['id'])
        if addresses:
            # Store a candidate address only. Each request still verifies its pin.
            store.update_router_address(router['id'], router['fp'], addresses[0])
    return [router['url'] for router in store.routers()]


async def verify_router(url, expected_id, expected_fp):
    uri = urlsplit(url)
    if uri.scheme != 'https' or not uri.hostname or uri.username or uri.password or uri.query or uri.fragment or uri.path not in {'', '/'}:
        raise ValueError('Router URL must be an HTTPS origin')
    pin = SPKIPin(expected_fp)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=pin), trust_env=False, timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get(url.rstrip('/') + '/api/v1/tls/identity', allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError('Router identity endpoint unavailable')
            identity = await response.json()
    if identity.get('v') != 1 or identity.get('id') != expected_id or identity.get('fp') != expected_fp:
        raise ValueError('Router identity does not match owner confirmation')
    return identity


class PinnedWebSocket:
    def __init__(self, url, headers=None):
        self.url = url
        self.headers = headers
    async def __aenter__(self):
        self.session = router_session()
        try:
            self.socket = await self.session.ws_connect(self.url, headers=self.headers, max_msg_size=16 * 1024 * 1024)
        except BaseException:
            await self.session.close(); raise
        return self
    async def __aexit__(self, *args):
        await self.socket.close()
        await self.session.close()
    async def send(self, value):
        if isinstance(value, str):
            await self.socket.send_str(value)
        else:
            await self.socket.send_bytes(value)
    async def recv(self):
        message = await self.socket.receive()
        if message.type in {aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY}:
            return message.data
        raise aiohttp.ClientConnectionError('Router WebSocket closed')
