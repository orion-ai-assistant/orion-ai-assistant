"""Install components into %LOCALAPPDATA%\\Orion, new or added to an existing installation.

`materialize`/`finish_setup` are the per-component steps shared by the first install and by `add_components`.
Adding Hub/Client to a Router-only installation (or a Router to a Hub installation) never touches what is
already there: the registered component keeps its code, data, identity and releases; only new component
folders and the registry change. Registry changes happen under the operation lock with a journal
(`updates/add-journal.json`) and are rolled back, together with the folders the transaction created, when
anything fails or the process dies half way (`recover_incomplete_add`).

User-facing messages are Turkish (the installer window is the only place they are shown).
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import uuid

from .installation_paths import canonical_path, validate_destination, validate_private_directory
from .installation_registry import Registry, component_record, operation_lock, _atomic_write
from .release_packages import compatible, record_prepared_package

KIND_DIRS = {'hub': 'Hub', 'router': 'Router', 'client': 'Client'}
KIND_TITLES = {'hub': 'Hub', 'router': 'Router', 'client': 'Web istemcisi'}
TERMINAL_JOURNAL = {'completed', 'rolled_back'}
KEPT_MARKER = '.orion-kept-by-uninstall.json'  # written into data/ by an uninstall that kept the user's data


def kept_data_dir(directory):
    """True for a component folder that only holds data kept by an earlier uninstall (adoptable by install)."""
    directory = Path(directory)
    try:
        return {p.name for p in directory.iterdir()} == {'data'} and (directory / 'data' / KEPT_MARKER).is_file()
    except OSError:
        return False


def check_destination(directory):
    """A component folder must be empty/new, or hold only data kept by an earlier uninstall."""
    if kept_data_dir(directory):
        validate_private_directory(canonical_path(directory))
        return
    validate_destination(directory, private=True)


class AddError(ValueError):
    """A refusal or failure the user can read (Turkish); nothing was changed unless the text says so."""


def titles(kinds):
    return ', '.join(KIND_TITLES[k] for k in sorted(kinds))


def materialize(client, target, root, settings):
    """Prepare one component's folders and return its (not yet registered) registry item."""
    release = target.custom
    directory = root / KIND_DIRS[release['component']]
    tree = client.prepare(target, directory / 'releases' / release['package_id'])
    data, state = directory / 'data', directory / 'state'
    adopted = (data / KEPT_MARKER).is_file()  # Data an earlier uninstall kept: reused as it is, secrets included.
    data.mkdir(exist_ok=adopted)
    state.mkdir()
    if adopted:
        (data / KEPT_MARKER).unlink()
    elif release['component'] == 'hub':
        (data / 'services').mkdir()
        # Generated secrets remain in data; static package defaults are
        # not copied from any development or existing user installation.
        env = data / 'services/.env.global.local'
        env.write_text('ORION_INSTALL_MODE=local\nORION_LOCAL_SHORTCUTS=0\n'
                       'JWT_SECRET_KEY=' + secrets.token_urlsafe(48) + '\n', encoding='utf-8')
        with env.open('a', encoding='utf-8') as stream:
            stream.write(''.join(f'{k}={v}\n' for k, v in settings.get('hub', {}).items()))
    elif release['component'] == 'router' and not adopted:
        # Preserve defaults as data on first install, never rewrite them
        # on package preparation or any later component change.
        (data / '.env').write_text(''.join(f'{k}={v}\n' for k, v in settings.get('router', {}).items()), encoding='utf-8')
    return component_record(release['component'], tree, distribution='packaged',
                            data_path=data, state_path=state, ownership='owned', management='local',
                            package=record_prepared_package(tree, state, target))


