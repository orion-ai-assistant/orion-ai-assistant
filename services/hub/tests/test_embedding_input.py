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
