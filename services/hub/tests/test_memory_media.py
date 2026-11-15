"""Images and voice notes of the "Asistanla konuş" chat in memory: which are taken, the warnings when the model
can't take a kind, the links that keep the file, the media endpoint and what search and the tool return."""
import asyncio
import io
import math
import struct
import wave
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from orion.api import routes
from orion.kernel import media_store, memory, registry
from orion.worker.tools._base import ToolContext
from orion.worker.tools.memory import tools as memory_tools

IMG = 'a' * 64
VOICE = 'b' * 64
TTS = 'c' * 64
GEMMA = {'model': 'EmbeddingGemma 2', 'has_extra': True, 'extra_on': True, 'extra_kinds': ['audio', 'image'],
         'image': True, 'audio': True}
JINA = {'model': 'Jina Embeddings v5 Omni Nano', 'has_extra': True, 'extra_on': True, 'extra_kinds': ['image'],
        'image': True, 'audio': False}
GEMMA_OFF = {**GEMMA, 'extra_on': False, 'image': False, 'audio': False}


def tone(seconds: float, rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                                    for i in range(int(seconds * rate))))
    return out.getvalue()


HISTORY = [
    {'role': 'user', 'display_text': 'Kedimin fotoğrafı', 'content': 'Kedimin fotoğrafı',
     'attachments': [{'name': 'kedi.jpg', 'mime_type': 'image/jpeg', 'media_id': IMG}]},
    {'role': 'user', 'content': 'dinle',
     'attachments': [{'name': 'Ses kaydı', 'mime_type': 'audio/wav', 'media_id': VOICE},
                     {'name': 'kedi.jpg', 'mime_type': 'image/jpeg', 'media_id': IMG}]},
    {'role': 'assistant', 'content': 'Çok tatlı!', 'audio': {'media_id': TTS, 'mime_type': 'audio/ogg'},
     'attachments': [{'name': 'cevap.ogg', 'mime_type': 'audio/ogg', 'media_id': TTS}]},
]


class Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Conn:
    def __init__(self, existing_media=()):
        self.rows, self.links, self.texts, self.closed = [], [], [], False
        self.existing_media = set(existing_media)

    async def fetchval(self, sql, *args):
        if 'pg_extension' in sql:
            return 1
        if 'media_id = $3' in sql:
            return 1 if args[2] in self.existing_media else None
        if sql.startswith('insert into orion_memory'):
            self.rows.append(args)
            return 100 + len(self.rows)
        return None

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        pass

    async def executemany(self, sql, rows):
        self.texts.extend(rows)

    async def fetch(self, sql, *args):
        return []

    def transaction(self):
        return Tx()

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(memory, '_table_ready', False)


def wire(monkeypatch, conn, support, audio=None, history=HISTORY):
    embedded = []

    async def connect():
        return conn

    async def target(settings):
        return SimpleNamespace(router_model_group='local-embed'), 'embeddinggemma-2', True

    async def embed(text, settings, kind, local):
        return [[0.5] * 768]

    async def embed_media(parts, target):
        embedded.append(parts[0]['type'])
        return [0.25] * 768

    async def read(chat_id, media_id):
        assert media_id != TTS, "the assistant's spoken reply is never read"
        return ('image/jpeg', b'jpeg') if media_id == IMG else ('audio/wav', audio or tone(2))

    async def link(media_id, owner_kind, owner_id, *, conn=None):
        conn.links.append((media_id, owner_kind, owner_id))

    async def messages(chat_id):
        return history

    monkeypatch.setattr(memory, '_connect', connect)
    monkeypatch.setattr(memory, 'embedding_target', target)
    monkeypatch.setattr(memory, '_embed', embed)
    monkeypatch.setattr(memory, '_embed_media', embed_media)
    monkeypatch.setattr(memory, '_read_chat_media', read)
    monkeypatch.setattr(memory, 'media_support', lambda local, name: {**support, 'video': support['image']})
    monkeypatch.setattr(memory, 'embedding_context', lambda: 2048)
    monkeypatch.setattr(media_store, 'link_media', link)
    async def chat_messages(chat_id):
        found = await messages(chat_id)
        return found, list(range(1, len(found) + 1))
    monkeypatch.setattr(memory, '_chat_messages', chat_messages)
    return embedded


def test_chat_media_takes_the_users_images_and_voice_once_and_never_the_assistants_reply():
    found = memory.chat_media(HISTORY)
    assert [(item['kind'], item['media_id']) for item in found] == [('image', IMG), ('audio', VOICE)]
    assert found[0]['text'] == 'Kedimin fotoğrafı' and found[0]['name'] == 'kedi.jpg'


