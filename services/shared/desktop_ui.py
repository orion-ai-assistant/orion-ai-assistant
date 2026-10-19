"""Orion's shared look for the bootstrap's Windows dialogs (`orion_ui.ps1`, installed as `Orion-Ui.ps1`).

Every writer of a bootstrap script (Orion.ps1, Orion-Tray.ps1, Orion-Updating.ps1, Orion-Uninstall.ps1)
also writes this file next to it, from the same code version, so the scripts can dot-source it. Cosmetic:
installing it never raises; the entry script falls back to plain dialogs without it.
"""
from pathlib import Path

SCRIPT = 'Orion-Ui.ps1'


def install_ui(folder):
    try:
        from .installation_registry import _atomic_write
        source = Path(__file__).with_name('orion_ui.ps1').read_bytes()
        destination = Path(folder) / SCRIPT
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_bytes() != source:
            _atomic_write(destination, source)
        return True
    except OSError:
        return False
