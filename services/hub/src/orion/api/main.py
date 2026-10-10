import asyncio
from contextlib import asynccontextmanager, suppress

import os
import re
from fastapi import FastAPI, Request
from orion.api.cors import configure_cors
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from redis.asyncio import Redis

from orion.api.routes import router
from orion.api.auth_routes import router as auth_router
from orion.api.google_ai_routes import router as google_ai_router
from orion.api.browser_routes import router as browser_router
from orion.api.owner_routes import router as owner_router
from orion.api.access_boundary import AccessBoundary
from orion.contracts.constants import SETTINGS_DEFAULT_USER
from services.shared.environment import get_redis_url, get_env
from orion.kernel.config import get_runtime_settings, seed_database_settings


@asynccontextmanager
async def service_lifespan(app: FastAPI):
    from orion.api.security import validate_security_configuration
    validate_security_configuration()
    if app.state.public:
        from orion.api.security import JWT_SECRET_KEY, DEFAULT_JWT_SECRET
        if JWT_SECRET_KEY == DEFAULT_JWT_SECRET:
            raise RuntimeError('Public Hub requires a configured JWT_SECRET_KEY')
    from orion.worker.tools._registry import get_registry
    get_registry()
    app.state.redis = Redis.from_url(get_redis_url(), decode_responses=True, protocol=2)
    from orion.api.router_connection import RouterAssociation
    association = RouterAssociation(app.state.redis)
    app.state.router_association = association
    from orion.kernel.router_bootstrap import bootstrap_local_router
    # Optional Router startup never delays the Hub listener. Association/mobile
    # refresh loops retry installations and servers that become available later.
    app.state.local_bootstrap_task = asyncio.create_task(bootstrap_local_router())
    if hasattr(app.state, 'panel_app'):
        app.state.panel_app.state.redis = app.state.redis
        app.state.panel_app.state.router_association = association
    await seed_database_settings(app.state.redis)
    await get_runtime_settings(app.state.redis, SETTINGS_DEFAULT_USER)
    if app.state.mobile:
        try:
            yield
        finally:
            await app.state.redis.close()
        return
    if app.state.public and not app.state.mobile:
        from orion.api.discovery import HubDiscovery
        discovery = HubDiscovery(app.version)
        if app.state.discovery_enabled:
            discovery.start()
            association.start()
        try:
            yield
        finally:
            await discovery.close()
            await association.close()
            await app.state.redis.close()
        return
    from orion.browser.bridge import BrowserBridge
    from orion.browser.manager import restore_saved_browsers
    bridge = await BrowserBridge().start()
    restore_task = asyncio.create_task(restore_saved_browsers())
    try:
        yield
    finally:
        restore_task.cancel()
        with suppress(asyncio.CancelledError):
            await restore_task
        await bridge.close()
        await app.state.redis.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # All API entry points monitor queues, independently of Worker availability.
    from orion.kernel.job_safety import queue_watchdog
    async with service_lifespan(app):
        watchdog = asyncio.create_task(queue_watchdog(app.state.redis))
        try:
            yield
        finally:
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog


