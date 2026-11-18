"""The embedding lookup every memory search makes is cached: the local embedding profile (while the files it reads stay the
same) and the inspection of the registered Router folder (while its inventory record stays the same). Neither may serve a
stale answer after the thing it depends on changed."""
import os
import time
from types import SimpleNamespace

from services.shared import local_parts
from orion.kernel import router_bootstrap


def _count_profile(monkeypatch, tmp_path):
    monkeypatch.setenv('ORION_PARTS_HOME', str(tmp_path))
    calls = []

    def profile():
        calls.append(1)
        return {'context': 2048, 'vector_space': f'space-{len(calls)}'}
    monkeypatch.setattr(local_parts, 'embedding_profile', profile)
    monkeypatch.setitem(local_parts._profile_cache, 'stamp', None)
    return calls


def test_the_embedding_profile_is_computed_once_while_its_files_are_unchanged(monkeypatch, tmp_path):
    calls = _count_profile(monkeypatch, tmp_path)
    first = local_parts.embedding_profile_cached()
    for _ in range(5):
        assert local_parts.embedding_profile_cached() == first
    assert len(calls) == 1
    first['context'] = 1  # A caller changing its copy does not change what the next one gets.
    assert local_parts.embedding_profile_cached()['context'] == 2048


def test_the_embedding_profile_is_computed_again_when_the_config_or_the_registry_changes(monkeypatch, tmp_path):
    calls = _count_profile(monkeypatch, tmp_path)
    assert local_parts.embedding_profile_cached()['vector_space'] == 'space-1'
    (tmp_path / 'config.json').write_text('{"embedding": {"model": "other"}}', encoding='utf-8')
    assert local_parts.embedding_profile_cached()['vector_space'] == 'space-2'  # The user picked another model.
    assert local_parts.embedding_profile_cached()['vector_space'] == 'space-2'
    (tmp_path / 'location').mkdir()
    (tmp_path / 'location' / 'install.json').write_text('{}', encoding='utf-8')  # The models were moved.
    assert local_parts.embedding_profile_cached()['vector_space'] == 'space-3'
    assert len(calls) == 3


def test_the_embedding_profile_has_a_time_limit_and_follows_a_replaced_function(monkeypatch, tmp_path):
    calls = _count_profile(monkeypatch, tmp_path)
    local_parts.embedding_profile_cached()
    monkeypatch.setitem(local_parts._profile_cache, 'at', time.monotonic() - local_parts.PROFILE_SECONDS - 1)
    local_parts.embedding_profile_cached()  # A model folder edited in place is picked up by the time limit.
    assert len(calls) == 2
    monkeypatch.setattr(local_parts, 'embedding_profile', lambda: {'vector_space': 'patched'})
    assert local_parts.embedding_profile_cached() == {'vector_space': 'patched'}  # A test's replacement is not hidden.


def _candidate(status='ready'):
    return SimpleNamespace(status=status)


def test_a_good_router_inspection_is_remembered_for_the_same_record_only(monkeypatch):
    router_bootstrap.forget_local_router_candidate()
    seen = []

    def inspect():
        seen.append(1)
        return _candidate()
    record = {'kind': 'router', 'code_path': 'C:/Router', 'package': {'id': 'a', 'inventory_sha256': '1'}}
    for _ in range(4):
        assert router_bootstrap._inspected(record, inspect).status == 'ready'
    assert len(seen) == 1
    updated = {**record, 'package': {'id': 'b', 'inventory_sha256': '2'}}  # An update wrote a new package record.
    router_bootstrap._inspected(updated, inspect)
    assert len(seen) == 2
    router_bootstrap._inspected(updated, inspect)
    assert len(seen) == 2
    router_bootstrap.forget_local_router_candidate()
    router_bootstrap._inspected(updated, inspect)
    assert len(seen) == 3


def test_a_refusal_and_an_old_answer_are_looked_at_again(monkeypatch):
    router_bootstrap.forget_local_router_candidate()
    answers = [_candidate('missing-broken'), _candidate('ready'), _candidate('ready')]
    seen = []

    def inspect():
        seen.append(1)
        return answers[len(seen) - 1]
    record = {'kind': 'router', 'code_path': 'C:/Router'}
    assert router_bootstrap._inspected(record, inspect).status == 'missing-broken'
    assert router_bootstrap._inspected(record, inspect).status == 'ready'  # The Router was repaired: seen at once.
    assert router_bootstrap._inspected(record, inspect).status == 'ready'
    assert len(seen) == 2
    key = next(iter(router_bootstrap._inspect_cache))
    stamped, value = router_bootstrap._inspect_cache[key]
    router_bootstrap._inspect_cache[key] = (stamped - router_bootstrap._INSPECT_SECONDS - 1, value)
    router_bootstrap._inspected(record, inspect)
    assert len(seen) == 3
    router_bootstrap.forget_local_router_candidate()
