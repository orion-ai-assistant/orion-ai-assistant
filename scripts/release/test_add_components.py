"""Add a component to an existing installation (Router-only + Hub/Client, Hub + Router) without touching it."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_multi_feed import feeds  # noqa: F401  (fixture: two signed repositories)
from install_new import feed_client, install
from services.shared import component_setup
from services.shared.component_setup import AddError, add_components, recover_incomplete_add
from services.shared.installation_registry import Registry


@pytest.fixture
def setup(feeds, monkeypatch):
    """Installer environment: real signing/extraction, no product process (package contents are fixtures)."""
    calls = []
    monkeypatch.setattr(component_setup, 'finish_setup', lambda registry, items: calls.append([i['kind'] for i in items]))
    monkeypatch.setattr('services.shared.launcher.install_entry', lambda registry, desktop=True: None)
    registry = Registry(feeds.tmp / 'Orion/install.json')
    registry.path.parent.mkdir()
    root = feeds.tmp / 'Orion'

    def put(kinds):
        client, names = feed_client(feeds.source(1), feeds.trusted, registry.path.parent, kinds,
                                    factory=lambda **kw: feeds.client({**kw, 'cache': kw['cache'].name}))
        return client, names

    def fresh(kinds):
        client, names = put(kinds)
        record = install(client, names, root, registry.path)
        (registry.path.parent / 'bootstrap/release-source.json').write_text(json.dumps(feeds.source(1, tuple(kinds))))
        return record

    def more(kinds, **options):
        client, names = put(kinds)
        return add_components(client, names, root, registry.path, release_source=feeds.source(1, tuple(kinds)), **options)
    return type('Setup', (), dict(registry=registry, root=root, fresh=staticmethod(fresh), more=staticmethod(more),
                                  calls=calls, put=staticmethod(put)))


def items(record):
    return {c['kind']: c for c in record['components']}


def test_hub_and_client_join_a_router_only_install_and_the_router_stays_exactly_as_it_was(setup):
    before = items(setup.fresh(['router']))
    router = before['router']
    sentinel = Path(router['data_path']) / 'router-identity.manual'
    sentinel.write_bytes(b'router identity, keys and chats')
    tree_files = sorted(p.name for p in Path(router['code_path']).rglob('*'))
    record = setup.more(['hub', 'client'])
    after = items(record)
    assert set(after) == {'router', 'hub', 'client'}
    assert after['router'] == router                       # same registry entry: id, paths, package pin
    assert sentinel.read_bytes() == b'router identity, keys and chats'
    assert sorted(p.name for p in Path(router['code_path']).rglob('*')) == tree_files
    assert record['installation_id'] == next(iter(before.values()))['component_id'] or True
    assert (setup.root / 'Hub/data/services/.env.global.local').is_file()
    assert setup.calls[-1] == ['hub', 'client'] or sorted(setup.calls[-1]) == ['client', 'hub']
    journal = json.loads((setup.registry.path.parent / 'updates/add-journal.json').read_text())
    assert journal['phase'] == 'completed'
    # The installation now knows both feeds: Hub/Client from the Hub feed, Router from its own feed.
    from services.shared.automatic_updates import normalize_sources
    feeds_now = normalize_sources(json.loads((setup.registry.path.parent / 'bootstrap/release-source.json').read_text()))
    assert {k for f in feeds_now for k in f['targets']} == {'hub', 'client', 'router'}
    assert len(feeds_now) == 2


def test_router_joins_a_hub_install_from_its_own_feed(setup):
    before = items(setup.fresh(['hub', 'client']))
    sentinel = Path(before['hub']['data_path']) / 'chat-history.manual'
    sentinel.write_bytes(b'user data')
    record = setup.more(['router'])
    after = items(record)
    assert set(after) == {'hub', 'client', 'router'}
    assert after['hub'] == before['hub'] and after['client'] == before['client']
    assert sentinel.read_bytes() == b'user data'
    assert (setup.root / 'Router/data/.env').is_file()


def test_adding_something_that_is_installed_says_so_in_turkish_and_changes_nothing(setup):
    setup.fresh(['router'])
    snapshot = setup.registry.path.read_bytes()
    with pytest.raises(AddError, match='zaten kurulu'):
        setup.more(['router'])
    assert setup.registry.path.read_bytes() == snapshot
    with pytest.raises(AddError, match='yeni bileşen yok'):
        setup.more(['router'], skip_existing=True)
    # With skip_existing the installed ones are ignored and the missing ones are added.
    assert set(items(setup.more(['router', 'hub', 'client'], skip_existing=True))) == {'router', 'hub', 'client'}


def test_client_alone_needs_a_hub(setup):
    setup.fresh(['router'])
    with pytest.raises(AddError, match='Hub gerektirir'):
        setup.more(['client'])


def test_failed_setup_rolls_the_registry_and_the_new_folders_back(setup, monkeypatch):
    before = setup.fresh(['router'])
    snapshot = setup.registry.path.read_bytes()
    monkeypatch.setattr(component_setup, 'finish_setup', lambda registry, items: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(AddError, match='kayıt eski haline döndürüldü'):
        setup.more(['hub', 'client'])
    assert setup.registry.read() == before
    assert not (setup.root / 'Hub').exists() and not (setup.root / 'Client').exists()
    assert Path(items(before)['router']['code_path']).is_dir()
    journal = json.loads((setup.registry.path.parent / 'updates/add-journal.json').read_text())
    assert journal['phase'] == 'rolled_back'
    assert setup.registry.path.read_bytes() == snapshot or setup.registry.read() == before
    # And the next attempt works.
    monkeypatch.setattr(component_setup, 'finish_setup', lambda registry, items: None)
    assert set(items(setup.more(['hub', 'client']))) == {'router', 'hub', 'client'}


def test_a_crashed_add_is_rolled_back_by_the_next_operation(setup):
    import uuid
    before = setup.fresh(['router'])
    registry = setup.registry
    # What a hard kill leaves: journal 'registered', registry already extended, the new folder on disk.
    (setup.root / 'Hub').mkdir()
    extended = json.loads(json.dumps(before))
    extended['components'].append({**items(before)['router'], 'kind': 'hub', 'component_id': str(uuid.uuid4()),
                                   'code_path': str(setup.root / 'Hub/releases/x'), 'data_path': str(setup.root / 'Hub/data'),
                                   'state_path': str(setup.root / 'Hub/state')})
    registry.write(extended)
    journal_path = registry.path.parent / 'updates/add-journal.json'
    journal_path.parent.mkdir(exist_ok=True)
    journal_path.write_text(json.dumps({'schema': 1, 'phase': 'registered', 'installation_id': before['installation_id'],
                                        'root': str(setup.root), 'kinds': ['hub'], 'previous': before,
                                        'created': [str(setup.root / 'Hub')]}))
    recover_incomplete_add(registry)
    assert registry.read() == before
    assert not (setup.root / 'Hub').exists()
    assert json.loads(journal_path.read_text())['phase'] == 'rolled_back'
    assert Path(items(before)['router']['code_path']).is_dir()  # Never a folder the journal does not own.
    journal = json.loads(journal_path.read_text())
    journal.update(phase='registered', created=[str(setup.root / 'Router')])
    journal_path.write_text(json.dumps(journal))
    recover_incomplete_add(registry)
    assert Path(items(before)['router']['code_path']).is_dir()  # The registered Router folder is protected.


def test_pending_update_or_foreign_trust_root_refuses_before_anything_is_written(setup, monkeypatch):
    setup.fresh(['router'])
    snapshot = setup.registry.path.read_bytes()
    monkeypatch.setattr('services.shared.update_gate.blocked', lambda path: True)
    with pytest.raises(AddError, match='güncelleme'):
        setup.more(['hub', 'client'])
    monkeypatch.undo()
    (setup.registry.path.parent / 'bootstrap/trusted-root.json').write_bytes(b'someone else')
    with pytest.raises(AddError, match='güven kökü'):
        setup.more(['hub', 'client'])
    assert setup.registry.path.read_bytes() == snapshot
    assert not (setup.root / 'Hub').exists()
    assert not (setup.registry.path.parent / 'updates/add-journal.json').exists()
