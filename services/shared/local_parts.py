"""Optional local AI parts ("Bileşenler"): what each one is, the models it can use, this PC's hardware, what
is installed, and the jobs that download and install them.

    parts home   <installation>/Parts for an installed Orion; <source tree>/.local_parts when the Hub runs from
                 source (a development tree never writes into the installed Orion's folder)
      config.json                         per part: enabled, model, settings
      engines/<engine>-<version>/<hw>/    llama-server and its libraries, shared by the parts that use them
      models/<part>/<folder>/<files>      (under the installation's models location when it has one)
      jobs/<part>.json                    the running or last job; jobs/<part>.cancel asks it to stop
      run/<part>/                         process lifecycle state of the running part

Nothing is installed with Orion itself: the user installs a part when they want it (Ayarlar > Bileşenler, or
where a feature needs it). A job runs in its own process (`python -m services.shared.local_parts job ...`), so
closing the Orion window never stops a download, and a download that was cut continues where it stopped.
Installed parts start with the Hub (`keep_running`) and stop with it.

The catalog (local_parts.json) lists engine builds per operating system; a part whose engine has no build for
this system is shown but cannot be installed here yet.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path(__file__).with_name('local_parts.json')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
TERMINAL = {'done', 'error', 'cancelled', 'interrupted'}
READY_TIMEOUT = 300           # a big model loads for minutes on a slow disk
HARDWARE_TTL = 600
HARDWARE_LABELS = {'cuda': 'Ekran kartı (NVIDIA CUDA)', 'vulkan': 'Ekran kartı (Vulkan)', 'metal': 'Apple GPU (Metal)',
                   'cpu': 'İşlemci (CPU)'}
ALL_LAYERS = 999


class PartError(ValueError):
    """A refusal the user reads (Turkish)."""


def catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def spec(part_id, data=None):
    for item in (data or catalog())['parts']:
        if item['id'] == part_id:
            return item
    raise PartError('Böyle bir bileşen yok.')


def model_spec(part, model_id):
    for model in part.get('models', []):
        if model['id'] == model_id:
            return model
    raise PartError('Bu bileşenin böyle bir modeli yok.')


def system():
    return {'Windows': 'windows', 'Darwin': 'mac', 'Linux': 'linux'}.get(platform.system(), platform.system().lower())


# --- Where things live ---------------------------------------------------------------------------------------

def _installed():
    """The installation record when this code is the registered Hub's own code, else None."""
    try:
        from .installation_registry import Registry
        record = Registry().read()
    except Exception:
        return None
    for item in (record or {}).get('components', []):
        if item['kind'] == 'hub':
            try:
                if ROOT.is_relative_to(Path(item['code_path']).resolve()):
                    return record
            except (OSError, ValueError):
                pass
    return None


def home():
    override = os.getenv('ORION_PARTS_HOME')
    if override:
        return Path(override)
    from .installation_registry import Registry
    record = _installed()
    return Path(Registry().path).parent / 'Parts' if record else ROOT / '.local_parts'


def models_dir(part_id):
    record = None if os.getenv('ORION_PARTS_HOME') else _installed()
    location = ((record or {}).get('locations') or {}).get('models')
    return (Path(location) / 'parts' if location else home() / 'models') / part_id


def engine_dir(engine, hardware, data=None):
    version = (data or catalog())['engines'][engine]['version']
    return home() / 'engines' / f'{engine}-{version}' / hardware


def engine_exe(engine, hardware, data=None):
    folder = engine_dir(engine, hardware, data)
    name = 'llama-server.exe' if os.name == 'nt' else 'llama-server'
    found = next(iter(sorted(folder.rglob(name))), None) if folder.is_dir() else None
    return found


def model_files(part, model):
    folder = models_dir(part['id']) / model['folder']
    return [folder / name for name in model['files'] + ([model['mmproj']] if model.get('mmproj') else [])]


def downloaded(part, model):
    return all(path.is_file() for path in model_files(part, model))


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def read_config():
    return _read_json(home() / 'config.json', {})


def write_part_config(part_id, **values):
    config = read_config()
    config[part_id] = {**config.get(part_id, {}), **values}
    _write_json(home() / 'config.json', config)
    return config[part_id]


# --- Hardware ------------------------------------------------------------------------------------------------

_hardware = {'at': 0, 'value': None}


