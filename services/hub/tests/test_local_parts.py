"""Bileşenler: optional local AI parts (services/shared/local_parts.py) and their API. Never downloads from the
internet, never touches a real installation: parts live under tmp_path, files come from a local HTTP server."""
import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.shared import local_parts as P

TWO_CARDS = {'system': 'windows', 'gpus': [
    {'index': 0, 'name': 'RTX 3060', 'vram_mb': 12288, 'vendor': 'nvidia'},
    {'index': 1, 'name': 'RTX 2060', 'vram_mb': 6144, 'vendor': 'nvidia'}],
    'choices': [{'id': 'cuda', 'label': 'x'}, {'id': 'vulkan', 'label': 'x'}, {'id': 'cpu', 'label': 'x'}],
    'recommended': 'cuda'}
NO_CARD = {'system': 'windows', 'gpus': [], 'choices': [{'id': 'cpu', 'label': 'x'}], 'recommended': 'cpu'}


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    """Every test, also one without `parts`, keeps its files (hardware cache, jobs) in tmp_path: a test once
    wrote its made-up graphics cards into the developer's own Parts cache."""
    monkeypatch.setenv('ORION_PARTS_HOME', str(tmp_path / 'Parts'))
    P._hardware.update(at=0, value=None)
    yield
    P._hardware.update(at=0, value=None)


@pytest.fixture
def parts(tmp_path, monkeypatch):
    monkeypatch.setenv('ORION_PARTS_HOME', str(tmp_path / 'Parts'))
    monkeypatch.setattr(P, 'hardware', lambda **kwargs: TWO_CARDS)
    monkeypatch.setattr(P, 'answers', lambda port, timeout=.5: False)
    monkeypatch.setattr(P, 'python_fits', lambda engine, data=None: True)  # Tests run on any Python.
    spawned = []
    monkeypatch.setattr(P, '_spawn', lambda argv: spawned.append(argv) or SimpleNamespace(pid=999999999))
    P.spawned = spawned
    return tmp_path / 'Parts'


def fake_engine(home, hardware='cuda'):
    folder = P.engine_dir('llama.cpp', hardware) / 'nested'
    folder.mkdir(parents=True)
    (folder / ('llama-server.exe' if os.name == 'nt' else 'llama-server')).write_bytes(b'')


def test_every_part_installs_here_with_its_own_settings(parts):
    status = P.status()
    by_id = {part['id']: part for part in status['parts']}
    assert set(by_id) == {'embedding', 'llm', 'stt', 'tts'}
    assert all(part['installable'] and part['state'] == 'absent' for part in by_id.values())
    assert by_id['embedding']['model'] == 'jina-v5-omni-nano'  # The recommended model, nothing installed yet.
    # Speech recognition's CUDA engine brings cuBLAS (~650 MB); Vulkan and the processor are small.
    assert by_id['stt']['model'] == 'whisper-small' and 600 < by_id['stt']['engines']['cuda']['mb'] < 700
    assert by_id['stt']['engines']['vulkan']['mb'] < 20 and by_id['stt']['engines']['cpu']['mb'] < 20
    keys = lambda part: [field['key'] for field in by_id[part]['advanced']]
    assert keys('llm') == ['hardware', 'gpu', 'context_size', 'parallel', 'vision']  # Two cards: which one.
    assert keys('stt') == ['hardware', 'gpu'] and keys('tts') == ['hardware', 'gpu']
    # Both speech parts run on CUDA, Vulkan or the processor, CUDA first on an NVIDIA card: next to each other on
    # one card, one on CUDA and the other on Vulkan starved both (docs/bilesenler.md).
    assert [c['id'] for c in by_id['tts']['advanced'][0]['options']] == ['cuda', 'vulkan', 'cpu']
    assert [c['id'] for c in by_id['stt']['advanced'][0]['options']] == ['vulkan', 'cuda', 'cpu']


def test_every_model_from_hugging_face_is_one_fixed_file_checked_by_its_sha256(parts):
    # Not /resolve/main/: a repository can replace a file there. A commit's file never changes, and each file
    # (the mmproj too) is checked after its download.
    for part in P.catalog()['parts']:
        for model in part['models']:
            if 'huggingface.co' not in (model.get('url') or ''):
                continue
            assert len(model['url'].rstrip('/').rsplit('/', 1)[1]) == 40, model['id']
            names = model['files'] + ([model['mmproj']] if model.get('mmproj') else [])
            assert set(model['sha256']) == set(names), model['id']
            assert all(len(value) == 64 for value in model['sha256'].values()), model['id']
    # The page shows where each model comes from: its repository's page, not the fixed commit.
    llm = next(part for part in P.status()['parts'] if part['id'] == 'llm')
    gemma = next(model for model in llm['models'] if model['id'] == 'gemma-4-e2b-it-q3-k-s')
    assert gemma['source'] == 'https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF'
    assert P.source_page({'user': True, 'url': gemma['source'] + '/resolve/main/'}) is None  # A user's own model.


def test_speech_services_run_on_orions_python_with_their_engine(parts):
    for part_id in ('stt', 'tts'):
        folder = P.engine_dir(P.spec(part_id)['engine'], 'cpu')
        (folder / 'site').mkdir(parents=True)
        (folder / '.complete').write_text('x')
    stt = P.spec('stt')
    small = P.model_spec(stt, 'whisper-small')
    argv, env, port = P.command(stt, small, {'hardware': 'cpu', 'gpu': 'all'})
    assert port == 9009 and argv[1:4] == ['-B', '-m', 'services.shared.part_python']
    assert argv[4].endswith('api.py') and argv[5].endswith('site')
    # whisper-server's model file and folder; where it runs said as it is (cuda, vulkan, cpu).
    assert env['WHISPER_MODEL'] == str(P.models_dir('stt') / 'whisper-small' / 'ggml-small-q8_0.bin')
    assert env['WHISPER_BIN_DIR'] == str(P.engine_dir('whisper.cpp', 'cpu')) and env['ORION_HW_ID'] == 'cpu'
    tts = P.spec('tts')
    argv, env, port = P.command(tts, P.model_spec(tts, 'omnivoice-gguf'), {'hardware': 'cpu', 'gpu': 'all'})
    assert port == 8810 and env['ENGINE_NAME'] == 'omnivoice-gguf'
    # Both files named (a user's own model may call them anything).
    assert env['MODEL_PATH'].endswith('omnivoice-base-Q8_0.gguf') and env['CODEC_PATH'].endswith('omnivoice-tokenizer-Q8_0.gguf')
    assert env['OMNIVOICE_BIN_DIR'] == str(P.engine_dir('omnivoice-gguf', 'cpu'))
    # Vulkan: its own whole set (the bundled ggml.dll needs ggml-cuda.dll), the card picked the Vulkan way.
    recipe = P.engine_recipe('omnivoice-gguf', 'vulkan')
    assert recipe is None if P.system() != 'windows' else 'bundled' not in recipe and [a['url'].rsplit('/', 1)[-1] for a in recipe['archives']] == ['omnivoice-gguf-windows-vulkan.zip']
    (P.engine_dir('omnivoice-gguf', 'vulkan')).mkdir(parents=True)
    (P.engine_dir('omnivoice-gguf', 'vulkan') / '.complete').write_text('x')
    argv, env, port = P.command(tts, P.model_spec(tts, 'omnivoice-gguf'), {'hardware': 'vulkan', 'gpu': '1'})
    assert env['OMNIVOICE_BIN_DIR'] == str(P.engine_dir('omnivoice-gguf', 'vulkan'))
    assert env['GGML_VK_VISIBLE_DEVICES'] == '1' and env.get('CUDA_VISIBLE_DEVICES') == os.environ.get('CUDA_VISIBLE_DEVICES')


def test_another_system_shows_the_cards_but_cannot_install_yet(parts, monkeypatch):
    monkeypatch.setattr(P, 'hardware', lambda **kwargs: {**NO_CARD, 'system': 'linux'})
    embedding = next(part for part in P.status()['parts'] if part['id'] == 'embedding')
    assert not embedding['installable'] and 'Windows' in embedding['note']
    with pytest.raises(P.PartError):
        P.start_job('embedding', 'install')


