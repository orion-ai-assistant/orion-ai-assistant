import json
import unittest

from orion.api.services.job_service import _append_processing_state


class AttachmentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.message = {
            'role': 'user', 'content': 'prompt', 'display_text': 'selam', 'turn_id': 'new',
            'attachments': [
                {'name': 'test.txt', 'data': 'kürşat', 'isText': True},
                {'name': 'photo.jpg', 'data': 'data:image/jpeg;base64,a'},
            ],
        }
        self.state = {'current_prompt': 'prompt', 'active_turn_id': 'new',
                      'current_user_message': json.dumps(self.message)}

    def test_navigation_restores_attachments_before_completion(self):
        history = []
        _append_processing_state(history, self.state)
        self.assertEqual(history[0], self.message)
        self.assertTrue(history[1]['partial'])

    def test_persisted_multimodal_turn_is_not_duplicated(self):
        history = [{**self.message, 'content': [{'type': 'text', 'text': 'prompt'}]}]
        _append_processing_state(history, self.state)
        self.assertEqual(len(history), 2)

    def test_repeated_prompt_still_shows_new_attachments(self):
        history = [{'role': 'user', 'content': 'prompt', 'turn_id': 'old'},
                   {'role': 'assistant', 'content': 'answer'}]
        _append_processing_state(history, self.state)
        self.assertEqual(history[2], self.message)


if __name__ == '__main__':
    unittest.main()