def _nvidia(run=subprocess.run):
    try:
        result = run(['nvidia-smi', '--query-gpu=index,name,memory.total,driver_version',
                      '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=8,
                     creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    gpus = []
    for line in result.stdout.splitlines():
        cells = [cell.strip() for cell in line.split(',')]
        if len(cells) >= 3 and cells[0].isdigit():
            try:
                vram = int(float(cells[2]))
            except ValueError:
                vram = None
            gpus.append({'index': int(cells[0]), 'name': cells[1], 'vram_mb': vram, 'vendor': 'nvidia'})
    return gpus


def _other_gpus(run=subprocess.run):
    """Non-NVIDIA display adapters on Windows (their memory size is not reliable there, so it is left out)."""
    if os.name != 'nt':
        return []
    powershell = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    try:
        result = run([powershell, '-NoProfile', '-NonInteractive', '-Command',
                      '(Get-CimInstance Win32_VideoController).Name'], capture_output=True, text=True, timeout=10,
                     creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    gpus = []
    for name in names:
        lower = name.lower()
        if 'nvidia' in lower or 'microsoft basic' in lower or 'remote' in lower or 'virtual' in lower:
            continue
        vendor = 'amd' if ('amd' in lower or 'radeon' in lower) else 'intel' if 'intel' in lower else 'other'
        gpus.append({'index': len(gpus), 'name': name, 'vram_mb': None, 'vendor': vendor})
    return gpus


def hardware(*, refresh=False, run=subprocess.run):
    """This PC: its system, graphics cards and which ways a llama.cpp part can run here (recommended first)."""
    if not refresh and _hardware['value'] and time.monotonic() - _hardware['at'] < HARDWARE_TTL:
        return _hardware['value']
    current = system()
    nvidia = _nvidia(run) if current != 'mac' else []
    others = _other_gpus(run) if current == 'windows' else []
    if current == 'mac':
        choices = ['metal', 'cpu'] if platform.machine().lower() in {'arm64', 'aarch64'} else ['cpu']
    else:
        choices = (['cuda'] if nvidia else []) + (['vulkan'] if nvidia or others else []) + ['cpu']
    value = {'system': current, 'gpus': nvidia + others, 'choices': [{'id': c, 'label': HARDWARE_LABELS[c]} for c in choices],
             'recommended': choices[0]}
    _hardware.update(at=time.monotonic(), value=value)
    return value


# --- Settings ------------------------------------------------------------------------------------------------

def default_settings(part, model, hw):
    """What a part runs with unless the user changes it (Gelişmiş ayarlar)."""
    hardware_id = hw['recommended']
    layers = 0
    if hardware_id != 'cpu':
        layers = ALL_LAYERS
        vram = max((gpu['vram_mb'] or 0 for gpu in hw['gpus']), default=0)
        size = (model or {}).get('size_mb') or 0
        if vram and size and size * 1.2 > vram:
            layers = 24  # Does not fit: half on the card, the rest on the processor.
    return {'hardware': hardware_id, 'gpu': 'all', 'gpu_layers': layers, 'context_size': part.get('context_size', 8192),
            'parallel': 1}


def advanced(part, hw):
    """The advanced settings a card shows (all have a default)."""
    fields = [{'key': 'hardware', 'label': 'Nerede çalışsın', 'type': 'choice', 'options': hw['choices']}]
    if len(hw['gpus']) > 1:
        fields.append({'key': 'gpu', 'label': 'Hangi ekran kartı', 'type': 'choice',
                       'options': [{'id': 'all', 'label': 'Hepsi'}] + [
                           {'id': str(gpu['index']), 'label': f"{gpu['index']}: {gpu['name']}"} for gpu in hw['gpus']]})
    fields += [
        {'key': 'gpu_layers', 'label': 'Ekran kartına giden katman', 'type': 'int', 'min': 0, 'max': ALL_LAYERS,
         'hint': f'{ALL_LAYERS} = hepsi. Ekran kartı belleği yetmezse azalt.'},
        {'key': 'context_size', 'label': 'Bağlam boyutu (token)', 'type': 'int', 'min': 512, 'max': 131072},
        {'key': 'parallel', 'label': 'Aynı anda istek', 'type': 'int', 'min': 1, 'max': 8},
    ]
    return fields


def clean_settings(part, values, hw):
    """Only known keys, inside their limits; anything else is refused with a Turkish reason."""
    fields = {field['key']: field for field in advanced(part, hw)}
    result = {}
    for key, value in (values or {}).items():
        field = fields.get(key)
        if field is None:
            raise PartError(f'Bilinmeyen ayar: {key}')
        if field['type'] == 'choice':
            if str(value) not in {option['id'] for option in field['options']}:
                raise PartError(f'{field["label"]}: bu seçenek bu bilgisayarda yok.')
            result[key] = str(value)
        else:
            try:
                number = int(value)
            except (TypeError, ValueError):
                raise PartError(f'{field["label"]} bir sayı olmalı.') from None
            if not field['min'] <= number <= field['max']:
                raise PartError(f'{field["label"]} {field["min"]} ile {field["max"]} arasında olmalı.')
            result[key] = number
    return result


def effective(part, hw, config=None):
    config = (read_config() if config is None else config).get(part['id'], {})
    model_id = config.get('model') or next((m['id'] for m in part.get('models', []) if m.get('recommended')), None)
    model = next((m for m in part.get('models', []) if m['id'] == model_id), None)
    settings = {**default_settings(part, model, hw), **(config.get('settings') or {})}
    if settings['hardware'] not in {choice['id'] for choice in hw['choices']}:
        settings['hardware'] = hw['recommended']  # A card that is gone, or a config copied from another PC.
    return model, settings


def command(part, model, settings, data=None):
    """llama-server's argv and environment for `part` running `model` with `settings`."""
    from .service_access import bind_host, own_origins
    exe = engine_exe(part['engine'], settings['hardware'], data)
    if exe is None:
        raise PartError('Motor kurulu değil.')
    files = model_files(part, model)
    port = str(os.getenv(part['port_key']) or part['port'])
    argv = [str(exe), '-m', str(files[0]), '--port', port, '--host', bind_host(), '--cors-origins', own_origins(port),
            '--no-slots', '-c', str(settings['context_size']), '-np', str(settings['parallel']),
            '-ngl', str(0 if settings['hardware'] == 'cpu' else settings['gpu_layers'])]
    if model.get('mmproj'):
        argv += ['--mmproj', str(files[-1])]
    argv += list(part.get('args', [])) + list(model.get('args', []))
    env = dict(os.environ)
    if settings.get('gpu', 'all') != 'all':
        env['CUDA_VISIBLE_DEVICES' if settings['hardware'] == 'cuda' else 'GGML_VK_VISIBLE_DEVICES'] = settings['gpu']
    return argv, env, int(port)


# --- State ---------------------------------------------------------------------------------------------------

def _alive(pid):
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except (ImportError, TypeError, ValueError):
        return False


def job(part_id):
    value = _read_json(home() / 'jobs' / f'{part_id}.json', None)
    if value and value.get('phase') not in TERMINAL and not _alive(value.get('pid')):
        value = {**value, 'phase': 'interrupted', 'error': 'İş yarıda kaldı (Orion kapandı ya da yeniden başladı). '
                 'Yeniden başlatınca indirme kaldığı yerden sürer.'}
    return value


def busy(part_id):
    value = job(part_id)
    return bool(value and value.get('phase') not in TERMINAL)


def answers(port, timeout=.5):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/health', timeout=timeout) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def run_dir(part_id):
    return home() / 'run' / part_id


def _supervised(part_id):
    """The part's supervisor is alive (starting or running)."""
    try:
        from .process_lifecycle import Runtime, living
        record = Runtime(run_dir(part_id), 'part').read()
        return bool(record and record['status'] != 'stopped' and living(record['supervisor']))
    except Exception:
        return False


def part_status(part, hw, config, data):
    port = int(os.getenv(part['port_key']) or part['port'])
    running = answers(port)
    base = {'id': part['id'], 'title': part['title'], 'description': part['description'], 'cloud': part.get('cloud'),
            'port': port}
    if not part.get('engine'):
        # Not installable from here yet: say whether it already runs (installed another way) and what else works.
        return {**base, 'installable': False, 'note': part.get('pending'), 'state': 'running' if running else 'absent',
                'models': [], 'advanced': [], 'settings': {}, 'job': None}
    builds = data['engines'][part['engine']].get(hw['system'])
    model, settings = effective(part, hw, config)
    entry = config.get(part['id'], {})
    current = job(part['id'])
    engine_ready = engine_exe(part['engine'], settings['hardware'], data) is not None
    if current and current.get('phase') not in TERMINAL:
        state = 'working'
    elif running:
        state = 'running'
    elif entry.get('enabled') and _supervised(part['id']):
        state = 'starting'
    elif entry.get('enabled'):
        state = 'stopped'
    else:
        state = 'absent'
    models = []
    for item in part['models']:
        partial = sum(p.with_name(p.name + '.part').stat().st_size for p in model_files(part, item)
                      if p.with_name(p.name + '.part').is_file())
        models.append({'id': item['id'], 'name': item['name'], 'description': item['description'],
                       'size_mb': item['size_mb'], 'recommended': bool(item.get('recommended')),
                       'downloaded': downloaded(part, item), 'partial_mb': round(partial / 1048576, 1)})
    return {**base, 'installable': bool(builds and settings['hardware'] in builds),
            'note': None if builds else 'Bu işletim sistemi için kurulum henüz yok (şimdilik yalnızca Windows).',
            'state': state, 'enabled': bool(entry.get('enabled')), 'model': model['id'] if model else None,
            'engine_ready': engine_ready, 'models': models, 'settings': settings, 'advanced': advanced(part, hw),
            'job': current}


def status():
    data = catalog()
    hw = hardware()
    config = read_config()
    return {'system': hw['system'], 'hardware': hw, 'parts': [part_status(p, hw, config, data) for p in data['parts']]}


# --- Actions (the Hub API calls these) -----------------------------------------------------------------------

def _spawn(argv):
    """A job/serve process of its own: closing the window or ending a request never ends it."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8')
    (home() / 'jobs').mkdir(parents=True, exist_ok=True)
    log = (home() / 'jobs' / 'jobs.log').open('ab')
    try:
        return subprocess.Popen([sys.executable, '-B', '-m', 'services.shared.local_parts', *argv], cwd=str(ROOT),
                                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=NO_WINDOW | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    finally:
        log.close()


def start_job(part_id, action, *, model=None, settings=None):
    """`download` fetches a model only; `install` fetches what is missing (engine, model), turns the part on
    and starts it: one press for "Modeli indir ve kur"."""
    data = catalog()
    part = spec(part_id, data)
    if not part.get('engine'):
        raise PartError(part.get('pending') or 'Bu bileşen buradan kurulamıyor.')
    hw = hardware()
    if not data['engines'][part['engine']].get(hw['system']):
        raise PartError('Bu işletim sistemi için kurulum henüz yok.')
    if busy(part_id):
        raise PartError('Bu bileşende bir iş zaten sürüyor.')
    current, _ = effective(part, hw)
    chosen = model_spec(part, model) if model else current
    if chosen is None:
        raise PartError('Önce bir model seç.')
    if action == 'install':
        values = {'model': chosen['id']}
        if settings:
            saved = read_config().get(part_id, {}).get('settings') or {}
            values['settings'] = {**saved, **clean_settings(part, settings, hw)}
        write_part_config(part_id, **values)
    elif action != 'download':
        raise PartError('Bilinmeyen iş.')
    (home() / 'jobs' / f'{part_id}.cancel').unlink(missing_ok=True)
    _write_json(home() / 'jobs' / f'{part_id}.json', {'action': action, 'model': chosen['id'], 'phase': 'queued',
                                                       'label': 'Başlıyor…', 'started': time.time(), 'pid': None})
    process = _spawn(['job', part_id, action, chosen['id']])
    _update_job(part_id, pid=process.pid)
    return job(part_id)


def cancel(part_id):
    if busy(part_id):
        (home() / 'jobs' / f'{part_id}.cancel').touch()


def select(part_id, *, model=None, settings=None):
    """Save the chosen model and settings; a running part restarts with them."""
    data = catalog()
    part = spec(part_id, data)
    hw = hardware()
    values = {}
    if model is not None:
        values['model'] = model_spec(part, model)['id']
    if settings is not None:
        values['settings'] = {**(read_config().get(part_id, {}).get('settings') or {}), **clean_settings(part, settings, hw)}
    entry = write_part_config(part_id, **values)
    if entry.get('enabled') and not busy(part_id):
        stop_part(part_id)
        current, _ = effective(part, hw)
        if current and downloaded(part, current):
            _spawn(['start', part_id])
    return entry


def delete_model(part_id, model_id):
    part = spec(part_id)
    model = model_spec(part, model_id)
    entry = read_config().get(part_id, {})
    if busy(part_id):
        raise PartError('Bu bileşende bir iş sürüyor; bitince sil.')
    if entry.get('enabled') and (entry.get('model') or model_id) == model_id:
        raise PartError('Bu model şu an kullanılıyor. Önce başka model seç ya da bileşeni kaldır.')
    folder = models_dir(part_id) / model['folder']
    if folder.is_dir():
        shutil.rmtree(folder)


def remove(part_id):
    """Turn the part off and stop it. Its models stay (the model panel deletes them)."""
    if busy(part_id):
        raise PartError('Bu bileşende bir iş sürüyor; önce iptal et.')
    write_part_config(part_id, enabled=False)
    stop_part(part_id)


def start(part_id):
    part = spec(part_id)
    entry = read_config().get(part_id, {})
    if not entry.get('enabled'):
        raise PartError('Bileşen kurulu değil.')
    model, _ = effective(part, hardware())
    if model is None or not downloaded(part, model):
        raise PartError('Modeli indirilmemiş; Modeli indir ve kur ile kur.')
    _spawn(['start', part_id])


# --- Running parts -------------------------------------------------------------------------------------------

def start_part(part_id):
    from .process_lifecycle import ensure_started
    folder = run_dir(part_id)
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8')
    return ensure_started(folder, 'part', [sys.executable, '-B', '-m', 'services.shared.local_parts', 'serve', part_id],
                          env=env, timeout=READY_TIMEOUT + 15)


def stop_part(part_id):
    from .process_lifecycle import LifecycleError, Runtime, stop_component
    folder = run_dir(part_id)
    try:
        if Runtime(folder, 'part').read() is None:
            return False
        return stop_component(folder, 'part')
    except LifecycleError:
        return False


def keep_running():
    """The Hub's owner process calls this at start and then now and then: every part the user installed runs
    (a crashed one comes back), none of the others does."""
    data = catalog()
    config = read_config()
    hw = hardware()
    for part in data['parts']:
        if not part.get('engine') or not config.get(part['id'], {}).get('enabled') or busy(part['id']):
            continue
        model, settings = effective(part, hw, config)
        if model is None or not downloaded(part, model) or engine_exe(part['engine'], settings['hardware'], data) is None:
            continue
        if not answers(int(os.getenv(part['port_key']) or part['port'])):
            try:
                start_part(part['id'])
            except Exception as error:
                print(f'local part {part["id"]} did not start: {type(error).__name__}: {error}', flush=True)


def stop_all():
    for part in catalog()['parts']:
        if part.get('engine'):
            stop_part(part['id'])


def serve(part_id):
    """The part's supervisor process: llama-server under Orion's process lifecycle."""
    from .process_lifecycle import Runtime, assert_port_free, wait_health
    data = catalog()
    part = spec(part_id, data)
    model, settings = effective(part, hardware(), read_config())
    argv, env, port = command(part, model, settings, data)
    with Runtime(run_dir(part_id), 'part') as runtime:
        if 'engine' not in runtime.children:
            assert_port_free(port)
        child = runtime.launch('engine', argv, cwd=str(Path(argv[0]).parent), env=env, creationflags=NO_WINDOW)
        wait_health(child, port, timeout=READY_TIMEOUT)
        runtime.ready()
        while not runtime.requested() and child.poll() is None:
            runtime.refresh_children()
            time.sleep(.2)


# --- Jobs ----------------------------------------------------------------------------------------------------

class Cancelled(Exception):
    pass


def _update_job(part_id, **values):
    path = home() / 'jobs' / f'{part_id}.json'
    _write_json(path, {**_read_json(path, {}), **values})


def download(url, target, *, report, cancelled, chunk=1 << 20, opener=None):
    """`url` into `target`, continuing a cut `.part` file; `report(bytes, total)` as it goes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.part')
    have = partial.stat().st_size if partial.is_file() else 0
    request = urllib.request.Request(url, headers={'User-Agent': 'Orion', **({'Range': f'bytes={have}-'} if have else {})})
    opener = opener or urllib.request.build_opener()
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        if error.code == 416 and have:  # Already complete.
            os.replace(partial, target)
            return
        raise
    with response:
        if have and response.status != 206:
            have = 0  # The server sends it whole: start over.
        length = response.headers.get('Content-Length')
        total = have + int(length) if length and length.isdigit() else None
        with partial.open('ab' if have else 'wb') as out:
            done = have
            report(done, total)
            while True:
                if cancelled():
                    raise Cancelled()
                block = response.read(chunk)
                if not block:
                    break
                out.write(block)
                done += len(block)
                report(done, total)
    if total is not None and done < total:
        raise OSError('İndirme yarıda kesildi; yeniden başlatınca kaldığı yerden sürer.')
    os.replace(partial, target)


class _Progress:
    """Writes the job file at most twice a second, with the speed of the last few seconds."""
    def __init__(self, part_id, label, offset=0, overall=None):
        self.part_id, self.label, self.offset, self.overall = part_id, label, offset, overall
        self.samples = []
        self.last = 0

    def __call__(self, done, total):
        now = time.monotonic()
        self.samples = [s for s in self.samples if now - s[0] < 5] + [(now, done)]
        if now - self.last < .5 and (total is None or done < total):
            return
        self.last = now
        rate = (self.samples[-1][1] - self.samples[0][1]) / (self.samples[-1][0] - self.samples[0][0]) \
            if len(self.samples) > 1 and self.samples[-1][0] > self.samples[0][0] else 0
        overall_total = self.overall if self.overall else total
        _update_job(self.part_id, label=self.label, bytes=self.offset + done, total=overall_total, rate=round(rate))


def _install_engine(part_id, part, settings, data, cancelled):
    hardware_id = settings['hardware']
    if engine_exe(part['engine'], hardware_id, data) is not None:
        return
    urls = data['engines'][part['engine']].get(system(), {}).get(hardware_id)
    if not urls:
        raise PartError('Bu donanım için motor yok.')
    final = engine_dir(part['engine'], hardware_id, data)
    (home() / 'engines').mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='unpack-', dir=str(home() / 'engines')))
    try:
        unpacked = work / 'unpacked'
        for number, url in enumerate(urls, 1):
            _update_job(part_id, phase='engine', label=f'Motor indiriliyor ({number}/{len(urls)})', bytes=0, total=None)
            archive = home() / 'engines' / 'downloads' / url.rsplit('/', 1)[-1]
            download(url, archive, report=_Progress(part_id, f'Motor indiriliyor ({number}/{len(urls)})'), cancelled=cancelled)
            _update_job(part_id, phase='engine', label='Motor açılıyor…', rate=0)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(unpacked)
        if not any(unpacked.rglob('llama-server.exe' if os.name == 'nt' else 'llama-server')):
            raise PartError('İndirilen motorda llama-server yok.')
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            shutil.rmtree(final)
        os.replace(unpacked, final)
        shutil.rmtree(home() / 'engines' / 'downloads', ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fetch_model(part_id, part, model, cancelled):
    files = model_files(part, model)
    missing = [path for path in files if not path.is_file()]
    if not missing:
        return
    estimate = int(model['size_mb'] * 1048576)
    offset = sum(path.stat().st_size for path in files if path.is_file())
    for path in missing:
        label = f'{model["name"]} indiriliyor'
        _update_job(part_id, phase='model', label=label, total=estimate)
        download(model['url'] + path.name, path, report=_Progress(part_id, label, offset, estimate), cancelled=cancelled)
        offset += path.stat().st_size


def run_job(part_id, action, model_id):
    data = catalog()
    part = spec(part_id, data)
    model = model_spec(part, model_id)
    marker = home() / 'jobs' / f'{part_id}.cancel'
    cancelled = marker.is_file
    _update_job(part_id, pid=os.getpid(), phase='starting', label='Hazırlanıyor…', error=None)
    try:
        if action == 'install':
            _, settings = effective(part, hardware(), read_config())
            _install_engine(part_id, part, settings, data, cancelled)
        _fetch_model(part_id, part, model, cancelled)
        if action == 'install':
            write_part_config(part_id, enabled=True, model=model_id)
            _update_job(part_id, phase='launching', label='Servis başlatılıyor (model yükleniyor)…', bytes=None, total=None,
                        rate=0)
            stop_part(part_id)
            start_part(part_id)
        _update_job(part_id, phase='done', label='Hazır.', rate=0, finished=time.time())
        return 0
    except Cancelled:
        _update_job(part_id, phase='cancelled', label='İptal edildi.', rate=0, finished=time.time())
        return 1
    except Exception as error:
        detail = str(error) if isinstance(error, (PartError, OSError)) else f'{type(error).__name__}: {error}'
        _update_job(part_id, phase='error', label='Olmadı.', error=detail, rate=0, finished=time.time())
        return 1
    finally:
        marker.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('serve', 'start'):
        sub.add_parser(name).add_argument('part')
    job_parser = sub.add_parser('job')
    job_parser.add_argument('part')
    job_parser.add_argument('kind', choices=['download', 'install'])
    job_parser.add_argument('model')
    sub.add_parser('status')
    args = parser.parse_args(argv)
    if args.action == 'serve':
        serve(args.part)
    elif args.action == 'start':
        start_part(args.part)
    elif args.action == 'job':
        return run_job(args.part, args.kind, args.model)
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