def test_graphics_cards_are_read_from_nvidia_smi():
    def run(argv, **kwargs):
        if argv[0] == 'nvidia-smi' and 'compute_cap' in argv[1]:
            return SimpleNamespace(returncode=0, stdout='0, 8.6\n1, 7.5\n')
        if argv[0] == 'nvidia-smi':
            return SimpleNamespace(returncode=0, stdout='0, NVIDIA GeForce RTX 3060, 12288, 581.0\n1, RTX 2060, 6144, 581.0\n')
        return SimpleNamespace(returncode=0, stdout='NVIDIA GeForce RTX 3060\nIntel(R) UHD Graphics 770\n')
    hw = P.hardware(refresh=True, run=run)
    if hw['system'] != 'windows':
        pytest.skip('Windows hardware layout')
    assert [gpu['name'] for gpu in hw['gpus']] == ['NVIDIA GeForce RTX 3060', 'RTX 2060', 'Intel(R) UHD Graphics 770']
    assert [gpu.get('compute') for gpu in hw['gpus']] == [8.6, 7.5, None]
    # Vulkan first (the default); speech starts on CUDA: the speech voice ran faster on CUDA than on Vulkan on an
    # RTX 3060 Ti, and recognition goes where the voice is (docs/bilesenler.md).
    assert [choice['id'] for choice in hw['choices']] == ['vulkan', 'cuda', 'cpu'] and hw['recommended'] == 'vulkan'
    P._hardware.update(at=__import__('time').monotonic(), value=hw)
    assert [c['id'] for c in P.choices(P.spec('stt'), P._labelled(hw))] == ['vulkan', 'cuda', 'cpu']
    assert [c['id'] for c in P.choices(P.spec('tts'), P._labelled(hw))] == ['cuda', 'vulkan', 'cpu']
    # An AMD or Intel card alone: the speech voice goes to it on Vulkan.
    amd = P.hardware(refresh=True, run=lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout='AMD Radeon RX 7600\n')
                     if argv[0] != 'nvidia-smi' else SimpleNamespace(returncode=1, stdout=''))
    assert [c['id'] for c in P.choices(P.spec('tts'), amd)] == ['vulkan', 'cpu']
    assert [c['id'] for c in P.choices(P.spec('stt'), amd)] == ['vulkan', 'cpu']
    assert P.default_settings(P.spec('tts'), None, amd)['hardware'] == 'vulkan'
    nothing = P.hardware(refresh=True, run=lambda argv, **kwargs: SimpleNamespace(returncode=1, stdout=''))
    assert nothing['recommended'] == 'cpu'


def card(name, compute, index=0):
    gpu = {'index': index, 'name': name, 'vram_mb': 6144, 'vendor': 'nvidia'}
    return gpu if compute is None else {**gpu, 'compute': compute}


def on(*gpus):
    return {'system': 'windows', 'gpus': list(gpus), 'recommended': 'vulkan',
            'choices': [{'id': c, 'label': 'x'} for c in ('vulkan', 'cuda', 'cpu')]}


def ids(part_id, hw):
    return [c['id'] for c in P.choices(P.spec(part_id), hw)]


def test_cuda_is_offered_only_where_the_card_runs_that_engine():
    if P.system() != 'windows':
        pytest.skip('Windows builds')
    # Each CUDA build's lowest compute capability (docs/bilesenler.md says where each comes from).
    assert [P.cuda_floor(e) for e in ('llama.cpp', 'omnivoice-gguf', 'whisper.cpp')] == [5.0, 7.5, 5.0]
    rtx3060 = on(card('NVIDIA GeForce RTX 3060', 8.6))
    old = {part: ids(part, rtx3060) for part in ('embedding', 'llm', 'stt', 'tts')}
    assert old['tts'] == ['cuda', 'vulkan', 'cpu'] and old['stt'] == ['vulkan', 'cuda', 'cpu'] and 'cuda' in old['llm']
    # A GTX 1060 (6.1): llama.cpp and whisper.cpp still have kernels for it, the speech voice does not (Vulkan,
    # else the processor).
    gtx1060 = on(card('NVIDIA GeForce GTX 1060 6GB', 6.1))
    assert ids('embedding', gtx1060) == old['embedding'] and ids('llm', gtx1060) == old['llm']
    assert ids('tts', gtx1060) == ['vulkan', 'cpu'] and ids('stt', gtx1060) == old['stt']
    assert P.default_settings(P.spec('tts'), None, gtx1060)['hardware'] == 'vulkan'
    assert P.default_settings(P.spec('stt'), None, gtx1060) == {'hardware': 'vulkan', 'gpu': 'all'}
    # A Kepler card (3.5) is below even llama.cpp's and whisper.cpp's floor.
    assert ids('stt', on(card('NVIDIA GeForce GTX 780', 3.5))) == ['vulkan', 'cpu']
    # A saved CUDA choice moves too (effective()).
    _, settings = P.effective(P.spec('tts'), gtx1060, {'tts': {'settings': {'hardware': 'cuda'}}})
    assert settings['hardware'] == 'vulkan'
    # Capability unknown (a driver without the field, or hardware.json from before): the card is taken to fit.
    assert ids('tts', on(card('NVIDIA GeForce GTX 1060 6GB', None))) == ['cuda', 'vulkan', 'cpu']
    # Two cards: the card field lists only the ones that part's CUDA build runs on.
    both = on(card('NVIDIA GeForce RTX 3060', 8.6), card('NVIDIA GeForce GTX 1060 6GB', 6.1, index=1))
    gpu_ids = lambda part: [o['id'] for f in P.advanced(P.spec(part), both) if f['key'] == 'gpu' for o in f['options']]
    assert gpu_ids('llm') == ['all', '0', '1'] and gpu_ids('tts') == []  # One card left: nothing to pick.
    assert ids('tts', both) == ['cuda', 'vulkan', 'cpu']
    _, settings = P.effective(P.spec('tts'), both, {'tts': {'settings': {'hardware': 'cuda', 'gpu': '1'}}})
    assert settings['gpu'] == 'all'
    _, settings = P.effective(P.spec('llm'), both, {'llm': {'settings': {'hardware': 'cuda', 'gpu': '1'}}})
    assert settings['gpu'] == '1'


def test_an_old_driver_without_compute_cap_still_lists_its_cards():
    def run(argv, **kwargs):
        if 'compute_cap' in argv[1]:
            return SimpleNamespace(returncode=2, stdout='Field "compute_cap" is not a valid field to query.\n')
        return SimpleNamespace(returncode=0, stdout='0, NVIDIA GeForce GTX 1060 6GB, 6144, 472.12\n')
    assert P._nvidia(run) == [{'index': 0, 'name': 'NVIDIA GeForce GTX 1060 6GB', 'vram_mb': 6144, 'vendor': 'nvidia'}]


def test_the_whole_model_goes_to_the_card_and_settings_stay_inside_their_limits(parts):
    fake_engine(parts)
    llm = P.spec('llm')
    gemma = P.model_spec(llm, 'gemma-4-e2b-it-q3-k-s')
    on_card, _, _ = P.command(llm, gemma, {**P.default_settings(llm, gemma, TWO_CARDS), 'hardware': 'cuda'})
    assert on_card[on_card.index('-ngl') + 1] == str(P.ALL_LAYERS)
    fake_engine(parts, 'cpu')
    on_cpu, _, _ = P.command(llm, gemma, {**P.default_settings(llm, gemma, TWO_CARDS), 'hardware': 'cpu'})
    assert on_cpu[on_cpu.index('-ngl') + 1] == '0'
    # An older saved layer count is gone from the settings.
    P.write_part_config('llm', settings={'gpu_layers': 24})
    assert 'gpu_layers' not in P.effective(llm, TWO_CARDS)[1]
    assert P.clean_settings(llm, {'gpu': '1', 'context_size': '4096'}, TWO_CARDS) == {'gpu': '1', 'context_size': 4096}
    for wrong in ({'context_size': 100}, {'parallel': 9}, {'hardware': 'metal'}, {'shell': 'calc.exe'}, {'parallel': 'x'}):
        with pytest.raises(P.PartError):
            P.clean_settings(llm, wrong, TWO_CARDS)


