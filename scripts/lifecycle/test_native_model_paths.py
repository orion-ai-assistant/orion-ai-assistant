"""Native command construction uses registered model storage without loading AI."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class NativeModelCommandTests(unittest.TestCase):
    def test_llama_and_embedding_commands_use_external_model_root(self):
        for relative in ('services/llm/llama-cpp/api.py', 'services/embedding/llama-cpp-embed/api.py'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as temporary:
                storage = Path(temporary) / 'Özel modeller'
                storage.mkdir()
                model = storage / 'model.gguf'
                model.write_bytes(b'synthetic model, never loaded')
                projector = storage / 'projector.gguf'
                projector.write_bytes(b'synthetic projector')
                spec = importlib.util.spec_from_file_location('native_model_fixture', ROOT / relative)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                original_exists = os.path.exists
                def exists(path):
                    if Path(path).name in {'llama-server.exe', 'llama-server'}:
                        return True
                    return original_exists(path)
                with ExitStack() as stack:
                    stack.enter_context(patch.dict(os.environ, ORION_NATIVE_MODELS_DIR=str(storage)))
                    stack.enter_context(patch.object(module, 'get_env_dict', return_value={
                        'MODEL_FILE': model.name, 'MMPROJ_FILE': projector.name, 'LLM_PORT': '18085', 'EMBED_PORT': '18086'}))
                    stack.enter_context(patch.object(module.os.path, 'exists', side_effect=exists))
                    stack.enter_context(patch.object(module.signal, 'signal'))
                    process = stack.enter_context(patch.object(module.subprocess, 'Popen'))
                    module.main()
                    command = process.call_args.args[0]
                    self.assertIn(str(model), command)
                    if '/llm/' in relative:
                        self.assertIn(str(projector), command)

    def test_existing_router_setup_never_executes_download_or_removes_source(self):
        from installer.app import config, services
        from services.shared import router_installation
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as temporary:
            source = Path(temporary)
            sentinel = source / 'synthetic-db'
            sentinel.write_bytes(b'existing data; no .git/HEAD')
            with patch.object(router_installation, 'find_router_script', return_value=('win', str(source / 'orionrouter.ps1'))), \
                    patch.object(services, 'start_local_service', return_value=True) as start, \
                    patch.object(services.subprocess, 'run', side_effect=AssertionError('No download, git or OS kill allowed')):
                services.run_local_installation('orion-router', str(source))
            start.assert_called_once_with('orion-router')
            self.assertEqual(b'existing data; no .git/HEAD', sentinel.read_bytes())
            self.assertNotIn('orion-router', config.INSTALLING_SERVICES)


if __name__ == '__main__':
    unittest.main()
