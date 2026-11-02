"""Optional local AI parts ("Bileşenler"): what each one is, the models it can use, this PC's hardware, what
is installed, and the jobs that download and install them.

    parts home   <installation>/Parts for an installed Orion; <source tree>/.local_parts when the Hub runs from
                 source (a development tree never writes into the installed Orion's folder)
      config.json                         per part: enabled, model, settings
      engines/<engine>-<version>/<hw>/    an engine (llama-server, tts-server, or a Python service's extra
                                          packages under site/), shared by the parts that use it
      models/<part>/<folder>/<files>      (under the installation's models location when it has one)
      jobs/<part>.json                    the running or last job; jobs/<part>.cancel asks it to stop
      run/<part>/                         process lifecycle state of the running part

Nothing is installed with Orion itself: the user installs a part when they want it (Ayarlar > Bileşenler, or
where a feature needs it). A job runs in its own process (`python -m services.shared.local_parts job ...`), so
closing the Orion window never stops a download, and finished files are kept while a cut one starts over (appending once gave corrupt models).
Installed parts start with the Hub (`keep_running`) and stop with it.

The catalog (local_parts.json) lists engine recipes per operating system and hardware: files shipped with Orion
(`bundled`), archives and Python wheels to download (with SHA-256 where known). A part whose engine has no
recipe for this system is shown but cannot be installed here yet. Two kinds of part run:
    llama    llama-server with the model (memory/embedding, local LLM)
    python   one of Orion's own services (STT, TTS) on the packaged Python, its engine's packages on the path
             (services/shared/part_python.py); no PyTorch anywhere
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import uuid
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path(__file__).with_name('local_parts.json')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
TERMINAL = {'done', 'error', 'cancelled', 'interrupted'}
READY_TIMEOUT = 300           # a big model loads for minutes on a slow disk
HARDWARE_TTL = 600
HARDWARE_LABELS = {'cuda': 'Ekran kartı (NVIDIA CUDA)', 'vulkan': 'Ekran kartı (Vulkan)',
                   'metal': 'Apple GPU (Metal)',
                   'cpu': 'İşlemci (CPU)'}
ALL_LAYERS = 999


class PartError(ValueError):
    """A refusal the user reads (Turkish)."""


class MissingFiles(PartError):
    """The model's files are not all there (nothing in the service log explains it)."""


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


def _python():
    return f'{sys.version_info.major}.{sys.version_info.minor}'


def engine_builds(engine, data=None, system_name=None):
    """{hardware: recipe} of this engine for this system (and, for Python packages, this Python), or None."""
    entry = (data or catalog())['engines'][engine]
    if 'pythons' in entry:
        entry = entry['pythons'].get(_python()) or {}
    return entry.get(system_name or system()) or None


def engine_dir(engine, hardware, data=None):
    entry = (data or catalog())['engines'][engine]
    # Python packages are built for one Python: each one gets its own folder.
    tag = '-py' + _python().replace('.', '') if 'pythons' in entry else ''
    return home() / 'engines' / f'{engine}-{entry["version"]}{tag}' / hardware


def engine_exe(engine, hardware, data=None):
    folder = engine_dir(engine, hardware, data)
    name = 'llama-server.exe' if os.name == 'nt' else 'llama-server'
    found = next(iter(sorted(folder.rglob(name))), None) if folder.is_dir() else None
    return found


def engine_recipe(engine, hardware, data=None):
    """How this engine is put together here: {'bundled', 'archives', 'wheels'}, or None (not on this system)."""
    return (engine_builds(engine, data) or {}).get(hardware)


def engine_ready(engine, hardware, data=None):
    folder = engine_dir(engine, hardware, data)
    return (folder / '.complete').is_file() or (engine == 'llama.cpp' and engine_exe(engine, hardware, data) is not None)


def python_fits(engine, data=None):
    """Python packages exist for the Pythons the catalog lists (the packaged Orion's, and the one a Hub run from
    source uses); another Python cannot install this part."""
    entry = (data or catalog())['engines'][engine]
    return 'pythons' not in entry or _python() in entry['pythons']


