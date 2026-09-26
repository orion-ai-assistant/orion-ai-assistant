from html.parser import HTMLParser
from pathlib import Path
import unittest

from orion.contracts.http import JobInput


class ViewParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.divs = []
        self.parents = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'div':
            identifier = dict(attrs).get('id')
            if identifier:
                self.parents[identifier] = list(self.divs)
            self.divs.append(identifier)

    def handle_endtag(self, tag):
        if tag == 'div':
            self.divs.pop()


class ComposerRegressionTests(unittest.TestCase):
    def test_settings_and_status_are_outside_chat_view(self):
        parser = ViewParser()
        parser.feed((Path(__file__).parents[1] / 'src/orion/api/ui/index.html').read_text(encoding='utf-8'))
        self.assertFalse(parser.divs)
        self.assertEqual(parser.parents['settings-view'], parser.parents['chat-view'])
        self.assertEqual(parser.parents['status-view'], parser.parents['chat-view'])

    def test_hub_accepts_messages_above_old_character_limit(self):
        text = 'uzun mesaj ' * 2000
        self.assertEqual(JobInput(text=text).text, text)


if __name__ == '__main__':
    unittest.main()
