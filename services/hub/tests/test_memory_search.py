"""Memory search, open-media and file-add tests on a NEW temporary PostgreSQL cluster (pgvector, pg_trgm), never
the live database. Embeddings are hand-made vectors: the score of a row is the cosine set by the test."""
import asyncio
import base64
import io
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg

from orion.api import routes
from orion.contracts.settings import RuntimeSettings
from orion.kernel import media_store, memory, memory_groups, registry
from orion.worker.tools._base import ToolContext
from orion.worker.tools._registry import get_registry
from orion.worker.tools.memory import tools as memory_tools
from orion.worker.services.tool_history import model_history
from orion.worker.services.tool_runner import ToolConversation

SPACE = 'embeddinggemma-2'
GEMMA = {'model': 'EmbeddingGemma 2', 'has_extra': True, 'extra_on': True, 'extra_kinds': ['audio', 'image'],
         'image': True, 'audio': True}
JINA = {'model': 'Jina', 'has_extra': True, 'extra_on': True, 'extra_kinds': ['image'], 'image': True, 'audio': False}


def postgres_bin():
    """The repo's .local_db PostgreSQL (a worktree has none of its own: look in the checkouts above it too)."""
    for folder in Path(__file__).resolve().parents:
        binary = folder / '.local_db/postgres/bin'
        if (binary / ('initdb.exe' if os.name == 'nt' else 'initdb')).exists():
            return binary
    return None


def vec(score, axis=1):
    """A unit vector whose cosine with the query (axis 0) is `score`; `axis` keeps rows apart."""
    values = [0.0] * 768
    values[0] = score
    values[axis] = math.sqrt(max(0.0, 1 - score * score))
    return values


def png():
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (4, 4), (200, 10, 10)).save(out, format='PNG')
    return out.getvalue()


