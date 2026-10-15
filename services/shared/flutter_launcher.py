"""Local Flutter web lifecycle. Never proxy APIs or grant desktop privileges."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import urllib.request
import webbrowser


def enabled(value):
    return str(value).lower() not in {'0', 'false', 'off', 'no'}


def app_directory(repo_root, get_env):
    default = Path(repo_root).resolve().parent / 'orion-ai-assistant-flutter' / 'main_app' / 'app'
    return Path(get_env('ORION_FLUTTER_APP_DIR', default=str(default), required=False)).resolve()


def open_window(url, *, allow_tab_fallback=True):
    """A dedicated UI profile must never become Orion's automation profile."""
    profile = Path(os.getenv('LOCALAPPDATA') or Path.home() / '.local' / 'share') / 'Orion' / 'flutter-ui-profile'
    candidates = [shutil.which(name) for name in ('msedge', 'google-chrome', 'chromium', 'brave-browser')]
    for root in (os.getenv('PROGRAMFILES'), os.getenv('PROGRAMFILES(X86)'), os.getenv('LOCALAPPDATA')):
        if root:
            candidates.extend(str(Path(root) / path) for path in (
                'Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe',
                'BraveSoftware/Brave-Browser/Application/brave.exe'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            subprocess.Popen([candidate, f'--app={url}', f'--user-data-dir={profile}',
                              '--no-first-run', '--no-default-browser-check'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return webbrowser.open(url) if allow_tab_fallback else False


def flutter_url(get_env):
    port = int(get_env('ORION_FLUTTER_PORT', default='8090', required=False))
    if not 1 <= port <= 65535:
        raise ValueError('ORION_FLUTTER_PORT must be a valid port')
    return f'http://localhost:{port}'


def start_flutter(repo_root, python, get_env, occupied_ports, log_file, stopping):
    """Return only our child process, or None. An occupied port is never killed."""
    if not enabled(get_env('ORION_FLUTTER_AUTOSTART', default='1', required=False)):
        return None
    directory = app_directory(repo_root, get_env)
    port = int(get_env('ORION_FLUTTER_PORT', default='8090', required=False))
    if not 1 <= port <= 65535 or port in occupied_ports:
        raise ValueError('ORION_FLUTTER_PORT must be valid and differ from Hub/Router/service ports')
    if not (directory / 'build' / 'web' / 'index.html').is_file():
        raise FileNotFoundError(f'Flutter web build missing: {directory}. Run flutter build web in this directory.')
    script = directory / 'tool' / 'serve_web.py'
    with socket.socket() as probe:
        probe.settimeout(.3)
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise OSError(f'Flutter port {port} is already occupied; no process was changed')
    env = os.environ.copy()
    env['ORION_UI_HUB_URL'] = 'http://localhost:' + str(get_env('ORION_LOCAL_PORT', default='8910', required=False))
    proc = subprocess.Popen([python, str(script), '--host', '127.0.0.1', '--port', str(port)], env=env,
                            cwd=str(directory), stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file,
                            creationflags=0x08000000 if os.name == 'nt' else 0)
    url = flutter_url(get_env)

    def ready():
        for _ in range(120):
            if stopping.is_set() or proc.poll() is not None:
                return
            try:
                with urllib.request.urlopen(url + '/__orion_flutter_health__', timeout=.5) as response:
                    if response.read(128) != b'orion-flutter-web':
                        return
                # Wait for the user API as well as the static web build.
                with urllib.request.urlopen(env['ORION_UI_HUB_URL'] + '/health', timeout=.5) as response:
                    if response.status != 200:
                        continue
                print(f'[*] Flutter ready: {url}', flush=True)
                if enabled(get_env('ORION_FLUTTER_OPEN_WINDOW', default='1', required=False)) and not stopping.is_set():
                    open_window(url + '/__orion_refresh__')
                return
            except OSError:
                stopping.wait(.5)
        print('[!] Flutter did not become ready; see hub.log', flush=True)

    threading.Thread(target=ready, daemon=True).start()
    return proc