def test_chat_media_also_reads_media_parts_of_the_content():
    history = [{'role': 'user', 'content': [{'type': 'text', 'text': 'x'}, {'type': 'hub_media', 'attachment': {
        'media_id': VOICE, 'mime_type': 'audio/ogg'}}]}]
    assert [item['kind'] for item in memory.chat_media(history)] == ['audio']


def test_gemma_saves_text_image_and_voice_and_links_each_row_to_the_memory(monkeypatch):
    conn = Conn()
    embedded = wire(monkeypatch, conn, GEMMA)
    result = asyncio.run(memory.save_chat('u1', 'c1', 'Kedi', object()))
    assert {k: result[k] for k in ('saved', 'skipped', 'warnings', 'jobs')} == {
        'saved': 3, 'skipped': {}, 'warnings': [], 'jobs': []}
    assert embedded == ['image_url', 'input_audio']
    text, image, voice = conn.rows
    assert image[6:9] == ('image', IMG, 'image/jpeg') and image[1] == 'kedi.jpg — Kedimin fotoğrafı'
    assert voice[6:8] == ('audio', VOICE) and voice[1] == 'Ses kaydı 0:02 — dinle' and voice[9] == pytest.approx(2, abs=0.05)
    assert image[18] == 'Kedimin fotoğrafı' and voice[18] == 'dinle'  # What the user wrote with the file.
    assert conn.links == [(IMG, 'memory', '102'), (VOICE, 'memory', '103')]
    assert image[10] == voice[10] == text[10] == result['group_id'] and image[11] == 'c1'  # One save, one memory.
    assert len({text[13], image[13], voice[13]}) == 3  # Three items, each its own.


def test_a_long_voice_note_becomes_overlapping_pieces_and_a_whole_vector_with_the_same_media(monkeypatch):
    conn = Conn()
    wire(monkeypatch, conn, GEMMA, audio=tone(130), history=[HISTORY[1]])
    # 1 text + 1 image + 4 audio pieces (40 s cores in 60 s windows) + the audio's whole vector.
    assert asyncio.run(memory.save_chat('u1', 'c1', '', object()))['saved'] == 7
    audio = [row for row in conn.rows if row[6] == 'audio']
    assert [row[1] for row in audio] == ['Ses kaydı 2:10 0:00–0:40 — dinle', 'Ses kaydı 2:10 0:40–1:20 — dinle',
                                         'Ses kaydı 2:10 1:20–2:00 — dinle', 'Ses kaydı 2:10 2:00–2:10 — dinle',
                                         'Ses kaydı 2:10 — dinle']
    assert [(row[15], row[16]) for row in audio[:4]] == [(0, 40), (40, 80), (80, 120), (120, pytest.approx(130, abs=0.1))]
    assert [row[14] for row in audio] == [False] * 4 + [True] and len({row[13] for row in audio}) == 1
    assert len({link[2] for link in conn.links if link[0] == VOICE}) == 5


def test_jina_skips_voice_notes_with_a_clear_warning(monkeypatch):
    conn = Conn()
    wire(monkeypatch, conn, JINA)
    result = asyncio.run(memory.save_chat('u1', 'c1', '', object()))
    assert result['saved'] == 2 and result['skipped'] == {'audio': 1}
    assert result['warnings'] == ["Sohbette 1 ses kaydı var ama Jina Embeddings v5 Omni Nano sesi anlamıyor; "
                                  "sesler kaydedilmedi. Ses için EmbeddingGemma 2'yi ek dosyasıyla seç."]
    assert [row[6] for row in conn.rows] == ['text', 'image']


def test_an_extra_file_that_is_off_says_where_to_turn_it_on(monkeypatch):
    conn = Conn()
    wire(monkeypatch, conn, GEMMA_OFF)
    result = asyncio.run(memory.save_chat('u1', 'c1', '', object()))
    assert result['skipped'] == {'image': 1, 'audio': 1} and [r[6] for r in conn.rows] == ['text']
    assert result['saved'] == 1
    assert all('ek dosyası kapalı' in w and 'Bileşenler → Hafıza' in w for w in result['warnings'])


def test_a_cloud_model_takes_no_media():
    support = memory.media_support(False, 'cloud-embed')
    assert not support['image'] and not support['audio']
    assert 'cloud-embed görselleri anlamıyor' in memory.skip_warning('image', 2, support)