class PostgresMemoryCase(unittest.IsolatedAsyncioTestCase):
    """A throwaway PostgreSQL cluster per class, emptied before each test (no tests of its own)."""
    @classmethod
    def setUpClass(cls):
        binary = postgres_bin()
        if binary is None:
            raise unittest.SkipTest('Local PostgreSQL test binaries are unavailable')
        cls.pg_ctl = binary / ('pg_ctl.exe' if os.name == 'nt' else 'pg_ctl')
        cls.temp = tempfile.TemporaryDirectory(prefix='orion-memory-test-')
        cls.root = Path(cls.temp.name).resolve()
        cls.data = cls.root / 'data'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        cls.url = f'postgresql://postgres@127.0.0.1:{port}/postgres'

        def run(args):
            with (cls.root / 'control.log').open('ab') as log:
                subprocess.run(args, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=40,
                               creationflags=0x08000000 if os.name == 'nt' else 0)
        try:
            run([str(binary / ('initdb.exe' if os.name == 'nt' else 'initdb')), '-D', str(cls.data), '-U', 'postgres',
                 '-A', 'trust', '--no-locale', '-E', 'UTF8'])
            run([str(cls.pg_ctl), '-D', str(cls.data), '-l', str(cls.root / 'postgres.log'),
                 '-o', f'-F -h 127.0.0.1 -p {port}', '-w', 'start'])
        except BaseException:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            subprocess.run([str(cls.pg_ctl), '-D', str(cls.data), '-m', 'immediate', '-w', 'stop'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
        finally:
            cls.temp.cleanup()

    async def asyncSetUp(self):
        async def connection(*args, **kwargs):
            return await asyncpg.connect(self.url, command_timeout=10)
        for target, name, value in ((registry, '_connect', connection), (registry, '_tables_ready', False),
                                    (memory, '_table_ready', False), (memory, '_trgm', False),
                                    (media_store, '_schema_ready', False)):
            guard = patch.object(target, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        conn = await connection()
        try:
            try:
                await conn.execute('create extension if not exists vector')
            except Exception as exc:
                raise unittest.SkipTest(f'pgvector is not in the local PostgreSQL: {exc}')
            await registry._ensure_tables(conn)
            await media_store.ensure_table(conn)
            await memory.ensure_memory_table(conn)
            await conn.execute('truncate orion_memory, orion_memory_jobs, orion_chats, orion_media cascade')
            await conn.execute("insert into orion_chats(id,user_id) values('chat','alice')")
        finally:
            await conn.close()
        self.query = [1.0] + [0.0] * 767
        self.settings = SimpleNamespace(router_model_group='chat')

        async def query_vector(q, settings):
            return SPACE, memory._vector(self.query), None

        async def space(settings):
            return SPACE
        for name, value in (('_query_vector', query_vector), ('current_space', space)):
            guard = patch.object(memory, name, value)
            guard.start()
            self.addCleanup(guard.stop)

    async def add(self, text, score=0.0, kind='text', user='alice', space=SPACE, minute=0, media_id=None, axis=1,
                  group=None, source='manual', title=None, copied_from=None, chat=None, message=None, seconds=0):
        conn = await registry._connect()
        try:
            return await conn.fetchval(
                "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, kind, media_id, "
                "media_mime, created_at, group_id, source_chat_title, copied_from, source_chat_id, source_message_id) "
                "values ($1, $2, $3, $4::vector, $9, $5, $6, $7, "
                "timestamptz '2026-11-01' + $8 * interval '1 minute' + $14 * interval '1 second', $10, $11, $12, "
                "$13, $15) returning id",
                user, text, space, memory._vector(vec(score, axis)), kind, media_id,
                {'image': 'image/png', 'audio': 'audio/ogg'}.get(kind), minute, source, group, title, copied_from,
                chat, seconds, message)
        finally:
            await conn.close()


class MemorySearchTests(PostgresMemoryCase):

    async def tool(self, **kwargs):
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)):
            return await memory_tools.hafizada_ara(memory_tools.MemoryInput(**kwargs), ToolContext('alice', 'chat', 't', None))

    async def test_the_tool_pages_ten_best_first_with_scores_and_says_when_more_follow(self):
        ids = [await self.add(f'not numara {k}', score=1 - k * 0.03, axis=1 + k) for k in range(25)]
        await self.add('başkasının notu', score=1.0, user='bob')
        first = await self.tool(sorgu='uzay gemisi')
        self.assertEqual([row['id'] for row in first['kayitlar']], ids[:10])
        self.assertEqual(first['kayitlar'][0]['skor'], 1.0)
        self.assertEqual(first['kayitlar'][0]['eslesme'], 'anlam')
        self.assertTrue(first['daha_var'])
        second = await self.tool(sorgu='uzay gemisi', sayfa=2)
        self.assertEqual([row['id'] for row in second['kayitlar']], ids[10:20])
        last = await self.tool(sorgu='uzay gemisi', sayfa=3)
        self.assertEqual([row['id'] for row in last['kayitlar']], ids[20:])
        self.assertFalse(last['daha_var'])

    async def test_a_low_media_score_is_not_dropped(self):
        """Text-to-image cosine is 0.2-0.4 for a real match (no threshold any more)."""
        heart = await self.add('IMG_2041.jpg', score=0.32, kind='image', media_id='a' * 64)
        await self.add('Bugün hava güzel', score=0.07)
        found = await self.tool(sorgu='kalp')
        self.assertEqual(found['kayitlar'][0]['id'], heart)
        self.assertEqual(found['kayitlar'][0]['tur'], 'görsel')
        self.assertIn('hafizadaki_medyayi_ac', found['medya_notu'])
        self.assertNotIn('göremezsin', found['medya_notu'])
        page = await memory_groups.list_groups('alice', self.settings, q='kalp', limit=10)
        self.assertEqual((page['groups'][0]['matched_part'], page['groups'][0]['match']), (heart, 'meaning'))

    async def test_tur_without_a_query_lists_the_newest_of_that_kind(self):
        images = [await self.add(f'foto {k}.jpg', kind='image', minute=k, media_id=f'{k}' * 64) for k in range(3)]
        await self.add('metin', minute=10)
        found = await self.tool(tur='görsel')
        self.assertEqual([row['id'] for row in found['kayitlar']], images[::-1])
        self.assertEqual(found['siralama'], 'En yeni kayıt önce.')
        self.assertFalse(found['daha_var'])
        only = await self.tool(sorgu='foto', tur='metin')
        self.assertEqual([row.get('metin') for row in only['kayitlar']], ['metin'])

    async def check_near_spellings(self):
        # Minutes apart: five notes, five memories (the group backfill joins notes seconds apart).
        hello = await self.add('Kullanıcı: selam naber nasılsın', score=0.3, axis=2, minute=1)
        merhaba = await self.add('Merhaba dünya', score=0.3, axis=3, minute=2)
        city = await self.add("İstanbul'a gittim", score=0.3, axis=4, minute=3)
        light = await self.add('IŞIK yandı', score=0.3, axis=5, minute=4)
        await self.add('Bugün hava çok güzel', score=0.6, axis=6, minute=5)
        found = await self.tool(sorgu='selam nabe')  # A partial word: a plain substring.
        self.assertEqual((found['kayitlar'][0]['id'], found['kayitlar'][0]['eslesme']), (hello, 'metin'))
        typo = await self.tool(sorgu='selam nabr nasilsin')
        self.assertEqual((typo['kayitlar'][0]['id'], typo['kayitlar'][0]['eslesme']), (hello, 'benzer yazım'))
        merhba = await self.tool(sorgu='merhba')
        self.assertEqual(merhba['kayitlar'][0]['id'], merhaba, merhba)
        exact = await self.tool(sorgu='İSTANBUL')
        self.assertEqual((exact['kayitlar'][0]['id'], exact['kayitlar'][0]['eslesme']), (city, 'metin'))
        self.assertEqual((await self.tool(sorgu='ışık'))['kayitlar'][0]['id'], light)
        page = await memory_groups.list_groups('alice', self.settings, q='selam nabr nasilsin', limit=1)
        self.assertEqual((page['groups'][0]['matched_part'], page['groups'][0]['match']), (hello, 'similar'))
        self.assertTrue(page['more'])

    async def test_near_spellings_with_pg_trgm(self):
        await self.check_near_spellings()
        self.assertTrue(memory._trgm)

    async def test_near_spellings_in_python_without_pg_trgm(self):
        async def no_trigrams(conn):
            memory._trgm = False
        with patch.object(memory, '_enable_trigrams', no_trigrams):
            memory._table_ready = False
            await self.check_near_spellings()
        self.assertFalse(memory._trgm)

    async def test_the_page_falls_back_to_text_and_another_model_is_searched_by_text_only(self):
        other = await self.add('Maviş jina notu', space='jina-v5', axis=2)
        mine = await self.add('kedim MAVİŞ uyuyor', score=0.4, axis=3)
        await self.add('alakasız', score=0.9, axis=4)
        page = await memory_groups.list_groups('alice', self.settings, q='maviş', limit=10)
        # A text match ranks its memory TEXT_BONUS higher: "kedim MAVİŞ" (0.4 + bonus) before the 0.9 one.
        self.assertEqual([(g['matched_part'], g['match']) for g in page['groups']],
                         [(mine, 'text'), (page['groups'][1]['matched_part'], 'meaning'), (other, 'text')])
        self.assertEqual(page['groups'][1]['score'], 0.9)
        jina = await memory_groups.list_groups('alice', self.settings, q='maviş', vector_space='jina-v5')
        self.assertEqual([(g['matched_part'], g['match']) for g in jina['groups']], [(other, 'text')])

        async def off(q, settings):
            return None, None, memory.SETUP_MESSAGE
        with patch.object(memory, '_query_vector', off):
            page = await memory_groups.list_groups('alice', self.settings, q='maviş')
            found = await self.tool(sorgu='maviş')
        self.assertEqual((page['semantic'], page['semantic_reason']), (False, memory.SETUP_MESSAGE))
        self.assertEqual({g['matched_part'] for g in page['groups']}, {other, mine})
        self.assertEqual({row['id'] for row in found['kayitlar']}, {other, mine})
        self.assertIn(memory.SETUP_MESSAGE, found['uyari_anlam'])

    async def stored_image(self, user='alice'):
        raw = png()
        media_id = __import__('hashlib').sha256(raw).hexdigest()
        conn = await registry._connect()
        try:
            async with conn.transaction():
                await media_store.hold_media(conn, media_id, 'image/png', raw)
        finally:
            await conn.close()
        row = await self.add('kalp.png — Kalbimin fotoğrafı', kind='image', media_id=media_id, user=user)
        await media_store.link_media(media_id, 'memory', str(row))
        return row, media_id, raw

    async def open_tool(self, ids, can=None):
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)), \
                patch.object(memory, 'chat_model_media', AsyncMock(return_value=can or {'image': True, 'audio': False})):
            return await memory_tools.hafizadaki_medyayi_ac(memory_tools.MemoryOpenInput(idler=ids),
                                                            ToolContext('alice', 'chat', 't', None))

    async def test_opening_media_links_the_file_to_the_chat_and_the_history_keeps_showing_it(self):
        row, media_id, raw = await self.stored_image()
        voice = await self.add('Ses kaydı 0:05', kind='audio', media_id='b' * 64)
        foreign, _, _ = await self.stored_image(user='bob')
        text = await self.add('düz metin')
        result = await self.open_tool([row, voice, foreign])
        self.assertEqual([entry['id'] for entry in result['acilanlar']], [row])
        self.assertEqual(result['_medya'][0]['media_id'], media_id)
        self.assertIn('dinleyemiyor', result['acilamayanlar'][0]['neden'])
        self.assertEqual(result['bulunamayanlar'], [foreign])
        self.assertIn('metin', (await self.open_tool([text]))['acilamayanlar'][0]['neden'])
        self.assertEqual(await media_store.read_media('chat', media_id), ('image/png', raw))

        call = {'id': 'c1', 'type': 'function',
                'function': {'name': 'hafizadaki_medyayi_ac', 'arguments': json.dumps({'idler': [row]})}}
        tool_message = {'role': 'tool', 'tool_call_id': 'c1', 'media_refs': result['_medya'],
                        'content': json.dumps({k: v for k, v in result.items() if k != '_medya'}, ensure_ascii=False)}
        history = [{'role': 'user', 'content': 'kalp fotoğrafında ne vardı?'},
                   {'role': 'assistant', 'content': None, 'tool_calls': [call]}, tool_message,
                   {'role': 'assistant', 'content': 'Kırmızı bir kare.'}]
        first = model_history(history, 40)
        self.assertEqual([m['role'] for m in first], ['user', 'assistant', 'tool', 'user', 'assistant'])
        self.assertEqual(first[3]['content'][-1], {'type': 'hub_media', 'attachment': {
            'media_id': media_id, 'mime_type': 'image/png', 'memory_id': row}})
        later = model_history(history + [{'role': 'user', 'content': 'peki rengi?'},
                                         {'role': 'assistant', 'content': 'Kırmızı.'}], 40)
        self.assertEqual(json.dumps(later[:5]), json.dumps(first))  # Same prefix: the prompt cache holds.
        hydrated = await media_store.hydrate_model_media('chat', later)
        self.assertEqual(hydrated[3]['content'][-1]['image_url']['url'],
                         'data:image/png;base64,' + base64.b64encode(raw).decode())

    async def test_a_chat_model_without_vision_gets_the_label(self):
        row, _, _ = await self.stored_image()
        result = await self.open_tool([row], can={'image': False, 'audio': False})
        self.assertNotIn('_medya', result)
        self.assertIn('göremiyor', result['acilamayanlar'][0]['neden'])

    async def test_a_gone_memory_file_does_not_fail_the_turn(self):
        messages = [{'role': 'user', 'content': [{'type': 'hub_media', 'attachment': {
            'media_id': 'f' * 64, 'mime_type': 'image/png', 'memory_id': 5}}]}]
        hydrated = await media_store.hydrate_model_media('chat', messages)
        self.assertEqual(hydrated[0]['content'], [{'type': 'text', 'text': '(Bu dosya artık yok.)'}])

    async def test_the_tool_loop_shows_the_media_right_after_the_tool_results(self):
        row, media_id, raw = await self.stored_image()
        conversation = ToolConversation(SimpleNamespace(user_id='alice', chat_id='chat', turn_id='t'),
                                        ['hafizadaki_medyayi_ac'])
        requests = []

        async def stream(messages, settings, **kwargs):
            requests.append(json.loads(json.dumps(messages)))
            if len(requests) == 1:
                yield 'tool_calls', json.dumps([{'id': 'c1', 'type': 'function', 'function': {
                    'name': 'hafizadaki_medyayi_ac', 'arguments': json.dumps({'idler': [row]})}}])
            else:
                yield 'content', 'Kırmızı bir kare.'
        with patch('orion.kernel.config.get_runtime_settings', AsyncMock(return_value=self.settings)), \
                patch.object(memory, 'chat_model_media', AsyncMock(return_value={'image': True, 'audio': False})):
            _ = [event async for event in conversation.stream([], RuntimeSettings(), AsyncMock(return_value=False), stream)]
        self.assertEqual([m['role'] for m in requests[1]], ['assistant', 'tool', 'user'])
        self.assertNotIn('"_medya"', requests[1][1]['content'])
        self.assertEqual(requests[1][2]['content'][-1]['image_url']['url'],
                         'data:image/png;base64,' + base64.b64encode(raw).decode())
        stored = conversation.transcript[1]
        self.assertEqual(stored['media_refs'][0]['media_id'], media_id)  # A reference, never base64.
        self.assertNotIn('base64', json.dumps(conversation.transcript))
        self.assertNotIn('"_medya"', json.dumps(conversation.activity))

    async def add_file(self, name, mime, raw, support=GEMMA, group=None):
        self.embedded = getattr(self, 'embedded', 0)

        async def target(settings):
            return settings, SPACE, True

        async def embed(text, settings, kind, local):
            self.embedded += 1
            return [vec(0.5, 2)]

        async def embed_media(parts, target):
            self.embedded += 1
            return vec(0.3, 3)
        with patch.object(memory, 'embedding_target', target), patch.object(memory, '_embed', embed), \
                patch.object(memory, '_embed_media', embed_media), \
                patch.object(memory, 'media_support', lambda local, name: support):
            return await memory.add_file('alice', name, mime, raw, self.settings, group=group)

    async def rows(self):
        conn = await registry._connect()
        try:
            return await conn.fetch('select text, kind, source_kind, source_chat_title, media_id from orion_memory '
                                    'order by id')
        finally:
            await conn.close()

    async def test_a_long_text_file_becomes_one_row_per_chunk(self):
        text = '\n\n'.join(f'Paragraf {k}: ' + ' '.join(['kelime'] * 120) for k in range(12))
        result = await self.add_file('notlar.md', 'text/markdown', text.encode('utf-8'))
        rows = await self.rows()
        self.assertGreater(len(rows), 1)
        self.assertEqual({**result, 'group_id': None}, {'added': len(rows), 'group_id': None, 'warnings': []})
        self.assertTrue(all((r['kind'], r['source_kind'], r['source_chat_title']) == ('text', 'file', 'notlar.md')
                            for r in rows))
        again = await self.add_file('notlar.md', 'text/markdown', text.encode('utf-8'), group=result['group_id'])
        self.assertEqual(again['added'], 0)
        # Into another memory: the same text relates them, with the vectors it has (nothing embedded again).
        before = self.embedded
        other = await self.add_file('notlar.md', 'text/markdown', text.encode('utf-8'))
        self.assertEqual((other['added'], self.embedded), (len(rows), before))
        self.assertNotEqual(other['group_id'], result['group_id'])

    async def test_an_image_file_is_stored_once_and_a_second_add_is_skipped(self):
        raw = png()
        first = await self.add_file('kalp.png', 'image/png', raw)
        self.assertEqual((first['added'], first['warnings'], len(first['group_id'])), (1, [], 32))
        same = await self.add_file('kopya.png', 'image/png', raw, group=first['group_id'])
        self.assertEqual((same['added'], same['warnings']), (0, ['Bu dosya bu hafızada zaten var.']))
        before = self.embedded
        other = await self.add_file('kopya.png', 'image/png', raw)
        self.assertEqual((other['added'], self.embedded, other.get('reused')), (1, before, True))
        rows = await self.rows()
        self.assertEqual([(r['text'], r['kind'], r['source_kind']) for r in rows],
                         [('kalp.png', 'image', 'file'), ('kalp.png', 'image', 'file')])
        conn = await registry._connect()
        try:
            self.assertEqual(await conn.fetchval('select count(*) from orion_media'), 1)
            self.assertEqual(await conn.fetchval("select count(*) from orion_media_links where owner_kind = 'memory'"), 2)
            self.assertEqual(await conn.fetchval('select count(distinct group_id) from orion_memory'), 2)
        finally:
            await conn.close()

    async def test_files_the_model_cannot_take_are_refused_in_turkish(self):
        with self.assertRaisesRegex(ValueError, 'sesi anlamıyor'):
            await self.add_file('ses.ogg', 'audio/ogg', b'OggS....', support=JINA)
        with self.assertRaisesRegex(ValueError, 'Yalnız görsel, ses, video ve metin'):
            await self.add_file('belge.pdf', 'application/pdf', b'%PDF')
        with self.assertRaises(routes.HTTPException) as error:
            await routes.add_memory_file(routes.MemoryFile(name='x.png', data='bozuk'), None, 'alice')
        self.assertEqual(error.exception.status_code, 400)


def test_the_open_media_tool_is_on_by_default_and_its_schema_pages():
    from orion.contracts.tools import default_tool_selection
    tools = get_registry()
    assert 'hafizadaki_medyayi_ac' in tools.enabled(default_tool_selection())
    schema = tools.tools['hafizada_ara'].schema()['function']['parameters']['properties']
    assert {'sorgu', 'tur', 'sayfa'} <= schema.keys() and schema['tur']['enum'] == ['hepsi', 'metin', 'görsel', 'ses', 'video']
    assert tools.tools['hafizadaki_medyayi_ac'].schema()['function']['parameters']['properties']['idler']['maxItems'] == 3


def test_python_word_similarity_finds_partial_words_and_typos():
    assert memory.word_similarity('selam nabe', 'kullanıcı: selam naber nasılsın') >= memory.FUZZY_MIN_PY
    assert memory.word_similarity('merhba', 'merhaba dünya') >= memory.FUZZY_MIN_PY
    assert memory.word_similarity('kedi', 'bugün hava çok güzel') < memory.FUZZY_MIN_PY
    assert memory.fold('İSTANBUL IŞIK Çiğdem') == 'istanbul isik cigdem'
