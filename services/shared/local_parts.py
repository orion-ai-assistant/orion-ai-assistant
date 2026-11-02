"""Optional local AI parts ("Bileşenler"): what each one is, the models it can use, this PC's hardware, what
is installed, and the jobs that download and install them.

    parts home   <installation>/Parts for an installed Orion; <source tree>/.local_parts when the Hub runs from
                 source (a development tree never writes into the installed Orion's folder)
      config.json                         per part: enabled, model, settings
      engines/<engine>-<version>/<hw>/    an engine (llama-server, tts-server, or a Python service's extra
                                          packages under site/), shared by the parts that use it
      models/<part>/<folder>/<files>      (the installation's models location's parts/ when it has one; the user
                                          can move it: `location`, services/shared/parts_location.py)
      models/<part>/custom/<name>/        the user's own models: a folder each, read from its files
      jobs/<part>.json                   the running or last job; jobs/<part>.cancel asks it to stop
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
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
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
    """Our parts and models, with the models the user put in each part's own folder after ours."""
    data = json.loads(CATALOG.read_text(encoding='utf-8'))
    for part in data['parts']:
        if part['id'] in USER_KINDS:
            part['models'] = part.get('models', []) + user_models(part)
    return data


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


def location():
    """(registry, place, folder): the models' place (what Ayarlar > Bileşenler shows and moves), the registry
    that says where it is, and the folder of the parts' models in it. An installed Orion's place is its
    installation's models location (the parts' models in its parts/ folder; registry None when the installation
    names none, and then it cannot move). A source tree's is its parts home's models/ until the user moves it;
    then a small registry of its own says where (it never writes into the installed Orion's)."""
    from .installation_registry import Registry
    record = None if os.getenv('ORION_PARTS_HOME') else _installed()
    if record is not None:
        place = (record.get('locations') or {}).get('models')
        return (Registry(), Path(place), Path(place) / 'parts') if place else (None, home() / 'models', home() / 'models')
    registry = Registry(home() / 'location' / 'install.json')
    try:
        place = ((registry.read() or {}).get('locations') or {}).get('models')
    except Exception:
        place = None
    place = Path(place) if place else home() / 'models'
    return registry, place, place


def models_dir(part_id):
    return location()[2] / part_id


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


def downloaded(part, model, settings=None):
    # A user's model is there once its folder holds every file its kind needs (none is still being copied).
    return model.get('complete', True) and all(path.is_file() for path in model_files(part, model, settings))


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
        # Vulkan first (the default): one build for every card, and on an RTX 3060 Ti it generated faster than
        # the CUDA build (about 100 against 60-70 tokens a second). CUDA stays a choice; speech has only CUDA.
        choices = (['vulkan'] if nvidia or others else []) + (['cuda'] if nvidia else []) + ['cpu']
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
_metas = {}
_GGUF_FORMATS = {0: '<B', 1: '<b', 2: '<H', 3: '<h', 4: '<I', 5: '<i', 6: '<f', 7: '<?', 10: '<Q', 11: '<q', 12: '<d'}


def gguf_meta(stream):
    """What a GGUF file says about itself, from its header (the first few KB: the tokenizer, which comes after
    what we need, is never read): architecture, context length it was trained for, vector size, how an
    embedding model pools (None: not said), and for an mmproj file whether it understands images and/or sound.
    None when it is not a GGUF file."""
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
        return struct.unpack(_GGUF_FORMATS[kind], read(struct.calcsize(_GGUF_FORMATS[kind])))[0]

    if read(4) != b'GGUF':
        return None
    read(4)
    _, count = struct.unpack('<QQ', read(16))
    found = {}
    for _ in range(min(count, 10000)):
        name = string()
        arch = found.get('general.architecture')
        if name.startswith('tokenizer.') and arch and f'{arch}.context_length' in found:
            break  # All we need is above: the tokenizer's long lists that follow are never read.
        found[name] = value(struct.unpack('<I', read(4))[0])
    arch = found.get('general.architecture')
    number = lambda key: found.get(f'{arch}.{key}') if isinstance(found.get(f'{arch}.{key}'), int) else None
    return {'architecture': arch, 'context': number('context_length'), 'dims': number('embedding_length'),
            'pooling': number('pooling_type'),
            'vision': bool(found.get('clip.has_vision_encoder')), 'audio': bool(found.get('clip.has_audio_encoder'))}


def file_meta(path):
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key not in _metas:
        try:
            with path.open('rb') as stream:
                _metas[key] = gguf_meta(stream)
        except (OSError, EOFError, KeyError, struct.error, UnicodeDecodeError):
            _metas[key] = None
    return _metas[key]


HEADER_RETRY = 3600
_fetching = threading.Lock()


def remote_meta(url, *, opener=None):
    """The header of a GGUF file on the internet: a few KB of it (a range request), not the file."""
    import io
    opener = opener or urllib.request.build_opener()
    for size in (65536, 4 << 20):
        request = urllib.request.Request(url, headers={'Range': f'bytes=0-{size - 1}', 'User-Agent': 'Orion'})
        with opener.open(request, timeout=20) as response:
            data = response.read(size)
        try:
            return gguf_meta(io.BytesIO(data))
        except EOFError:
            continue
    return None


