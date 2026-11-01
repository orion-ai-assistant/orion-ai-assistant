"""The setup window the one-line install opens: where, which type, what will be installed, then install.

    get-orion.ps1 → this bundle's python -m scripts.release.setup_window → a page in an app window

The page is served only on this PC's loopback, under a random one-time path; the work itself is
install_new.py with --mode (services/shared/install_plan.py decides what each type installs). Ends when
the window says it is done or after an hour without the page.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from services.shared import install_plan  # noqa: E402

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def default_root():
    return Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'Orion'


def registry_path():
    return default_root() / 'install.json'


def existing():
    """The installation already on this PC, if any (one per Windows user)."""
    from services.shared.installation_registry import Registry
    try:
        return Registry(registry_path()).read()
    except Exception:  # A damaged registry: the installer's own refusal explains it at install time.
        return None


def hub_mode(code_path):
    from services.shared.native_services import hub_mode as mode
    return mode(code_path)


def elevated():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


class Setup:
    def __init__(self, bundle):
        self.bundle = Path(bundle)
        self.token = secrets.token_urlsafe(24)
        self.lines = []
        self.process = None
        self.result = None
        self.log_path = None
        self.finished = threading.Event()
        self.seen = time.monotonic()
        self.opened = False

    def state(self):
        record = existing()
        root = (record or {}).get('root') or str(default_root())
        return {'root': root, 'fixed_root': record is not None,
                'existing': sorted(c['kind'] for c in (record or {}).get('components', [])),
                'plans': {mode: install_plan.plan(mode, record, hub_mode=hub_mode) for mode in install_plan.MODES},
                'running': self.process is not None and self.result is None}

    def browse(self, current=None):
        # Windows' own "Select folder" dialog, in front of this window (services/shared/pick_folder.ps1).
        start = Path(current).parent if current else default_root().parent
        result = subprocess.run(['powershell', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(ROOT / 'services/shared/pick_folder.ps1')], capture_output=True, timeout=3600,
                                env=dict(os.environ, ORION_PICK_START=str(start)), creationflags=NO_WINDOW)
        chosen = result.stdout.decode('utf-8', 'replace').strip()
        if not chosen:
            return None
        return chosen if Path(chosen).name.lower() == 'orion' else str(Path(chosen) / 'Orion')

    def uninstall(self):
        """Orion's own removal dialog (the one Settings > Apps opens); the page reads the state again after."""
        if self.process is not None and self.result is None:
            raise ValueError('Kurulum sürerken kaldırılamaz.')
        from services.shared.uninstall import SCRIPT, _powershell
        script = registry_path().parent / 'bootstrap' / SCRIPT
        if existing() is None or not script.is_file():
            raise ValueError('Kaldırılacak bir Orion kurulumu bulunamadı.')
        subprocess.Popen([_powershell(), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                          '-File', str(script), '-Registry', str(registry_path())], creationflags=NO_WINDOW)

    def install(self, choice):
        mode = choice.get('mode')
        if self.process is not None:
            raise ValueError('Kurulum zaten sürüyor.')
        rows = install_plan.plan(mode, existing(), hub_mode=hub_mode)
        if any(row['action'] == 'conflict' for row in rows):
            raise ValueError('Bu bilgisayarda diğer türde bir Orion kurulu. Önce onu kaldır.')
        if all(row['action'] == 'present' for row in rows):
            raise ValueError('Bu türün bütün parçaları zaten kurulu.')
        record = existing()
        root = record['root'] if record else str(choice.get('root') or default_root())
        command = [sys.executable, '-B', '-m', 'scripts.release.install_new',
                   '--trusted-root', str(self.bundle / 'trusted-root.json'),
                   '--release-source', str(self.bundle / 'release-source.json'),
                   '--root', root, '--registry', str(registry_path()), '--mode', mode]
        if record:
            command.append('--add-missing')
        if not choice.get('start_with_windows', True):
            command.append('--no-start-with-windows')
        if not choice.get('automatic_updates', True):
            command.append('--no-auto-update')
        # From an administrator window Orion would run elevated; the window opens it through Explorer instead.
        if not record and not elevated():
            command.append('--launch')
        self.log_path = registry_path().parent / 'logs' / time.strftime('kurulum-%Y%m%d-%H%M%S.log')
        self._line('Kurulum başladı: ' + ', '.join(row['title'] for row in rows if row['action'] == 'install'))
        self.process = subprocess.Popen(command, cwd=self.bundle, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
        threading.Thread(target=self._follow, daemon=True).start()

    def _follow(self):
        status = None
        for line in self.process.stdout:
            line = line.rstrip()
            if not line:
                continue
            try:
                value = json.loads(line)
                if isinstance(value, dict) and 'status' in value:
                    status = value
                    continue
            except ValueError:
                pass
            self._line(line)
        code = self.process.wait()
        if code == 0 and elevated():
            shortcut = Path(os.path.expandvars(r'%USERPROFILE%\Desktop\Orion.lnk'))
            if shortcut.is_file():
                subprocess.Popen(['explorer.exe', str(shortcut)])
        if code != 0:
            self._line(f'Kurulum {code} koduyla bitti. Bu kayıt: {self.log_path}')
        # The window stays until the user closes it, so a failure can be read (and copied).
        self.result = {'ok': code == 0, 'code': code, 'status': status, 'log': str(self.log_path)}

    def _line(self, line):
        self.lines.append(line[:400])
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open('a', encoding='utf-8') as log:
                log.write(line + '\n')
        except OSError:
            pass  # The page still shows it.


PAGE = r'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>Orion kurulumu</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#2a2f3a;--text:#e6e8eb;--muted:#9aa3ae;--green:#10a37f;--red:#ef6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
main{max-width:620px;margin:0 auto;padding:32px 20px}h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}
p{color:var(--muted);margin:4px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:8px 0}
label.choice{display:block;cursor:pointer}label.choice input{margin-right:10px}.choice b{font-size:16px}
.row{display:flex;gap:8px;align-items:center}input[type=text]{flex:1;background:#0b0d11;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:10px}
button{background:var(--green);color:#fff;border:0;border-radius:10px;padding:12px 20px;font-size:15px;font-weight:600;cursor:pointer}
button.ghost{background:transparent;color:var(--green);border:1px solid var(--line)}button:disabled{opacity:.5;cursor:default}
ul{list-style:none;padding:0;margin:0}li{padding:6px 0;border-bottom:1px solid var(--line)}li:last-child{border:0}
.tag{float:right;color:var(--muted)}.install .tag{color:var(--green)}.conflict .tag{color:var(--red)}
#log{white-space:pre-wrap;font:12px Consolas,monospace;color:var(--muted);max-height:220px;overflow:auto}
.error{color:var(--red)}[hidden]{display:none}
</style></head><body><main>
<h1>Orion kurulumu</h1><p>Birkaç seçim yap, gerisini kurulum halleder.</p>
<section id="form">
<h2>1. Nereye kurulsun?</h2>
<div class="row"><input id="root" type="text"><button class="ghost" id="browse" type="button">Değiştir</button></div>
<p id="rootnote"></p>
<p id="removerow" hidden><button class="ghost" id="remove" type="button">Kurulu Orion’u kaldır…</button></p>
<h2>2. Bu bilgisayar ne olacak?</h2>
<div class="card"><label class="choice"><input type="radio" name="mode" value="merkez" checked><b>Orion merkezi</b>
<p>Orion bu bilgisayarda çalışır: hesaplar, sohbetler, yapay zekâ bağlantısı. Telefonlar ve diğer bilgisayarlar buna bağlanır.</p></label></div>
<div class="card"><label class="choice"><input type="radio" name="mode" value="baglan"><b>Başka bilgisayardaki Orion’a bağlan</b>
<p>Yalnızca Orion penceresi kurulur. Bu bilgisayar, Orion merkezi olan bilgisayarı telefon gibi kullanır.</p></label></div>
<h2>3. Kurulacaklar</h2><div class="card"><ul id="plan"></ul></div>
<div class="card"><label><input type="checkbox" id="startup" checked> Windows açılınca Orion’u başlat</label><br>
<label><input type="checkbox" id="updates" checked> Güncellemeleri otomatik al</label></div>
<p class="error" id="error"></p>
<p style="margin-top:16px"><button id="go">Kur</button></p>
</section>
<section id="progress" hidden><h2 id="phase">Kuruluyor…</h2><p id="elapsed"></p><div class="card"><div id="log"></div></div>
<p><button id="close" hidden>Kapat</button></p></section>
</main><script>
const base=location.pathname.replace(/\/$/,'');let state,started;
const call=(path,body)=>fetch(base+path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Orion-Setup':'1'},body:JSON.stringify(body)}:{}).then(async r=>{const v=await r.json();if(!r.ok)throw new Error(v.error||'Hata');return v});
const mode=()=>document.querySelector('input[name=mode]:checked').value;
const words={install:'kurulacak',present:'zaten kurulu, kullanılacak',conflict:'diğer türde kurulu'};
function draw(){const rows=state.plans[mode()];document.getElementById('plan').innerHTML=rows.map(r=>`<li class="${r.action}"><span class="tag">${words[r.action]}</span><b>${r.title}</b><p>${r.detail}</p></li>`).join('');
const conflict=rows.some(r=>r.action==='conflict'),done=rows.every(r=>r.action==='present');
document.getElementById('error').textContent=conflict?'Bu bilgisayarda diğer türde bir Orion kurulu. Önce yukarıdan onu kaldır.':done?'Bu türün bütün parçaları zaten kurulu.':'';
document.getElementById('go').disabled=conflict||done}
async function load(){state=await call('/state');const root=document.getElementById('root');root.value=state.root;root.disabled=state.fixed_root;document.getElementById('browse').hidden=state.fixed_root;
document.getElementById('rootnote').textContent=state.fixed_root?'Bu bilgisayarda Orion zaten kurulu; eksik parçalar aynı yere eklenir.':'';
document.getElementById('removerow').hidden=!state.fixed_root;
if(state.existing.includes('router')&&!state.existing.includes('hub'))document.querySelector('input[value=merkez]').checked=true;draw()}
document.querySelectorAll('input[name=mode]').forEach(e=>e.onchange=draw);
document.getElementById('browse').onclick=async()=>{const b=document.getElementById('browse');b.disabled=true;try{const v=await call('/browse',{root:document.getElementById('root').value});if(v.root)document.getElementById('root').value=v.root}catch(e){document.getElementById('error').textContent=e.message}finally{b.disabled=false}};
// The removal dialog is Orion's own window; this page follows what it leaves behind.
document.getElementById('remove').onclick=async()=>{try{await call('/uninstall',{});document.getElementById('rootnote').textContent='Kaldırma penceresi açıldı. Bitince bu sayfa kendini yeniler.';
const before=state.existing.join();let n=0;const t=setInterval(async()=>{await load();if(state.existing.join()!==before||++n>200)clearInterval(t)},3000)}catch(e){document.getElementById('error').textContent=e.message}};
document.getElementById('go').onclick=async()=>{try{await call('/install',{mode:mode(),root:document.getElementById('root').value,start_with_windows:document.getElementById('startup').checked,automatic_updates:document.getElementById('updates').checked});
document.getElementById('form').hidden=true;document.getElementById('progress').hidden=false;started=Date.now();poll()}catch(e){document.getElementById('error').textContent=e.message}};
async function poll(){const v=await call('/progress');document.getElementById('log').textContent=v.lines.join('\n');document.getElementById('elapsed').textContent=Math.round((Date.now()-started)/1000)+' sn';
if(!v.result)return setTimeout(poll,1000);
document.getElementById('phase').textContent=v.result.ok?'Orion kuruldu. Açılıyor…':'Kurulum tamamlanamadı';document.getElementById('phase').className=v.result.ok?'':'error';
document.getElementById('elapsed').textContent=v.result.ok?'':'Ayrıntılar aşağıda; aynısı şu dosyada: '+v.result.log;
const close=document.getElementById('close');close.hidden=false;close.onclick=()=>call('/done',{}).finally(()=>window.close());
if(v.result.ok)setTimeout(()=>close.onclick(),5000)}
setInterval(()=>fetch(base+'/alive').catch(()=>{}),20000);load().catch(e=>document.getElementById('error').textContent=e.message);
</script></body></html>'''


def handler(setup, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, kind='application/json'):
            data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _path(self):
            # Only this window's one-time address, on this PC's own name for itself (no DNS rebinding).
            if self.headers.get('Host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
                return None
            prefix = '/' + setup.token
            if self.path != prefix and not self.path.startswith(prefix + '/'):
                return None
            setup.seen = time.monotonic()
            setup.opened = True
            return self.path[len(prefix):] or '/'

        def do_GET(self):
            path = self._path()
            if path is None:
                return self._send(404, {'error': 'Bulunamadı'})
            if path == '/':
                return self._send(200, PAGE, 'text/html')
            if path == '/state':
                return self._send(200, setup.state())
            if path == '/progress':
                return self._send(200, {'lines': setup.lines[-200:], 'result': setup.result})
            if path == '/alive':
                return self._send(200, {})
            return self._send(404, {'error': 'Bulunamadı'})

        def do_POST(self):
            path = self._path()
            origin = self.headers.get('Origin')
            if path is None or self.headers.get('X-Orion-Setup') != '1' or (origin and origin != f'http://{self.headers["Host"]}'):
                return self._send(403, {'error': 'Geçersiz istek'})
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 65536)) or b'{}')
                if path == '/browse':
                    current = body.get('root') if isinstance(body, dict) else None
                    return self._send(200, {'root': setup.browse(current if isinstance(current, str) else None)})
                if path == '/uninstall':
                    setup.uninstall()
                    return self._send(200, {'ok': True})
                if path == '/install':
                    setup.install(body if isinstance(body, dict) else {})
                    return self._send(200, {'ok': True})
                if path == '/done':
                    setup.finished.set()
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return self._send(200, {'ok': True})
            except ValueError as error:
                return self._send(400, {'error': str(error)})
            return self._send(404, {'error': 'Bulunamadı'})
    return Handler


def open_window(url):
    from services.shared.flutter_launcher import open_window as app_window
    profile = Path(os.getenv('TEMP') or Path.home()) / ('orion-setup-' + secrets.token_hex(4))
    return app_window(url, profile=profile)


def serve(bundle, *, show=True):
    setup = Setup(bundle)
    server = ThreadingHTTPServer(('127.0.0.1', 0), None)
    port = server.server_address[1]
    server.RequestHandlerClass = handler(setup, port)
    url = f'http://127.0.0.1:{port}/{setup.token}/'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if show:
        open_window(url)
    else:
        print(url, flush=True)
    print('Orion kurulum penceresi acildi. Kurulumu o pencereden bitir.', flush=True)
    try:
        while not setup.finished.wait(5):
            # The page pings every 20 s while it is open. Its window closed (a minute without a ping) or never
            # opened (five minutes): this process ends too; an install already running finishes first, below.
            idle = time.monotonic() - setup.seen
            if (setup.process is None or setup.result is not None) and idle > (60 if setup.opened else 300):
                break
        if setup.process is not None and setup.result is None:
            setup.process.wait()
    finally:
        server.shutdown()
    return 0 if (setup.result or {}).get('ok') else (3 if setup.result is None else 1)


def remove_later(folder):
    """A throw-away bundle (adding to an existing install) goes once this process, which runs from it, has exited."""
    subprocess.Popen(f'cmd /c ping -n 4 127.0.0.1 >nul & rmdir /s /q "{Path(folder)}"', cwd=os.getenv('TEMP'),
                     creationflags=NO_WINDOW | getattr(subprocess, 'DETACHED_PROCESS', 0))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT)
    parser.add_argument('--no-window', action='store_true', help='Print the address instead of opening it')
    parser.add_argument('--remove-bundle', action='store_true', help='Delete --bundle at the end (a temporary copy)')
    args = parser.parse_args(argv)
    try:
        return serve(args.bundle, show=not args.no_window)
    finally:
        # Only ever a folder under TEMP (get-orion.ps1 makes it there).
        if args.remove_bundle and os.getenv('TEMP') and Path(os.environ['TEMP']).resolve() in args.bundle.resolve().parents:
            remove_later(args.bundle.resolve())


if __name__ == '__main__':
    raise SystemExit(main())
