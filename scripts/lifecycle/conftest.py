import pytest


@pytest.fixture(autouse=True)
def no_real_port_check(monkeypatch):
    """A start's port check reads this PC's real ports, which a developer's own Orion may hold."""
    monkeypatch.setattr('services.shared.launcher.ports_taken', lambda *args, **kwargs: None)
