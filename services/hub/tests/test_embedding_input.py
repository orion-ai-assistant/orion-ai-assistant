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


from orion.worker.services.router import chunk_text, estimate_tokens, split_for_embedding


def test_short_text_is_one_piece():
    assert split_for_embedding('kısa bir not', 'title: none | text: ', 2048) == ['kısa bir not']


def test_long_text_splits_so_that_prefix_and_piece_fit_the_context():
    prefix = 'title: none | text: '
    text = ' '.join(f'Cümle numara {i}, biraz uzun bir cümle.' for i in range(600))
    pieces = split_for_embedding(text, prefix, 2048)
    assert len(pieces) > 1
    assert all(estimate_tokens(prefix + piece) <= 2048 - 32 for piece in pieces)
    assert ' '.join(pieces).split() == text.split()  # nothing lost, cuts at word boundaries


def test_chinese_japanese_and_korean_count_one_token_per_character():
    """Measured: Jina takes 1.3 characters per token on Chinese; the Latin estimate would overflow the context."""
    for sample in ('今天天气非常好，我和朋友们去了公园。', '今日はとても天気が良かったので公園に行きました。', '오늘은 날씨가 아주 좋아서 공원에 갔습니다.'):
        text = sample * 400
        pieces = split_for_embedding(text, '', 2048)
        assert len(pieces) > 1 and all(estimate_tokens(piece) <= 2048 - 32 for piece in pieces)
        assert ''.join(pieces).replace(' ', '') == text.replace(' ', '')


def test_a_word_longer_than_a_piece_is_cut_hard():
    pieces = split_for_embedding('a' * 10000, '', 512)
    assert all(estimate_tokens(piece) <= 512 for piece in pieces) and ''.join(pieces) == 'a' * 10000


def test_chunks_pack_whole_paragraphs_up_to_the_target():
    paragraphs = [f'Paragraf {i}. ' + 'Bu bir cümle. ' * 20 for i in range(30)]
    chunks = chunk_text('\n\n'.join(paragraphs), 400)
    assert len(chunks) > 1 and all(estimate_tokens(chunk) <= 400 for chunk in chunks)
    assert all(chunk.count('Paragraf') >= 1 for chunk in chunks)
    assert sum(chunk.count('Paragraf') for chunk in chunks) == 30  # paragraphs stay whole
    assert chunk_text('') == [] and chunk_text('kısa') == ['kısa']


import asyncio
from types import SimpleNamespace

from orion.worker.services import router as router_module


def _embed_with(monkeypatch, provider, **kwargs):
    sent = []

    async def fake_provider(*_):
        return provider

    async def fake_embed(text, settings):
        sent.append(text)
        return [0.0]

    monkeypatch.setattr(router_module, 'get_model_provider', fake_provider)
    monkeypatch.setattr(router_module, 'generate_embeddings', fake_embed)
    from services.shared import local_parts
    monkeypatch.setattr(local_parts, 'embedding_profile', lambda: {
        'context': 2048, 'query_prefix': 'task: search result | query: ', 'document_prefix': 'title: none | text: '})
    settings = SimpleNamespace(router_model_group='embed')
    vectors = asyncio.run(router_module.embed_text(settings=settings, **kwargs))
    return sent, vectors


def test_a_local_document_gets_its_prefix_and_a_title(monkeypatch):
    sent, _ = _embed_with(monkeypatch, 'local', text='Kedim uyuyor.', kind='document', title='Kedi | notları')
    assert sent == ['title: Kedi / notları | text: Kedim uyuyor.']


def test_a_local_query_gets_the_query_prefix_and_ignores_a_title(monkeypatch):
    sent, _ = _embed_with(monkeypatch, 'local', text='kedi', kind='query', title='x')
    assert sent == ['task: search result | query: kedi']


def test_a_cloud_model_gets_the_text_untouched(monkeypatch):
    sent, _ = _embed_with(monkeypatch, 'openai', text='kedi', kind='document')
    assert sent == ['kedi']


def test_long_local_text_becomes_several_vectors(monkeypatch):
    sent, vectors = _embed_with(monkeypatch, 'local', text='Bir cümle daha. ' * 2000, kind='document')
    assert len(sent) == len(vectors) > 1 and all(item.startswith('title: none | text: ') for item in sent)


def test_webp_goes_as_png_and_native_formats_stay():
    import base64, io
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGBA', (4, 4), (255, 0, 0, 128)).save(out, format='WEBP')
    url = image_part(out.getvalue(), 'image/webp')['image_url']['url']
    assert url.startswith('data:image/png;base64,')
    with Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))) as png:
        assert png.format == 'PNG' and png.mode == 'RGBA' and png.size == (4, 4)
    assert image_part(b'\xff\xd8jpeg', 'image/jpeg')['image_url']['url'].startswith('data:image/jpeg;base64,')
