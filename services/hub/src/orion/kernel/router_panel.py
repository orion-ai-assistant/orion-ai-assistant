"""Navigation metadata only; Hub API traffic always keeps pinned Router TLS."""
import asyncio
import ipaddress
from urllib.parse import urlsplit

from orion.kernel.router_bootstrap import local_router_identity
from orion.kernel.pinned_router import verify_router


def panel_url(value, *, local=False):
    try:
        parsed = urlsplit(value)
        if (parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != '/dashboard' or not parsed.hostname
                or not 1 <= (parsed.port or (443 if parsed.scheme == 'https' else 80)) <= 65535):
            return None
        loopback = parsed.hostname.rstrip('.').lower() == 'localhost'
        try:
            address = ipaddress.ip_address(parsed.hostname)
            address = getattr(address, 'ipv4_mapped', None) or address
            loopback = loopback or address.is_loopback
            if address.is_unspecified or address.is_multicast:
                return None
        except ValueError:
            pass
        if local:
            return value if parsed.scheme == 'http' and loopback else None
        return value if parsed.scheme == 'https' and not loopback else None
    except (TypeError, ValueError):
        return None


async def navigation(record, data, *, desktop=False):
    """Never advertise a Hub-local address to a different socket peer."""
    # Prefer the verified current connection (including VPN/DNS) to an
    # advertised LAN address that might be unreachable outside that network.
    network = panel_url(record['url'].rstrip('/') + '/dashboard') or panel_url(data.get('dashboard_url'))
    result = {'dashboard_url': network, 'dashboard_scope': 'network',
              'dashboard_open_in_app': bool(desktop and network)}
    local_url = panel_url(data.get('local_dashboard_url'), local=True)
    if not desktop or not local_url:
        return result
    try:
        local = await local_router_identity()
        if not local or local['id'] != record['id'] or local['fp'] != record['fp']:
            return result
        verified = await asyncio.wait_for(verify_router(local['url'], local['id'], local['fp']), timeout=2)
        if verified['id'] == record['id'] and verified['fp'] == record['fp']:
            return {'dashboard_url': local_url, 'dashboard_scope': 'local', 'dashboard_open_in_app': True}
    except Exception:
        # Missing files, stale registration or failed identity probe: use HTTPS.
        pass
    return result
