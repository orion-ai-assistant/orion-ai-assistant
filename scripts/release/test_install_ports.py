"""Which ports an update must find free before it switches (services/shared/install_ports.py)."""
import socket

from services.shared import install_ports


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
    assert install_ports.ports(record(tmp_path, 'POSTGRES_PORT=5500\n')) == [5500, 6379, 8090, 8443, 8909, 8910, 9555]


def test_a_light_hub_needs_only_the_window_ports(tmp_path):
    assert install_ports.ports(record(tmp_path, mode='relay')) == [8090, 8910, 9555]


def test_a_held_port_is_reported(tmp_path, monkeypatch):
    with socket.socket() as other:
        other.bind(('127.0.0.1', 0))
        other.listen()
        port = other.getsockname()[1]
        monkeypatch.setattr(install_ports, 'ports', lambda rec: [port])
        assert install_ports.in_use({}) == [port]
    assert install_ports.in_use({}) == []
