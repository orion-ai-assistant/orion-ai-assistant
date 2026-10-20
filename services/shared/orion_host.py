"""The installation's resident Orion.exe ("host"): the one long-lived root of every Orion process.

Orion.exe (scripts/release/launcher/Orion.cs) stays running per installation, is the notification-area icon
and holds the only handle of a kill-on-close job it runs in. Supervisors and the update watcher are created
as ITS children (PROC_THREAD_ATTRIBUTE_PARENT_PROCESS), whoever starts them - the launcher, the watcher's
"update now" restart, a health probe: Task Manager shows one "Orion AI Assistant" entry with everything under
it, the processes inherit the host's job (their own jobs nest inside it), and "End task" on it ends them all.
Without a host (development, an Orion.exe that predates it, a non-interactive run) processes start as before.
"""
import json
import os
from pathlib import Path
import subprocess

WINDOW = 'Orion-Host-'             # + installation id: title of the host's hidden window
JOB = 'Local\\Orion-HostJob-'      # + installation id: the host's kill-on-close job
EXIT_MARKER = 'bootstrap/host.exit'  # the launcher stopped everything: the host ends
NAMES = {'orion.exe', 'orionrouter.exe'}


def _registry_path(registry):
    if registry is None:
        registry = os.environ.get('ORION_INSTALLATION_REGISTRY')
    return Path(getattr(registry, 'path', registry)) if registry else None


def _installation_id(registry):
    path = _registry_path(registry)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig')).get('installation_id')
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value and len(value) <= 64 else None


def find(registry=None):
    """This installation's running host (a psutil.Process), or None."""
    if os.name != 'nt':
        return None
    identity = _installation_id(registry)
    if identity is None:
        return None
    import ctypes
    from ctypes import wintypes as W
    import psutil
    user = ctypes.WinDLL('user32')
    user.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
    user.FindWindowW.restype = W.HWND
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    window = user.FindWindowW(None, WINDOW + identity)
    if not window:
        return None
    pid = W.DWORD()
    user.GetWindowThreadProcessId(window, ctypes.byref(pid))
    try:
        process = psutil.Process(pid.value)
        return process if process.name().lower() in NAMES else None
    except (psutil.Error, OSError):
        return None


def inside(registry=None):
    """True when this process runs inside the host's job (the host's end would end it too)."""
    if os.name != 'nt':
        return False
    identity = _installation_id(registry)
    if identity is None:
        return False
    import ctypes
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
    kernel.OpenJobObjectW.restype = W.HANDLE
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    job = kernel.OpenJobObjectW(0x4, False, JOB + identity)  # JOB_OBJECT_QUERY
    if not job:
        return False
    try:
        result = W.BOOL()
        return bool(kernel.IsProcessInJob(kernel.GetCurrentProcess(), job, ctypes.byref(result)) and result.value)
    finally:
        kernel.CloseHandle(job)


def release(registry, *, timeout=10):
    """Every service is stopped: the host ends (and its job with it). A caller inside the host's job (the host's
    own "Quit" run) is not waited for: the host ends once that run has finished. Returns the host or None."""
    host = find(registry)
    if host is None:
        return None
    marker = _registry_path(registry).parent / EXIT_MARKER
    marker.write_bytes(b'exit')
    if inside(registry):
        return host
    import psutil
    try:
        host.wait(timeout)
    except psutil.TimeoutExpired:
        try:
            host.terminate()  # Everything it hosted is stopped already.
            host.wait(5)
        except psutil.Error:
            pass
    except psutil.Error:
        pass
    marker.unlink(missing_ok=True)
    return host


class HostedProcess:
    """The part of Popen that the callers use (pid, poll, wait, returncode) for a process created under the host."""

    def __init__(self, handle, pid, args):
        self._handle, self.pid, self.args, self.returncode = handle, pid, args, None

    def poll(self):
        return self.wait(0) if self.returncode is None else self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        import ctypes
        from ctypes import wintypes as W
        kernel = ctypes.WinDLL('kernel32')
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
        milliseconds = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
        if kernel.WaitForSingleObject(self._handle, milliseconds) != 0:
            if timeout == 0:
                return None
            raise subprocess.TimeoutExpired(self.args, timeout)
        code = W.DWORD()
        kernel.GetExitCodeProcess(self._handle, ctypes.byref(code))
        self.returncode = code.value
        self._close()
        return self.returncode

    def _close(self):
        if self._handle:
            import ctypes
            ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None

    def __del__(self):
        try:
            self._close()
        except Exception:
            pass


def _registered(registry, component_root):
    """`component_root` is the code folder of one of this installation's components."""
    from .installation_paths import canonical_path
    try:
        record = json.loads(_registry_path(registry).read_text(encoding='utf-8-sig'))
        wanted = canonical_path(component_root)
        return any(canonical_path(item['code_path']) == wanted for item in record.get('components', []))
    except Exception:  # Unreadable or foreign: no host.
        return False


