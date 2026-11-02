"""Private-network inbound rules so Windows does not prompt for every bundled python.exe.

Rules are port based (not program based) so Hub/Router updates, which change the
python.exe path, never trigger a new prompt (with the port rule in place Windows
does not ask "allow Python?" when a service starts listening either). They allow
only the Private profile and the local subnet. Adding rules needs administrator
rights: the installer asks once through UAC and keeps installing if the user
declines (Orion still works on this PC; phones and other devices need the permission).

A full Hub opens its phone port and announces itself (mDNS). A light Hub opens no
TCP port but listens for those announcements ("Cihazları ara"): it gets the
discovery rule alone.
"""
import base64
import ctypes
import os
from pathlib import Path
import subprocess
import sys

TCP_RULE = 'Orion private network (TCP)'
UDP_RULE = 'Orion private network discovery (UDP)'
DEFAULT_PORTS = {'ORION_MOBILE_PORT': 8443, 'ORION_ROUTER_TLS_PORT': 9443}
MDNS_PORT = 5353
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _read_env(path):
    values = {}
    try:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            key, separator, value = line.partition('=')
            if separator and (key.strip() in DEFAULT_PORTS or key.strip() == 'ORION_HUB_MODE'):
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def tcp_ports(record):
    """Ports Hub/Router listen on for other devices, honoring installed overrides."""
    values = {}
    for item in record['components']:
        if item['kind'] == 'hub':
            values.update(_read_env(Path(item['data_path']) / 'services/.env.global.local'))
        elif item['kind'] == 'router':
            values.update(_read_env(Path(item['data_path']) / '.env'))
    ports = set()
    for key, default in DEFAULT_PORTS.items():
        if key == 'ORION_ROUTER_TLS_PORT' and not any(c['kind'] == 'router' for c in record['components']):
            continue
        if key != 'ORION_ROUTER_TLS_PORT' and not any(c['kind'] == 'hub' for c in record['components']):
            continue
        # A light Hub (it only relays this PC's window to another Hub) listens on loopback alone.
        if key != 'ORION_ROUTER_TLS_PORT' and values.get('ORION_HUB_MODE', '').strip().lower() == 'relay':
            continue
        try:
            port = int(values.get(key, default))
        except ValueError:
            port = default
        if 1024 <= port <= 65535:
            ports.add(port)
    return sorted(ports)


def wanted(record):
    """The rules `record` needs, in current_rules' form: TCP for its network ports, discovery for any Hub
    (a light one too) or network port."""
    ports = tcp_ports(record)
    return {'tcp': ports or None, 'udp': bool(ports) or any(c['kind'] == 'hub' for c in record['components'])}


def _rule_command(ports):
    tcp = ','.join(map(str, ports))
    command = (f"netsh advfirewall firewall delete rule name='{TCP_RULE}' | Out-Null; "
               f"netsh advfirewall firewall delete rule name='{UDP_RULE}' | Out-Null; ")
    if ports:
        command += (f"netsh advfirewall firewall add rule name='{TCP_RULE}' dir=in action=allow protocol=TCP "
                    f"localport={tcp} profile=private remoteip=localsubnet enable=yes | Out-Null; "
                    "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; ")
    return command + (
        f"netsh advfirewall firewall add rule name='{UDP_RULE}' dir=in action=allow protocol=UDP "
        f"localport={MDNS_PORT} profile=private remoteip=localsubnet enable=yes | Out-Null; "
        "exit $LASTEXITCODE")


def _remove_command():
    return (f"netsh advfirewall firewall delete rule name='{TCP_RULE}' | Out-Null; "
            f"netsh advfirewall firewall delete rule name='{UDP_RULE}' | Out-Null; exit 0")


def _powershell():
    return str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe')


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


NO_RULES = {'tcp': None, 'udp': False}


def current_rules(run=subprocess.run):
    """Orion's installed rules: {'tcp': local TCP ports or None when missing, 'udp': discovery rule present}.

    Uses the NetSecurity cmdlets: unlike `netsh show rule` their output is not
    localized, and reading needs no administrator rights."""
    command = (f"$r = Get-NetFirewallRule -DisplayName '{TCP_RULE}' -ErrorAction SilentlyContinue; "
               f"if (Get-NetFirewallRule -DisplayName '{UDP_RULE}' -ErrorAction SilentlyContinue) {{ 'udp' }}; "
               "if ($r) { 'tcp ' + ((($r | Get-NetFirewallPortFilter).LocalPort) -join ',') }")
    result = run([_powershell(), '-NoProfile', '-NonInteractive', '-Command', command],
                 capture_output=True, text=True, creationflags=NO_WINDOW)
    rules = dict(NO_RULES)
    if result.returncode != 0:
        return rules
    for line in result.stdout.splitlines():
        word, _, rest = line.strip().partition(' ')
        if word == 'udp':
            rules['udp'] = True
        elif word == 'tcp':
            try:
                rules['tcp'] = sorted({int(p) for p in rest.split(',') if p.strip()}) or None
            except ValueError:
                rules['tcp'] = None
    return rules


def _run_command(command, run, elevate):
    if is_admin():
        return run([_powershell(), '-NoProfile', '-NonInteractive', '-Command', command],
                   capture_output=True, creationflags=NO_WINDOW).returncode
    if not elevate:
        return 1223
    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    # The command is passed encoded in the elevation request: no script file that
    # another process could replace before it runs with administrator rights.
    outer = ("try { $p = Start-Process -FilePath '" + _powershell() + "' -Verb RunAs -Wait -PassThru "
             "-WindowStyle Hidden -ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','" + encoded + "'; "
             "exit $p.ExitCode } catch { exit 1223 }")
    return run([_powershell(), '-NoProfile', '-NonInteractive', '-Command', outer],
               capture_output=True, creationflags=NO_WINDOW).returncode


def ensure_rules(record, *, run=subprocess.run, elevate=True):
    """Return 'unchanged', 'added', 'declined' or 'failed'. Never raises."""
    if sys.platform != 'win32':
        return 'unsupported'
    want = wanted(record)
    if want == NO_RULES:
        return 'unchanged'
    try:
        if current_rules(run) == want:
            return 'unchanged'
        code = _run_command(_rule_command(want['tcp'] or []), run, elevate)
        if code == 1223 or code == 1:
            # 1223 is the standard "operation cancelled by the user" result.
            return 'declined' if code == 1223 else 'failed'
        return 'added' if code == 0 and current_rules(run) == want else 'failed'
    except (OSError, subprocess.SubprocessError):
        return 'failed'


def remove_rules(*, run=subprocess.run, elevate=True):
    if sys.platform != 'win32' or current_rules(run) == NO_RULES:
        return 'unchanged'
    try:
        code = _run_command(_remove_command(), run, elevate)
        return 'removed' if code == 0 and current_rules(run) == NO_RULES else ('declined' if code == 1223 else 'failed')
    except (OSError, subprocess.SubprocessError):
        return 'failed'