def engine_size(engine, hardware, data=None):
    """Bytes to download for the engine when every piece says its size, else None."""
    recipe = engine_recipe(engine, hardware, data) or {}
    pieces = recipe.get('archives', []) + recipe.get('wheels', [])
    return sum(piece['size'] for piece in pieces) if all('size' in piece for piece in pieces) else None


def model_files(part, model, settings=None):
    """The files `model` needs with these settings: its vision file (mmproj) only while vision is on."""
    folder = models_dir(part['id']) / model['folder']
    vision = model.get('mmproj') and (settings or {}).get('vision', True)
    return [folder / name for name in model['files'] + ([model['mmproj']] if vision else [])]


def model_mb(model, settings=None):
    """The model's size with these settings: its vision file counts only while vision is on."""
    vision = model.get('mmproj') and (settings or {}).get('vision', True)
    return model['size_mb'] + (model.get('mmproj_mb', 0) if vision else 0)


def missing_mb(part, model, settings=None):
    """What is left to download of the model (finished files count as there; a cut one starts over)."""
    files = model_files(part, model, settings)
    there = 0
    for path in files:
        if path.is_file():
            there += path.stat().st_size
    if all(path.is_file() for path in files):
        return 0
    return max(0, round(model_mb(model, settings) - there / 1048576))


def downloaded(part, model, settings=None):
    return all(path.is_file() for path in model_files(part, model, settings))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(path, expected, what):
    if expected and sha256(path) != expected:
        Path(path).unlink(missing_ok=True)
        raise PartError(f'{what} bozuk indi (SHA-256 tutmadı); yeniden dene.')


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
        return _labelled(_hardware['value'])
    # Every Hub process asks; the cards do not wait for nvidia-smi and PowerShell each time (seconds).
    saved = None if refresh else _read_json(home() / 'hardware.json', None)
    if saved and 0 <= time.time() - saved.get('at', 0) < HARDWARE_TTL and isinstance(saved.get('value'), dict):
        _hardware.update(at=time.monotonic(), value=saved['value'])
        return _labelled(saved['value'])
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
    try:
        _write_json(home() / 'hardware.json', {'at': time.time(), 'value': value})
    except OSError:
        pass
    return _labelled(value)


def _labelled(value):
    """Today's labels on cached facts (an old cache once kept an old wording for ten minutes)."""
    return {**value, 'choices': [{'id': choice['id'], 'label': HARDWARE_LABELS.get(choice['id'], choice['id'])}
                                 for choice in value.get('choices', [])]}


# --- Settings ------------------------------------------------------------------------------------------------

COMPUTE_TYPES = [{'id': 'int8_float16', 'label': 'int8 + float16 (önerilen)'},
                 {'id': 'int8', 'label': 'int8 (en az bellek)'}, {'id': 'float16', 'label': 'float16 (en doğru, çok bellek)'}]


def choices(part, hw):
    """The ways this part can run on this PC, best first."""
    allowed = part.get('hardware') or [choice['id'] for choice in hw['choices']]
    return [choice for choice in hw['choices'] if choice['id'] in allowed] or [{'id': 'cpu', 'label': HARDWARE_LABELS['cpu']}]


def default_settings(part, model, hw):
    """What a part runs with unless the user changes it (Gelişmiş ayarlar)."""
    hardware_id = choices(part, hw)[0]['id']
    if part.get('runner') == 'python':
        settings = {'hardware': hardware_id, 'gpu': 'all'}
        if part['id'] == 'stt':
            settings['compute_type'] = 'int8' if hardware_id == 'cpu' else 'int8_float16'
        return settings
    settings = {'hardware': hardware_id, 'gpu': 'all', 'context_size': part.get('context_size', 8192), 'parallel': 1}
    if any(item.get('mmproj') for item in part.get('models', [])):
        settings['vision'] = True
    return settings


CONTEXT_FALLBACK = 131072
_contexts = {}


