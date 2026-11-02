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


@pytest.fixture
def parts(tmp_path, monkeypatch):
    monkeypatch.setenv('ORION_PARTS_HOME', str(tmp_path / 'Parts'))
    monkeypatch.setattr(P, 'hardware', lambda **kwargs: TWO_CARDS)
    monkeypatch.setattr(P, 'answers', lambda port, timeout=.5: False)
    return tmp_path / 'Parts'


def fake_engine(home, hardware='cuda'):
    folder = P.engine_dir('llama.cpp', hardware) / 'nested'
    folder.mkdir(parents=True)
    (folder / ('llama-server.exe' if os.name == 'nt' else 'llama-server')).write_bytes(b'')


def test_the_cards_show_every_part_and_only_llama_parts_install_here(parts):
    status = P.status()
    by_id = {part['id']: part for part in status['parts']}
    assert set(by_id) == {'embedding', 'llm', 'stt', 'tts'}
    assert by_id['embedding']['installable'] and by_id['embedding']['state'] == 'absent'
    assert by_id['embedding']['model'] == 'jina-v5-omni-nano'  # The recommended model, nothing installed yet.
    assert not by_id['stt']['installable'] and by_id['stt']['note'] and by_id['stt']['cloud']
    keys = [field['key'] for field in by_id['llm']['advanced']]
    assert keys == ['hardware', 'gpu', 'gpu_layers', 'context_size', 'parallel']  # Two cards: which one.


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
    P.hardware(refresh=True, run=run)  # Leave the cache as a test with cards expects; tests patch it anyway.


def test_defaults_fit_the_card_and_settings_stay_inside_their_limits(parts):
    llm = P.spec('llm')
    gemma = P.model_spec(llm, 'gemma-4-e2b-it-q3-k-s')
    assert P.default_settings(llm, gemma, TWO_CARDS)['gpu_layers'] == P.ALL_LAYERS
    small_card = {**TWO_CARDS, 'gpus': [{'index': 0, 'name': 'x', 'vram_mb': 2048, 'vendor': 'nvidia'}]}
    assert P.default_settings(llm, gemma, small_card)['gpu_layers'] == 24  # Half on the card.
    assert P.default_settings(llm, gemma, NO_CARD)['gpu_layers'] == 0
    assert P.clean_settings(llm, {'gpu': '1', 'gpu_layers': '30'}, TWO_CARDS) == {'gpu': '1', 'gpu_layers': 30}
    for wrong in ({'gpu_layers': 5000}, {'hardware': 'metal'}, {'shell': 'calc.exe'}, {'parallel': 'x'}):
        with pytest.raises(P.PartError):
            P.clean_settings(llm, wrong, TWO_CARDS)


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


def test_a_cut_download_continues_where_it_stopped(tmp_path, server):
    target = tmp_path / 'model.gguf'
    target.with_name('model.gguf.part').write_bytes(Files.content[:300000])
    seen = []
    P.download(server + 'model.gguf', target, report=lambda done, total: seen.append((done, total)), cancelled=lambda: False)
    assert target.read_bytes() == Files.content
    assert seen[0] == (300000, len(Files.content))  # It asked for the rest only.
    with pytest.raises(P.Cancelled):
        P.download(server + 'x.gguf', tmp_path / 'x.gguf', report=lambda *a: None, cancelled=lambda: True)
    assert not (tmp_path / 'x.gguf').exists()


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
    assert client.put('/api/v1/parts/llm', json={'settings': {'gpu_layers': 10}}).status_code == 403
    monkeypatch.setattr(parts_routes, 'local_control', lambda request: True)
    response = client.put('/api/v1/parts/llm', json={'settings': {'gpu_layers': 5000}})
    assert response.status_code == 400 and 'arasında' in response.json()['detail']
    assert client.put('/api/v1/parts/llm', json={'model': 'miniplm-qwen-200m'}).status_code == 200
    assert P.read_config()['llm']['model'] == 'miniplm-qwen-200m'


def test_a_stopped_part_stays_off_until_started(parts, monkeypatch):
    started = []
    monkeypatch.setattr(P, 'start_part', lambda part_id: started.append(part_id))
    monkeypatch.setattr(P, 'stop_part', lambda part_id: True)
    monkeypatch.setattr(P, 'downloaded', lambda part, model: True)
    fake_engine(parts)
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    P.stop('embedding')
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
