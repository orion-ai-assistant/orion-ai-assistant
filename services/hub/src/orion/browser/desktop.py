"""Small Windows desktop helpers for the user's installed Chromium browsers."""
import os
import base64
import csv
import io
import subprocess
import threading
from contextlib import contextmanager


EXECUTABLES = {'chrome.exe': 'chrome', 'brave.exe': 'brave', 'msedge.exe': 'edge'}


@contextmanager
def maintain_foreground():
    """Ensure the user's active foreground window does not lose focus during automation."""
    if os.name != 'nt':
        yield
        return
    import ctypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    fg = user32.GetForegroundWindow()
    try:
        yield
    finally:
        if fg:
            try:
                current = user32.GetForegroundWindow()
                if current != fg and user32.IsWindow(fg):
                    user32.SetForegroundWindow(fg)
            except Exception:
                pass


def browser_windows():
    """Return visible browser windows in foreground/z order, without changing them."""
    if os.name != 'nt':
        return []
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    foreground = user32.GetForegroundWindow()
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            name = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(name))
            if kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
                choice = EXECUTABLES.get(os.path.basename(name.value).lower())
                if choice:
                    windows.append((int(hwnd), choice))
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows(visit, 0)
    windows.sort(key=lambda item: item[0] != foreground)
    return windows


def suggested_browser():
    windows = browser_windows()
    if windows:
        return windows[0][1]
    running = running_browser_choices()
    return next(iter(running)) if running is not None and len(running) == 1 else None


def running_browser_choices():
    """Process-only fallback when the Hub cannot inspect the interactive desktop."""
    if os.name != 'nt':
        return set()
    try:
        import psutil
        running = set()
        for process in psutil.process_iter(['name']):
            name = (process.info.get('name') or '').lower()
            if name in EXECUTABLES:
                running.add(EXECUTABLES[name])
        return running
    except ImportError:
        pass
    except (OSError, PermissionError):
        pass
    try:
        result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=3,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            return None
        rows = csv.reader(io.StringIO(result.stdout.decode('utf-8-sig', errors='replace')))
        return {EXECUTABLES[row[0].lower()] for row in rows if row and row[0].lower() in EXECUTABLES}
    except (OSError, subprocess.TimeoutExpired):
        return None


def minimize_window(hwnd):
    if os.name != 'nt' or hwnd is None:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    if not user32.IsWindow(hwnd):
        return False
    user32.ShowWindowAsync(hwnd, 6)  # SW_MINIMIZE, for this new setup window only.
    return True


def close_window(hwnd):
    if os.name != 'nt' or hwnd is None:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    if not user32.IsWindow(hwnd):
        return False
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return True


def enable_remote_debugging(choice):
    """Open choice://inspect/#remote-debugging in a new window, toggle checkbox, and immediately close it.

    Operates strictly on the newly opened window handle so user tabs and windows are never touched.
    """
    if os.name != 'nt':
        return False
    from .manager import find_binary, discover_endpoint
    try:
        binary = find_binary(choice)
    except Exception:
        return False

    target_url = f'{choice}://inspect/#remote-debugging'
    previous = {hwnd for hwnd, _ in browser_windows()}
    try:
        subprocess.Popen([binary, '--new-window', target_url],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        return False

    import time
    inspect_hwnd = None
    for _ in range(30):
        time.sleep(0.1)
        current = [(hwnd, name) for hwnd, name in browser_windows()
                   if name == choice and hwnd not in previous]
        if current:
            inspect_hwnd = current[0][0]
            break

    if inspect_hwnd is None:
        return False
    script = r'''
param([long]$WindowHandle, [string]$TargetUrl)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Windows.Forms

$user32 = Add-Type -MemberDefinition @"
[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
"@ -Name "Win32Util" -Namespace "Util" -PassThru

$root = $null
for ($i = 0; $i -lt 30; $i++) {
    $elem = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
    if ($null -ne $elem) { $root = $elem; break }
    Start-Sleep -Milliseconds 100
}
if ($null -eq $root) { exit 2 }

[Util.Win32Util]::SetForegroundWindow([IntPtr]$WindowHandle)

# 1. Omnibox navigation
$editCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Edit)

$navigated = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $edits = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCond)
    foreach ($edit in $edits) {
        $cls = $edit.Current.ClassName
        if ($cls -eq 'OmniboxViewViews' -or $cls -match 'omnibox') {
            try {
                $valPat = $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                if ($valPat) {
                    $valPat.SetValue($TargetUrl)
                    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
                    $navigated = $true
                    break
                }
            } catch {}
        }
    }
    if ($navigated) { break }
    Start-Sleep -Milliseconds 100
}

# 2. Toggle checkbox and immediately close this inspect window
$checkCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::CheckBox)

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 200
    $boxes = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $checkCond)
    foreach ($box in $boxes) {
        if ($box.Current.Name -eq 'Allow remote debugging for this browser instance') {
            try {
                $pattern = $box.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
                if ($pattern.Current.ToggleState -eq [System.Windows.Automation.ToggleState]::Off) {
                    $pattern.Toggle()
                }
            } catch {}
            Start-Sleep -Milliseconds 300
            [Util.Win32Util]::PostMessage([IntPtr]$WindowHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
            exit 0
        }
    }
}
[Util.Win32Util]::PostMessage([IntPtr]$WindowHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
exit 1
'''
    invocation = script.replace('param([long]$WindowHandle, [string]$TargetUrl)',
                                f'$WindowHandle = {int(inspect_hwnd)}; $TargetUrl = "{target_url}"', 1)
    encoded = base64.b64encode(invocation.encode('utf-16-le')).decode('ascii')
    try:
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                        '-EncodedCommand', encoded], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        pass

    close_window(inspect_hwnd)
    for _ in range(20):
        if discover_endpoint(choice):
            return True
        time.sleep(0.1)
    return False