def _gguf_urls(data):
    return [model['url'] + name for part in data['parts'] for model in part.get('models', [])
            if model.get('url') for name in model['files'] + ([model['mmproj']] if model.get('mmproj') else [])
            if name.endswith('.gguf')]


def _fetch_headers(urls):
    with _fetching:
        path = home() / 'headers.json'
        for url in urls:
            saved = _read_json(path, {})
            try:
                saved[url] = {'meta': remote_meta(url), 'at': time.time()}
            except Exception:
                saved[url] = {'failed': True, 'at': time.time()}
            _write_json(path, saved)


def prefetch_headers(data):
    """Read (once, in the background) the headers of the catalog's models that are not downloaded, so a card
    knows a model's limits before it comes. The catalog says only where a model is and how big; the rest comes
    from the model itself, as for a model the user adds."""
    saved = _read_json(home() / 'headers.json', {})
    wanted = [url for url in _gguf_urls(data) if url not in saved
              or (saved[url].get('failed') and time.time() - saved[url].get('at', 0) > HEADER_RETRY)]
    if wanted and not _fetching.locked():
        threading.Thread(target=_fetch_headers, args=(wanted,), daemon=True).start()


def model_meta(part, model, name=None):
    """What the model's file says (`name`: one of its files, the first by default): the downloaded file, else
    its header read from the internet, else None."""
    name = name or next(iter(model['files']), None)
    if name is None:
        return None  # A user's model whose file has not come yet.
    meta = file_meta(models_dir(part['id']) / model['folder'] / name) if name.endswith('.gguf') else None
    if meta is None and model.get('url'):
        meta = (_read_json(home() / 'headers.json', {}).get(model['url'] + name) or {}).get('meta')
    return meta


def context_max(part, model):
    """The model's own limit (from its file); a general ceiling while that is not known yet."""
    if model is None:
        return CONTEXT_FALLBACK
    return (model_meta(part, model) or {}).get('context') or CONTEXT_FALLBACK


EXTRAS = {(True, True): ('Görsel ve ses anlama', 'Resimleri ve sesleri anlar.'),
          (True, False): ('Görsel anlama', 'Resimleri anlar.'),
          (False, True): ('Ses anlama', 'Sesleri anlar.')}


def extra_info(part, model):
    """The name of a model's extra file (mmproj) from what it understands, as the file says."""
    meta = model_meta(part, model, model['mmproj']) or {}
    name, text = EXTRAS.get((meta.get('vision', False), meta.get('audio', False)), ('Görsel anlama', 'Resimleri anlar.'))
    return name, f'{text} (mmproj dosyası)'


# --- The user's own models -----------------------------------------------------------------------------------
# Ours come from the catalog; the user's are folders under models/<part>/custom/, one per model, named by the
# user (a part with several engines, such as TTS, has a folder per engine: custom/OmniVoice/<name>). The user
# picks the engine; which file is which, the context limit and the rest are read from the files' contents,
# never from their names, so a folder filled by hand works as well as one made from the page.

USER_DIR = 'custom'
MB = 1048576

# What each engine needs from a model folder, in the order the service takes the files. `names`: one of these
# exact files (faster-whisper loads them by name); `gguf`: a GGUF file whose header says this architecture ('*':
# any model, not an mmproj or a sibling's). A kind whose engine Orion does not have yet (`engine` other than the
# part's) is shown and cannot be added.
USER_KINDS = {
    'llm': [{'id': 'gguf', 'label': 'GGUF (llama.cpp)', 'engine': 'llama.cpp', 'files': [
        {'gguf': '*', 'label': 'Model dosyası (.gguf)'},
        {'gguf': 'clip', 'role': 'mmproj', 'optional': True, 'label': 'Ek dosya (mmproj, .gguf)',
         'hint': 'Resim ve ses anlamak için.'}]}],
    'embedding': [{'id': 'gguf', 'label': 'GGUF (llama.cpp)', 'engine': 'llama.cpp', 'files': [
        {'gguf': '*', 'label': 'Embedding modeli (.gguf)'}]}],
    # As the models people download have them: Systran's small and medium (older conversions, ours too) bring
    # vocabulary.txt and no preprocessor_config.json; large-v3 and turbo bring vocabulary.json and need
    # preprocessor_config.json (128 bands: without it the audio is prepared with 80 and refused). So either
    # vocabulary name, and preprocessor_config.json only for a model that hears more than 80 bands (read from
    # model.bin). tokenizer.json always: nothing is fetched from the internet in place of a missing file.
    'stt': [{'id': 'faster-whisper', 'label': 'faster-whisper (CTranslate2 Whisper)', 'engine': 'faster-whisper',
             'files': [
        {'names': ['model.bin'], 'label': 'model.bin', 'hint': 'Model'},
        {'names': ['config.json'], 'label': 'config.json', 'hint': 'Ayarlar'},
        {'names': ['tokenizer.json'], 'label': 'tokenizer.json', 'hint': 'Sözlük'},
        {'names': ['vocabulary.json', 'vocabulary.txt'], 'label': 'vocabulary.json / .txt', 'hint': 'Sözcük listesi'},
        {'names': ['preprocessor_config.json'], 'optional': True, 'label': 'preprocessor_config.json',
         'hint': 'Ses ayarları; large-v3 ve turbo için gerekli'}]}],
    'tts': [{'id': 'omnivoice', 'label': 'OmniVoice', 'folder': 'OmniVoice', 'engine': 'omnivoice-gguf', 'files': [
                {'gguf': 'omnivoice-lm', 'label': 'Ses modeli (.gguf)'},
                {'gguf': 'omnivoice-tokenizer', 'label': 'Ses çözücü (.gguf)'}]},
            {'id': 'voxcpm2', 'label': 'VoxCPM2', 'folder': 'VoxCPM2', 'engine': 'voxcpm2', 'files': []}],
}
_SPLIT = re.compile(r'-(\d{5})-of-\d{5}\.gguf$')  # llama.cpp's own naming of a model split in pieces


