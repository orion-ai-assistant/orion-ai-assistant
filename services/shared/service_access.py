"""Who may reach the local model services (TTS, STT, LLM, embeddings).

Only the Router calls them. One setting decides where they listen:
ORION_SERVICES_BIND_HOST in services/.env.global, overridden per machine in
<data folder>/services/.env.global.local. 127.0.0.1 (the default) lets only
programs on this PC in; 0.0.0.0 also lets in other devices, for a Router that
runs on another computer.

Listening on 127.0.0.1 does not keep out web pages: a page the user opens can
still send requests to 127.0.0.1. BrowserGuard turns those away, so the API
answers programs (the Router) and the service's own dashboard page only.
"""
import ipaddress
import os
from urllib.parse import urlsplit

BIND_HOST_KEY = 'ORION_SERVICES_BIND_HOST'


def bind_host():
    return os.getenv(BIND_HOST_KEY, '').strip() or '127.0.0.1'


def own_origins(port):
    """The origins of a service's own pages, for servers that take a CORS list."""
    return f'http://127.0.0.1:{port},http://localhost:{port}'


def _trusted_host(host):
    """A DNS rebinding page reaches us under the attacker's own domain name.

    The Router uses an IP address, localhost or a Docker service name (no dot);
    .local names resolve only on this network."""
    try:
        name = urlsplit('//' + host).hostname
    except ValueError:
        return False
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return name == 'localhost' or name.endswith(('.localhost', '.local')) or '.' not in name


def _same_origin(origin, host):
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    return parts.scheme in {'http', 'https'} and parts.netloc.lower() == host.lower()


def allowed(headers):
    """Browsers send Origin on every cross-origin request, every POST and every
    WebSocket handshake; the Router sends none."""
    host = headers.get('host', '')
    if not _trusted_host(host):
        return False
    origin = headers.get('origin')
    return origin is None or _same_origin(origin, host)


class BrowserGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'}:
            headers = {key.decode('latin-1').lower(): value.decode('latin-1')
                       for key, value in scope.get('headers', [])}
            if not allowed(headers):
                if scope['type'] == 'websocket':
                    # Closing before accept answers the handshake with 403.
                    await send({'type': 'websocket.close', 'code': 1008})
                    return
                # Imported here: the llama.cpp launchers use this module without Starlette.
                from starlette.responses import PlainTextResponse
                return await PlainTextResponse('Forbidden', status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)
