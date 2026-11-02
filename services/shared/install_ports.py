"""The ports an installation's services listen on, and which of them another program holds right now.

An update switches only when the new version can start: a port that another program holds (a second Orion
run from source, another database, ...) would make the candidate fail its health check after the switch.
The setup window (before a new installation) and the launcher (before a start) name that program instead of
failing deep inside a service.
"""
from pathlib import Path
import socket

HUB = {'POSTGRES_PORT': 5445, 'REDIS_PORT': 6379, 'HUB_PORT': 8909, 'ORION_LOCAL_PORT': 8910,
       'ORION_MOBILE_PORT': 8443, 'ORION_FLUTTER_PORT': 8090}
LIGHT_HUB = ('ORION_LOCAL_PORT', 'ORION_FLUTTER_PORT')  # A light Hub has no database, Redis or phone listener.
ROUTER = {'ORION_ROUTER_TLS_PORT': 9443, 'ORION_ROUTER_LOCAL_HTTP_PORT': 20128, 'POSTGRES_PORT': 5433}


def _read(path):
    values = {}
    try:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            key, separator, value = line.partition('=')
            if separator and not key.lstrip().startswith('#'):
                values[key.strip()] = value.strip().strip('"\'')
    except OSError:
        pass
    return values


def defaults(mode):
    """Ports of a new installation of this type (install_plan), before anything is configured."""
    from .install_plan import components
    result = set()
    for row in components(mode):
        if row.kind == 'hub':
            light = row.settings.get('ORION_HUB_MODE') == 'relay'
            result.update([HUB[key] for key in LIGHT_HUB] if light else HUB.values())
        elif row.kind == 'router':
            result.update(ROUTER.values())
    return sorted(result)


def ports(record):
    result = set()
    for item in record['components']:
        if item['kind'] == 'hub':
            values = {**_read(Path(item['code_path']) / 'services/.env.global'),
                      **_read(Path(item['data_path']) / 'services/.env.global.local')}
            light = values.get('ORION_HUB_MODE', '').lower() == 'relay'
            wanted = {key: HUB[key] for key in LIGHT_HUB} if light else HUB
        elif item['kind'] == 'router':
            values, wanted = _read(Path(item['data_path']) / '.env'), ROUTER
        else:
            continue
        for key, default in wanted.items():
            try:
                port = int(values.get(key) or default)
            except ValueError:
                port = default
            if 1 <= port <= 65535:
                result.add(port)
    return sorted(result)


def held(port):
    """Is something listening on `port` (loopback or every address)?"""
    for host in ('127.0.0.1', '0.0.0.0'):
        with socket.socket() as probe:
            try:
                probe.bind((host, port))
            except OSError:
                return True
    return False


def in_use(record):
    """Ports of `record` that some process holds. Call it only while the installation itself is stopped."""
    return [port for port in ports(record) if held(port)]


def holder(port):
    """The program listening on `port`: {'name', 'orion'} ('orion': another Orion, such as one run from source or
    a second installation), or None when Windows does not say."""
    try:
        import psutil
        for connection in psutil.net_connections(kind='tcp'):
            if connection.status == psutil.CONN_LISTEN and connection.laddr and connection.laddr.port == port                     and connection.pid:
                process = psutil.Process(connection.pid)
                try:
                    where = ' '.join([process.exe(), *process.cmdline()])
                except psutil.Error:
                    where = ''
                return {'name': process.name(), 'orion': 'orion' in where.lower()}
    except Exception:
        pass  # Only the name is missing: the port is still reported.
    return None


def holders(busy):
    """`busy` ports grouped by what to close: [{'ports', 'name' (None: unknown), 'names', 'orion'}]. Another
    Orion is one group, whatever its parts are called (its database, Redis, Python)."""
    groups = {}
    for port in busy:
        found = holder(port) or {'name': None, 'orion': False}
        group = groups.setdefault(True if found['orion'] else found['name'], {**found, 'ports': [], 'names': []})
        group['ports'].append(port)
        if found['name'] and found['name'] not in group['names']:
            group['names'].append(found['name'])
    return list(groups.values())