def gguf_context(path):
    """The context length a GGUF model was trained for (its `<arch>.context_length`), read from the file's
    header; None when the file says nothing. llama.cpp accepts a larger -c, but past this the model loses the
    thread (and the memory is spent for nothing), so the setting stops here."""
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key in _contexts:
        return _contexts[key]
    formats = {0: '<B', 1: '<b', 2: '<H', 3: '<h', 4: '<I', 5: '<i', 6: '<f', 7: '<?', 10: '<Q', 11: '<q', 12: '<d'}
    found = None
    try:
        with path.open('rb') as stream:
            def read(size):
                data = stream.read(size)
                if len(data) < size:
                    raise EOFError
                return data

            def string():
                return read(struct.unpack('<Q', read(8))[0]).decode('utf-8', 'replace')

            def value(kind):
                if kind == 8:
                    return string()
                if kind == 9:
                    inner, count = struct.unpack('<IQ', read(12))
                    for _ in range(count):
                        value(inner)
                    return None
                return struct.unpack(formats[kind], read(struct.calcsize(formats[kind])))[0]

            if read(4) == b'GGUF':
                read(4)
                _, count = struct.unpack('<QQ', read(16))
                for _ in range(min(count, 10000)):
                    name = string()
                    result = value(struct.unpack('<I', read(4))[0])
                    if name.endswith('.context_length') and isinstance(result, int) and result > 0:
                        found = result
                        break
    except (OSError, EOFError, KeyError, struct.error):
        found = None
    _contexts[key] = found
    return found


def context_max(part, model):
    """The model's own limit: from its file once downloaded, else the catalog's, else a general ceiling."""
    if model is None:
        return CONTEXT_FALLBACK
    first = models_dir(part['id']) / model['folder'] / model['files'][0]
    return gguf_context(first) or model.get('context_max') or CONTEXT_FALLBACK


def advanced(part, hw, model=None):
    """The advanced settings a card shows (all have a default)."""
    fields = [{'key': 'hardware', 'label': 'Nerede çalışsın', 'type': 'choice', 'options': choices(part, hw)}]
    # Only NVIDIA cards are numbered for CUDA (CUDA_VISIBLE_DEVICES); the field shows when there is a choice.
    nvidia = [gpu for gpu in hw['gpus'] if gpu.get('vendor') == 'nvidia']
    if len(nvidia) > 1:
        fields.append({'key': 'gpu', 'label': 'Hangi ekran kartı', 'type': 'choice',
                       'options': [{'id': 'all', 'label': 'Hepsi'}] + [
                           {'id': str(gpu['index']), 'label': f"{gpu['index']}: {gpu['name']}"} for gpu in nvidia]})
    if part.get('runner') == 'python':
        if part['id'] == 'stt':
            fields.append({'key': 'compute_type', 'label': 'Hesaplama hassasiyeti', 'type': 'choice',
                           'options': COMPUTE_TYPES, 'hint': 'İşlemcide her zaman int8 kullanılır.'})
        return fields
    # No layer count: on a card the whole model goes to it, on the processor none can (command()).
    fields += [
        {'key': 'context_size', 'label': 'Bağlam boyutu (token)', 'type': 'int', 'min': 512,
         'max': context_max(part, model),
         'hint': 'Bir seferde ne kadar metin görebilir; üst sınır seçili modelin kendi sınırı. '
                 'Büyüdükçe daha çok bellek ister.'},
        {'key': 'parallel', 'label': 'Aynı anda istek', 'type': 'int', 'min': 1, 'max': 8,
         'hint': 'Kaç isteğe aynı anda yanıt verir. Her biri bellek ister.'},
    ]
    if any(item.get('mmproj') for item in part.get('models', [])):
        # Shown on the model's tile (model panel), kept with the other settings.
        fields.append({'key': 'vision', 'label': 'Görsel desteği (mmproj)', 'type': 'bool'})
    return fields


def clean_settings(part, values, hw, model=None):
    """Only known keys, inside their limits (the context: the model's own); anything else is refused in Turkish."""
    fields = {field['key']: field for field in advanced(part, hw, model)}
    result = {}
    for key, value in (values or {}).items():
        field = fields.get(key)
        if field is None:
            raise PartError(f'Bilinmeyen ayar: {key}')
        if field['type'] == 'bool':
            if not isinstance(value, bool):
                raise PartError(f'{field["label"]} açık ya da kapalı olmalı.')
            result[key] = value
        elif field['type'] == 'choice':
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
    settings.pop('gpu_layers', None)  # An older setting: the whole model goes to the card now.
    if 'context_size' in settings:
        # Never past what the model was trained for (a context saved for another model, or the default).
        settings['context_size'] = min(int(settings['context_size']), context_max(part, model))
    if settings['hardware'] not in {choice['id'] for choice in choices(part, hw)}:
        settings['hardware'] = choices(part, hw)[0]['id']  # A card that is gone, or a config copied from another PC.
    return model, settings


