"""The installation types and what each installs, opens and writes."""
import pytest

from services.shared import install_plan
from services.shared.windows_firewall import tcp_ports, wanted


def test_the_center_gets_everything_and_a_light_install_only_the_window():
    assert sorted({row.kind for row in install_plan.components('merkez')}) == ['client', 'hub', 'router']
    assert sorted({row.kind for row in install_plan.components('baglan')}) == ['client', 'hub']
    assert install_plan.settings('merkez') == {}
    assert install_plan.settings('baglan') == {'hub': {'ORION_HUB_MODE': 'relay'}}
    # Both ask Windows once at install; a light Hub only for finding the center (no TCP port, below).
    assert install_plan.firewall('merkez') and install_plan.firewall('baglan')
    with pytest.raises(ValueError):
        install_plan.components('router')


def test_the_summary_says_what_is_already_there(tmp_path):
    record = {'components': [{'kind': 'router', 'code_path': str(tmp_path / 'router')}]}
    rows = {row['kind']: row['action'] for row in install_plan.plan('merkez', record)}
    assert rows == {'hub': 'install', 'client': 'install', 'router': 'present'}
    full_hub = {'components': [{'kind': 'hub', 'code_path': 'hub'}, {'kind': 'client', 'code_path': 'client'}]}
    # A full Hub cannot silently become a light one (it keeps accounts and chats).
    rows = {row['kind']: row['action'] for row in install_plan.plan('baglan', full_hub, hub_mode=lambda root: 'full')}
    assert rows == {'hub': 'conflict', 'client': 'present'}


def test_a_light_hub_opens_no_firewall_port(tmp_path):
    def record(mode):
        data = tmp_path / mode / 'services'
        data.mkdir(parents=True)
        (data / '.env.global.local').write_text(f'ORION_HUB_MODE={mode}\n', encoding='utf-8')
        return {'components': [{'kind': 'hub', 'data_path': str(tmp_path / mode)}]}
    light = record('relay')
    assert tcp_ports(light) == [] and wanted(light) == {'tcp': None, 'udp': True}
    assert 8443 in tcp_ports(record('full'))
