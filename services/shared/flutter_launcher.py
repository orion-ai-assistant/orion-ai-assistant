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


def open_window(url, *, allow_tab_fallback=True, profile=None, reuse_existing=False):
    """A dedicated UI profile must never become Orion's automation profile."""
    profile = Path(profile) if profile is not None else Path(os.getenv('LOCALAPPDATA') or Path.home() / '.local' / 'share') / 'Orion' / 'flutter-ui-profile'
    if reuse_existing and os.name == 'nt':
        from .windows_autostart import focus_window
        if focus_window(profile):
            return True
    candidates = [shutil.which(name) for name in ('msedge', 'google-chrome', 'chromium', 'brave-browser')]
    for root in (os.getenv('PROGRAMFILES'), os.getenv('PROGRAMFILES(X86)'), os.getenv('LOCALAPPDATA')):
        if root:
            candidates.extend(str(Path(root) / path) for path in (
                'Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe',
                'BraveSoftware/Brave-Browser/Application/brave.exe'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            # The window is part of Orion: the launcher starts it inside the resident Orion.exe's job, so End task
            # on Orion closes it with the services (closing the window never ends them).
            subprocess.Popen([candidate, f'--app={url}', f'--user-data-dir={profile}',
                              '--no-first-run', '--no-default-browser-check'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    return webbrowser.open(url) if allow_tab_fallback else False


UI_EXE = 'OrionUi.exe'
UI_PREFIX = 'Orion-Ui-'      # + installation id: OrionUi.exe's message-only command window
UI_COMMAND = 0x4F52554F      # WM_COPYDATA tag of a command (OrionUi.cs CommandTag)


def ui_host_exe(app_dir):
    """Orion's own window (OrionUi.exe, WebView2): `ui_host/` of the installed Client package, or the
    development build (`build/ui_host/`, windows_host/build_ui_host.py). None: the browser window is used."""
    if os.name != 'nt' or app_dir is None:
        return None
    for candidate in (Path(app_dir) / 'ui_host' / UI_EXE, Path(app_dir) / 'build' / 'ui_host' / UI_EXE):
        if candidate.is_file():
            return candidate
    return None


def _ui_window(ident):
    import ctypes
    from ctypes import wintypes as W
    user = ctypes.WinDLL('user32')
    user.FindWindowExW.argtypes = [W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR]
    user.FindWindowExW.restype = W.HWND
    return user.FindWindowExW(W.HWND(-3), None, None, UI_PREFIX + ident)  # HWND_MESSAGE


def ui_running(ident):
    return os.name == 'nt' and bool(_ui_window(ident))


def ui_command(ident, command):
    """show / hide / quit / stop-listening / "reload <url>" to the running window; False when none runs."""
    if os.name != 'nt':
        return False
    import ctypes
    from ctypes import wintypes as W
    window = _ui_window(ident)
    if not window:
        return False
    user = ctypes.WinDLL('user32')
    if command == 'show':
        # The window may take the foreground (the user just asked for it).
        pid = W.DWORD()
        user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        user.AllowSetForegroundWindow.argtypes = [W.DWORD]
        user.AllowSetForegroundWindow(pid.value)

    class CopyData(ctypes.Structure):
        _fields_ = [('tag', ctypes.c_void_p), ('size', W.DWORD), ('data', ctypes.c_void_p)]
    text = ctypes.create_unicode_buffer(command)
    data = CopyData(UI_COMMAND, ctypes.sizeof(text), ctypes.cast(text, ctypes.c_void_p))
    result = ctypes.c_size_t()
    user.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, ctypes.c_void_p, W.UINT, W.UINT,
                                         ctypes.POINTER(ctypes.c_size_t)]
    user.SendMessageTimeoutW.restype = ctypes.c_size_t
    sent = user.SendMessageTimeoutW(window, 0x004A, 0, ctypes.byref(data), 0x0002, 5000, ctypes.byref(result))
    return bool(sent and result.value)


def _runnable_copy(exe, root):
    """The window runs for days: from a copy named by its contents, never from the Client folder, which an
    update replaces (or a development build rewrites) while it runs. Copies no longer running are removed."""
    import hashlib
    folder = Path(exe).parent
    names = sorted(p.name for p in folder.iterdir() if p.is_file())
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode() + b'\0' + (folder / name).read_bytes())
    target = Path(root) / digest.hexdigest()[:16]
    if not (target / UI_EXE).is_file():
        staging = target.with_name(target.name + '.tmp')
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(folder, staging)
        try:
            staging.replace(target)
        except OSError:
            shutil.rmtree(staging, ignore_errors=True)  # Another start copied it first.
    for old in Path(root).iterdir():
        if old != target and old.is_dir():
            shutil.rmtree(old, ignore_errors=True)  # One still running stays (its files are in use).
    return target / UI_EXE


def open_ui(url, *, app_dir, ident, data_dir, hidden=False, registry=None):
    """Show (or start) Orion's own window. It is started once and then only hidden and shown: closing it keeps
    the page running (the assistant keeps listening) and opening it again is instant. False when this Client has
    no OrionUi.exe; the caller opens the browser window instead."""
    exe = ui_host_exe(app_dir)
    if exe is None:
        return False
    if ui_running(ident):
        return hidden or ui_command(ident, 'show')
    exe = _runnable_copy(exe, Path(data_dir).parent / 'ui-host')
    argv = [str(exe), '--url', url, '--id', ident, '--data', str(data_dir)] + (['--hidden'] if hidden else [])
    # Under the resident Orion.exe when it runs: "End task" on Orion and Orion's stop end it with the rest.
    from .orion_host import popen
    popen(argv, registry=registry, component_root=app_dir, cwd=str(exe.parent), stdin=subprocess.DEVNULL,
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not hidden:
        # The new window shows itself; it is ready for commands once its message window exists.
        for _ in range(50):
            if ui_running(ident):
                break
            threading.Event().wait(.1)
    return True


def flutter_url(get_env):
    port = int(get_env('ORION_FLUTTER_PORT', default='8090', required=False))
    if not 1 <= port <= 65535:
        raise ValueError('ORION_FLUTTER_PORT must be a valid port')
    return f'http://localhost:{port}'


def start_flutter(repo_root, python, get_env, occupied_ports, log_file, stopping, runtime=None):
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
        if probe.connect_ex(('127.0.0.1', port)) == 0 and not (runtime and 'flutter' in runtime.children):
            raise OSError(f'Flutter port {port} is already occupied; no process was changed')
    env = os.environ.copy()
    env['ORION_UI_HUB_URL'] = 'http://localhost:' + str(get_env('ORION_LOCAL_PORT', default='8910', required=False))
    launch = (lambda argv, **kw: runtime.launch('flutter', argv, python=True, **kw)) if runtime else subprocess.Popen
    proc = launch([python, str(script), '--host', '127.0.0.1', '--port', str(port)], env=env,
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
                    refresh = url + '/__orion_refresh__'
                    # Development: Orion's own window when this checkout has built it, refreshed to the new
                    # build when it already runs.
                    data = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'Orion' / 'ui-webview2-dev'
                    if ui_running('dev'):
                        ui_command('dev', 'reload ' + refresh)
                        ui_command('dev', 'show')
                    elif not open_ui(refresh, app_dir=directory, ident='dev', data_dir=data):
                        open_window(refresh)
                return
            except OSError:
                stopping.wait(.5)
        print('[!] Flutter did not become ready; see hub.log', flush=True)

    threading.Thread(target=ready, daemon=True).start()
    return proc