def command(part, model, settings, data=None):
    """The argv, environment and port for `part` running `model` with `settings`."""
    if part.get('runner') == 'python':
        return _python_command(part, model, settings, data)
    from .service_access import bind_host, own_origins
    exe = engine_exe(part['engine'], settings['hardware'], data)
    if exe is None:
        raise PartError('Motor kurulu değil.')
    files = model_files(part, model, settings)
    port = str(os.getenv(part['port_key']) or part['port'])
    argv = [str(exe), '-m', str(files[0]), '--port', port, '--host', bind_host(), '--cors-origins', own_origins(port),
            '--no-slots', '-c', str(settings['context_size']), '-np', str(settings['parallel']),
            '-ngl', str(0 if settings['hardware'] == 'cpu' else ALL_LAYERS)]
    if model.get('mmproj') and len(files) > len(model['files']):
        argv += ['--mmproj', str(files[-1])]
    argv += list(part.get('args', [])) + list(model.get('args', []))
    env = dict(os.environ)
    if settings.get('gpu', 'all') != 'all':
        env['CUDA_VISIBLE_DEVICES' if settings['hardware'] == 'cuda' else 'GGML_VK_VISIBLE_DEVICES'] = settings['gpu']
    return argv, env, int(port)


def _python_command(part, model, settings, data=None):
    """One of Orion's own services on the packaged Python, with its engine's packages and libraries."""
    folder = engine_dir(part['engine'], settings['hardware'], data)
    if not engine_ready(part['engine'], settings['hardware'], data):
        raise PartError('Motor kurulu değil.')
    port = str(os.getenv(part['port_key']) or part['port'])
    gpu = settings['hardware'] != 'cpu'
    values = {'model_folder': model['folder'], 'model_dir': str(models_dir(part['id']) / model['folder']),
              'engine_dir': str(folder), 'compute_type': settings.get('compute_type', 'int8') if gpu else 'int8'}
    site = folder / 'site'
    argv = [sys.executable, '-B', '-m', 'services.shared.part_python', str(ROOT / part['script']),
            str(site) if site.is_dir() else '']
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8', ORION_HW_ID='nvidia' if gpu else 'cpu',
               ORION_NATIVE_MODELS_DIR=str(models_dir(part['id'])), ORION_PART=part['id'])
    env[part['port_key']] = port
    env.update({key: value.format(**values) for key, value in part.get('env', {}).items()})
    if gpu and settings.get('gpu', 'all') != 'all':
        env['CUDA_VISIBLE_DEVICES'] = settings['gpu']
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
                 'Yeniden başlatınca tam inen dosyalar kalır, yarım kalan baştan iner.'}
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
    builds = engine_builds(part['engine'], data, hw['system'])
    model, settings = effective(part, hw, config)
    entry = config.get(part['id'], {})
    current = job(part['id'])
    ready = engine_ready(part['engine'], settings['hardware'], data)
    engine_bytes = None if ready else engine_size(part['engine'], settings['hardware'], data)
    change = transition(part['id'])
    moving = change and not change.get('error')
    if current and current.get('phase') not in TERMINAL:
        state = 'working'
    elif moving and change['to'] == 'stopped' and running:
        state = 'stopping'
    elif moving and change['to'] == 'running' and not running:
        state = 'starting'
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
        partial = sum(p.with_name(p.name + '.part').stat().st_size for p in model_files(part, item, settings)
                      if p.with_name(p.name + '.part').is_file())
        vision_file = models_dir(part['id']) / item['folder'] / item['mmproj'] if item.get('mmproj') else None
        main_there = all((models_dir(part['id']) / item['folder'] / name).is_file() for name in item['files'])
        models.append({'id': item['id'], 'name': item['name'], 'description': item['description'],
                       'size_mb': model_mb(item, settings), 'mmproj_mb': item.get('mmproj_mb'),
                       'base_mb': item['size_mb'], 'main_downloaded': main_there,
                       'extra_name': item.get('mmproj_name'), 'extra_description': item.get('mmproj_description'),
                       'recommended': bool(item.get('recommended')),
                       'downloaded': downloaded(part, item, settings), 'partial_mb': round(partial / 1048576, 1),
                       'mmproj': bool(item.get('mmproj')),
                       'vision_downloaded': bool(vision_file and vision_file.is_file())})
    note = None if builds else 'Bu işletim sistemi için kurulum henüz yok (şimdilik yalnızca Windows).'
    if not python_fits(part['engine'], data):
        known = ', '.join(data['engines'][part['engine']]['pythons'])
        note = f'Bu parçanın paketleri Python {known} için var; bu Hub Python {_python()} ile çalışıyor.'
    return {**base, 'installable': bool(builds and settings['hardware'] in builds),
            'note': note,
            'state': state, 'enabled': bool(entry.get('enabled')), 'paused': bool(entry.get('paused')), 'model': model['id'] if model else None,
            'engine_ready': ready, 'engine_mb': round(engine_bytes / 1048576) if engine_bytes else None,
            # Per way of running: the page switches hardware and shows the engine left to fetch at once.
            'engines': {choice['id']: {'ready': engine_ready(part['engine'], choice['id'], data),
                                       'mb': round((engine_size(part['engine'], choice['id'], data) or 0) / 1048576)}
                        for choice in choices(part, hw)},
            # One number for the button: everything "Modeli indir ve kur" fetches (engine and model, what is left).
            'download_mb': ((missing_mb(part, model, settings) if model else 0)
                            + (round(engine_bytes / 1048576) if engine_bytes else 0)), 'models': models, 'settings': settings, 'advanced': advanced(part, hw, model),
            'job': current, 'error': (change or {}).get('error')}


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
    if not python_fits(part['engine'], data):
        raise PartError('Bu parçanın paketleri bu Python sürümü için yok.')
    if not engine_builds(part['engine'], data, hw['system']):
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
            values['settings'] = {**saved, **clean_settings(part, settings, hw, chosen)}
        write_part_config(part_id, **values)
    elif action != 'download':
        raise PartError('Bilinmeyen iş.')
    (home() / 'jobs' / f'{part_id}.cancel').unlink(missing_ok=True)
    if action == 'install':
        (run_dir(part_id) / 'transition.json').unlink(missing_ok=True)
    _write_json(home() / 'jobs' / f'{part_id}.json', {'action': action, 'model': chosen['id'], 'phase': 'queued',
                                                       'label': 'Başlıyor…', 'started': time.time(), 'pid': None})
    process = _spawn(['job', part_id, action, chosen['id']])
    _update_job(part_id, pid=process.pid)
    return job(part_id)


