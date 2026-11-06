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


def window_profile(name):
    """The browser profile folder of one kind of Orion window."""
    return Path(os.getenv('LOCALAPPDATA') or Path.home() / '.local' / 'share') / 'Orion' / name


# Each kind of window has its own browser profile: opening Orion (shortcut, Start menu) focuses an open window
# of *its* profile, so a Router or admin panel window sharing the Hub window's profile was brought to the front
# instead of the Hub's own window.
ROUTER_PROFILE = 'router-ui-profile'
PANEL_PROFILE = 'panel-ui-profile'


def open_window(url, *, allow_tab_fallback=True, profile=None, reuse_existing=False):
    """A dedicated UI profile must never become Orion's automation profile."""
    profile = Path(profile) if profile is not None else window_profile('flutter-ui-profile')
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
PANEL_IDENTS = ('router-', 'panel-')  # Stand-alone panel windows (their own process), never the assistant.


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
    return bool(_ui_send(ident, command))


def _ui_send(ident, command):
    """The window's answer to a command: 0 when none runs or it did not answer, 1 done, 2 a "panel" opened."""
    if os.name != 'nt':
        return 0
    import ctypes
    from ctypes import wintypes as W
    window = _ui_window(ident)
    if not window:
        return 0
    user = ctypes.WinDLL('user32')
    if command == 'show' or command.startswith('panel\t'):
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
    return result.value if sent else 0


def _assistant_window():
    """The ident of the running assistant window (the Hub's own OrionUi.exe, not a stand-alone panel), or None."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes as W
    user = ctypes.WinDLL('user32')
    user.FindWindowExW.argtypes = [W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR]
    user.FindWindowExW.restype = W.HWND
    user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    window = None
    for _ in range(4096):
        window = user.FindWindowExW(W.HWND(-3), window, None, None)  # HWND_MESSAGE windows
        if not window:
            return None
        title = ctypes.create_unicode_buffer(128)
        user.GetWindowTextW(window, title, 128)
        ident = title.value[len(UI_PREFIX):] if title.value.startswith(UI_PREFIX) else ''
        if ident and not ident.startswith(PANEL_IDENTS):
            return ident
    return None


def _build_id(folder):
    """The name of a window build's runnable copy: the digest of its files."""
    import hashlib
    folder = Path(folder)
    digest = hashlib.sha256()
    for name in sorted(p.name for p in folder.iterdir() if p.is_file()):
        digest.update(name.encode() + b'\0' + (folder / name).read_bytes())
    return digest.hexdigest()[:16]


def _ui_process(ident):
    """(pid, exe path) of the running window; (0, None) when none runs, path None when it cannot be read."""
    if os.name != 'nt':
        return 0, None
    import ctypes
    from ctypes import wintypes as W
    window = _ui_window(ident)
    if not window:
        return 0, None
    user = ctypes.WinDLL('user32')
    kernel = ctypes.WinDLL('kernel32')
    pid = W.DWORD()
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user.GetWindowThreadProcessId(window, ctypes.byref(pid))
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return pid.value, None
    try:
        size = W.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return pid.value, None
        return pid.value, Path(buffer.value)
    finally:
        kernel.CloseHandle(handle)


def ui_outdated(ident, app_dir):
    """True when the running window is an older build than the one in app_dir (a rebuild or an update): it
    keeps its old icon and code until it is started again, since showing and reloading never restart it."""
    exe = ui_host_exe(app_dir)
    if exe is None:
        return False
    _, running = _ui_process(ident)
    if running is None:
        return False
    try:
        return running.parent.name != _build_id(exe.parent)
    except OSError:
        return False


def _quit_and_wait(ident, timeout_ms=10000):
    """Quit the running window and wait until its process has ended (its single-instance lock is free)."""
    pid, _ = _ui_process(ident)
    ui_command(ident, 'quit')
    if not pid:
        return
    import ctypes
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32')
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if handle:
        try:
            kernel.WaitForSingleObject(handle, timeout_ms)
        finally:
            kernel.CloseHandle(handle)


def _runnable_copy(exe, root):
    """The window runs for days: from a copy named by its contents, never from the Client folder, which an
    update replaces (or a development build rewrites) while it runs. Copies no longer running are removed."""
    folder = Path(exe).parent
    target = Path(root) / _build_id(folder)
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


def open_panel(url, *, key, title, icon=None, app_dir, data_dir):
    """Another local Orion page (the Router panel, the Hub's management panel) in Orion's own window instead of
    Edge --app: its title keeps its colour when inactive and its taskbar icon is sharp. One window per panel: an
    open one is only brought to the front, as it is (no second window, no reload). False without OrionUi.exe.

    Opened inside the running assistant window's process ("panel" command): its browser already runs, so the
    panel shows at once, as part of the same Orion app, with its own profile. Without that window (or with an
    older build that does not know the command) a stand-alone OrionUi.exe --panel starts, which takes seconds."""
    if ui_host_exe(app_dir) is None:
        return False
    host = _assistant_window()
    if host and _ui_send(host, '\t'.join(['panel', key, title, str(icon or ''), url])) == 2:
        return True
    import hashlib
    ident = key + '-' + hashlib.sha256(str(app_dir).encode()).hexdigest()[:12]
    if ui_running(ident) and ui_outdated(ident, app_dir):
        _quit_and_wait(ident)
    if ui_running(ident):
        return ui_command(ident, 'show')
    extra = ['--panel', '--title', title] + (['--icon', str(icon)] if icon else [])
    return open_ui(url, app_dir=app_dir, ident=ident, data_dir=data_dir, extra=extra)


