"""Fixed health probe for the candidate Hub, worker, DB, web client and Router."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request


def probe(registry, record):
    from .launcher import launch_environment
    from .native_services import start_hub, start_router
    from .update_gate import blocked
    if not blocked(registry.path):
        raise RuntimeError('Candidate health requires closed admission')
    hub = next(c for c in record['components'] if c['kind'] == 'hub')
    root = Path(hub['code_path'])
    operation = json.loads((registry.path.parent / 'updates/active.json').read_text(encoding='utf-8'))['operation_id']
    with launch_environment(registry):
        for item in record['components']:
            if item['kind'] == 'router':
                start_router(str(Path(item['code_path']) / 'cli.py'))
        start_hub(root, update_operation=operation)
        # Always use the candidate's own runtime/configuration/schema checker.
        result = subprocess.run([str(root / 'runtime/python/python.exe'), '-B', '-m',
                                 'services.shared.update_health'], cwd=root, env=os.environ.copy(),
                                capture_output=True, timeout=180,
                                **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        if result.returncode:
            raise RuntimeError('Candidate runtime health failed; see local supervisor logs')
        return json.loads(result.stdout.decode('utf-8').splitlines()[-1])


def runtime_probe():
    from .environment import get_env
    from .installation_registry import Registry
    from .native_services import hub_database, component_status
    from .process_lifecycle import Runtime, ManagedChild as Child, wait_health, child_running, owns_listener
    from .release_packages import compatible, verify_package_record
    from .update_gate import blocked
    import importlib.util
    record = Registry().read()
    if not blocked():
        raise RuntimeError('Admission opened before candidate health')
    items = {c['kind']: c for c in record['components']}
    hub = items['hub']
    root = Path(hub['code_path'])
    manifests = [verify_package_record(c) for c in record['components']]
    compatible(manifests, mobile_api=1)
    state = Runtime(root, 'hub').read()
    if component_status(root, 'hub') != 'ready':
        raise RuntimeError('Candidate supervisor is not ready')
    from orion.kernel.local_security import tls_identity
    identity = tls_identity()
    wait_health(Child(state['children']['api']), int(get_env('HUB_PORT')),
                certificate=identity['cert'], timeout=10)
    # ready checks authenticated app DB connectivity, not just an open port.
    wait_health(Child(state['children']['api']), int(get_env('HUB_PORT')),
                certificate=identity['cert'], path='/ready', timeout=10)
    database = hub_database(root, get_env)
    if not database.ready() or database.sql("SELECT to_regclass('public.orion_settings') IS NOT NULL") != 't':
        raise RuntimeError('Expected DB/schema unavailable')
    spec = importlib.util.spec_from_file_location('candidate_run_local', root / 'services/hub/run_local.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    worker = Child(state['children']['worker'])
    if not child_running(worker.child) or not module.worker_ready(worker, get_env('REDIS_PORT')):
        raise RuntimeError('Worker ownership unavailable')
    if 'client' in items:
        client = state['children']['flutter']
        wait_health(Child(client), int(get_env('ORION_FLUTTER_PORT')), path='/__orion_flutter_health__',
                    json_response=False, timeout=10)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:' + get_env('ORION_FLUTTER_PORT') + '/__orion_flutter_health__', timeout=3) as response:
            if response.read(128) != b'orion-flutter-web':
                raise RuntimeError('Unexpected web client')
    if 'router' in items:
        router = items['router']
        from .router_installation import _inspect_registered_component
        # Inspection pins code provenance and the recorded UUID/SPKI. Native
        # Router startup already uses owned listener + certificate health.
        result = _inspect_registered_component(router)
        if result.status not in {'ready', 'closed-unverified'}:
            raise RuntimeError('Router identity/provenance unavailable')
        if component_status(router['code_path'], 'router-prod') != 'ready':
            raise RuntimeError('Registered Router is not ready')
        from .router_verification import _local_identity
        backend = Runtime(router['code_path'], 'router-prod').read()['children']['backend']
        if not owns_listener(backend['process'], router['tls_port']):
            raise RuntimeError('Router listener ownership unavailable')
        live = _local_identity(router['tls_port'], router['certificate_path'], router['tls_spki_sha256'])
        if live.get('id') != router['router_uuid'] or live.get('fp') != router['tls_spki_sha256']:
            raise RuntimeError('Router live identity mismatch')
    return dict(hub_api=True, worker=True, database=True, client_api=True, router_identity=True)


if __name__ == '__main__':
    print(json.dumps(runtime_probe()))
