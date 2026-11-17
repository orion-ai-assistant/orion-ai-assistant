"""Memory scale benchmark: seeds a scratch Postgres (pgvector + pg_trgm) with ~50k pieces of one user (plus as many
of other users) and times a meaning search, a word search, a list page, a panel match and a delete.

Never point it at the Hub's own database: give it a scratch cluster.

  python scripts/memory_scale_bench.py seed --dsn postgresql://orion@127.0.0.1:5461/postgres
  python scripts/memory_scale_bench.py time --dsn ...            # whatever code PYTHONPATH finds (old or new)
  python scripts/memory_scale_bench.py explain --dsn ...         # EXPLAIN ANALYZE of the search queries

Run `seed` and the first `time` with the old code (a checkout before the scale change), then `time`/`explain` with
the new one: ensure_memory_table adds the indexes on the same data."""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(os.environ.get("ORION_SRC", HERE.parent / "src"))))
sys.path.insert(0, str(HERE.parents[2]))

import asyncpg  # noqa: E402

from orion.kernel import memory, memory_groups  # noqa: E402

USER, OTHER = "bench", "other"
SPACE = "bench-space"
_LETTERS = "abcdefghijklmnoprstuvyz"
WORDS = ["".join(random.Random(n).choice(_LETTERS) for _ in range(random.Random(n).randint(5, 9))) for n in range(20000)]


def unit_vectors(count: int, rng) -> list[str]:
    try:
        import numpy as np
        v = np.random.default_rng(rng.randrange(1 << 30)).standard_normal((count, 768)).astype("float32")
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        return ["[" + ",".join(f"{x:.5f}" for x in row) + "]" for row in v]
    except ImportError:
        out = []
        for _ in range(count):
            row = [rng.gauss(0, 1) for _ in range(768)]
            norm = sum(x * x for x in row) ** 0.5
            out.append("[" + ",".join(f"{x / norm:.5f}" for x in row) + "]")
        return out


async def connect(dsn):
    async def _connect():
        return await asyncpg.connect(dsn)
    memory._connect = _connect

    async def current_space(settings):  # The Router probe of the real one is not what is measured here.
        return SPACE
    memory.current_space = current_space
    return await asyncpg.connect(dsn)


async def seed(dsn: str, pieces: int, per_group: int) -> None:
    rng = random.Random(7)
    conn = await connect(dsn)
    await conn.execute("create extension if not exists vector")
    await memory.ensure_memory_table(conn)
    await conn.execute("delete from orion_memory")
    for user in (USER, OTHER):
        made, started = 0, time.perf_counter()
        while made < pieces:
            batch = min(1000, pieces - made)
            texts, groups, items = [], [], []
            for k in range(batch):
                group = f"{user}-g{(made + k) // per_group}"
                texts.append(" ".join(rng.choice(WORDS) for _ in range(rng.randint(12, 40))))
                groups.append(group)
                items.append(group + "-i")
            await conn.execute(
                "insert into orion_memory (user_id, text, vector_space, embedding, source_kind, kind, group_id, item_id, "
                "item_order) select $1, t, $2, v::vector, case when right(g, 1) = '0' then 'file' else 'note' end, "
                "'text', g, i, 0 from unnest($3::text[], $4::text[], $5::text[], $6::text[]) as x(t, g, i, v)",
                user, SPACE, texts, groups, items, unit_vectors(batch, rng))  # Every 10th memory a file.
            made += batch
        print(f"seeded {made} rows for {user} in {time.perf_counter() - started:.0f}s")
    await conn.execute("analyze orion_memory")
    await conn.close()


async def timed(label: str, factory, repeat: int = 5) -> float:
    await factory()  # warm
    runs = []
    for _ in range(repeat):
        started = time.perf_counter()
        await factory()
        runs.append((time.perf_counter() - started) * 1000)
    print(f"{label:34s} median {statistics.median(runs):9.1f} ms   max {max(runs):9.1f} ms")
    return statistics.median(runs)


