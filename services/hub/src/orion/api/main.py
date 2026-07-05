from contextlib import asynccontextmanager

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from orion.api.routes import router
from orion.api.auth_routes import router as auth_router
from orion.contracts.constants import SETTINGS_DEFAULT_USER
from services.shared.environment import get_redis_url
from orion.kernel.config import get_runtime_settings, seed_database_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = Redis.from_url(get_redis_url(), decode_responses=True, protocol=2)
    await seed_database_settings(app.state.redis)
    await get_runtime_settings(app.state.redis, SETTINGS_DEFAULT_USER)
    try:
        yield
    finally:
        await app.state.redis.close()


app = FastAPI(title="Orion Hub API", version="0.1.0", lifespan=lifespan)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(router)

# Arayüzü sunma
ui_dir = os.path.join(os.path.dirname(__file__), "ui")
if os.path.exists(ui_dir):
    app.mount("/dashboard", StaticFiles(directory=ui_dir, html=True), name="dashboard")

# Admin arayüzü sunma
admin_dir = os.path.join(os.path.dirname(__file__), "admin_ui")
if os.path.exists(admin_dir):
    app.mount("/admin", StaticFiles(directory=admin_dir, html=True), name="admin_ui")
