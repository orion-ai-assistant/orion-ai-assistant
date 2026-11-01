"""Orion's public downloads are verified by Windows itself (services/shared/windows_trust.py)."""
import json
import ssl

import pytest

import web
from services.shared import windows_trust

FEED = 'https://github.com/orion-ai-assistant/orion-ai-assistant/releases/download/feed/'


def test_the_context_is_windows_own_verifier_and_still_verifies():
    truststore = pytest.importorskip('truststore')
    context = windows_trust.context()
    assert isinstance(context, truststore.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_the_update_fetcher_uses_it():
    pytest.importorskip('truststore')
    fetcher = windows_trust.tuf_fetcher()
    pool = fetcher._proxy_env._kw_args
    assert pool['ssl_context'].verify_mode == ssl.CERT_REQUIRED


@pytest.mark.skipif(not __import__('sys').platform.startswith('win'), reason='Windows trust')
def test_the_live_feed_reads_through_release_tools_and_the_updater():
    assert json.loads(web.read(FEED + 'timestamp.json'))['signed']['_type'] == 'timestamp'
    body = b''.join(windows_trust.tuf_fetcher().fetch(FEED + 'timestamp.json'))
    assert json.loads(body)['signed']['_type'] == 'timestamp'


@pytest.mark.skipif(not __import__('sys').platform.startswith('win'), reason='Windows trust')
def test_a_user_without_symlink_rights_can_install(tmp_path, monkeypatch):
    """A normal Windows user (no admin, no Developer Mode) may not create symlinks: WinError 1314."""
    from pathlib import Path
    from services.shared.release_packages import ReleaseClient

    def refused(*args, **kwargs):
        raise OSError(1314, 'A required privilege is not held by the client')
    monkeypatch.setattr('os.symlink', refused)
    root = Path(__import__('os').environ['LOCALAPPDATA']) / 'Orion/bootstrap/trusted-root.json'  # Only read.
    if not root.is_file():
        pytest.skip('No installed Orion trust root on this PC')
    for _ in range(2):  # First run, then again over the kept cache.
        client = ReleaseClient(trusted_root=root, cache=tmp_path, metadata_url=FEED, targets_url=FEED)
        client.updater.refresh()
    current = tmp_path / 'metadata/root.json'
    assert not current.is_symlink()
    version = json.loads(current.read_bytes())['signed']['version']
    assert current.read_bytes() == (tmp_path / f'metadata/root_history/{version}.root.json').read_bytes()


def test_a_wrong_name_is_still_refused():
    pytest.importorskip('truststore')
    import urllib.error
    with pytest.raises(urllib.error.URLError):
        # GitHub's certificate does not name this host: verification stays on.
        web.read('https://140.82.121.4/')
