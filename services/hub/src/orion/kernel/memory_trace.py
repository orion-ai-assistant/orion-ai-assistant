"""Where an API server's memory grows, when Python traces its allocations.

Off by default (tracing costs CPU and memory). Start the Hub with PYTHONTRACEMALLOC=1 (or a frame depth, e.g. 5)
and every API server logs, every REPORT_SECONDS, the code lines whose allocations grew most since its first
report: a leak shows as the same line growing report after report. The supervisor's [MEMORY] lines say which
process grows; this says where inside it (Python allocations only: numpy arrays count, native libraries do not).
"""
import asyncio
import logging
import os
import tracemalloc

REPORT_SECONDS = 600
TOP = 8

log = logging.getLogger('orion.memory')


async def report_growth(name):
    if not tracemalloc.is_tracing():
        return
    base = tracemalloc.take_snapshot()
    while True:
        await asyncio.sleep(REPORT_SECONDS)
        snapshot = tracemalloc.take_snapshot()
        current, peak = tracemalloc.get_traced_memory()
        lines = [f'{stat.size_diff / 2**20:+.1f} MB {stat.traceback.format()[-1].strip()}'
                 for stat in snapshot.compare_to(base, 'lineno')[:TOP] if stat.size_diff > 0]
        log.warning('Memory %s (pid %d): traced %.0f MB (peak %.0f); grew since start: %s', name, os.getpid(),
                    current / 2**20, peak / 2**20, ' | '.join(lines) or 'nothing')
