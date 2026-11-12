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
