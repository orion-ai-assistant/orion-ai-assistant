"""Bounded LAN browsing for the web login screen; never proxies credentials."""
import asyncio
import os
import socket
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

from orion.api.discovery import SERVICE_TYPE, LAN_NETWORKS, device_id, lan_addresses
import ipaddress

router = APIRouter(prefix='/api/v1/discovery')


def local_identity():
    from orion.kernel.local_security import tls_identity
    identity = tls_identity()
    return {'id': identity['id'], 'name': identity['name'], 'fp': identity['fp']}


def service_record(info, api_path='/api/v1'):
    """Treat LAN advertisements as untrusted input, not arbitrary redirect URLs."""
    try:
        properties = info.decoded_properties
        identity = str(uuid.UUID(properties['id']))
        if properties.get('path') != api_path or properties.get('scheme') != 'https' or not 1 <= info.port <= 65535:
            return None
        addresses = [address for address in info.parsed_addresses(IPVersion.V4Only)
                     if any(ipaddress.ip_address(address) in net for net in LAN_NETWORKS)]
        if not addresses:
            return None
        record = {'id': identity, 'name': (properties.get('name') or 'Orion')[:240],
                  'url': f'https://{addresses[0]}:{info.port}/'}
        # This is an untrusted advertisement, never a persisted trust decision.
        fingerprint = properties.get('fp', '')
        if len(fingerprint) == 64 and all(c in '0123456789abcdef' for c in fingerprint):
            record['fp'] = fingerprint
        return record
    except (KeyError, TypeError, ValueError):
        return None


class HubDirectory:
    def __init__(self, service_type=SERVICE_TYPE, api_path='/api/v1'):
        self.service_type = service_type
        self.api_path = api_path
        self.lock = asyncio.Lock()
        self.cached = []
        self.expires = 0

    async def list(self):
        async with self.lock:
            if time.monotonic() < self.expires:
                return self.cached
            self.cached = await self.scan()
            self.expires = time.monotonic() + 5
            return self.cached

    async def scan(self):
        addresses = await asyncio.to_thread(lan_addresses)
        if not addresses or Path('/.dockerenv').exists() or os.getenv('ORION_MDNS', '1').lower() in {'0', 'false', 'off', 'no'}:
            return []
        names = set()
        def changed(zeroconf, service_type, name, state_change):
            if state_change == ServiceStateChange.Removed:
                names.discard(name)
            elif len(names) < 64:
                names.add(name)
        zc = None
        browser = None
        try:
            zc = AsyncZeroconf(interfaces=addresses, ip_version=IPVersion.V4Only)
            browser = AsyncServiceBrowser(zc.zeroconf, self.service_type, handlers=[changed])
            await asyncio.sleep(2)
            async def resolve(name):
                info = await zc.async_get_service_info(self.service_type, name, timeout=1000)
                return service_record(info, self.api_path) if info else None
            records = await asyncio.gather(*(resolve(name) for name in tuple(names)))
            unique = {}
            for record in records:
                if not record:
                    continue
                previous = unique.get(record['id'])
                if previous and previous['url'] != record['url']:
                    previous['conflict'] = True
                elif not previous:
                    unique[record['id']] = record
            return sorted(unique.values(), key=lambda record: (record['name'].casefold(), record['id']))
        except Exception:
            import logging
            logging.getLogger(__name__).warning('Hub LAN browse failed', exc_info=True)
            return []
        finally:
            if browser:
                await browser.async_cancel()
            if zc:
                await zc.async_close()


@router.get('/self')
async def current_hub(request: Request):
    return {**await asyncio.to_thread(local_identity), 'desktop': desktop_available(request),
            'local_entry': request.app.state.local_entry}


@router.get('/connection')
async def connection(request: Request):
    """The local settings screen shows current LAN URLs, not permanent aliases."""
    from fastapi import HTTPException
    from orion.api.access_boundary import is_local
    from services.shared.environment import get_env
    if not is_local(request.scope):
        raise HTTPException(403, 'Bağlantı bilgisi yalnızca bu bilgisayarda açılabilir.')
    port = int(get_env('ORION_PUBLIC_PORT', default='8911', required=False))
    if port == int(get_env('ORION_LOCAL_PORT', default='8910', required=False)):
        port = 8911
    addresses = await asyncio.to_thread(lan_addresses)
    return {**await asyncio.to_thread(local_identity),
            'urls': [f'https://{address}:{port}/' for address in addresses],
            'encrypted': True}


def desktop_available(request):
    from orion.api.access_boundary import is_desktop_peer
    return is_desktop_peer(request.scope)


@router.get('/hubs')
async def hubs(request: Request):
    from orion.api.access_boundary import FORWARDED
    try:
        peer = ipaddress.ip_address(request.client.host)
        local_network = peer.is_loopback or any(peer in net for net in LAN_NETWORKS)
    except (AttributeError, ValueError):
        local_network = False
    # Public/tunnel clients may use this Hub, but do not receive its LAN directory.
    local_network = local_network and not any(name in request.headers for name in FORWARDED)
    records = list(await request.app.state.hub_directory.list()) if local_network else []
    current = await asyncio.to_thread(local_identity)
    # Current origin stays usable even without multicast or in Docker.
    records = [record for record in records if record['id'] != current['id']]
    self_url = f'http://127.0.0.1:{os.getenv("ORION_LOCAL_PORT", "8910")}/hub' if getattr(request.app.state, 'local_http', False) else '/hub' if request.app.state.local_entry else '/'
    records.insert(0, {**current, 'url': self_url})
    return {'hubs': records, 'current_id': current['id']}
