"""Optional local AI parts ("Bileşenler"): what each one is, the models it can use, this PC's hardware, what
is installed, and the jobs that download and install them.

    parts home   <installation>/Parts for an installed Orion; <source tree>/.local_parts when the Hub runs from
                 source (a development tree never writes into the installed Orion's folder)
      config.json                         per part: enabled, model, settings
      engines/<engine>-<version>/<hw>/    an engine (llama-server, tts-server, or a Python service's extra
                                          packages under site/), shared by the parts that use it
      engines/shared/<sha256>/            an archive several engines carry (CUDA's runtime, 373 MB): unpacked
                                          once, its files hard-linked into each engine folder
      models/<part>/<folder>/<files>      (the installation's models location's parts/ when it has one; the user
                                          can move it: `location`, services/shared/parts_location.py)
      models/<part>/custom/<name>/        the user's own models: a folder each, read from its files
      models/tts/voices/                   cloned voice caches and generated TTS warm-up audio
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
import functools
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


def engine_exe(engine, hardware, data=None, folder=None):
    folder = folder or engine_dir(engine, hardware, data)
    name = 'llama-server.exe' if os.name == 'nt' else 'llama-server'
    found = next(iter(sorted(folder.rglob(name))), None) if folder.is_dir() else None
    return found


def engine_recipe(engine, hardware, data=None):
    """How this engine is put together here: {'bundled', 'archives', 'wheels'}, or None (not on this system)."""
    return (engine_builds(engine, data) or {}).get(hardware)


def for_model(part, model, data=None):
    """`part` as `model` runs it. A model may bring its own engine (VoxCPM2 next to OmniVoice in the speech voice),
    and then that engine's service settings (its `env`) stand for the part's."""
    engine = (model or {}).get('engine')
    if not engine or engine == part.get('engine'):
        return part
    entry = (data or catalog())['engines'][engine]
    return {**part, 'engine': engine, 'env': entry.get('env', part.get('env', {}))}


def part_engines(part):
    """Every engine one of the part's models runs on (the part's own first)."""
    engines = [part.get('engine')] + [m.get('engine') for m in part.get('models', [])]
    return list(dict.fromkeys(engine for engine in engines if engine))


def engine_ready(engine, hardware, data=None):
    folder = engine_dir(engine, hardware, data)
    return (folder / '.complete').is_file() or (engine == 'llama.cpp' and engine_exe(engine, hardware, data) is not None)


def _engine_tags(entry):
    return ['-py' + version.replace('.', '') for version in entry['pythons']] if 'pythons' in entry else ['']


def older_engines(engine, hardware, data=None):
    """Complete folders of this engine's earlier versions for `hardware`, newest first. An Orion update that
    raises an engine's version keeps the old one: the part runs on it while the new one downloads, and it goes
    once a part has answered on the new one (`_drop_older`)."""
    data = data or catalog()
    current = engine_dir(engine, hardware, data)
    tags = _engine_tags(data['engines'][engine])
    root = home() / 'engines'
    found = []
    for folder in root.iterdir() if root.is_dir() else []:
        path = folder / hardware
        try:
            version = (path / '.complete').read_text(encoding='utf-8').strip()
        except OSError:
            continue
        if path != current and folder.name in {f'{engine}-{version}{tag}' for tag in tags}:
            found.append(path)
    return sorted(found, key=lambda path: (path / '.complete').stat().st_mtime, reverse=True)


OLD_ENGINE = 'ORION_OLD_ENGINE'  # Set for a start after the new engine version failed to start in this Hub run.


def engine_in_use(engine, hardware, data=None):
    """The folder a start runs: the catalog's version, else the newest earlier one (the new one is still
    downloading, or did not start in this Hub run); None when neither is here."""
    older = older_engines(engine, hardware, data)
    if engine_ready(engine, hardware, data) and not (os.getenv(OLD_ENGINE) and older):
        return engine_dir(engine, hardware, data)
    return older[0] if older else None


def python_fits(engine, data=None):
    """Python packages exist for the Pythons the catalog lists (the packaged Orion's, and the one a Hub run from
    source uses); another Python cannot install this part."""
    entry = (data or catalog())['engines'][engine]
    return 'pythons' not in entry or _python() in entry['pythons']


def shared_dir(piece):
    """Where a `shared` archive is unpacked, once for every engine that carries it."""
    key = piece.get('sha256', '')[:16] or piece['url'].rsplit('/', 1)[-1].removesuffix('.zip')
    return home() / 'engines' / 'shared' / key


def _have_shared(piece):
    return bool(piece.get('shared')) and (shared_dir(piece) / '.complete').is_file()


def engine_size(engine, hardware, data=None):
    """Bytes to download for the engine when every piece says its size, else None (a shared archive already
    here downloads no more)."""
    recipe = engine_recipe(engine, hardware, data) or {}
    pieces = [piece for piece in recipe.get('archives', []) + recipe.get('wheels', []) if not _have_shared(piece)]
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


def public_opener():
    """For models and engines from the internet (Hugging Face, GitHub): Windows verifies the certificate, as for
    Orion's own updates (windows_trust.py). Python's check alone fails on GitHub's new Sectigo chain until Windows
    has fetched its root for a connection of its own: an engine from GitHub then never downloaded."""
    from .windows_trust import context
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context()))


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
    caps = _compute_caps(run) if gpus else {}
    for gpu in gpus:
        if gpu['index'] in caps:
            gpu['compute'] = caps[gpu['index']]
    return gpus


def _compute_caps(run=subprocess.run):
    """{index: compute capability} of the NVIDIA cards. Asked on its own: a driver that does not know the field
    still lists its cards (a card whose capability is unknown is taken to fit every CUDA build, cuda_cards())."""
    try:
        result = run(['nvidia-smi', '--query-gpu=index,compute_cap', '--format=csv,noheader'],
                     capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return {}
    caps = {}
    for line in result.stdout.splitlines() if result.returncode == 0 else []:
        cells = [cell.strip() for cell in line.split(',')]
        if len(cells) == 2 and cells[0].isdigit():
            try:
                caps[int(cells[0])] = float(cells[1])
            except ValueError:
                pass
    return caps


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
        # the CUDA build (about 100 against 60-70 tokens a second). CUDA stays a choice. Speech lists its own
        # order (choices()): the voice ran faster on CUDA, and recognition beside it on Vulkan starved both.
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

def choices(part, hw):
    """The ways this part can run on this PC, best first: in the part's own order where it lists one (speech
    runs faster on CUDA than on Vulkan on an NVIDIA card), else in this PC's."""
    offered = {choice['id']: choice for choice in hw['choices']}
    if 'cuda' in offered and _nvidia_cards(hw) and not cuda_cards(part, hw):
        del offered['cuda']  # Every NVIDIA card is older than this part's CUDA build: Vulkan or the processor.
    order = part.get('hardware') or list(offered)
    return [offered[i] for i in order if i in offered] or [{'id': 'cpu', 'label': HARDWARE_LABELS['cpu']}]


def _nvidia_cards(hw):
    return [gpu for gpu in hw['gpus'] if gpu.get('vendor') == 'nvidia']


@functools.lru_cache(maxsize=None)
def _catalog_file():
    """local_parts.json as shipped (choices() asks it often; catalog() also looks for the user's models)."""
    return json.loads(CATALOG.read_text(encoding='utf-8'))


