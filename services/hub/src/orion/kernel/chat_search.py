"""Text of saved chat messages, as the chat search sees it.

Every message row keeps two extra columns next to its JSON: the text the user
can read (`plain_text`, for the excerpt) and the same text folded for matching
(`search_text`). The search then never opens the JSON, which can hold megabytes
of audio.
"""

import re

SEARCHED_ROLES = ('user', 'assistant')
MAX_WORDS = 8
# A shorter word only counts as part of the whole phrase: alone, "k" is in every message.
LOOSE_WORD_LENGTH = 3
_FOLD = str.maketrans({'İ': 'i', 'I': 'i', 'ı': 'i'})


def fold(text: str) -> str:
    """Lower case; the four Turkish i's count as one letter (the app folds names the same way)."""
    return text.translate(_FOLD).lower()


def query_words(query: str) -> list[str]:
    return fold(query).split()[:MAX_WORDS]


def phrase_pattern(words: list[str]) -> str:
    """The words in a row, as typed, with any white space between them.

    A regular expression both PostgreSQL and Python read the same way.
    """
    return r'\s+'.join(re.sub(r'([^\w\s])', r'\\\1', word) for word in words)


def loose_words(words: list[str]) -> list[str]:
    """The words a message may also hold apart from each other, when the phrase is not in it."""
    return [word for word in words if len(word) >= LOOSE_WORD_LENGTH] if len(words) > 1 else []


def like_pattern(word: str) -> str:
    """A LIKE pattern that finds the word literally (backslash is PostgreSQL's default escape)."""
    return '%' + re.sub(r'([\\%_])', r'\\\1', word) + '%'


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part['text'] for part in content
                         if isinstance(part, dict) and isinstance(part.get('text'), str))
    return ''


def message_text(message: dict) -> str | None:
    """What the user reads in this message, or None when it is not searched.

    A question is its typed text (not the prompt built from it), the names of
    its attachments and the content of the text ones.
    """
    if message.get('role') not in SEARCHED_ROLES:
        return None
    shown = message.get('display_text')
    parts = [shown if isinstance(shown, str) else _content_text(message.get('content'))]
    attachments = message.get('attachments')
    for item in attachments if isinstance(attachments, list) else []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('name'), str):
            parts.append(item['name'])
        data = item.get('data')
        if item.get('isText') and isinstance(data, str) and not data.startswith('data:'):
            parts.append(data)
    # PostgreSQL text cannot hold NUL.
    text = '\n'.join(part for part in parts if part).replace('\x00', '').strip()
    return text or None


def excerpt(text: str, words: list[str], before: int = 40, after: int = 110) -> str:
    """One line of the message around the phrase, or around the first loose word found."""
    folded = fold(text)
    at = -1
    if len(folded) == len(text):  # Folding keeps positions except for rare letters.
        phrase = re.search(phrase_pattern(words), folded)
        found = [phrase.start()] if phrase else [folded.find(word) for word in loose_words(words)]
        at = min((position for position in found if position >= 0), default=-1)
    start = max(0, at - before) if at > 0 else 0
    end = min(len(text), start + before + after)
    if start:
        # Start on a word, not inside one.
        space = text.find(' ', start, at)
        start = space + 1 if space >= 0 else start
    piece = ' '.join(text[start:end].split())
    return ('…' if start else '') + piece + ('…' if end < len(text) else '')
