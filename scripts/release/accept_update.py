"""Real Stage 05 -> 06 update with SDK-free helper and ephemeral TUF fixture.

Fresh disposable AppData installation only. Sequential product/DB work. This is
neither a clean Windows VM nor physical power-cut acceptance.
"""
import argparse
from contextlib import ExitStack
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from accept_packages import run, FixtureFetcher, fingerprint, package_names
from publish_metadata import publish
from services.shared.installation_registry import Registry
from services.shared.release_packages import sha256


def check_restart(fixture, registry, current, env, log_name):
    from services.shared.process_lifecycle import Runtime, ProcessReference
    from services.shared.update_helper import StoppedPackages
    hub = next(c for c in current['components'] if c['kind'] == 'hub')
    python = str(Path(hub['code_path']) / 'runtime/python/python.exe')
    with (fixture / log_name).open('wb') as log, ExitStack() as owned:
        references = []
        def call(args):
            subprocess.run([python, '-B', *args], cwd=hub['code_path'], env=env,
                           stdout=log, stderr=log, check=True, timeout=180)
        try:
            call(['-m', 'services.shared.launcher', '--registry', str(registry.path), '--background'])
            for item in current['components']:
                if item['kind'] != 'client':
                    mode = 'hub' if item['kind'] == 'hub' else 'router-prod'
                    state = Runtime(item['code_path'], mode).read()
                    references.append(owned.enter_context(ProcessReference(state['supervisor'])))
            call(['-c', "from services.shared.native_services import hub_database; from services.shared.environment import get_env; db=hub_database('.',get_env); assert db.ready(); assert db.sql('SELECT value FROM package_probe') == 'Türkçe 中文'; from orion.kernel.router_access import manual_state; assert manual_state('00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002')['key'] == 'sk-orion-stage05-fixture'"])
        finally:
            call(['-m', 'services.shared.launcher', '--registry', str(registry.path), 'stop'])
            for reference in references:
                reference.wait(15)
    with StoppedPackages(current) as stopped:
        assert stopped, 'Fixture consumers not stopped'


