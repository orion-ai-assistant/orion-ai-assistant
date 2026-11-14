from orion.worker.services.router import audio_part, embedding_input, image_part


def test_text_input_stays_a_string():
    assert embedding_input('merhaba') == 'merhaba'


def test_content_parts_nest_one_level_deeper():
    parts = [{'type': 'text', 'text': 'kırmızı kare'}, image_part(b'\x89PNG')]
    body = embedding_input(parts)
    assert body == [{'content': parts}]
    assert body[0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')


def test_audio_part_shape():
    part = audio_part(b'RIFF', 'wav')
    assert part['type'] == 'input_audio'
    assert part['input_audio']['format'] == 'wav'


from orion.worker.services.router import EMBED_CHARS_PER_TOKEN, EMBED_TOKEN_MARGIN, split_for_embedding


def test_short_text_is_one_piece():
    assert split_for_embedding('kısa bir not', 'title: none | text: ', 2048) == ['kısa bir not']


def test_long_text_splits_so_that_prefix_and_piece_fit_the_context():
    prefix = 'title: none | text: '
    text = ' '.join(f'Cümle numara {i}, biraz uzun bir cümle.' for i in range(600))
    pieces = split_for_embedding(text, prefix, 2048)
    assert len(pieces) > 1
    limit = (2048 - EMBED_TOKEN_MARGIN) * EMBED_CHARS_PER_TOKEN
    assert all(len(prefix) + len(piece) <= limit for piece in pieces)
    assert ' '.join(pieces).split() == text.split()  # nothing lost, cuts at word boundaries


def test_a_word_longer_than_a_piece_is_cut_hard():
    pieces = split_for_embedding('a' * 10000, '', 512)
    assert all(len(piece) <= 960 for piece in pieces) and ''.join(pieces) == 'a' * 10000
