"""A light Hub (ORION_HUB_MODE=relay) updates like any installation, without a database, worker or Router."""
import pytest

from services.shared import native_services
from services.shared.update_helper import expected_health


def install(tmp_path, mode=None):
    settings = tmp_path / 'services'
    settings.mkdir(parents=True, exist_ok=True)
    (settings / '.env.global.local').write_text(f'ORION_HUB_MODE={mode}\n' if mode else 'JWT_SECRET_KEY=x\n',
                                                encoding='utf-8')
    return tmp_path


@pytest.fixture(autouse=True)
def no_process_mode(monkeypatch):
    # The installation's file decides, never the environment of the process that updates it.
    monkeypatch.delenv('ORION_HUB_MODE', raising=False)


def test_the_installation_setting_names_the_mode(tmp_path):
    assert native_services.hub_mode(install(tmp_path / 'light', 'relay')) == 'relay'
    assert native_services.hub_mode(install(tmp_path / 'full')) == 'full'
    assert native_services.hub_mode(tmp_path / 'nothing') == 'full'


def test_a_light_hub_must_show_only_its_relay_and_window(tmp_path):
    light = {'components': [{'kind': 'hub', 'code_path': str(install(tmp_path / 'light', 'relay'))},
                            {'kind': 'client', 'code_path': str(tmp_path / 'client')}]}
    full = {'components': [{'kind': 'hub', 'code_path': str(install(tmp_path / 'full'))}]}
    router = {'components': [{'kind': 'router', 'code_path': str(tmp_path / 'router')}]}
    assert expected_health(light) == {'relay_api', 'client_api'}
    assert expected_health(full) == {'hub_api', 'worker', 'database', 'client_api', 'router_identity'}
    assert expected_health(router) == {'database', 'router_identity'}


def test_stopping_a_light_hub_never_touches_a_database(tmp_path, monkeypatch):
    root = install(tmp_path, 'relay')
    stopped, databases = [], []
    monkeypatch.setattr('services.shared.process_lifecycle.stop_component', lambda root, kind: stopped.append(kind))
    monkeypatch.setattr(native_services, 'dispatch_if_needed', lambda *args: False)
    monkeypatch.setattr(native_services, 'hub_database', lambda *args: databases.append(args))
    native_services.stop_hub(root)
    assert stopped == ['hub'] and databases == []


def test_a_rollback_of_a_light_hub_needs_no_database_check(tmp_path):
    from services.shared.update_helper import NativeHost
    record = {'components': [{'kind': 'hub', 'code_path': str(install(tmp_path, 'relay'))}]}
    assert NativeHost.recovery_health(object(), record) is True