_approver_lock = threading.Lock()
_approver_running = False


def auto_approve_browser_dialogs(choice=None, timeout_secs=10):
    """Gentle helper that clicks 'İzin ver' / 'Allow' if the remote debugging prompt appears.

    Polls gently (once per second) so it never floods the UI thread or freezes the browser,
    leaving the user free to click the button directly as well.
    """
    if os.name != 'nt':
        return False
    global _approver_running
    with _approver_lock:
        if _approver_running:
            return False
        _approver_running = True

    try:
        script = r'''
param([string]$BrowserChoice, [int]$TimeoutSecs)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Windows.Forms

$user32 = Add-Type -MemberDefinition @"
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
"@ -Name "Win32Util2" -Namespace "Util2" -PassThru

$btnCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button)

$procNames = if ($BrowserChoice) { @($BrowserChoice) } else { @('chrome', 'brave', 'msedge') }
$deadline = [System.DateTime]::UtcNow.AddSeconds($TimeoutSecs)

while ([System.DateTime]::UtcNow -lt $deadline) {
    $pids = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($pname in $procNames) {
        $procs = Get-Process -Name $pname -ErrorAction SilentlyContinue
        if ($procs) {
            foreach ($p in $procs) { [void]$pids.Add($p.Id) }
        }
    }

    if ($pids.Count -gt 0) {
        # 1. Instant check: Is the focused element the prompt or button?
        try {
            $f = [System.Windows.Automation.AutomationElement]::FocusedElement
            if ($null -ne $f -and $pids.Contains($f.Current.ProcessId)) {
                $fname = $f.Current.Name
                if ($fname -match '(?i)(izin\s*ver|\u0130zin\s*ver|allow|onayla)') {
                    try {
                        $inv = $f.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                        if ($inv) { $inv.Invoke(); exit 0 }
                    } catch {}
                    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
                    exit 0
                }
            }
        } catch {}

        # 2. Check top-level desktop windows belonging to the target browser
        try {
            $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
                [System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.Condition]::TrueCondition)
            foreach ($w in $windows) {
                if (-not $pids.Contains($w.Current.ProcessId)) { continue }
                try {
                    $buttons = $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCondition)
                    foreach ($btn in $buttons) {
                        $name = $btn.Current.Name
                        if ($name -match '(?i)(izin\s*ver|\u0130zin\s*ver|allow|onayla|confirm|accept)' -and
                            $name -notmatch '(?i)(cancel|iptal|deny|block|reddet|don''t|kapat|settings)') {
                            if ($btn.Current.IsEnabled) {
                                try {
                                    $inv = $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                                    if ($inv) { $inv.Invoke(); exit 0 }
                                } catch {}
                                try {
                                    [Util2.Win32Util2]::SetForegroundWindow([IntPtr]$w.Current.NativeWindowHandle)
                                    Start-Sleep -Milliseconds 20
                                    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
                                    exit 0
                                } catch {}
                            }
                        }
                    }
                } catch {}
            }
        } catch {}
    }

    Start-Sleep -Milliseconds 60
}
exit 1
'''
        choice_arg = choice or ''
        invocation = script.replace('param([string]$BrowserChoice, [int]$TimeoutSecs)',
                                    f'$BrowserChoice = "{choice_arg}"; $TimeoutSecs = {int(timeout_secs)}', 1)
        encoded = base64.b64encode(invocation.encode('utf-16-le')).decode('ascii')
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                     '-EncodedCommand', encoded], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     timeout=timeout_secs + 3, creationflags=subprocess.CREATE_NO_WINDOW)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    finally:
        with _approver_lock:
            _approver_running = False


