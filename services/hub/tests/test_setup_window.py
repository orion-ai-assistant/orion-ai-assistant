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
    monkeypatch.setattr(setup_window, 'busy', lambda mode: [])  # This PC's own ports never decide a test.
    monkeypatch.setattr(setup_window, 'registry_path', lambda: tmp_path / 'Orion' / 'install.json')
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
    for _ in range(50):
        if setup.result:
            break
        threading.Event().wait(0.1)
    assert setup.result['ok'] is True and setup.result['status'] == {'status': 'installed'}
    assert not setup.finished.is_set()  # The window stays until the user closes it.


def test_a_failure_stays_on_screen_and_in_a_file(window, monkeypatch, tmp_path):
    setup, base, _ = window
    monkeypatch.setattr(setup_window, 'registry_path', lambda: tmp_path / 'Orion' / 'install.json')

    class Failing:
        stdout = iter(['Traceback: something broke\n'])
        def wait(self):
            return 1
    monkeypatch.setattr(setup_window.subprocess, 'Popen', lambda command, **kwargs: Failing())
    assert request(base + '/' + setup.token + '/install', {'mode': 'baglan'}, {'X-Orion-Setup': '1'})[0] == 200
    for _ in range(50):
        if setup.result:
            break
        threading.Event().wait(0.1)
    assert setup.result['ok'] is False and not setup.finished.is_set()
    status, progress = request(base + '/' + setup.token + '/progress')
    assert status == 200 and 'Traceback: something broke' in progress['lines']
    assert 'Traceback: something broke' in (tmp_path / 'Orion' / 'logs').glob('kurulum-*.log').__next__().read_text(encoding='utf-8')


def test_uninstall_opens_only_with_an_installation(window):
    setup, base, _ = window
    assert request(base + '/' + setup.token + '/uninstall', {}, {'X-Orion-Setup': '1'})[0] == 400


def test_an_existing_installation_only_gets_what_it_lacks(window, monkeypatch):
    setup, base, started = window
    monkeypatch.setattr(setup_window, 'existing', lambda: {'root': 'C:/Orion', 'components': [
        {'kind': 'router', 'code_path': 'r'}]})
    assert request(base + '/' + setup.token + '/install', {'mode': 'merkez', 'root': 'D:/elsewhere'},
                   {'X-Orion-Setup': '1'})[0] == 200
    command = started[0]
    assert '--add-missing' in command and command[command.index('--root') + 1] == 'C:/Orion'
    assert '--launch' not in command  # Adding never starts or restarts what the user is running.


def test_a_new_install_names_the_program_on_its_ports_and_waits(window, monkeypatch):
    setup, base, started = window
    other = [{'ports': [5445, 8910], 'name': 'python.exe', 'orion': True}]
    monkeypatch.setattr(setup_window, 'busy', lambda mode: other if mode == 'merkez' else [])
    status, state = request(base + '/' + setup.token + '/state')
    assert state['busy'] == {'merkez': other, 'baglan': []}
    assert 'yönetici izni' in state['firewall']['merkez'] and 'yönetici izni' in state['firewall']['baglan']
    assert request(base + '/' + setup.token + '/install', {'mode': 'merkez'}, {'X-Orion-Setup': '1'})[0] == 400
    assert started == []
    assert request(base + '/' + setup.token + '/install', {'mode': 'baglan'}, {'X-Orion-Setup': '1'})[0] == 200


def test_an_addition_never_checks_ports(window, monkeypatch):
    setup, base, _ = window
    monkeypatch.setattr(setup_window, 'existing', lambda: {'root': 'C:/Orion', 'components': [
        {'kind': 'router', 'code_path': 'r'}]})
    monkeypatch.setattr(setup_window, 'busy', lambda mode: [{'ports': [9443], 'name': 'python.exe', 'orion': True}])
    assert request(base + '/' + setup.token + '/state')[1]['busy'] == {}  # The running Orion holds its own ports.
    assert request(base + '/' + setup.token + '/install', {'mode': 'merkez'}, {'X-Orion-Setup': '1'})[0] == 200
