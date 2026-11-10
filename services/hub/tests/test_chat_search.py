import re
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from orion.api import routes
from orion.api.services import job_service
from orion.kernel import registry
from orion.kernel.chat_search import (
    excerpt, fold, like_pattern, loose_words, message_text, phrase_pattern, query_words)


class ChatSearchTextTests(unittest.TestCase):
    def test_fold_treats_turkish_i_as_one_letter(self):
        self.assertEqual(fold('İSTANBUL Isı ışık'), 'istanbul isi işik')
        self.assertEqual(len(fold('İİİ')), 3)

    def test_query_words(self):
        self.assertEqual(query_words('  Kürşat   ESP32 '), ['kürşat', 'esp32'])
        self.assertEqual(len(query_words(' '.join('abcdefghijkl'))), 8)

    def test_like_pattern_matches_wildcards_literally(self):
        self.assertEqual(like_pattern('50%_a\\b'), '%50\\%\\_a\\\\b%')

    def test_query_is_one_phrase_and_short_words_never_match_alone(self):
        words = query_words('ilde sabaha k')
        veysel = fold('Türk halkı şekilde\nsabaha  kadar bekledi')
        self.assertTrue(re.search(phrase_pattern(words), veysel))
        self.assertFalse(re.search(phrase_pattern(words), fold('ilde bir kuş, sabaha karşı k')))
        # Apart from each other only the longer words count: "k" is in every message.
        self.assertEqual(loose_words(words), ['ilde', 'sabaha'])
        self.assertEqual(loose_words(['ab', 'k']), [])
        # One word is the phrase itself.
        self.assertEqual(loose_words(['sabaha']), [])

    def test_phrase_pattern_takes_punctuation_literally(self):
        pattern = phrase_pattern(query_words('c++ (a.b) 50%'))
        self.assertTrue(re.search(pattern, 'x c++ (a.b)\n50% y'))
        self.assertFalse(re.search(pattern, 'x ccc (aXb) 50% y'))

    def test_excerpt_prefers_the_phrase_to_an_earlier_single_word(self):
        text = 'sabaha bir not. ' + 'x' * 200 + ' şekilde sabaha kadar ' + 'y' * 200
        piece = excerpt(text, ['ilde', 'sabaha', 'k'])
        self.assertIn('şekilde sabaha kadar', piece)
        # Without the phrase, a one-letter word does not place the excerpt.
        self.assertTrue(excerpt('k ' + 'x' * 200 + ' sabaha ilde', ['ilde', 'sabaha', 'k']).startswith('…'))

    def test_question_is_typed_text_attachment_names_and_text_files(self):
        message = {
            'role': 'user', 'content': 'built prompt', 'display_text': 'Ekleri incele',
            'attachments': [
                {'name': 'test.txt', 'data': 'benim adım kürşat', 'isText': True},
                {'name': 'photo.jpg', 'data': 'data:image/jpeg;base64,AAAA'},
                {'name': 'odd.txt', 'data': 'data:text/plain;base64,AAAA', 'isText': True},
            ],
        }
        self.assertEqual(message_text(message), 'Ekleri incele\ntest.txt\nbenim adım kürşat\nphoto.jpg\nodd.txt')

    def test_answer_text_and_multimodal_parts(self):
        self.assertEqual(message_text({'role': 'assistant', 'content': ' Merhaba\x00 ', 'thinking': 'gizli'}), 'Merhaba')
        parts = [{'type': 'text', 'text': 'bir'}, {'type': 'image_url', 'image_url': {'url': 'x'}},
                 {'type': 'text', 'text': 'iki'}]
        self.assertEqual(message_text({'role': 'user', 'content': parts}), 'bir\niki')

    def test_other_roles_and_empty_messages_are_not_searched(self):
        self.assertIsNone(message_text({'role': 'tool', 'content': 'sonuç'}))
        self.assertIsNone(message_text({'role': 'assistant', 'content': None}))

    def test_excerpt_is_one_line_around_the_first_word_found(self):
        text = 'a' * 30 + ' başta uzun bir giriş var\nve sonra ESP32 derin uyku modundan söz ediliyor ' + 'b' * 200
        piece = excerpt(text, ['esp32', 'yok'])
        self.assertTrue(piece.startswith('…') and piece.endswith('…'))
        self.assertIn('ESP32 derin uyku', piece)
        self.assertNotIn('\n', piece)
        self.assertLess(len(piece), 160)
        self.assertEqual(excerpt('Kısa İleti', ['ileti']), 'Kısa İleti')
        # A word that is not in the text (matched in another part) still gives the start.
        self.assertEqual(excerpt('Kısa ileti', ['yok']), 'Kısa ileti')


class ChatSearchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_results_carry_an_excerpt(self):
        rows = [{'chat_id': 'c1', 'title': 'Ekler', 'role': 'user', 'plain_text': 'benim adım Kürşat',
                 'created_at': datetime(2026, 11, 10, 9, 30, tzinfo=timezone.utc)}]
        with patch.object(registry, 'search_chat_messages_db', AsyncMock(return_value=rows)) as search:
            found = await job_service.search_chats('u', ' KÜRŞAT ')
        search.assert_awaited_once_with('u', ['kürşat'])
        self.assertEqual(found, [{'chat_id': 'c1', 'name': 'Ekler', 'role': 'user', 'snippet': 'benim adım Kürşat',
                                  'created_at': '2026-11-10T09:30:00+00:00'}])

    async def test_short_or_empty_queries_do_not_reach_the_database(self):
        with patch.object(registry, 'search_chat_messages_db', AsyncMock()) as search:
            self.assertEqual(await job_service.search_chats('u', ''), [])
            self.assertEqual(await job_service.search_chats('u', ' a b '), [])
        search.assert_not_awaited()

    async def test_database_failure_is_a_503(self):
        with patch.object(registry, 'search_chat_messages_db', AsyncMock(side_effect=RuntimeError('down'))):
            with self.assertRaises(HTTPException) as raised:
                await job_service.search_chats('u', 'kürşat')
        self.assertEqual(raised.exception.status_code, 503)

    async def test_database_gets_the_phrase_and_only_the_longer_words(self):
        conn = AsyncMock()
        conn.fetch.return_value = []
        with patch.object(registry, '_connect', AsyncMock(return_value=conn)), \
                patch.object(registry, '_ensure_tables', AsyncMock()):
            await registry.search_chat_messages_db('u', ['ilde', 'sabaha', 'k'])
        args = conn.fetch.await_args.args
        self.assertEqual(args[1:], ('u', ['user', 'assistant'], r'ilde\s+sabaha\s+k', ['%ilde%', '%sabaha%'], 30, 3))

    async def test_saved_messages_get_their_search_columns(self):
        conn = AsyncMock()
        with patch.object(registry, '_connect', AsyncMock(return_value=conn)), \
                patch.object(registry, '_ensure_tables', AsyncMock()):
            await registry.insert_messages('c1', [{'role': 'user', 'content': 'p', 'display_text': 'İyi Günler'},
                                                  {'role': 'tool', 'content': 'x'}])
        records = conn.executemany.await_args.args[1]
        self.assertEqual([record[3:] for record in records], [('İyi Günler', 'iyi günler'), ('', '')])

    async def test_older_messages_are_indexed_in_batches(self):
        conn = AsyncMock()
        conn.fetch.return_value = [{'id': 1, 'content_json': '{"role": "assistant", "content": "ISI"}'},
                                   {'id': 2, 'content_json': 'not json'}]
        with patch.object(registry, '_connect', AsyncMock(return_value=conn)), \
                patch.object(registry, '_ensure_tables', AsyncMock()):
            self.assertEqual(await registry.index_unsearchable_messages(), 2)
        self.assertEqual(conn.executemany.await_args.args[1], [(1, 'ISI', 'isi'), (2, '', '')])

    def test_search_route_is_not_taken_for_a_chat_id(self):
        paths = [route.path for route in routes.router.routes if 'GET' in getattr(route, 'methods', ())]
        self.assertLess(paths.index('/api/v1/chats/search'), paths.index('/api/v1/chats/{chat_id}'))


if __name__ == '__main__':
    unittest.main()
