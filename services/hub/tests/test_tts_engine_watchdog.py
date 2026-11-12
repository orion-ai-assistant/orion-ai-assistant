"""A TTS engine server that runs but no longer answers is restarted (it looked alive, so it stayed hung)."""
import asyncio
import http.server
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tts'))
import engines  # noqa: E402


class Health(http.server.BaseHTTPRequestHandler):
    hang = threading.Event()

    def do_GET(self):
        if Health.hang.is_set():
            Health.hang.wait(10)  # Holds the request past the probe's timeout.
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{}')

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Health.hang.clear()
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Health)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    Health.hang.clear()
    httpd.shutdown()


def engine(port):
    item = engines.VoxCPM2GGUFEngine.__new__(engines.VoxCPM2GGUFEngine)  # Its server is not started.
    item.host, item.port = '127.0.0.1', port
    item.server_proc = Mock()
    item.server_proc.poll.return_value = None
    item.PROBE_TIMEOUT = 0.3
    return item


def test_an_answering_server_is_ok(server):
    assert engine(server.server_address[1]).server_state() == 'ok'


def test_hung_only_after_three_unanswered_probes_in_a_row(server):
    item = engine(server.server_address[1])
    Health.hang.set()
    assert [item.server_state() for _ in range(3)] == ['ok', 'ok', 'hung']
    Health.hang.clear()
    assert item.server_state() == 'ok' and item._unanswered == 0


def test_an_answer_in_between_starts_the_count_again(server):
    item = engine(server.server_address[1])
    Health.hang.set()
    item.server_state(); item.server_state()
    Health.hang.clear()
    item.server_state()
    Health.hang.set()
    assert [item.server_state() for _ in range(2)] == ['ok', 'ok']


def test_an_ended_process_is_down_not_hung(server):
    item = engine(server.server_address[1])
    item.server_proc.poll.return_value = 1
    assert item.server_state() == 'down'


def test_a_hung_server_is_stopped_and_started_again(server):
    item = engine(server.server_address[1])
    item._unanswered = 3
    calls = []
    item._shutdown_server = lambda: calls.append('stop')
    item._start_server = lambda: calls.append('start')
    item.restart_hung_server()
    assert calls == ['stop', 'start'] and item._unanswered == 0


def test_the_service_watchdog_restarts_a_hung_engine(monkeypatch):
    api = pytest.importorskip('api')
    fake = Mock()
    fake.server_state.return_value = 'hung'
    fake.server_alive.return_value = True
    monkeypatch.setattr(api, 'engine', fake)
    asyncio.run(api.check_engine_once())
    fake.restart_hung_server.assert_called_once()
    assert api._engine_state['value'] == 'ok'
    fake.server_state.return_value = 'ok'
    asyncio.run(api.check_engine_once())
    fake.restart_hung_server.assert_called_once()


def test_a_single_request_that_makes_no_progress_is_hung_after_the_limit(server):
    item = engine(server.server_address[1])  # Its /health still answers.
    with item._engine_call():
        assert item.server_state() == 'ok'
        item._last_activity -= item.STALLED_AFTER + 1
        assert item.stalled_seconds() >= item.STALLED_AFTER
        assert item.server_state() == 'hung'


def test_no_request_in_flight_is_never_stalled(server):
    item = engine(server.server_address[1])
    item._calls_state()
    item._last_activity -= 10_000
    assert item.stalled_seconds() == 0.0 and item.server_state() == 'ok'
    with item._engine_call():
        pass
    assert item.stalled_seconds() == 0.0


def test_a_queued_request_is_not_hung_while_another_progresses(server):
    item = engine(server.server_address[1])
    with item._engine_call() as running, item._engine_call():
        item._last_activity -= item.STALLED_AFTER + 1
        assert item.server_state() == 'hung'  # Nothing moved for the limit...
        running.progress()  # ...until the running one receives a byte.
        assert item.stalled_seconds() < 1
        assert item.server_state() == 'ok'


def test_time_spent_handing_a_piece_on_is_not_the_servers_stall(server):
    item = engine(server.server_address[1])
    with item._engine_call() as call:
        call.progress()
        with call.handing_on():  # A slow reader: nothing in flight on the server's side.
            item._last_activity -= item.STALLED_AFTER + 1
            assert item.stalled_seconds() == 0.0 and item.server_state() == 'ok'
        assert item.stalled_seconds() < 1  # Counting starts again from the hand-back.
        assert item.server_state() == 'ok'


def test_a_finished_request_leaves_nothing_behind_even_on_error(server):
    item = engine(server.server_address[1])
    with pytest.raises(RuntimeError):
        with item._engine_call():
            raise RuntimeError('boom')
    assert not item._calls


def test_a_restart_clears_the_tracked_calls_and_the_stall(server):
    item = engine(server.server_address[1])
    item._shutdown_server = lambda: None
    item._start_server = lambda: None
    with item._engine_call():
        item._last_activity -= item.STALLED_AFTER + 1
        assert item.server_state() == 'hung'
        item.restart_hung_server()
        assert item.stalled_seconds() == 0.0
        assert item.server_state() == 'ok'