def finish_setup(registry, items):
    """Steps that need the registry to exist first, so every executable resolves exactly the new data
    directory. Failures retain the installation for explicit repair (first install) or roll the add back."""
    for item in items:
        tree = Path(item['code_path'])
        env = dict(os.environ, ORION_INSTALLATION_REGISTRY=str(registry.path), ORION_SKIP_GLOBAL_REGISTRATION='1')
        if item['kind'] == 'router':
            result = subprocess.run([str(tree / 'runtime/python/python.exe'), '-c',
                'import json; from core import config; from core.mdns import router_id; '
                'from core.tls_identity import load_identity; '
                'i=load_identity(config.TLS_DIRECTORY,router_id(config.MDNS_ID_FILE)); '
                'print(json.dumps(dict(router_uuid=i.id,tls_spki_sha256=i.fp,identity_path=str(config.MDNS_ID_FILE),'
                'certificate_path=str(i.certificate_path),tls_port=config.TLS_PORT)))'],
                cwd=tree, env=env, check=True, capture_output=True, timeout=30)
            identity = json.loads(result.stdout.decode('utf-8').splitlines()[-1])

            def save_identity(current, component_id=item['component_id'], identity=identity):
                target = next(c for c in current['components'] if c['component_id'] == component_id)
                target.update(identity)
                return current
            registry.update(save_identity)
        if item['kind'] == 'hub':
            subprocess.run([str(tree / 'runtime/python/python.exe'), str(tree / 'local_db_setup.py')],
                           cwd=tree, env=env, check=True, timeout=180)
            subprocess.run([str(tree / 'runtime/python/python.exe'), '-c',
                'from services.shared.launcher import install_entry; '
                'from services.shared.installation_registry import Registry; install_entry(Registry(),desktop=False)'],
                cwd=tree, env=env, check=True, timeout=30)


# --- add a component to an existing installation ---------------------------------------------------

def _journal_path(registry):
    return registry.path.parent / 'updates/add-journal.json'


def _write_journal(registry, value):
    path = _journal_path(registry)
    canonical_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, (json.dumps(value, ensure_ascii=True) + '\n').encode())


