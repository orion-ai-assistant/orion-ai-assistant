"""Where the parts' models are and moving them (services/shared/parts_location.py). Never touches a real
installation: the parts home, the models and every place they move to are under tmp_path, and no process starts
(the move runs in this process; parts "stop" and "start" as recorded calls)."""
import json
import os
import shutil
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.shared import local_parts as P
from services.shared import parts_location as L

NO_CARD = {'system': 'windows', 'gpus': [], 'choices': [{'id': 'cpu', 'label': 'x'}], 'recommended': 'cpu'}


@pytest.fixture
def parts(tmp_path, monkeypatch):
    home = tmp_path / 'Parts'
    monkeypatch.setenv('ORION_PARTS_HOME', str(home))
    monkeypatch.setattr(P, 'hardware', lambda **kwargs: NO_CARD)
    monkeypatch.setattr(P, 'answers', lambda port, timeout=.5: False)
    monkeypatch.setattr(P, 'python_fits', lambda engine, data=None: True)
    monkeypatch.setattr(P, 'prefetch_headers', lambda data: None)
    calls = SimpleNamespace(spawned=[], stopped=[], started=[], phases=[])
    monkeypatch.setattr(P, '_spawn', lambda argv, module=None: calls.spawned.append(argv) or SimpleNamespace(pid=999999999))
    monkeypatch.setattr(P, 'stop_part', lambda part_id: calls.stopped.append(part_id))
    monkeypatch.setattr(P, '_move', lambda part_id, to, restart=False: calls.started.append((part_id, to)))
    update = L._update
    monkeypatch.setattr(L, '_update', lambda **values: (calls.phases.append(values.get('phase')), update(**values)))
    model = P.models_dir('embedding') / 'jina-v5-omni-nano'
    model.mkdir(parents=True)
    (model / 'model.gguf').write_bytes(b'synthetic model ' * 200000)  # ~3 MB: a few blocks to copy.
    (P.models_dir('stt') / 'custom' / 'Benim modelim').mkdir(parents=True)
    (P.models_dir('stt') / 'custom' / 'Benim modelim' / 'config.json').write_text('{}', encoding='utf-8')
    calls.home = home
    return calls


def moving_now(home):
    """A move this very process runs (as the job's own process would)."""
    (home / 'jobs').mkdir(parents=True, exist_ok=True)
    (home / 'jobs' / 'move.json').write_text(json.dumps({'phase': 'copy', 'pid': os.getpid()}), encoding='utf-8')


def test_a_move_copies_checks_switches_and_then_deletes_the_old_folder(parts, tmp_path):
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    P.write_part_config('stt', enabled=True, paused=True)  # Stopped by the user: stays stopped.
    old = P.location()[1]
    model = (old / 'embedding/jina-v5-omni-nano/model.gguf').read_bytes()
    before = P.status()['location']
    assert before['path'] == str(old) and before['bytes'] > 3_000_000 and before['movable']
    assert before['move'] is None and before['old'] == []
    target = tmp_path / 'Yeni yer ü'
    P._write_json(L._job_path(), {'phase': 'queued', 'started': 0, 'pid': os.getpid()})
    assert L.run_move(str(target)) == 0
    # The parts read their models from the new place, every file is there, and the old folder is gone.
    assert P.models_dir('embedding') == target / 'embedding'
    assert (target / 'embedding/jina-v5-omni-nano/model.gguf').read_bytes() == model
    assert not old.exists()
    assert (target / 'stt/custom/Benim modelim/config.json').is_file()
    assert set(parts.stopped) == {'embedding', 'llm', 'stt', 'tts'} and parts.started == [('embedding', 'running')]
    # The page saw each phase in turn: reading the source, copying, checking the copy.
    seen = [phase for phase in parts.phases if phase]
    assert seen.index('scan') < seen.index('copy') < seen.index('verify') < seen.index('done')
    after = P.status()['location']
    assert after['path'] == str(target) and after['move']['phase'] == 'done' and after['move']['error'] is None
    assert after['old'] == []


