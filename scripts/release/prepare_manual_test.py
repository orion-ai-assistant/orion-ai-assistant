"""Validate a successful, stopped Stage 05 fixture and add a web test entry.

No install, update, identity/config rewrite, Startup link or Desktop link. The
selected AppData fixture is explicitly supplied and must have passed acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.shared.installation_paths import canonical_path, validate_private_directory
from services.shared.installation_registry import Registry, _atomic_write
from services.shared.release_packages import verify_package_record
from services.shared.update_helper import StoppedPackages


def prepare(fixture):
    fixture = canonical_path(fixture, must_exist=True)
    validate_private_directory(fixture)
    report = json.loads((fixture / 'result.json').read_text(encoding='utf-8'))
    if report.get('success') is not True or report.get('trust') != 'ephemeral-test-only':
        raise ValueError('A successful isolated package acceptance fixture is required')
    registry = Registry(fixture / 'install.json')
    record = registry.read()
    if record['preferences']['automatic_updates'] or record['preferences']['start_with_windows']:
        raise ValueError('Manual test requires updates and Startup disabled')
    for item in record['components']:
        for field in ('code_path', 'data_path', 'state_path'):
            if not canonical_path(item[field], must_exist=True).is_relative_to(fixture):
                raise ValueError('Manual fixture cannot manage an outside path')
        verify_package_record(item)
    os.environ['ORION_INSTALLATION_REGISTRY'] = str(registry.path)
    with StoppedPackages(record) as stopped:
        if not stopped:
            raise ValueError('Fixture must be fully stopped before preparation')
    source = '''param([switch]$Stop, [switch]$Background)
$ErrorActionPreference = 'Stop'
$fixture = $PSScriptRoot
$registry = Join-Path $fixture 'install.json'
$record = Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
$hub = @($record.components | Where-Object { $_.kind -eq 'hub' })[0].code_path
$env:ORION_INSTALLATION_REGISTRY = $registry
$env:ORION_SKIP_GLOBAL_REGISTRATION = '1'
$env:ORION_FLUTTER_OPEN_WINDOW = '0'
$env:ORION_ROUTER_MDNS = '0'
$env:ORION_HUB_MDNS = '0'
foreach ($name in @('LOCALAPPDATA','APPDATA','TEMP','TMP')) {
    [Environment]::SetEnvironmentVariable($name, (Join-Path $fixture $name), 'Process')
}
$env:PATH = Join-Path $env:SystemRoot 'System32'
$python = Join-Path $hub 'runtime/python/python.exe'
Push-Location -LiteralPath $hub
try {
    $action = if ($Stop) { 'stop' } else { 'launch' }
    & $python -B -m services.shared.launcher --registry $registry --background $action
    if ($LASTEXITCODE -ne 0) { throw 'Orion test start/stop failed; check fixture logs.' }
} finally { Pop-Location }
if (-not $Stop -and -not $Background) {
    $config = Get-Content -LiteralPath (Join-Path $fixture 'fixture.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Start-Process ("http://127.0.0.1:" + $config.settings.hub.ORION_FLUTTER_PORT + "/")
}
'''
    entry = fixture / 'Try-Orion.ps1'
    _atomic_write(entry, source.encode('utf-8'))
    settings = json.loads((fixture / 'fixture.json').read_text(encoding='utf-8'))['settings']['hub']
    return {'entry': str(entry), 'url': 'http://127.0.0.1:' + str(settings['ORION_FLUTTER_PORT']) + '/',
            'status': 'verified-stopped', 'trust': 'local-test-only'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    print(json.dumps(prepare(parser.parse_args().fixture)))