def create_app(public=False, discovery_enabled=True, local_entry=False, mobile=False, local_http=False):
    application = FastAPI(title='Orion Hub', version='0.1.0', lifespan=lifespan,
                          docs_url=None if public else '/docs', redoc_url=None,
                          openapi_url=None if public else '/openapi.json')
    application.state.public = public
    application.state.mobile = mobile
    application.state.discovery_enabled = discovery_enabled
    application.state.local_entry = local_entry
    application.state.local_http = local_http
    configure_cors(application)
    application.include_router(auth_router, prefix='/api/v1')
    application.include_router(router)
    from orion.api.router_providers import router as provider_router
    application.include_router(provider_router)
    from orion.api.mobile_security import mobile_router, pairing_router, owner_router as secure_owner_router, MobileBoundary, LegacyDeviceBoundary
    if public or mobile:
        application.include_router(pairing_router)
    if mobile:
        application.include_router(mobile_router)
    ui_dir = os.path.join(os.path.dirname(__file__), 'ui')

    @application.get('/favicon.ico', include_in_schema=False)
    async def favicon():
        return FileResponse(os.path.join(ui_dir, 'favicon.svg'), media_type='image/svg+xml')

    if not public:
        application.include_router(google_ai_router)
        application.include_router(browser_router)
        application.include_router(owner_router)
        application.include_router(secure_owner_router)
        from orion.api.router_connection import router as router_connection_router
        application.include_router(router_connection_router)
        owner_dir = os.path.join(os.path.dirname(__file__), 'owner_ui')

        @application.get('/', include_in_schema=False)
        async def home(request: Request):
            return owner_page('index.html', request)

        def owner_page(filename, request, directory=owner_dir):
            with open(os.path.join(directory, filename), encoding='utf-8') as page:
                html = page.read()
            root = request.scope.get('root_path', '').rstrip('/')
            if local_http:
                local_port = int(get_env('ORION_LOCAL_SHORTCUT_PORT', default='80', required=False))
                suffix = '' if local_port == 80 else f':{local_port}'
                target = '/hub' if root else f'http://127.0.0.1:{get_env("ORION_LOCAL_PORT", default="8910", required=False)}/hub'
                html = html.replace('href="/dashboard/"', 'href="' + target + '"')
                if root:
                    html = re.sub(r'(href|src)="(/(?!/)[^"]*)"', lambda match: f'{match[1]}="{match[2] if match[2] == target else root + match[2]}"', html)
                return HTMLResponse(html, headers={'Cache-Control': 'no-store'})
            target = '/hub' if root else f'http://127.0.0.1:{get_env("ORION_LOCAL_PORT", default="8910", required=False)}/hub'
            html = html.replace('href="/dashboard/"', 'href="' + target + '"')
            if root:
                html = re.sub(r'(href|src)="(/(?!/)[^"]*)"',
                              lambda match: f'{match[1]}="{match[2] if match[2] == target else root + match[2]}"', html)
            else:
                # Even the legacy owner entry points open management at /panel.
                panel = f'http://127.0.0.1:{get_env("ORION_LOCAL_PORT", default="8910", required=False)}/panel'
                for path in ('/', '/manage/', '/manage/security', '/admin/', '/router/'):
                    html = html.replace(f'href="{path}"', f'href="{panel}{path}"')
            return HTMLResponse(html, headers={'Cache-Control': 'no-store'})

        @application.get('/manage', include_in_schema=False)
        @application.get('/manage/', include_in_schema=False)
        async def manage(request: Request):
            return owner_page('people.html', request)

        @application.get('/manage/security', include_in_schema=False)
        async def secure_management(request: Request):
            return owner_page('security.html', request)

        @application.get('/connect', include_in_schema=False)
        async def connect_phone(request: Request):
            return owner_page('connect.html', request)

        @application.get('/router/', include_in_schema=False)
        async def router_settings(request: Request):
            return owner_page('router.html', request)

        @application.get('/admin/', include_in_schema=False)
        async def admin_home(request: Request):
            return owner_page('index.html', request, os.path.join(os.path.dirname(__file__), 'admin_ui'))

        @application.get('/owner-assets/auth_wait.js', include_in_schema=False)
        async def owner_countdown():
            return FileResponse(os.path.join(ui_dir, 'js', 'auth_wait.js'))

        @application.api_route('/dashboard', methods=['GET', 'HEAD'], include_in_schema=False)
        @application.api_route('/dashboard/{path:path}', methods=['GET', 'HEAD'], include_in_schema=False)
        async def public_dashboard(request: Request, path: str = ''):
            port = int(get_env('ORION_LOCAL_PORT', default='8910', required=False))
            target = f'http://127.0.0.1:{port}' + ('/' + path.lstrip('/') if path else '/hub')
            if request.url.query:
                target += '?' + request.url.query
            return RedirectResponse(target)

        application.mount('/owner-assets', StaticFiles(directory=owner_dir), name='owner-assets')
        application.mount('/admin', StaticFiles(directory=os.path.join(os.path.dirname(__file__), 'admin_ui'), html=True), name='admin')
    elif not mobile:
        application.include_router(browser_router)
        from orion.api.hub_directory import router as discovery_router, HubDirectory
        application.include_router(discovery_router)
        application.state.hub_directory = HubDirectory()
        @application.get('/connect', include_in_schema=False)
        async def connection_landing():
            return FileResponse(os.path.join(ui_dir, 'connect.html'), headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

        @application.api_route('/hub', methods=['GET', 'HEAD'], include_in_schema=False)
        @application.api_route('/hub/', methods=['GET', 'HEAD'], include_in_schema=False)
        @application.api_route('/', methods=['GET', 'HEAD'], include_in_schema=False)
        async def public_home():
            with open(os.path.join(ui_dir, 'index.html'), encoding='utf-8') as page:
                html = page.read()
            html = re.sub(r'(href|src)="((?:css|js)/[^"]*)"', r'\1="/\2"', html)
            return HTMLResponse(html, headers={'Cache-Control': 'no-store'})

        @application.api_route('/dashboard', methods=['GET', 'HEAD'], include_in_schema=False)
        @application.api_route('/dashboard/{path:path}', methods=['GET', 'HEAD'], include_in_schema=False)
        async def legacy_dashboard(request: Request, path: str = ''):
            # Keep old invitations/bookmarks and asset URLs on this origin.
            target = ('/hub' if local_entry and not path else '/') + path.lstrip('/')
            if request.url.query:
                target += '?' + request.url.query
            return RedirectResponse(target)

        for asset in ('css', 'js'):
            application.mount('/' + asset, StaticFiles(directory=os.path.join(ui_dir, asset)), name=asset)
    if mobile:
        application.add_middleware(MobileBoundary)
    else:
        application.add_middleware(AccessBoundary, public=public)
        application.add_middleware(LegacyDeviceBoundary)
    from orion.api.https_boundary import RequireTLS, LocalTransportBoundary
    application.add_middleware(LocalTransportBoundary if local_http else RequireTLS)
    return application


app = create_app()
public_app = create_app(public=True)
mobile_app = create_app(public=True, mobile=True, discovery_enabled=False)

# The local launcher is never a LAN entry or a Router address service.
# Only the HTTPS web listener (8911 by default) advertises DNS-SD.
from orion.api.shortcuts import OwnerShortcut

local_app = create_app(public=True, discovery_enabled=False, local_entry=True, local_http=True)
local_app.state.panel_app = create_app(discovery_enabled=False, local_http=True)
local_app.add_middleware(OwnerShortcut, panel_app=local_app.state.panel_app,
                         directory=local_app.state.hub_directory)
from orion.api.https_boundary import LocalTransportBoundary
local_app.add_middleware(LocalTransportBoundary)
from orion.api.mobile_security import LegacyDeviceBoundary
local_app.add_middleware(LegacyDeviceBoundary)
# Compatibility for callers of the previous local entry symbol.
shortcuts_app = local_app