def test_an_old_folder_a_held_file_kept_is_offered_to_delete(parts, tmp_path, monkeypatch):
    """The old folder goes once the move is done; if a file in it cannot go (held by something outside
    Orion), the page offers to delete it later, or to keep it."""
    old = P.location()[1]
    real = shutil.rmtree
    def held(path, *args, **kwargs):
        raise PermissionError(13, 'The process cannot access the file')
    monkeypatch.setattr(L.shutil, 'rmtree', held)
    first = tmp_path / 'birinci'
    assert L.run_move(str(first)) == 0  # The move itself succeeded.
    assert old.is_dir() and L.facts()['old'] == [{'path': str(old), 'bytes': L._size(old)}]
    monkeypatch.setattr(L.shutil, 'rmtree', real)
    L.drop_old(str(old))
    assert not old.exists() and L.facts()['old'] == []
    assert P.models_dir('embedding') == first / 'embedding'


def test_a_second_move_starts_from_the_first_ones_place(parts, tmp_path, monkeypatch):
    first, second = tmp_path / 'birinci', tmp_path / 'ikinci'
    assert L.run_move(str(first)) == 0 and L.run_move(str(second)) == 0
    assert P.models_dir('embedding') == second / 'embedding'
    assert (second / 'embedding/jina-v5-omni-nano/model.gguf').is_file()
    assert not first.exists() and not (tmp_path / 'Parts' / 'models').exists() and L.facts()['old'] == []
    # One a held file kept: Kalsın keeps its files, and the page stops asking.
    monkeypatch.setattr(L.shutil, 'rmtree', lambda path, *a, **k: (_ for _ in ()).throw(PermissionError(13, 'held')))
    assert L.run_move(str(first)) == 0
    assert [item['path'] for item in L.facts()['old']] == [str(second)]
    L.keep_old(str(second))
    assert second.is_dir() and L.facts()['old'] == []


def test_a_failed_move_keeps_the_old_place_and_takes_away_only_its_copy(parts, tmp_path, monkeypatch):
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    old = P.location()[1]
    target = tmp_path / 'dolu disk'
    copy = shutil.copyfileobj
    def full(reader, writer, **kwargs):
        writer.write(reader.read(10))
        raise OSError(28, 'There is not enough space on the disk')
    monkeypatch.setattr('services.shared.content_migration.shutil.copyfileobj', full)
    assert L.run_move(str(target)) == 1
    # The old folder is never touched by a move that failed.
    assert P.models_dir('embedding') == old / 'embedding' and (old / 'embedding/jina-v5-omni-nano/model.gguf').is_file()
    assert not target.exists()  # Only what it copied went (the folder was made by the move).
    status = L.facts()
    assert status['move']['phase'] == 'error' and 'Taşınamadı' in status['move']['error'] and status['old'] == []
    assert parts.started == [('embedding', 'running')]  # What ran runs again, from the old place.
    monkeypatch.setattr('services.shared.content_migration.shutil.copyfileobj', copy)
    assert L.run_move(str(target)) == 0  # The failed one does not block the next.
    assert P.models_dir('embedding') == target / 'embedding'


def test_a_move_cut_short_is_settled_by_the_next(parts, tmp_path, monkeypatch):
    from services.shared import content_migration
    class Crash(BaseException):
        pass
    def cut(phase, journal):
        if phase == 'copying' and journal['copied_files']:
            raise Crash()
    real = content_migration.begin_content_migration
    monkeypatch.setattr(L, 'begin_content_migration', lambda *a, **k: real(*a, **{**k, 'fault': cut}))
    with pytest.raises(Crash):  # Orion closed in the middle: nothing ran after it.
        L.run_move(str(tmp_path / 'yarım'))
    assert (tmp_path / 'yarım').exists()
    # Cut short: the old folder is untouched and still the place.
    assert (P.location()[1] / 'embedding/jina-v5-omni-nano/model.gguf').is_file()
    monkeypatch.setattr(L, 'begin_content_migration', real)
    assert L.run_move(str(tmp_path / 'tam')) == 0
    assert not (tmp_path / 'yarım').exists() and P.models_dir('embedding') == tmp_path / 'tam' / 'embedding'


