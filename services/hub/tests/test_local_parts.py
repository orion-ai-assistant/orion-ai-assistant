"""Bileşenler: optional local AI parts (services/shared/local_parts.py) and their API. Never downloads from the
internet, never touches a real installation: parts live under tmp_path, files come from a local HTTP server."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    # With an NVIDIA card the speech engine brings cuBLAS and cuDNN (~1.3 GB); on the processor it is 43 MB.
    assert by_id['stt']['model'] == 'whisper-small-official' and by_id['stt']['engine_mb'] > 1300
    keys = lambda part: [field['key'] for field in by_id[part]['advanced']]
    assert keys('llm') == ['hardware', 'gpu', 'context_size', 'parallel', 'vision']  # Two cards: which one.
    assert keys('stt') == ['hardware', 'gpu', 'compute_type'] and keys('tts') == ['hardware', 'gpu']
    # Speech runs on CUDA or the processor; there is no Vulkan build of it.
    assert [c['id'] for c in by_id['tts']['advanced'][0]['options']] == ['cuda', 'cpu']


def test_speech_services_run_on_orions_python_with_their_engine(parts):
    for part_id in ('stt', 'tts'):
        folder = P.engine_dir(P.spec(part_id)['engine'], 'cpu')
        (folder / 'site').mkdir(parents=True)
        (folder / '.complete').write_text('x')
    stt = P.spec('stt')
    small = P.model_spec(stt, 'whisper-small-official')
    argv, env, port = P.command(stt, small, {'hardware': 'cpu', 'gpu': 'all', 'compute_type': 'float16'})
    assert port == 9009 and argv[1:4] == ['-B', '-m', 'services.shared.part_python']
    assert argv[4].endswith('api.py') and argv[5].endswith('site')
    assert env['WHISPER_MODEL'] == 'whisper-small-official' and env['WHISPER_COMPUTE_TYPE'] == 'int8'  # CPU: int8.
    assert env['ORION_HW_ID'] == 'cpu' and env['MODEL_FILE'] == ''
    tts = P.spec('tts')
    argv, env, port = P.command(tts, P.model_spec(tts, 'omnivoice-gguf'), {'hardware': 'cpu', 'gpu': 'all'})
    assert port == 8810 and env['ENGINE_NAME'] == 'omnivoice-gguf' and env['MODEL_PATH'].endswith('omnivoice-gguf')
    assert env['OMNIVOICE_BIN_DIR'] == str(P.engine_dir('omnivoice-gguf', 'cpu'))


def test_another_system_shows_the_cards_but_cannot_install_yet(parts, monkeypatch):
    monkeypatch.setattr(P, 'hardware', lambda **kwargs: {**NO_CARD, 'system': 'linux'})
    embedding = next(part for part in P.status()['parts'] if part['id'] == 'embedding')
    assert not embedding['installable'] and 'Windows' in embedding['note']
    with pytest.raises(P.PartError):
        P.start_job('embedding', 'install')


def test_graphics_cards_are_read_from_nvidia_smi():
    def run(argv, **kwargs):
        if argv[0] == 'nvidia-smi':
            return SimpleNamespace(returncode=0, stdout='0, NVIDIA GeForce RTX 3060, 12288, 581.0\n1, RTX 2060, 6144, 581.0\n')
        return SimpleNamespace(returncode=0, stdout='NVIDIA GeForce RTX 3060\nIntel(R) UHD Graphics 770\n')
    hw = P.hardware(refresh=True, run=run)
    if hw['system'] != 'windows':
        pytest.skip('Windows hardware layout')
    assert [gpu['name'] for gpu in hw['gpus']] == ['NVIDIA GeForce RTX 3060', 'RTX 2060', 'Intel(R) UHD Graphics 770']
    assert [choice['id'] for choice in hw['choices']] == ['cuda', 'vulkan', 'cpu'] and hw['recommended'] == 'cuda'
    nothing = P.hardware(refresh=True, run=lambda argv, **kwargs: SimpleNamespace(returncode=1, stdout=''))
    assert nothing['recommended'] == 'cpu'


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


def test_a_download_job_fetches_the_model_and_reports_it(parts, server, monkeypatch):
    data = P.catalog()
    for part in data['parts']:
        for model in part.get('models', []):
            model['url'] = server
    monkeypatch.setattr(P, 'catalog', lambda: data)
    assert P.run_job('embedding', 'download', 'jina-v5-omni-nano') == 0
    record = P.job('embedding')
    assert record['phase'] == 'done' and record['bytes'] == len(Files.content)
    embedding = next(part for part in P.status()['parts'] if part['id'] == 'embedding')
    assert next(m for m in embedding['models'] if m['id'] == 'jina-v5-omni-nano')['downloaded']
    assert embedding['state'] == 'absent'  # Downloaded is not installed: the card offers Kur.
    # The model in use cannot be deleted; another can.
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    with pytest.raises(P.PartError):
        P.delete_model('embedding', 'jina-v5-omni-nano')
    P.write_part_config('embedding', enabled=False)
    P.delete_model('embedding', 'jina-v5-omni-nano')
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
    import hashlib
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Archives)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/'
    wheel = zip_bytes({'faster_whisper/__init__.py': 'x = 1\n'})
    model = zip_bytes({'whisper-small-ct2/model.bin': 'm', 'whisper-small-ct2/config.json': '{}',
                       'whisper-small-ct2/tokenizer.json': '{}', 'whisper-small-ct2/vocabulary.txt': 'v'})
    Archives.files = {'fw.whl': wheel, 'small.zip': model, 'bad.whl': b'not this'}
    bundled = tmp_path / 'bundled'
    bundled.mkdir()
    (bundled / 'tool.dll').write_text('dll')
    data = P.catalog()
    monkeypatch.setattr(P, 'ROOT', tmp_path)
    data['engines']['faster-whisper'] = {'version': 'test', 'pythons': {P._python(): {'windows': {
        'cpu': {'bundled': 'bundled', 'wheels': [
            {'url': base + 'fw.whl', 'sha256': hashlib.sha256(wheel).hexdigest(), 'size': len(wheel)}]}}}}}
    stt = next(part for part in data['parts'] if part['id'] == 'stt')
    small = next(m for m in stt['models'] if m['id'] == 'whisper-small-official')
    small.update(archive=base + 'small.zip', archive_sha256=hashlib.sha256(model).hexdigest())
    monkeypatch.setattr(P, 'catalog', lambda: data)
    monkeypatch.setattr(P, 'hardware', lambda **kwargs: {**NO_CARD, 'system': 'windows'})
    monkeypatch.setattr(P, 'start_part', lambda part_id: True)
    monkeypatch.setattr(P, 'stop_part', lambda part_id: True)
    try:
        assert P.run_job('stt', 'install', 'whisper-small-official') == 0, P.job('stt')
        folder = P.engine_dir('faster-whisper', 'cpu')
        assert (folder / 'tool.dll').is_file() and (folder / 'site/faster_whisper/__init__.py').is_file()
        assert P.downloaded(stt, small) and P.read_config()['stt']['enabled']
        # A piece that does not match its SHA-256 is refused and nothing is installed.
        data['engines']['faster-whisper']['pythons'][P._python()]['windows']['cuda'] = {
            'wheels': [{'url': base + 'bad.whl', 'sha256': '0' * 64}]}
        P.write_part_config('stt', settings={'hardware': 'cuda'}, enabled=False)
        monkeypatch.setattr(P, 'hardware', lambda **kwargs: TWO_CARDS)
        assert P.run_job('stt', 'install', 'whisper-small-official') == 1
        assert 'SHA-256' in P.job('stt')['error'] and not P.engine_ready('faster-whisper', 'cuda')
    finally:
        httpd.shutdown()


def test_speech_packages_need_a_python_they_are_built_for(parts, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv('ORION_PARTS_HOME', str(parts))
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
    assert row['mmproj'] and not row['vision_downloaded'] and status['settings']['vision'] is True


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
    assert card()['state'] == 'stopped' and 'out of memory' in card()['error']


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
    assert P.context_max(llm, small) == 32768  # From the catalog before the download.
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
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    P.select('embedding', settings={'parallel': 2})
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


def test_the_button_says_one_total_that_follows_vision(parts):
    llm = P.spec('llm')
    card = lambda: next(part for part in P.status()['parts'] if part['id'] == 'llm')
    engine = card()['engine_mb']
    assert engine and card()['download_mb'] == 2332 + 941 + engine  # Model, its vision file and the engine.
    P.write_part_config('llm', settings={'vision': False})
    assert card()['download_mb'] == 2332 + engine
    gemma = next(m for m in card()['models'] if m['id'] == 'gemma-4-e2b-it-q3-k-s')
    assert gemma['size_mb'] == 2332 and gemma['mmproj_mb'] == 941
    fake_engine(parts)
    folder = P.models_dir('llm') / 'gemma-4-e2b'
    folder.mkdir(parents=True)
    (folder / 'gemma-4-E2B-it-Q3_K_S.gguf').write_bytes(b'm')
    assert card()['download_mb'] == 0  # Everything is there: the button says Kur.
