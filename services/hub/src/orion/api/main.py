import asyncio
from contextlib import asynccontextmanager, suppress

import os
from fastapi import FastAPI
from orion.api.cors import configure_cors
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from orion.api.routes import router
from orion.api.auth_routes import router as auth_router
from orion.api.google_ai_routes import router as google_ai_router
from orion.api.browser_routes import router as browser_router
from orion.contracts.constants import SETTINGS_DEFAULT_USER
from services.shared.environment import get_redis_url
from orion.kernel.config import get_runtime_settings, seed_database_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from orion.worker.tools._registry import get_registry
    get_registry()
    app.state.redis = Redis.from_url(get_redis_url(), decode_responses=True, protocol=2)
    await seed_database_settings(app.state.redis)
    await get_runtime_settings(app.state.redis, SETTINGS_DEFAULT_USER)
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


app = FastAPI(title="Orion Hub API", version="0.1.0", lifespan=lifespan)
configure_cors(app)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(router)
app.include_router(google_ai_router)
app.include_router(browser_router)

# Arayüzü sunma
ui_dir = os.path.join(os.path.dirname(__file__), "ui")
if os.path.exists(ui_dir):
    app.mount("/dashboard", StaticFiles(directory=ui_dir, html=True), name="dashboard")

# Admin arayüzü sunma
admin_dir = os.path.join(os.path.dirname(__file__), "admin_ui")
if os.path.exists(admin_dir):
    app.mount("/admin", StaticFiles(directory=admin_dir, html=True), name="admin_ui")
