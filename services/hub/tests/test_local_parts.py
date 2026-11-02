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
    assert by_id['stt']['model'] == 'whisper-small-official' and by_id['stt']['engines']['cuda']['mb'] > 1300
    assert by_id['stt']['engines']['cpu']['mb'] < 100
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
    assert env['WHISPER_MODEL'] == str(P.models_dir('stt') / 'whisper-small-official')
    assert env['WHISPER_COMPUTE_TYPE'] == 'int8'  # CPU: int8.
    assert env['ORION_HW_ID'] == 'cpu' and env['MODEL_FILE'] == ''
    tts = P.spec('tts')
    argv, env, port = P.command(tts, P.model_spec(tts, 'omnivoice-gguf'), {'hardware': 'cpu', 'gpu': 'all'})
    assert port == 8810 and env['ENGINE_NAME'] == 'omnivoice-gguf'
    # Both files named (a user's own model may call them anything).
    assert env['MODEL_PATH'].endswith('omnivoice-base-Q8_0.gguf') and env['CODEC_PATH'].endswith('omnivoice-tokenizer-Q8_0.gguf')
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
    facts = lambda value: {key: item for key, item in value.items() if key not in ('model', 'settings')}
    before = card()
    assert not {'download_mb', 'engine_ready', 'engine_mb'} & set(before)
    assert not {'size_mb', 'downloaded'} & set(before['models'][0])
    P.select('llm', model='miniplm-qwen-200m', settings={'vision': False, 'hardware': 'cpu', 'context_size': 1024})
    after = card()
    assert facts(after) == facts(before) and after['settings']['hardware'] == 'cpu'
    gemma = next(m for m in after['models'] if m['id'] == 'gemma-4-e2b-it-q3-k-s')
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
    assert P.read_config()['llm']['settings']['vision'] is False
    with pytest.raises(P.PartError):
        P.delete_model('llm', 'miniplm-qwen-200m', extra=True)  # It has no extra file.


def test_users_own_models_are_read_from_their_folders(parts, monkeypatch):
    opened = []
    monkeypatch.setattr(P.os, 'startfile', opened.append, raising=False)
    # LLM: the name is the folder's; which file is the model and which the extra is read from them.
    assert P.add_user_model('llm', 'Benim Qwen') == 'custom-benim-qwen'
    with pytest.raises(P.PartError):
        P.add_user_model('llm', 'benim qwen')  # The same name.
    folder = P.user_dir('llm') / 'Benim Qwen'
    card = lambda part_id: next(part for part in P.status()['parts'] if part['id'] == part_id)
    row = lambda part_id, model_id: next(m for m in card(part_id)['models'] if m['id'] == model_id)
    empty = row('llm', 'custom-benim-qwen')
    assert empty['user'] and not empty['complete'] and empty['checklist'][0]['state'] == 'missing'
    gguf(folder / 'qwen-7b-q4.gguf', 'qwen2', context_length=32768)
    gguf(folder / 'vision.gguf', 'clip')
    model = P.model_spec(P.spec('llm'), 'custom-benim-qwen')
    assert (model['files'], model['mmproj'], model['complete']) == (['qwen-7b-q4.gguf'], 'vision.gguf', True)
    assert row('llm', 'custom-benim-qwen')['context_max'] == 32768
    gguf(folder / 'other.gguf', 'llama')  # Two models in one folder: which one is not guessed.
    assert P.model_spec(P.spec('llm'), 'custom-benim-qwen')['checklist'][0]['state'] == 'many'
    P.open_folder('llm', 'custom-benim-qwen')
    assert opened == [str(folder)]
    # Speech: the files faster-whisper needs, each ticked as it comes.
    stt = P.user_dir('stt') / 'whisper-tr'
    ct2(stt / 'model.bin', mels=80)
    (stt / 'config.json').write_text('{}')
    states = [item['state'] for item in P.model_spec(P.spec('stt'), 'custom-whisper-tr')['checklist']]
    assert states == ['ok', 'ok', 'missing', 'missing', 'missing']
    (stt / 'vocabulary.txt').write_text('x')  # One name only: vocabulary.json.
    assert P.model_spec(P.spec('stt'), 'custom-whisper-tr')['checklist'][3]['state'] == 'missing'
    for name in ('tokenizer.json', 'vocabulary.json'):
        (stt / name).write_text('x')
    assert not P.model_spec(P.spec('stt'), 'custom-whisper-tr')['complete']  # All five, as ours have them.
    (stt / 'preprocessor_config.json').write_text('{"feature_size": 80}')
    whisper = P.model_spec(P.spec('stt'), 'custom-whisper-tr')
    assert whisper['complete'] and whisper['files'] == ['model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json',
                                                        'preprocessor_config.json']
    engine = P.engine_dir('faster-whisper', 'cpu')
    engine.mkdir(parents=True)
    (engine / '.complete').write_text('x')
    assert P.command(P.spec('stt'), whisper, {'hardware': 'cpu', 'gpu': 'all'})[1]['WHISPER_MODEL'] == str(stt)
    # Speech out: the user picks the engine (a folder each); its two files are told apart by their contents,
    # whatever their names; a file of another kind is named, not taken. An engine Orion lacks cannot be added.
    with pytest.raises(P.PartError):
        P.add_user_model('tts', 'Ses', 'voxcpm2')
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


