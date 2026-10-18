"""Orion never runs elevated, and a different integrity level is explained, not shown as a Repair case."""
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from test_stop_reliability import install, fakes  # noqa: F401  (fixtures)
from services.shared import launcher
from services.shared.installation_registry import RegistryError
from services.shared.process_lifecycle import LifecycleError

ROOT = Path(__file__).resolve().parents[2]


def test_orion_is_never_started_elevated(fakes):
    registry, calls, *_ = fakes
    with patch('services.shared.launcher.is_elevated', return_value=True):
        with pytest.raises(RegistryError, match='ORION-ELEVATED-SELF'):
            launcher.launch(registry)
        assert launcher.launch(registry, stop=True) == {'status': 'stopped'}  # Stopping is still allowed.


def test_services_owned_by_another_integrity_level_are_explained_on_stop(fakes):
    registry, calls, stop_hub, stop_router = fakes
    stop_hub.side_effect = LifecycleError('Process access denied; ownership unknown')
    with pytest.raises(RegistryError, match='ORION-ELEVATED: '):
        launcher.launch(registry, stop=True)
    assert 'router' in calls and 'tray' in calls  # The rest was still attempted.


def test_ownership_denied_while_verifying_counts_as_not_stopped(install):
    registry, hub, router = install
    components = launcher.managed_components(registry.read())
    with patch('services.shared.process_lifecycle.Runtime.read', autospec=True,
               side_effect=LifecycleError('Process job access denied; ownership unknown')):
        assert sorted(launcher.running_services(components)) == ['hub', 'router']


def test_main_prints_a_code_the_entry_script_turns_into_a_turkish_dialog(fakes):
    registry, *_ = fakes
    with patch('services.shared.launcher.launch', side_effect=LifecycleError('Process access denied; ownership unknown')):
        out = io.StringIO()
        with redirect_stdout(out):
            code = launcher.main(['--registry', str(registry.path)])
    assert code == 1 and 'ORION-ELEVATED:' in out.getvalue() and 'Cik' in out.getvalue()
    assert launcher.explain(RuntimeError('Shutdown timeout')) == 'Shutdown timeout'


def test_entry_script_has_a_dedicated_elevated_dialog_and_stays_ascii():
    data = (ROOT / 'services/shared/launcher_entry.ps1').read_bytes()
    assert all(byte < 128 for byte in data)
    text = data.decode('ascii')
    assert 'function Show-OrionElevated' in text and "-match 'ORION-ELEVATED'" in text
    assert text.index("-match 'ORION-ELEVATED'") < text.index('Show-OrionRepair $_.Exception.Message')