def test_removing_can_take_the_models_after_the_service_stopped(parts, monkeypatch):
    stopped = []
    monkeypatch.setattr(P, 'stop_part', lambda part_id: stopped.append(part_id))
    folder = P.models_dir('embedding') / 'jina-embeddings'
    folder.mkdir(parents=True)
    (folder / 'model.gguf').write_bytes(b'm')
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    P.remove('embedding')
    assert P.apply('embedding') == 0 and folder.is_dir()  # Kept unless asked.
    P.write_part_config('embedding', enabled=True)
    P.remove('embedding', delete_models=True)
    assert P.apply('embedding') == 0 and stopped == ['embedding', 'embedding']
    assert not P.models_dir('embedding').exists() and not P.read_config()['embedding']['purge']


def test_the_engine_command_follows_the_part_model_and_settings(parts):
    fake_engine(parts)
    embedding = P.spec('embedding')
    model = P.model_spec(embedding, 'jina-v5-omni-nano')
    argv, env, port = P.command(embedding, model, {**P.default_settings(embedding, model, TWO_CARDS), 'gpu': '1'})
    assert port == 8086 and '--embedding' in argv and argv[-2:] == ['--pooling', 'last']
    assert argv[argv.index('-ngl') + 1] == str(P.ALL_LAYERS) and env['CUDA_VISIBLE_DEVICES'] == '1'
    assert argv[argv.index('--host') + 1] == '127.0.0.1'  # Never on the network.
    llm = P.spec('llm')
    gemma = P.model_spec(llm, 'gemma-4-e2b-it-q3-k-s')
    argv, env, _ = P.command(llm, gemma, {**P.default_settings(llm, gemma, TWO_CARDS), 'hardware': 'cuda'})
    assert argv[argv.index('--mmproj') + 1].endswith('mmproj-BF16.gguf')  # Gemma reads images too.
    assert argv[argv.index('--flash-attn') + 1] == 'auto'


class Files(BaseHTTPRequestHandler):
    content = bytes(range(256)) * 4096   # 1 MB
    def do_GET(self):
        start = 0
        if self.headers.get('Range'):
            start = int(self.headers['Range'].split('=')[1].rstrip('-'))
            self.send_response(206)
        else:
            self.send_response(200)
        body = self.content[start:]
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Files)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}/'
    httpd.shutdown()


def test_a_cut_file_starts_over_and_a_finished_one_is_kept(tmp_path, server):
    target = tmp_path / 'model.gguf'
    target.with_name('model.gguf.part').write_bytes(b'x' * 300000)  # A cut (maybe corrupt) earlier try.
    seen = []
    P.download(server + 'model.gguf', target, report=lambda done, total: seen.append((done, total)), cancelled=lambda: False)
    assert target.read_bytes() == Files.content
    assert seen[0] == (0, len(Files.content))  # Fetched whole, nothing appended to the cut file.
    # A piece that finished on an earlier try is not fetched again (a two-piece engine cancelled in its
    # second piece continues there).
    asked = []
    P.download(server + 'model.gguf', target, report=lambda done, total: asked.append(done), cancelled=lambda: False,
               opener=SimpleNamespace(open=lambda *a, **k: pytest.fail('downloaded again')))
    assert asked == [len(Files.content)]
    with pytest.raises(P.Cancelled):
        P.download(server + 'x.gguf', tmp_path / 'x.gguf', report=lambda *a: None, cancelled=lambda: True)
    assert not (tmp_path / 'x.gguf').exists() and not (tmp_path / 'x.gguf.part').exists()  # Nothing half left.


def served(server, monkeypatch):
    """The catalog with every model's files on the test server, each with that file's own SHA-256."""
    data = P.catalog()
    digest = hashlib.sha256(Files.content).hexdigest()
    for part in data['parts']:
        for model in part.get('models', []):
            model['url'] = server
            model['sha256'] = {name: digest for name in model.get('files', []) + [model.get('mmproj')] if name}
    monkeypatch.setattr(P, 'catalog', lambda: data)
    return data


def test_a_model_file_that_is_not_the_one_named_is_not_kept(parts, server, monkeypatch):
    data = served(server, monkeypatch)
    jina = next(m for part in data['parts'] for m in part['models'] if m['id'] == 'jina-v5-omni-nano')
    jina['sha256'] = {name: '0' * 64 for name in jina['files']}  # The repository's file changed.
    assert P.run_job('embedding', 'download', 'jina-v5-omni-nano') != 0
    assert not (P.models_dir('embedding') / jina['folder'] / jina['files'][0]).exists()


def test_a_download_job_fetches_the_model_and_reports_it(parts, server, monkeypatch):
    data = served(server, monkeypatch)
    assert P.run_job('embedding', 'download', 'jina-v5-omni-nano') == 0
    record = P.job('embedding')
    assert record['phase'] == 'done' and record['bytes'] == len(Files.content)
    embedding = next(part for part in P.status()['parts'] if part['id'] == 'embedding')
    assert next(m for m in embedding['models'] if m['id'] == 'jina-v5-omni-nano')['main_there']
    assert embedding['state'] == 'absent'  # Downloaded is not installed: the card offers Kur.
    # The chosen model can go too while nothing runs it (the card then offers to download it again).
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    P.delete_model('embedding', 'jina-v5-omni-nano')
    assert P.spawned == []  # Nothing to stop: gone at once.
    assert not P.downloaded(P.spec('embedding'), P.model_spec(P.spec('embedding'), 'jina-v5-omni-nano'))


def test_a_job_whose_process_is_gone_is_shown_as_cut(parts):
    (parts / 'jobs').mkdir(parents=True)
    (parts / 'jobs' / 'llm.json').write_text(json.dumps({'phase': 'model', 'pid': 999999999}), encoding='utf-8')
    assert P.job('llm')['phase'] == 'interrupted' and not P.busy('llm')


def test_only_this_pcs_window_changes_parts(parts, monkeypatch):
    from orion.api import parts_routes
    from orion.api.auth_routes import get_current_user
    app = FastAPI()
    app.include_router(parts_routes.router)
    app.dependency_overrides[get_current_user] = lambda: 'alice'
    client = TestClient(app)
    monkeypatch.setattr(parts_routes, 'local_control', lambda request: False)
    assert client.get('/api/v1/parts').json()['can_manage'] is False
    assert client.post('/api/v1/parts/embedding/install', json={}).status_code == 403
    assert client.put('/api/v1/parts/llm', json={'settings': {'parallel': 2}}).status_code == 403
    monkeypatch.setattr(parts_routes, 'local_control', lambda request: True)
    response = client.put('/api/v1/parts/llm', json={'settings': {'context_size': 500000}})
    assert response.status_code == 400 and 'arasında' in response.json()['detail']
    assert client.put('/api/v1/parts/llm', json={'model': 'miniplm-qwen-200m'}).status_code == 200
    assert P.read_config()['llm']['model'] == 'miniplm-qwen-200m'


def test_a_stopped_part_stays_off_until_started(parts, monkeypatch):
    started = []
    monkeypatch.setattr(P, 'start_part', lambda part_id: started.append(part_id))
    monkeypatch.setattr(P, 'stop_part', lambda part_id: True)
    monkeypatch.setattr(P, 'downloaded', lambda part, model, settings=None: True)
    fake_engine(parts)
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    P.stop('embedding')
    assert P.spawned[-1][:3] == ['apply', 'embedding', '--id']  # Stopping happens in its own process, not in the request.
    P.keep_running()
    assert started == [] and next(p for p in P.status()['parts'] if p['id'] == 'embedding')['state'] == 'stopped'
    P.write_part_config('embedding', paused=False)
    P.keep_running()
    assert started == ['embedding']


def test_the_hardware_is_read_once_for_every_hub_process(parts, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv('ORION_PARTS_HOME', str(parts))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv[0])
        return SimpleNamespace(returncode=1, stdout='')
    P.hardware(refresh=True, run=run)
    P._hardware.update(at=0, value=None)  # Another process: nothing in memory.
    P.hardware(run=run)
    assert len(calls) <= 2 and (parts / 'hardware.json').is_file()


def zip_bytes(files):
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


class Archives(BaseHTTPRequestHandler):
    files = {}
    def do_GET(self):
        body = self.files.get(self.path.lstrip('/'))
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass


