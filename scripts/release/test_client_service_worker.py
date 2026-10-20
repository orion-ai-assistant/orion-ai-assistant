"""The packaged web client must not carry a Flutter service worker, and old ones are retired."""
import importlib.util
from pathlib import Path
import threading
import urllib.request

import pytest

import build_package

ROOT = Path(__file__).resolve().parents[2]
SERVE = ROOT.parent / 'orion-ai-assistant-flutter/main_app/app/tool/serve_web.py'
BOOTSTRAP_NONE = '_flutter.buildConfig = {};\n\n_flutter.loader.load();\n'
BOOTSTRAP_PWA = '_flutter.loader.load({\n  serviceWorkerSettings: {\n    serviceWorkerVersion: "1"\n  }\n});\n'
LOADER = 'async load({serviceWorkerSettings:e,onEntrypointLoaded:r}={}){}\n'


def web(tmp_path, bootstrap, worker=None):
    (tmp_path / 'flutter_bootstrap.js').write_text(LOADER + bootstrap)
    if worker is not None:
        (tmp_path / 'flutter_service_worker.js').write_text(worker)
    return tmp_path


def test_build_without_service_worker_is_accepted(tmp_path):
    build_package.verify_no_service_worker(web(tmp_path, BOOTSTRAP_NONE, ''))
    build_package.verify_no_service_worker(web(tmp_path / 'x' if (tmp_path / 'x').mkdir() else tmp_path / 'x', BOOTSTRAP_NONE))


@pytest.mark.parametrize('bootstrap,worker', [(BOOTSTRAP_NONE, 'self.addEventListener("fetch",()=>{})'),
                                              (BOOTSTRAP_PWA, ''), (BOOTSTRAP_PWA, 'const RESOURCES={};')])
def test_default_pwa_build_is_rejected(tmp_path, bootstrap, worker):
    with pytest.raises(ValueError, match='pwa-strategy=none'):
        build_package.verify_no_service_worker(web(tmp_path, bootstrap, worker))


def test_real_client_package_build_is_checked_before_copying(tmp_path):
    source = Path(build_package.__file__).read_text(encoding='utf-8')
    assert source.index('verify_no_service_worker(web)') < source.index("shutil.copytree(web, stage / 'build/web')")


def test_workflow_and_docs_use_pwa_strategy_none():
    for path in (ROOT / '.github/workflows/windows-packages.yml', ROOT / 'scripts/release/README.md'):
        assert '--pwa-strategy=none' in path.read_text(encoding='utf-8'), path


@pytest.mark.skipif(not SERVE.is_file(), reason='Flutter repository not checked out beside the Hub')
def test_server_answers_old_worker_url_with_a_self_removing_worker(tmp_path):
    spec = importlib.util.spec_from_file_location('serve_web', SERVE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import http.server
    (tmp_path / 'flutter_service_worker.js').write_text('const RESOURCES={}; // old cache worker')
    module.DIRECTORY = tmp_path
    with http.server.ThreadingHTTPServer(('127.0.0.1', 0), module.CustomHTTPHandler) as server:
        server.isolate = False
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            response = urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/flutter_service_worker.js?v=3')
            body = response.read().decode()
            assert response.headers['Cache-Control'] == 'no-store'
        finally:
            server.shutdown()
    assert 'unregister()' in body and 'caches.delete' in body and 'RESOURCES' not in body


@pytest.mark.skipif(not SERVE.is_file(), reason='Flutter repository not checked out beside the Hub')
def test_icon_urls_carry_the_icon_content_so_a_changed_icon_is_fetched_again(tmp_path):
    # Browsers keep a site icon per URL: Orion windows kept the old (Flutter) icon after it changed.
    spec = importlib.util.spec_from_file_location('serve_web', SERVE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'icons').mkdir()
    (tmp_path / 'favicon.png').write_bytes(b'old icon')
    (tmp_path / 'icons/Icon-192.png').write_bytes(b'touch icon')
    (tmp_path / 'manifest.json').write_text('{}')
    (tmp_path / 'index.html').write_text(
        '<link rel="apple-touch-icon" href="icons/Icon-192.png">\n'
        '<link rel="icon" type="image/png" href="favicon.png"/>\n'
        '<link rel="manifest" href="manifest.json">\n<link rel="stylesheet" href="style.css">\n')
    first = module.versioned_index(tmp_path).decode()
    assert first.count('?v=') == 3 and 'href="style.css"' in first  # Only icon/manifest links.
    (tmp_path / 'favicon.png').write_bytes(b'new icon')
    second = module.versioned_index(tmp_path).decode()
    old, new = (page.split('href="favicon.png?v=')[1][:12] for page in (first, second))
    assert old != new  # A changed icon is a new URL on every PC, without renaming files.
    assert first.split('Icon-192.png?v=')[1][:12] == second.split('Icon-192.png?v=')[1][:12]
    import http.server
    module.DIRECTORY = tmp_path
    with http.server.ThreadingHTTPServer(('127.0.0.1', 0), module.CustomHTTPHandler) as server:
        server.isolate = False
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for path in ('/', '/index.html?x=1'):
                body = urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{path}').read().decode()
                assert f'favicon.png?v={new}' in body
        finally:
            server.shutdown()
