"""Permission-based attachment to existing desktop browser sessions."""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path

import aiohttp

from .cdp import CDPSession
from .desktop import (auto_approve_browser_dialogs, browser_windows, close_window,
                      enable_remote_debugging, minimize_window,
                      running_browser_choices, suggested_browser)


class BrowserUnavailable(RuntimeError):
    pass


class BrowserBusy(RuntimeError):
    pass


class BrowserTabMissing(BrowserUnavailable):
    pass


def browser_root():
    return Path(__file__).resolve().parents[3] / 'data' / 'browser-profiles'


def require_desktop():
    if Path('/.dockerenv').exists() or not (os.name == 'nt' or sys.platform == 'darwin'
            or os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        raise BrowserUnavailable('Tarayıcı araçları yerel masaüstü Orion üzerinde çalışır.')


def find_binary(choice='auto'):
    candidates = {
        'chrome': ['google-chrome', 'google-chrome-stable', 'chrome'],
        'edge': ['microsoft-edge', 'microsoft-edge-stable', 'msedge'],
        'brave': ['brave-browser', 'brave'],
    }
    windows = {'chrome': 'Google/Chrome/Application/chrome.exe',
               'edge': 'Microsoft/Edge/Application/msedge.exe',
               'brave': 'BraveSoftware/Brave-Browser/Application/brave.exe'}
    mac = {'chrome': 'Google Chrome', 'edge': 'Microsoft Edge', 'brave': 'Brave Browser'}
    for name in candidates if choice == 'auto' else [choice]:
        if name not in candidates:
            raise ValueError('Unsupported browser')
        paths = list(candidates[name])
        if os.name == 'nt':
            paths += [str(Path(os.environ[root]) / windows[name])
                      for root in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA') if os.getenv(root)]
        elif sys.platform == 'darwin':
            paths += [f'/Applications/{mac[name]}.app/Contents/MacOS/{mac[name]}']
        for path in paths:
            found = shutil.which(path)
            if found:
                return found
    raise BrowserUnavailable('Chrome, Edge veya Brave bulunamadı. Desteklenen bir tarayıcı kurun.')


@contextmanager
def file_lock(path, timeout=3.0):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o777 if os.name == 'nt' else 0o700)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        start = time.monotonic()
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() - start >= timeout:
                    raise BrowserBusy('Başka bir tarayıcı işlemi sürüyor. Biraz sonra yeniden deneyin.') from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def user_data_dir(choice):
    if choice not in ('chrome', 'edge', 'brave'):
        raise BrowserUnavailable('Chrome, Edge veya Brave seçin.')
    if os.name == 'nt':
        root = Path(os.environ['LOCALAPPDATA'])
        return root / {'chrome': 'Google/Chrome/User Data', 'edge': 'Microsoft/Edge/User Data',
                       'brave': 'BraveSoftware/Brave-Browser/User Data'}[choice]
    if sys.platform == 'darwin':
        root = Path.home() / 'Library/Application Support'
        return root / {'chrome': 'Google/Chrome', 'edge': 'Microsoft Edge',
                       'brave': 'BraveSoftware/Brave-Browser'}[choice]
    root = Path(os.getenv('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    return root / {'chrome': 'google-chrome', 'edge': 'microsoft-edge',
                   'brave': 'BraveSoftware/Brave-Browser'}[choice]


def discover_endpoint(choice):
    try:
        path = user_data_dir(choice) / 'DevToolsActivePort'
        content = path.read_text(encoding='utf-8')
        lines = content.strip().splitlines()
        if len(lines) != 2 or not lines[0].isdigit():
            return None
        port = int(lines[0])
        if not 0 < port < 65536 or not re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+', lines[1]):
            return None
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(('127.0.0.1', port)) != 0:
                with suppress(OSError):
                    path.unlink()
                return None
        return f'http://127.0.0.1:{port}', f'ws://127.0.0.1:{port}{lines[1]}'
    except (OSError, ValueError):
        return None




async def watch_browser_reconnect():
    """Background task: when a saved browser restarts, automatically reconnect (no user action needed).

    Polls DevToolsActivePort every 5 seconds.  When the file reappears after being absent
    (browser was closed then reopened), it calls BrowserManager.resume() to re-establish
    the CDP connection — exactly what the old "Devam et" button did.
    """
    log = logging.getLogger(__name__)
    root = browser_root()
    # Track which browsers were seen as connected on the last iteration.
    previous_endpoints: dict[str, str | None] = {}
    while not (root / '.stopping').exists():
        await asyncio.sleep(5)
        try:
            for directory in root.iterdir():
                if not directory.is_dir() or directory.is_symlink() or not re.fullmatch(r'[a-f0-9]{64}', directory.name):
                    continue
                config = BrowserManager._read(directory / 'config.json')
                if (config.get('mode') != 'existing' or not config.get('enabled')
                        or not config.get('autoconnect', True)):
                    continue
                choice = config.get('browser')
                if choice not in ('chrome', 'edge', 'brave'):
                    continue
                key = directory.name
                endpoint = discover_endpoint(choice)
                previous = previous_endpoints.get(key)
                current = endpoint[1] if endpoint else None
                previous_endpoints[key] = current
                if current and current != previous:
                    # Browser just came back — attempt silent resume.
                    log.info('Browser %s restarted; auto-reconnecting…', choice)
                    try:
                        mgr = BrowserManager(directory)
                        await mgr.resume()
                        log.info('Auto-reconnect for %s succeeded.', choice)
                    except Exception as exc:
                        log.warning('Auto-reconnect for %s failed: %s', choice, exc)
        except Exception as exc:
            log.debug('watch_browser_reconnect error: %s', exc)


async def restore_saved_browsers():
    """At Hub startup, restore selected ordinary browsers without requesting CDP."""
    if os.name != 'nt' or (browser_root() / '.stopping').exists():
        return
    root = browser_root()
    if not root.exists():
        return
    selected = set()
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink() or not re.fullmatch(r'[a-f0-9]{64}', directory.name):
            continue
        config = BrowserManager._read(directory / 'config.json')
        if config.get('mode') == 'existing' and config.get('enabled') and config.get('browser') in ('chrome', 'edge', 'brave'):
            selected.add(config['browser'])
    for choice in selected:
        running = running_browser_choices()
        if running is None or choice in running:
            continue
        try:
            binary = find_binary(choice)
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE
            subprocess.Popen([binary, '--no-first-run', '--no-default-browser-check'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, startupinfo=startup)
        except (BrowserUnavailable, OSError):
            logging.getLogger(__name__).warning('Could not restore saved %s browser', choice)
    # Kick off the reconnect watcher regardless of whether any browser was launched.
    # Keep the watcher owned by the lifespan's restore_task so shutdown cancels it.
    await watch_browser_reconnect()


_connections = {}


async def get_direct_connection(endpoint):
    # Only the API-owned bridge calls this; workers never connect to Chrome.
    # On shutdown the OS closes sockets; never issue Browser.close.
    key = (asyncio.get_running_loop(), endpoint)
    existing = _connections.get(key)
    if existing is not None and not existing.closed:
        return existing
    if existing is not None:
        await existing.close()
    try:
        async with asyncio.timeout(60):
            asyncio.create_task(asyncio.to_thread(auto_approve_browser_dialogs))
            connection = await CDPSession.connect(endpoint, timeout=60)
            # Current page helpers poll state and do not consume unsolicited events.
            connection.discard_events = True
            await connection.send('Browser.getVersion', timeout=60)
    except BaseException as exc:
        if 'connection' in locals():
            await connection.close()
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        logging.getLogger(__name__).warning('Browser connection failed: %s', type(exc).__name__)
        if isinstance(exc, TimeoutError):
            message = 'Tarayıcı bağlantısı 60 saniyede tamamlanmadı. Tarayıcıdaki İzin ver onayı bekleniyor olabilir; bağlantı henüz kurulmadı.'
        elif isinstance(exc, aiohttp.WSServerHandshakeError):
            message = f'Tarayıcı bağlantı isteğini reddetti (HTTP {exc.status}). Uzaktan hata ayıklama ayarını ve tarayıcıdaki izin isteğini kontrol edin.'
        elif isinstance(exc, aiohttp.ClientConnectorError):
            message = 'Tarayıcının bağlantı adresine ulaşılamadı. Tarayıcı kapalı veya uzaktan hata ayıklama kapatılmış olabilir.'
        else:
            message = f'Tarayıcı bağlantısı tamamlanmadan kesildi ({type(exc).__name__}). Bağlantı hazır değil; tarayıcıdaki izin isteğini kontrol edin.'
        raise BrowserUnavailable(message) from exc
    _connections[key] = connection
    return connection


async def get_connection(endpoint, *, authorize=False):
    from .bridge import SharedConnection
    connection = SharedConnection(endpoint)
    if authorize:
        await connection.rpc('connect')
    elif not (await connection.rpc('status'))['connected']:
        raise BrowserUnavailable('Tarayıcı bağlantısı kapalı. Mevcut tarayıcınıza bağlanmanız gerekiyor.')
    return connection


class BrowserManager:
    def __init__(self, user_id):
        if not user_id:
            raise BrowserUnavailable('An authenticated Orion user is required')
        if isinstance(user_id, Path):
            self.directory = user_id
        else:
            self.directory = browser_root() / hashlib.sha256(str(user_id).encode()).hexdigest()
        self.config = self.directory / 'config.json'
        self.state = self.directory / 'tabs.json'

    @staticmethod
    def _read(path):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write(path, value):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value), encoding='utf-8')
        temporary.replace(path)

    def browser_choice(self):
        config = self._read(self.config)
        return config.get('browser', 'chrome') if config.get('mode') == 'existing' else 'chrome'

    async def endpoint(self):
        # The opt-in endpoint has no /json/version HTTP API. Read only the
        # browser's published socket coordinates, never its cookies/passwords.
        return discover_endpoint(self.browser_choice())

    async def status(self, client_browser=None):
        config = self._read(self.config)
        saved = config.get('mode') == 'existing' and bool(config.get('enabled'))
        autoconnect = saved and bool(config.get('autoconnect', True))
        active_choice = (config.get('browser') if saved else None) or client_browser or self.browser_choice()
        endpoint = discover_endpoint(active_choice)
        if not saved and not endpoint and client_browser and client_browser != active_choice:
            alt_endpoint = discover_endpoint(client_browser)
            if alt_endpoint:
                endpoint = alt_endpoint
                active_choice = client_browser
        connected = False
        if endpoint:
            from .bridge import SharedConnection
            with suppress(Exception):
                connected = (await SharedConnection(endpoint[1]).rpc('status'))['connected']
        return {'saved': saved, 'autoconnect': autoconnect, 'running': connected, 'available': bool(endpoint),
                'browser': config.get('browser') or active_choice,
                'suggested_browser': client_browser or suggested_browser(),
                'manual': saved and bool(config.get('manual'))}

    async def disconnect(self):
        with file_lock(self.directory / 'operation.lock'):
            config = self._read(self.config)
            choice = config.get('browser') or self.browser_choice()
            config.update({'enabled': False, 'autoconnect': False, 'mode': 'none'})
            self._write(self.config, config)
            endpoint = discover_endpoint(choice)
            if endpoint:
                from .bridge import SharedConnection
                with suppress(Exception):
                    await SharedConnection(endpoint[1]).rpc('disconnect')

    async def ensure_launched(self, *, manual=False, choice=None, url=None, background=False):
        # Compatibility name: this method NEVER launches a browser.
        require_desktop()
        if (browser_root() / '.stopping').exists():
            raise BrowserUnavailable('Orion Hub kapanıyor. Hub’ı yeniden başlatın.')
        config = self._read(self.config)
        if not manual and (config.get('mode') != 'existing' or not config.get('enabled')):
            raise BrowserUnavailable('Mevcut tarayıcınıza bağlanmanız gerekiyor.')
        selected = choice or self.browser_choice()
        if manual:
            # Retain the user's actual selection even when Chrome/Brave denies
            # the request. Refresh/resume must not silently switch to Chrome.
            if config.get('mode') != 'existing' or config.get('browser') != selected:
                config = {'mode': 'existing', 'browser': selected, 'enabled': False, 'manual': False}
            self._write(self.config, config)
        endpoint = discover_endpoint(selected)
        if not endpoint:
            scheme = {'chrome': 'chrome', 'brave': 'brave', 'edge': 'edge'}.get(selected, 'chrome')
            raise BrowserUnavailable(f'Tarayıcınızı açın ve {scheme}://inspect/#remote-debugging sayfasında uzaktan hata ayıklamayı etkinleştirin. Bu seçenek yoksa güncel Chrome kullanın.')
        if manual:
            # Save only after the browser has granted the actual connection.
            asyncio.create_task(asyncio.to_thread(auto_approve_browser_dialogs, selected))
            await get_connection(endpoint[1], authorize=True)
            self._write(self.config, {'mode': 'existing', 'browser': selected,
                                      'enabled': True, 'manual': False, 'autoconnect': True})
        return endpoint

    async def open_manual(self, choice=None, url=None, tag=None):
        with file_lock(self.directory / 'operation.lock'):
            selected = choice or suggested_browser() or self.browser_choice()
            setup = choice is not None and url is None
            if setup:
                require_desktop()
                endpoint = discover_endpoint(selected)
                if not endpoint:
                    # Remote debugging port is not open yet: open inspect, toggle box, close inspect window
                    await asyncio.to_thread(enable_remote_debugging, selected)
                    endpoint = discover_endpoint(selected)
                    if not endpoint:
                        scheme = {'chrome': 'chrome', 'brave': 'brave', 'edge': 'edge'}.get(selected, 'chrome')
                        raise BrowserUnavailable(f'Tarayıcınızı açın ve {scheme}://inspect/#remote-debugging sayfasında uzaktan hata ayıklamayı etkinleştirin.')

                # Connect to CDP (this triggers the "Uzaktan hata ayıklamaya izin verilsin mi?" dialog in browser)
                asyncio.create_task(asyncio.to_thread(auto_approve_browser_dialogs, selected))
                connection = await get_connection(endpoint[1], authorize=True)
                await connection.send('Browser.getVersion')
                self._write(self.config, {'mode': 'existing', 'browser': selected,
                                          'enabled': True, 'manual': False, 'autoconnect': True})
                return

            if url is not None:
                _, endpoint = await self.ensure_launched()
                from .page import web_url
                connection = await get_connection(endpoint)
                # A visible page is opened only by this explicit user action.
                await connection.send('Target.createTarget', {'url': web_url(url)})
                self.mark_manual()

    async def resume(self):
        with file_lock(self.directory / 'operation.lock'):
            config = self._read(self.config)
            if config.get('mode') != 'existing':
                raise BrowserUnavailable('Önce mevcut tarayıcınıza bağlanın.')
            require_desktop()
            if (browser_root() / '.stopping').exists():
                raise BrowserUnavailable('Orion Hub kapanıyor.')
            endpoint = await self.endpoint()
            if not endpoint:
                raise BrowserUnavailable('Tarayıcı bulunamadı. Seçtiğiniz tarayıcıyı açıp uzaktan hata ayıklama ayarını kontrol edin.')
            # This explicit recovery action reconnects automatically if needed.
            asyncio.create_task(asyncio.to_thread(auto_approve_browser_dialogs, config.get('browser')))
            connection = await get_connection(endpoint[1], authorize=True)
            from .cdp import CDPConnectionLost, CDPTimeout, CDPError
            try:
                await connection.send('Browser.getVersion')
            except (CDPConnectionLost, CDPTimeout, CDPError) as exc:
                raise BrowserUnavailable('Tarayıcı yanıt vermedi. Bağlantı hazır değil; yeniden bağlanın.') from exc
            config['manual'] = False
            config['enabled'] = True
            self._write(self.config, config)

    def mark_manual(self):
        config = self._read(self.config)
        config['manual'] = True
        self._write(self.config, config)

    async def list_tabs(self):
        """List pages in the browser the user connected, without creating a tab."""
        with file_lock(self.directory / 'operation.lock'):
            if self._read(self.config).get('manual') and self._read(self.config).get('mode') == 'existing':
                raise BrowserUnavailable('Doğrulamayı tamamlayıp tarayıcıya bağlan düğmesine basın.')
            _, endpoint = await self.ensure_launched()
            connection = await get_connection(endpoint)
            targets = (await connection.send('Target.getTargets'))['targetInfos']
            return [{'id': item['targetId'], 'title': item.get('title', ''), 'url': item.get('url', '')}
                    for item in targets if item.get('type') == 'page']

    @asynccontextmanager
    async def connected(self):
        """Share one locked browser connection across a read-only multi-tab operation."""
        with file_lock(self.directory / 'operation.lock'):
            if self._read(self.config).get('manual') and self._read(self.config).get('mode') == 'existing':
                raise BrowserUnavailable('Doğrulamayı tamamlayıp tarayıcıya bağlan düğmesine basın.')
            _, endpoint = await self.ensure_launched()
            lease = browser_root() / ('shared-' + hashlib.sha256(endpoint.encode()).hexdigest() + '.lock')
            with file_lock(lease):
                yield await get_connection(endpoint)

    @asynccontextmanager
    async def tab(self, tag, *, background=False, inactive=False, target_id=None, create=True):
        if target_id and background:
            raise ValueError('An existing tab cannot be a temporary background target')
        with file_lock(self.directory / 'operation.lock'):
            if self._read(self.config).get('manual') and self._read(self.config).get('mode') == 'existing':
                raise BrowserUnavailable('Doğrulamayı tamamlayıp tarayıcıya bağlan düğmesine basın.')
            _, endpoint = await self.ensure_launched()
            # Serialize all Orion users sharing this actual desktop browser.
            lease = browser_root() / ('shared-' + hashlib.sha256(endpoint.encode()).hexdigest() + '.lock')
            with file_lock(lease):
                connection = await get_connection(endpoint)
                target = None
                try:
                    if target_id:
                        targets = (await connection.send('Target.getTargets'))['targetInfos']
                        if not any(t.get('targetId') == target_id and t.get('type') == 'page'
                                   for t in targets):
                            raise BrowserTabMissing('Seçilen sekme artık açık değil. Sekmeleri yeniden listeleyin.')
                        target = target_id
                    elif background:
                        # No window, no tab-strip entry, same default browser context.
                        # Never silently fall back to a visible target.
                        from .cdp import CDPError
                        try:
                            target = (await connection.send('Target.createTarget',
                                {'url': 'about:blank', 'hidden': True, 'background': True}))['targetId']
                        except CDPError as exc:
                            raise BrowserUnavailable('Bu tarayıcı görünmez sayfa oluşturmayı desteklemiyor. Güncel Chrome kullanın.') from exc
                    else:
                        saved = self._read(self.state)
                        tabs = saved.get('tabs', {}) if saved.get('endpoint') == endpoint else {}
                        if not isinstance(tabs, dict):
                            tabs = {}
                        targets = (await connection.send('Target.getTargets'))['targetInfos']
                        target = tabs.get(tag)
                        if not any(t['targetId'] == target and t['type'] == 'page' for t in targets):
                            if not create:
                                raise BrowserTabMissing('Orion medya sekmesi açık değil. Mevcut sekmeleri listeleyip tab_id seçin.')
                            options = {'url': 'about:blank'}
                            if inactive:
                                # Real, visible-in-tab-strip media tab, without
                                # switching away from Orion's currently active tab.
                                options.update(background=True, focus=False)
                            target = (await connection.send('Target.createTarget', options))['targetId']
                            tabs[tag] = target
                            self._write(self.state, {'endpoint': endpoint, 'tabs': tabs})
                    session = (await connection.send('Target.attachToTarget', {'targetId': target, 'flatten': True}))['sessionId']
                    from .page import BrowserPage
                    page = BrowserPage(connection, session, target, self)
                    page.hidden = background
                    try:
                        if background:
                            # Hidden targets start without a usable viewport.
                            await page.send('Emulation.setDeviceMetricsOverride', {
                                'width': 1280, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False})
                        yield page
                    finally:
                        if not connection.closed:
                            with suppress(Exception):
                                await connection.send('Target.detachFromTarget', {'sessionId': session})
                finally:
                    if background and target and not connection.closed:
                        with suppress(Exception):
                            await connection.send('Target.closeTarget', {'targetId': target})