def test_media_already_saved_in_this_space_is_not_saved_again(monkeypatch):
    conn = Conn(existing_media={IMG, VOICE})
    embedded = wire(monkeypatch, conn, GEMMA)
    assert asyncio.run(memory.save_chat('u1', 'c1', '', object()))['saved'] == 1
    assert not embedded and [r[6] for r in conn.rows] == ['text']


def test_a_media_the_model_fails_on_is_a_warning_not_a_lost_save(monkeypatch):
    conn = Conn()
    wire(monkeypatch, conn, GEMMA)

    async def broken(parts, target):
        raise RuntimeError('500')
    monkeypatch.setattr(memory, '_embed_media', broken)
    result = asyncio.run(memory.save_chat('u1', 'c1', '', object()))
    assert result['saved'] == 1 and 'kaydedilemedi' in ' '.join(result['warnings'])


def test_a_chat_with_only_a_photo_is_saved():
    history = [{'role': 'user', 'display_text': '', 'attachments': [
        {'name': 'kedi.jpg', 'mime_type': 'image/jpeg', 'media_id': IMG}]}]
    assert memory.chat_text(history).startswith('Kullanıcı: kedi.jpg')  # Its name is text too.
    assert memory.chat_media(history)[0]['kind'] == 'image'


def test_audio_pieces_cut_130_seconds_into_three_wav_pieces_with_real_pyav():
    pieces, seconds = memory.audio_pieces(tone(130, rate=44100))
    assert len(pieces) == 3 and seconds == pytest.approx(130, abs=0.1)
    lengths = []
    for piece in pieces:
        with wave.open(io.BytesIO(piece)) as reader:
            assert (reader.getnchannels(), reader.getframerate(), reader.getsampwidth()) == (1, 16000, 2)
            lengths.append(reader.getnframes() / 16000)
    assert lengths[0] == pytest.approx(60, abs=0.01) and lengths[1] == pytest.approx(60, abs=0.01)
    assert lengths[2] == pytest.approx(10, abs=0.1)


class DeleteConn(Conn):
    async def fetch(self, sql, *args):
        if 'where group_id is null' in sql:
            return []
        self.delete_args = args
        return [{'id': 7, 'text': 'kedi.jpg', 'media_id': IMG}, {'id': 8, 'text': 'not', 'media_id': None}]


def test_deleting_a_media_row_drops_its_memory_link(monkeypatch):
    conn = DeleteConn()
    wire(monkeypatch, conn, GEMMA)
    unlinked = []

    async def unlink(media_id, owner_kind, owner_id, *, conn=None):
        unlinked.append((media_id, owner_kind, owner_id))
    monkeypatch.setattr(media_store, 'unlink_media', unlink)
    assert [r['id'] for r in asyncio.run(memory.delete_items('u1', [7, 8]))] == [7, 8]
    assert unlinked == [(IMG, 'memory', '7')] and conn.delete_args[0] == 'u1'


class MediaConn(Conn):
    async def fetchrow(self, sql, *args):
        self.row_args = args
        return {'id': IMG, 'mime_type': 'image/jpeg', 'data': b'jpeg'} if args == ('u1', 7) else None


def _media_route(monkeypatch, user, item, headers=None):
    conn = MediaConn()
    wire(monkeypatch, conn, GEMMA)
    request = SimpleNamespace(headers=headers or {})
    return asyncio.run(routes.memory_item_media(item, request, user)), conn


def test_the_media_endpoint_streams_the_users_file_and_answers_others_with_404(monkeypatch):
    response, conn = _media_route(monkeypatch, 'u1', 7)
    assert response.body == b'jpeg' and response.media_type == 'image/jpeg'
    assert response.headers['etag'] == f'"{IMG}"' and 'private' in response.headers['cache-control']
    for user, item in (('u2', 7), ('u1', 8)):
        with pytest.raises(HTTPException) as error:
            _media_route(monkeypatch, user, item)
        assert error.value.status_code == 404 and error.value.detail == 'Media not found'


def test_the_media_endpoint_answers_a_known_etag_with_304(monkeypatch):
    response, _ = _media_route(monkeypatch, 'u1', 7, {'if-none-match': f'"{IMG}"'})
    assert response.status_code == 304


def test_the_media_path_passes_the_access_boundary():
    from orion.api.access_boundary import public_path
    assert public_path('/api/v1/memory/12/media')


