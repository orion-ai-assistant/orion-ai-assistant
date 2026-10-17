"""Fast, signed disposable fixtures. Native health/links are injected; no services."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import uuid
import zipfile

import pytest

from test_release_packages import repo, publisher, release
from test_update_helper import Host, Crash
from services.shared.installation_registry import Registry, new_registry, component_record
from services.shared.release_packages import sha256, record_prepared_package
from services.shared.update_helper import UpdateHelper, UpdateError
from services.shared.automatic_updates import AutomaticUpdates
from services.shared.legacy_conversion import preflight, convert, revision
from services.shared import package_maintenance as maintenance
from services.shared.release_acceptance import verify_acceptance


class RouterHost(Host):
    def health(self, record):
        return {k: self.healthy for k in ('database', 'router_identity')}


def artifact(artifacts, manifest):
    path = artifacts / (manifest['package_id'] + '.zip')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('orion-release.json', json.dumps(manifest))
        archive.writestr(manifest['entrypoint'], 'fixture code, never executed')
        if manifest['component'] == 'router':
            archive.writestr('pyproject.toml', '[project]\nname="orion-router"')
    (artifacts / (manifest['component'] + '.target.json')).write_text(json.dumps({
        'path': path.name, 'custom': manifest, 'length': path.stat().st_size,
        'hashes': {'sha256': sha256(path)}}))
    return path.name


@pytest.fixture
def router_installed(repo, monkeypatch):
    base, output, _, signers, bootstrap, artifacts, fetcher, factory = repo
    (artifacts / 'hub.target.json').unlink()
    old = release('router')
    name = artifact(artifacts, old)
    fetcher.directory = publisher.publish(artifacts, base / 'router-v1', bootstrap, signers, version=2)
    client = factory('router-install-cache')
    _, targets = client.check([name])
    target = targets[name]
    tree = client.prepare(target, base / 'Router/releases' / old['package_id'])
    data, state = base / 'Router/data', base / 'Router/state'
    data.mkdir(); state.mkdir()
    (data / 'persistent').mkdir()
    identity = str(uuid.uuid4())
    (data / 'persistent/mdns-id').write_text(identity)
    (data / 'persistent/tls.key').write_bytes(b'fixture TLS sentinel')
    (data / '.env').write_text('POSTGRES_PORT=59123\n')
    item = component_record('router', tree, distribution='packaged', data_path=data, state_path=state,
                            ownership='owned', management='local', router_uuid=identity,
                            package=record_prepared_package(tree, state, target))
    registry = Registry(base / 'install.json')
    registry.write(new_registry(base, [item], preferences={'automatic_updates': False}))
    monkeypatch.setenv('ORION_INSTALLATION_REGISTRY', str(registry.path))
    bootstrap_dir = base / 'bootstrap'
    bootstrap_dir.mkdir()
    new = deepcopy(old)
    new.update(sequence=old['sequence'] + 1, version='9.0.1', package_id='orion-router-9.0.1-windows-x86_64-cpu')
    name = artifact(artifacts, new)
    fetcher.directory = publisher.publish(artifacts, base / 'router-v2', bootstrap, signers, version=3)
    (bootstrap_dir / 'release-source.json').write_text(json.dumps({'schema': 1, 'channel': 'stable',
        'metadata_url': 'https://release.example/metadata/', 'targets_url': 'https://release.example/targets/',
        'targets': {'router': name}}))
    host = RouterHost()
    helper = UpdateHelper(registry, host=host)
    return helper, factory, name, data, host


def test_standalone_router_signed_update_and_independent_preference(router_installed):
    helper, factory, name, data, host = router_installed
    old = helper.registry.read()
    hub = AutomaticUpdates(helper.registry, helper=helper, client_factory=lambda: factory('update-cache'))
    router = AutomaticUpdates(helper.registry, helper=helper, client_factory=lambda: factory('update-cache'), scope='router')
    hub.preferences(enabled=True)
    hub.check()
    assert helper.status() is None
    assert router.policy()['enabled'] is False
    router.check(manual=True)
    assert helper.status()['phase'] == 'staged'
    assert helper.resume(automatic=True)['phase'] == 'staged'  # Hub opt-in is not Router consent.
    router.preferences(enabled=True)
    host.idle = False
    assert helper.resume(automatic=True)['phase'] == 'waiting_for_idle'
    assert host.stopped_calls == 0
    host.idle = True
    assert helper.resume(automatic=True)['phase'] == 'completed'
    assert helper.registry.read()['components'][0]['component_id'] == old['components'][0]['component_id']
    assert helper.registry.read()['components'][0]['router_uuid'] == old['components'][0]['router_uuid']
    assert (data / 'persistent/tls.key').read_bytes() == b'fixture TLS sentinel'


def test_router_failed_health_rolls_back_without_data_restore(router_installed):
    helper, factory, name, data, host = router_installed
    before = helper.registry.read()
    helper.prepare(factory('update-cache'), [name])
    host.healthy = False
    assert helper.resume()['phase'] == 'rolled_back'
    assert helper.registry.read() == before
    assert (data / 'persistent/tls.key').exists()


@pytest.fixture
def legacy(repo, monkeypatch):
    base, output, name, signers, bootstrap, artifacts, fetcher, factory = repo
    source = base / 'legacy-hub'
    (source / 'services/hub').mkdir(parents=True)
    (source / 'services/hub/pyproject.toml').write_text('[project]\nname="orion-hub"')
    (source / '.local_db/postgres/data').mkdir(parents=True)
    (source / '.local_db/postgres/data/PG_VERSION').write_text('16')
    (source / 'files/security').mkdir(parents=True)
    (source / 'files/security/identity.manual').write_bytes(b'encrypted fixture preference')
    (source / 'Models').mkdir()
    (source / 'Models/model.gguf').write_bytes(b'fixture model')
    (source / '.orion-lifecycle').mkdir()
    registry = Registry(base / 'install.json')
    registry.write(new_registry(base, [component_record('hub', source, ownership='owned', management='local')],
                                preferences={'automatic_updates': False}))
    monkeypatch.setenv('ORION_INSTALLATION_REGISTRY', str(registry.path))
    helper = UpdateHelper(registry, host=Host())
    destination = base / 'converted'
    return registry, helper, factory, name, source, destination


def test_explicit_conversion_preserves_original_and_data_identity(legacy):
    registry, helper, factory, name, source, dest = legacy
    before = registry.read()
    plan, files = preflight(registry, dest)
    result = convert(registry, factory('conversion-cache'), [name], dest,
                     expected_revision=plan['revision'], helper=helper)
    assert result['phase'] == 'completed', result['reason']
    current = registry.read()
    assert current['components'][0]['component_id'] == before['components'][0]['component_id']
    assert current['preferences'] == before['preferences']
    data = Path(current['components'][0]['data_path'])
    assert (data / 'files/security/identity.manual').read_bytes() == (source / 'files/security/identity.manual').read_bytes()
    assert (data / 'Models/model.gguf').read_bytes() == b'fixture model'
    assert (source / '.orion-converted.json').exists()


def test_conversion_failure_restores_registration_and_source_guard(legacy):
    registry, helper, factory, name, source, dest = legacy
    before = registry.read()
    helper.host.healthy = False
    result = convert(registry, factory('conversion-cache'), [name], dest,
                     expected_revision=revision(before), helper=helper)
    assert result['phase'] == 'rolled_back', result['reason']
    assert registry.read() == before
    assert not (source / '.orion-converted.json').exists()
    assert (source / 'files/security/identity.manual').exists()


def test_conversion_copy_interruption_requires_explicit_rollback(legacy):
    registry, helper, factory, name, source, dest = legacy
    before = registry.read()
    def crash(label):
        if label == 'after:journal:backing_up':
            raise Crash(label)
    helper.fault = crash
    with pytest.raises(Crash):
        convert(registry, factory('conversion-cache'), [name], dest, expected_revision=revision(before), helper=helper)
    helper.fault = lambda _: None
    assert helper.resume()['phase'] == 'repair_required'
    assert helper.rollback()['phase'] == 'rolled_back'
    assert registry.read() == before


@pytest.mark.parametrize('change', ['development', 'schema', 'stale', 'busy'])
def test_conversion_refuses_unsupported_or_unreviewed_changes(legacy, change):
    registry, helper, factory, name, source, dest = legacy
    expected = revision(registry.read())
    if change == 'development':
        record = registry.read(); record['components'][0]['distribution'] = 'development'; registry.write(record)
        expected = revision(record)
    if change == 'schema':
        (source / '.local_db/postgres/data/PG_VERSION').write_text('15')
    if change == 'stale':
        expected = 'stale'
    if change == 'busy':
        helper.host.idle = False
    with pytest.raises(ValueError):
        convert(registry, factory('conversion-cache'), [name], dest, expected_revision=expected, helper=helper)
    assert not dest.exists()
    assert (source / 'Models/model.gguf').exists()


def test_uninstall_and_explicit_code_cleanup_preserve_data_models_bootstrap(router_installed, monkeypatch):
    helper, factory, name, data, host = router_installed
    registry = helper.registry
    old = registry.read()
    tree = Path(old['components'][0]['code_path'])
    links = Mock()
    result = maintenance.uninstall(registry, ['router'], expected_revision=revision(old), helper=helper, links=links)
    assert result['data_preserved'] and tree.exists()
    assert registry.read()['components'] == []
    assert registry.read()['preferences']['start_with_windows'] is False
    plan = maintenance.cleanup_plan(registry)
    assert [c['package']['id'] for c in plan['components']] == [old['components'][0]['package']['id']]
    monkeypatch.setattr(maintenance, 'StoppedPackages', host.stopped)
    maintenance.purge_old_code(registry, old['components'][0]['package']['id'], expected_revision=plan['revision'])
    assert not tree.exists()
    assert (data / 'persistent/tls.key').exists()
    assert (registry.path.parent / 'bootstrap/release-source.json').exists()
    links.assert_called_once()


def test_active_or_recovery_release_is_never_cleaned(router_installed):
    helper, factory, name, data, host = router_installed
    registry = helper.registry
    before = registry.read()
    with pytest.raises(UpdateError):
        maintenance.purge_old_code(registry, before['components'][0]['package']['id'], expected_revision=revision(before))
    helper.prepare(factory('update-cache'), [name])
    with pytest.raises(UpdateError):
        maintenance.cleanup_plan(registry)
    assert (data / 'persistent/tls.key').exists()


def test_router_bridge_queues_only_its_registered_installation(router_installed):
    helper, factory, name, data, host = router_installed
    path = Path(__file__).resolve().parents[3] / 'orion-router/core/updater.py'
    # Load Router's bridge without initializing its real development config.
    import sys
    with patch.dict(sys.modules, {'core.config': SimpleNamespace(APP_VERSION='fixture')}):
        sys.path.insert(0, str(path.parents[1]))
        try:
            spec = importlib.util.spec_from_file_location('core.updater_stage08_test', path)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            module.ROOT = Path(helper.registry.read()['components'][0]['code_path'])
            (helper.registry.path.parent / 'bootstrap/automatic-runtime.json').write_text('{"schema":1,"root":"fixture"}')
            assert module.start_update()['status'] == 'started'
            assert (helper.base / 'router-request.json').exists()
            assert module.get_update_status()['success'] is False
            module.ROOT = data
            assert module.start_update()['status'] == 'error'
        finally:
            sys.path.pop(0)


def test_stable_publication_rejects_missing_or_unbound_acceptance(repo):
    base, output, name, signers, bootstrap, artifacts, fetcher, factory = repo
    report = base / 'acceptance.json'
    report.write_text(json.dumps({'schema': 1, 'channel': 'stable', 'test_only': False, 'packages': {}, 'checks': {}}))
    with pytest.raises(ValueError, match='exact package set'):
        verify_acceptance(artifacts, report, 'stable')
    target = json.loads((artifacts / 'hub.target.json').read_text())
    report.write_text(json.dumps({'schema': 1, 'channel': 'stable', 'test_only': False,
                                 'packages': {target['custom']['package_id']: target['hashes']['sha256']}, 'checks': {}}))
    with pytest.raises(ValueError, match='Missing release acceptance'):
        verify_acceptance(artifacts, report, 'stable')
