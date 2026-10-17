"""Local content migration for a verified stopped installation.

Does not stop user services. Holding their normal operation and instance locks
prevents a concurrent native start until copy, verification and switch finish.
"""
from contextlib import ExitStack
from pathlib import Path
import os

from .installation_registry import operation_lock
from .installation_paths import canonical_path
from .process_lifecycle import InstanceLock, Runtime, child_tree_alive
from .content_migration import MigrationError, _begin_content_migration_locked, _resume_content_migration_locked


def _targets(record):
    targets = set()
    for item in record['components']:
        if item['distribution'] == 'remote':
            continue
        if item['management'] != 'local' or item['ownership'] != 'owned' or item['distribution'] == 'docker':
            raise MigrationError('Local consumer ownership is not established; migration is unavailable')
        root = canonical_path(item['code_path'], must_exist=True)
        if Path(item['state_path']).resolve() != root / '.orion-lifecycle':
            raise MigrationError('This lifecycle does not support external component state yet')
        if item['kind'] == 'hub':
            targets.add((root, 'hub'))
            services = root / 'services'
            for directory, children, files in os.walk(services, followlinks=False):
                children[:] = [name for name in children if not name.startswith('.')
                               and name not in {'models', 'data', 'node_modules', '__pycache__'}]
                for name in children:
                    canonical_path(Path(directory) / name, must_exist=True)
                if 'manifest.json' in files:
                    targets.add((canonical_path(directory, must_exist=True), 'service'))
        elif item['kind'] == 'router':
            targets.update((root, mode) for mode in ('router-prod', 'router-dev'))
        else:
            raise MigrationError('Client consumer coordination is not implemented; migration is unavailable')
    return sorted(targets, key=lambda value: (os.path.normcase(str(value[0])), value[1]))


class StoppedInstallation:
    def __init__(self, registry, *, operation_locked=False):
        self.registry, self.stack, self.held = registry, ExitStack(), False
        self.operation_locked = operation_locked

    def __enter__(self):
        try:
            if not self.operation_locked:
                self.stack.enter_context(operation_lock(self.registry.path))
            self.record = self.registry.read()
            if self.record is None:
                raise MigrationError('No registered installation')
            self.targets = _targets(self.record)
            for root, component in self.targets:
                directory = root / '.orion-lifecycle' / component
                self.stack.enter_context(InstanceLock(directory, 'operation', timeout=0))
                self.stack.enter_context(InstanceLock(directory, 'instance', timeout=0))
            self.held = True
            if not self():
                raise MigrationError('A registered consumer is running or needs recovery; existing services were left untouched')
            self._unmanaged_consumers()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __call__(self):
        if not self.held or self.registry.read() is None:
            return False
        # Registry may switch its location inside the held operation; IDs and
        # component ownership, however, must remain the same throughout.
        current = self.registry.read()
        if (current['installation_id'] != self.record['installation_id']
                or current['components'] != self.record['components']):
            return False
        for root, component in self.targets:
            state = Runtime(root, component).read()
            if state is None:
                continue
            # The instance lock is held here. A CLI may have exited its Runtime
            # context while its OS process continues doing unrelated work.
            if (state['status'] != 'stopped'
                    or any(child_tree_alive(child) for child in state['children'].values())):
                return False
            if state.get('database'):
                from .native_postgres import Postgres
                if Postgres(**state['database']).owner() is not None:
                    return False
        return True

    def _unmanaged_consumers(self):
        import psutil
        ignored = {os.getpid(), *(process.pid for process in psutil.Process().parents())}
        user = psutil.Process().username()
        roots = [Path(item['code_path']).resolve() for item in self.record['components']
                 if item['distribution'] != 'remote']
        for process in psutil.process_iter(['pid', 'username']):
            if process.pid in ignored or process.info['username'] != user:
                continue
            try:
                executable = Path(process.exe()).resolve()
                if any(executable.is_relative_to(root) for root in roots):
                    raise MigrationError('An uncoordinated process uses this installation; migration refused')
                # Unrelated protected desktop applications may deny cwd access.
                # Only source interpreters need argv/cwd disambiguation; native
                # product binaries have already been checked by their path.
                if executable.name.lower() not in {'python.exe', 'pythonw.exe', 'python', 'python3', 'node.exe', 'node'}:
                    continue
                paths = [Path(process.cwd()), *[Path(arg) for arg in process.cmdline() if Path(arg).is_absolute()]]
                if any(path.resolve().is_relative_to(root) for path in paths for root in roots):
                    raise MigrationError('An uncoordinated process uses this installation; migration refused')
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, OSError, ValueError) as error:
                raise MigrationError('Cannot establish local consumer ownership') from error

    def __exit__(self, *args):
        self.held = False
        self.stack.close()


def move_content(registry, name, destination, *, expected_revision, fault=None):
    with StoppedInstallation(registry) as gate:
        return _begin_content_migration_locked(registry, name, destination, expected_revision=expected_revision,
                                       quiescence_gate=gate, fault=fault)


def recover_content(registry, journal_path, *, fault=None):
    with StoppedInstallation(registry) as gate:
        return _resume_content_migration_locked(registry, journal_path, quiescence_gate=gate, fault=fault)
