"""Where an API server's memory grows, when Python traces its allocations.

Off by default (tracing costs CPU and memory). Start the Hub with PYTHONTRACEMALLOC=1 (or a frame depth, e.g. 5)
and every API server logs:
- every REPORT_SECONDS, the code lines whose allocations grew most since its first report (a leak shows as the same
  line growing report after report), with the process's committed memory beside the traced one: when committed grows
  and traced does not, the growth is in native code (a library's own allocations) or left over from a freed peak;
- a spike: when traced memory rises SPIKE_MB above the highest level seen so far, the lines that hold it at that
  moment (a big transient allocation, gone by the next report, still leaves the heap committed).
The supervisor's [MEMORY] lines say which process grows; this says where inside it (Python allocations only: numpy
arrays count, native libraries do not).
"""
import asyncio
import logging
import os
import tracemalloc

REPORT_SECONDS = 600
SPIKE_EVERY = 1.0
SPIKE_MB = 30
TOP = 8

log = logging.getLogger('orion.memory')


def _committed_mb():
    try:
        import psutil
        info = psutil.Process().memory_info()
        return getattr(info, 'private', info.rss) / 2**20
    except Exception:
        return 0.0


def _where(traceback):
    """The allocating line and, with a deeper trace (PYTHONTRACEMALLOC=5), the Orion code that called it."""
    frames = list(reversed(traceback))  # Most recent call first.
    mine = [f for f in frames[1:] if 'orion' in f.filename.replace(os.sep, '/').split('/site-packages/')[0]]
    shown = [frames[0]] + mine[:2]
    return ' < '.join(f'{os.path.basename(f.filename)}:{f.lineno}' for f in shown)


def _top(snapshot, base):
    group = 'traceback' if tracemalloc.get_traceback_limit() > 1 else 'lineno'
    return ' | '.join(f'{stat.size_diff / 2**20:+.1f} MB {_where(stat.traceback)}'
                      for stat in snapshot.compare_to(base, group)[:TOP] if stat.size_diff > 0) or 'nothing'


async def report_growth(name):
    if not tracemalloc.is_tracing():
        return
    base = tracemalloc.take_snapshot()
    pid = os.getpid()
    high = tracemalloc.get_traced_memory()[0]
    loop = asyncio.get_running_loop()
    next_report = loop.time() + REPORT_SECONDS
    while True:
        await asyncio.sleep(SPIKE_EVERY)
        current = tracemalloc.get_traced_memory()[0]
        if current > high + SPIKE_MB * 2**20:
            high = current
            log.warning('Memory %s (pid %d): spike, traced %.0f MB, committed %.0f MB; held by: %s', name, pid,
                        current / 2**20, _committed_mb(), _top(tracemalloc.take_snapshot(), base))
        if loop.time() >= next_report:
            next_report = loop.time() + REPORT_SECONDS
            current, peak = tracemalloc.get_traced_memory()
            log.warning('Memory %s (pid %d): traced %.0f MB (peak %.0f), committed %.0f MB; grew since start: %s',
                        name, pid, current / 2**20, peak / 2**20, _committed_mb(),
                        _top(tracemalloc.take_snapshot(), base))
