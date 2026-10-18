"""Per-step wall-clock timing for the release tooling (build and acceptance).

`Timings.step(name)` prints one line when a step ends and `summary()` prints a table that
shows where the minutes went. Thread-safe so parallel build steps can share one instance.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import time


class Timings:
    def __init__(self, title='', *, stream=None):
        self.title = title
        self.rows = []
        self.started = time.perf_counter()
        self._lock = threading.Lock()
        self._print = (lambda text: print(text, file=stream, flush=True)) if stream else (lambda text: print(text, flush=True))

    @contextmanager
    def step(self, name, note=''):
        begin = time.perf_counter()
        self._print(f'[{self.title}] > {name}')
        status = 'ok'
        try:
            yield
        except BaseException:
            status = 'FAILED'
            raise
        finally:
            seconds = time.perf_counter() - begin
            with self._lock:
                self.rows.append({'step': name, 'seconds': round(seconds, 2), 'status': status, 'note': note})
            self._print(f'[{self.title}] < {name}: {seconds:.1f}s {status}{" - " + note if note else ""}')

    def skipped(self, name, why):
        with self._lock:
            self.rows.append({'step': name, 'seconds': 0.0, 'status': 'skipped', 'note': why})
        self._print(f'[{self.title}] = {name}: skipped ({why})')

    def total(self):
        return time.perf_counter() - self.started

    def summary(self):
        width = max([len(r['step']) for r in self.rows] + [4])
        lines = [f'--- {self.title or "timings"} (wall clock) ---']
        for row in self.rows:
            lines.append(f"{row['step']:<{width}}  {row['seconds']:>7.1f}s  {row['status']}"
                         + (f"  {row['note']}" if row['note'] else ''))
        lines.append(f"{'TOTAL':<{width}}  {self.total():>7.1f}s")
        text = '\n'.join(lines)
        self._print(text)
        return text

    def write(self, path):
        Path(path).write_text(json.dumps({'title': self.title, 'total': round(self.total(), 2), 'steps': self.rows},
                                         indent=2), encoding='utf-8')
