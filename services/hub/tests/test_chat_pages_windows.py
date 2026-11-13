"""Chat list pages, history windows, the model's tail, inline media migration and the account key cache.

The database tests run on a NEW temporary PostgreSQL cluster (the one test_chat_media_storage builds), never
on the Hub's own."""
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
from fastapi import HTTPException

import test_chat_media_storage as base
from orion.api.services import job_service
from orion.contracts.settings import RuntimeSettings
from orion.kernel import media_migration, media_store, registry, router_accounts
from orion.kernel.chat_history import visible_history
from orion.worker.services.tool_history import model_history


def turn(index, answer_chars=20, failed=False):
    user = {'role': 'user', 'turn_id': f't{index}', 'content': f'q{index} ' + 'x' * answer_chars}
    reply = {'role': 'assistant', 'turn_id': f't{index}', 'content': f'a{index} ' + 'y' * answer_chars}
    if failed:
        user['failed_turn'] = reply['failed_turn'] = True
        reply['generation_error'] = 'offline'
    return [user, reply]


def chat(turns, **kwargs):
    return [message for index in range(turns) for message in turn(index, **kwargs)]


class PgCase(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(base.ChatMediaTests.setUpClass.__func__)
    tearDownClass = classmethod(base.ChatMediaTests.tearDownClass.__func__)

    async def asyncSetUp(self):
        async def connection(*args, **kwargs):
            return await asyncpg.connect(self.url, command_timeout=10)
        self.connection = connection
        for guard in (patch.object(registry, '_connect', connection), patch.object(registry, '_tables_ready', False)):
            guard.start()
            self.addCleanup(guard.stop)
        conn = await connection()
        try:
            await registry._ensure_tables(conn)
            await media_store.ensure_table(conn)
            await conn.execute('truncate orion_chats cascade')
            await conn.execute("insert into orion_chats(id,user_id) values('chat','alice')")
        finally:
            await conn.close()


class ChatListPageTests(PgCase):
    async def seed(self, count=250, ties=True):
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        conn = await self.connection()
        try:
            await conn.execute('truncate orion_chats cascade')
            rows = []
            for index in range(count):
                # Every 5 chats share one updated_at: only the id keeps their order stable.
                stamp = start + timedelta(minutes=index // 5 if ties else index)
                rows.append((f'c{index:04d}', 'alice', f'Sohbet {index}', stamp, stamp, index % 50 == 7))
            await conn.executemany(
                'insert into orion_chats(id,user_id,title,created_at,updated_at,assistant) values($1,$2,$3,$4,$5,$6)', rows)
            await conn.execute("insert into orion_chats(id,user_id,title) values('bob-chat','bob','Bob')")
        finally:
            await conn.close()

    def cursor(self, chat):
        return job_service.parse_chat_cursor(f"{chat['updated_at']}|{chat['chat_id']}")

    async def test_two_pages_equal_the_first_hundred_of_the_whole_list(self):
        await self.seed()
        everything = await registry.get_user_chats_db('alice', include_assistant=True)
        first = await registry.get_user_chats_db('alice', limit=100)
        second = await registry.get_user_chats_db('alice', limit=100, before=self.cursor(first[-1]))
        self.assertEqual(len(everything), 250)
        self.assertEqual(first + second, everything[:200])
        third = await registry.get_user_chats_db('alice', limit=100, before=self.cursor(second[-1]))
        self.assertEqual(first + second + third, everything)
        self.assertEqual(await registry.get_user_chats_db('alice', limit=100, before=self.cursor(third[-1])), [])

    async def test_chats_with_the_same_updated_at_are_ordered_by_id_and_none_is_skipped(self):
        await self.seed(count=40)
        everything = await registry.get_user_chats_db('alice')
        for earlier, later in zip(everything, everything[1:]):
            self.assertGreaterEqual((earlier['updated_at'], earlier['chat_id']), (later['updated_at'], later['chat_id']))
        seen, before = [], None
        while True:  # Pages of 3 cut through groups of 5 equal timestamps.
            page = await registry.get_user_chats_db('alice', limit=3, before=before)
            if not page:
                break
            seen += page
            before = self.cursor(page[-1])
        self.assertEqual(seen, everything)
        self.assertEqual(len({chat['chat_id'] for chat in seen}), 40)

    async def test_the_assistant_chat_is_left_out_of_the_page_and_other_users_chats_never_appear(self):
        await self.seed(count=60)
        page = await registry.get_user_chats_db('alice', limit=500, include_assistant=False)
        self.assertEqual(len(page), 60 - 2)  # c0007 and c0057
        self.assertFalse({'c0007', 'c0057'} & {chat['chat_id'] for chat in page})
        self.assertNotIn('bob-chat', {chat['chat_id'] for chat in await registry.get_user_chats_db('alice')})
        everyone = await registry.get_all_chats_db()
        self.assertIn('bob-chat', {chat['chat_id'] for chat in everyone})
        self.assertEqual(len(everyone), 61)

    def test_cursor_parsing(self):
        moment = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(job_service.parse_chat_cursor(None))
        self.assertIsNone(job_service.parse_chat_cursor(''))
        self.assertEqual(job_service.parse_chat_cursor(f'{moment.isoformat()}|abc'), (moment, 'abc'))
        # A chat id may hold the separator: only the first one splits.
        self.assertEqual(job_service.parse_chat_cursor(f'{moment.isoformat()}|a|b')[1], 'a|b')

    def test_an_invalid_cursor_is_a_422(self):
        for bad in ('garbage', 'nope|abc', '2026-11-01T12:00:00+00:00', '2026-11-01T12:00:00+00:00|',
                    '2026-11-01T12:00:00|abc', '|abc'):
            with self.subTest(cursor=bad):
                with self.assertRaises(HTTPException) as raised:
                    job_service.parse_chat_cursor(bad)
                self.assertEqual(raised.exception.status_code, 422)

    async def test_the_service_returns_pages_without_the_assistant_chat_and_a_database_error_is_a_503(self):
        await self.seed(count=30)
        with patch('orion.api.services.assistant_chat.hide_assistant_chat', AsyncMock(side_effect=lambda r, u, chats: chats)):
            page = await job_service.get_user_chats(AsyncMock(), 'alice', limit=10)
            self.assertEqual(len(page), 10)
            self.assertTrue(all(chat['channel'] for chat in page))
            nxt = await job_service.get_user_chats(AsyncMock(), 'alice', limit=10, before=self.cursor(page[-1]))
            self.assertFalse({c['chat_id'] for c in page} & {c['chat_id'] for c in nxt})
        with patch.object(job_service, 'get_user_chats_db', AsyncMock(side_effect=RuntimeError('down'))), \
                self.assertLogs(level='ERROR'):
            with self.assertRaises(HTTPException) as raised:
                await job_service.get_user_chats(AsyncMock(), 'alice')
        self.assertEqual(raised.exception.status_code, 503)

    async def test_titles_are_searched_in_the_database(self):
        await self.seed(count=12, ties=False)
        found = await registry.search_chat_titles_db('alice', ['sohbet', '11'])
        self.assertEqual([row['title'] for row in found], ['Sohbet 11'])
        self.assertEqual(await registry.search_chat_titles_db('alice', []), [])
        self.assertEqual(await registry.search_chat_titles_db('bob', ['sohbet']), [])


class WindowTests(PgCase):
    async def store(self, messages, chat_id='chat'):
        await registry.insert_messages(chat_id, messages)

    def old_way(self, full, limit=None, before_turn=None):
        """The window over the whole visible history: the last `limit` messages reaching back to the start of
        their oldest turn, plus the one message before them when older ones exist."""
        history = visible_history(full)
        if limit is None:
            return job_service.history_window(history, None, before_turn)
        end = len(history)
        if before_turn is not None:
            end = next((i for i, m in enumerate(history) if m.get('turn_id') == before_turn), None)
            if end is None:
                return []
        if end == 0:
            return []
        start = max(0, end - limit)
        while start > 0 and history[start]['role'] != 'user':
            start -= 1
        return history[max(0, start - 1):end]

    async def new_way(self, limit=None, before_turn=None, max_chars=None):
        window = await registry.get_chat_window_db('chat', limit, before_turn, max_chars)
        if window is None:
            return None
        messages, size = window
        return visible_history(messages)[-size:] if size else []

    async def assert_same(self, full, **kwargs):
        expected = self.old_way(full, kwargs.get('limit'), kwargs.get('before_turn'))
        if kwargs.get('before_turn') is not None and expected == [] and not any(
                m.get('turn_id') == kwargs['before_turn'] for m in full):
            expected = None  # The old window gave [] for an unknown turn; the database says None.
        self.assertEqual(await self.new_way(**kwargs), expected, kwargs)

    async def test_windows_equal_the_old_window_of_the_whole_visible_history(self):
        full = chat(4) + turn(4, failed=True) + chat(8)[0:0]
        full += [{**message, 'turn_id': f'u{i}'} for i, message in enumerate(chat(6))]
        await self.store(full)
        for limit in (1, 2, 4, 5, 6, 9, 20, 200):
            await self.assert_same(full, limit=limit)
        for before in ('t0', 't1', 't3', 't4', 'u0', 'u2', 'u5'):
            for limit in (2, 4, 7, 50):
                await self.assert_same(full, limit=limit, before_turn=before)

    async def test_without_a_limit_the_window_is_everything(self):
        full = chat(5)
        await self.store(full)
        self.assertEqual(await self.new_way(), self.old_way(full))
        self.assertEqual(await self.new_way(before_turn='t3'), self.old_way(full, before_turn='t3'))

    async def test_a_window_with_tool_messages_starts_at_the_beginning_of_its_turn(self):
        full = chat(3)
        full[3:3] = [{'role': 'assistant', 'turn_id': 't1', 'content': '', 'tool_calls': [{'id': 'x'}]},
                     {'role': 'tool', 'turn_id': 't1', 'tool_call_id': 'x', 'content': 'result'}]
        await self.store(full)
        for limit in (1, 2, 3, 4):
            await self.assert_same(full, limit=limit)

    async def test_an_unknown_turn_is_none_and_an_empty_chat_is_empty(self):
        await self.store(chat(3))
        self.assertIsNone(await registry.get_chat_window_db('chat', 4, 'gone'))
        self.assertEqual(await registry.get_chat_window_db('chat', 4, 't0'), ([], 0))
        conn = await self.connection()
        try:
            await conn.execute("insert into orion_chats(id,user_id) values('empty','alice')")
        finally:
            await conn.close()
        self.assertEqual(await registry.get_chat_window_db('empty', 4), ([], 0))

    async def test_a_window_reports_one_more_message_only_when_older_ones_exist(self):
        await self.store(chat(5))  # 10 messages
        messages, size = await registry.get_chat_window_db('chat', 4)
        self.assertEqual(size, 5)
        self.assertEqual(len(visible_history(messages)[-size:]), 5)
        messages, size = await registry.get_chat_window_db('chat', 10)
        self.assertEqual(size, 10)
        messages, size = await registry.get_chat_window_db('chat', 50)
        self.assertEqual((len(messages), size), (10, 10))

    async def test_max_chars_cuts_the_window_by_readable_text_and_keeps_at_least_one_message(self):
        await self.store(chat(6, answer_chars=100))  # Each message is about 105 characters.
        messages, size = await registry.get_chat_window_db('chat', 50, None, 250)
        # Three messages reach 250, back to their turn's start: four; one older exists.
        self.assertEqual(size, 5)
        self.assertEqual(visible_history(messages)[-size:][-4:], visible_history(messages)[-4:])
        _, size = await registry.get_chat_window_db('chat', 50, None, 1)
        self.assertEqual(size, 3)  # One message with its question, plus the sign that older ones exist.
        _, size = await registry.get_chat_window_db('chat', 2, None, 100_000)
        self.assertEqual(size, 3)  # The limit still applies.
        _, size = await registry.get_chat_window_db('chat', 50, None, 100_000)
        self.assertEqual(size, 12)

    async def test_one_answer_longer_than_max_chars_comes_with_its_question_and_no_older_sign(self):
        # The only turn of a chat, its answer past max_chars: a client dropped the question as the "older"
        # message, then the answer as the rest of a cut turn, and the chat opened empty.
        await self.store(turn(0, answer_chars=20_000))
        messages, size, older = await registry.get_chat_window_db('chat', 60, None, 12_000, with_older=True)
        self.assertEqual((len(messages), size, older), (2, 2, False))
        await self.store(turn(1, answer_chars=20_000))
        messages, size, older = await registry.get_chat_window_db('chat', 60, None, 12_000, with_older=True)
        self.assertEqual((size, older), (3, True))
        self.assertEqual([m['content'][:2] for m in visible_history(messages)[-size:]], ['a0', 'q1', 'a1'])

    async def test_the_history_service_uses_the_window_and_a_database_error_is_a_503(self):
        await self.store(chat(5))
        redis = AsyncMock()
        redis.hgetall.return_value = {}
        with patch.object(job_service, 'ensure_chat_access', AsyncMock()):
            page = await job_service.get_chat_history(redis, 'alice', 'chat', limit=4)
            expected = self.old_way(chat(5), limit=4)
            # Older messages exist: the extra first message carries the marker; the rest is the old window.
            self.assertEqual(page, [{**expected[0], 'has_older': True}, *expected[1:]])
            whole = await job_service.get_chat_history(redis, 'alice', 'chat', limit=50)
            self.assertNotIn('has_older', whole[0])
            self.assertEqual(await job_service.get_chat_history(redis, 'alice', 'chat', limit=4, before_turn='gone'), [])
            with patch.object(registry, 'get_chat_window_db', AsyncMock(side_effect=RuntimeError('down'))), \
                    self.assertLogs(level='ERROR'):
                with self.assertRaises(HTTPException) as raised:
                    await job_service.get_chat_history(redis, 'alice', 'chat', limit=4)
        self.assertEqual(raised.exception.status_code, 503)

    async def test_older_rows_get_their_turn_id_and_are_found_by_it(self):
        await self.store(chat(3))
        conn = await self.connection()
        try:
            await conn.execute('update orion_chat_messages set turn_id = null')
        finally:
            await conn.close()
        # Before the column is filled the window still finds the turn in the message text.
        self.assertEqual((await registry.get_chat_window_db('chat', 50, 't1'))[1], 2)
        self.assertEqual(await registry.index_message_turns(batch=4), 4)
        self.assertEqual(await registry.index_message_turns(batch=4), 2)
        self.assertEqual(await registry.index_message_turns(), 0)
        conn = await self.connection()
        try:
            self.assertEqual(await conn.fetchval("select count(*) from orion_chat_messages where turn_id = 't1'"), 2)
        finally:
            await conn.close()


class TailTests(PgCase):
    async def test_the_tail_starts_at_a_turn_and_counts_the_successful_turns_before_it(self):
        full = chat(2) + turn(2, failed=True) + chat(5)[6:]  # t0 t1 (t2 failed) t3 t4
        await registry.insert_messages('chat', full)
        tail, prior = await registry.get_chat_tail_db('chat', 4)
        self.assertEqual([m['turn_id'] for m in tail], ['t3', 't3', 't4', 't4'])
        self.assertEqual(prior, 2)  # t0 and t1; the failed t2 does not count.
        tail, prior = await registry.get_chat_tail_db('chat', 5)  # Reaches into t3's user message... then t3 starts.
        self.assertEqual(prior, 2)
        tail, prior = await registry.get_chat_tail_db('chat', 100)
        self.assertEqual((len(tail), prior), (len(full), 0))
        self.assertEqual(await registry.get_chat_tail_db('missing', 10), ([], 0))

    async def test_the_models_history_is_the_same_from_the_tail_as_from_the_whole_chat(self):
        full = chat(40)
        full[30:32] = turn(15, failed=True)
        await registry.insert_messages('chat', full)
        for max_messages, slide in ((8, 1), (8, 3), (12, 4), (20, 6), (6, 5)):
            whole = model_history([m for m in full], max_messages, slide)
            tail, prior = await registry.get_chat_tail_db('chat', 4 * max_messages)
            self.assertEqual(model_history(tail, max_messages, slide, prior_turns=prior), whole, (max_messages, slide))


class ModelHistoryPriorTurnsTests(unittest.TestCase):
    def test_prior_turns_keep_the_window_start_where_the_whole_chat_puts_it(self):
        full = chat(30)
        for max_messages, slide in ((6, 1), (8, 2), (8, 4), (12, 5), (10, 3)):
            expected = model_history(full, max_messages, slide)
            for tail_turns in (10, 15, 29):
                tail = full[-2 * tail_turns:]
                self.assertEqual(model_history(tail, max_messages, slide, prior_turns=30 - tail_turns), expected,
                                 (max_messages, slide, tail_turns))

    def test_the_window_start_moves_only_every_few_turns(self):
        starts = set()
        for turns in range(20, 32):
            kept = model_history(chat(turns), 18, 3)
            starts.add(kept[0]['content'][:3])
        self.assertLess(len(starts), 12)  # Consecutive requests share their prefix until the window moves.

    def test_without_prior_turns_a_tail_counts_from_its_own_start(self):
        tail = chat(30)[-12:]
        self.assertEqual(model_history(tail, 8, 4, prior_turns=0)[0]['role'], 'user')


class MediaMigrationTests(PgCase):
    PNG = 'data:image/png;base64,aGVsbG8='  # "hello"
    WAV = 'ZGF0YQ=='  # "data"

    async def migrate(self, message):
        conn = await self.connection()
        try:
            changed = await media_migration._migrate_message(conn, 'chat', message)
            stored = await conn.fetchval('select count(*) from orion_chat_media where chat_id = $1', 'chat')
            return changed, stored
        finally:
            await conn.close()

    async def test_an_attachment_data_url_becomes_a_reference(self):
        message = {'role': 'user', 'content': 'bak', 'attachments': [
            {'name': 'a.png', 'mime_type': 'image/png', 'isText': False, 'data': self.PNG}]}
        changed, stored = await self.migrate(message)
        item = message['attachments'][0]
        self.assertTrue(changed)
        self.assertNotIn('data', item)
        self.assertEqual((item['mime_type'], item['size'], stored), ('image/png', 5, 1))
        self.assertEqual(await media_store.read_media('chat', item['media_id']), ('image/png', b'hello'))
        self.assertEqual((message['content'], item['name']), ('bak', 'a.png'))

    async def test_image_audio_and_video_parts_of_a_message_become_hub_media(self):
        message = {'role': 'user', 'content': [
            {'type': 'text', 'text': 'merhaba'},
            {'type': 'image_url', 'image_url': {'url': self.PNG}},
            {'type': 'input_audio', 'input_audio': {'data': self.WAV, 'format': 'mp3'}},
            {'type': 'input_video', 'input_video': {'data': 'dmlkZW8=', 'format': 'webm'}}]}
        changed, stored = await self.migrate(message)
        text, image, audio, video = message['content']
        self.assertTrue(changed)
        self.assertEqual(text, {'type': 'text', 'text': 'merhaba'})
        for part, mime in ((image, 'image/png'), (audio, 'audio/mpeg'), (video, 'video/webm')):
            self.assertEqual(part['type'], 'hub_media')
            self.assertEqual(part['attachment']['mime_type'], mime)
            self.assertEqual(set(part['attachment']), {'mime_type', 'media_id', 'media_path', 'size'})
        self.assertEqual(stored, 3)

    async def test_an_assistant_recording_becomes_a_reference_and_keeps_its_other_fields(self):
        message = {'role': 'assistant', 'content': 'selam', 'turn_id': 't', 'audio': {
            'audio': self.WAV, 'format': 'wav', 'sample_rate': 24000, 'arrival_ms': 2100}}
        changed, stored = await self.migrate(message)
        audio = message['audio']
        self.assertTrue(changed)
        self.assertNotIn('audio', audio)
        self.assertEqual((audio['format'], audio['sample_rate'], audio['arrival_ms'], audio['mime_type']),
                         ('wav', 24000, 2100, 'audio/wav'))
        self.assertEqual(await media_store.read_media('chat', audio['media_id']), ('audio/wav', b'data'))
        self.assertEqual((message['content'], stored), ('selam', 1))

    async def test_text_and_references_and_urls_are_left_alone(self):
        message = {'role': 'user', 'content': [
            {'type': 'text', 'text': 'merhaba'},
            {'type': 'image_url', 'image_url': {'url': 'https://example.com/a.png'}},
            {'type': 'hub_media', 'attachment': {'media_id': 'x'}}],
            'attachments': [
                {'name': 'notes.txt', 'isText': True, 'data': 'data:text/plain;base64,aGVsbG8='},
                {'name': 'a.png', 'isText': False, 'media_id': 'abc', 'mime_type': 'image/png'},
                {'name': 'u', 'isText': False, 'data': 'https://example.com/u.png'}],
            'audio': {'media_id': 'abc', 'format': 'wav'}}
        before = json.dumps(message, sort_keys=True)
        changed, stored = await self.migrate(message)
        self.assertEqual((changed, stored), (False, 0))
        self.assertEqual(json.dumps(message, sort_keys=True), before)
        plain = {'role': 'user', 'content': 'sadece metin'}
        self.assertEqual(await self.migrate(plain), (False, 0))

    async def test_only_large_messages_are_opened_and_a_broken_one_is_left_in_place(self):
        pixel = 'data:image/png;base64,' + 'QUJD' * 30_000  # ~120 000 characters
        big = {'role': 'user', 'content': 'resim', 'attachments': [{'name': 'a.png', 'isText': False, 'data': pixel}]}
        broken = {'role': 'user', 'content': 'bozuk', 'attachments': [
            {'name': 'b.png', 'isText': False, 'data': 'data:image/png;base64,@@@' + 'x' * 120_000}]}
        small = {'role': 'user', 'content': 'k', 'attachments': [{'name': 'c.png', 'isText': False, 'data': self.PNG}]}
        await registry.insert_messages('chat', [big, broken, small])
        with self.assertLogs(level='WARNING'):
            moved = await media_migration.migrate_inline_media()
        self.assertEqual(moved, 1)
        conn = await self.connection()
        try:
            rows = await conn.fetch('select content_json from orion_chat_messages order by id')
            self.assertEqual(await conn.fetchval('select count(*) from orion_chat_media'), 1)
        finally:
            await conn.close()
        stored = [json.loads(row['content_json']) for row in rows]
        self.assertNotIn('data', stored[0]['attachments'][0])
        self.assertIn('media_id', stored[0]['attachments'][0])
        self.assertIn('data', stored[1]['attachments'][0])  # Left in place.
        self.assertIn('data', stored[2]['attachments'][0])  # Small: never opened.
        self.assertEqual(await media_migration.migrate_inline_media(), 0)  # Nothing left to do.

    async def test_a_chat_deleted_meanwhile_is_skipped(self):
        pixel = 'data:image/png;base64,' + 'QUJD' * 30_000
        await registry.insert_messages('chat', [{'role': 'user', 'content': 'r', 'attachments': [
            {'name': 'a.png', 'isText': False, 'data': pixel}]}])
        with patch.object(media_migration, '_migrate_message', AsyncMock(side_effect=AssertionError('no chat row'))), \
                patch.object(registry, '_connect', self.connection):
            conn = await self.connection()
            try:
                await conn.execute("delete from orion_chats where id='chat'")
            finally:
                await conn.close()
            self.assertEqual(await media_migration.migrate_inline_media(), 0)


class AccountKeyCacheTests(unittest.IsolatedAsyncioTestCase):
    class Bound:
        route = 'route'

        def __init__(self, n):
            self.n = n

        def __str__(self):
            return f'sk-key-{self.n}'

    async def asyncSetUp(self):
        router_accounts.forget_account_keys()
        self.addCleanup(router_accounts.forget_account_keys)
        self.disk = 0
        self.bound = []

        async def selected():
            self.disk += 1
            return {'id': 'r1'}

        async def identity(user_id):
            return 'subject', None

        async def account_key(user_id, record, subject, key):
            if self.fail:
                raise RuntimeError('Router unreachable')
            item = self.Bound(len(self.bound) + 1)
            self.bound.append(item)
            return item

        self.fail = False
        for guard in (patch.object(router_accounts, 'selected_router', selected),
                      patch.object(router_accounts, 'account_identity', identity),
                      patch.object(router_accounts, '_credential', lambda *a: 'secret'),
                      patch.object(router_accounts, 'provision_if_available', AsyncMock()),
                      patch('orion.kernel.router_access.account_key', account_key)):
            guard.start()
            self.addCleanup(guard.stop)
        self.settings = RuntimeSettings()

    async def key(self, user='alice'):
        return (await router_accounts.account_settings(self.settings, user)).router_api_key

    async def test_a_second_call_within_thirty_seconds_does_not_go_to_disk(self):
        self.assertEqual(await self.key(), 'sk-key-1')
        self.assertEqual(await self.key(), 'sk-key-1')
        self.assertEqual(self.disk, 1)
        self.assertEqual(await self.key('bob'), 'sk-key-2')  # Another account binds on its own.
        self.assertEqual(self.disk, 2)
        self.assertEqual(router_accounts.ACCOUNT_KEY_SECONDS, 30.0)

    async def test_the_key_is_bound_again_after_thirty_seconds(self):
        await self.key()
        user, (expires, bound) = next(iter(router_accounts._bound_keys.items()))
        self.assertAlmostEqual(expires - time.monotonic(), 30.0, delta=2.0)
        router_accounts._bound_keys[user] = (time.monotonic() - 1, bound)
        self.assertEqual(await self.key(), 'sk-key-2')
        self.assertEqual(self.disk, 2)

    async def test_forgetting_makes_the_next_call_bind_again(self):
        await self.key('alice')
        await self.key('bob')
        router_accounts.forget_account_keys('alice')
        self.assertEqual(await self.key('alice'), 'sk-key-3')
        await self.key('bob')
        self.assertEqual(self.disk, 3)  # Bob was kept.
        router_accounts.forget_account_keys()
        await self.key('bob')
        self.assertEqual(self.disk, 4)

    async def test_a_failure_is_never_cached(self):
        self.fail = True
        with self.assertRaises(RuntimeError):
            await self.key()
        self.assertEqual(router_accounts._bound_keys, {})
        self.fail = False
        self.assertEqual(await self.key(), 'sk-key-1')
        self.assertEqual(self.disk, 2)

    async def test_the_global_account_never_binds_or_caches(self):
        self.assertEqual(await self.key('global'), self.settings.router_api_key)
        self.assertEqual((self.disk, router_accounts._bound_keys), (0, {}))

    async def test_saving_a_manual_key_forgets_the_cached_keys(self):
        from orion.kernel import router_access
        await self.key()
        with patch.object(router_access, 'manual_path', side_effect=RuntimeError('stop here')):
            with self.assertRaises(RuntimeError):
                router_access.save_manual('r1', 'subject', key='k')
        self.assertEqual(router_accounts._bound_keys, {})
