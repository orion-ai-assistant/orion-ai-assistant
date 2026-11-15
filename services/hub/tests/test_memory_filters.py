"""Search honesty (noise against the query's own distribution) and negative terms."""
import asyncio

from orion.kernel import memory_filters as mf


def test_the_typical_score_is_measured_from_the_querys_own_scores():
    flat = [0.30, 0.31, 0.29, 0.30, 0.32, 0.31, 0.30, 0.29, 0.33]
    stats = mf.calibrate(flat)
    assert abs(stats[0] - 0.305) < 0.01
    assert mf.standing(0.62, mf.calibrate(flat + [0.62])) >= mf.EXCLUDE_Z
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
    # A "benzemesin" file: its vector alone, no words to look for.
    out = asyncio.run(mf.excluded_groups(conn, 'u', 'space', [], None, ['[0]']))
    assert out == {'kopek_foto'}