def test_the_tool_gives_label_kind_and_date_and_says_how_to_open_media(monkeypatch):
    async def search(user, query, settings, page=1, kind='all', exclude=None, also=None):
        return {'rows': [{'id': 3, 'text': 'kedi.jpg — Kedimin fotoğrafı', 'kind': 'image', 'media_id': IMG,
                          'date': '2026-11-14', 'score': 0.31, 'match': 'meaning'},
                         {'id': 4, 'text': 'Kullanıcı: adım Ayşe', 'kind': 'text', 'media_id': None,
                          'date': '2026-11-13', 'score': 0.2, 'match': 'meaning'}], 'more': False, 'other_spaces': 0}

    async def settings(*args):
        return object()
    monkeypatch.setattr(memory, 'search', search)
    monkeypatch.setattr('orion.kernel.config.get_runtime_settings', settings)
    found = asyncio.run(memory_tools.hafizada_ara(memory_tools.MemoryInput(sorgu='kedinin fotoğrafı'),
                                                 ToolContext('u1', 'c', 't', None)))
    assert found['kayitlar'][0] == {'id': 3, 'tarih': '2026-11-14', 'tur': 'görsel',
                                    'etiket': 'kedi.jpg — Kedimin fotoğrafı', 'skor': 0.31, 'eslesme': 'anlam'}
    assert found['kayitlar'][1]['metin'] == 'Kullanıcı: adım Ayşe'
    assert 'hafizadaki_medyayi_ac' in found['medya_notu'] and 'göremezsin' not in found['medya_notu']


def test_the_catalog_says_what_each_extra_file_takes(monkeypatch):
    from services.shared import local_parts
    monkeypatch.setattr(local_parts, 'model_meta', lambda *args, **kwargs: None)
    hw = local_parts.hardware()
    jina = local_parts.embedding_media({'embedding': {'model': 'jina-v5-omni-nano', 'vision_off': []}}, hw)
    assert (jina['image'], jina['audio'], jina['extra_kinds']) == (True, False, ['image'])
    gemma = local_parts.embedding_media({'embedding': {'model': 'embeddinggemma-2', 'vision_off': []}}, hw)
    assert gemma['image'] and gemma['audio'] and gemma['model'] == 'EmbeddingGemma 2'
    off = local_parts.embedding_media({'embedding': {'model': 'embeddinggemma-2'}}, hw)  # Off by default.
    assert off['has_extra'] and not off['extra_on'] and not off['image'] and not off['audio']


class ReuseConn(Conn):
    """A file already stored in another memory: its rows are copied, never embedded again."""
    def __init__(self, in_group=False):
        super().__init__()
        self.in_group = in_group

    async def fetchval(self, sql, *args):
        if 'group_id = $2' in sql and 'media_id = $3' in sql:
            return 1 if self.in_group else None
        if 'select item_id' in sql:
            return 'old-item'
        return await super().fetchval(sql, *args)

    async def fetch(self, sql, *args):
        if 'item_id = $3' in sql:
            return [{'text': 'kedi.jpg', 'embedding': '[0.1,0.2]', 'kind': 'image', 'media_mime': 'image/png',
                     'media_seconds': None, 'whole': False, 'span_start': None, 'span_end': None, 'track': None}]
        return []


def test_a_file_already_in_another_memory_is_added_with_its_vector_and_one_media_copy(monkeypatch):
    conn = ReuseConn()
    embedded = wire(monkeypatch, conn, GEMMA)

    async def join(conn, user_id, group):
        return group or 'new-group'
    monkeypatch.setattr(memory, 'join_group', join)
    result = asyncio.run(memory.add_file('u1', 'kedi.png', 'image/png', b'png', SimpleNamespace(), group='g2',
                                         caption='yeni'))
    assert result['added'] == 1 and result['warnings'] == [] and embedded == []
    row = conn.rows[0]
    assert row[3] == '[0.1,0.2]' and row[10] == 'g2' and row[13] != 'old-item' and row[18] == 'yeni'
    assert conn.links and conn.links[0][1] == 'memory'


def test_the_same_file_twice_in_one_memory_is_skipped(monkeypatch):
    conn = ReuseConn(in_group=True)
    wire(monkeypatch, conn, GEMMA)

    async def join(conn, user_id, group):
        return group
    monkeypatch.setattr(memory, 'join_group', join)
    result = asyncio.run(memory.add_file('u1', 'kedi.png', 'image/png', b'png', SimpleNamespace(), group='g2'))
    assert result['added'] == 0 and result['warnings'] == ['Bu dosya bu hafızada zaten var.'] and not conn.rows
