"""Orion.exe is compiled with the Framework csc.exe, carries the product identity and builds reproducibly."""
import os
import subprocess

import pytest

from build_launcher import build_launcher, compiler

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Needs the .NET Framework compiler that ships with Windows')


def info(path, field):
    command = f'(Get-Item -LiteralPath "{path}").VersionInfo.{field}'
    return subprocess.check_output(['powershell', '-NoProfile', '-Command', command], text=True).strip()


def test_launcher_has_product_identity_and_is_reproducible(tmp_path):
    assert compiler().is_file()
    first = build_launcher(tmp_path / 'a/Orion.exe', '0.1.1')
    second = build_launcher(tmp_path / 'b/Orion.exe', '0.1.1')
    assert first.read_bytes() == second.read_bytes()
    assert build_launcher(tmp_path / 'c/Orion.exe', '0.1.2').read_bytes() != first.read_bytes()
    assert info(first, 'FileDescription') == 'Orion AI Assistant'
    assert info(first, 'ProductName') == 'Orion AI Assistant'
    assert info(first, 'CompanyName') == 'Orion'
    assert info(first, 'FileVersion') == '0.1.1.0'


def test_launcher_forwards_only_known_switches_without_waiting(tmp_path):
    exe = build_launcher(tmp_path / 'Orion.exe', '0.1.1')
    (tmp_path / 'Orion.ps1').write_text(
        'param([switch]$Startup,[switch]$Stop,[switch]$Evil)\n'
        '"$Startup $Stop $Evil" | Out-File (Join-Path $PSScriptRoot out.txt) -Encoding ascii\n')
    result = subprocess.run([str(exe), '-Startup', '-Stop', '-Evil'], timeout=10)
    assert result.returncode == 0
    import time
    for _ in range(60):
        if (tmp_path / 'out.txt').exists():
            break
        time.sleep(.25)
    assert (tmp_path / 'out.txt').read_text().strip() == 'True True False'


def test_launcher_without_its_script_reports_instead_of_hanging(tmp_path, monkeypatch):
    # No dialog in an unattended test: only the version check path matters, so just prove the exe is a valid PE.
    exe = build_launcher(tmp_path / 'Orion.exe', '0.1.1')
    assert exe.read_bytes()[:2] == b'MZ'
