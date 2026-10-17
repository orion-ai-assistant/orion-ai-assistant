"""Configuration for the bundled interpreter; never patches vendor executables."""
from pathlib import Path
import subprocess


def install_site_hook(python):
    python = Path(python)
    hook = python / 'site-packages/sitecustomize.py'
    # _pth isolates Python from PYTHONIOENCODING/PYTHONDONTWRITEBYTECODE.
    # Do not relaunch (Windows Job ownership must keep its original host PID).
    hook.write_text(
        'import sys\nsys.dont_write_bytecode = True\n'
        'for stream in (sys.stdin, sys.stdout, sys.stderr):\n'
        '    if hasattr(stream, "reconfigure"):\n'
        '        stream.reconfigure(encoding="utf-8", errors="replace")\n', encoding='utf-8')
    subprocess.run([str(python / 'python.exe'), '-B', '-c',
        'import py_compile,sys; py_compile.compile(sys.argv[1], cfile=sys.argv[1]+"c", '
        'dfile="orion-runtime/sitecustomize.py", doraise=True, '
        'invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)', str(hook)], check=True)
    hook.unlink()
