"""Fresh AppData/C-to-workspace content migration; never uses user content."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lifecycle.run_registry_acceptance import public_manifests
from scripts.lifecycle.test_environment import snapshot
from services.shared.bootstrap_registration import register_bootstrap
from services.shared.content_migration import registry_revision
from services.shared.migration_host import move_content, recover_content
from services.shared.installation_registry import Registry
from services.shared.installation_paths import validate_private_directory
from services.shared.model_locations import models_directory


class SimulatedCrash(BaseException):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--appdata', type=Path, required=True)
    args = parser.parse_args()
    appdata = args.appdata.resolve(strict=True)
    before = public_manifests(appdata)
    base = appdata / 'Orion/lifecycle-tests'
    base.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='stage03-', dir=base))
    validate_private_directory(scratch)
    main_root = scratch / 'Orion'
    code, models = main_root / 'Hub', main_root / 'Models'
    project = code / 'services/hub/pyproject.toml'
    project.parent.mkdir(parents=True)
    project.write_text('[project]\nname="orion-hub"\n', encoding='utf-8')
    environment = code / 'services/.env.global.local'
    environment.write_text('POSTGRES_PASSWORD=synthetic-only\n', encoding='utf-8')
    store = Registry(scratch / 'profile/Orion/install.json')
    record = register_bootstrap(code, root=main_root, models_path=models, registry_path=store.path)
    service = code / 'services/llm/fixture'
    service.mkdir(parents=True)
    content = models / 'fixture'
    content.mkdir(parents=True)
    (content / 'model ü.gguf').write_bytes(b'synthetic model; never loaded' * 4096)
    source_hashes, source_code = snapshot(models), snapshot(code)
    destination = Path(tempfile.mkdtemp(prefix='stage03-cross-volume-', dir=ROOT / '.tmp'))

    def crash(phase, journal):
        if phase == 'verified':
            raise SimulatedCrash()
    try:
        move_content(store, 'models', destination, expected_revision=registry_revision(record), fault=crash)
        raise AssertionError('Fault injection did not run')
    except SimulatedCrash:
        pass
    assert store.read() == record
    journal = next((store.path.parent / 'migrations').glob('*.json'))
    recovered = recover_content(store, journal)
    assert recovered['phase'] == 'completed'
    assert snapshot(models) == source_hashes
    assert snapshot(code) == source_code
    assert store.read()['components'] == record['components']
    assert models_directory('fixture', service, {}, mode='local',
                            settings={'ORION_INSTALLATION_REGISTRY': str(store.path)}) == destination / 'fixture'
    assert before == public_manifests(appdata)
    report = dict(success=True, scope='synthetic Models only; no DB/runtime migration or product installation',
                  source=str(models), destination=str(destination), registry=str(store.path), journal=str(journal),
                  source_volume=models.anchor, destination_volume=destination.anchor,
                  source_and_config_hashes_preserved=True, component_ids_preserved=True,
                  real_public_manifests_preserved=True, verified_boundary_crash_recovered=True)
    report['host_gate'] = 'real stopped-installation and native operation locks'
    (scratch / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'result': str(scratch / 'result.json'), **report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