def cancel(part_id):
    if busy(part_id):
        (home() / 'jobs' / f'{part_id}.cancel').touch()


def select(part_id, *, model=None, settings=None):
    """Save the chosen model and settings; a running part restarts with them (in the background: the answer
    does not wait for it)."""
    data = catalog()
    part = spec(part_id, data)
    hw = hardware()
    values = {}
    if model is not None:
        values['model'] = model_spec(part, model)['id']
    if settings is not None:
        target = model_spec(part, values['model']) if 'model' in values else effective(part, hw)[0]
        values['settings'] = {**(read_config().get(part_id, {}).get('settings') or {}),
                              **clean_settings(part, settings, hw, target)}
    entry = write_part_config(part_id, **values)
    current, settings = effective(part, hw)
    # A running part restarts only when its new files are all there (vision turned on before its file came:
    # it keeps running without it until "Modeli indir ve kur" fetches it).
    if (entry.get('enabled') and not entry.get('paused') and not busy(part_id) and current
            and downloaded(part, current, settings)):
        _move(part_id, 'running', restart=True)
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


def remove(part_id, *, delete_models=False):
    """Turn the part off and stop it (in the background); with `delete_models` its downloaded models go too,
    once the service has let go of them. The engine stays (another part may use it)."""
    if busy(part_id):
        raise PartError('Bu bileşende bir iş sürüyor; önce iptal et.')
    write_part_config(part_id, enabled=False, paused=False, purge=bool(delete_models))
    _move(part_id, 'stopped')