def test_where_the_models_would_go_is_told_before_anything_moves(parts, tmp_path):
    busy = tmp_path / 'Belgeler'
    busy.mkdir()
    (busy / 'not.txt').write_text('x', encoding='utf-8')
    answer = L.check(str(busy))
    # A folder with other files in it: the models go into a folder of their own there.
    assert answer['path'] == str(busy / L.SUBFOLDER) and answer['problem'] is None and answer['free_bytes'] > 0
    assert L.check(str(tmp_path / 'boş'))['path'] == str(tmp_path / 'boş')
    here = P.location()[1]
    assert L.check(str(here))['problem'] == 'Modeller zaten burada.'
    assert 'içinde' in L.check(str(here / 'embedding' / 'x'))['problem']
    assert 'Tam bir yol' in L.check('göreli/klasör')['problem']
    assert not L.check(str(tmp_path / 'boş'))['problem']
    assert not (tmp_path / 'boş').exists()  # Checking makes nothing.


def test_a_move_that_would_not_fit_is_refused_before_anything_stops(parts, tmp_path, monkeypatch):
    real = shutil.disk_usage
    monkeypatch.setattr(L.shutil, 'disk_usage', lambda path: real(path)._replace(free=1000))
    with pytest.raises(P.PartError, match='yer yetmiyor'):
        L.start_move(str(tmp_path / 'küçük disk'))
    assert parts.spawned == [] and parts.stopped == [] and L.job() is None


def test_while_the_models_move_nothing_starts_downloads_or_deletes(parts):
    P.write_part_config('embedding', enabled=True, model='jina-v5-omni-nano')
    moving_now(parts.home)
    assert P.moving()
    for action in (lambda: P.start_job('embedding', 'install'), lambda: P.start('embedding'),
                   lambda: P.delete_model('embedding', 'jina-v5-omni-nano'), lambda: P.remove('embedding'),
                   lambda: P.add_user_model('stt', 'yeni'), lambda: L.start_move(str(parts.home / 'x'))):
        with pytest.raises(P.PartError, match='taşınıyor'):
            action()
    P.keep_running()
    assert parts.started == []
    # A move whose process is gone is said to be cut short, and no longer holds anything.
    P._write_json(L._job_path(), {'phase': 'copy', 'pid': 999999999, 'started': 0})
    assert not P.moving() and L.job()['phase'] == 'interrupted'


def test_the_location_is_facts_only(parts, monkeypatch):
    """The page words the progress and works out whether the models fit: the Hub says neither."""
    monkeypatch.setattr(P, '_spawn', lambda argv, module=None: SimpleNamespace(pid=os.getpid()))  # Still running.
    location = P.status()['location']
    assert set(location) == {'path', 'bytes', 'movable', 'move', 'old'}
    L.start_move(str(parts.home.parent / 'yeni'))
    move = L.facts()['move']
    assert move['phase'] == 'queued' and move['total'] == location['bytes'] and move['done'] == 0
    assert not {'label', 'fits', 'percent', 'text'} & set(move)


def test_only_this_pcs_window_moves_the_models(parts, monkeypatch, tmp_path):
    from orion.api import parts_routes
    from orion.api.auth_routes import get_current_user
    app = FastAPI()
    app.include_router(parts_routes.router)
    app.dependency_overrides[get_current_user] = lambda: 'alice'
    client = TestClient(app)
    monkeypatch.setattr(parts_routes, 'local_control', lambda request: False)
    assert client.get('/api/v1/parts').json()['location']['path'] == str(P.location()[1])  # Seen by everyone.
    for path in ('check', 'move', 'pick', 'old/delete', 'old/keep'):
        assert client.post(f'/api/v1/parts/location/{path}', json={'path': str(tmp_path)}).status_code == 403
    monkeypatch.setattr(parts_routes, 'local_control', lambda request: True)
    assert client.post('/api/v1/parts/location/check', json={'path': str(tmp_path / 'yeni')}).json()['problem'] is None
    answer = client.post('/api/v1/parts/location/move', json={'path': str(tmp_path / 'yeni')})
    assert answer.status_code == 200 and answer.json()['location']['move']['destination'] == str(tmp_path / 'yeni')
    assert parts.spawned == [['move', str(tmp_path / 'yeni')]]
    refused = client.post('/api/v1/parts/location/old/delete', json={'path': str(tmp_path)})
    assert refused.status_code == 400 and 'eski kopya yok' in refused.json()['detail']
