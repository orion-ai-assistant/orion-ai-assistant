"""The Hub keeps the start of every prompt stable and names the user, so providers can cache the history."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from orion.contracts.settings import RuntimeSettings
from orion.worker.services import router
from orion.worker.services.tool_history import HISTORY_SLIDE_TURNS, model_history
from orion.worker.services.tool_runner import ToolConversation


def chat(turns):
    history = []
    for index in range(turns):
        history += [{'role': 'user', 'content': f'q{index}'}, {'role': 'assistant', 'content': f'a{index}'}]
    return history


class HistoryWindowTests(unittest.TestCase):
    def test_default_still_slides_every_turn(self):
        self.assertEqual(model_history(chat(15), 20)[0]['content'], 'q5')
        self.assertEqual(model_history(chat(16), 20)[0]['content'], 'q6')

    def test_block_window_moves_only_every_few_turns_and_never_exceeds_the_limit(self):
        starts = []
        for turns in range(1, 60):
            window = model_history(chat(turns), 20, HISTORY_SLIDE_TURNS)
            self.assertLessEqual(len(window), 20)
            self.assertEqual(window[-1]['content'], f'a{turns - 1}')  # the newest turn is always there
            if turns > 10:
                self.assertGreaterEqual(len(window), 14)  # at most a third of the window is held back
            starts.append(window[0]['content'])
        moves = sum(1 for before, after in zip(starts, starts[1:]) if before != after)
        # Sliding every turn would move about 49 times; blocks of 3 turns move about 16 times.
        self.assertLessEqual(moves, 20)

    def test_consecutive_requests_share_their_whole_prefix_between_moves(self):
        shared = 0
        for turns in range(12, 40):
            before = model_history(chat(turns), 20, HISTORY_SLIDE_TURNS)
            after = model_history(chat(turns + 1), 20, HISTORY_SLIDE_TURNS)
            if after[:len(before)] == before:
                shared += 1
        self.assertGreaterEqual(shared, 18)  # of 28 consecutive pairs

    def test_small_or_short_chats_are_not_trimmed_further(self):
        self.assertEqual(model_history(chat(3), 20, HISTORY_SLIDE_TURNS), chat(3))
        self.assertEqual(model_history([], 20, HISTORY_SLIDE_TURNS), [])
        # Two big turns: the block never swallows a window this small.
        self.assertEqual(len(model_history(chat(2), 2, HISTORY_SLIDE_TURNS)), 2)

    def test_tool_turns_stay_paired(self):
        call = {'id': 'c1', 'type': 'function', 'function': {'name': 'datetime', 'arguments': '{}'}}
        turn = [{'role': 'user', 'content': 'time?'},
                {'role': 'assistant', 'content': None, 'tool_calls': [call]},
                {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"now":"x"}'},
                {'role': 'assistant', 'content': 'noon'}]
        for after in range(0, 12):
            window = model_history(chat(8) + turn + chat(after), 20, HISTORY_SLIDE_TURNS)
            self.assertEqual(window[0]['role'], 'user')
            for index, message in enumerate(window):
                if message['role'] == 'tool':
                    self.assertEqual(window[index - 1]['tool_calls'][0]['id'], message['tool_call_id'])


class ChatNamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_model_round_names_the_user_not_the_chat(self):
        seen = []

        async def model(*args, **kwargs):
            seen.append(kwargs)
            yield 'content', 'ok'

        conversation = ToolConversation(SimpleNamespace(user_id='user-7', chat_id='chat-7', turn_id='t'), [])
        events = [event async for event in conversation.stream([], RuntimeSettings(), AsyncMock(return_value=False), model)]
        self.assertEqual(events, [('content', 'ok')])
        self.assertEqual(seen[0]['cache_key'], 'user-7')

    async def test_router_request_carries_prompt_cache_key_only_when_named(self):
        async def chunks():
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b'data: [DONE]\n\n'

        for cache_key in ('chat-7', None):
            with self.subTest(cache_key=cache_key):
                response = AsyncMock()
                response.status = 200
                response.raise_for_status = Mock()
                response.close = Mock()
                response.content = Mock()
                response.content.iter_any.return_value = chunks()
                response.__aenter__.return_value = response
                session = Mock()
                session.post.return_value = response
                with patch.object(router, 'get_session', AsyncMock(return_value=session)), \
                        patch.object(router, '_router_urls', return_value=['http://test/chat']), \
                        patch.object(router, '_chat_headers', AsyncMock(return_value={})):
                    events = [event async for event in router.llama_stream_chat_typed([], RuntimeSettings(), cache_key=cache_key)]
                self.assertIn(('content', 'ok'), events)
                payload = session.post.call_args.kwargs['json']
                if cache_key:
                    self.assertEqual(payload['prompt_cache_key'], 'chat-7')
                else:
                    self.assertNotIn('prompt_cache_key', payload)


if __name__ == '__main__':
    unittest.main()
