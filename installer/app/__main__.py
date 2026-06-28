from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import signal
from . import config
from .routes import router

def handle_exit(sig, frame):
    config.SHOULD_EXIT = True
    print("\n[SYSTEM] Kapatma sinyali alındı, görevler durduruluyor...")

# Sinyalleri yakala (Ctrl+C ve Kill)
signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    print("[SYSTEM] Sunucu kapatıldı.")

app = FastAPI(title="Orion Installer API", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def no_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

app.include_router(router)

@app.get("/")
def index():
    return FileResponse(f"{config.UI_DIR}/index.html")

app.mount("/ui", StaticFiles(directory=config.UI_DIR), name="ui")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)