async def measure(dsn: str) -> None:
    conn = await connect(dsn)
    started = time.perf_counter()
    await memory.ensure_memory_table(conn)  # new code: adds hash column, HNSW and trigram indexes (once)
    print(f"ensure_memory_table: {time.perf_counter() - started:.1f}s (no index build: it runs in the background)")
    if hasattr(memory, "_build_indexes"):  # New code: the background build, awaited here to time it.
        if memory._index_task is not None:  # The Hub's own scheduled build: replaced by this awaited one.
            memory._index_task.cancel()
        memory.INDEX_DELAY = 0
        started = time.perf_counter()
        await memory._build_indexes()
        print(f"background index build (concurrently): {time.perf_counter() - started:.1f}s")
    await conn.execute("analyze orion_memory")
    rng = random.Random(11)
    vector = unit_vectors(1, rng)[0]
    sample = await conn.fetchval("select text from orion_memory where user_id = $1 and group_id = $2 limit 1", USER,
                                 await conn.fetchval("select group_id from orion_memory where user_id = $1 limit 1 offset 700", USER))
    word = sample.split()[3]  # A word one memory has: selective, the usual case.
    near = word[:-1] + ("a" if word[-1] != "a" else "e")  # One letter off.
    count = await conn.fetchval("select count(*) from orion_memory where user_id = $1", USER)
    print(f"user rows: {count}")
    settings = None
    await timed("meaning search (page 1)", lambda: memory_groups.list_groups(
        USER, settings, q="", vector=vector, vector_space_of_query=SPACE, vector_space=SPACE, limit=20))
    await timed("meaning + word search (page 1)", lambda: memory_groups.list_groups(
        USER, settings, q=word, vector=vector, vector_space_of_query=SPACE, vector_space=SPACE, limit=20))
    await timed("word search (near spelling)", lambda: memory_groups.list_groups(
        USER, settings, q=near, vector=vector, vector_space_of_query=SPACE, vector_space=SPACE, limit=20))
    await timed("list page (newest, no query)", lambda: memory_groups.list_groups(
        USER, settings, limit=20, vector_space=SPACE))
    group = await conn.fetchval("select group_id from orion_memory where user_id = $1 limit 1", USER)
    await timed("panel match (one memory)", lambda: memory_groups.part_scores(
        USER, group, settings, q=word, vector=vector, space=SPACE))
    for_delete = [r["item_id"] for r in await conn.fetch(
        "select distinct item_id from orion_memory where user_id = $1 order by item_id limit 40", USER)]
    started = time.perf_counter()
    for chunk in (for_delete[:20], for_delete[20:]):
        await memory.delete_whole_items(USER, chunk)
    print(f"{'delete 40 memories x ~25 pieces':34s} total {(time.perf_counter() - started) * 1000:9.1f} ms")
    try:
        from orion.kernel import memory_collections, memory_trash
    except ImportError:  # Old code: no collections, no trash.
        await conn.close()
        return
    from types import SimpleNamespace
    v2 = SimpleNamespace(memory_v2=True, memory_trash_days=30)
    started = time.perf_counter()
    await memory_collections.ensure_user(conn, USER)
    print(f"{'memory_v2: sort existing (once)':34s} total {(time.perf_counter() - started) * 1000:9.1f} ms")
    await conn.execute("analyze orion_memory")
    await timed("v2 meaning search, whole memory", lambda: memory_groups.list_groups(
        USER, v2, q="", vector=vector, vector_space_of_query=SPACE, vector_space=SPACE, limit=20))
    await timed("v2 meaning search, in documents", lambda: memory_groups.list_groups(
        USER, v2, q="", vector=vector, vector_space_of_query=SPACE, vector_space=SPACE, limit=20,
        collections=["documents"]))
    await timed("v2 meaning + word, in documents", lambda: memory_groups.list_groups(
        USER, v2, q=word, vector=vector, vector_space_of_query=SPACE, vector_space=SPACE, limit=20,
        collections=["documents"]))
    await timed("v2 list page, in documents", lambda: memory_groups.list_groups(
        USER, v2, limit=20, vector_space=SPACE, collections=["documents"]))
    groups = [r["group_id"] for r in await conn.fetch(
        "select distinct group_id from orion_memory where user_id = $1 and deleted_at is null order by group_id "
        "limit 40", USER)]
    started = time.perf_counter()
    removed = [await memory_trash.trash_groups(USER, groups[k:k + 20]) for k in (0, 20)]
    print(f"{'trash 40 memories x ~25 pieces':34s} total {(time.perf_counter() - started) * 1000:9.1f} ms")
    await timed("meaning search (40 in the trash)", lambda: memory_groups.list_groups(
        USER, settings, q="", vector=vector, vector_space_of_query=SPACE, vector_space=SPACE, limit=20))
    await timed("list page (40 in the trash)", lambda: memory_groups.list_groups(
        USER, settings, limit=20, vector_space=SPACE))
    await timed("trash list (2 removals)", lambda: memory_trash.list_trash(USER, v2))
    started = time.perf_counter()
    await memory_trash.restore(USER, [removed[0]["trash_id"]])
    print(f"{'restore 20 memories':34s} total {(time.perf_counter() - started) * 1000:9.1f} ms")
    started = time.perf_counter()
    await memory_trash.purge(USER, [removed[1]["trash_id"]])
    print(f"{'purge 20 memories':34s} total {(time.perf_counter() - started) * 1000:9.1f} ms")
    await conn.close()


async def explain(dsn: str) -> None:
    conn = await connect(dsn)
    await memory.ensure_memory_table(conn)
    vector = unit_vectors(1, random.Random(3))[0]
    plans = {
        "meaning (nearest rows)": ("select id, 1 - (embedding <=> $3::vector) from orion_memory where user_id = $1 "
                                   "and vector_space = $2 order by embedding <=> $3::vector limit 200",
                                   [USER, SPACE, vector]),
        "word (like or <%)": (f"select id from orion_memory where user_id = $1 and not whole and "
                              f"({memory.FOLD_SQL} like $2 or $3 <% {memory.FOLD_SQL}) limit 100",
                              [USER, "%" + WORDS[7] + "%", WORDS[7]]),
        "duplicate check (hash)": ("select content_hash from orion_memory where user_id = $1 and vector_space = $2 "
                                   "and content_hash = any($3::text[])", [USER, SPACE, ["x", "y"]]),
    }
    for name, (sql, args) in plans.items():
        async with conn.transaction():
            await conn.execute("select set_config('hnsw.ef_search', '200', true), "
                               "set_config('hnsw.iterative_scan', 'relaxed_order', true), "
                               "set_config('pg_trgm.word_similarity_threshold', '0.45', true)")
            print(f"\n== {name}")
            for row in await conn.fetch("explain (analyze, costs off, timing on) " + sql, *args):
                print("  " + row[0])
    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["seed", "time", "explain"])
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--pieces", type=int, default=50000)
    parser.add_argument("--per-group", type=int, default=25)
    args = parser.parse_args()
    if args.stage == "seed":
        asyncio.run(seed(args.dsn, args.pieces, args.per_group))
    elif args.stage == "time":
        asyncio.run(measure(args.dsn))
    else:
        asyncio.run(explain(args.dsn))