def cuda_floor(engine):
    """The lowest compute capability this engine's CUDA build has kernels for here, or None (no limit known).
    Each one's source is in docs/bilesenler.md."""
    return ((engine_builds(engine, _catalog_file()) or {}).get('cuda') or {}).get('min_compute')


def cuda_cards(part, hw):
    """The NVIDIA cards this part's CUDA build runs on. A card below its floor would load nothing ("no kernel
    image"); one whose capability is unknown (an older driver, a cache from before) is taken to fit."""
    floor = cuda_floor(part['engine']) if part.get('engine') else None
    return [gpu for gpu in _nvidia_cards(hw) if floor is None or gpu.get('compute') is None or gpu['compute'] >= floor]


def _numbered_cards(part, hw):
    """The cards "Hangi ekran kartı" lists: where the part runs on CUDA here, only the ones its CUDA build runs on."""
    return cuda_cards(part, hw) if any(c['id'] == 'cuda' for c in choices(part, hw)) else _nvidia_cards(hw)


def default_settings(part, model, hw):
    """What a part runs with unless the user changes it (Gelişmiş ayarlar)."""
    hardware_id = choices(part, hw)[0]['id']
    if part.get('runner') == 'python':
        return {'hardware': hardware_id, 'gpu': 'all', **({'vram_mode': 'balanced'} if part['id'] == 'tts' else {})}
    settings = {'hardware': hardware_id, 'gpu': 'all', 'context_size': part.get('context_size', 8192), 'parallel': 1,
                'vram_mode': 'balanced'}
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
            'layers': number('block_count'), 'heads': number('attention.head_count'),
            'kv_heads': number('attention.head_count_kv'), 'pooling': number('pooling_type'),
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
    opener = opener or public_opener()
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


# Video memory a model needs, as facts for the page to work out with the user's choices (the context, the memory
# mode): llama.cpp models = their file + the context's cache + about 0.3 GB of working buffers. The speech models'
# are measured on an RTX 3060 Ti after the model loaded and answered one ~280-character request (the engine's own
# dedicated GPU memory, 2026-11-13): VoxCPM2 v1.0.3 on CUDA by mode, OmniVoice on CUDA, whisper.cpp on Vulkan. A
# speech recognition model not measured (the user's own) shows its file x 1.5 (Small 2.1x, the large ones 1.55x), as
# an estimate.
BUFFERS_MB = 300
MEASURED_VOXCPM2_MB = {'full': 4100, 'balanced': 3400, 'low': 3000}
MEASURED_OMNIVOICE_MB = 1500
MEASURED_WHISPER_MB = {'whisper-small': 520, 'whisper-large-v3-turbo': 840, 'whisper-large-v3': 1610}
# The same model on Vulkan takes less than on CUDA, whose context costs more (2026-11-14, same card): VoxCPM2
# 3599/2869/2519 MB on CUDA against 3475/2728/2379 on Vulkan (about 130 MB), OmniVoice 1531 against 1375, the
# llama.cpp server 433 against 305 with a 200M model, whisper.cpp 1007 against 887. The tables above are CUDA's
# (whisper.cpp's are Vulkan's, the engine it ships with first); these are what the other backend adds.
HARDWARE_DELTA_MB = {'voxcpm2': {'vulkan': -130}, 'omnivoice': {'vulkan': -150}, 'llama': {'vulkan': -120},
                     'whisper': {'cuda': 120}}


def _shifted(value, delta):
    return {key: item + delta for key, item in value.items()} if isinstance(value, dict) else value + delta


