import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from . import config
from .routes import router

# Global tarayıcı süreç referansı
browser_process = None

# ---------------------------------------------------------------------------
# Sinyal Yönetimi ve Temizlik İşlemleri
# ---------------------------------------------------------------------------
def handle_exit(sig, frame):
    config.SHOULD_EXIT = True
    print("\n[SYSTEM] Kapatma sinyali alındı, görevler durduruluyor...")
    
    global browser_process
    if browser_process is not None:
        try:
            print("[SYSTEM] Açık olan tarayıcı penceresi kapatılıyor...")
            if platform.system() == "Windows":
                # Ensure the entire browser process tree is terminated
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(browser_process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            else:
                browser_process.terminate()
                browser_process.wait(timeout=1.0)
        except Exception:
            try:
                browser_process.kill()
            except Exception:
                pass

    if sig == signal.SIGINT:
        raise KeyboardInterrupt
    else:
        sys.exit(0)

# Sinyalleri yakala (Ctrl+C ve Kill)
signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)


# ---------------------------------------------------------------------------
# FastAPI Lifespan ve Sunucu Ayarları
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    print("[SYSTEM] Sunucu kapatıldı.")

app = FastAPI(title="Orion Installer API", lifespan=lifespan)

# CORS ve Önbellek (Cache) Engelleme Middleware'leri
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def no_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

# Rotalar ve Statik Arayüz Dosyaları
app.include_router(router)

@app.get("/")
def index():
    return FileResponse(f"{config.UI_DIR}/index.html")

app.mount("/ui", StaticFiles(directory=config.UI_DIR), name="ui")


# ---------------------------------------------------------------------------
# Kapanma (Shutdown) Yönetimi
# ---------------------------------------------------------------------------
shutdown_timer: threading.Timer = None
shutdown_lock = threading.Lock()

def schedule_shutdown(delay: float = 2.0):
    global shutdown_timer
    with shutdown_lock:
        cancel_shutdown_unlocked()
        def perform_shutdown():
            print("\n[SYSTEM] Arayüz kapatıldı. Sunucu durduruluyor...")
            try:
                handle_exit(signal.SIGINT, None)
            except KeyboardInterrupt:
                pass
            import _thread
            _thread.interrupt_main()
        shutdown_timer = threading.Timer(delay, perform_shutdown)
        shutdown_timer.start()

def cancel_shutdown_unlocked():
    global shutdown_timer
    if shutdown_timer is not None:
        shutdown_timer.cancel()
        shutdown_timer = None

def cancel_shutdown():
    with shutdown_lock:
        cancel_shutdown_unlocked()

@app.post("/api/shutdown")
def shutdown():
    schedule_shutdown()
    return {"status": "success", "message": "Shutdown scheduled"}

@app.post("/api/keepalive")
def keepalive():
    cancel_shutdown()
    return {"status": "success", "message": "Shutdown cancelled"}


@app.get("/api/ping")
def ping():
    return {"app": "orion-installer"}