def ct2(path, *, mels, spec='WhisperSpec'):
    """A CTranslate2 model.bin holding only its header: the spec and the first convolution (its mel bands)."""
    import struct
    def string(text):
        data = text.encode() + b'\0'
        return struct.pack('<H', len(data)) + data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack('<I', 6) + string(spec) + struct.pack('<II', 3, 2)
                     + string('decoder/embeddings/weight') + struct.pack('<B2IBI', 2, 4, 4, 0, 16) + bytes(16)
                     + string('encoder/conv1/weight') + struct.pack('<B3IBI', 3, 384, mels, 3, 0, 0))


def test_a_speech_model_says_which_files_it_needs(parts):
    """Tried with Orion's CTranslate2 and faster-whisper: a model hearing 128 mel
    bands (large-v3, turbo) refuses audio prepared without preprocessor_config.json."""
    folder = P.user_dir('stt') / 'turbo'
    ct2(folder / 'model.bin', mels=128)
    for name in ('config.json', 'tokenizer.json', 'vocabulary.json'):
        (folder / name).write_text('{}')
    checklist = lambda: P.model_spec(P.spec('stt'), 'custom-turbo')['checklist']
    assert P.ct2_meta(folder / 'model.bin') == {'spec': 'WhisperSpec', 'mels': 128}
    assert (checklist()[-1]['state'], checklist()[-1]['optional']) == ('missing', False)
    assert not P.model_spec(P.spec('stt'), 'custom-turbo')['complete']
    (folder / 'preprocessor_config.json').write_text('{"feature_size": 80}')
    assert checklist()[-1]['state'] == 'wrong'  # Says 80: as wrong as none.
    (folder / 'preprocessor_config.json').write_text('{"feature_size": 128}')
    model = P.model_spec(P.spec('stt'), 'custom-turbo')
    assert model['complete'] and model['files'][-1] == 'preprocessor_config.json'
    ct2(folder / 'model.bin', mels=128, spec='TransformerSpec')  # A translation model is not a Whisper one.
    assert checklist()[0]['state'] == 'wrong'


def test_a_download_names_and_counts_only_what_it_fetches(parts, server, monkeypatch):
    data = P.catalog()
    for part in data['parts']:
        for model in part.get('models', []):
            model['url'] = server
    monkeypatch.setattr(P, 'catalog', lambda: data)
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
    data = P.catalog()
    for part in data['parts']:
        for model in part.get('models', []):
            model['url'] = server
    monkeypatch.setattr(P, 'catalog', lambda: data)
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