def memory_facts(part, model, meta):
    if part.get('runner') != 'python':
        weights = model.get('size_mb') or 0
        by_hardware = {hw: {'buffers_mb': BUFFERS_MB + delta} for hw, delta in HARDWARE_DELTA_MB['llama'].items()}
        layers, heads, kv_heads, dims = (meta or {}).get('layers'), (meta or {}).get('heads'),             (meta or {}).get('kv_heads'), (meta or {}).get('dims')
        # The cache holds a key and a value (2 bytes each) per layer, KV head and head dimension, per token.
        kv = 2 * layers * kv_heads * (dims // heads) * 2 * 1000 / 1048576 if layers and heads and kv_heads and dims else None
        return {'weights_mb': weights, 'kv_mb_per_1k': kv, 'buffers_mb': BUFFERS_MB, 'by_hardware': by_hardware}
    if part['id'] == 'tts':
        if model.get('engine') == 'voxcpm2-gguf':
            return {'by_mode': MEASURED_VOXCPM2_MB, 'measured': True, 'by_hardware': {
                hw: {'by_mode': _shifted(MEASURED_VOXCPM2_MB, delta)} for hw, delta in HARDWARE_DELTA_MB['voxcpm2'].items()}}
        return {'fixed_mb': MEASURED_OMNIVOICE_MB, 'measured': True, 'by_hardware': {
            hw: {'fixed_mb': MEASURED_OMNIVOICE_MB + delta} for hw, delta in HARDWARE_DELTA_MB['omnivoice'].items()}}
    if model.get('id') in MEASURED_WHISPER_MB:
        base = MEASURED_WHISPER_MB[model['id']]
        return {'fixed_mb': base, 'measured': True, 'by_hardware': {
            hw: {'fixed_mb': base + delta} for hw, delta in HARDWARE_DELTA_MB['whisper'].items()}}
    return {'fixed_mb': round((model.get('size_mb') or 0) * 1.5), 'measured': False}


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
    return EXTRAS.get((meta.get('vision', False), meta.get('audio', False)), ('Görsel anlama', 'Resimleri anlar.'))


# --- The user's own models -----------------------------------------------------------------------------------
# Ours come from the catalog; the user's are folders under models/<part>/custom/, one per model, named by the
# user (a part with several engines, such as TTS, has a folder per engine: custom/OmniVoice/<name>). The user
# picks the engine; which file is which, the context limit and the rest are read from the files' contents,
# never from their names, so a folder filled by hand works as well as one made from the page.

USER_DIR = 'custom'
VOICES_DIR = 'voices'  # models/tts/voices: cloned voices and generated warm-up audio (the user's, like custom/)
USER_DATA_DIRS = (USER_DIR, VOICES_DIR)
MB = 1048576

# What each engine needs from a model folder, in the order the service takes the files. `gguf`: a GGUF file
# whose header says this architecture ('*': any model, not an mmproj or a sibling's); `whisper`: a whisper.cpp
# model (ggml), whatever its name. A kind whose engine Orion does not have yet (`engine` other than the part's)
# is shown and cannot be added.
USER_KINDS = {
    'llm': [{'id': 'gguf', 'label': 'GGUF (llama.cpp)', 'engine': 'llama.cpp', 'files': [
        {'gguf': '*', 'label': 'Model', 'expect': 'model.gguf'},
        {'gguf': 'clip', 'role': 'mmproj', 'optional': True, 'label': 'Görsel ve ses anlama', 'expect': 'mmproj.gguf'}]}],
    'embedding': [{'id': 'gguf', 'label': 'GGUF (llama.cpp)', 'engine': 'llama.cpp', 'files': [
        {'gguf': '*', 'label': 'Model', 'expect': 'model.gguf'}]}],
    # `expect`: what a missing file is, the same way for every part: a plain file name of its kind (OmniVoice's
    # two are its base and tokenizer GGUFs, as their own names say); any name does, the contents decide.
    # One file, as whisper.cpp's own models come (ggml-small.bin, ggml-large-v3-turbo-q5_0.bin...), read from its
    # header, whatever its name (`expect` says what is wanted while it is missing); another model format put
    # there by mistake is named (_other_format).
    'stt': [{'id': 'ggml', 'label': 'whisper.cpp (ggml)', 'engine': 'whisper.cpp', 'files': [
        {'whisper': True, 'label': 'Model', 'expect': 'model.bin'}]}],
    'tts': [{'id': 'omnivoice', 'label': 'OmniVoice', 'folder': 'OmniVoice', 'engine': 'omnivoice-gguf', 'files': [
                {'gguf': 'omnivoice-lm', 'label': 'Ses modeli', 'expect': 'base.gguf'},
                {'gguf': 'omnivoice-tokenizer', 'label': 'Ses çözücü', 'expect': 'tokenizer.gguf'}]},
            # VoxCPM2's two GGUFs (llama.cpp-omni): the BaseLM says it is a MiniCPM4, the other its acoustic stack.
            {'id': 'voxcpm2', 'label': 'VoxCPM2', 'folder': 'VoxCPM2', 'engine': 'voxcpm2-gguf', 'files': [
                {'gguf': 'minicpm4', 'label': 'Dil modeli (BaseLM)', 'expect': 'BaseLM.gguf'},
                {'gguf': 'voxcpm-acoustic', 'label': 'Ses modeli (Acoustic)', 'expect': 'Acoustic.gguf'}]}],
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


def whisper_meta(path):
    """What a whisper.cpp model file (ggml) says about itself, from its first bytes: how many mel bands it hears,
    its layers (audio, text) and vocabulary. None when it is not one: the magic and Whisper's fixed audio (1500)
    and text (448) windows tell it from an old llama.cpp ggml file or the Silero VAD."""
    path = Path(path)
    try:
        key = ('whisper', str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key not in _metas:
        try:
            with path.open('rb') as stream:
                head = stream.read(48)
        except OSError:
            head = b''
        meta = None
        if len(head) == 48 and struct.unpack_from('<I', head)[0] == 0x67676d6c:
            vocab, audio_ctx, _, _, audio_layers, text_ctx, _, _, text_layers, mels, _ = struct.unpack_from('<11i', head, 4)
            if audio_ctx == 1500 and text_ctx == 448 and mels in (80, 128):
                meta = {'mels': mels, 'audio_layers': audio_layers, 'text_layers': text_layers, 'vocab': vocab}
        _metas[key] = meta
    return _metas[key]


# Model formats people download that whisper.cpp does not read, by suffix where the header does not tell.
_FORMATS = {'.safetensors': 'safetensors (Hugging Face)', '.onnx': 'ONNX', '.pt': 'PyTorch', '.pth': 'PyTorch',
            '.ckpt': 'PyTorch'}


def _other_format(path):
    """The format of a model file that is not a whisper.cpp model, or None for a file that is no model (a
    config.json): GGUF, faster-whisper's CTranslate2 (Systran's model.bin), another ggml file (the Silero VAD,
    an old llama.cpp model), else from its suffix ('tanınmayan' for a .bin nothing says more about)."""
    try:
        with path.open('rb') as stream:
            head = stream.read(64)
    except OSError:
        return None
    if head[:4] == b'GGUF':
        return 'GGUF'
    if len(head) >= 4 and struct.unpack_from('<I', head)[0] == 0x67676d6c:
        return 'başka bir ggml'
    if len(head) >= 8 and 1 <= struct.unpack_from('<I', head)[0] <= 100:
        size = struct.unpack_from('<H', head, 4)[0]
        if re.fullmatch(rb'[A-Za-z0-9]+Spec\0', head[6:6 + size]):
            return 'faster-whisper (CTranslate2)'
    return _FORMATS.get(path.suffix.lower(), 'tanınmayan' if path.suffix.lower() == '.bin' else None)


def _user_model(part, kind, path, model_id, folder):
    """One folder of the user's as a catalog entry like ours (files in the service's order, sizes, args), with
    a checklist of what its engine needs: each file there, missing, more than one candidate, or not right."""
    present = sorted(file for file in path.iterdir() if file.is_file())
    copying = [file.name for file in present if not _settled(file)]
    ggufs = [(file, file_meta(file)) for file in present if file.suffix.lower() == '.gguf' and file.name not in copying
             and any('gguf' in spec for spec in kind['files'])]
    claimed = {spec['gguf'] for spec in kind['files'] if spec.get('gguf', '*') != '*'} | {'clip'}
    problems = [f'{file.name}: okunamadı.' for file, meta in ggufs if meta is None]
    checklist, picked = [], []
    for spec in kind['files']:
        if spec.get('whisper'):
            matches = [file for file in present if file.name not in copying and whisper_meta(file)]
        else:
            matches = [file for file, meta in ggufs if meta and (
                meta['architecture'] == spec['gguf'] or spec['gguf'] == '*' and meta['architecture'] not in claimed)]
            parts = [file for file in matches if _SPLIT.search(file.name)]
            if parts and len(parts) == len(matches):
                matches = [min(parts, key=lambda file: file.name)]  # A split model loads from its first piece.
        # `label` says what the file is for, `names` which file it is; `expect` the name it must have (an engine
        # that loads its files by name), shown while it is missing.
        item = {'label': spec['label'], 'optional': bool(spec.get('optional')), 'hint': spec.get('hint'),
                'role': spec.get('role'), 'expect': ' / '.join(spec.get('names') or []) or spec.get('expect'),
                'state': 'ok' if len(matches) == 1 else 'many' if matches else 'missing',
                'names': [file.name for file in matches]}
        if len(matches) == 1:
            item['mb'] = round(matches[0].stat().st_size / MB)
            meta = file_meta(matches[0]) if matches[0].suffix.lower() == '.gguf' else None
            if meta:
                item['architecture'], item['context'] = meta['architecture'], meta['context']
                if spec.get('role') == 'mmproj':  # What it understands, as the file says (as for ours).
                    item['label'] = EXTRAS.get((meta.get('vision', False), meta.get('audio', False)),
                                               (spec['label'],))[0]
        checklist.append(item)
        picked.append(matches[0] if len(matches) == 1 else None)
    used = {name for item in checklist for name in item['names']}
    problems += [f'{file.name}: tanınmayan dosya ({meta["architecture"]}).'
                 for file, meta in ggufs if meta and file.name not in used]
    if any(spec.get('whisper') for spec in kind['files']):
        for file in present:
            other = None if file.name in copying or whisper_meta(file) else _other_format(file)
            if other:
                problems.append(f"{file.name}: {other} biçiminde; whisper.cpp'nin ggml .bin dosyası gerekir.")
    ready = kind['engine'] in part_engines(part)
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
    entry = {'id': model_id, 'name': path.name, 'description': _descriptions().get(f'{part["id"]}/{model_id}', ''),
             'folder': folder,
             'files': [file.name for file in files], 'size_mb': round(sum(file.stat().st_size for file in files) / MB),
             'args': args, 'user': True, 'kind': kind['id'], 'kind_label': kind['label'], 'engine': kind['engine'],
             'complete': complete,
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


NAME_MAX, DESCRIPTION_MAX = 40, 80  # The page counts down to these.


def _descriptions():
    """The words the user gave their own models, by model id (kept beside the jobs, not in the model folder:
    the folder holds only the model's files)."""
    return _read_json(home() / 'user-models.json', {})


def _clean_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', str(name or '')).strip().rstrip('. ')
    if not name:
        raise PartError('Modele bir ad ver.')
    if len(name) > NAME_MAX:
        raise PartError(f'Ad en çok {NAME_MAX} harf olabilir.')
    return name


def _clean_description(description):
    description = ' '.join(str(description or '').split())
    if len(description) > DESCRIPTION_MAX:
        raise PartError(f'Açıklama en çok {DESCRIPTION_MAX} harf olabilir.')
    return description


def _describe(part_id, model_id, description, *, was=None):
    descriptions = _descriptions()
    descriptions.pop(f'{part_id}/{was or model_id}', None)
    if description:
        descriptions[f'{part_id}/{model_id}'] = description
    _write_json(home() / 'user-models.json', descriptions)


def edit_user_model(part_id, model_id, name, description=None):
    """Rename one of the user's models (its folder) and set its description. The saved choice follows a new
    name; a model its service has open keeps its name until the service stops (Windows will not move an open
    file's folder). Returns its id."""
    _not_moving()
    part = spec(part_id)
    model = model_spec(part, model_id)
    if not model.get('user'):
        raise PartError('Yalnızca kendi modellerin düzenlenir.')
    name, description = _clean_name(name), _clean_description(description)
    kind = next(place[0] for place in _places(part_id) if place[0]['id'] == model['kind'])
    new_id = _user_id(part_id, kind, name)
    folder = models_dir(part_id) / model['folder']
    if name != folder.name:
        if new_id != model_id and any(other['id'] == new_id for other in part['models']):
            raise PartError('Bu adda bir model zaten var.')
        if held(part, model_id):
            raise PartError('Model çalışırken adı değişmez; önce durdur.')
        try:
            folder.rename(folder.with_name(name))
        except OSError:
            raise PartError('Klasörün adı değiştirilemedi; içindeki bir dosya açık olabilir.') from None
        if (read_config().get(part_id) or {}).get('model') == model_id:
            write_part_config(part_id, model=new_id)
    _describe(part_id, new_id, description, was=model_id)
    return new_id


def add_user_model(part_id, name, kind_id=None, description=None):
    """Make the folder for a model the user brings, for the engine they chose: it shows at once (empty) and
    fills as they copy its files in. `description` shows under its name, as ours do. Returns its id."""
    _not_moving()
    part = spec(part_id)
    places = _places(part_id)
    if not places:
        raise PartError('Bu bileşene kendi modelin eklenemiyor.')
    kind, folder, _ = places[0] if kind_id is None else next(
        (place for place in places if place[0]['id'] == kind_id), (None, None, None))
    if kind is None:
        raise PartError('Böyle bir motor yok.')
    if kind['engine'] not in part_engines(part):
        raise PartError(f'{kind["label"]} motoru henüz eklenmedi.')
    name, description = _clean_name(name), _clean_description(description)
    if any(model['id'] == _user_id(part_id, kind, name) for model in part['models']):
        raise PartError('Bu adda bir model zaten var.')
    try:
        (folder / name).mkdir(parents=True)
    except OSError:
        raise PartError('Bu adla klasör açılamadı; başka bir ad dene.') from None
    model_id = _user_id(part_id, kind, name)
    _describe(part_id, model_id, description)  # A folder made again under an old name starts bare.
    return model_id


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
    to_front(lambda name, title: name == 'CabinetWClass' and (
        title == folder.name or title.startswith(f'{folder.name} - ') or str(folder) in title), wait)


def to_front(matches, wait=3.0):
    """Bring the first visible window that `matches(class_name, title)` to the front once it shows (see
    `_to_front`)."""
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32')
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(window, _):
        name, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(1024)
        user.GetClassNameW(window, name, 64)
        if user.IsWindowVisible(window):
            user.GetWindowTextW(window, title, 1024)
            if matches(name.value, title.value):
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


# "Bellek": how much of the model sits on the card. Balanced is the default; full puts all of it there, balanced and
# low keep part of it on the processor: less video memory, slower (the layers run one after the other).
VRAM_MODES = ('full', 'balanced', 'low')
VOXCPM2_LAYERS = {'balanced': 14, 'low': 7}  # of the BaseLM's 28; measured on an RTX 3060 Ti (CUDA and Vulkan alike)
LLAMA_SHARE = {'balanced': 0.5, 'low': 0.25}  # the share of a llama.cpp model's layers kept on the card


def vram_field(part, model=None):
    """The memory mode a card offers (None: speech recognition has no such choice). Facts only, the same for any
    model of the part unless that model's engine has no such control; each option says in `detail` what it costs.
    VoxCPM2's numbers are what was measured (docs/bilesenler.md); OmniVoice ignores the mode."""
    if part.get('runner') == 'python':
        if part['id'] != 'tts':
            return None
        if model is not None and for_model(part, model).get('engine') != 'voxcpm2-gguf':
            return None
        detail = {'balanced': 'Yaklaşık 0,7 GB daha az bellek, ses biraz geç başlar.',
                  'low': 'Yaklaşık 1,1 GB daha az bellek, biraz daha yavaş.'}
        hint = None
    else:
        detail = {'balanced': 'Bellek yaklaşık yarıya iner, yanıt biraz geç gelir.',
                  'low': 'Bellek yaklaşık dörtte birine iner, yanıt daha geç gelir.'}
        hint = None
    return {'key': 'vram_mode', 'label': 'Bellek kullanımı', 'type': 'choice', 'slider': True, 'hint': hint,
            'options': [{'id': 'full', 'label': 'Hızlı', 'detail': 'Hepsi ekran kartında, en hızlı.'},
                        {'id': 'balanced', 'label': 'Dengeli', 'detail': detail['balanced']},
                        {'id': 'low', 'label': 'Az bellek', 'detail': detail['low']}]}


def advanced(part, hw, model=None):
    """The advanced settings a card shows (all have a default)."""
    fields = [{'key': 'hardware', 'label': 'Nerede çalışsın', 'type': 'choice', 'options': choices(part, hw)}]
    # Only NVIDIA cards are numbered for CUDA (CUDA_VISIBLE_DEVICES); the field shows when there is a choice.
    nvidia = _numbered_cards(part, hw)
    if len(nvidia) > 1:
        fields.append({'key': 'gpu', 'label': 'Hangi ekran kartı', 'type': 'choice',
                       'options': [{'id': 'all', 'label': 'Hepsi'}] + [
                           {'id': str(gpu['index']), 'label': f"{gpu['index']}: {gpu['name']}"} for gpu in nvidia]})
    if part.get('runner') == 'python':
        extra = vram_field(part, model)
        return fields + ([extra] if extra else [])
    # No layer count to type: on a card the whole model goes to it unless the memory mode keeps part on the
    # processor (command()); on the processor none can go.
    fields += [
        {'key': 'context_size', 'label': 'Bağlam boyutu (token)', 'type': 'int', 'min': 512,
         'max': context_max(part, model),
         'hint': 'Bir seferde ne kadar metin görebilir; üst sınır seçili modelin kendi sınırı. '
                 'Büyüdükçe daha çok bellek ister.'},
        {'key': 'parallel', 'label': 'Aynı anda istek', 'type': 'int', 'min': 1, 'max': 8,
         'hint': 'Kaç isteğe aynı anda yanıt verir. Her biri bellek ister.'},
    ]
    fields.append(vram_field(part))
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
            # Older TTS config can carry VoxCPM2's mode while OmniVoice is selected. Ignore any stale client
            # value and preserve the saved VoxCPM2 preference; OmniVoice has no corresponding control.
            if (key == 'vram_mode' and part.get('runner') == 'python' and part['id'] == 'tts' and model is not None
                    and for_model(part, model).get('engine') != 'voxcpm2-gguf'):
                continue
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


def _vision_off(entry):
    """The models whose extra file (mmproj) the user turned off, from a part's saved entry. An entry from before
    this was kept per model has one `vision` setting: it was the chosen model's."""
    if 'vision_off' in entry:
        return list(entry['vision_off'])
    return [entry['model']] if entry.get('model') and (entry.get('settings') or {}).get('vision') is False else []


def effective(part, hw, config=None):
    config = (read_config() if config is None else config).get(part['id'], {})
    model_id = config.get('model') or next((m['id'] for m in part.get('models', []) if m.get('recommended')), None)
    # A model the catalog replaced (speech's CTranslate2 models by whisper.cpp's): its successor is the choice.
    model_id = part.get('retired_models', {}).get(model_id, model_id)
    model = next((m for m in part.get('models', []) if m['id'] == model_id), None)
    settings = {**default_settings(part, model, hw), **(config.get('settings') or {})}
    if 'vision' in settings and model is not None:
        # Each model keeps its own: the extra file is on unless that model's was turned off.
        settings['vision'] = model['id'] not in _vision_off(config)
    settings.pop('gpu_layers', None)  # An older setting: the whole model goes to the card now.
    settings.pop('compute_type', None)  # Speech's on faster-whisper; a ggml model carries its own precision.
    if 'context_size' in settings:
        # Never past what the model was trained for (a context saved for another model, or the default).
        settings['context_size'] = min(int(settings['context_size']), context_max(part, model))
    if settings['hardware'] not in {choice['id'] for choice in choices(part, hw)}:
        settings['hardware'] = choices(part, hw)[0]['id']  # A card that is gone, or a config copied from another PC.
    if settings.get('gpu', 'all') != 'all' and settings['gpu'] not in {str(gpu['index']) for gpu in _numbered_cards(part, hw)}:
        settings['gpu'] = 'all'  # That card is gone, or this part's CUDA build does not run on it.
    return model, settings


def _gpu_layers(settings, file):
    """llama.cpp's -ngl: none on the processor, all on a card, or the memory mode's share of the model's layers."""
    if settings['hardware'] == 'cpu':
        return 0
    share = LLAMA_SHARE.get(settings.get('vram_mode'))
    layers = (file_meta(file) or {}).get('layers') if share else None
    return max(1, round(layers * share)) if layers else ALL_LAYERS


def command(part, model, settings, data=None):
    """The argv, environment and port for `part` running `model` with `settings`."""
    part = for_model(part, model, data)
    if part.get('runner') == 'python':
        return _python_command(part, model, settings, data)
    from .service_access import bind_host, own_origins
    folder = engine_in_use(part['engine'], settings['hardware'], data)
    exe = engine_exe(part['engine'], settings['hardware'], data, folder) if folder else None
    if exe is None:
        raise PartError('Motor kurulu değil.')
    files = model_files(part, model, settings)
    port = str(os.getenv(part['port_key']) or part['port'])
    argv = [str(exe), '-m', str(files[0]), '--port', port, '--host', bind_host(), '--cors-origins', own_origins(port),
            '--no-slots', '-c', str(settings['context_size']), '-np', str(settings['parallel']),
            '-ngl', str(_gpu_layers(settings, files[0]))]
    if model.get('mmproj') and len(files) > len(model['files']):
        argv += ['--mmproj', str(files[-1])]
    argv += list(part.get('args', [])) + list(model.get('args', []))
    env = dict(os.environ)
    if settings.get('gpu', 'all') != 'all':
        env['CUDA_VISIBLE_DEVICES' if settings['hardware'] == 'cuda' else 'GGML_VK_VISIBLE_DEVICES'] = settings['gpu']
    return argv, env, int(port)


def _python_command(part, model, settings, data=None):
    """One of Orion's own services on the packaged Python, with its engine's packages and libraries."""
    folder = engine_in_use(part['engine'], settings['hardware'], data)
    if folder is None:
        raise PartError('Motor kurulu değil.')
    port = str(os.getenv(part['port_key']) or part['port'])
    gpu = settings['hardware'] != 'cpu'
    # `files`: the model's files in the order its kind lists them (a user's model names them its own way).
    values = {'model_folder': model['folder'], 'model_dir': str(models_dir(part['id']) / model['folder']),
              'files': [str(path) for path in model_files(part, model, settings)], 'engine_dir': str(folder)}
    site = folder / 'site'
    argv = [sys.executable, '-B', '-m', 'services.shared.part_python', str(ROOT / part['script']),
            str(site) if site.is_dir() else '']
    # Offline: whatever the model lacks, nothing is fetched from the internet in its place (a library that
    # finds no model or dictionary under a name may download one).
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8', ORION_HW_ID=settings['hardware'],
               ORION_NATIVE_MODELS_DIR=str(models_dir(part['id'])), ORION_PART=part['id'], HF_HUB_OFFLINE='1',
               TRANSFORMERS_OFFLINE='1')
    if part['id'] == 'tts':
        env['ORION_TTS_VOICE_DATA_DIR'] = str(models_dir('tts') / VOICES_DIR)
    # numpy's OpenBLAS reserves ~30 MB of working buffer per CPU core when it loads (+363 MB on 12 cores, measured
    # in a process that only imports numpy); these processes pass audio around and do no matrix work.
    env.setdefault('OPENBLAS_NUM_THREADS', '1')
    env[part['port_key']] = port
    env.update({key: value.format(**values) for key, value in part.get('env', {}).items()})
    if gpu and part.get('engine') == 'voxcpm2-gguf' and settings.get('vram_mode') in VOXCPM2_LAYERS:
        env['VOXCPM2_GPU_LAYERS'] = str(VOXCPM2_LAYERS[settings['vram_mode']])
    if gpu and settings.get('gpu', 'all') != 'all':
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


def _listening(port):
    """Something on this PC listens on the port (None: cannot tell). Asked first because on Windows a connection
    to a port nobody listens on is not refused at once: each such probe took the whole half-second timeout, and
    the Bileşenler page waited 2 s for every status (four parts, most of them stopped)."""
    try:
        import psutil
        return any(conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port
                   for conn in psutil.net_connections('tcp'))
    except Exception:
        return None


def answers(port, timeout=.5):
    if _listening(port) is False:
        return False
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


def source_page(model):
    """The page a catalog model comes from, for the user to see: the catalog's `source`, else the Hugging Face
    repository its files are downloaded from (its page, not the fixed commit the files come from). A user's own
    model has none."""
    if model.get('user'):
        return None
    if model.get('source'):
        return model['source']
    url = model.get('url') or ''
    if url.startswith('https://huggingface.co/') and '/resolve/' in url:
        return url.split('/resolve/', 1)[0]
    return None


def part_status(part, hw, config, data):
    port = int(os.getenv(part['port_key']) or part['port'])
    running = answers(port)
    base = {'id': part['id'], 'title': part['title'], 'description': part['description'], 'cloud': part.get('cloud'),
            'port': port}
    if not part.get('engine'):
        # Not installable from here yet: say whether it already runs (installed another way) and what else works.
        return {**base, 'installable': False, 'note': part.get('pending'), 'state': 'running' if running else 'absent',
                'models': [], 'advanced': [], 'settings': {}, 'job': None}
    model, settings = effective(part, hw, config)
    run = for_model(part, model, data)  # The saved model's engine (a part's models may run on different ones).
    builds = engine_builds(run['engine'], data, hw['system'])
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
        # What failed before no longer holds, however the part came up (Başlat, the Hub's own start after a
        # restart or an update): dropped here, the one place, so no page has a rule of its own for it.
        if change and change.get('error'):  # A start that failed (or timed out).
            (run_dir(part['id']) / 'transition.json').unlink(missing_ok=True)
            change = None
        if current and current.get('action') == 'install' and current.get('phase') == 'error':
            (home() / 'jobs' / f"{part['id']}.json").unlink(missing_ok=True)
            current = None
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
               'extra_name': extra[0], 'main_files': item['files'], 'extra_file': item.get('mmproj'),
               'context_max': (meta.get('context') or CONTEXT_FALLBACK) if part.get('runner') != 'python' else None,
               'dims': meta.get('dims') if part['id'] == 'embedding' else None,
               'source': source_page(item)}
        # Its engine, every model its own (TTS: OmniVoice's or VoxCPM2's): what the page shows for every hardware
        # the moment this model is picked, not the saved model's.
        row['engines'] = engine_facts(item.get('engine') or part['engine'], part, hw, data)
        row['memory'] = memory_facts(part, item, meta)
        if item.get('user'):
            row.update(complete=item['complete'], checklist=item['checklist'], copying=item['copying'],
                       problems=item['problems'], path=str(folder))
        models.append(row)
    note = None if builds else 'Bu işletim sistemi için kurulum henüz yok (şimdilik yalnızca Windows).'
    if not python_fits(run['engine'], data):
        known = ', '.join(data['engines'][run['engine']]['pythons'])
        note = f'Bu parçanın paketleri Python {known} için var; bu Hub Python {_python()} ile çalışıyor.'
    kinds = USER_KINDS.get(part['id'])
    return {**base, 'installable': bool(builds and settings['hardware'] in builds),
            'note': note,
            'state': state, 'enabled': bool(entry.get('enabled')), 'paused': bool(entry.get('paused')),
            # The saved choice (what Başlat, Kur or Uygula uses).
            'model': model['id'] if model else None, 'settings': settings,
            'vision_off': _vision_off(entry),
            'engines': engine_facts(run['engine'], part, hw, data),
            'engine_label': engine_label(run['engine'], settings['hardware'], data),
            'models': models, 'advanced': advanced(part, hw, model),
            'job': current, 'error': (change or {}).get('error'),
            # What the service runs with (the page compares the choice with it: "not applied yet").
            'applied': applied(part['id']) if state in ('running', 'restarting') else None,
            # The engines the user can bring a model for (one not in Orion yet is shown, not offered).
            'user': {'kinds': [{**kind, 'ready': kind['engine'] in part_engines(part)} for kind in kinds],
                     'folder': str(user_dir(part['id']))} if kinds else None}


ENGINE_LABELS = {'llama.cpp': 'llama.cpp', 'whisper.cpp': 'whisper.cpp', 'omnivoice-gguf': 'OmniVoice',
                 'voxcpm2-gguf': 'VoxCPM2'}


def engine_label(engine, hardware, data=None):
    """'llama.cpp b11026': the engine a part runs on and the version of the folder it uses (an earlier version while
    the catalog's one downloads), None when no engine is there."""
    folder = engine_in_use(engine, hardware, data)
    try:
        version = (folder / '.complete').read_text(encoding='utf-8').strip()
    except (OSError, TypeError):
        return None
    # The catalog's versions repeat the engine's name ('voxcpm2-v1.0.3', 'tts-v1.1.0'): show the number.
    version = re.sub(r'^(?:voxcpm2|tts)-', '', version)
    return f'{ENGINE_LABELS.get(engine, engine)} {version}'


def engine_facts(engine, part, hw, data):
    """For every hardware the part can run on here: whether `engine` is installed for it and its download (MB)."""
    return {choice['id']: {'ready': engine_ready(engine, choice['id'], data),
                           'mb': round((engine_size(engine, choice['id'], data) or 0) / MB)}
            for choice in choices(part, hw)}


def status():
    data = catalog()
    prefetch_headers(data)
    hw = hardware()
    config = read_config()
    from .parts_location import facts
    return {'system': hw['system'], 'hardware': hw, 'parts': [part_status(p, hw, config, data) for p in data['parts']],
            'location': facts()}


# --- Actions (the Hub API calls these) -----------------------------------------------------------------------

def _spawn(argv, module='services.shared.local_parts'):
    """A job/serve process of its own: closing the window or ending a request never ends it."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING='utf-8')
    (home() / 'jobs').mkdir(parents=True, exist_ok=True)
    log = (home() / 'jobs' / 'jobs.log').open('ab')
    try:
        return subprocess.Popen([sys.executable, '-B', '-m', module, *argv], cwd=str(ROOT),
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
    if busy(part_id):
        raise PartError('Bu bileşende bir iş zaten sürüyor.')
    _not_moving()
    current, _ = effective(part, hw)
    chosen = model_spec(part, model) if model else current
    if chosen is None:
        raise PartError('Önce bir model seç.')
    engine = for_model(part, chosen, data)['engine']
    if not python_fits(engine, data):
        raise PartError('Bu parçanın paketleri bu Python sürümü için yok.')
    if not engine_builds(engine, data, hw['system']):
        raise PartError('Bu işletim sistemi için kurulum henüz yok.')
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
    elif action not in ('download', 'engine'):
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


def select(part_id, *, model=None, settings=None, vision_off=None):
    """Save the chosen model and settings, and which models have their extra file off (`vision_off`, each
    model its own). Nothing restarts: a running part keeps what it runs until the user presses Uygula
    (`apply_choice`); a stopped one takes them when it starts, a new one when it is installed."""
    data = catalog()
    part = spec(part_id, data)
    hw = hardware()
    values = {}
    if model is not None:
        values['model'] = model_spec(part, model)['id']
    known = {item['id'] for item in part['models'] if item.get('mmproj') or item.get('user')}  # Ones with a switch.
    if vision_off is not None:
        values['vision_off'] = sorted({str(item) for item in vision_off} & known)
    if settings is not None and 'vision' in settings:
        # The chosen model's switch (an older page): kept for that model.
        entry = read_config().get(part_id, {})
        chosen = values.get('model') or (effective(part, hw)[0] or {}).get('id')
        off = set(values['vision_off'] if 'vision_off' in values else _vision_off(entry))
        off.discard(chosen) if settings['vision'] else off.add(chosen)
        values['vision_off'] = sorted(off & known)
        settings = {key: value for key, value in settings.items() if key != 'vision'}
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
    """Everything `model` needs with `settings` is here: its files and an engine for that hardware (an earlier
    version runs while the catalog's one downloads)."""
    return downloaded(part, model, settings) and engine_in_use(for_model(part, model, data)['engine'],
                                                               settings['hardware'], data) is not None


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
    if extra:
        # Its extra file is gone: that model's choice stops asking for it (the others keep theirs).
        write_part_config(part_id, vision_off=sorted({*_vision_off(entry), model_id}))
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
    # An earlier failed start can leave its transition error visible after the user stops the part.
    # Dismissing that start error is part of an explicit stop; a failure in this stop gets its own
    # transition record below and remains visible.
    old_transition = transition(part_id)
    if old_transition and old_transition.get('error'):
        (run_dir(part_id) / 'transition.json').unlink(missing_ok=True)
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
        return value  # Shown until the part runs (part_status drops it), is stopped, or its choice changes.
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
                # Own names: `path` (the transition record) is read again by mine() below.
                models = models_dir(part_id)
                for item in (models.iterdir() if models.is_dir() else []):
                    # The user's own models and cloned voices go only when the user asked for that too.
                    if item.name not in USER_DATA_DIRS or entry.get('purge_user'):
                        shutil.rmtree(item, ignore_errors=True)
                if models.is_dir() and not any(models.iterdir()):
                    models.rmdir()
                for unused in unused_engines(part):
                    (unused / '.complete').unlink(missing_ok=True)  # Never "ready" if a file is held.
                    shutil.rmtree(unused, ignore_errors=True)
                write_part_config(part_id, purge=False, purge_user=False)
            if wanted == 'running':
                model, settings = effective(part, hardware())
                if model is None or not downloaded(part, model, settings):
                    raise MissingFiles('Modelin dosyaları eksik; indir ve başlat ile tamamla')
                if engine_in_use(for_model(part, model)['engine'], settings['hardware']) is None:
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
    """The folders of `part`'s engines (every hardware, every engine one of its models runs on) that no other
    installed part uses: an engine is shared (memory and the local LLM both run llama.cpp), so it goes only with
    the last part that needs it."""
    data = data or catalog()
    hw = hardware()
    config = read_config()
    mine = part_engines(part)
    used = set()
    for other in data['parts']:
        if other['id'] == part['id'] or not other.get('engine'):
            continue
        entry = config.get(other['id'], {})
        if entry.get('enabled') or busy(other['id']):
            model, settings = effective(other, hw, config)
            used.add(engine_dir(for_model(other, model, data)['engine'], settings['hardware'], data))
        running = applied(other['id'])
        if running and answers(int(os.getenv(other['port_key']) or other['port'])):
            model = next((m for m in other.get('models', []) if m['id'] == running.get('model')), None)
            used.add(engine_dir(for_model(other, model, data)['engine'], running['settings']['hardware'], data))
    used |= {engine_in_use(engine, hw_id, data) for engine, hw_id in list(_in_use_by_others(used, mine, data))}
    folders = [path for engine in mine for hw_id in (engine_builds(engine, data) or {})
               for path in [engine_dir(engine, hw_id, data)] + older_engines(engine, hw_id, data)]
    return [folder for folder in folders if folder.is_dir() and folder not in used]


def _in_use_by_others(used, engines, data):
    """(engine, hardware) of `engines` whose catalog folder another part uses: the earlier version it may be
    running on stays too."""
    for engine in engines:
        for hw_id in engine_builds(engine, data) or {}:
            if engine_dir(engine, hw_id, data) in used:
                yield engine, hw_id


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
    env.pop(OLD_ENGINE, None)
    if part_id in _old_engine:
        env[OLD_ENGINE] = '1'
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


_tidied = []


def tidy(data):
    """What an Orion update left behind, once per Hub start (before its parts start): the engine folders the
    catalog no longer names (another version, or an engine gone: faster-whisper's 1.4 GB went with whisper.cpp)
    and the folders of the models it retired (`retired_models`, the old model's folder by its id). A file still
    open stays and goes at a later start."""
    if _tidied:
        return
    _tidied.append(True)
    current = set()
    for name, entry in data['engines'].items():
        current |= {f'{name}-{entry["version"]}{tag}' for tag in _engine_tags(entry)}
    # An earlier version of an engine still in the catalog stays until a part has run the new one (`_drop_older`).
    current |= {path.parent.name for name in data['engines'] for hw_id in (engine_builds(name, data) or {})
                for path in older_engines(name, hw_id, data)}
    folder = home() / 'engines'
    # An `unpack-` folder is a job's scratch space; with no job running it is what an interrupted one left
    # (an Orion update restarts the Hub mid-install): hundreds of MB that nothing will ever use.
    jobs_running = any(busy(part['id']) for part in data['parts'])
    for path in folder.iterdir() if folder.is_dir() else []:
        scratch = path.name.startswith('unpack-')
        if path.is_dir() and path.name not in ('downloads', 'shared') and (
                (scratch and not jobs_running) or (not scratch and path.name not in current)):
            shutil.rmtree(path, ignore_errors=True)
    if not jobs_running:
        _drop_stale_downloads(data, folder / 'downloads')
        for part in data['parts']:
            leftovers = models_dir(part['id']) / '.downloads'
            for path in leftovers.iterdir() if leftovers.is_dir() else []:
                if path.name.startswith('unpack-') or path.name.endswith('.part'):
                    shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(missing_ok=True)
    # A shared archive goes when no engine links to its files any more (and no job is putting one together).
    busy_unpacking = any(path.name.startswith('unpack-') for path in folder.iterdir()) if folder.is_dir() else True
    for store in (folder / 'shared').iterdir() if (folder / 'shared').is_dir() and not busy_unpacking else []:
        files = [path for path in store.rglob('*') if path.is_file() and path.name != '.complete']
        if all(path.stat().st_nlink <= 1 for path in files):
            shutil.rmtree(store, ignore_errors=True)
    for part in data['parts']:
        for old in part.get('retired_models', {}):
            shutil.rmtree(models_dir(part['id']) / old, ignore_errors=True)


def _drop_stale_downloads(data, folder):
    """Engine archives of jobs that never finished (killed with the Hub, an update, a power cut), once no job runs:
    a cut download starts over anyway, and a finished one stays only while a recipe in the catalog still names it
    (a whole archive for hardware the user never picked would otherwise sit there for good)."""
    wanted = {piece['url'].rsplit('/', 1)[-1] for entry in data['engines'].values()
              for builds in entry.get('windows', {}).values() for piece in builds.get('archives', []) + builds.get('wheels', [])}
    for path in folder.iterdir() if folder.is_dir() else []:
        if path.name.endswith('.part') or path.name not in wanted:
            path.unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass


def keep_running():
    """The Hub's owner process calls this at start and then now and then: every part the user installed runs
    (a crashed one comes back), none of the others does."""
    if moving():
        return  # The move stopped them; it starts them again from the new place.
    data = catalog()
    tidy(data)
    config = read_config()
    hw = hardware()
    for part in data['parts']:
        entry = config.get(part['id'], {})
        if (not part.get('engine') or not entry.get('enabled') or entry.get('paused') or busy(part['id'])
                or transition(part['id'])):
            continue
        model, settings = effective(part, hw, config)
        if model is None or not ready(part, model, settings, data):
            continue
        if answers(int(os.getenv(part['port_key']) or part['port'])):
            _silent.pop(part['id'], None)
            continue
        if _running_ready(part['id']):
            # Started and ready, yet not answering: hung (a crashed one has no living process). Restarting it
            # was a no-op before (its records said "running"), so it stayed hung for weeks. One missed answer
            # can be a busy moment; HUNG_PASSES in a row (minutes apart) is not.
            silent = _silent[part['id']] = _silent.get(part['id'], 0) + 1
            if silent < HUNG_PASSES:
                continue
            print(f'local part {part["id"]} runs but has not answered {silent} times; restarting it', flush=True)
            stop_part(part['id'])
        _silent.pop(part['id'], None)
        try:
            start_part(part['id'])
        except Exception as error:
            print(f'local part {part["id"]} did not start: {type(error).__name__}: {error}', flush=True)
            engine = for_model(part, model, data)['engine']
            if (part['id'] not in _old_engine and older_engines(engine, settings['hardware'], data)
                    and _engine_used(part['id']) == engine_dir(engine, settings['hardware'], data)):
                # The new engine version did not start: the earlier one runs until the Hub starts again.
                print(f'local part {part["id"]}: starting it on the earlier engine version', flush=True)
                _old_engine.add(part['id'])
                try:
                    start_part(part['id'])
                except Exception as again:
                    print(f'local part {part["id"]} did not start: {type(again).__name__}: {again}', flush=True)
    _update_engines(data, config, hw)


_old_engine = set()  # parts whose new engine version failed to start in this Hub run
_updating = set()  # (engine, hardware) whose new version a job downloaded, or tried to, in this Hub run


def _engine_used(part_id):
    """The engine folder the part's last start ran (`serve` writes it)."""
    try:
        text = (run_dir(part_id) / 'engine.txt').read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return Path(text) if text else None


def _update_engines(data, config, hw):
    """An Orion update raised an engine's version: the installed parts run on the earlier one while a job
    downloads the new one (this hardware's only); the next start runs the new one, and once a part answers on
    it the earlier one goes. Nothing restarts for it."""
    for part in data['parts']:
        if not part.get('engine') or not config.get(part['id'], {}).get('enabled'):
            continue
        model, settings = effective(part, hw, config)
        if model is None:
            continue
        engine, hw_id = for_model(part, model, data)['engine'], settings['hardware']
        if not older_engines(engine, hw_id, data):
            continue
        if engine_ready(engine, hw_id, data):
            if (part['id'] not in _old_engine and _engine_used(part['id']) == engine_dir(engine, hw_id, data)
                    and answers(int(os.getenv(part['port_key']) or part['port']))):
                _drop_older(engine, hw_id, data)
        elif (engine, hw_id) not in _updating and not busy(part['id']) and engine_recipe(engine, hw_id, data):
            _updating.add((engine, hw_id))
            try:
                start_job(part['id'], 'engine')
                print(f'local part {part["id"]}: downloading engine {engine} {data["engines"][engine]["version"]} '
                      f'({hw_id}); the earlier version runs until the next start', flush=True)
            except Exception as error:
                print(f'local part {part["id"]}: engine update did not start: {error}', flush=True)


def _drop_older(engine, hardware, data):
    """The earlier versions' folders, each only when nothing runs from it: Windows refuses to rename a folder
    with an open file in it, so a running engine is never half deleted."""
    for folder in older_engines(engine, hardware, data):
        trash = folder.with_name(f'{folder.name}.old-{os.getpid()}')
        try:
            os.replace(folder, trash)
        except OSError:
            continue
        shutil.rmtree(trash, ignore_errors=True)
        try:
            folder.parent.rmdir()  # The version folder, once no other hardware's engine is in it.
        except OSError:
            pass
        print(f'engine {engine}: removed the earlier version {folder.parent.name} ({hardware})', flush=True)


HUNG_PASSES = 3
_silent = {}  # part id -> passes of keep_running in a row that a ready part did not answer


def _running_ready(part_id):
    """The part's supervisor lives and reported its engine ready (not still loading a model)."""
    try:
        from .process_lifecycle import Runtime, living
        record = Runtime(run_dir(part_id), 'part').read()
        return bool(record and record['status'] == 'ready' and living(record['supervisor']))
    except Exception:
        return False


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
    engine = for_model(part, model, data)['engine'] if part.get('engine') else None
    used = engine_in_use(engine, settings['hardware'], data) if engine else None
    (folder / 'engine.txt').write_text(str(used or ''), encoding='utf-8')
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
        # Once a second: each look opens the job and process handles (several times a second cost a few
        # percent of a core per part for weeks).
        while not runtime.requested() and child.poll() is None:
            try:
                runtime.refresh_children()
            except Exception as error:  # A look that fails this second must not stop a serving engine.
                print(f'[!] Child records not refreshed: {error}', flush=True)
            time.sleep(1)


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
    opener = opener or public_opener()
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
    opener = public_opener()
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
    """Put the engine together in a scratch folder (Orion's bundled files, archives, wheels), then move it in.
    An archive is unpacked when it is a zip and goes in as it is otherwise."""
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
        for piece in recipe.get('archives', []):
            if _have_shared(piece):
                _link_tree(shared_dir(piece), unpacked)
        pieces = ([(piece, 'archive') for piece in recipe.get('archives', []) if not _have_shared(piece)]
                  + [(piece, 'wheel') for piece in recipe.get('wheels', [])])
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
            if piece.get('shared'):
                # Unpacked once for every engine that carries it; this engine gets links to its files.
                store = shared_dir(piece)
                scratch = Path(tempfile.mkdtemp(prefix='unpack-', dir=str(home() / 'engines')))
                try:
                    with zipfile.ZipFile(archive) as bundle:
                        bundle.extractall(scratch / 'files')
                    (scratch / 'files' / '.complete').write_text(piece['url'], encoding='utf-8')
                    store.parent.mkdir(parents=True, exist_ok=True)
                    shutil.rmtree(store, ignore_errors=True)
                    os.replace(scratch / 'files', store)
                finally:
                    shutil.rmtree(scratch, ignore_errors=True)
                _link_tree(store, unpacked)
            elif kind == 'wheel' or archive.suffix.lower() == '.zip':
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(unpacked / 'site' if kind == 'wheel' else unpacked)
            else:  # One file the engine needs, as its source publishes it (whisper.cpp's VAD model).
                shutil.copy2(archive, unpacked / archive.name)
        exe = data['engines'][part['engine']].get('exe') or ('llama-server' if part.get('runner', 'llama') == 'llama' else None)
        if exe and not any(unpacked.rglob(exe + '.exe' if os.name == 'nt' else exe)):
            raise PartError(f'İndirilen motorda {exe} yok.')
        (unpacked / '.complete').write_text(data['engines'][part['engine']]['version'], encoding='utf-8')
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            shutil.rmtree(final)
        os.replace(unpacked, final)
        # Only this engine's archives: another part's job may be downloading into the same folder.
        for piece, _ in pieces:
            (home() / 'engines' / 'downloads' / piece['url'].rsplit('/', 1)[-1]).unlink(missing_ok=True)
        try:
            (home() / 'engines' / 'downloads').rmdir()
        except OSError:
            pass
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _link_tree(source, target):
    """`source`'s files at the same places under `target`: hard links (no second copy on the disk), a copy where
    the disk cannot link."""
    for path in source.rglob('*'):
        if path.is_dir() or path.name == '.complete':
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        try:
            os.link(path, destination)
        except OSError:
            shutil.copy2(path, destination)


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
    part = for_model(part, model, data)  # The engine this model runs on, installed and started below.
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
        if action in ('install', 'engine'):
            _install_engine(part_id, part, settings, data, cancelled, step('engine'))
        if action == 'engine':  # A new engine version: the part keeps running and takes it at its next start.
            _update_job(part_id, phase='done', label='Motorun yeni sürümü indi; bir sonraki başlatmada kullanılacak.',
                        rate=0, finished=time.time())
            return 0
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
        if action == 'engine':
            detail = f'Motorun yeni sürümü indirilemedi; eski sürümle çalışmaya devam ediyor. ({detail})'
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
    job_parser.add_argument('kind', choices=['download', 'install', 'engine'])
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
