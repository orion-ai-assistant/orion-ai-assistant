"""Which ports Orion needs free and who holds them: an update switch, a start, a new installation (install_ports.py)."""
import socket

from services.shared import install_ports, launcher


def record(tmp_path, local='', mode=''):
    code, data, router = tmp_path / 'code', tmp_path / 'data', tmp_path / 'router'
    (code / 'services').mkdir(parents=True)
    (data / 'services').mkdir(parents=True)
    router.mkdir()
    (code / 'services/.env.global').write_text('POSTGRES_PORT=5445\nREDIS_PORT=6379\nHUB_PORT=8909\n', encoding='utf-8')
    (data / 'services/.env.global.local').write_text(local + (f'ORION_HUB_MODE={mode}\n' if mode else ''), encoding='utf-8')
    (router / '.env').write_text('ORION_ROUTER_TLS_PORT=9555\n', encoding='utf-8')
    return {'components': [{'kind': 'hub', 'code_path': str(code), 'data_path': str(data)},
                           {'kind': 'client', 'code_path': 'x', 'data_path': 'y'},
                           {'kind': 'router', 'code_path': 'r', 'data_path': str(router)}]}


def test_a_full_hub_needs_its_database_redis_and_listeners(tmp_path):
    assert install_ports.ports(record(tmp_path, 'POSTGRES_PORT=5500\n')) == [5433, 5500, 6379, 8090, 8443, 8909, 8910,
                                                                             9555, 20128]


def test_a_light_hub_needs_only_the_window_ports(tmp_path):
    assert install_ports.ports(record(tmp_path, mode='relay')) == [5433, 8090, 8910, 9555, 20128]


def test_a_held_port_is_reported(tmp_path, monkeypatch):
    with socket.socket() as other:
        other.bind(('127.0.0.1', 0))
        other.listen()
        port = other.getsockname()[1]
        monkeypatch.setattr(install_ports, 'ports', lambda rec: [port])
        assert install_ports.in_use({}) == [port]
    assert install_ports.in_use({}) == []


def test_a_new_installation_checks_the_ports_of_its_type():
    assert install_ports.defaults('baglan') == [8090, 8910]
    assert install_ports.defaults('merkez') == [5433, 5445, 6379, 8090, 8443, 8909, 8910, 9443, 20128]


def test_the_holder_of_a_port_is_named():
    with socket.socket() as other:
        other.bind(('127.0.0.1', 0))
        other.listen()
        port = other.getsockname()[1]
        groups = install_ports.holders([port])
    assert len(groups) == 1 and groups[0]['ports'] == [port]
    assert groups[0]['name'] and groups[0]['name'].lower().startswith('python')


START_CHECK = launcher.ports_taken  # conftest replaces it for every other test (this PC's real ports)


def test_a_start_names_the_program_holding_its_ports(monkeypatch):
    monkeypatch.setattr(launcher, 'running_services', lambda components: [])
    monkeypatch.setattr(install_ports, 'in_use', lambda record: [5445, 8910])
    monkeypatch.setattr(install_ports, 'holder', lambda port: {
        'name': 'postgres.exe' if port == 5445 else 'python.exe', 'orion': True})
    message = START_CHECK({}, [], wait=0)
    assert message.startswith('ORION-PORT: ports=5445,8910 programs=postgres.exe,python.exe orion=1')
    assert launcher.explain(launcher.RegistryError(message)) == message  # The entry script words it as a dialog.
    message.encode('ascii')  # Read through the console code page.


def test_a_start_with_its_own_services_running_or_ports_freed_in_time_says_nothing(monkeypatch):
    monkeypatch.setattr(launcher, 'running_services', lambda components: ['hub'])
    monkeypatch.setattr(install_ports, 'in_use', lambda record: [8910])
    assert START_CHECK({}, [{'kind': 'hub'}]) is None  # Orion's own services hold its ports.
    answers = iter([[8910], [8910], []])  # A just-stopped Orion lets go a moment later.
    monkeypatch.setattr(launcher, 'running_services', lambda components: [])
    monkeypatch.setattr(install_ports, 'in_use', lambda record: next(answers))
    assert START_CHECK({}, [], sleep=lambda seconds: None) is None


def test_another_orion_is_one_thing_to_close(monkeypatch):
    found = {5445: ('postgres.exe', True), 6379: ('redis-server.exe', True), 8090: ('nginx.exe', False)}
    monkeypatch.setattr(install_ports, 'holder', lambda port: {'name': found[port][0], 'orion': found[port][1]})
    groups = install_ports.holders([5445, 6379, 8090])
    assert [(g['orion'], g['ports'], g['names']) for g in groups] == [
        (True, [5445, 6379], ['postgres.exe', 'redis-server.exe']), (False, [8090], ['nginx.exe'])]
