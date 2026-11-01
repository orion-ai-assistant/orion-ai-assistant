"""The light Hub of a computer that only uses another PC's Hub (ORION_HUB_MODE=relay).

No database, Redis, worker, Router or network listener: this PC's Orion window talks to it on loopback, and it
relays the window to the paired main Hub (api/relay.py). It is not advertised and accepts no device
pairing, so phones only ever see and pair with the main Hub.
"""
import asyncio

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from orion.api.https_boundary import LocalTransportBoundary
from orion.api.relay import RelayGateway

LOOPBACK_ORIGINS = r'https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?'


def relay_mode():
    import os
    return os.getenv('ORION_HUB_MODE', '').strip().lower() == 'relay'


async def health(request):
    return JSONResponse({'status': 'ok', 'mode': 'relay'})


async def current_hub(request):
    from orion.api.hub_directory import local_identity
    identity = await asyncio.to_thread(local_identity)
    return JSONResponse({**identity, 'mode': 'relay', 'registration_allowed': False, 'desktop': True,
                         'local_entry': True})


async def hubs(request):
    # This computer has no Hub of its own to offer, only the ones it paired with.
    from orion.api.relay import window_hubs
    return JSONResponse({'hubs': await asyncio.to_thread(window_hubs), 'current_id': None, 'mode': 'relay'})


async def window(request):
    import os
    return RedirectResponse(f'http://127.0.0.1:{os.getenv("ORION_FLUTTER_PORT", "8090")}/')


def create_relay_app():
    inner = Starlette(routes=[
        Route('/health', health), Route('/ready', health),
        Route('/api/v1/discovery/self', current_hub), Route('/api/v1/discovery/hubs', hubs),
        Route('/hub', window), Route('/', window),
    ], middleware=[Middleware(CORSMiddleware, allow_origin_regex=LOOPBACK_ORIGINS, allow_methods=['GET'])])
    return LocalTransportBoundary(RelayGateway(inner))


app = create_relay_app()