def _local_http(url):
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return parts.scheme == 'http' and parts.hostname in ('localhost', '127.0.0.1', '::1')


def _panel_data(name):
    return Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'Orion' / name


def open_router_window(url, get_env):
    """The local Router panel: Orion's own window with the Router's icon, else the Edge --app window."""
    from urllib.parse import urlsplit
    app_dir = app_directory(Path(__file__).resolve().parents[2], get_env)
    if os.name == 'nt' and _local_http(url) and ui_host_exe(app_dir) is not None:
        data = _panel_data('ui-webview2-router')
        icon = data / 'router.ico'
        parts = urlsplit(url)
        # 127.0.0.1, not "localhost": Windows tries ::1 first, where the Router does not listen, and a refused
        # loopback connection there takes about 2 s.
        source = f'{parts.scheme}://127.0.0.1:{parts.port or 80}/dashboard/favicon.ico'

        def fetch_icon():
            try:
                # The Router's own multi-size icon (dashboard/public/favicon.ico), kept for the next open.
                with urllib.request.urlopen(source, timeout=2) as response:
                    body = response.read(2_000_000)
                if body[:4] == b'\0\0\1\0':
                    data.mkdir(parents=True, exist_ok=True)
                    partial = icon.with_suffix(f'.{threading.get_ident()}.tmp')
                    partial.write_bytes(body)
                    partial.replace(icon)
            except OSError:
                pass  # The previous copy, or the window's own icon.
        if icon.is_file():
            threading.Thread(target=fetch_icon, daemon=True).start()  # Refreshed for the next open, no wait.
        else:
            fetch_icon()
        if open_panel(url, key='router', title='Orion Router', icon=icon if icon.is_file() else None,
                      app_dir=app_dir, data_dir=data):
            return True
    # Edge: its own profile, so an open Router window is focused (not opened twice) and never taken for the Hub's.
    return open_window(url, allow_tab_fallback=False, profile=window_profile(ROUTER_PROFILE), reuse_existing=True)


def open_management_window(url, get_env):
    """The Hub's management panel (/panel): Orion's own window with the Hub's icon, else the Edge --app window."""
    app_dir = app_directory(Path(__file__).resolve().parents[2], get_env)
    if os.name == 'nt' and _local_http(url) and ui_host_exe(app_dir) is not None:
        if open_panel(url, key='panel', title='Orion · Yönetim paneli', app_dir=app_dir,
                      data_dir=_panel_data('ui-webview2-panel')):
            return True
    # Its own profile: Orion's shortcut must open the Hub window, not focus this panel.
    return open_window(url, allow_tab_fallback=False, profile=window_profile(PANEL_PROFILE), reuse_existing=True)


def open_ui(url, *, app_dir, ident, data_dir, hidden=False, registry=None, extra=()):
    """Show (or start) Orion's own window. It is started once and then only hidden and shown: closing it keeps
    the page running (the assistant keeps listening) and opening it again is instant. False when this Client has
    no OrionUi.exe; the caller opens the browser window instead."""
    exe = ui_host_exe(app_dir)
    if exe is None:
        return False
    if ui_running(ident) and ui_outdated(ident, app_dir):
        _quit_and_wait(ident)  # Started again below from the new build.
    if ui_running(ident):
        return hidden or ui_command(ident, 'show')
    exe = _runnable_copy(exe, Path(data_dir).parent / 'ui-host')
    argv = [str(exe), '--url', url, '--id', ident, '--data', str(data_dir)] + (['--hidden'] if hidden else []) + list(extra)
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

    def log(message):
        print(message, flush=True)
        if log_file is not None:
            try:
                log_file.write(message + '\n')
                log_file.flush()
            except (OSError, ValueError):
                pass

    def ready():
        # Development checkout: rebuild the web build and the window first when their sources changed (like
        # Router's dashboard), so a start from source never shows an old build. The server already runs and
        # reads the files from disk; the window opens (or reloads) only after the build.
        from .flutter_dev_build import refresh as refresh_dev_build
        try:
            refresh_dev_build(directory, python, log)
        except OSError as error:
            log(f'[!] Flutter development build check failed: {error}')
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
                    if ui_running('dev') and not ui_outdated('dev', directory):
                        ui_command('dev', 'reload ' + refresh)
                        ui_command('dev', 'show')
                    elif not open_ui(refresh, app_dir=directory, ident='dev', data_dir=data):  # Restarts an old build.
                        open_window(refresh)
                return
            except OSError:
                stopping.wait(.5)
        print('[!] Flutter did not become ready; see hub.log', flush=True)

    threading.Thread(target=ready, daemon=True).start()
    return proc