def audit_fixture(fixture):
    """Finish a completed test's restart/cleanup audit after driver interruption."""
    from services.shared.installation_paths import canonical_path, validate_private_directory
    from services.shared.release_packages import verify_package_record
    fixture = canonical_path(fixture, must_exist=True)
    validate_private_directory(fixture)
    report = json.loads((fixture / 'result.json').read_text(encoding='utf-8'))
    result = json.loads((fixture / 'retry-update-result.json').read_text(encoding='utf-8'))
    assert report['trust'] == 'ephemeral-test-only' and result['phase'] == 'completed'
    registry = Registry(fixture / 'install.json')
    current, previous = registry.read(), result['previous']
    assert current == result['proposed']
    for record in (current, previous):
        for item in record['components']:
            for field in ('code_path', 'data_path', 'state_path'):
                assert canonical_path(item[field], must_exist=True).is_relative_to(fixture)
            verify_package_record(item)
    assert current['preferences'] == previous['preferences']
    assert {c['kind']: (c['component_id'], c['data_path']) for c in current['components']} == {
        c['kind']: (c['component_id'], c['data_path']) for c in previous['components']}
    hub = next(c for c in current['components'] if c['kind'] == 'hub')
    protected = {name: digest for name, digest in result['backups']['hub']['files'].items()
                 if Path(name).name in {'hub-key.pem', 'hub-cert.pem', 'hub-id', 'router-access-seed'}
                 or name.endswith('.manual')}
    assert protected and all(sha256(Path(hub['data_path']) / name) == digest for name, digest in protected.items())
    model = Path(current['locations']['models']) / 'fixture-existing.gguf'
    assert model.read_bytes() == b'existing model sentinel; never opened by any engine'
    env = os.environ.copy()
    for name in ('LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP'):
        env[name] = str(fixture / name)
    env.update(ORION_INSTALLATION_REGISTRY=str(registry.path), ORION_SKIP_GLOBAL_REGISTRATION='1',
               ORION_FLUTTER_OPEN_WINDOW='0', ORION_ROUTER_MDNS='0', ORION_HUB_MDNS='0',
               PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
    os.environ['ORION_INSTALLATION_REGISTRY'] = str(registry.path)
    check_restart(fixture, registry, current, env, 'audit-restart.log')
    summary = {'success': True, 'clean_vm': False, 'power_cut': False,
               'phase': result['phase'], 'health': result['health'],
               'checks': ['separate SDK-free recovery bootstrap and same signed target retry',
                          'old/new registered package inventories unchanged', 'UUID/data/preferences preserved',
                          'model and security/manual file hashes preserved', 'real DB row/manual key after restart',
                          'exact supervisor OS exit and stopped DB/children verified']}
    (fixture / 'stage06-result.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    return fixture


def retry_fixture(fixture, inputs):
    """Retry only a stopped, rolled-back ephemeral acceptance fixture.

    Retains the original trust root/repository and old bootstrap. A new recovery
    bootstrap is prepared alongside it, never over its only working copy.
    """
    from build_bootstrap import build
    from services.shared.release_packages import extract_zip
    from services.shared.installation_paths import canonical_path, validate_private_directory
    from services.shared.update_helper import StoppedPackages, UpdateHelper
    fixture = canonical_path(fixture, must_exist=True)
    validate_private_directory(fixture)
    report = json.loads((fixture / 'result.json').read_text(encoding='utf-8'))
    registry = Registry(fixture / 'install.json')
    prior = UpdateHelper(registry).status()
    if report.get('trust') != 'ephemeral-test-only' or prior['phase'] != 'rolled_back':
        raise ValueError('Only a rolled-back disposable acceptance fixture can be retried')
    previous = registry.read()
    for item in previous['components']:
        for field in ('code_path', 'data_path', 'state_path'):
            if not canonical_path(item[field], must_exist=True).is_relative_to(fixture):
                raise ValueError('Acceptance cannot manage paths outside its fixture')
    os.environ['ORION_INSTALLATION_REGISTRY'] = str(registry.path)
    with StoppedPackages(previous) as stopped:
        assert stopped, 'Previous fixture consumers are not stopped'
    hub_old = next(c for c in previous['components'] if c['kind'] == 'hub')
    original = fingerprint(Path(hub_old['code_path']))
    model = Path(previous['locations']['models']) / 'fixture-existing.gguf'
    model_hash = sha256(model)
    archive = build(fixture / 'TEST-ONLY-root.json', inputs,
                    fixture / 'TEST-ONLY-recovery-v2.zip', test_only=True)
    installer = extract_zip(archive, fixture / 'recovery-v2')
    env = os.environ.copy()
    for name in ('LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP'):
        env[name] = str(fixture / name)
    env.update(ORION_SKIP_GLOBAL_REGISTRATION='1', ORION_FLUTTER_OPEN_WINDOW='0',
               ORION_ROUTER_MDNS='0', ORION_HUB_MDNS='0',
               PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
    with (fixture / 'retry-product.log').open('wb') as log:
        subprocess.run([str(installer / 'runtime/python/python.exe'), '-B',
            str(fixture / 'fixture_update.py'), str(fixture / 'update-fixture.json'),
            str(fixture / 'retry-update-result.json')], cwd=installer, env=env,
            stdout=log, stderr=log, check=True, timeout=1200)
    result = json.loads((fixture / 'retry-update-result.json').read_text(encoding='utf-8'))
    assert result['phase'] == 'completed'
    current = registry.read()
    assert current['preferences'] == previous['preferences']
    assert {c['kind']: (c['component_id'], c['data_path']) for c in current['components']} == {
        c['kind']: (c['component_id'], c['data_path']) for c in previous['components']}
    assert fingerprint(Path(hub_old['code_path'])) == original
    assert sha256(model) == model_hash
    hub = next(c for c in current['components'] if c['kind'] == 'hub')
    protected = {name: digest for name, digest in result['backups']['hub']['files'].items()
                 if Path(name).name in {'hub-key.pem', 'hub-cert.pem', 'hub-id', 'router-access-seed'}
                 or name.endswith('.manual')}
    assert protected and all(sha256(Path(hub['data_path']) / name) == digest for name, digest in protected.items())
    check_restart(fixture, registry, current, env, 'retry-restart.log')
    summary = {'success': True, 'clean_vm': False, 'power_cut': False,
               'phase': result['phase'], 'health': result['health'],
               'checks': ['real health timeout safely rolled back', 'new recovery bootstrap alongside original',
                          'same signed target re-proved without patching old/orphan runtime',
                          'real API/worker/DB/web/Router identity health', 'old package/model/security hashes preserved',
                          'UUID/data paths/preferences preserved', 'DB row and encrypted manual key survive restart',
                          'all fixture services and databases verified stopped']}
    (fixture / 'stage06-result.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    return fixture


def accept(old_artifacts, update_artifacts, base, inputs):
    def update(scratch, signers, root, installer, env):
        registry = scratch / 'install.json'
        previous = Registry(registry).read()
        hub_old = next(c for c in previous['components'] if c['kind'] == 'hub')
        original = fingerprint(Path(hub_old['code_path']))
        repository = publish(update_artifacts, scratch / 'update-repository', root, signers, version=2)
        names = package_names(update_artifacts)
        config = scratch / 'update-fixture.json'
        config.write_text(json.dumps({'repository': str(repository), 'names': names, 'registry': str(registry)}))
        script = scratch / 'fixture_update.py'
        script.write_text('''import json,sys,os
from pathlib import Path
from tuf.ngclient.fetcher import FetcherInterface
from tuf.api.exceptions import DownloadHTTPError
from services.shared.release_packages import ReleaseClient
from services.shared.update_helper import UpdateHelper
from services.shared.installation_registry import Registry
''' + inspect.getsource(FixtureFetcher) + '''
c=json.loads(Path(sys.argv[1]).read_text())
registry=Registry(c['registry'])
client=ReleaseClient(trusted_root=registry.path.parent/'bootstrap/trusted-root.json',
    cache=registry.path.parent/'update-cache',metadata_url='https://fixture.invalid/metadata/',
    targets_url='https://fixture.invalid/targets/',fetcher=FixtureFetcher(Path(c['repository'])))
helper=UpdateHelper(registry)
assert helper.prepare(client,c['names'])['phase']=='staged'
result=helper.resume()
Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
assert result['phase']=='completed', result.get('reason')
''', encoding='utf-8')
        with (scratch / 'update-product.log').open('wb') as log:
            subprocess.run([str(installer / 'runtime/python/python.exe'), '-B', str(script), str(config),
                            str(scratch / 'update-result.json')], cwd=installer, env=env,
                           stdout=log, stderr=log, check=True, timeout=900)
        result = json.loads((scratch / 'update-result.json').read_text(encoding='utf-8'))
        assert result['phase'] == 'completed'
        current = Registry(registry).read()
        assert current['preferences'] == previous['preferences']
        assert {c['kind']: (c['component_id'], c['data_path']) for c in current['components']} == {
            c['kind']: (c['component_id'], c['data_path']) for c in previous['components']}
        assert fingerprint(Path(hub_old['code_path'])) == original
        hub = next(c for c in current['components'] if c['kind'] == 'hub')
        python = str(Path(hub['code_path']) / 'runtime/python/python.exe')
        # Verify actual PostgreSQL row and encrypted manual preference after the
        # update, using the newly selected package's public launcher.
        with (scratch / 'update-restart.log').open('wb') as log:
            def call(args):
                subprocess.run([python, '-B', *args], cwd=hub['code_path'], env=env,
                               stdout=log, stderr=log, check=True, timeout=180)
            try:
                call(['-m', 'services.shared.launcher', '--registry', str(registry), '--background'])
                call(['-c', "from services.shared.native_services import hub_database; from services.shared.environment import get_env; db=hub_database('.',get_env); assert db.ready(); assert db.sql('SELECT value FROM package_probe') == 'Türkçe 中文'; from orion.kernel.router_access import manual_state; assert manual_state('00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002')['key'] == 'sk-orion-stage05-fixture'"])
            finally:
                call(['-m', 'services.shared.launcher', '--registry', str(registry), 'stop'])
        summary = {'success': True, 'clean_vm': False, 'power_cut': False,
                   'phase': result['phase'], 'health': result['health'],
                   'checks': ['real signed immutable package switch', 'SDK-free separate bootstrap helper',
                              'old package unchanged', 'component UUID/data path/preferences preserved',
                              'real DB row and encrypted manual key survive update/restart']}
        (scratch / 'stage06-result.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
    return run(old_artifacts, base, inputs, after_acceptance=update)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-artifacts', type=Path)
    parser.add_argument('--update-artifacts', type=Path)
    parser.add_argument('--scratch-base', type=Path)
    parser.add_argument('--retry-fixture', type=Path)
    parser.add_argument('--audit-fixture', type=Path)
    parser.add_argument('--inputs', type=Path)
    args = parser.parse_args()
    if args.audit_fixture:
        audit_fixture(args.audit_fixture)
    elif args.retry_fixture:
        if not args.inputs:
            parser.error('Retry requires --inputs')
        retry_fixture(args.retry_fixture, args.inputs.resolve())
    else:
        if not args.previous_artifacts or not args.update_artifacts or not args.scratch_base or not args.inputs:
            parser.error('Fresh acceptance requires previous/update artifacts and private scratch base')
        accept(args.previous_artifacts.resolve(), args.update_artifacts.resolve(), args.scratch_base, args.inputs.resolve())
