import os
import subprocess
import sys

from services.shared.process_lifecycle import ProcessReference, living


def test_a_live_recorded_process_is_living():
    assert living(ProcessReference.capture(os.getpid()))


def test_a_record_whose_number_now_belongs_to_another_process_is_not_living():
    # The record says this process, but started at another time: as after a restart that gave its number away.
    record = ProcessReference.capture(os.getpid())
    record['started'] -= 3600
    assert living(record) is False


def test_a_record_whose_number_now_belongs_to_a_protected_process_is_not_living():
    # A system process the user may not open (e.g. its number was reused by a service after a restart).
    record = {**ProcessReference.capture(os.getpid()), 'pid': 4 if os.name == 'nt' else 1}
    assert living(record) is False


def test_an_ended_process_is_not_living():
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        record = ProcessReference.capture(child.pid)
    finally:
        child.kill()
        child.wait()
    assert living(record) is False
