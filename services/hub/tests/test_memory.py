import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from orion.api import routes
from orion.kernel import memory, registry
from orion.worker.services import router as router_module
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import get_registry
from orion.worker.tools.memory import tools as memory_tools


class FakeConn:
    def __init__(self, extension=True, rows=None, other=0):
        self.extension, self.rows, self.other = extension, rows or [], other
        self.executed, self.inserted, self.closed, self.existing = [], [], False, set()

    async def fetchval(self, sql, *args):
        if 'pg_extension' in sql:
            return 1 if self.extension else None
        if sql.startswith('select 1 from orion_memory'):
            return 1 if (args[2], args[1]) in self.existing else None
        return self.other

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def executemany(self, sql, rows):
        self.inserted.extend(rows)

    async def fetch(self, sql, *args):
        self.fetch_args = args
        return self.rows

    async def close(self):
        self.closed = True


class Settings:
    router_model_group = 'chat'

    def model_copy(self, update):
        return SimpleNamespace(**{**vars(self), **update})


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(memory, '_table_ready', False)


def wire(monkeypatch, conn, space='embeddinggemma-2', history=None):
    async def connect():
        return conn

    async def target(settings):
        return settings, space, True

    async def embed(text, settings, kind, local):
        return [[0.5] * 768]

    async def messages(chat_id):
        return history if history is not None else [
            {'role': 'system', 'content': 'gizli'},
            {'role': 'user', 'content': 'Benim adım Ayşe.'},
            {'role': 'assistant', 'content': 'Memnun oldum Ayşe.'},
            {'role': 'tool', 'content': 'x'}]

    monkeypatch.setattr(memory, '_connect', connect)
    monkeypatch.setattr(memory, 'embedding_target', target)
    monkeypatch.setattr(memory, '_embed', embed)
    monkeypatch.setattr(registry, 'get_chat_history_db', messages)


def test_chat_text_keeps_only_what_the_user_and_the_assistant_said():
    text = memory.chat_text([{'role': 'system', 'content': 'a'}, {'role': 'user', 'content': 'Merhaba'},
                             {'role': 'assistant', 'content': 'Selam'}, {'role': 'tool', 'content': 'b'}])
    assert text == 'Kullanıcı: Merhaba\n\nAsistan: Selam'


def test_save_chat_stores_one_row_per_chunk_with_its_own_text_and_vector_space(monkeypatch):
    conn = FakeConn()
    wire(monkeypatch, conn)
    assert asyncio.run(memory.save_chat('u1', 'c1', 'Tanışma', Settings())) == 1
    user, text, space, vector, title, source = conn.inserted[0]
    assert source == 'chat_save'
    assert (user, space, title) == ('u1', 'embeddinggemma-2', 'Tanışma')
    assert 'Benim adım Ayşe.' in text and 'gizli' not in text
    assert vector.startswith('[0.5,') and vector.count(',') == 767
    assert conn.closed and any('create table if not exists orion_memory' in sql for sql in conn.executed)


def test_the_memory_table_does_not_reference_the_chat(monkeypatch):
    conn = FakeConn()
    wire(monkeypatch, conn)
    asyncio.run(memory.save_chat('u1', 'c1', '', Settings()))
    ddl = next(sql for sql in conn.executed if 'create table' in sql)
    assert 'references' not in ddl and 'chat_id' not in ddl


def test_an_empty_chat_is_a_clear_error(monkeypatch):
    wire(monkeypatch, FakeConn(), history=[{'role': 'system', 'content': 'x'}])
    with pytest.raises(memory.MemoryUnavailable, match='kaydedilecek metin yok'):
        asyncio.run(memory.save_chat('u', 'c', '', Settings()))


def test_without_the_vector_extension_the_answer_is_turkish_and_nothing_crashes(monkeypatch):
    conn = FakeConn(extension=False)
    wire(monkeypatch, conn)
    with pytest.raises(memory.MemoryUnavailable, match='pgvector'):
        asyncio.run(memory.save_chat('u', 'c', '', Settings()))
    assert conn.closed and not conn.inserted


def test_search_returns_texts_with_dates_and_counts_other_vector_spaces(monkeypatch):
    rows = [{'id': 7, 'text': 'Kullanıcı: adım Ayşe', 'created_at': dt.datetime(2026, 11, 14, 10, 0)}]
    conn = FakeConn(rows=rows, other=3)
    wire(monkeypatch, conn)
    found = asyncio.run(memory.search('u1', 'adım ne', Settings()))
    assert found == {'rows': [{'id': 7, 'text': 'Kullanıcı: adım Ayşe', 'date': '2026-11-14'}], 'other_spaces': 3}
    assert conn.fetch_args[:2] == ('u1', 'embeddinggemma-2') and conn.fetch_args[3] == memory.LIMIT


def _tool_settings(monkeypatch):
    async def settings(*args):
        return Settings()

    monkeypatch.setattr('orion.kernel.config.get_runtime_settings', settings)