def test_an_engine_is_put_together_from_bundled_files_and_checked_wheels(parts, monkeypatch, tmp_path):
    """No part's engine is Python packages today (speech recognition was, on faster-whisper); the way stays."""
    import hashlib
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Archives)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/'
    wheel = zip_bytes({'speech/__init__.py': 'x = 1\n'})
    model = zip_bytes({'whisper-small/ggml-small-q8_0.bin': 'm'})
    Archives.files = {'speech.whl': wheel, 'small.zip': model, 'bad.whl': b'not this'}
    bundled = tmp_path / 'bundled'
    bundled.mkdir()
    (bundled / 'tool.dll').write_text('dll')
    data = P.catalog()
    monkeypatch.setattr(P, 'ROOT', tmp_path)
    data['engines']['speech'] = {'version': 'test', 'pythons': {P._python(): {'windows': {
        'cpu': {'bundled': 'bundled', 'wheels': [
            {'url': base + 'speech.whl', 'sha256': hashlib.sha256(wheel).hexdigest(), 'size': len(wheel)}]}}}}}
    stt = next(part for part in data['parts'] if part['id'] == 'stt')
    stt['engine'] = 'speech'
    small = next(m for m in stt['models'] if m['id'] == 'whisper-small')
    small.update(archive=base + 'small.zip', archive_sha256=hashlib.sha256(model).hexdigest())
    monkeypatch.setattr(P, 'catalog', lambda: data)
    monkeypatch.setattr(P, '_catalog_file', lambda: data)  # Where the CUDA floor is read.
    monkeypatch.setattr(P, 'hardware', lambda **kwargs: {**NO_CARD, 'system': 'windows'})
    monkeypatch.setattr(P, 'start_part', lambda part_id: True)
    monkeypatch.setattr(P, 'stop_part', lambda part_id: True)
    try:
        assert P.run_job('stt', 'install', 'whisper-small') == 0, P.job('stt')
        folder = P.engine_dir('speech', 'cpu')
        assert (folder / 'tool.dll').is_file() and (folder / 'site/speech/__init__.py').is_file()
        assert P.downloaded(stt, small) and P.read_config()['stt']['enabled']
        # A piece that does not match its SHA-256 is refused and nothing is installed.
        data['engines']['speech']['pythons'][P._python()]['windows']['cuda'] = {
            'wheels': [{'url': base + 'bad.whl', 'sha256': '0' * 64}]}
        P.write_part_config('stt', settings={'hardware': 'cuda'}, enabled=False)
        monkeypatch.setattr(P, 'hardware', lambda **kwargs: TWO_CARDS)
        assert P.run_job('stt', 'install', 'whisper-small') == 1
        assert 'SHA-256' in P.job('stt')['error'] and not P.engine_ready('speech', 'cuda')
        # An engine that names its program (whisper.cpp: whisper-server) is refused without it.
        data['engines']['speech'] = {'version': 'test2', 'exe': 'whisper-server', 'windows': {
            'cuda': {'archives': [{'url': base + 'speech.whl', 'sha256': hashlib.sha256(wheel).hexdigest()}]}}}
        assert P.run_job('stt', 'install', 'whisper-small') == 1
        assert 'whisper-server yok' in P.job('stt')['error'] and not P.engine_ready('speech', 'cuda')
    finally:
        httpd.shutdown()


def pe_imports(path):
    """The DLL names a Windows exe or DLL imports (its import table)."""
    import struct
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    sections, optional_size = struct.unpack_from('<H', data, pe + 6)[0], struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    directories = optional + (112 if struct.unpack_from('<H', data, optional)[0] == 0x20b else 96)
    table = [struct.unpack_from('<IIII', data, optional + optional_size + 40 * i + 8) for i in range(sections)]
    offset = lambda rva: next(raw + rva - start for size, start, _, raw in table if start <= rva < start + size)
    name = lambda rva: data[offset(rva):data.index(b'\0', offset(rva))].decode()
    entry, names = offset(struct.unpack_from('<I', data, directories + 8)[0]), []
    while struct.unpack_from('<I', data, entry + 12)[0]:
        names.append(name(struct.unpack_from('<I', data, entry + 12)[0]))
        entry += 20
    return names


def local_archive(piece):
    """A zip of a recipe that is already on this PC with the catalog's SHA-256: the TTS zips the developer packaged
    (services/tts/omnivoice-gguf, services/tts/voxcpm2-gguf) or any in ORION_ENGINE_ARCHIVES. Tests never download
    them."""
    name = piece['url'].rsplit('/', 1)[-1]
    places = [P.ROOT / 'services/tts/omnivoice-gguf', P.ROOT / 'services/tts/voxcpm2-gguf'] + [
        Path(folder) for folder in os.environ.get('ORION_ENGINE_ARCHIVES', '').split(os.pathsep) if folder]
    for place in places:
        path = place / name
        if path.is_file() and piece.get('sha256') and P.sha256(path) == piece['sha256']:
            return path
    return None


@pytest.mark.skipif(os.name != 'nt', reason='Windows engines')
def test_an_engine_brings_every_dll_it_needs(tmp_path):
    """An engine put together from its recipe starts on a PC that has only Windows and the card's driver: the TTS
    CPU set once imported ggml-cuda.dll, which only the developer's bin/ had, and the TTS CUDA zip needed
    cudart64_12.dll and cuBLAS, which only a PC with the CUDA Toolkit had (0xC0000135 both times). A recipe with
    zips is checked when all of them are here (`local_archive`)."""
    import zipfile
    system32 = os.path.join(os.environ['SYSTEMROOT'], 'System32')
    driver = {'nvcuda.dll', 'vulkan-1.dll'}  # Come with the graphics card's driver, not with Orion.
    checked = set()
    for engine in P.catalog()['engines']:
        for hardware, recipe in (P.engine_builds(engine) or {}).items():
            if recipe.get('wheels'):
                continue
            pieces = [local_archive(piece) for piece in recipe.get('archives', [])]
            if None in pieces:
                continue
            folder = tmp_path / engine / hardware
            if recipe.get('bundled'):
                __import__('shutil').copytree(P.ROOT / recipe['bundled'], folder)
            for piece in pieces:
                with zipfile.ZipFile(piece) as bundle:
                    bundle.extractall(folder)
            for path in [*folder.rglob('*.exe'), *folder.rglob('*.dll')]:
                missing = [dll for dll in pe_imports(path) if not dll.lower().startswith('api-ms-')
                           and dll.lower() not in driver and not (path.parent / dll).is_file()
                           and not os.path.isfile(os.path.join(system32, dll))]
                assert not missing, f'{engine} {hardware}: {path.name} needs {missing}'
            checked.add((engine, hardware))
    assert ('omnivoice-gguf', 'cpu') in checked  # Orion's own files: always here.


