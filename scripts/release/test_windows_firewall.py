"""Firewall rule planning/flow with a fake command runner; never touches the real firewall."""
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
    assert firewall.tcp_ports(record(tmp_path)) == [8443, 8911, 9443]
    custom = record(tmp_path / 'custom', 'ORION_MOBILE_PORT=18443\nJWT_SECRET_KEY=x\n', 'ORION_ROUTER_TLS_PORT=19443\n')
    assert firewall.tcp_ports(custom) == [8911, 18443, 19443]
    standalone = {'components': [c for c in custom['components'] if c['kind'] == 'router']}
    assert firewall.tcp_ports(standalone) == [19443]
    assert firewall.tcp_ports(record(tmp_path / 'bad', 'ORION_MOBILE_PORT=abc\nORION_PUBLIC_PORT=80\n')) == [8443, 9443]


class Runner:
    def __init__(self, existing=None, admin_code=0):
        self.rule = existing
        self.calls = []
        self.admin_code = admin_code

    def __call__(self, argv, **kwargs):
        command = argv[-1]
        self.calls.append(command)
        if 'Get-NetFirewallRule' in command:
            if self.rule is None:
                return SimpleNamespace(returncode=3, stdout='')
            return SimpleNamespace(returncode=0, stdout='\n'.join(map(str, self.rule)))
        if self.admin_code == 0 and 'add rule' in command:
            self.rule = [8443, 8911, 9443]
        if 'add rule' not in command and self.admin_code == 0:
            self.rule = None
        return SimpleNamespace(returncode=self.admin_code, stdout='')


def test_rules_added_once_then_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(firewall, 'is_admin', lambda: True)
    monkeypatch.setattr(firewall.sys, 'platform', 'win32')
    rec, runner = record(tmp_path), Runner()
    assert firewall.ensure_rules(rec, run=runner) == 'added'
    command = next(c for c in runner.calls if 'add rule' in c)
    assert 'profile=private' in command and 'remoteip=localsubnet' in command and 'localport=8443,8911,9443' in command
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