def popen(argv, *, registry=None, component_root=None, require_host=False, cwd=None, env=None, stdin=None,
          stdout=None, stderr=None, creationflags=0, **fallback):
    """Start `argv` as a child of this installation's host (inside its job); subprocess.Popen without a host.

    `component_root`: only a process of one of this installation's components goes under its host (a test or
    development tree that happens to run with the registry in its environment never does). `require_host`: raise
    OSError instead of starting it another way."""
    host = None
    if subprocess.PIPE not in (stdin, stdout, stderr) and (component_root is None or _registered(registry, component_root)):
        host = find(registry)
    if host is not None:
        try:
            return _spawn_under(host.pid, [str(arg) for arg in argv], cwd, env, (stdin, stdout, stderr), creationflags)
        except OSError:
            if require_host:
                raise
            # The host just ended (or refused): start the way Orion always did.
    if require_host:
        raise OSError('No resident Orion.exe to start the process under')
    return subprocess.Popen(argv, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
                            creationflags=creationflags, **fallback)


def _spawn_under(host_pid, argv, cwd, env, streams, creationflags):
    """CreateProcess with the host as parent. The standard handles are duplicated into the host and passed with
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST (with a parent attribute, handles are inherited from that parent), then
    closed there again."""
    import ctypes
    import msvcrt
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR), ('lpTitle', W.LPWSTR),
                    ('dwX', W.DWORD), ('dwY', W.DWORD), ('dwXSize', W.DWORD), ('dwYSize', W.DWORD),
                    ('dwXCountChars', W.DWORD), ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
                    ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                    ('lpReserved2', ctypes.c_void_p), ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE),
                    ('hStdError', W.HANDLE)]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [('StartupInfo', STARTUPINFOW), ('lpAttributeList', ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]

    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.DuplicateHandle.argtypes = [W.HANDLE, W.HANDLE, W.HANDLE, ctypes.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD]
    kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, W.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                                 ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, W.BOOL, W.DWORD,
                                      ctypes.c_void_p, W.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION)]
    kernel.CloseHandle.argtypes = [W.HANDLE]

    def fail(what):
        raise ctypes.WinError(ctypes.get_last_error(), what)

    # PROCESS_CREATE_PROCESS | PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION
    host = kernel.OpenProcess(0x80 | 0x40 | 0x1000, False, host_pid)
    if not host:
        fail('Orion host unavailable')
    opened, duplicated = [], []
    attributes = None
    try:
        sources = []
        for stream in streams:
            if stream == subprocess.STDOUT:
                sources.append(sources[-1])
                continue
            if stream is None or stream == subprocess.DEVNULL:
                null = open(os.devnull, 'r+b')
                opened.append(null)
                stream = null
            fileno = stream if isinstance(stream, int) else stream.fileno()
            sources.append(msvcrt.get_osfhandle(fileno))
        targets = {}
        for source in sources:
            if source not in targets:
                target = W.HANDLE()
                # Inheritable in the host, same access.
                if not kernel.DuplicateHandle(kernel.GetCurrentProcess(), source, host, ctypes.byref(target), 0, True, 2):
                    fail('Cannot hand a standard handle to the Orion host')
                targets[source] = target.value
                duplicated.append(target.value)
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
            attributes = None
            fail('Cannot prepare process attributes')
        parent = W.HANDLE(host)
        handles = (W.HANDLE * len(duplicated))(*duplicated)
        if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020000, ctypes.byref(parent), ctypes.sizeof(parent),
                                                None, None):  # PROC_THREAD_ATTRIBUTE_PARENT_PROCESS
            fail('Cannot set the Orion host as parent')
        if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020002, handles, ctypes.sizeof(handles),
                                                None, None):  # PROC_THREAD_ATTRIBUTE_HANDLE_LIST
            fail('Cannot limit the inherited handles')
        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        startup.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = (
            targets[source] for source in sources)
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        block = None
        if env is not None:
            text = ''.join(f'{key}={value}\0' for key, value in sorted(env.items(), key=lambda item: item[0].upper()))
            block = ctypes.create_unicode_buffer(text + '\0', len(text) + 1)
        information = PROCESS_INFORMATION()
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        # EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT
        flags = creationflags | 0x80000 | 0x400
        if not kernel.CreateProcessW(None, command, None, None, True, flags,
                                     ctypes.cast(block, ctypes.c_void_p) if block is not None else None,
                                     str(cwd) if cwd else None, ctypes.byref(startup), ctypes.byref(information)):
            fail('Cannot start a process under the Orion host')
        kernel.CloseHandle(information.hThread)
        return HostedProcess(information.hProcess, information.dwProcessId, argv)
    finally:
        if attributes is not None:
            kernel.DeleteProcThreadAttributeList(attributes)
        for target in duplicated:
            kernel.DuplicateHandle(host, target, None, None, 0, False, 1)  # DUPLICATE_CLOSE_SOURCE: closed in the host
        for item in opened:
            item.close()
        kernel.CloseHandle(host)