def test_python_packages_need_a_python_they_are_built_for(parts, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv('ORION_PARTS_HOME', str(parts))
    data = P.catalog()
    data['engines']['speech'] = {'version': 'test', 'pythons': {'3.11': {'windows': {'cpu': {'wheels': []}}},
                                                                '3.14': {'windows': {'cpu': {'wheels': []}}}}}
    next(part for part in data['parts'] if part['id'] == 'stt')['engine'] = 'speech'
    monkeypatch.setattr(P, 'catalog', lambda: data)
    monkeypatch.setattr(P, 'hardware', lambda **kwargs: NO_CARD)
    monkeypatch.setattr(P, 'answers', lambda port, timeout=.5: False)
    monkeypatch.setattr(P, '_python', lambda: '3.9')  # A Python the catalog has no packages for.
    stt = next(part for part in P.status()['parts'] if part['id'] == 'stt')
    assert not stt['installable'] and '3.11, 3.14' in stt['note'] and '3.9' in stt['note']
    with pytest.raises(P.PartError):
        P.start_job('stt', 'install')


def test_the_vision_file_is_optional_and_can_come_later(parts):
    fake_engine(parts)
    llm = P.spec('llm')
    gemma = P.model_spec(llm, 'gemma-4-e2b-it-q3-k-s')
    off = {**P.default_settings(llm, gemma, TWO_CARDS), **P.clean_settings(llm, {'vision': False}, TWO_CARDS)}
    assert [f.name for f in P.model_files(llm, gemma, off)] == ['gemma-4-E2B-it-Q3_K_S.gguf']
    folder = P.models_dir('llm') / gemma['folder']
    folder.mkdir(parents=True)
    (folder / 'gemma-4-E2B-it-Q3_K_S.gguf').write_bytes(b'm')
    assert P.downloaded(llm, gemma, off) and not P.downloaded(llm, gemma, {**off, 'vision': True})
    argv, _, _ = P.command(llm, gemma, off)
    assert '--mmproj' not in argv  # Installed and running without images.
    with pytest.raises(P.PartError):
        P.clean_settings(llm, {'vision': 'yes'}, TWO_CARDS)
    status = next(part for part in P.status()['parts'] if part['id'] == 'llm')
    row = next(m for m in status['models'] if m['id'] == gemma['id'])
    assert row['extra'] and not row['extra_there'] and status['settings']['vision'] is True


def test_start_answers_at_once_and_a_failed_start_says_why(parts, monkeypatch):
    fake_engine(parts)
    monkeypatch.setattr(P, 'downloaded', lambda part, model, settings=None: True)
    monkeypatch.setattr(P, '_alive', lambda pid: True)  # The background process is running.
    P.write_part_config('embedding', enabled=True, paused=True, model='jina-v5-omni-nano')
    P.start('embedding')
    card = lambda: next(part for part in P.status()['parts'] if part['id'] == 'embedding')
    assert card()['state'] == 'starting'  # From the first answer on: the button never flips back to Başlat.

    def broken(part_id):
        (P.run_dir(part_id) / 'service.log').write_text('loading...\nerror: out of memory\n', encoding='utf-8')
        raise P.PartError('Servis hazır olmadan kapandı')
    monkeypatch.setattr(P, 'start_part', broken)
    monkeypatch.setattr(P, 'stop_part', lambda part_id: True)
    assert P.apply('embedding') == 1
    assert card()['state'] == 'stopped' and 'belleği yetmedi' in card()['error']
    assert P.read_config()['embedding']['paused']  # Nothing starts it again by itself; Başlat does.


def tiny_gguf(path, context):
    """A GGUF header with a few keys (one array before the context, as real files have)."""
    import struct
    def string(text):
        data = text.encode()
        return struct.pack('<Q', len(data)) + data
    keys = (string('general.architecture') + struct.pack('<I', 8) + string('gemma4')
            + string('tokenizer.ggml.scores') + struct.pack('<IIQ', 9, 6, 3) + struct.pack('<fff', 1, 2, 3)
            + string('gemma4.context_length') + struct.pack('<I', 4) + struct.pack('<I', context))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 3) + keys)


def test_the_context_stops_at_what_the_model_was_trained_for(parts):
    llm = P.spec('llm')
    small = P.model_spec(llm, 'miniplm-qwen-200m')
    assert P.context_max(llm, small) == P.CONTEXT_FALLBACK  # Nothing known yet.
    P._write_json(parts / 'headers.json', {small['url'] + small['files'][0]: {'meta': {'context': 32768}, 'at': 0}})
    assert P.context_max(llm, small) == 32768  # The file's header, read from the internet before the download.
    tiny_gguf(P.models_dir('llm') / small['folder'] / small['files'][0], 4096)
    assert P.context_max(llm, small) == 4096  # The file itself, once it is there.
    field = next(f for f in P.advanced(llm, TWO_CARDS, small) if f['key'] == 'context_size')
    assert field['max'] == 4096
    with pytest.raises(P.PartError):
        P.clean_settings(llm, {'context_size': 8192}, TWO_CARDS, small)
    # A context saved for a bigger model is cut down to this one's.
    P.write_part_config('llm', model='miniplm-qwen-200m', settings={'context_size': 65536})
    assert P.effective(llm, TWO_CARDS)[1]['context_size'] == 4096
    with pytest.raises(P.PartError):
        P.select('llm', settings={'context_size': 8192})


def test_an_older_wish_never_undoes_a_newer_one(parts, monkeypatch):
    # A restart (setting change) was still waiting when "Kaldır + modelleri sil" came: the restart must not
    # start the model being deleted, nor leave its error on the card.
    started, stopped = [], []
    monkeypatch.setattr(P, 'start_part', lambda part_id: started.append(part_id))
    monkeypatch.setattr(P, 'stop_part', lambda part_id: stopped.append(part_id))
    monkeypatch.setattr(P, 'downloaded', lambda part, model, settings=None: True)
    fake_engine(parts)
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    P.select('embedding', settings={'parallel': 2})
    P.apply_choice('embedding')
    restart = P.spawned[-1]
    P.remove('embedding', delete_models=True)
    removal = P.spawned[-1]
    assert P.apply('embedding', wish=restart[restart.index('--id') + 1], restart=True) == 0
    assert started == [] and stopped == []  # The old wish did nothing.
    assert P.apply('embedding', wish=removal[removal.index('--id') + 1]) == 0
    assert stopped == ['embedding'] and P.transition('embedding') is None
    card = next(part for part in P.status()['parts'] if part['id'] == 'embedding')
    assert card['state'] == 'absent' and card['error'] is None


def test_a_start_without_its_model_files_says_so(parts, monkeypatch):
    monkeypatch.setattr(P, 'start_part', lambda part_id: pytest.fail('started without its files'))
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    assert P.apply('embedding') == 1
    assert 'dosyaları eksik' in P.transition('embedding')['error']


def test_the_status_holds_facts_only_so_no_choice_waits_for_it(parts):
    """What a choice makes of the facts (sizes with the extra file, what is left to download, the context limit)
    the page works out with the click. The status never says it: a value only the Hub worked out once showed late
    on the page, so whatever the user chooses, every fact stays the same and only the choice changes."""
    card = lambda: next(part for part in P.status()['parts'] if part['id'] == 'llm')
    facts = lambda value: {key: item for key, item in value.items() if key not in ('model', 'settings', 'vision_off')}
    before = card()
    assert not {'download_mb', 'engine_ready', 'engine_mb'} & set(before)
    assert not {'size_mb', 'downloaded'} & set(before['models'][0])
    P.select('llm', model='miniplm-qwen-200m', settings={'vision': False, 'hardware': 'cpu', 'context_size': 1024})
    after = card()
    assert facts(after) == facts(before) and after['settings']['hardware'] == 'cpu'
    gemma = next(m for m in after['models'] if m['id'] == 'gemma-4-e2b-it-q3-k-s')
    # Each model says its own engine for every hardware (TTS: OmniVoice's and VoxCPM2's differ), so picking one
    # shows its download with the click, whatever the saved model is.
    tts = next(part for part in P.status()['parts'] if part['id'] == 'tts')
    sizes = {m['id']: {hw: e['mb'] for hw, e in m['engines'].items()} for m in tts['models']}
    assert sizes['omnivoice-gguf'] != sizes['voxcpm2-gguf'] and set(sizes['omnivoice-gguf']) == set(tts['engines'])
    assert (gemma['main_mb'], gemma['extra_mb'], gemma['main_there'], gemma['extra_there']) == (2332, 941, False, False)
    assert gemma['name'] == 'Gemma 4 E2B' and gemma['extra_name'] == 'Görsel anlama'
    assert set(after['engines']) == {'cuda', 'vulkan', 'cpu'}
    assert after['engines']['cuda']['mb'] > after['engines']['vulkan']['mb'] > 0 and not after['engines']['cuda']['ready']
    # Labels come from this code even when the hardware came from an old cache.
    P._hardware.update(at=__import__('time').monotonic(), value={**TWO_CARDS, 'choices': [{'id': 'vulkan', 'label': 'old words'}]})
    assert P._labelled(P._hardware['value'])['choices'][0]['label'] == 'Ekran kartı (Vulkan)'


def test_the_model_file_tells_its_limits_vector_size_and_what_its_extra_understands(parts, tmp_path):
    import struct
    def string(text):
        data = text.encode()
        return struct.pack('<Q', len(data)) + data
    path = tmp_path / 'mmproj.gguf'
    keys = (string('general.architecture') + struct.pack('<I', 8) + string('clip')
            + string('clip.has_vision_encoder') + struct.pack('<I', 7) + b'\x01'
            + string('clip.has_audio_encoder') + struct.pack('<I', 7) + b'\x01')
    path.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 3) + keys)
    assert P.file_meta(path) == {'architecture': 'clip', 'context': None, 'dims': None, 'pooling': None,
                                 'vision': True, 'audio': True}
    llm = P.spec('llm')
    gemma = P.model_spec(llm, 'gemma-4-e2b-it-q3-k-s')
    assert P.extra_info(llm, gemma)[0] == 'Görsel anlama'  # Not known yet.
    P._write_json(parts / 'headers.json', {gemma['url'] + gemma['mmproj']: {'meta': P.file_meta(path), 'at': 0}})
    assert P.extra_info(llm, gemma)[0] == 'Görsel ve ses anlama'
    embedding = P.spec('embedding')
    jina = P.model_spec(embedding, 'jina-v5-omni-nano')
    P._write_json(parts / 'headers.json', {jina['url'] + jina['files'][0]: {'meta': {'context': 8192, 'dims': 768}, 'at': 0}})
    row = next(m for m in next(c for c in P.status()['parts'] if c['id'] == 'embedding')['models'] if m['id'] == jina['id'])
    assert (row['context_max'], row['dims']) == (8192, 768)