def stop(part_id):
    """Stop a part but keep it installed: it stays off (also after a restart) until Başlat."""
    if busy(part_id):
        raise PartError('Bu bileşende bir iş sürüyor; önce iptal et.')
    if not read_config().get(part_id, {}).get('enabled'):
        raise PartError('Bileşen kurulu değil.')
    write_part_config(part_id, paused=True)
    _move(part_id, 'stopped')


def start(part_id):
    part = spec(part_id)
    entry = read_config().get(part_id, {})
    if not entry.get('enabled'):
        raise PartError('Bileşen kurulu değil.')
    model, settings = effective(part, hardware())
    if model is None or not downloaded(part, model, settings):
        raise PartError('Modelin dosyaları eksik; Modeli indir ve kur ile tamamla.')
    write_part_config(part_id, paused=False)
    _move(part_id, 'running')


# --- Moving between running and stopped ----------------------------------------------------------------------
# Starting loads a model (up to minutes) and stopping waits for a clean exit: neither happens inside a request.
# The wish is written first (so every status says "starting"/"stopping" from the first moment, and the button
# never flips back), then a process of its own does it and records an error the card shows.

TRANSITION_TTL = READY_TIMEOUT + 60


def transition(part_id):
    value = _read_json(run_dir(part_id) / 'transition.json', None)
    if not value:
        return None
    if value.get('error'):
        return value
    if time.time() - value.get('at', 0) > TRANSITION_TTL or not _alive(value.get('pid')):
        return None  # Done long ago, or its process is gone: the state alone tells.
    return value


def _move(part_id, to, *, restart=False):
    folder = run_dir(part_id)
    folder.mkdir(parents=True, exist_ok=True)
    wish = uuid.uuid4().hex
    _write_json(folder / 'transition.json', {'id': wish, 'to': to, 'at': time.time(), 'pid': None, 'error': None})
    process = _spawn(['apply', part_id, '--id', wish] + (['--restart'] if restart else []))
    path = folder / 'transition.json'
    current = _read_json(path, {})
    if current.get('id') == wish and not current.get('pid'):
        _write_json(path, {**current, 'pid': process.pid})


APPLY_WAIT = READY_TIMEOUT + 120


