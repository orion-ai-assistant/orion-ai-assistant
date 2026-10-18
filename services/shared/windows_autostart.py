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


PRODUCT_NAME = 'Orion AI Assistant'
ROUTER_PRODUCT_NAME = 'Orion Router'
PRODUCT_NAMES = (PRODUCT_NAME, ROUTER_PRODUCT_NAME)
LAUNCHER_EXE = 'Orion.exe'

STARTUP_APPROVED = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved StartupFolder'


def clean_startup_approved(keep_names, stale_names, *, hive=None, key=STARTUP_APPROVED):
    """Delete orphaned Task Manager "Startup apps" state values of Orion's own link names.

    Explorer keeps one StartupApproved StartupFolder value per Startup link NAME and never deletes it
    when the link goes away. An old `Orion.lnk` value (installs before the product-named link) then
    keeps a ghost entry in Task Manager > Startup, listed by the executable's file name ("Orion.exe")
    instead of "Orion AI Assistant". A value is removed only when its link is absent or is named in
    `stale_names`; names in `keep_names` (the links that exist now) and every foreign value stay.
    Returns the names that were removed. Never raises: this is cosmetic state."""
    if os.name != 'nt':
        return []
    removed = []
    try:
        import winreg
        hive = hive if hive is not None else winreg.HKEY_CURRENT_USER
        with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as handle:
            names = []
            index = 0
            while True:
                try:
                    names.append(winreg.EnumValue(handle, index)[0])
                except OSError:
                    break
                index += 1
            for name in names:
                if name.casefold() in {n.casefold() for n in stale_names} and name.casefold() not in {
                        n.casefold() for n in keep_names}:
                    winreg.DeleteValue(handle, name)
                    removed.append(name)
    except (OSError, ImportError):
        pass
    return removed


def shortcut(path, entry, installation_id, *, startup=False, remove=False, product=PRODUCT_NAME):
    """Refuse to replace another application's or installation's shortcut.

    The link targets Orion.exe (product name and icon) when it sits next to the PowerShell entry;
    without it the PowerShell form is used. An existing link is ours if it matches either form,
    so installations created before Orion.exe existed are upgraded in place and never foreign-blocked.
    """
    powershell = Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    exe = Path(entry).with_name(LAUNCHER_EXE)
    legacy = dict(target=str(powershell), description='Orion launcher ' + installation_id,
                  arguments=subprocess.list2cmdline(['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle',
                                                     'Hidden', '-File', str(entry), *(['-Startup'] if startup else [])]))
    forms = [dict(target=str(exe), description=name, arguments='-Startup' if startup else '') for name in PRODUCT_NAMES]
    wanted = dict(target=str(exe), description=product, arguments='-Startup' if startup else '')
    chosen = wanted if exe.is_file() else legacy
    # Either product name is ours, so a Router-only link is restyled in place when Hub is added (and back).
    payload = dict(path=str(path), **chosen, icon=str(exe) + ',0' if chosen is wanted else '',
                   owned=[legacy, *forms], remove=remove)
    command = """
$ErrorActionPreference = 'Stop'
$p = $env:ORION_LINK_REQUEST | ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $p.path) {
    $old = $shell.CreateShortcut($p.path)
    $ours = $false
    foreach ($o in $p.owned) {
        if ($old.Description -ceq $o.description -and $old.TargetPath -ieq $o.target -and $old.Arguments -ceq $o.arguments) { $ours = $true }
    }
    if (-not $ours) { throw 'Shortcut ownership mismatch; existing shortcut preserved' }
}
if ($p.remove) {
    if (Test-Path -LiteralPath $p.path) { Remove-Item -LiteralPath $p.path }
} else {
    $link = $shell.CreateShortcut($p.path)
    $link.TargetPath = $p.target
    $link.Arguments = $p.arguments
    $link.Description = $p.description
    if ($p.icon) { $link.IconLocation = $p.icon }
    $link.WorkingDirectory = [IO.Path]::GetDirectoryName($p.path)
    $link.WindowStyle = 7
    $link.Save()
}
"""
    try:
        subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command],
                       env=dict(os.environ, ORION_LINK_REQUEST=json.dumps(payload)),
                       check=True, capture_output=True, timeout=30, creationflags=0x08000000)
    except subprocess.SubprocessError as error:
        raise RuntimeError('Windows shortcut could not be changed; check shortcut ownership and folder access') from error