def test_a_choice_is_only_saved_until_uygula(parts, monkeypatch):
    fake_engine(parts)  # CUDA only: the processor's engine is not here.
    monkeypatch.setattr(P, 'downloaded', lambda part, model, settings=None: True)
    monkeypatch.setattr(P, '_alive', lambda pid: True)
    P.write_part_config('llm', enabled=True, model='miniplm-qwen-200m', settings={'hardware': 'cuda'})
    card = lambda: next(part for part in P.status()['parts'] if part['id'] == 'llm')
    P.select('llm', settings={'parallel': 2})
    P.select('llm', settings={'hardware': 'cpu'})
    assert P.spawned == []  # Saving never stops or restarts anything.
    # Uygula with a choice whose engine is not here: the download comes first, the old one keeps running.
    P.apply_choice('llm')
    assert P.spawned[-1][:2] == ['job', 'llm'] and 'install' in P.spawned[-1]
    (P.home() / 'jobs' / 'llm.json').unlink()
    P.select('llm', settings={'hardware': 'cuda'})
    P.apply_choice('llm')  # Everything here: a restart with the new choice.
    assert '--restart' in P.spawned[-1] and card()['state'] == 'restarting'
    assert P.read_config()['llm']['settings']['parallel'] == 2


def test_a_paused_part_only_saves_its_settings(parts, monkeypatch):
    fake_engine(parts)
    monkeypatch.setattr(P, 'downloaded', lambda part, model, settings=None: True)
    P.write_part_config('llm', enabled=True, paused=True, model='miniplm-qwen-200m')
    P.select('llm', settings={'parallel': 3})
    assert P.spawned == [] and P.read_config()['llm']['settings']['parallel'] == 3


def test_the_engine_goes_with_the_last_part_that_uses_it(parts, monkeypatch):
    monkeypatch.setattr(P, 'start_part', lambda part_id: None)
    monkeypatch.setattr(P, 'stop_part', lambda part_id: True)
    fake_engine(parts, 'cuda')
    fake_engine(parts, 'vulkan')
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano', settings={'hardware': 'cuda'})
    P.write_part_config('llm', enabled=True, model='miniplm-qwen-200m', settings={'hardware': 'vulkan'})
    P.remove('llm', delete_models=True)
    assert P.apply('llm') == 0
    # Memory still runs on the CUDA build: it stays; the Vulkan one nobody uses is gone.
    assert P.engine_dir('llama.cpp', 'cuda').is_dir() and not P.engine_dir('llama.cpp', 'vulkan').exists()
    P.remove('embedding', delete_models=False)
    assert P.apply('embedding') == 0
    assert P.engine_dir('llama.cpp', 'cuda').is_dir()  # Kept: the user kept the downloads.
    P.remove('embedding', delete_models=True)
    assert P.apply('embedding') == 0
    assert not P.engine_dir('llama.cpp', 'cuda').exists()  # The last one: gone too.


def gguf(path, arch, **numbers):
    """A GGUF file holding only a header: its architecture and these `<arch>.<key>` numbers."""
    import struct
    def string(text):
        data = text.encode()
        return struct.pack('<Q', len(data)) + data
    keys = string('general.architecture') + struct.pack('<I', 8) + string(arch)
    for key, number in numbers.items():
        keys += string(f'{arch}.{key}') + struct.pack('<I', 4) + struct.pack('<I', number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 1 + len(numbers)) + keys)


def test_a_held_model_is_deleted_once_its_service_lets_go(parts, monkeypatch):
    stopped = []
    monkeypatch.setattr(P, 'stop_part', lambda part_id: stopped.append(part_id) or True)
    monkeypatch.setattr(P, 'answers', lambda port, timeout=.5: True)
    folder = P.models_dir('embedding') / 'jina-embeddings'
    gguf(folder / 'jina-embeddings-v5-omni-nano-retrieval-F16.gguf', 'eurobert')
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    P._write_json(P.run_dir('embedding') / 'applied.json', {'model': 'jina-v5-omni-nano', 'settings': {'hardware': 'cuda'}})
    P.delete_model('embedding', 'jina-v5-omni-nano')
    assert folder.is_dir()  # Still held: nothing deleted inside the request.
    assert P.read_config()['embedding']['paused'] and 'apply' in P.spawned[-1]
    wish = P.spawned[-1][P.spawned[-1].index('--id') + 1]
    monkeypatch.setattr(P, 'answers', lambda port, timeout=.5: False)
    assert P.apply('embedding', wish=wish) == 0
    assert stopped == ['embedding'] and not folder.exists() and P.read_config()['embedding']['drop'] == []


def test_the_extra_file_goes_alone_and_the_choice_stops_asking_for_it(parts):
    folder = P.models_dir('llm') / 'gemma-4-e2b'
    gguf(folder / 'gemma-4-E2B-it-Q3_K_S.gguf', 'gemma4', context_length=131072)
    gguf(folder / 'mmproj-BF16.gguf', 'clip')
    P.write_part_config('llm', enabled=True, model='gemma-4-e2b-it-q3-k-s', settings={'vision': True})
    P.delete_model('llm', 'gemma-4-e2b-it-q3-k-s', extra=True)
    assert (folder / 'gemma-4-E2B-it-Q3_K_S.gguf').is_file() and not (folder / 'mmproj-BF16.gguf').exists()
    assert P.read_config()['llm']['vision_off'] == ['gemma-4-e2b-it-q3-k-s']  # That model's only.
    assert P.effective(P.spec('llm'), P.hardware())[1]['vision'] is False
    # Each model keeps its own switch: off for one is not off for another.
    P.select('llm', vision_off=[])
    P.select('llm', model='miniplm-qwen-200m', settings={'vision': False})  # No extra file: nothing to keep.
    assert P.read_config()['llm']['vision_off'] == []
    P.select('llm', model='gemma-4-e2b-it-q3-k-s', settings={'vision': False})
    assert P.read_config()['llm']['vision_off'] == ['gemma-4-e2b-it-q3-k-s']
    llm = next(part for part in P.status()['parts'] if part['id'] == 'llm')
    assert llm['vision_off'] == ['gemma-4-e2b-it-q3-k-s']  # The page draws each model's switch from it.
    with pytest.raises(P.PartError):
        P.delete_model('llm', 'miniplm-qwen-200m', extra=True)  # It has no extra file.