def apply(part_id, *, wish=None, restart=False):
    """Bring the part where its configuration says: running when installed and not stopped, else stopped.

    One at a time per part (a lock), and only the latest wish: an older one that waited behind a newer click
    does nothing, and never writes its outcome over the newer one's (a restart from a setting change once
    started a model while "Kaldır + modelleri sil" was deleting it, and its error stayed on the card)."""
    from .process_lifecycle import InstanceLock, LifecycleError
    folder = run_dir(part_id)
    path = folder / 'transition.json'
    mine = lambda: wish is None or _read_json(path, {}).get('id') == wish
    try:
        lock = InstanceLock(folder, 'apply', timeout=APPLY_WAIT)
        lock.__enter__()
    except LifecycleError:
        return 1
    try:
        if not mine():
            return 0
        _write_json(path, {**_read_json(path, {}), 'pid': os.getpid()})
        part = spec(part_id)
        entry = read_config().get(part_id, {})
        wanted = 'running' if entry.get('enabled') and not entry.get('paused') else 'stopped'
        try:
            if wanted == 'stopped' or restart:
                stop_part(part_id)
            if wanted == 'stopped' and entry.get('purge'):
                shutil.rmtree(models_dir(part_id), ignore_errors=True)
                write_part_config(part_id, purge=False)
            if wanted == 'running':
                model, settings = effective(part, hardware())
                if model is None or not downloaded(part, model, settings):
                    raise MissingFiles('Modelin dosyaları eksik; Modeli indir ve kur ile tamamla')
                start_part(part_id)
        except Exception as error:
            log = folder / 'service.log'
            tail = ''
            try:
                lines = [line for line in log.read_text(encoding='utf-8', errors='replace').splitlines() if line.strip()]
                tail = ' Son satır: ' + lines[-1][-300:] if lines and not isinstance(error, MissingFiles) else ''
            except OSError:
                pass
            if mine():
                _write_json(path, {'id': wish, 'to': wanted, 'at': time.time(), 'pid': None,
                                   'error': f'{"Başlatılamadı" if wanted == "running" else "Durdurulamadı"}: {error}.{tail}'})
            return 1
        if mine():
            path.unlink(missing_ok=True)
        return 0
    finally:
        lock.__exit__(None, None, None)


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
        entry = config.get(part['id'], {})
        if (not part.get('engine') or not entry.get('enabled') or entry.get('paused') or busy(part['id'])
                or transition(part['id'])):
            continue
        model, settings = effective(part, hw, config)
        if model is None or not downloaded(part, model, settings) or not engine_ready(part['engine'], settings['hardware'], data):
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
        cwd = run_dir(part_id) if part.get('runner') == 'python' else Path(argv[0]).parent
        # The service's own output (why it did not start) is kept next to its state.
        with (run_dir(part_id) / 'service.log').open('ab') as log:
            child = runtime.launch('engine', argv, cwd=str(cwd), env=env, creationflags=NO_WINDOW,
                                   stdout=log, stderr=subprocess.STDOUT)
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
    """`url` into `target`; `report(bytes, total)` as it goes.

    Whole files only: a finished file is kept and never fetched again, but a cut one starts over. Appending
    to a cut file once gave corrupt models, and most models here have no SHA-256 that would catch that."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        # Finished on an earlier try (only a complete download is renamed from .part): never fetched again;
        # the caller still checks its SHA-256 where the catalog has one.
        report(target.stat().st_size, target.stat().st_size)
        return
    partial = target.with_name(target.name + '.part')
    partial.unlink(missing_ok=True)
    opener = opener or urllib.request.build_opener()
    try:
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'Orion'}), timeout=30) as response:
            length = response.headers.get('Content-Length')
            total = int(length) if length and length.isdigit() else None
            done = 0
            with partial.open('wb') as out:
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
            raise OSError('İndirme yarıda kesildi; yeniden dene (bu dosya baştan iner).')
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)  # Never left to look like a resumable piece.
        raise


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


def _remote_size(urls):
    """The bytes all `urls` hold, asked before downloading (None when a server does not say)."""
    total = 0
    opener = urllib.request.build_opener()
    for url in urls:
        try:
            with opener.open(urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Orion'}), timeout=15) as reply:
                length = reply.headers.get('Content-Length')
        except (OSError, ValueError):
            return None
        if not (length and length.isdigit()):
            return None
        total += int(length)
    return total


def _install_engine(part_id, part, settings, data, cancelled, step=''):
    """Put the engine together in a scratch folder (Orion's bundled files, archives, wheels), then move it in."""
    hardware_id = settings['hardware']
    if engine_ready(part['engine'], hardware_id, data):
        return
    recipe = engine_recipe(part['engine'], hardware_id, data)
    if not recipe:
        raise PartError('Bu donanım için motor yok.')
    final = engine_dir(part['engine'], hardware_id, data)
    (home() / 'engines').mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='unpack-', dir=str(home() / 'engines')))
    try:
        unpacked = work / 'unpacked'
        unpacked.mkdir()
        if recipe.get('bundled'):
            shutil.copytree(ROOT / recipe['bundled'], unpacked, dirs_exist_ok=True)
        pieces = [(piece, 'archive') for piece in recipe.get('archives', [])] + [(piece, 'wheel') for piece in recipe.get('wheels', [])]
        # One total for every piece: the catalog's sizes, else what the servers say (a total of one file only
        # made "616 / 373 MB").
        total = engine_size(part['engine'], hardware_id, data) or _remote_size([piece['url'] for piece, _ in pieces])
        offset = 0
        for number, (piece, kind) in enumerate(pieces, 1):
            label = f'{step}Motor indiriliyor ({part["engine"]}, parça {number}/{len(pieces)})'
            _update_job(part_id, phase='engine', label=label, bytes=offset, total=total)
            archive = home() / 'engines' / 'downloads' / piece['url'].rsplit('/', 1)[-1]
            # Without a known total each piece shows its own share.
            download(piece['url'], archive, report=_Progress(part_id, label, offset if total else 0, total),
                     cancelled=cancelled)
            verify(archive, piece.get('sha256'), 'Motor dosyası')
            offset += archive.stat().st_size
            _update_job(part_id, phase='engine', label='Motor açılıyor…', rate=0)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(unpacked / 'site' if kind == 'wheel' else unpacked)
        if part.get('runner', 'llama') == 'llama' and not any(unpacked.rglob('llama-server.exe' if os.name == 'nt' else 'llama-server')):
            raise PartError('İndirilen motorda llama-server yok.')
        (unpacked / '.complete').write_text(data['engines'][part['engine']]['version'], encoding='utf-8')
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            shutil.rmtree(final)
        os.replace(unpacked, final)
        shutil.rmtree(home() / 'engines' / 'downloads', ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fetch_model(part_id, part, model, cancelled, settings=None, step=''):
    files = model_files(part, model, settings)
    if all(path.is_file() for path in files):
        return
    estimate = int(model_mb(model, settings) * 1048576)
    label = f'{step}Model indiriliyor: {model["name"]}'
    if model.get('archive'):
        archive = models_dir(part_id) / '.downloads' / model['archive'].rsplit('/', 1)[-1]
        _update_job(part_id, phase='model', label=label, total=estimate)
        download(model['archive'], archive, report=_Progress(part_id, label, 0, estimate), cancelled=cancelled)
        verify(archive, model.get('archive_sha256'), 'Model')
        _update_job(part_id, phase='model', label='Model açılıyor…', rate=0)
        folder = files[0].parent
        work = Path(tempfile.mkdtemp(prefix='unpack-', dir=str(archive.parent)))
        try:
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(work)
            # The zip holds the model's files, at its root or in one folder.
            source = next((path.parent for path in work.rglob(model['files'][0])), None)
            if source is None:
                raise PartError('İndirilen modelde beklenen dosyalar yok.')
            if folder.exists():
                shutil.rmtree(folder)
            folder.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, folder)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            archive.unlink(missing_ok=True)
        return
    offset = sum(path.stat().st_size for path in files if path.is_file())
    for path in [path for path in files if not path.is_file()]:
        _update_job(part_id, phase='model', label=label, total=estimate)
        download(model['url'] + path.name, path, report=_Progress(part_id, label, offset, estimate), cancelled=cancelled)
        verify(path, (model.get('sha256') or {}).get(path.name), 'Model')
        offset += path.stat().st_size


