"""Private-network inbound rules so Windows does not prompt for every bundled python.exe.

Rules are port based (not program based) so Hub/Router updates, which change the
python.exe path, never trigger a new prompt. They allow only the Private profile
and the local subnet. Adding rules needs administrator rights: the installer asks
once through UAC and keeps installing if the user declines (Orion still works on
this PC; phones and other devices need the permission).
"""
import base64
import ctypes
import os
from pathlib import Path
import subprocess
import sys

TCP_RULE = 'Orion private network (TCP)'
UDP_RULE = 'Orion private network discovery (UDP)'
DEFAULT_PORTS = {'ORION_MOBILE_PORT': 8443, 'ORION_PUBLIC_PORT': 8911, 'ORION_ROUTER_TLS_PORT': 9443}
MDNS_PORT = 5353
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _read_env(path):
    values = {}
    try:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() in DEFAULT_PORTS:
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
        try:
            port = int(values.get(key, default))
        except ValueError:
            port = default
        if 1024 <= port <= 65535:
            ports.add(port)
    return sorted(ports)


def _rule_command(ports):
    tcp = ','.join(map(str, ports))
    return (
        f"netsh advfirewall firewall delete rule name='{TCP_RULE}' | Out-Null; "
        f"netsh advfirewall firewall delete rule name='{UDP_RULE}' | Out-Null; "
        f"netsh advfirewall firewall add rule name='{TCP_RULE}' dir=in action=allow protocol=TCP "
        f"localport={tcp} profile=private remoteip=localsubnet enable=yes | Out-Null; "
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; "
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


def current_ports(run=subprocess.run):
    """Local TCP ports of the installed rule, or None when it is missing.

    Uses the NetSecurity cmdlets: unlike `netsh show rule` their output is not
    localized, and reading needs no administrator rights."""
    command = (f"$r = Get-NetFirewallRule -DisplayName '{TCP_RULE}' -ErrorAction SilentlyContinue; "
               "if (-not $r) { exit 3 }; ($r | Get-NetFirewallPortFilter).LocalPort")
    result = run([_powershell(), '-NoProfile', '-NonInteractive', '-Command', command],
                 capture_output=True, text=True, creationflags=NO_WINDOW)
    if result.returncode != 0:
        return None
    try:
        return sorted({int(p) for line in result.stdout.split() for p in line.split(',') if p})
    except ValueError:
        return None


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
    ports = tcp_ports(record)
    if not ports:
        return 'unchanged'
    try:
        if current_ports(run) == ports:
            return 'unchanged'
        code = _run_command(_rule_command(ports), run, elevate)
        if code == 1223 or code == 1:
            # 1223 is the standard "operation cancelled by the user" result.
            return 'declined' if code == 1223 else 'failed'
        return 'added' if code == 0 and current_ports(run) == ports else 'failed'
    except (OSError, subprocess.SubprocessError):
        return 'failed'


def remove_rules(*, run=subprocess.run, elevate=True):
    if sys.platform != 'win32' or current_ports(run) is None:
        return 'unchanged'
    try:
        code = _run_command(_remove_command(), run, elevate)
        return 'removed' if code == 0 and current_ports(run) is None else ('declined' if code == 1223 else 'failed')
    except (OSError, subprocess.SubprocessError):
        return 'failed'