def user_dir(part_id):
    return models_dir(part_id) / USER_DIR


def _slug(text):
    plain = text.translate(str.maketrans('çğıöşüÇĞİÖŞÜ', 'cgiosuCGIOSU')).lower()
    return '-'.join(re.findall(r'[a-z0-9]+', plain)) or 'model'


def _settled(path):
    """False while another program still writes the file (a copy in Explorer: a model is gigabytes)."""
    if os.name != 'nt':
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    # Read, letting others only read: refused exactly while someone has it open for writing (a running service
    # only reads its model, so its files count as settled).
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return ctypes.get_last_error() != 32  # ERROR_SHARING_VIOLATION
    kernel.CloseHandle(handle)
    return True


def ct2_meta(path):
    """What a CTranslate2 model file says about itself: its kind (`spec`, 'WhisperSpec' for Whisper) and, for
    Whisper, how many mel bands its audio input has (large-v3 and turbo 128, the older ones 80). Read from the
    header: the weights are skipped, not read."""
    path = Path(path)
    try:
        key = ('ct2', str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key not in _metas:
        _metas[key] = _ct2_header(path)
    return _metas[key]


def _ct2_header(path):
    try:
        with path.open('rb') as stream:
            def read(size):
                data = stream.read(size)
                if len(data) < size:
                    raise EOFError
                return data

            def string():
                return read(struct.unpack('<H', read(2))[0])[:-1].decode('utf-8', 'replace')

            version = struct.unpack('<I', read(4))[0]
            if not 1 <= version <= 100:
                return None
            spec = string() if version >= 2 else None
            if version >= 3:
                read(4)  # The spec's revision.
            for _ in range(min(struct.unpack('<I', read(4))[0], 100000)):
                name = string()
                rank = read(1)[0]
                dims = struct.unpack(f'<{rank}I', read(4 * rank))
                if version >= 4:
                    read(1)  # Its type.
                size = struct.unpack('<I', read(4))[0]
                if name == 'encoder/conv1/weight':  # [width, mel bands, kernel]
                    return {'spec': spec, 'mels': dims[1] if len(dims) == 3 else None}
                stream.seek(size, 1)
            return {'spec': spec, 'mels': None}
    except (OSError, EOFError, struct.error):
        return None


def _whisper(path, kind, checklist):
    """faster-whisper's own rules, from the files: model.bin must be a Whisper model, and the audio must be prepared
    with as many mel bands as it hears: preprocessor_config.json is needed past 80 (without it faster-whisper uses
    80, and a turbo model refuses that), and when present it must say the model's number."""
    items = {spec['names'][0]: item for spec, item in zip(kind['files'], checklist)}
    model, prepare = items['model.bin'], items['preprocessor_config.json']
    if model['state'] != 'ok':
        return
    meta = ct2_meta(path / 'model.bin')
    if not meta or meta['spec'] != 'WhisperSpec':
        model.update(state='wrong', hint='Whisper modeli değil.')
        return
    mels = meta['mels']
    if mels and mels != 80:
        prepare.update(optional=False, hint=f'Bu model {mels} bantlı: gerekli')
    if prepare['state'] == 'ok' and mels:
        try:
            said = json.loads((path / 'preprocessor_config.json').read_text(encoding='utf-8')).get('feature_size', 80)
        except (OSError, ValueError, AttributeError):
            said = None
        if said != mels:
            prepare.update(state='wrong', hint=f'{said} bant diyor, model {mels} bant istiyor.' if said
                           else 'Okunamadı.')


def _user_model(part, kind, path, model_id, folder):
    """One folder of the user's as a catalog entry like ours (files in the service's order, sizes, args), with
    a checklist of what its engine needs: each file there, missing, more than one candidate, or not right."""
    present = sorted(file for file in path.iterdir() if file.is_file())
    copying = [file.name for file in present if not _settled(file)]
    ggufs = [(file, file_meta(file)) for file in present if file.suffix.lower() == '.gguf' and file.name not in copying]
    claimed = {spec['gguf'] for spec in kind['files'] if spec.get('gguf', '*') != '*'} | {'clip'}
    problems = [f'{file.name}: okunamadı.' for file, meta in ggufs if meta is None]
    checklist, picked = [], []
    for spec in kind['files']:
        if 'names' in spec:
            matches = [path / name for name in spec['names'] if (path / name).is_file() and name not in copying][:1]
        else:
            matches = [file for file, meta in ggufs if meta and (
                meta['architecture'] == spec['gguf'] or spec['gguf'] == '*' and meta['architecture'] not in claimed)]
            parts = [file for file in matches if _SPLIT.search(file.name)]
            if parts and len(parts) == len(matches):
                matches = [min(parts, key=lambda file: file.name)]  # A split model loads from its first piece.
        item = {'label': spec['label'], 'optional': bool(spec.get('optional')), 'hint': spec.get('hint'),
                'role': spec.get('role'),
                'state': 'ok' if len(matches) == 1 else 'many' if matches else 'missing',
                'names': [file.name for file in matches]}
        if len(matches) == 1:
            item['mb'] = round(matches[0].stat().st_size / MB)
            meta = file_meta(matches[0]) if matches[0].suffix.lower() == '.gguf' else None
            if meta:
                item['architecture'], item['context'] = meta['architecture'], meta['context']
        checklist.append(item)
        picked.append(matches[0] if len(matches) == 1 else None)
    used = {name for item in checklist for name in item['names']}
    problems += [f'{file.name}: tanınmayan dosya ({meta["architecture"]}).'
                 for file, meta in ggufs if meta and file.name not in used]
    if kind['id'] == 'faster-whisper':
        _whisper(path, kind, checklist)
    ready = kind['engine'] == part.get('engine')
    if not ready:
        problems.append(f'{kind["label"]} motoru henüz yok.')
    complete = (ready and not copying
                and all(item['state'] == 'ok' or item['optional'] and item['state'] == 'missing' for item in checklist))
    picked = [file if item['state'] == 'ok' else None for file, item in zip(picked, checklist)]
    extra = next((file for spec, file in zip(kind['files'], picked) if spec.get('role') == 'mmproj' and file), None)
    files = [file for spec, file in zip(kind['files'], picked) if spec.get('role') != 'mmproj' and file]
    main = file_meta(files[0]) if files and files[0].suffix.lower() == '.gguf' else None
    # An embedding model that does not say how it pools is pooled like ours (the last token).
    args = ['--pooling', 'last'] if part['id'] == 'embedding' and main and main.get('pooling') is None else []
    entry = {'id': model_id, 'name': path.name, 'description': 'Kendi modelin.', 'folder': folder,
             'files': [file.name for file in files], 'size_mb': round(sum(file.stat().st_size for file in files) / MB),
             'args': args, 'user': True, 'kind': kind['id'], 'kind_label': kind['label'], 'complete': complete,
             'present': [file.name for file in present], 'checklist': checklist, 'copying': copying,
             'problems': problems}
    if extra:
        entry.update(mmproj=extra.name, mmproj_mb=round(extra.stat().st_size / MB))
    return entry


def _places(part_id):
    """(kind, folder, its path under the models folder) for each engine of the part: one folder for a part with
    one engine, one per engine otherwise."""
    kinds = USER_KINDS.get(part_id) or []
    if len(kinds) == 1:
        return [(kinds[0], user_dir(part_id), USER_DIR)]
    return [(kind, user_dir(part_id) / kind['folder'], f'{USER_DIR}/{kind["folder"]}') for kind in kinds]


def _user_id(part_id, kind, name):
    several = len(USER_KINDS.get(part_id) or []) > 1
    return f'{USER_DIR}-{kind["id"] + "-" if several else ""}{_slug(name)}'


def user_models(part):
    """The models the user put in this part's own folder, each a folder (its name is the model's)."""
    models, ids = [], set()
    for kind, folder, relative in _places(part['id']):
        if not folder.is_dir():
            continue
        for path in sorted((path for path in folder.iterdir() if path.is_dir()), key=lambda path: path.name.lower()):
            model_id = base = _user_id(part['id'], kind, path.name)
            number = 1
            while model_id in ids:
                number += 1
                model_id = f'{base}-{number}'
            ids.add(model_id)
            try:
                models.append(_user_model(part, kind, path, model_id, f'{relative}/{path.name}'))
            except OSError:
                continue  # Being renamed or deleted in Explorer just now.
    return models


def add_user_model(part_id, name, kind_id=None):
    """Make the folder for a model the user brings, for the engine they chose: it shows at once (empty) and
    fills as they copy its files in. Returns its id."""
    _not_moving()
    part = spec(part_id)
    places = _places(part_id)
    if not places:
        raise PartError('Bu bileşene kendi modelin eklenemiyor.')
    kind, folder, _ = places[0] if kind_id is None else next(
        (place for place in places if place[0]['id'] == kind_id), (None, None, None))
    if kind is None:
        raise PartError('Böyle bir motor yok.')
    if kind['engine'] != part.get('engine'):
        raise PartError(f'{kind["label"]} motoru henüz eklenmedi.')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', str(name or '')).strip().rstrip('. ')
    if not name:
        raise PartError('Modele bir ad ver.')
    if len(name) > 60:
        raise PartError('Ad en çok 60 harf olabilir.')
    if any(model['id'] == _user_id(part_id, kind, name) for model in part['models']):
        raise PartError('Bu adda bir model zaten var.')
    try:
        (folder / name).mkdir(parents=True)
    except OSError:
        raise PartError('Bu adla klasör açılamadı; başka bir ad dene.') from None
    return _user_id(part_id, kind, name)


def open_folder(part_id, model_id):
    """Show one of the user's model folders in this PC's file manager, in front of the Orion window."""
    _not_moving()
    model = model_spec(spec(part_id), model_id)
    if not model.get('user'):
        raise PartError('Yalnızca kendi modellerinin klasörü açılır.')
    folder = models_dir(part_id) / model['folder']
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.startfile(str(folder))
        threading.Thread(target=_to_front, args=(folder,), daemon=True).start()
    else:
        subprocess.Popen(['open' if system() == 'mac' else 'xdg-open', str(folder)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _to_front(folder, wait=3.0):
    """Explorer opened by the Hub (a program in the background) sometimes came up behind the Orion window:
    Windows lets only the program in front give the front away. Joining the front window's input for a moment
    lets this one bring the folder's window there."""
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32')
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(window, _):
        name, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(1024)
        user.GetClassNameW(window, name, 64)
        if name.value == 'CabinetWClass' and user.IsWindowVisible(window):
            user.GetWindowTextW(window, title, 1024)
            if title.value == folder.name or title.value.startswith(f'{folder.name} - ') or str(folder) in title.value:
                found.append(window)
        return True

    until = time.monotonic() + wait
    while not found and time.monotonic() < until:
        user.EnumWindows(each, 0)
        if not found:
            time.sleep(.1)
    if not found:
        return
    window = found[0]
    front = user.GetForegroundWindow()
    theirs = user.GetWindowThreadProcessId(front, None)
    mine = kernel.GetCurrentThreadId()
    joined = theirs and theirs != mine and user.AttachThreadInput(mine, theirs, True)
    try:
        if user.IsIconic(window):
            user.ShowWindow(window, 9)  # SW_RESTORE
        user.BringWindowToTop(window)
        user.SetForegroundWindow(window)
    finally:
        if joined:
            user.AttachThreadInput(mine, theirs, False)


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
    # `files`: the model's files in the order its kind lists them (a user's model names them its own way).
    values = {'model_folder': model['folder'], 'model_dir': str(models_dir(part['id']) / model['folder']),
              'files': [str(path) for path in model_files(part, model, settings)],
              'engine_dir': str(folder), 'compute_type': settings.get('compute_type', 'int8') if gpu else 'int8'}
    site = folder / 'site'
    argv = [sys.executable, '-B', '-m', 'services.shared.part_python', str(ROOT / part['script']),
            str(site) if site.is_dir() else '']
    # Offline: whatever the model lacks, nothing is fetched from the internet in its place (faster-whisper
    # would download a general dictionary, or a whole model for a name it does not find).
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8', ORION_HW_ID='nvidia' if gpu else 'cpu',
               ORION_NATIVE_MODELS_DIR=str(models_dir(part['id'])), ORION_PART=part['id'], HF_HUB_OFFLINE='1',
               TRANSFORMERS_OFFLINE='1')
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


def moving():
    """The models are being moved (services/shared/parts_location.py): nothing may start, download or delete
    until it is done (a file written meanwhile would be left behind, or fail the check of the copy)."""
    value = _read_json(home() / 'jobs' / 'move.json', None)
    if not value or value.get('phase') in TERMINAL:
        return False
    return _alive(value.get('pid')) or not value.get('pid') and time.time() - value.get('started', 0) < 30


def _not_moving():
    if moving():
        raise PartError('Modeller taşınıyor; bitince yeniden dene.')


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
    change = transition(part['id'])
    moving = change and not change.get('error')
    if moving and change['to'] == 'running' and change.get('restart'):
        state = 'restarting'
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
    # Facts only, each true whatever the user chooses next: every model's files (main and extra apart), every
    # hardware's engine, the model's own limits. What a choice makes of them (sizes with or without the extra
    # file, what is left to download, the context limit, "not applied yet") the page works out itself, so it
    # shows with the click and never waits for this answer.
    models = []
    for item in part['models']:
        folder = models_dir(part['id']) / item['folder']
        names = item['files'] + ([item['mmproj']] if item.get('mmproj') else [])
        partial = sum((folder / (name + '.part')).stat().st_size for name in names
                      if (folder / (name + '.part')).is_file())
        meta = model_meta(part, item) or {}
        extra = extra_info(part, item) if item.get('mmproj') else (None, None)
        row = {'id': item['id'], 'name': item['name'], 'description': item['description'],
               'recommended': bool(item.get('recommended')), 'user': bool(item.get('user')),
               'main_mb': item['size_mb'], 'partial_mb': round(partial / MB, 1),
               'main_there': item.get('complete', True) and all((folder / name).is_file() for name in item['files']),
               'extra': bool(item.get('mmproj')), 'extra_mb': item.get('mmproj_mb'),
               'extra_there': bool(item.get('mmproj')) and (folder / item.get('mmproj', '')).is_file(),
               'extra_name': extra[0], 'extra_description': extra[1],
               'context_max': (meta.get('context') or CONTEXT_FALLBACK) if part.get('runner') != 'python' else None,
               'dims': meta.get('dims') if part['id'] == 'embedding' else None}
        if item.get('user'):
            row.update(complete=item['complete'], checklist=item['checklist'], copying=item['copying'],
                       problems=item['problems'], path=str(folder))
        models.append(row)
    note = None if builds else 'Bu işletim sistemi için kurulum henüz yok (şimdilik yalnızca Windows).'
    if not python_fits(part['engine'], data):
        known = ', '.join(data['engines'][part['engine']]['pythons'])
        note = f'Bu parçanın paketleri Python {known} için var; bu Hub Python {_python()} ile çalışıyor.'
    kinds = USER_KINDS.get(part['id'])
    return {**base, 'installable': bool(builds and settings['hardware'] in builds),
            'note': note,
            'state': state, 'enabled': bool(entry.get('enabled')), 'paused': bool(entry.get('paused')),
            # The saved choice (what Başlat, Kur or Uygula uses).
            'model': model['id'] if model else None, 'settings': settings,
            'engines': {choice['id']: {'ready': engine_ready(part['engine'], choice['id'], data),
                                       'mb': round((engine_size(part['engine'], choice['id'], data) or 0) / MB)}
                        for choice in choices(part, hw)},
            'models': models, 'advanced': advanced(part, hw),
            'job': current, 'error': (change or {}).get('error'),
            # What the service runs with (the page compares the choice with it: "not applied yet").
            'applied': applied(part['id']) if state in ('running', 'restarting') else None,
            # The engines the user can bring a model for (one not in Orion yet is shown, not offered).
            'user': {'kinds': [{**kind, 'ready': kind['engine'] == part['engine']} for kind in kinds],
                     'folder': str(user_dir(part['id']))} if kinds else None}


def status():
    data = catalog()
    prefetch_headers(data)
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


def start_job(part_id, action, *, model=None, settings=None, fetch=None):
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
    _not_moving()
    current, _ = effective(part, hw)
    chosen = model_spec(part, model) if model else current
    if chosen is None:
        raise PartError('Önce bir model seç.')
    if chosen.get('user') and not chosen['complete']:
        raise PartError('Bu modelin klasöründe eksik dosya var; Modeller panelinde neyin eksik olduğu yazıyor.')
    if action == 'download' and chosen.get('user'):
        raise PartError('Kendi modelin indirilmez: dosyaları klasöründe.')
    fetch = [role for role in (fetch or []) if role in ('model', 'extra')] or None
    if fetch and 'extra' in fetch and not chosen.get('mmproj'):
        raise PartError('Bu modelin ek dosyası yok.')
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
    installed = bool(read_config().get(part_id, {}).get('enabled'))
    _write_json(home() / 'jobs' / f'{part_id}.json', {'action': action, 'model': chosen['id'], 'phase': 'queued',
                                                       'label': 'Başlıyor…', 'started': time.time(), 'pid': None,
                                                       'installed': installed})
    process = _spawn(['job', part_id, action, chosen['id']] + (['--fetch', ','.join(fetch)] if fetch else []))
    _update_job(part_id, pid=process.pid)
    return job(part_id)


def cancel(part_id):
    if busy(part_id):
        (home() / 'jobs' / f'{part_id}.cancel').touch()


def select(part_id, *, model=None, settings=None):
    """Save the chosen model and settings. Nothing restarts: a running part keeps what it runs until the user
    presses Uygula (`apply_choice`); a stopped one takes them when it starts, a new one when it is installed."""
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
    change = transition(part_id)
    if change and change.get('error'):
        (run_dir(part_id) / 'transition.json').unlink(missing_ok=True)  # About the old choice.
    return entry


def apply_choice(part_id):
    """Uygula: the saved choice replaces what runs. What it needs and is not here (another hardware's engine,
    the vision file, another model) is downloaded first; until then the old one keeps running."""
    part = spec(part_id)
    _not_moving()
    if busy(part_id):
        raise PartError('Bu bileşende bir iş sürüyor; bitince uygula.')
    if not read_config().get(part_id, {}).get('enabled'):
        raise PartError('Bileşen kurulu değil.')
    model, settings = effective(part, hardware())
    if model is None:
        raise PartError('Önce bir model seç.')
    if not ready(part, model, settings):
        return start_job(part_id, 'install')
    write_part_config(part_id, paused=False)
    _move(part_id, 'running', restart=True)


def ready(part, model, settings, data=None):
    """Everything `model` needs with `settings` is here: its files and the engine for that hardware."""
    return downloaded(part, model, settings) and engine_ready(part['engine'], settings['hardware'], data)


def _signature(model, settings):
    return {'model': model['id'], 'settings': {key: settings[key] for key in sorted(settings)}}


def applied(part_id):
    """The model and settings the part's service was last started with (None: never started)."""
    return _read_json(run_dir(part_id) / 'applied.json', None)


def delete_model(part_id, model_id, *, extra=False, file=None):
    """Delete a model (all its files; a user's model with its folder), or with `extra` only its extra file
    (mmproj): the model stays and, when it is the chosen one, the choice stops asking for that file.

    Any model can go, the chosen one too (the card then offers to download it again). A file the running
    service holds is let go of first: the service stops, then the file goes (in the background, like Durdur);
    it stays stopped until Başlat. `file`: one file of a user's model (its folder goes with its last file)."""
    part = spec(part_id)
    model = model_spec(part, model_id)
    _not_moving()
    if busy(part_id):
        raise PartError('Bu bileşende bir iş sürüyor; bitince sil.')
    if extra and not model.get('mmproj'):
        raise PartError('Bu modelin ek dosyası yok.')
    if file is not None and (not model.get('user') or file not in model.get('present', [])):
        raise PartError('Bu dosya bu modelin klasöründe yok.')
    entry = read_config().get(part_id, {})
    if extra and (effective(part, hardware())[0] or {}).get('id') == model_id:
        write_part_config(part_id, settings={**(entry.get('settings') or {}), 'vision': False})
    if not held(part, model_id, extra=extra):
        _drop(part, model, extra, file)
        return
    write_part_config(part_id, paused=True,
                      drop=[*(entry.get('drop') or []), {'model': model_id, 'extra': extra, 'file': file}])
    _move(part_id, 'stopped')


def held(part, model_id, *, extra=False):
    """The part's service (running or starting) has this model's files open (with `extra`: its extra file)."""
    running = applied(part['id'])
    if not running or running.get('model') != model_id or extra and running['settings'].get('vision') is False:
        return False
    return answers(int(os.getenv(part['port_key']) or part['port'])) or _supervised(part['id'])


def _drop(part, model, extra=False, file=None):
    folder = models_dir(part['id']) / model['folder']
    try:
        if file:
            (folder / file).unlink(missing_ok=True)
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        elif extra:
            (folder / model['mmproj']).unlink(missing_ok=True)
        elif folder.is_dir():
            shutil.rmtree(folder)
    except OSError:
        raise PartError('Dosya şu an kullanılıyor; birazdan yeniden dene.') from None


def remove(part_id, *, delete_models=False, delete_user_models=False):
    """Turn the part off and stop it (in the background); with `delete_models` what it downloaded goes too,
    once the service has let go of it: our models, and its engine unless another installed part uses it. The
    user's own models go only when asked too (`delete_user_models`): they may have nowhere else."""
    if busy(part_id):
        raise PartError('Bu bileşende bir iş sürüyor; önce iptal et.')
    _not_moving()
    write_part_config(part_id, enabled=False, paused=False, purge=bool(delete_models),
                      purge_user=bool(delete_models and delete_user_models))
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
    _not_moving()
    part = spec(part_id)
    entry = read_config().get(part_id, {})
    if not entry.get('enabled'):
        raise PartError('Bileşen kurulu değil.')
    model, settings = effective(part, hardware())
    if model is None or not ready(part, model, settings):
        raise PartError('Bu seçimin dosyaları eksik; İndir ve başlat ile tamamla.')
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
    _write_json(folder / 'transition.json', {'id': wish, 'to': to, 'restart': restart, 'at': time.time(), 'pid': None,
                                             'error': None})
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
                # Files a delete waited for (the service held them): let go of now.
                for item in entry.get('drop') or []:
                    try:
                        _drop(part, model_spec(part, item['model']), item.get('extra'), item.get('file'))
                    except PartError:
                        pass
                if entry.get('drop'):
                    write_part_config(part_id, drop=[])
            if wanted == 'stopped' and entry.get('purge'):
                folder = models_dir(part_id)
                for path in (folder.iterdir() if folder.is_dir() else []):
                    if path.name != USER_DIR or entry.get('purge_user'):
                        shutil.rmtree(path, ignore_errors=True)
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
                for unused in unused_engines(part):
                    (unused / '.complete').unlink(missing_ok=True)  # Never "ready" if a file is held.
                    shutil.rmtree(unused, ignore_errors=True)
                write_part_config(part_id, purge=False, purge_user=False)
            if wanted == 'running':
                model, settings = effective(part, hardware())
                if model is None or not downloaded(part, model, settings):
                    raise MissingFiles('Modelin dosyaları eksik; indir ve başlat ile tamamla')
                if not engine_ready(part['engine'], settings['hardware']):
                    raise MissingFiles('Bu donanımın motoru indirilmedi; indir ve başlat ile tamamla')
                try:
                    start_part(part_id)
                except PartError:
                    raise
                except Exception:
                    # A service that just stopped gives its card memory back a moment after it exits: a restart
                    # once ran out of memory for that alone. One more try.
                    if not _out_of_memory(part_id):
                        raise
                    time.sleep(RETRY_AFTER)
                    start_part(part_id)
        except Exception as error:
            if wanted == 'running':
                # A failed start stays stopped (Başlat tries again): nothing restarts it by itself.
                write_part_config(part_id, paused=True)
            if mine():
                _write_json(path, {'id': wish, 'to': wanted, 'at': time.time(), 'pid': None,
                                   'error': failure(part_id, wanted, error)})
            return 1
        if mine():
            path.unlink(missing_ok=True)
        return 0
    finally:
        lock.__exit__(None, None, None)


def unused_engines(part, data=None):
    """The folders of `part`'s engine (every hardware) that no other installed part uses: an engine is shared
    (memory and the local LLM both run llama.cpp), so it goes only with the last part that needs it."""
    data = data or catalog()
    hw = hardware()
    config = read_config()
    used = set()
    for other in data['parts']:
        if other['id'] == part['id'] or other.get('engine') != part['engine']:
            continue
        entry = config.get(other['id'], {})
        if entry.get('enabled') or busy(other['id']):
            used.add(engine_dir(other['engine'], effective(other, hw, config)[1]['hardware'], data))
        running = applied(other['id'])
        if running and answers(int(os.getenv(other['port_key']) or other['port'])):
            used.add(engine_dir(other['engine'], running['settings']['hardware'], data))
    return [engine_dir(part['engine'], hw_id, data) for hw_id in (engine_builds(part['engine'], data) or {})
            if engine_dir(part['engine'], hw_id, data).is_dir() and engine_dir(part['engine'], hw_id, data) not in used]


HINTS = (('out of memory', 'Ekran kartının belleği yetmedi: bağlam boyutunu ya da aynı anda isteği küçült, '
                           'veya işlemciyi seç.'),
         ('failed to allocate', 'Bellek yetmedi: bağlam boyutunu ya da aynı anda isteği küçült.'),
         ('unknown model architecture', 'Bu motor sürümü bu modelin mimarisini tanımıyor.'))


RETRY_AFTER = 4


def _out_of_memory(part_id):
    try:
        text = (run_dir(part_id) / 'service.log').read_text(encoding='utf-8', errors='replace').lower()
    except OSError:
        return False
    return 'out of memory' in text or 'failed to allocate' in text


def failure(part_id, wanted, error):
    """What went wrong, in words the card can show: our own reason, else the service's last words from this
    start (the log starts over at every start, so an older run's lines never explain a new failure)."""
    verb = 'Başlatılamadı' if wanted == 'running' else 'Durdurulamadı'
    try:
        lines = [line.strip() for line in (run_dir(part_id) / 'service.log').read_text(
            encoding='utf-8', errors='replace').splitlines() if line.strip()]
    except OSError:
        lines = []
    for needle, hint in HINTS:
        if any(needle in line.lower() for line in lines):
            return f'{verb}: {hint}'
    if isinstance(error, PartError):
        return f'{verb}: {error}.'
    return f'{verb}. Son satır: {lines[-1][-300:]}' if lines else f'{verb}: {error}.'


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
    if moving():
        return  # The move stopped them; it starts them again from the new place.
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
    folder = run_dir(part_id)
    log_path = folder / 'service.log'
    if log_path.is_file():
        os.replace(log_path, folder / 'service.prev.log')
    try:
        argv, env, port = command(part, model, settings, data)
    except Exception as error:
        log_path.write_text(f'Orion: {error}\n', encoding='utf-8')
        raise
    _write_json(folder / 'applied.json', _signature(model, settings))
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


def _fetch_model(part_id, part, model, cancelled, settings=None, step='', roles=None):
    """Fetch what the model lacks for these settings, and say just that: the extra file alone is not "the model"
    (a job that fetched only the mmproj once said "Model indiriliyor" and counted the model already there, as if
    it downloaded it again). `fetch` in the job names the files' roles, so the page marks those lines."""
    extra = model.get('mmproj')
    # `roles`: only these files (the line's own İndir: the extra file whether or not it is used yet).
    files = model_files(part, model, {**(settings or {}), 'vision': True} if roles and 'extra' in roles else settings)
    missing = [path for path in files if not path.is_file()
               and (roles is None or ('extra' if path.name == extra else 'model') in roles)]
    if not missing:
        return
    if model.get('user'):
        raise PartError('Kendi modelinin dosyaları eksik; klasörüne koy.')
    main = [path for path in missing if path.name != extra]
    roles = (['model'] if main else []) + (['extra'] if len(main) < len(missing) else [])
    label = (f'{step}Model indiriliyor: {model["name"]}{" ve ek dosyası" if "extra" in roles else ""}' if main
             else f'{step}Ek dosya indiriliyor: {extra_info(part, model)[0]}')
    # The total is what the buttons said (the catalog's sizes), of the missing files only.
    there = sum(path.stat().st_size for path in files if path.is_file() and path.name != extra)
    estimate = ((max(0, model['size_mb'] * MB - there) if main else 0)
                + (model.get('mmproj_mb', 0) * MB if 'extra' in roles else 0))
    _update_job(part_id, phase='model', label=label, fetch=roles, bytes=0, total=int(estimate) or None, rate=0)
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
    offset = 0
    for path in missing:
        download(model['url'] + path.name, path, report=_Progress(part_id, label, offset, int(estimate) or None),
                 cancelled=cancelled)
        verify(path, (model.get('sha256') or {}).get(path.name), 'Model')
        offset += path.stat().st_size


def run_job(part_id, action, model_id, fetch=None):
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
        _fetch_model(part_id, part, model, cancelled, settings, step('model'), fetch)
        if action == 'install':
            again = bool(read_config().get(part_id, {}).get('enabled'))
            write_part_config(part_id, enabled=True, paused=False, model=model_id)
            _update_job(part_id, phase='launching', bytes=None, total=None, rate=0, fetch=[],
                        label='Yeni ayarlarla yeniden başlatılıyor (model belleğe yükleniyor)…' if again
                        else 'Kuruluyor: servis başlatılıyor (model belleğe yükleniyor)…')
            stop_part(part_id)
            try:
                start_part(part_id)
            except Exception as error:
                write_part_config(part_id, paused=True)  # Başlat tries again; nothing restarts it by itself.
                raise PartError(failure(part_id, 'running', error)) from None
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
    job_parser.add_argument('--fetch')
    sub.add_parser('status')
    args = parser.parse_args(argv)
    if args.action == 'serve':
        serve(args.part)
    elif args.action == 'start':
        start_part(args.part)
    elif args.action == 'apply':
        return apply(args.part, wish=args.id, restart=args.restart)
    elif args.action == 'job':
        return run_job(args.part, args.kind, args.model, args.fetch.split(',') if args.fetch else None)
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
