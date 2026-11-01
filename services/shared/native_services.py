"""The manager and installer use the same native entry points and results."""
import os
from pathlib import Path
import sys
import subprocess
import shutil
from functools import wraps


def packaged_hub_operation(action):
    """Packaged manager/installer entrypoints share the update operation lock."""
    def decorate(function):
        @wraps(function)
        def run(root, *, update_operation=None):
            if (Path(root) / '.orion-converted.json').exists():
                raise LifecycleError('Legacy installation converted; use the registered launcher')
            from .runtime_layout import component
            if not component(root):
                return function(root)
            from .installation_registry import Registry, operation_lock
            from .update_gate import blocked
            registry = Registry()
            if update_operation is not None:
                # The fixed local helper already owns this lock. Avoid a second
                # flock on non-Windows hosts; match its durable operation first.
                import json
                active = json.loads((registry.path.parent / 'updates/active.json').read_text(encoding='utf-8'))
                if active['operation_id'] != update_operation or not blocked(registry.path):
                    raise LifecycleError('Helper operation does not match the admission barrier')
                return function(root)
            with operation_lock(registry.path, timeout=180):
                if action == 'start' and blocked(registry.path):
                    raise LifecycleError('Update recovery in progress; resume the local helper')
                return function(root)
        return run
    return decorate

LifecycleError = RuntimeError


def dispatch_if_needed(action, root, argument=None):
    import importlib.util
    if importlib.util.find_spec('psutil') is not None:
        return False
    root = Path(root).resolve()
    runtime = python_at(root / 'services/hub') if action.endswith('hub') else router_python(router_root(argument))
    if Path(runtime).resolve() == Path(sys.executable).resolve():
        raise LifecycleError('psutil missing; run the existing native dependency setup first')
    # Preserve the public base-Python manager CLI without installing dependencies.
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    subprocess.run([runtime, '-m', 'services.shared.native_services', action, str(root), argument or ''],
                   env=env, check=True)
    return True


def python_at(root):
    from .runtime_layout import python_path, component
    repo = Path(root).resolve().parents[1]
    if component(repo):
        path = python_path(repo)
        if not path.is_file():
            raise LifecycleError('Packaged Python runtime missing; repair required')
        return str(path)
    path = Path(root) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not path.is_file():
        raise LifecycleError('Service environment missing; run setup first')
    return str(path)


def hub_database(root, get_env):
    from services.shared.native_postgres import Postgres
    from .runtime_layout import data_root
    # The installation's own password wins over a default "orion" any process may still carry.
    saved = _saved_database_passwords(root).get('POSTGRES_PASSWORD')
    return Postgres(Path(root) / '.local_db/postgres/bin' / ('pg_ctl.exe' if os.name == 'nt' else 'pg_ctl'),
                    data_root(root) / '.local_db/postgres/data', get_env('POSTGRES_PORT'),
                    get_env('POSTGRES_USER'), get_env('POSTGRES_DB'), saved or get_env('POSTGRES_PASSWORD'))


def _saved_database_passwords(root):
    from dotenv import dotenv_values
    from .runtime_layout import data_root
    path = data_root(root) / 'services/.env.global.local'
    saved = dotenv_values(path) if path.is_file() else {}
    return {key: value for key, value in saved.items() if key.startswith('POSTGRES_') and value and value != 'orion'}


def hub_database_passwords(root):
    """This installation's PostgreSQL passwords (postgres_auth.py), kept with its other machine-specific values
    in <data>/services/.env.global.local. The shipped default "orion" is replaced by a random one once.

    Read from that file directly: in a source checkout .env.global (with the default) is loaded first and wins."""
    from .postgres_auth import new_password
    from .runtime_layout import data_root
    path = data_root(root) / 'services/.env.global.local'
    saved = _saved_database_passwords(root)
    values, created = {}, {}
    for key in ('POSTGRES_PASSWORD', 'POSTGRES_ADMIN_PASSWORD'):
        # A deliberately configured password (file, then process) stays.
        chosen = next((value for value in (saved.get(key), os.environ.get(key)) if value and value != 'orion'), None)
        values[key] = chosen or created.setdefault(key, new_password())
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as configuration:
            configuration.write('\n# PostgreSQL passwords of this installation (random, services/shared/postgres_auth.py)\n'
                                + ''.join(f'{key}={value}\n' for key, value in created.items()))
    return values