def run_job(part_id, action, model_id):
    data = catalog()
    part = spec(part_id, data)
    model = model_spec(part, model_id)
    marker = home() / 'jobs' / f'{part_id}.cancel'
    cancelled = marker.is_file
    _update_job(part_id, pid=os.getpid(), phase='starting', label='Hazırlanıyor…', error=None)
    try:
        _, settings = effective(part, hardware(), read_config())
        # Steps counted from what this job really does: the engine may be there already (another part uses it).
        # Only the downloads are numbered ("1/2 · ..."); starting comes after them as "Kuruluyor".
        steps = ([] if action != 'install' or engine_ready(part['engine'], settings['hardware'], data) else ['engine'])
        steps += [] if downloaded(part, model, settings) else ['model']
        step = lambda name: f'{steps.index(name) + 1}/{len(steps)} · ' if len(steps) > 1 and name in steps else ''
        if action == 'install':
            _install_engine(part_id, part, settings, data, cancelled, step('engine'))
        _fetch_model(part_id, part, model, cancelled, settings, step('model'))
        if action == 'install':
            write_part_config(part_id, enabled=True, paused=False, model=model_id)
            _update_job(part_id, phase='launching', label='Kuruluyor: servis başlatılıyor (model belleğe yükleniyor)…',
                        bytes=None, total=None, rate=0)
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
    apply_parser = sub.add_parser('apply')
    apply_parser.add_argument('part')
    apply_parser.add_argument('--restart', action='store_true')
    apply_parser.add_argument('--id')
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
    elif args.action == 'apply':
        return apply(args.part, wish=args.id, restart=args.restart)
    elif args.action == 'job':
        return run_job(args.part, args.kind, args.model)
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