def _read_journal(registry):
    try:
        value = json.loads(_journal_path(registry).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise AddError('Önceki ekleme kaydı okunamadı; Orion\'u onarın veya updates/add-journal.json dosyasını inceleyin.') from error
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise AddError('Önceki ekleme kaydı geçersiz; hiçbir şey değiştirilmedi.')
    return value


def _rollback(registry, journal, root):
    """Restore the registry and delete only folders this transaction created. Caller holds the lock."""
    previous = journal['previous']
    current = registry.read()
    if current is not None and current != previous:
        registry.write(previous)
    referenced = {str(Path(c['code_path']).parent.parent) for c in previous['components']}
    failed = []
    for name in journal.get('created', []):
        path = Path(name)
        try:
            inside = canonical_path(path).parent == canonical_path(root) and path.name in KIND_DIRS.values()
        except ValueError:
            inside = False
        if not inside or str(path) in referenced:
            continue  # Never delete anything that is not a component folder created by this journal.
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            failed.append(path.name)
    journal.update(phase='rolled_back', rolled_back_at=time.time(), leftover=failed)
    _write_journal(registry, journal)
    return failed


def recover_incomplete_add(registry, *, locked=False):
    """An earlier add that died between preparing and completed is rolled back (registry + folders)."""
    journal = _read_journal(registry)
    if journal is None or journal.get('phase') in TERMINAL_JOURNAL:
        return None
    if journal.get('installation_id') != (registry.read() or {}).get('installation_id'):
        raise AddError('Önceki ekleme kaydı başka bir kuruluma ait; hiçbir şey değiştirilmedi.')
    if locked:
        return _rollback(registry, journal, journal['root'])
    with operation_lock(registry.path, timeout=180):
        return _rollback(registry, journal, journal['root'])


def plan_add(record, kinds, *, skip_existing=False):
    """Which of `kinds` can be added to `record`; raises AddError with the reason otherwise."""
    kinds = set(kinds)
    existing = {c['kind'] for c in record['components']}
    already = kinds & existing
    if already and not skip_existing:
        raise AddError(f'{titles(already)} zaten kurulu. Kurulu bileşenler değiştirilmez; güncellemeler Orion ayarlarından yapılır.')
    kinds -= existing
    if not kinds:
        raise AddError('Eklenecek yeni bileşen yok: istenen bileşenlerin hepsi zaten kurulu.')
    if 'client' in kinds and 'hub' not in kinds | existing:
        raise AddError('Web istemcisi bir Hub gerektirir; Hub ile birlikte kurun.')
    for item in record['components']:
        if item['distribution'] != 'packaged' or item['ownership'] != 'owned' or item['management'] != 'local':
            raise AddError('Mevcut kurulum Orion paket kurucusuyla yapılmamış (kaynak/geliştirme kaydı); '
                           'ona bileşen eklenmez, kayıt olduğu gibi korundu.')
    return kinds


def add_components(client, names, root, registry, *, settings=None, skip_existing=False, release_source=None):
    """Add the signed components `names` to the installation in `registry`; existing ones stay untouched.

    `release_source` (the installer bundle's release-source.json value) is merged into the installation's
    feed list so the new component is also updated from its own feed. Returns the new registry record."""
    import sys
    if sys.platform != 'win32':
        raise AddError('Yalnızca 64-bit Windows desteklenir.')
    root = canonical_path(root)
    registry = Registry(canonical_path(registry))
    settings = settings or {}
    with operation_lock(registry.path, timeout=180):
        recover_incomplete_add(registry, locked=True)
        old = registry.read()
        if old is None:
            raise AddError('Kurulu bir Orion bulunamadı; önce tam kurulum yapın.')
        if canonical_path(old['root']) != root:
            raise AddError('Kurulum klasörü mevcut kayıttakiyle aynı değil; hiçbir şey değiştirilmedi.')
        from .update_gate import blocked, TERMINAL
        from .update_helper import UpdateHelper, UpdateError
        try:
            pending = UpdateHelper(registry).status()
        except UpdateError as error:
            raise AddError('Güncelleme kaydı onarım gerektiriyor; önce onarın. Hiçbir şey değiştirilmedi.') from error
        if blocked(registry.path) or (pending and pending['phase'] not in TERMINAL):
            raise AddError('Bekleyen veya yarım kalmış bir güncelleme var; o bitince yeniden deneyin. Hiçbir şey değiştirilmedi.')
        trusted = registry.path.parent / 'bootstrap/trusted-root.json'
        if trusted.is_file() and trusted.read_bytes() != client.bootstrap_root:
            raise AddError('Bu kurucunun güven kökü kurulu Orion\'unkiyle aynı değil; hiçbir şey eklenmedi.')
        status, targets = client.check(names)
        if targets is None:
            raise AddError('İmzalı sürüm denetimi yapılamadı (' + status['reason'] + '). İnternet bağlantısını kontrol edip yeniden deneyin.')
        wanted = {t.custom['component'] for t in targets.values()}
        if len(wanted) != len(targets):
            raise AddError('Her bileşen için tek paket seçilmelidir.')
        kinds = plan_add(old, wanted, skip_existing=skip_existing)
        chosen = {n: t for n, t in targets.items() if t.custom['component'] in kinds}
        installed = {}
        for item in old['components']:
            installed[item['kind']] = json.loads((Path(item['code_path']) / 'orion-release.json').read_text(encoding='utf-8'))
        try:
            compatible([*installed.values(), *(t.custom for t in chosen.values())],
                       installed_sequences={k: v['sequence'] for k, v in installed.items()})
        except ValueError as error:
            raise AddError('Yeni bileşen kurulu sürümlerle uyumlu değil (' + str(error) + '); hiçbir şey eklenmedi. '
                           'Önce mevcut kurulumu güncelleyin.') from error
        for kind in kinds:
            directory = root / KIND_DIRS[kind]
            try:
                check_destination(directory)
            except ValueError as error:
                raise AddError(f'{directory.name} klasörü zaten var veya kullanılamıyor; hiçbir şey eklenmedi.') from error
        journal = {'schema': 1, 'phase': 'preparing', 'installation_id': old['installation_id'], 'root': str(root),
                   'kinds': sorted(kinds), 'previous': old, 'created': [], 'started_at': time.time(),
                   'operation_id': str(uuid.uuid4())}
        _write_journal(registry, journal)
        items = []
        try:
            for target in chosen.values():
                directory = root / KIND_DIRS[target.custom['component']]
                journal['created'].append(str(directory))
                _write_journal(registry, journal)  # Recorded before it exists: a crash can always clean it.
                items.append(materialize(client, target, root, settings))
            record = json.loads(json.dumps(old))
            record['components'] = [*record['components'], *items]
            from .installation_registry import validate_record
            validate_record(record)
            journal['phase'] = 'registering'
            _write_journal(registry, journal)
            registry.write(record)
            journal['phase'] = 'registered'
            _write_journal(registry, journal)
            finish_setup(registry, items)
            if release_source is not None:
                from .automatic_updates import merge_sources, read_json, write_json
                path = registry.path.parent / 'bootstrap/release-source.json'
                current = read_json(path, None)
                write_json(path, merge_sources(current, release_source) if current is not None else release_source)
        except BaseException as error:
            left = _rollback(registry, journal, root)
            detail = f' Bazı klasörler silinemedi ({", ".join(left)}); elle silebilirsiniz.' if left else ''
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise AddError(f'{titles(kinds)} eklenemedi ({type(error).__name__}); kayıt eski haline döndürüldü, '
                           f'mevcut kurulum olduğu gibi duruyor.{detail}') from error
        journal.update(phase='completed', completed_at=time.time())
        _write_journal(registry, journal)
        return registry.read()
