"""Search honesty (noise against the query's own distribution) and negative terms."""
import asyncio

from orion.kernel import memory_filters as mf


def test_negative_terms_are_parsed_and_a_dash_inside_a_word_stays():
    assert mf.parse_query('kedi -köpek -"iki kelime" e-posta') == ('kedi e-posta', ['köpek', 'iki kelime'])
    assert mf.parse_query('-köpek') == ('', ['köpek'])


def test_a_match_must_stand_out_of_the_querys_own_scores():
    flat = [0.30, 0.31, 0.29, 0.30, 0.32, 0.31, 0.30, 0.29, 0.33]
    stats = mf.calibrate(flat)
    # A gun against cats and notes: the best is still in the noise.
    assert mf.standing(max(flat), stats) < mf.NOISE_Z
    clear = flat + [0.62]
    stats = mf.calibrate(clear)
    assert mf.standing(0.62, stats) >= mf.NOISE_Z
    assert mf.calibrate(flat[:3]) is None  # Too few memories to tell.


class Conn:
    def __init__(self, texts, scores):
        self.texts, self.scores = texts, scores

    async def fetch(self, sql, *args):
        if 'like $2' in sql:
            return [{'g': g} for g, t in self.texts.items() if args[1].strip('%') in t]
        return [{'g': g, 's': s} for g, s in self.scores.items()]


def test_excluded_memories_are_close_to_the_term_or_contain_it(monkeypatch):
    from orion.kernel import memory

    async def vector(q, settings):
        return 'space', '[0]', None
    monkeypatch.setattr(memory, '_query_vector', vector)
    scores = {f'm{k}': 0.2 + 0.005 * k for k in range(10)} | {'kopek_foto': 0.7}
    conn = Conn({'not': 'kopegim cok tatli', 'm1': 'kedi'}, scores)
    out = asyncio.run(mf.excluded_groups(conn, 'u', 'space', ['köpeğim'], None))
    assert 'kopek_foto' in out and 'not' in out and 'm1' not in out
