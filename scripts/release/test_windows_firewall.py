"""Firewall rule planning/flow with a fake command runner; never touches the real firewall."""
import re
from types import SimpleNamespace

import pytest

from services.shared import windows_firewall as firewall


def record(tmp_path, hub_env='', router_env=''):
    hub, router = tmp_path / 'hub', tmp_path / 'router'
    (hub / 'services').mkdir(parents=True)
    router.mkdir()
    (hub / 'services/.env.global.local').write_text(hub_env)
    (router / '.env').write_text(router_env)
    return {'components': [{'kind': 'hub', 'data_path': str(hub)}, {'kind': 'router', 'data_path': str(router)}]}


def test_ports_default_and_honor_installed_overrides(tmp_path):
    assert firewall.tcp_ports(record(tmp_path)) == [8443, 9443]
    custom = record(tmp_path / 'custom', 'ORION_MOBILE_PORT=18443\nJWT_SECRET_KEY=x\n', 'ORION_ROUTER_TLS_PORT=19443\n')
    assert firewall.tcp_ports(custom) == [18443, 19443]
    standalone = {'components': [c for c in custom['components'] if c['kind'] == 'router']}
    assert firewall.tcp_ports(standalone) == [19443]
    # A browser-listener port left in an older install's settings never opens the firewall.
    assert firewall.tcp_ports(record(tmp_path / 'old', 'ORION_PUBLIC_PORT=8911\nORION_WEB_PORT=8911\n')) == [8443, 9443]
    assert firewall.tcp_ports(record(tmp_path / 'bad', 'ORION_MOBILE_PORT=abc\n')) == [8443, 9443]


class Runner:
    """Fake powershell: keeps the two rules as netsh would and answers current_rules' query."""
    def __init__(self, existing=None, admin_code=0, udp=None):
        self.rule = existing
        self.udp = existing is not None if udp is None else udp
        self.calls = []
        self.admin_code = admin_code

    def __call__(self, argv, **kwargs):
        command = argv[-1]
        self.calls.append(command)
        if 'Get-NetFirewallRule' in command:
            lines = (['udp'] if self.udp else []) + ([f'tcp {",".join(map(str, self.rule))}'] if self.rule else [])
            return SimpleNamespace(returncode=0, stdout='\n'.join(lines))
        if self.admin_code == 0 and 'add rule' in command:
            tcp = re.search(r'protocol=TCP localport=([\d,]+)', command)
            self.rule = [int(p) for p in tcp.group(1).split(',')] if tcp else None
            self.udp = 'protocol=UDP' in command
        if 'add rule' not in command and self.admin_code == 0:
            self.rule, self.udp = None, False
        return SimpleNamespace(returncode=self.admin_code, stdout='')


def test_rules_added_once_then_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(firewall, 'is_admin', lambda: True)
    monkeypatch.setattr(firewall.sys, 'platform', 'win32')
    rec, runner = record(tmp_path), Runner()
    assert firewall.ensure_rules(rec, run=runner) == 'added'
    command = next(c for c in runner.calls if 'add rule' in c)
    assert 'profile=private' in command and 'remoteip=localsubnet' in command and 'localport=8443,9443' in command
    assert 'program=' not in command  # port rule survives package updates
    assert firewall.ensure_rules(rec, run=runner) == 'unchanged'
    assert firewall.remove_rules(run=runner) == 'removed'


def test_declined_elevation_never_fails_the_install(tmp_path, monkeypatch):
    monkeypatch.setattr(firewall, 'is_admin', lambda: False)
    monkeypatch.setattr(firewall.sys, 'platform', 'win32')
    runner = Runner(admin_code=1223)
    rec = record(tmp_path)
    assert firewall.ensure_rules(rec, run=runner) == 'declined'
    assert firewall.ensure_rules(rec, run=Runner(), elevate=False) == 'declined'
    elevated = [c for c in runner.calls if 'EncodedCommand' in c]
    assert elevated and 'RunAs' in elevated[0]  # the rule script itself is never written to disk


def test_runner_errors_are_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(firewall.sys, 'platform', 'win32')

    def broken(*args, **kwargs):
        raise OSError('powershell missing')
    assert firewall.ensure_rules(record(tmp_path), run=broken) == 'failed'


def test_bootstrap_installer_script_exposes_default_on_switches():
    from pathlib import Path
    source = (Path(__file__).with_name('build_bootstrap.py')).read_text(encoding='utf-8')
    for name in ('NoStartWithWindows', 'NoAutoUpdate', 'NoFirewall'):
        assert f'[switch]${name}' in source
    assert 'StartWithWindows) { $launch += "--start-with-windows"' not in source


def test_a_light_hub_gets_the_discovery_rule_alone(tmp_path, monkeypatch):
    # It opens no TCP port, but listens for the center's announcement: without the rule Windows would ask
    # "allow Python?" the first time "Cihazları ara" runs.
    monkeypatch.setattr(firewall, 'is_admin', lambda: True)
    monkeypatch.setattr(firewall.sys, 'platform', 'win32')
    hub = tmp_path / 'hub'
    (hub / 'services').mkdir(parents=True)
    (hub / 'services/.env.global.local').write_text('ORION_HUB_MODE=relay\n')
    light = {'components': [{'kind': 'hub', 'data_path': str(hub)}, {'kind': 'client', 'data_path': str(tmp_path)}]}
    assert firewall.wanted(light) == {'tcp': None, 'udp': True}
    runner = Runner()
    assert firewall.ensure_rules(light, run=runner) == 'added'
    command = next(c for c in runner.calls if 'add rule' in c)
    assert 'protocol=TCP' not in command and 'localport=5353' in command and 'profile=private' in command
    assert firewall.ensure_rules(light, run=runner) == 'unchanged'
    assert firewall.remove_rules(run=runner) == 'removed'
    assert firewall.wanted({'components': [{'kind': 'client', 'data_path': str(tmp_path)}]}) == firewall.NO_RULES


def test_a_full_hub_that_had_only_discovery_gets_its_port_rule_too(tmp_path, monkeypatch):
    monkeypatch.setattr(firewall, 'is_admin', lambda: True)
    monkeypatch.setattr(firewall.sys, 'platform', 'win32')
    runner = Runner(udp=True)
    assert firewall.ensure_rules(record(tmp_path), run=runner) == 'added'
    assert runner.rule == [8443, 9443] and runner.udp
