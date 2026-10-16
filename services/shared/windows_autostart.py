"""Per-user Windows links, with explicit ownership and no shell interpolation."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def focus_window(profile):
    """Focus only a browser belonging to the dedicated Orion UI profile."""
    import psutil
    wanted = os.path.normcase(str(Path(profile).resolve()))
    pids = set()
    for process in psutil.process_iter(['pid', 'name', 'username']):
        if (process.info['name'] or '').lower() not in {'msedge.exe', 'chrome.exe', 'brave.exe', 'chromium.exe'}:
            continue
        try:
            if process.username() != psutil.Process().username():
                continue
            for arg in process.cmdline():
                if arg.startswith('--user-data-dir=') and os.path.normcase(str(Path(arg.split('=', 1)[1]).resolve())) == wanted:
                    pids.add(process.pid)
                    pids.update(child.pid for child in process.children(recursive=True))
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as error:
            raise RuntimeError('Cannot verify the existing Orion window') from error
    user = ctypes.WinDLL('user32', use_last_error=True)
    from ctypes import wintypes
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    found = []
    def visit(window, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value in pids and user.IsWindowVisible(window):
            user.ShowWindow(window, 9)
            user.SetForegroundWindow(window)
            found.append(window)
            return False
        return True
    # A rapid second click can arrive between browser CreateProcess and HWND
    # creation. Keep that click inside the launch lock while the window appears.
    for _ in range(30 if pids else 1):
        user.EnumWindows(callback_type(visit), 0)
        if found:
            break
        if pids:
            time.sleep(.1)
    return bool(found)


def known_folder(name):
    ids = {'desktop': 'B4BFCC3A-DB2C-424C-B029-7FE99A87C641',
           'startup': 'B97D20BB-F46A-4C97-BA10-5E3608430854'}
    if os.name != 'nt':
        raise RuntimeError('Windows shortcuts require Windows')
    guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(ids[name]).bytes_le)
    pointer = ctypes.c_wchar_p()
    shell = ctypes.WinDLL('shell32')
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_wchar_p)]
    result = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pointer))
    if result:
        raise OSError('Windows user folder unavailable')
    try:
        return Path(pointer.value)
    finally:
        ole = ctypes.WinDLL('ole32')
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree(ctypes.cast(pointer, ctypes.c_void_p))


def shortcut(path, entry, installation_id, *, startup=False, remove=False):
    """Refuse to replace another application's or installation's shortcut."""
    target = Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    args = subprocess.list2cmdline(['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle',
                                  'Hidden', '-File', str(entry), *(['-Startup'] if startup else [])])
    payload = dict(path=str(path), target=str(target), arguments=args,
                   description='Orion launcher ' + installation_id, remove=remove)
    command = """
$ErrorActionPreference = 'Stop'
$p = $env:ORION_LINK_REQUEST | ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $p.path) {
    $old = $shell.CreateShortcut($p.path)
    if ($old.Description -cne $p.description -or $old.TargetPath -ine $p.target -or $old.Arguments -cne $p.arguments) {
        throw 'Shortcut ownership mismatch; existing shortcut preserved'
    }
}
if ($p.remove) {
    if (Test-Path -LiteralPath $p.path) { Remove-Item -LiteralPath $p.path }
} else {
    $link = $shell.CreateShortcut($p.path)
    $link.TargetPath = $p.target
    $link.Arguments = $p.arguments
    $link.Description = $p.description
    $link.WorkingDirectory = [IO.Path]::GetDirectoryName($p.path)
    $link.WindowStyle = 7
    $link.Save()
}
"""
    try:
        subprocess.run([str(target), '-NoProfile', '-NonInteractive', '-Command', command],
                       env=dict(os.environ, ORION_LINK_REQUEST=json.dumps(payload)),
                       check=True, capture_output=True, timeout=30, creationflags=0x08000000)
    except subprocess.SubprocessError as error:
        raise RuntimeError('Windows shortcut could not be changed; check shortcut ownership and folder access') from error