def test_users_own_models_are_read_from_their_folders(parts, monkeypatch):
    opened = []
    monkeypatch.setattr(P.os, 'startfile', opened.append, raising=False)
    # LLM: the name is the folder's; which file is the model and which the extra is read from them.
    with pytest.raises(P.PartError):
        P.add_user_model('llm', 'Uzun', description='x' * 81)  # Its words fit under its name.
    assert P.add_user_model('llm', 'Benim Qwen', description='  Türkçe sohbet  için ') == 'custom-benim-qwen'
    with pytest.raises(P.PartError):
        P.add_user_model('llm', 'benim qwen')  # The same name.
    folder = P.user_dir('llm') / 'Benim Qwen'
    card = lambda part_id: next(part for part in P.status()['parts'] if part['id'] == part_id)
    row = lambda part_id, model_id: next(m for m in card(part_id)['models'] if m['id'] == model_id)
    empty = row('llm', 'custom-benim-qwen')
    assert empty['description'] == 'Türkçe sohbet için'  # Under its name, as ours have theirs.
    assert empty['user'] and not empty['complete'] and empty['checklist'][0]['state'] == 'missing'
    gguf(folder / 'qwen-7b-q4.gguf', 'qwen2', context_length=32768)
    gguf(folder / 'vision.gguf', 'clip')
    model = P.model_spec(P.spec('llm'), 'custom-benim-qwen')
    assert (model['files'], model['mmproj'], model['complete']) == (['qwen-7b-q4.gguf'], 'vision.gguf', True)
    assert row('llm', 'custom-benim-qwen')['context_max'] == 32768
    assert [item['label'] for item in model['checklist']] == ['Model', 'Görsel ve ses anlama']
    gemma = row('llm', 'gemma-4-e2b-it-q3-k-s')  # Ours name their files too, under the same words.
    assert (gemma['main_files'], gemma['extra_file']) == (['gemma-4-E2B-it-Q3_K_S.gguf'], 'mmproj-BF16.gguf')
    gguf(folder / 'other.gguf', 'llama')  # Two models in one folder: which one is not guessed.
    assert P.model_spec(P.spec('llm'), 'custom-benim-qwen')['checklist'][0]['state'] == 'many'
    # Renamed from the page: the folder moves, the saved choice and the description follow it.
    P.write_part_config('llm', model='custom-benim-qwen')
    assert P.edit_user_model('llm', 'custom-benim-qwen', 'Qwen 7B', 'Kod için') == 'custom-qwen-7b'
    assert not folder.exists() and (P.user_dir('llm') / 'Qwen 7B' / 'qwen-7b-q4.gguf').is_file()
    assert P.read_config()['llm']['model'] == 'custom-qwen-7b'
    assert row('llm', 'custom-qwen-7b')['description'] == 'Kod için'
    with monkeypatch.context() as patch:
        patch.setattr(P, 'held', lambda *a, **k: True)
        with pytest.raises(P.PartError, match='önce durdur'):
            P.edit_user_model('llm', 'custom-qwen-7b', 'Başka')  # Its service has the file open.
        assert P.edit_user_model('llm', 'custom-qwen-7b', 'Qwen 7B', '') == 'custom-qwen-7b'  # Words only: fine.
    P.edit_user_model('llm', 'custom-qwen-7b', 'Benim Qwen')
    P.open_folder('llm', 'custom-benim-qwen')
    assert opened == [str(folder)]
    # Speech: one whisper.cpp file, whatever its name.
    assert P.add_user_model('stt', 'whisper-tr') == 'custom-whisper-tr'
    stt = P.user_dir('stt') / 'whisper-tr'
    lines = P.model_spec(P.spec('stt'), 'custom-whisper-tr')['checklist']
    assert [(item['state'], item['label'], item['expect']) for item in lines] == [
        ('missing', 'Model', 'model.bin')]
    whisper_ggml(stt / 'benim-modelim.bin')
    whisper = P.model_spec(P.spec('stt'), 'custom-whisper-tr')
    assert whisper['complete'] and whisper['files'] == ['benim-modelim.bin'] and whisper['kind'] == 'ggml'
    engine = P.engine_dir('whisper.cpp', 'cpu')
    engine.mkdir(parents=True)
    (engine / '.complete').write_text('x')
    env = P.command(P.spec('stt'), whisper, {'hardware': 'cpu', 'gpu': 'all'})[1]
    assert env['WHISPER_MODEL'] == str(stt / 'benim-modelim.bin')
    whisper_ggml(stt / 'ikinci.bin')  # Two models in one folder: which one is not guessed.
    assert P.model_spec(P.spec('stt'), 'custom-whisper-tr')['checklist'][0]['state'] == 'many'
    # Speech out: the user picks the engine (a folder each); its two files are told apart by their contents,
    # whatever their names; a file of another kind is named, not taken.
    assert P.add_user_model('tts', 'Ses', 'omnivoice') == 'custom-omnivoice-ses'
    tts = P.user_dir('tts') / 'OmniVoice' / 'Ses'
    gguf(tts / 'zz.gguf', 'omnivoice-tokenizer')
    gguf(tts / 'a.gguf', 'omnivoice-lm', context_length=40960)
    gguf(tts / 'c.gguf', 'llama')
    voice = P.model_spec(P.spec('tts'), 'custom-omnivoice-ses')
    assert voice['complete'] and voice['files'] == ['a.gguf', 'zz.gguf'] and voice['kind'] == 'omnivoice'
    assert voice['problems'] == ['c.gguf: tanınmayan dosya (llama).']
    # Memory: a model that does not say how it pools is pooled like ours.
    gguf(P.user_dir('embedding') / 'E' / 'e.gguf', 'qwen2', context_length=8192, embedding_length=1024)
    assert P.model_spec(P.spec('embedding'), 'custom-e')['args'] == ['--pooling', 'last']
    assert row('embedding', 'custom-e')['dims'] == 1024


def test_voxcpm2_runs_on_its_own_engine_next_to_omnivoice(parts):
    """The speech voice has two engines: OmniVoice (the part's) and VoxCPM2 (llama.cpp-omni's llama-tts-server),
    which its model brings; everything about a model (download, install, start, what is ready) follows its own."""
    tts = P.spec('tts')
    vox = P.model_spec(tts, 'voxcpm2-gguf')
    assert vox['engine'] == 'voxcpm2-gguf' and P.part_engines(tts) == ['omnivoice-gguf', 'voxcpm2-gguf']
    folder = P.models_dir('tts') / vox['folder']
    for name in vox['files']:
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b'')
    settings = {'hardware': 'vulkan', 'gpu': '1'}
    omni_engine = P.engine_dir('omnivoice-gguf', 'vulkan')
    omni_engine.mkdir(parents=True)
    (omni_engine / '.complete').write_text('x')
    assert not P.ready(tts, vox, settings)  # OmniVoice's engine is here, VoxCPM2's is not.
    with pytest.raises(P.PartError):
        P.command(tts, vox, settings)
    vox_engine = P.engine_dir('voxcpm2-gguf', 'vulkan')
    vox_engine.mkdir(parents=True)
    (vox_engine / '.complete').write_text('x')
    assert P.ready(tts, vox, settings)
    argv, env, port = P.command(tts, vox, settings)
    assert port == 8810 and argv[4].endswith('api.py') and env['ENGINE_NAME'] == 'voxcpm2-gguf'
    assert env['MODEL_PATH'].endswith('VoxCPM2-BaseLM-Q8_0.gguf') and env['ACOUSTIC_PATH'].endswith('VoxCPM2-Acoustic-F16.gguf')
    assert env['VOXCPM2_BIN_DIR'] == str(vox_engine) and 'OMNIVOICE_BIN_DIR' not in env and 'CODEC_PATH' not in env
    assert env['GGML_VK_VISIBLE_DEVICES'] == '1'
    # Its row on the page says what its own engine costs on each hardware (CUDA brings CUDA's runtime too).
    row = next(m for m in next(p for p in P.status()['parts'] if p['id'] == 'tts')['models'] if m['id'] == vox['id'])
    assert row['engines']['vulkan']['ready'] and not row['engines']['cuda']['ready']
    recipe = P.engine_recipe('voxcpm2-gguf', 'cuda')
    if recipe is not None:
        assert [a['url'].rsplit('/', 1)[-1] for a in recipe['archives']] == [
            'voxcpm2-gguf-windows-cuda.zip', 'cudart-llama-bin-win-cuda-12.4-x64.zip']
    # A user's own VoxCPM2: its two GGUFs told apart by what they say they are.
    assert P.add_user_model('tts', 'Benim', 'voxcpm2') == 'custom-voxcpm2-benim'
    mine = P.user_dir('tts') / 'VoxCPM2' / 'Benim'
    gguf(mine / 'b.gguf', 'voxcpm-acoustic')
    gguf(mine / 'a.gguf', 'minicpm4', context_length=32768)
    user = P.model_spec(P.spec('tts'), 'custom-voxcpm2-benim')
    assert user['complete'] and user['files'] == ['a.gguf', 'b.gguf'] and user['engine'] == 'voxcpm2-gguf'
    assert P.command(P.spec('tts'), user, settings)[1]['ENGINE_NAME'] == 'voxcpm2-gguf'
    # Removing the part takes both engines it installed, which no other part uses.
    assert set(P.unused_engines(tts)) == {omni_engine, vox_engine}


def whisper_ggml(path, *, mels=80, audio_ctx=1500, text_ctx=448, magic=0x67676d6c):
    """A whisper.cpp model file holding only its header (whisper.cpp's whisper_model_load reads it so): the magic
    and the hyperparameters, here those of small (12 layers)."""
    import struct
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack('<I11i', magic, 51865, audio_ctx, 768, 12, 12, text_ctx, 768, 12, 12, mels, 8)
                     + bytes(64))


