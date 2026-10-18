"""Version skew: files in <Orion>/bootstrap and <Orion>/updates are shared by component versions that are
updated at different times (the retained bootstrap updates itself before the Hub). Whatever the new code
writes there, the OLD shipped code must still read. The old readers run from git history, unchanged."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from test_update_visibility import _install
from services.shared import bootstrap_update
from services.shared.automatic_updates import AutomaticUpdates, SOURCE_FILE, SOURCES_FILE, normalize_sources
from services.shared.update_helper import UpdateHelper

REPO = Path(__file__).resolve().parents[2]
# Shipped Hubs that may still be installed next to a newer bootstrap: 0.1.11 (schema 1 only) and 0.1.12
# (first one that knows schema 2).
OLD_HUBS = {'0.1.11': 'f35121f', '0.1.12': 'ce22c7e'}
HUB = 'https://hub-feed.example/'
ROUTER = 'https://router-feed.example/'


def feed(url, targets, cache=None):
    value = {'metadata_url': url + 'm/', 'targets_url': url + 't/', 'targets': targets}
    return {**value, 'cache': cache} if cache else value


# What publish_release.build_source ships in the Hub feed's bootstrap bundle (bootstrap 0.1.4).
TWO_FEEDS = {'schema': 2, 'channel': 'stable',
             'sources': [feed(HUB, {'hub': 'hub.zip', 'client': 'client.zip'}, 'release-cache'),
                         feed(ROUTER, {'router': 'router.zip'}, 'release-cache-router')]}


@pytest.fixture(scope='module')
def old_code(tmp_path_factory):
    """services/shared of each old Hub, extracted from git (no checkout, no network)."""
    roots = {}
    for version, commit in OLD_HUBS.items():
        archive = subprocess.run(['git', 'archive', '--format=tar', commit, 'services/shared'], cwd=REPO,
                                 capture_output=True, check=True).stdout
        root = tmp_path_factory.mktemp('hub-' + version)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(root)  # Our own repository's history.
        roots[version] = root
    return roots


def old_status(root, registry):
    """What the old Hub's GET /updates computes: AutomaticUpdates(registry).status() of that version."""
    code = ('import json,sys; from services.shared.automatic_updates import AutomaticUpdates;'
            'from services.shared.installation_registry import Registry;'
            'print(json.dumps(AutomaticUpdates(Registry(sys.argv[1])).status()))')
    env = {**os.environ, 'PYTHONPATH': str(root), 'ORION_INSTALLATION_REGISTRY': str(registry.path)}
    result = subprocess.run([sys.executable, '-B', '-c', code, str(registry.path)], cwd=root, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-1500:]
    return json.loads(result.stdout)


def bootstrap_with(tmp_path, source):
    own = tmp_path / 'Bootstrap-Installer-s9'
    own.mkdir()
    (own / 'release-source.json').write_text(json.dumps(source))
    return own


def test_old_hubs_read_what_the_new_bootstrap_writes(tmp_path, old_code):
    """The user's PC: bootstrap 0.1.4 wrote schema 2 into release-source.json and Hub 0.1.11 answered 503.
    The new bootstrap's source sync heals that file and keeps both forms readable."""
    registry = _install(tmp_path, ['hub', 'client', 'router'])
    folder = tmp_path / 'bootstrap'
    (folder / SOURCE_FILE).write_text(json.dumps(TWO_FEEDS))  # Exactly what bootstrap 0.1.4 left behind.
    with pytest.raises(AssertionError, match='Invalid stable release source'):
        old_status(old_code['0.1.11'], registry)  # The bug, reproduced with the shipped 0.1.11 code.

    assert bootstrap_update.sync_source(registry, bootstrap_with(tmp_path, TWO_FEEDS))
    for version, root in old_code.items():
        status = old_status(root, registry)
        assert status['configured'] is True and status['error'] is None, version
    legacy = json.loads((folder / SOURCE_FILE).read_text())
    assert legacy['schema'] == 1 and legacy['metadata_url'] == HUB + 'm/'
    # New code still sees both feeds (the Router feed lives in release-sources.json).
    feeds = AutomaticUpdates(registry).sources()
    assert {f['metadata_url'] for f in feeds} == {HUB + 'm/', ROUTER + 'm/'}
    assert next(f for f in feeds if f['metadata_url'] == HUB + 'm/')['cache'] == 'release-cache'
    assert not bootstrap_update.sync_source(registry, tmp_path / 'Bootstrap-Installer-s9')  # Nothing left to fix.


def test_new_hub_degrades_on_an_unrecognized_source(tmp_path):
    """A shared file this Hub cannot read: the card still shows versions/history, says why, never fails."""
    registry = _install(tmp_path, ['hub', 'client'])
    folder = tmp_path / 'bootstrap'
    (folder / SOURCE_FILE).write_text(json.dumps({'schema': 99, 'channel': 'stable', 'future': True}))
    status = AutomaticUpdates(registry, helper=UpdateHelper(registry)).status()
    assert status['source_error'] == 'SourceUnrecognized' and status['error'] == 'SourceUnrecognized'
    assert 'tanınmadı' in status['error_message'] and status['versions']['hub']
    # A valid file next to it wins: one unreadable file never hides the other.
    (folder / SOURCES_FILE).write_text(json.dumps(TWO_FEEDS))
    status = AutomaticUpdates(registry, helper=UpdateHelper(registry)).status()
    assert status['source_error'] is None and status['configured']
    assert len(normalize_sources(AutomaticUpdates(registry).source())) == 2
