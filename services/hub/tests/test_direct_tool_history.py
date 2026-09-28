import copy
import json
import unittest

from orion.worker.services.tool_history import model_history
from orion.worker.services.tool_runner import google_direct_text


class DirectToolHistoryTests(unittest.TestCase):
    def history(self, marked=True):
        result = {'status': 'ok', 'answer': 'Unique Google answer', 'sources': [
            {'title': 'Source', 'url': 'https://example.org/source'}]}
        final = {'role': 'assistant', 'content': google_direct_text(result['answer'], result)}
        if marked:
            final['direct_tool_call_id'] = 'g'
        return [
            {'role': 'user', 'content': 'Research'},
            {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'g', 'type': 'function', 'function': {'name': 'google_ai_mode', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'g', 'content': json.dumps(result)}, final,
            {'role': 'user', 'content': 'Summarize it'},
        ]

    def test_direct_answer_once_in_tool_new_and_existing_history(self):
        for marked in (True, False):
            history = self.history(marked)
            original = copy.deepcopy(history)
            projected = model_history(history, 20)
            self.assertEqual([m['role'] for m in projected], ['user', 'assistant', 'tool', 'user'])
            self.assertEqual(projected[1]['tool_calls'][0]['id'], projected[2]['tool_call_id'])
            self.assertEqual(json.dumps(projected).count('Unique Google answer'), 1)
            self.assertEqual(history, original, 'Display history is never mutated')

    def test_normal_model_answer_preserved_even_with_same_heading(self):
        history = self.history(False)
        history[3]['content'] = '**Google AI Mode**\n\nMy own summary'
        self.assertEqual(len(model_history(history, 20)), 5)

    def test_markdown_answer_is_sent_once_without_losing_format(self):
        history = self.history(False)
        result = {'status': 'ok', 'answer': '## Başlık\n\n**Kalın**', 'answer_format': 'markdown'}
        history[2]['content'] = json.dumps(result)
        history[3]['content'] = google_direct_text(result['answer'], result)
        projected = model_history(history, 20)
        self.assertEqual(len(projected), 4)
        self.assertEqual(json.loads(projected[2]['content'])['answer'], result['answer'])

    def test_missing_result_drops_whole_incomplete_turn(self):
        history = self.history()
        del history[2]
        self.assertEqual(model_history(history, 20), [{'role': 'user', 'content': 'Summarize it'}])

    def test_failed_result_cannot_hide_assistant_error(self):
        history = self.history()
        history[2]['content'] = '{"status":"timeout"}'
        history[3]['content'] = 'The request failed'
        self.assertEqual(model_history(history, 20)[3]['content'], 'The request failed')

    def test_cancelled_partial_google_answer_is_only_in_tool_result(self):
        history = self.history()
        result = {'status': 'partial', 'provider': 'google_ai_mode', 'cancelled': True,
                  'answer': 'Visible Google draft', 'answer_format': 'markdown'}
        history[2]['content'] = json.dumps(result)
        history[3]['content'] = google_direct_text(result['answer'], result)
        projected = model_history(history, 20)
        self.assertEqual([message['role'] for message in projected], ['user', 'assistant', 'tool', 'user'])
        self.assertEqual(json.dumps(projected).count('Visible Google draft'), 1)
        self.assertEqual(projected[2]['content'], 'Visible Google draft')
        self.assertEqual(json.loads(history[2]['content']), result)

    def test_budget_counts_projected_messages_and_retains_pairs(self):
        history = self.history()
        self.assertEqual(len(model_history(history, 4)), 4)
        self.assertEqual(model_history(history, 3), [{'role': 'user', 'content': 'Summarize it'}])
