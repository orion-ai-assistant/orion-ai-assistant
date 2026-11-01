"""The setup window: only its own one-time page on this PC can drive it, and each type installs its parts."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from scripts.release import setup_window


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_window, 'existing', lambda: None)
    monkeypatch.setattr(setup_window, 'elevated', lambda: False)
    started = []

    class Process:
        stdout = iter(['downloading\n', json.dumps({'status': 'installed'}) + '\n'])
        def wait(self):
            return 0
    monkeypatch.setattr(setup_window.subprocess, 'Popen', lambda command, **kwargs: started.append(command) or Process())
    setup = setup_window.Setup(tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), None)
    port = server.server_address[1]
    server.RequestHandlerClass = setup_window.handler(setup, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield setup, f'http://127.0.0.1:{port}', started
    server.shutdown()


def request(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers or {}), timeout=5) as response:
            return response.status, json.loads(response.read() or b'null') if 'json' in response.headers['Content-Type'] else None
    except urllib.error.HTTPError as error:
        return error.code, None


def test_only_the_one_time_page_answers(window):
    setup, base, _ = window
    assert request(base + '/' + setup.token + '/')[0] == 200
    assert request(base + '/')[0] == 404
    assert request(base + '/wrong-token/state')[0] == 404
    status, state = request(base + '/' + setup.token + '/state')
    assert status == 200 and state['fixed_root'] is False
    assert [row['action'] for row in state['plans']['baglan']] == ['install', 'install']


def test_installing_needs_the_page_itself(window):
    setup, base, started = window
    path = base + '/' + setup.token + '/install'
    assert request(path, {'mode': 'baglan'})[0] == 403
    assert request(path, {'mode': 'baglan'}, {'X-Orion-Setup': '1', 'Origin': 'http://evil.example'})[0] == 403
    assert started == []
    assert request(path, {'mode': 'baglan', 'root': 'D:/Orion', 'start_with_windows': False},
                   {'X-Orion-Setup': '1', 'Content-Type': 'application/json'})[0] == 200
    command = started[0]
    assert command[command.index('--mode') + 1] == 'baglan' and command[command.index('--root') + 1] == 'D:/Orion'
    assert '--no-start-with-windows' in command and '--launch' in command and '--add-missing' not in command
    setup.finished.wait(5)
    assert setup.result == {'ok': True, 'code': 0, 'status': {'status': 'installed'}}


def test_an_existing_installation_only_gets_what_it_lacks(window, monkeypatch):
    setup, base, started = window
    monkeypatch.setattr(setup_window, 'existing', lambda: {'root': 'C:/Orion', 'components': [
        {'kind': 'router', 'code_path': 'r'}]})
    assert request(base + '/' + setup.token + '/install', {'mode': 'merkez', 'root': 'D:/elsewhere'},
                   {'X-Orion-Setup': '1'})[0] == 200
    command = started[0]
    assert '--add-missing' in command and command[command.index('--root') + 1] == 'C:/Orion'
    assert '--launch' not in command  # Adding never starts or restarts what the user is running.
