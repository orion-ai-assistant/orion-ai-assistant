"""Production publishing gate. Test signatures are never release acceptance."""
import json
from pathlib import Path

from .release_packages import sha256


def verify_acceptance(artifacts, report_path, channel):
    if channel not in {'beta', 'stable'}:
        raise ValueError('Choose beta or stable rollout explicitly')
    report_path = Path(report_path)
    value = json.loads(report_path.read_text(encoding='utf-8'))
    if value.get('schema') != 1 or value.get('channel') != channel or value.get('test_only') is not False:
        raise ValueError('Production acceptance report required')
    packages = {}
    for path in Path(artifacts).glob('*.target.json'):
        target = json.loads(path.read_text(encoding='utf-8'))
        archive = Path(artifacts) / target['path']
        digest = sha256(archive)
        if digest != target['hashes']['sha256']:
            raise ValueError('Artifact changed after acceptance')
        packages[target['custom']['package_id']] = digest
    if not packages or value.get('packages') != packages:
        raise ValueError('Acceptance evidence does not bind this exact package set')
    required = {'clean_windows_vm', 'upgrade_from_previous', 'failed_release_recovery', 'uninstall_preserves_data'}
    if channel == 'stable':
        required |= {'windows_logon', 'abrupt_shutdown', 'active_use_and_remote_clients'}
    checks = value.get('checks', {})
    for name in required:
        check = checks.get(name, {})
        if check.get('passed') is not True:
            raise ValueError('Missing release acceptance: ' + name)
        path = report_path.parent / check['report']
        if sha256(path) != check.get('sha256'):
            raise ValueError('Acceptance report changed: ' + name)
    return value
