import asyncio
from contextlib import asynccontextmanager, suppress

import os
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
async def lifespan(app: FastAPI):
    from orion.api.security import validate_security_configuration
    validate_security_configuration()
    if app.state.public:
        from orion.api.security import JWT_SECRET_KEY, DEFAULT_JWT_SECRET
        if JWT_SECRET_KEY == DEFAULT_JWT_SECRET:
            raise RuntimeError('Public Hub requires a configured JWT_SECRET_KEY')
    from orion.worker.tools._registry import get_registry
    get_registry()
    app.state.redis = Redis.from_url(get_redis_url(), decode_responses=True, protocol=2)
    await seed_database_settings(app.state.redis)
    await get_runtime_settings(app.state.redis, SETTINGS_DEFAULT_USER)
    if app.state.public:
        try:
            yield
        finally:
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


def create_app(public=False):
    application = FastAPI(title='Orion Hub', version='0.1.0', lifespan=lifespan,
                          docs_url=None if public else '/docs', redoc_url=None,
                          openapi_url=None if public else '/openapi.json')
    application.state.public = public
    configure_cors(application)
    application.include_router(auth_router, prefix='/api/v1')
    application.include_router(router)
    ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
    if not public:
        application.include_router(google_ai_router)
        application.include_router(browser_router)
        application.include_router(owner_router)
        owner_dir = os.path.join(os.path.dirname(__file__), 'owner_ui')

        @application.get('/', include_in_schema=False)
        async def home(request: Request):
            return owner_page('index.html', request)

        def owner_page(filename, request):
            with open(os.path.join(owner_dir, filename), encoding='utf-8') as page:
                html = page.read()
            target = str(request.url.replace(port=int(get_env('ORION_PUBLIC_PORT', default='8910', required=False)), path='/dashboard/', query=''))
            return HTMLResponse(html.replace('href="/dashboard/"', 'href="' + target + '"'), headers={'Cache-Control': 'no-store'})

        @application.get('/manage/', include_in_schema=False)
        async def manage(request: Request):
            return owner_page('people.html', request)

        @application.get('/owner-assets/auth_wait.js', include_in_schema=False)
        async def owner_countdown():
            return FileResponse(os.path.join(ui_dir, 'js', 'auth_wait.js'))

        @application.api_route('/dashboard', methods=['GET', 'HEAD'], include_in_schema=False)
        @application.api_route('/dashboard/{path:path}', methods=['GET', 'HEAD'], include_in_schema=False)
        async def public_dashboard(request: Request):
            target = request.url.replace(port=int(get_env('ORION_PUBLIC_PORT', default='8910', required=False)))
            return RedirectResponse(str(target))

        application.mount('/owner-assets', StaticFiles(directory=owner_dir), name='owner-assets')
        application.mount('/admin', StaticFiles(directory=os.path.join(os.path.dirname(__file__), 'admin_ui'), html=True), name='admin')
    else:
        @application.get('/', include_in_schema=False)
        async def public_home():
            return RedirectResponse('/dashboard/')

        application.mount('/dashboard', StaticFiles(directory=ui_dir, html=True), name='dashboard')
    application.add_middleware(AccessBoundary, public=public)
    return application


app = create_app()
public_app = create_app(public=True)