def test_a_speech_model_is_known_by_its_header(parts):
    """Only a whisper.cpp model counts, whatever its name: not a faster-whisper (CTranslate2) folder from before,
    not the Silero VAD (ggml too, other numbers), not an old llama.cpp ggml file."""
    folder = P.user_dir('stt') / 'eski'
    (folder / 'model.bin').parent.mkdir(parents=True)
    (folder / 'model.bin').write_bytes(b'\x06\x00\x00\x00\x0c\x00WhisperSpec\x00' + bytes(64))
    for name in ('config.json', 'tokenizer.json', 'vocabulary.txt'):
        (folder / name).write_text('{}')
    model = P.model_spec(P.spec('stt'), 'custom-eski')
    assert not model['complete'] and model['checklist'][0]['state'] == 'missing'
    # Systran's faster-whisper files said for what they are; the JSON files beside them are no model.
    assert model['problems'] == ["model.bin: faster-whisper (CTranslate2) biçiminde; whisper.cpp'nin ggml .bin dosyası gerekir."]
    whisper_ggml(folder / 'silero.bin', audio_ctx=64)
    whisper_ggml(folder / 'llama.bin', audio_ctx=4096, text_ctx=32)
    assert P.whisper_meta(folder / 'silero.bin') is None and P.whisper_meta(folder / 'llama.bin') is None
    gguf(folder / 'whisper.gguf', 'whisper')
    (folder / 'model.safetensors').write_bytes(b'{}')
    whisper_ggml(folder / 'turbo.bin', mels=128)
    assert P.whisper_meta(folder / 'turbo.bin') == {'mels': 128, 'audio_layers': 12, 'text_layers': 12, 'vocab': 51865}
    model = P.model_spec(P.spec('stt'), 'custom-eski')
    assert model['complete'] and model['files'] == ['turbo.bin']
    assert sorted(problem.split(' biçiminde')[0] for problem in model['problems']) == [
        'llama.bin: başka bir ggml', 'model.bin: faster-whisper (CTranslate2)',
        'model.safetensors: safetensors (Hugging Face)', 'silero.bin: başka bir ggml', 'whisper.gguf: GGUF']


def test_an_update_leaves_no_faster_whisper_behind(parts):
    """A PC that ran speech recognition on faster-whisper: its engine (1.4 GB with cuDNN) and our CTranslate2
    models go at the first start of the new Hub; the choice moves to whisper.cpp's model of the same size.
    The user's own models stay, and so does a job's scratch folder."""
    engines = P.home() / 'engines'
    old = engines / 'faster-whisper-1.2.1-py311' / 'cuda'
    old.mkdir(parents=True)
    (old / 'cudnn.dll').write_bytes(b'x')
    current = P.engine_dir('llama.cpp', 'cpu')
    current.mkdir(parents=True)
    (engines / 'unpack-123').mkdir()
    retired = P.models_dir('stt') / 'whisper-small-official'
    retired.mkdir(parents=True)
    (retired / 'model.bin').write_bytes(b'x')
    whisper_ggml(P.user_dir('stt') / 'benim' / 'm.bin')
    P.write_part_config('stt', enabled=True, model='whisper-small-official',
                        settings={'hardware': 'cuda', 'gpu': 'all', 'compute_type': 'int8_float16'})
    P._tidied.clear()
    P.tidy(P.catalog())
    assert not (engines / 'faster-whisper-1.2.1-py311').exists() and current.is_dir() and (engines / 'unpack-123').is_dir()
    assert not retired.exists() and (P.user_dir('stt') / 'benim' / 'm.bin').is_file()
    model, settings = P.effective(P.spec('stt'), TWO_CARDS)
    assert model['id'] == 'whisper-small' and settings == {'hardware': 'cuda', 'gpu': 'all'}
    (engines / 'faster-whisper-1.2.1-py311').mkdir()
    P.tidy(P.catalog())  # Once per Hub start: what an update leaves comes only with an update.
    assert (engines / 'faster-whisper-1.2.1-py311').is_dir()


def test_a_download_names_and_counts_only_what_it_fetches(parts, server, monkeypatch):
    data = served(server, monkeypatch)
    seen = []
    record = P._update_job
    monkeypatch.setattr(P, '_update_job', lambda part_id, **values: seen.append(values) or record(part_id, **values))
    folder = P.models_dir('llm') / 'gemma-4-e2b'
    gguf(folder / 'gemma-4-E2B-it-Q3_K_S.gguf', 'gemma4', context_length=131072)
    P.write_part_config('llm', model='gemma-4-e2b-it-q3-k-s', settings={'vision': True})
    assert P.run_job('llm', 'download', 'gemma-4-e2b-it-q3-k-s') == 0
    started = next(values for values in seen if values.get('fetch'))
    # Only the extra file: named so, its own size (not the model already here counted again).
    assert started['label'] == 'Ek dosya indiriliyor: Görsel anlama' and started['fetch'] == ['extra']
    assert started['total'] == 941 * P.MB
    assert not any('Model indiriliyor' in str(values.get('label')) for values in seen)
    assert (folder / 'mmproj-BF16.gguf').read_bytes() == Files.content


def test_removing_keeps_the_users_own_models_unless_asked(parts, monkeypatch):
    monkeypatch.setattr(P, 'stop_part', lambda part_id: True)
    gguf(P.user_dir('llm') / 'Mine' / 'm.gguf', 'qwen2', context_length=4096)
    gguf(P.models_dir('llm') / 'miniplm-qwen-200m' / 'MiniPLM-Qwen-200M-Q3_K_M.gguf', 'qwen2')
    P.write_part_config('llm', enabled=True, model='custom-mine')
    P.remove('llm', delete_models=True)
    assert P.apply('llm') == 0
    assert (P.user_dir('llm') / 'Mine' / 'm.gguf').is_file()
    assert not (P.models_dir('llm') / 'miniplm-qwen-200m').exists()
    P.write_part_config('llm', enabled=True)
    P.remove('llm', delete_models=True, delete_user_models=True)
    assert P.apply('llm') == 0
    assert not P.user_dir('llm').exists()


def test_one_file_at_a_time(parts, server, monkeypatch):
    """Each file's own line downloads or deletes just it; a user's model goes with its last file."""
    real = P.catalog
    served(server, monkeypatch)
    P.write_part_config('llm', model='gemma-4-e2b-it-q3-k-s', settings={'vision': False})
    assert P.run_job('llm', 'download', 'gemma-4-e2b-it-q3-k-s', ['extra']) == 0  # Not used yet: still fetched.
    folder = P.models_dir('llm') / 'gemma-4-e2b'
    assert (folder / 'mmproj-BF16.gguf').is_file() and not (folder / 'gemma-4-E2B-it-Q3_K_S.gguf').exists()
    P.start_job('llm', 'download', model='gemma-4-e2b-it-q3-k-s', fetch=['model'])
    assert P.spawned[-1][-2:] == ['--fetch', 'model']
    monkeypatch.setattr(P, 'catalog', real)
    mine = P.user_dir('llm') / 'Mine'
    gguf(mine / 'm.gguf', 'qwen2', context_length=4096)
    gguf(mine / 'v.gguf', 'clip')
    with pytest.raises(P.PartError):
        P.delete_model('llm', 'custom-mine', file='../other.gguf')
    P.delete_model('llm', 'custom-mine', file='v.gguf')
    assert (mine / 'm.gguf').is_file() and not (mine / 'v.gguf').exists()
    P.delete_model('llm', 'custom-mine', file='m.gguf')
    assert not mine.exists()


def test_internet_downloads_are_verified_by_windows(monkeypatch):
    """Engines and models from GitHub and Hugging Face go through Windows' own certificate check (truststore), as
    Orion's updates do: Python's alone refused GitHub's new Sectigo chain and no engine from GitHub downloaded."""
    import ssl
    import urllib.request
    from services.shared import windows_trust
    made = ssl.create_default_context()
    monkeypatch.setattr(windows_trust, 'context', lambda: made)
    handler = next(h for h in P.public_opener().handlers if isinstance(h, urllib.request.HTTPSHandler))
    assert handler._context is made
