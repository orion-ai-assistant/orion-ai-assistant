import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.shared import environment, launcher, native_services


class RouterStartEnvironmentTests(unittest.TestCase):
    def test_router_does_not_inherit_the_hubs_docker_service_names(self):
        # Importing the Hub's environment loads TTS_HOST=tts; the Router must use its own .env instead.
        root = Path(tempfile.mkdtemp())
        (root / 'core').mkdir()
        (root / 'core/process_lifecycle.py').write_text('')
        # launch_environment() set POSTGRES_PORT over the parent's own value; that value comes back.
        with patch.dict(os.environ, {'TTS_HOST': 'tts', 'POSTGRES_PORT': '5445', 'PARENT_ONLY': 'kept'}), patch.dict(
                environment.LOADED, {'TTS_HOST': 'tts'}, clear=True), patch.object(
                launcher, '_hub_only', {'POSTGRES_PORT': '5433'}), patch(
                'services.shared.update_gate.require_start_admission'), patch.object(
                native_services, 'dispatch_if_needed', return_value=False), patch.object(
                native_services, 'router_root', return_value=root), patch.object(
                native_services, 'router_python', return_value='python'), patch(
                'services.shared.process_lifecycle.ensure_started') as started:
            native_services.start_router(root / 'cli.py')
        env = started.call_args.kwargs['env']
        self.assertNotIn('TTS_HOST', env)
        self.assertEqual(env['POSTGRES_PORT'], '5433')
        self.assertEqual(env['PARENT_ONLY'], 'kept')