def test_the_tool_is_registered_enabled_by_default_and_answers_in_turkish(monkeypatch):
    from orion.contracts.tools import default_tool_selection
    registry_ = get_registry()
    assert 'hafizada_ara' in registry_.enabled(default_tool_selection())
    assert registry_.categories['memory'].name == 'Hafıza'
    wire(monkeypatch, FakeConn(rows=[{'id': 3, 'text': 'adım Ayşe', 'created_at': dt.datetime(2026, 11, 14)}], other=2))
    _tool_settings(monkeypatch)
    context = ToolContext('u1', 'c', 't', None)
    result = asyncio.run(memory_tools.hafizada_ara(memory_tools.MemoryInput(sorgu='adım'), context))
    assert result['kayitlar'] == [{'id': 3, 'tarih': '2026-11-14', 'metin': 'adım Ayşe'}]
    assert result['uyari'] == '2 kayıt başka embedding modeliyle kaydedilmiş, aranmadı'


def test_the_tool_reports_the_setup_message_instead_of_raising(monkeypatch):
    async def fail(*args):
        raise memory.MemoryUnavailable(memory.SETUP_MESSAGE)

    monkeypatch.setattr(memory, 'search', fail)
    _tool_settings(monkeypatch)
    result = asyncio.run(memory_tools.hafizada_ara(memory_tools.MemoryInput(sorgu='x'), ToolContext('u', 'c', 't', None)))
    assert result == {'hata': "Hafıza için Bileşenler'de embedding bileşenini kur ve aç."}


def _target(monkeypatch, models, local_space='jina-v5-omni-nano'):
    async def catalog(**kwargs):
        return {'models': models}

    monkeypatch.setattr('orion.kernel.router_models.get_router_catalog', catalog)
    from services.shared import local_parts
    monkeypatch.setattr(local_parts, 'embedding_profile', lambda: {'vector_space': local_space})


def test_the_local_embedding_model_names_its_vector_space(monkeypatch):
    _target(monkeypatch, [{'name': 'text-embedding-3', 'provider': 'openai', 'capability': 'embed'},
                          {'name': 'local-embed', 'provider': 'local', 'capability': 'embed'}])
    settings, space, local = asyncio.run(memory.embedding_target(Settings()))
    assert (settings.router_model_group, space, local) == ('local-embed', 'jina-v5-omni-nano', True)


def test_a_cloud_embedding_model_is_a_cloud_vector_space(monkeypatch):
    _target(monkeypatch, [{'name': 'text-embedding-3', 'provider': 'openai', 'capability': 'embed'}])
    assert asyncio.run(memory.embedding_target(Settings()))[1:] == ('cloud:text-embedding-3', False)


def test_no_embedding_model_asks_to_install_the_component(monkeypatch):
    _target(monkeypatch, [{'name': 'x', 'provider': 'local', 'capability': 'chat'}])
    with pytest.raises(memory.MemoryUnavailable, match="Bileşenler'de embedding"):
        asyncio.run(memory.embedding_target(Settings()))


def test_a_local_embedding_that_does_not_answer_says_to_install_and_start_it(monkeypatch):
    async def broken(text, settings, kind):
        raise RuntimeError('connect failed')

    monkeypatch.setattr(router_module, 'embed_text', broken)
    with pytest.raises(memory.MemoryUnavailable, match="Bileşenler'de embedding"):
        asyncio.run(memory._embed('x', Settings(), 'query', True))


def _route(monkeypatch, assistant_chat, save):
    async def access(redis, user, chat_id):
        return {'name': 'Başlık'}

    async def current(redis, user):
        return assistant_chat

    async def settings(*args):
        return Settings()

    monkeypatch.setattr(routes, 'ensure_chat_access', access)
    monkeypatch.setattr('orion.api.services.assistant_chat.current_assistant_chat', current)
    monkeypatch.setattr(routes, 'get_runtime_settings', settings)
    monkeypatch.setattr(memory, 'save_chat', save)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
    return asyncio.run(routes.save_chat_to_memory('c1', request, 'u1'))


def test_the_endpoint_saves_the_assistant_chat_and_returns_the_count(monkeypatch):
    async def save(user, chat, title, settings):
        assert (user, chat, title) == ('u1', 'c1', 'Başlık')
        return 4

    assert _route(monkeypatch, 'c1', save) == {'saved': 4}


def test_the_endpoint_refuses_other_chats(monkeypatch):
    with pytest.raises(HTTPException) as error:
        _route(monkeypatch, 'another', None)
    assert error.value.status_code == 400 and 'Asistanla konuş' in error.value.detail


def test_the_endpoint_turns_a_memory_error_into_a_turkish_503(monkeypatch):
    async def save(*args):
        raise memory.MemoryUnavailable(memory.SETUP_MESSAGE)

    with pytest.raises(HTTPException) as error:
        _route(monkeypatch, 'c1', save)
    assert error.value.status_code == 503 and error.value.detail == memory.SETUP_MESSAGE


