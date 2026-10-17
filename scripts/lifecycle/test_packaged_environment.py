"""Packaged config precedence without starting or connecting to any DB."""
import importlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.parametrize('explicit,expected', [(None, 'private-data'), ('process-value', 'process-value')])
def test_private_data_config_overrides_package_defaults_but_not_process(tmp_path, explicit, expected):
    code, data, state = (tmp_path / name for name in ('code', 'data', 'state'))
    (code / 'services/shared').mkdir(parents=True)
    (data / 'services').mkdir(parents=True)
    state.mkdir()
    (code / 'orion-release.json').write_text('{}')
    (code / 'services/.env.global').write_text('STAGE05_CONFIG_PROBE=package-default\n')
    (data / 'services/.env.global.local').write_text('STAGE05_CONFIG_PROBE=private-data\n')
    registry = tmp_path / 'install.json'
    registry.write_text(json.dumps({'schema_version': 1, 'components': [{'distribution': 'packaged',
        'code_path': str(code), 'data_path': str(data), 'state_path': str(state)}]}))
    with patch.dict(os.environ, {}, clear=True):
        environment = importlib.import_module('services.shared.environment')
        os.environ['ORION_INSTALLATION_REGISTRY'] = str(registry)
        if explicit:
            os.environ['STAGE05_CONFIG_PROBE'] = explicit
        with patch.object(environment, '__file__', str(code / 'services/shared/environment.py')):
            environment.load_environment()
        assert environment.get_env('STAGE05_CONFIG_PROBE') == expected


@pytest.mark.skipif(os.name != 'nt', reason='Windows installer contract')
@pytest.mark.parametrize('relative_field', ['root', 'registry'])
def test_installer_rejects_relative_authority_before_fetch_or_write(tmp_path, relative_field):
    from scripts.release.install_new import install
    class NeverFetch:
        def check(self, names):
            raise AssertionError('Invalid authority path must fail before catalog access')
    root, registry = tmp_path / 'fresh', tmp_path / 'install.json'
    if relative_field == 'root':
        root = Path('relative-install-root')
    else:
        registry = Path('relative-install.json')
    with pytest.raises(ValueError, match='absolute'):
        install(NeverFetch(), [], root, registry)
    assert not (tmp_path / 'fresh').exists()
