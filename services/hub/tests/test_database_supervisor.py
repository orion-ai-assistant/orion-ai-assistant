import threading
import unittest
from unittest.mock import Mock

from services.hub import run_local


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Server:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


class DatabaseSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.stopping = threading.Event()
        self.reports = []
        self.database = Mock()
        self.database._server = Server()
        self.supervisor = run_local.DatabaseSupervisor(self.database, self.stopping, self.reports.append,
                                                       clock=self.clock)

    def tick(self, seconds):
        self.clock.now += seconds
        self.supervisor.check()

    def test_a_running_server_is_left_alone(self):
        self.tick(10)
        self.tick(10)
        self.database.ensure.assert_not_called()
        self.assertEqual(self.reports, [])

    def test_checks_only_every_ten_seconds(self):
        self.database._server = Server(code=1)
        self.tick(5)
        self.database.ensure.assert_not_called()
        self.tick(5)
        self.database.ensure.assert_called_once()

    def test_an_ended_server_is_started_again(self):
        self.database._server = Server(code=1)
        def start():
            self.database._server = Server()
        self.database.ensure.side_effect = start
        self.tick(10)
        self.assertEqual(self.reports, ['PostgreSQL is not running; starting it again', 'PostgreSQL started again'])
        self.tick(10)
        self.database.ensure.assert_called_once()

    def test_failed_starts_back_off_to_a_minute_and_never_give_up(self):
        self.database._server = Server(code=1)
        self.database.ensure.side_effect = RuntimeError('error code 1455')
        delays = []
        for _ in range(6):
            calls = self.database.ensure.call_count
            waited = 0
            while self.database.ensure.call_count == calls:
                self.tick(1)
                waited += 1
            delays.append(waited)
        self.assertEqual(delays, [10, 20, 40, 60, 60, 60])
        self.database.ensure.side_effect = None
        self.tick(60)
        self.assertEqual(self.reports[-1], 'PostgreSQL started again')

    def test_without_its_own_server_process_it_asks_postgres(self):
        self.database._server = None
        self.database.ready.return_value = False
        self.tick(10)
        self.database.ready.assert_called_once()
        self.database.ensure.assert_called_once()

    def test_not_answering_counts_as_down(self):
        self.database._server = None
        self.database.ready.side_effect = RuntimeError('not accepting connections')
        self.tick(10)
        self.database.ensure.assert_called_once()

    def test_nothing_happens_while_the_hub_stops(self):
        self.database._server = Server(code=1)
        self.stopping.set()
        self.tick(30)
        self.database.ensure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
