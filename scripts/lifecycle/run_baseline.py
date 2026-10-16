"""Bounded, isolated lifecycle tests. Usage: python ... hub|router|fixtures|lifecycle.

Uses the invoking interpreter's installed dependencies, never installs packages.
Router is a sibling checkout. The lifecycle suite starts only synthetic services
and fresh PostgreSQL/Redis data; it never runs production lifecycle commands.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import json
import shutil
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HUB_TESTS = [
    'services/hub/tests/test_installer_browser_lifecycle.py',
    'services/hub/tests/test_flutter_launcher.py',
    'services/hub/tests/test_router_bootstrap.py',
    'services/hub/tests/test_local_registration.py',
    'services/hub/tests/test_worker_supervisor.py',
    'installer/tests',
]
EXTRA_SUITES = {
    'jobs': ('worker_resilience', 'pending_recovery', 'stream_stop'),
    'audio': ('tts_stream', 'pc_audio', 'pc_audio_access'),
    'browser': ('browser_manager', 'browser_routes', 'browser_tools',
                'browser_bridge', 'browser_cdp', 'browser_live'),
    'browser-live': ('browser_live',),
    'storage-ui': ('installation_storage_ui',),
    'browser-bridge': ('browser_bridge',),
    'browser-api': ('browser_routes', 'browser_tools', 'browser_cdp'),
}
EXTENDED_TESTS = [f'services/hub/tests/test_{name}.py'
                  for key in ('jobs', 'audio', 'browser') for name in EXTRA_SUITES[key]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=['hub', 'router', 'fixtures', 'lifecycle', 'boundaries', 'extended', *EXTRA_SUITES])
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        import pytest
        import faulthandler
        faulthandler.dump_traceback_later(45, repeat=True)
        import asyncpg
        from orion.browser import manager as browser_manager
        # Regression fixtures must never drive the person's desktop dialogs.
        from contextlib import ExitStack
        tests = (EXTENDED_TESTS if args.suite == 'extended' else HUB_TESTS if args.suite == 'hub' else ['scripts/lifecycle/test_process_lifecycle.py']
                 if args.suite == 'lifecycle' else ['dev/test-orion-router/test_local_installation.py',
                                                   'dev/test-orion-router/test_native_lifecycle.py'])
        if args.suite in EXTRA_SUITES:
            tests = [f'services/hub/tests/test_{name}.py' for name in EXTRA_SUITES[args.suite]]
        if args.suite == 'boundaries':
            tests = ['scripts/lifecycle/test_process_lifecycle.py', '-k',
                'fast_parent or protected_process or node_fork or worker_legacy or worker_readiness']
        with ExitStack() as desktop_guards, patch.object(asyncpg, 'connect', side_effect=AssertionError('Live DB forbidden')) as connect, \
                patch.object(asyncpg, 'create_pool', side_effect=AssertionError('Live DB pool forbidden')) as pool:
            desktop_guards.enter_context(patch.object(browser_manager, 'auto_approve_browser_dialogs', return_value=None))
            desktop_guards.enter_context(patch.object(browser_manager, 'enable_remote_debugging',
                side_effect=AssertionError('Desktop mutation forbidden in baseline tests')))
            result = pytest.main(['-q', '--tb=short', '-p', 'no:cacheprovider',
                                 '--junitxml=' + str(Path(os.environ['TEMP']) / 'tests.xml'),
                                 *(['-x'] if args.suite == 'extended' else []), *tests])
            print(f'DB connection attempts: {connect.call_count}; pool attempts: {pool.call_count}', flush=True)
        return result or int(bool(connect.call_count or pool.call_count))
    scratch = ROOT / '.tmp/lifecycle'
    scratch.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='baseline-', dir=scratch)
    result = None
    results = scratch / 'results'
    results.mkdir(exist_ok=True)
    result_path = results / (Path(tmp).name + '-' + args.suite + '.json')
    try:
        env = os.environ.copy()
        for key in ('LOCALAPPDATA', 'APPDATA', 'XDG_CONFIG_HOME', 'ORION_SECURITY_DIR'):
            env[key] = str(Path(tmp) / key)
            Path(env[key]).mkdir()
        env.update(TEMP=tmp, TMP=tmp, ORION_ROUTER_CONNECTION_FILE=str(Path(tmp) / 'selected.json'),
                   ORION_MDNS_ID_FILE=str(Path(tmp) / 'mdns-id'), PYTHONDONTWRITEBYTECODE='1',
                   PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONUNBUFFERED='1',
                   PYTHONPATH=os.pathsep.join([str(ROOT / 'services/hub/src'), str(ROOT)]))
        if args.suite in {'extended', 'browser', 'browser-live', 'storage-ui'}:
            env.update(ORION_BROWSER_TESTS='1', ORION_BROWSER_TEST_HEADLESS='1')
        if args.suite == 'fixtures':
            command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts/lifecycle', '-p', 'test_test_environment.py', '-v']
        else:
            command = [sys.executable, str(Path(__file__).resolve()), args.suite, '--worker']
        cwd = ROOT.parent / 'orion-router' if args.suite == 'router' else ROOT
        try:
            from services.shared.process_lifecycle import Runtime
            with Runtime(tmp, 'test-runner') as runtime:
                child = runtime.launch('pytest', command, python=True, cwd=cwd, env=env)
                deadline = time.monotonic() + 240
                while child.popen.poll() is None:
                    runtime.refresh_children()
                    if time.monotonic() >= deadline:
                        result = 124
                        child.stop(60)
                        raise subprocess.TimeoutExpired(command, 240)
                    time.sleep(.1)
                result = child.popen.returncode
            xml = Path(tmp) / 'tests.xml'
            if xml.is_file():
                shutil.copy2(xml, result_path.with_suffix('.xml'))
            if result == 0:
                shutil.rmtree(tmp)
            else:
                print('Failed test scratch retained:', tmp, flush=True)
            return result
        except subprocess.TimeoutExpired:
            print('TIMEOUT after 240 seconds; test worker terminated, no success claimed.', flush=True)
            return 124
    finally:
        result_path.write_text(json.dumps(dict(suite=args.suite, python=sys.executable,
            exit_code=result, scratch=tmp, time=time.time(),
            error=str(sys.exc_info()[1]) if sys.exc_info()[1] else None), indent=2))
        print('Result record:', result_path, flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