def component_status(root, component):
    try:
        from services.shared.process_lifecycle import Runtime, living, child_running, child_tree_alive, LifecycleError
    except ModuleNotFoundError as error:
        if error.name == 'psutil':
            return 'ownership-unavailable'
        raise
    try:
        record = Runtime(root, component).read()
        if record is None:
            return 'unverified'
        supervisor_alive = living(record['supervisor'])
        children_alive = [child_running(child) for child in record['children'].values()]
        if record['status'] == 'ready' and supervisor_alive and children_alive and all(children_alive):
            return 'ready'
        if record['status'] == 'stopped' and not any(child_tree_alive(child) for child in record['children'].values()):
            return 'stopped'
        if record['status'] == 'ready':
            return 'starting' if supervisor_alive else 'recovery-required'
        return record['status'] if supervisor_alive else 'recovery-required'
    except LifecycleError:
        return 'ownership-error'


def hub_mode(root):
    """'relay' for a PC that only uses another PC's Hub (light Hub, orion.api.relay_server), else 'full'.

    The installation's own setting decides, not this process's environment: the updater checks other trees."""
    value = None
    try:
        from dotenv import dotenv_values
        from services.shared.runtime_layout import data_root
        settings = data_root(Path(root)) / 'services/.env.global.local'
        if settings.is_file():
            value = dotenv_values(settings).get('ORION_HUB_MODE')
    except (ImportError, OSError, RuntimeError):
        pass
    if value is None:
        value = os.getenv('ORION_HUB_MODE')
    return 'relay' if (value or '').strip().lower() == 'relay' else 'full'


@packaged_hub_operation('start')
def start_hub(root):
    if dispatch_if_needed('start-hub', root):
        return True
    from services.shared.process_lifecycle import ensure_started
    root = Path(root).resolve()
    hub = root / 'services/hub'
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(root), str(hub / 'src'), os.environ.get('PYTHONPATH')])), PYTHONUNBUFFERED='1')
    return ensure_started(root, 'hub', [python_at(hub), str(hub / 'run_local.py')], env=env)


@packaged_hub_operation('stop')
def stop_hub(root):
    if dispatch_if_needed('stop-hub', root):
        return True
    from services.shared.process_lifecycle import stop_component
    stop_component(root, 'hub')
    if hub_mode(root) == 'relay':
        return True  # A light Hub has no database.
    from services.shared.environment import get_env
    hub_database(root, get_env).stop()
    return True


def router_root(script):
    from services.shared.router_installation import registered_router_root
    # An explicitly passed development entry point must never operate on the
    # user's installed Router. Installer/manager discovery resolves the registry
    # before calling this function.
    if script:
        root = Path(script).resolve().parent
        import tomllib
        try:
            project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise LifecycleError('Router installation cannot be verified') from error
        if project.get('project', {}).get('name') != 'orion-router':
            raise LifecycleError('Selected path is not Orion Router')
        return root
    registered = registered_router_root()
    if registered is None:
        raise LifecycleError('No Router selected; select an existing installation first')
    return Path(registered).resolve()


def router_python(root):
    from .runtime_layout import component, python_path
    if component(root):
        path = python_path(root)
        if not path.is_file():
            raise LifecycleError('Packaged Router Python missing; repair required')
        return str(path)
    # Legacy native Router installers use the user's Python; dev checkouts may
    # have a venv. This phase does not silently replace that runtime.
    path = Path(root) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    selected = str(path) if path.is_file() else shutil.which('python' if os.name == 'nt' else 'python3')
    if not selected:
        raise LifecycleError('Router Python runtime unavailable')
    return selected


def start_router(script, env=None):
    from .update_gate import require_start_admission
    require_start_admission(router_root(script))
    if dispatch_if_needed('start-router', Path(__file__).resolve().parents[2], script):
        return True
    from services.shared.process_lifecycle import ensure_started
    root = router_root(script)
    # Unupgraded legacy launchers cannot promise the new ownership contract.
    if not (root / 'core/process_lifecycle.py').is_file():
        raise LifecycleError('Router lifecycle upgrade required; existing Router left untouched')
    if env is None:
        from services.shared.launcher import router_environment
        env = router_environment()
    return ensure_started(root, 'router-prod', [router_python(root), str(root / 'cli.py'), 'prod'], env=env)


def stop_router(script):
    root = router_root(script)
    if not (root / 'core/process_lifecycle.py').is_file():
        raise LifecycleError('Router ownership unavailable; stop legacy Router manually')
    # Router supervisor owns DB ordering and independent shutdown.
    result = subprocess.run([router_python(root), str(root / 'cli.py'), 'stop'], cwd=root, timeout=150,
                            **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
    if result.returncode:
        raise LifecycleError('Router shutdown failed; state retained')
    return True


if __name__ == '__main__':
    action, root, argument = sys.argv[1:]
    {'start-hub': start_hub, 'stop-hub': stop_hub,
     'start-router': start_router, 'stop-router': stop_router}[action](argument if action.endswith('router') else root)