def test_saving_twice_skips_texts_already_in_the_same_space(monkeypatch):
    conn = FakeConn()
    wire(monkeypatch, conn)
    asyncio.run(memory.save_chat('u1', 'c1', '', Settings()))
    conn.existing.add((conn.inserted[0][1], 'embeddinggemma-2'))
    conn.inserted.clear()
    assert asyncio.run(memory.save_chat('u1', 'c1', '', Settings())) == 0 and not conn.inserted
    assert 'not exists' in memory.INSERT_SQL


def test_the_kind_column_is_added_idempotently(monkeypatch):
    conn = FakeConn()
    wire(monkeypatch, conn)
    asyncio.run(memory.save_chat('u1', 'c1', '', Settings()))
    assert any("add column if not exists kind text not null default 'text'" in sql for sql in conn.executed)


def test_a_note_is_embedded_and_stored_as_manual(monkeypatch):
    conn = FakeConn()
    wire(monkeypatch, conn)
    assert asyncio.run(memory.add_note('u1', '  Kızımın adı Elif. ', Settings())) == 1
    assert conn.inserted[0][1] == 'Kızımın adı Elif.' and conn.inserted[0][5] == 'manual'


@pytest.mark.parametrize('text, message', [('   ', 'boş'), ('x' * 4001, '4000')])
def test_an_empty_or_too_long_note_is_a_turkish_400(monkeypatch, text, message):
    async def settings(*args):
        return Settings()
    monkeypatch.setattr(routes, 'get_runtime_settings', settings)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.add_memory_note(routes.MemoryNote(text=text), request, 'u1'))
    assert error.value.status_code == 400 and message in error.value.detail


class ListConn(FakeConn):
    async def fetch(self, sql, *args):
        self.fetch_args = args
        if 'group by vector_space' in sql:
            return [{'key': 'embeddinggemma-2', 'n': 2}, {'key': 'jina-v5', 'n': 1}]
        if 'group by kind' in sql:
            return [{'key': 'text', 'n': 3}]
        if sql.startswith('delete'):
            self.deleted_args = args
            return [{'id': 5, 'text': 'eski'}] if 5 in args[1] else []
        return [{'id': 1, 'text': 'a', 'kind': 'text', 'vector_space': 'jina-v5', 'source_kind': 'manual',
                 'source_chat_title': None, 'created_at': dt.datetime(2026, 11, 14)}]


def test_the_list_marks_rows_of_another_space_and_summarises(monkeypatch):
    conn = ListConn(other=1)
    wire(monkeypatch, conn)
    result = asyncio.run(memory.list_items('u1', Settings(), q='ay', limit=10))
    assert result['items'][0]['searchable'] is False and result['items'][0]['id'] == 1
    assert result['summary'] == {'total': 3, 'per_vector_space': {'embeddinggemma-2': 2, 'jina-v5': 1},
                                 'per_kind': {'text': 3}, 'current_vector_space': 'embeddinggemma-2'}
    assert conn.fetch_args[0] == 'u1'


def test_delete_is_scoped_to_the_user_and_a_foreign_id_is_404(monkeypatch):
    conn = ListConn()
    wire(monkeypatch, conn)
    assert asyncio.run(routes.delete_memory_item(5, 'u1')) == {'deleted': [5]}
    assert conn.deleted_args[0] == 'u1'
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.delete_memory_item(9, 'u1'))
    assert error.value.status_code == 404
    assert asyncio.run(routes.delete_memory_items(routes.MemoryDelete(ids=[5, 9]), 'u1')) == {'deleted': [5]}


def test_add_and_delete_tools_are_on_by_default_and_report_what_they_did(monkeypatch):
    from orion.contracts.tools import default_tool_selection
    enabled = get_registry().enabled(default_tool_selection())
    assert {'hafizaya_ekle', 'hafizadan_sil'} <= set(enabled)
    conn = ListConn()
    wire(monkeypatch, conn)
    _tool_settings(monkeypatch)
    context = ToolContext('u1', 'c', 't', None)
    added = asyncio.run(memory_tools.hafizaya_ekle(memory_tools.MemoryAddInput(metin='Doğum günüm 3 Mart.'), context))
    assert added == {'eklendi': 'Doğum günüm 3 Mart.'} and conn.inserted[0][5] == 'assistant'
    gone = asyncio.run(memory_tools.hafizadan_sil(memory_tools.MemoryDeleteInput(idler=[5]), context))
    assert gone['silinenler'] == [{'id': 5, 'metin': 'eski'}]
    nothing = asyncio.run(memory_tools.hafizadan_sil(memory_tools.MemoryDeleteInput(idler=[9]), context))
    assert 'silinmedi' in nothing['not']


def test_the_memory_page_paths_pass_the_access_boundary():
    """The page's GET/POST/DELETE (and the browser's preflight) were refused with 404 before the memory paths
    were listed: the app showed "Hub bağlantısı kurulamadı"."""
    from orion.api.access_boundary import public_path
    for path in ('/api/v1/memory', '/api/v1/memory/12', '/api/v1/memory/delete'):
        assert public_path(path)
    assert not public_path('/api/v1/memoryx')
