"""Isolated stdlib Windows host: own a job before launching application code.

Base Python -I -S prevents user/site hooks from spawning uncontained children.
The durable launch intent precedes this host. It keeps a job handle until the
direct application exits; the supervisor keeps its own handle until tree stop.
If both controllers die, KILL_ON_JOB_CLOSE prevents unobserved orphan survivors.
"""
import ctypes
from ctypes import wintypes as W
import subprocess
import sys
from pathlib import Path


def main():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenJobObjectW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
    kernel.OpenJobObjectW.restype = W.HANDLE
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    job = kernel.OpenJobObjectW(0x1, False, sys.argv[1])
    if not job:
        raise RuntimeError('Lifecycle job missing or inaccessible; application not started')
    try:
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise RuntimeError('Cannot contain application process tree')
        if Path(sys.argv[2]).exists():
            return 1  # A recovered pending launch was canceled before admission.
        # No breakaway flags: venv launchers, reloaders and native applications
        # inherit membership as part of CreateProcess itself.
        process = subprocess.Popen(sys.argv[3:])
        return process.wait()
    finally:
        kernel.CloseHandle(job)


if __name__ == '__main__':
    raise SystemExit(main())
