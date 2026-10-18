import pytest


@pytest.fixture(autouse=True)
def no_update_windows(monkeypatch):
    """The updating window and the app-window closer are real OS actions; sandboxes never run them."""
    monkeypatch.setenv('ORION_UPDATE_UI', 'off')
    monkeypatch.setenv('ORION_ALLOW_ELEVATED', '1')  # Sandboxes may run from an elevated shell; tests patch is_elevated.
