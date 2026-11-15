"""Overlapping pieces and whole vectors (texts, long audio, video), the background video job, per-part scores,
search by file and the tools' parts/neighbours, on a throwaway real PostgreSQL (test_memory_search.PostgresMemoryCase)
and a tiny video PyAV makes."""
import asyncio
import base64
import hashlib
import io
import math
from unittest.mock import patch

import av
import numpy as np
import pytest

from orion.api import routes
from orion.kernel import media_store, memory, memory_groups, memory_media_jobs, registry
from orion.kernel import memory_pieces as mp
from orion.worker.tools._base import ToolContext
from orion.worker.tools.memory import tools as memory_tools

from test_memory_search import SPACE, PostgresMemoryCase, png, vec

GEMMA = {'model': 'EmbeddingGemma 2', 'has_extra': True, 'extra_on': True, 'extra_kinds': ['audio', 'image'],
         'image': True, 'audio': True, 'video': True}


def clip(seconds: float = 70, fps: int = 2, size=(64, 48), audio: bool = True) -> bytes:
    """A small MP4: a bar moving over a background whose colour changes every 20 s, and a tone."""
    out = io.BytesIO()
    with av.open(out, 'w', format='mp4') as container:
        codec = 'libx264' if 'libx264' in av.codecs_available else 'mpeg4'
        video = container.add_stream(codec, rate=fps)
        video.width, video.height, video.pix_fmt = size[0], size[1], 'yuv420p'
        sound = container.add_stream('aac', rate=16000) if audio else None
        if sound is not None:
            sound.layout = 'mono'
        for i in range(int(seconds * fps)):
            image = np.zeros((size[1], size[0], 3), np.uint8)
            image[:, :, (i // (20 * fps)) % 3] = 180
            x = (i * 3) % (size[0] - 8)
            image[10:30, x:x + 8] = 255
            for packet in video.encode(av.VideoFrame.from_ndarray(image, format='rgb24')):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        if sound is not None:
            total = int(seconds * 16000)
            samples = (0.3 * np.sin(2 * math.pi * 440 * np.arange(total) / 16000) * 32767).astype('<i2')
            for start in range(0, total, 1024):
                frame = av.AudioFrame.from_ndarray(samples[None, start:start + 1024], format='s16', layout='mono')
                frame.sample_rate = 16000
                for packet in sound.encode(frame):
                    container.mux(packet)
            for packet in sound.encode():
                container.mux(packet)
    return out.getvalue()


# --- Maths ------------------------------------------------------------------------------------------------------------

def test_spans_tile_the_item_and_windows_overlap_by_a_third():
    found = mp.spans(130, 60)
    assert [(s.start, s.end) for s in found] == [(0, 40), (40, 80), (80, 120), (120, 130)]
    assert [(s.window_start, s.window_end) for s in found] == [(0, 50), (30, 90), (70, 130), (110, 130)]
    middle = found[1]
    overlap = found[0].window_end - middle.window_start
    assert overlap == pytest.approx((middle.window_end - middle.window_start) / 3)
    assert [(s.start, s.end) for s in mp.spans(45, 60)] == [(0, 45)]  # Not longer than a window: one piece.
    assert [(s.start, s.end) for s in mp.spans(80.5, 60)] == [(0, 40), (40, 80.5)]  # A tail < 1 s joins.


def test_the_window_and_frame_count_follow_the_context_and_the_measured_frame_cost():
    assert mp.audio_window(2048) == 60 and mp.audio_window(512) == 17 and mp.audio_window(None) == 60
    assert mp.frames_per_window(2048, 79) == 25  # EmbeddingGemma 2: ~79 tokens per <=448 px frame.
    assert mp.frames_per_window(2048, 277) == 7  # Jina v5: ~277.
    assert mp.frames_per_window(8192, 79) == 30  # At most one a second.
    assert mp.frames_per_window(2048, None) == 6  # The server did not say: a cost above any measured one.
    assert mp.frames_per_window(256, 277) == 1


def test_frames_beside_the_sound_fit_the_context():
    assert mp.combined_frames(2048, 79, 30) == 15  # 2048 - 750 sound - 64 margin = 1234 -> 15 frames.
    assert mp.combined_frames(2048, 277, 30) == 4
    assert mp.combined_frames(1024, 277, 30) == 0  # Not one frame beside 30 s of sound.


def test_the_whole_vector_is_the_normalized_mean_of_the_unit_pieces():
    whole = mp.mean_vector([[3.0, 0.0], [0.0, 1.0]])
    assert whole == pytest.approx([math.sqrt(0.5), math.sqrt(0.5)])
    assert math.isclose(sum(v * v for v in mp.mean_vector([[1, 2, 3], [4, 5, 6], [-1, 0, 2]])), 1.0)


def test_text_pieces_tile_the_text_and_windows_reach_into_the_neighbours():
    paragraphs = [f'Paragraf {k}: ' + ' '.join(f'kelime{k}_{i}' for i in range(60)) for k in range(8)]
    pieces = mp.text_pieces('\n\n'.join(paragraphs))
    assert len(pieces) > 2
    cores = [core for core, _ in pieces]
    assert '\n\n'.join(cores).split() == '\n\n'.join(paragraphs).split()  # The cores are the text, once.
    core, window = pieces[1]
    assert window.startswith(pieces[0][0][-40:].split()[-1]) or pieces[0][0].split()[-1] in window
    assert pieces[2][0].split()[0] in window and core in window
    assert pieces[0][1].startswith(pieces[0][0])  # The first has nothing before it.


def test_window_frames_take_the_frames_inside_spread_evenly():
    times = [k + 0.5 for k in range(60)]
    frames = [f'f{k}'.encode() for k in range(60)]
    picked = mp.window_frames(times, frames, mp.Span(20, 40, 15, 45), 6)
    assert len(picked) == 6 and all(15 <= int(f[1:]) + 0.5 < 45 for f in picked)
    assert mp.window_frames([1.0, 50.0], [b'a', b'b'], mp.Span(20, 40, 15, 45), 4) == [b'b']  # The nearest.


def test_a_video_is_decoded_without_ffmpeg():
    raw = clip(30, fps=2)
    info = mp.video_info(raw)
    assert info['seconds'] == pytest.approx(30, abs=1) and info['audio'] and info['width'] == 64
    times, frames, last = mp.video_frames(raw, [0.5, 10.5, 29.5])
    assert times == [0.5, 10.5, 29.5] and all(f.startswith(b'\xff\xd8') for f in frames) and last > 29
    assert mp.frame_at(raw, 12.0).startswith(b'\xff\xd8')
    assert len(mp.decode_pcm(raw)) / 32000 == pytest.approx(30, abs=0.2)
    with pytest.raises(ValueError):
        mp.video_info(b'not a video')


def test_promote_pieces_shows_the_best_piece_where_the_whole_ranked():
    ranked = [{'id': 9, 'whole': True, 'item_id': 'v', 'track': 'frames', 'score': 0.8},
              {'id': 3, 'item_id': 'n', 'score': 0.7},
              {'id': 5, 'item_id': 'v', 'track': 'frames', 'score': 0.6},
              {'id': 6, 'item_id': 'v', 'track': 'frames', 'score': 0.5},
              {'id': 10, 'whole': True, 'item_id': 'w', 'score': 0.4}]
    shown = memory.promote_pieces(ranked)
    assert [r['id'] for r in shown] == [5, 3, 6, 10]
    assert shown[0]['via_whole'] and shown[0]['whole_score'] == 0.8


# --- Real PostgreSQL ------------------------------------------------------------------------------------------------

class MemoryPiecesTests(PostgresMemoryCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        conn = await registry._connect()
        try:
            await conn.execute('truncate orion_memory_media_jobs')
        finally:
            await conn.close()

    def patches(self, support=GEMMA, frame_tokens=79):
        """The embedding server: a frames request answers by its first frame's background colour, a sound one by
        its length; texts by whether they mention 'kedi'."""
        self.requests = []

        async def target(settings):
            return settings, SPACE, True

        async def embed(text, settings, kind, local):
            return [vec(0.9 if 'kedi' in text else 0.1, 2)]

        async def embed_media(parts, target):
            self.requests.append([p['type'] for p in parts])
            if parts[0]['type'] == 'input_audio':
                return vec(0.2, 7)
            if len(parts) == 1 and parts[0]['image_url']['url'].startswith('data:image/png'):
                return vec(0.3, 3)
            return vec(0.5 + 0.1 * len(self.requests) % 0.4, 4 + len(self.requests) % 3)

        async def cost(frame, target):
            return frame_tokens
        guards = [patch.object(memory, 'embedding_target', target), patch.object(memory, '_embed', embed),
                  patch.object(memory, '_embed_media', embed_media),
                  patch.object(memory, 'media_support', lambda local, name: support),
                  patch.object(memory, 'embedding_context', lambda: 2048),
                  patch.object(memory_media_jobs, 'frame_cost', cost)]
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)

    async def finish_jobs(self):
        for _ in range(400):
            if not memory_media_jobs._tasks:
                return
            await asyncio.sleep(0.05)
        raise AssertionError('the video job did not finish')

    async def rows(self, where='true'):
        conn = await registry._connect()
        try:
            return [dict(r) for r in await conn.fetch(
                f'select id, text, kind, track, whole, span_start, span_end, item_id, group_id, caption, media_id '
                f'from orion_memory where {where} order by id')]
        finally:
            await conn.close()

    async def test_a_video_is_embedded_in_the_background_as_segments_of_frames_and_sound_with_whole_vectors(self):
        self.patches()
        raw = clip(70, fps=2)
        added = await memory.add_file('alice', 'tatil.mp4', 'video/mp4', raw, self.settings, caption='denizde')
        self.assertEqual((added['added'], added['job']['state']), (0, 'running'))
        await self.finish_jobs()
        jobs = await memory_media_jobs.jobs('alice')
        self.assertEqual((jobs[0]['state'], jobs[0]['done'], jobs[0]['total']), ('done', 4, 4))
        rows = await self.rows()
        frames = [r for r in rows if r['track'] == 'frames' and not r['whole']]
        sound = [r for r in rows if r['track'] == 'audio' and not r['whole']]
        self.assertEqual([(r['span_start'], r['span_end']) for r in frames], [(0, 20), (20, 40), (40, 60), (60, 70)])
        self.assertEqual(len(sound), 4)
        together = [r for r in rows if r['track'] == 'both' and not r['whole']]
        self.assertEqual([(r['span_start'], r['span_end']) for r in together], [(0, 20), (20, 40), (40, 60), (60, 70)])
        self.assertEqual(together[1]['text'], 'Video 1:10 (tatil.mp4) 0:20–0:40 görüntü+ses — denizde')
        self.assertEqual(sorted((r['track'], r['whole']) for r in rows if r['whole']),
                         [('audio', True), ('both', True), ('frames', True)])
        self.assertEqual({r['kind'] for r in rows}, {'video'})
        self.assertEqual(len({r['item_id'] for r in rows}), 1)
        self.assertEqual(frames[1]['text'], 'Video 1:10 (tatil.mp4) 0:20–0:40 görüntü — denizde')
        self.assertEqual(frames[1]['caption'], 'denizde')
        # 30 s windows at 2 fps hold <= 25 frames at Gemma's cost: one request per segment, 20-25 frames.
        frame_requests = [r for r in self.requests if r[0] == 'image_url' and 'input_audio' not in r]
        self.assertEqual(len(frame_requests), 4)
        self.assertTrue(all(len(r) <= 25 for r in frame_requests) and len(frame_requests[1]) >= 20)
        # Frames and 30 s of sound (750 tokens) in one request: as many frames as fit beside it, 15 at Gemma's cost.
        combined = [r for r in self.requests if 'input_audio' in r and r[0] == 'image_url']
        self.assertEqual([len(r) for r in combined], [16] * 4)
        self.assertEqual(combined[0][-1], 'input_audio')
        conn = await registry._connect()
        try:  # The job's own link to the file is gone; each row keeps it.
            links = await conn.fetch('select owner_id from orion_media_links')
        finally:
            await conn.close()
        self.assertFalse(any(link['owner_id'].startswith('job:') for link in links))
        self.assertEqual(len(links), len(rows))
        page = await memory_groups.list_groups('alice', self.settings)
        card = page['groups'][0]
        self.assertEqual((card['counts'], card['part_count'], card['kind']), ({'video': 1}, 12, 'video'))
        self.assertEqual([p['id'] for p in card['parts']], [frames[0]['id']])  # One preview: its first segment.
        detail = await memory_groups.group_detail('alice', card['group_id'], self.settings)
        self.assertEqual(len(detail['parts']), 12)
        self.assertEqual((detail['parts'][0]['piece'], detail['parts'][0]['pieces'], detail['parts'][0]['track']),
                         (1, 4, 'frames'))
        self.assertEqual(page['summary']['per_kind'], {'video': 1})

    async def test_a_model_that_refuses_frames_and_sound_together_keeps_both_alone(self):
        self.patches()
        alone = memory._embed_media

        async def picky(parts, target):
            if len({p['type'] for p in parts}) > 1:
                raise RuntimeError('500 mixed input')
            return await alone(parts, target)
        with patch.object(memory, '_embed_media', picky):
            await memory.add_file('alice', 'v.mp4', 'video/mp4', clip(70, fps=2), self.settings)
            await self.finish_jobs()
        rows = await self.rows()
        self.assertEqual({r['track'] for r in rows}, {'frames', 'audio'})
        self.assertEqual((await memory_media_jobs.jobs('alice'))[0]['state'], 'done')

    async def test_a_video_too_long_or_not_a_video_is_refused(self):
        self.patches()
        with patch.object(mp, 'MAX_VIDEO_SECONDS', 20):
            with self.assertRaisesRegex(ValueError, 'Video çok uzun'):
                await memory.add_file('alice', 'uzun.mp4', 'video/mp4', clip(30, fps=1), self.settings)
        with self.assertRaisesRegex(ValueError, 'Video okunamadı'):
            await memory.add_file('alice', 'bozuk.mp4', 'video/mp4', b'\x00' * 100, self.settings)
        self.patches(support={**GEMMA, 'image': False, 'video': False, 'extra_on': False})
        with self.assertRaisesRegex(ValueError, 'ek dosyası kapalı'):
            await memory.add_file('alice', 'v.mp4', 'video/mp4', clip(5, fps=1), self.settings)

    async def test_cancelling_a_video_job_keeps_nothing(self):
        self.patches()
        added = await memory.add_file('alice', 'v.mp4', 'video/mp4', clip(70, fps=2), self.settings)
        self.assertTrue(await memory_media_jobs.cancel('alice', added['job']['id']))
        await self.finish_jobs()
        self.assertEqual(await self.rows(), [])
        self.assertEqual((await memory_media_jobs.jobs('alice'))[0]['state'], 'cancelled')
        conn = await registry._connect()
        try:
            self.assertEqual(await conn.fetchval('select count(*) from orion_media'), 0)  # The file went too.
        finally:
            await conn.close()
        self.assertFalse(await memory_media_jobs.cancel('bob', added['job']['id']))

    async def test_a_long_note_is_overlapping_pieces_with_a_whole_vector_and_search_names_the_best_piece(self):
        self.patches()
        parts = [' '.join([f'konu{k}'] * 60) for k in range(3)] + ['Burada kedi var. ' * 12]
        added = await memory.add_note_to('alice', '\n\n'.join(parts)[:3900], self.settings)
        rows = await self.rows()
        pieces = [r for r in rows if not r['whole']]
        self.assertGreaterEqual(len(pieces), 3)
        self.assertEqual(sum(r['whole'] for r in rows), 1)
        self.assertEqual(added['added'], len(rows))
        page = await memory_groups.list_groups('alice', self.settings, q='kedi')
        card = page['groups'][0]
        best = next(p for p in pieces if 'kedi' in p['text'])
        self.assertEqual(card['best_part']['id'], best['id'])
        self.assertEqual((card['best_part']['piece'], card['best_part']['pieces']), (len(pieces), len(pieces)))
        # The piece before it was embedded with the start of the 'kedi' piece in its window: it matches by meaning.
        self.assertEqual(card['matched_parts'], 2)
        scores = await memory_groups.part_scores('alice', card['group_id'], self.settings, q='kedi')
        self.assertTrue(scores['parts'][str(best['id'])]['best'])
        self.assertEqual(scores['parts'][str(best['id'])]['match'], 'text')
        self.assertEqual(scores['matched'], 2)
        before = pieces[pieces.index(best) - 1]
        self.assertEqual(scores['parts'][str(before['id'])]['match'], 'meaning')
        self.assertIsNotNone(scores['whole_score'])

    async def test_the_whole_vector_ranks_an_item_and_the_card_shows_its_best_piece(self):
        await self.add('ilgisiz not', score=0.5, axis=9, group='n')
        await self.add('parça bir', score=0.3, group='v', axis=2)
        conn = await registry._connect()
        try:
            await conn.execute("update orion_memory set item_id = 'it', item_order = 1 where group_id = 'v'")
        finally:
            await conn.close()
        second = await self.add('parça iki', score=0.4, group='v', axis=3)
        whole = await self.add('tamamı', score=0.7, group='v', axis=4)
        conn = await registry._connect()
        try:
            await conn.execute("update orion_memory set item_id = 'it', item_order = 1 where group_id = 'v'")
            await conn.execute('update orion_memory set whole = true where id = $1', whole)
        finally:
            await conn.close()
        page = await memory_groups.list_groups('alice', self.settings, q='zzz')
        self.assertEqual([c['group_id'] for c in page['groups']], ['v', 'n'])
        card = page['groups'][0]
        self.assertTrue(card['via_whole'])
        self.assertEqual((card['matched_part'], card['best_part']['id'], card['best_part']['piece']), (second, second, 2))
        self.assertEqual(card['part_count'], 2)
        found = await memory.search('alice', 'zzz', self.settings)
        self.assertEqual(found['rows'][0]['id'], second)
        self.assertTrue(found['rows'][0]['via_whole'])
        tool = memory_tools._row(found['rows'][0], {})
        self.assertEqual((tool['parca'], tool['eslesme_notu'][:10]), ('2/2', 'kaydın tam'))

    async def test_deleting_pieces_keeps_the_whole_vector_right(self):
        self.patches()
        await memory.add_note_to('alice', '\n\n'.join(' '.join([f'konu{k}'] * 60) for k in range(3)), self.settings)
        rows = await self.rows()
        pieces = [r for r in rows if not r['whole']]
        self.assertEqual(len(pieces), 3)
        conn = await registry._connect()
        try:
            await conn.execute('update orion_memory set embedding = $2::vector where id = $1', pieces[1]['id'],
                               memory._vector(vec(0.9, 5)))
            before = await conn.fetchval('select embedding::text from orion_memory where whole')
            await memory.delete_items('alice', [pieces[0]['id']])
            after = await conn.fetchval('select embedding::text from orion_memory where whole')
            self.assertNotEqual(before, after)
            await memory.delete_items('alice', [pieces[1]['id']])
            self.assertEqual(await conn.fetchval('select count(*) from orion_memory where whole'), 0)
        finally:
            await conn.close()

    async def test_context_tool_gives_just_the_neighbours_of_a_piece(self):
        self.patches()
        await memory.add_note_to('alice', '\n\n'.join(' '.join([f'konu{k}'] * 60) for k in range(5)), self.settings)
        pieces = [r for r in await self.rows() if not r['whole']]
        self.assertEqual(len(pieces), 5)
        context = ToolContext('alice', 'chat', 't', None)
        with patch('orion.kernel.config.get_runtime_settings', self._settings()):
            near = await memory_tools.hafiza_baglami(memory_tools.MemoryContextInput(id=pieces[2]['id'], komsu=1),
                                                     context)
            whole = await memory_tools.hafiza_baglami(memory_tools.MemoryContextInput(id=pieces[2]['id']), context)
        self.assertEqual([p['id'] for p in near['parcalar']], [p['id'] for p in pieces[1:4]])
        self.assertEqual([p['parca'] for p in near['parcalar']], ['2/5', '3/5', '4/5'])
        self.assertTrue(near['parcalar'][1]['sorulan'] and 'kapsam' in near)
        self.assertEqual(len(whole['parcalar']), 5)

    def _settings(self):
        async def settings(*args):
            return self.settings
        return settings

    async def test_search_by_file_finds_the_same_image_and_pages_with_the_vector(self):
        self.patches()
        raw = png()
        await memory.add_file('alice', 'kalp.png', 'image/png', raw, self.settings)
        await memory.add_note_to('alice', 'bir not', self.settings)
        data = 'data:image/png;base64,' + base64.b64encode(raw).decode()
        with patch.object(routes, 'get_runtime_settings', self._settings()):
            request = type('R', (), {'app': type('A', (), {'state': type('S', (), {'redis': None})})})()
            found = await routes.search_memory_by_file(routes.MemorySearch(name='kalp.png', data=data, limit=1),
                                                       request, 'alice')
            self.assertEqual(found['groups'][0]['parts'][0]['kind'], 'image')
            self.assertEqual(found['groups'][0]['score'], pytest.approx(1.0, abs=1e-3))
            self.assertTrue(found['more'] and found['query']['kind'] == 'image')
            nxt = await routes.search_memory_by_file(routes.MemorySearch(
                vector=found['query']['vector'], vector_space=found['query']['vector_space'], limit=1, offset=1),
                request, 'alice')
            self.assertEqual(nxt['groups'][0]['counts'], {'text': 1})
            match = await routes.memory_group_match(found['groups'][0]['group_id'], routes.MemoryMatch(
                vector=found['query']['vector'], vector_space=SPACE), request, 'alice')
            self.assertTrue(next(iter(match['parts'].values()))['best'])
            with self.assertRaises(routes.HTTPException) as error:
                await routes.search_memory_by_file(routes.MemorySearch(name='x.pdf', data='data:application/pdf;base64,JVBERg=='),
                                                   request, 'alice')
            self.assertEqual(error.exception.status_code, 400)

    async def test_search_by_video_file_embeds_frames_spread_over_it(self):
        self.patches()
        space, vector, kind = await memory.file_query_vector('v.mp4', 'video/mp4', clip(20, fps=2), self.settings)
        self.assertEqual((space, kind), (SPACE, 'video'))
        self.assertEqual(len(self.requests[-1]), 20)  # min(25 that fit, 20 s at 1 fps... spread over the clip).

    async def test_opening_a_video_segment_shows_a_few_frames_and_its_sound(self):
        self.patches()
        added = await memory.add_file('alice', 'v.mp4', 'video/mp4', clip(70, fps=2), self.settings)
        await self.finish_jobs()
        rows = await self.rows()
        segment = next(r for r in rows if r['track'] == 'audio' and r['span_start'] == 20)
        opened = await memory.open_media('alice', 'chat', [segment['id']], {'image', 'audio'})
        media = opened[0]['media']
        self.assertEqual([m['kind'] for m in media], ['image'] * 4 + ['audio'])
        self.assertEqual(media[-1]['mime'], 'audio/wav')
        conn = await registry._connect()
        try:
            wav = await conn.fetchval('select data from orion_media where id = $1', media[-1]['media_id'])
            owners = {r['media_id'] for r in await conn.fetch(
                "select media_id from orion_media_links where owner_kind = 'chat' and owner_id = 'chat'")}
        finally:
            await conn.close()
        self.assertAlmostEqual((len(wav) - 44) / 32000, 20, delta=0.2)  # The core's 20 s.
        self.assertTrue({m['media_id'] for m in media} <= owners)
        only_frames = await memory.open_media('alice', 'chat', [segment['id']], {'image'})
        self.assertEqual([m['kind'] for m in only_frames[0]['media']], ['image'] * 4)
        with patch.object(memory, 'chat_model_media', self._can()), \
                patch('orion.kernel.config.get_runtime_settings', self._settings()):
            result = await memory_tools.hafizadaki_medyayi_ac(memory_tools.MemoryOpenInput(idler=[segment['id']]),
                                                             ToolContext('alice', 'chat', 't', None))
        self.assertEqual(result['acilanlar'][0]['acilan'], '4 kare, 1 ses')
        self.assertEqual(len(result['_medya']), 5)
        self.assertEqual(added['group_id'], rows[0]['group_id'])

    def _can(self):
        async def can(settings):
            return {'image': True, 'audio': True}
        return can

    async def test_a_caption_is_kept_and_the_frame_endpoint_answers_a_jpeg(self):
        self.patches()
        await memory.add_file('alice', 'v.mp4', 'video/mp4', clip(10, fps=2), self.settings, caption='  bahçe  ')
        await self.finish_jobs()
        rows = await self.rows()
        self.assertEqual({r['caption'] for r in rows}, {'bahçe'})
        request = type('R', (), {'headers': {}})()
        answer = await routes.memory_item_frame(rows[0]['id'], request, None, 'alice')
        self.assertEqual(answer.media_type, 'image/jpeg')
        self.assertTrue(answer.body.startswith(b'\xff\xd8'))
        with self.assertRaises(routes.HTTPException):
            await routes.memory_item_frame(rows[0]['id'], request, None, 'bob')

    async def test_the_tool_says_which_piece_and_when(self):
        self.patches()
        await memory.add_file('alice', 'v.mp4', 'video/mp4', clip(70, fps=2), self.settings)
        await self.finish_jobs()
        found = await memory.search('alice', '', self.settings, kind='video')
        tool = [memory_tools._row(r, {}) for r in found['rows']]
        self.assertTrue(all('tamami' not in t for t in tool))  # Whole vectors are never listed alone.
        times = sorted(t['zaman'] for t in tool)
        self.assertIn('0:20–0:40', times)
        self.assertTrue(all(t['parca'].endswith('/4') for t in tool))