# ---------------------------------------------------------------------------
# Masaüstü Uygulama Modu (App Mode) Tetikleyici Fonksiyon
# ---------------------------------------------------------------------------
def open_app_window(host: str, port: int):
    url = f"http://{host}:{port}"

    def wait_and_open():
        global browser_process
        # Uvicorn sunucusunun hazır olmasını bekle
        for _ in range(50):
            try:
                with socket.create_connection((host, port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            return

        system = platform.system()
        opened = False
        profile_dir = os.path.join(tempfile.gettempdir(), "orion_installer_profile")
        app_args = [
            f"--app={url}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ]

        try:
            if system == "Windows":
                # 1. Google Chrome'u dene (ikonun doğru görünmesi için en iyi seçenek)
                user_local = os.environ.get("LOCALAPPDATA", "")
                chrome_paths = [
                    os.path.join(user_local, r"Google\Chrome\Application\chrome.exe"),
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                ]
                chrome_exe = next((p for p in chrome_paths if os.path.exists(p)), None)

                if chrome_exe:
                    print("[SYSTEM] Tarayıcı: Google Chrome (uygulama modu)")
                    browser_process = subprocess.Popen([chrome_exe] + app_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    opened = True
                else:
                    # 2. Chrome yoksa Varsayılan Tarayıcıyı aç
                    print("[SYSTEM] Chrome bulunamadı. Varsayılan tarayıcı açılıyor...")
                    try:
                        webbrowser.open(url)
                        opened = True
                    except Exception:
                        # 3. Son çare Edge
                        print("[SYSTEM] Varsayılan tarayıcı açılamadı. Tarayıcı: Microsoft Edge (son çare)")
                        edge_paths = [
                            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                        ]
                        edge_exe = next((p for p in edge_paths if os.path.exists(p)), None)
                        if edge_exe:
                            browser_process = subprocess.Popen([edge_exe] + app_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            cmd_str = f'start msedge --app={url} --user-data-dir="{profile_dir}" --no-first-run --no-default-browser-check'
                            browser_process = subprocess.Popen(cmd_str, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        opened = True

            elif system == "Darwin":  # macOS
                browser_process = subprocess.Popen(
                    ['open', '-na', 'Google Chrome', '--args'] + app_args,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                opened = True

            elif system == "Linux":
                browser_process = subprocess.Popen(
                    ['google-chrome'] + app_args,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                opened = True

        except Exception as e:
            print(f"[SYSTEM] App modunda tarayıcı başlatılamadı: {e}")

        # Eğer yukarıdaki işletim sistemi komutları bir şekilde başarısız olursa,
        # Python'un evrensel kütüphanesiyle normal bir sekme aç (Garanti çözüm)
        if not opened:
            webbrowser.open(url)

    threading.Thread(target=wait_and_open, daemon=True).start()


# ---------------------------------------------------------------------------
# Uygulama Başlangıç Noktası (Entrypoint)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import urllib.request
    import json

    global_env = config._load_global_env()
    PORT = int(global_env.get("INSTALLER_PORT", "7171"))

    # Check if another instance is already running on this port via ping
    is_already_running = False
    port_occupied = False
    try:
        # First try a quick socket check to see if anyone is listening
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.2):
            port_occupied = True
    except OSError:
        pass

    if port_occupied:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/ping")
            with urllib.request.urlopen(req, timeout=0.5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    if data.get("app") == "orion-installer":
                        is_already_running = True
        except Exception:
            pass

        if is_already_running:
            print(f"\n[SYSTEM] Orion Installer zaten çalışıyor.")
            print(f"[SYSTEM] Mevcut pencere ön plana getiriliyor...")

            focused = False
            if platform.system() == "Windows":
                try:
                    # Pencere başlığına göre mevcut Edge app penceresini bul ve öne getir
                    focus_script = (
                        '$wshell = New-Object -ComObject wscript.shell;'
                        'if ($wshell.AppActivate("Orion AI")) { "focused" } else { "not_found" }'
                    )
                    result = subprocess.run(
                        ['powershell', '-NoProfile', '-Command', focus_script],
                        capture_output=True, text=True, timeout=3
                    )
                    focused = "focused" in result.stdout
                except Exception as e:
                    print(f"[SYSTEM] Pencere odaklanamadı: {e}")

            if not focused:
                # Mevcut pencere bulunamadıysa son çare olarak yeni bir pencere aç
                print(f"[SYSTEM] Mevcut pencere bulunamadı, yeni pencere açılıyor...")
                open_app_window("127.0.0.1", PORT)
                time.sleep(1.0)

            sys.exit(0)
        else:
            print(f"\n[!] Kritik Hata: {PORT} portu kullanımda, lütfen arka plandaki diğer uygulamaları kapatın.")
            sys.exit(1)

    print(f"\n[SYSTEM] Orion Installer başlatıldı.")
    print(f"[SYSTEM] Arayüz otomatik olarak açılacaktır.")
    print(f"[SYSTEM] Eğer otomatik açılmazsa, tarayıcınızdan şu adrese gidebilirsiniz: http://127.0.0.1:{PORT}\n")

    open_app_window("127.0.0.1", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
