"""LAN discovery for the public Hub only; discovery grants no authorization."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import ipaddress
import logging
import os
from pathlib import Path
import socket
import sys
import uuid

import psutil
from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

logger = logging.getLogger(__name__)
SERVICE_TYPE = "_orion._tcp.local."
VIRTUAL_INTERFACES = ("docker", "veth", "vethernet", "wsl", "hyper-v", "vmware",
                      "virtualbox", "virbr", "br-", "tailscale", "tun", "tap", "vpn")
LAN_NETWORKS = tuple(ipaddress.ip_network(net) for net in
                     ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def lan_addresses() -> list[str]:
    """Use active physical RFC1918 IPv4 interfaces, optionally explicitly selected."""
    selected = {name.strip() for name in os.getenv("ORION_MDNS_INTERFACES", "").split(",") if name.strip()}
    stats = psutil.net_if_stats()
    addresses = set()
    for name, entries in psutil.net_if_addrs().items():
        if selected:
            if name not in selected:
                continue
        elif any(token in name.lower() for token in VIRTUAL_INTERFACES):
            continue
        if name not in stats or not stats[name].isup:
            continue
        for entry in entries:
            if entry.family != socket.AF_INET:
                continue
            address = ipaddress.ip_address(entry.address)
            if any(address in network for network in LAN_NETWORKS):
                addresses.add(str(address))
    return sorted(addresses)


def device_id(path: Path) -> str:
    """Persist once; never silently replace a corrupt existing identity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as file:
            file.write(str(uuid.uuid4()))
    except FileExistsError:
        pass
    return str(uuid.UUID(path.read_text(encoding="ascii").strip()))


def canonical_hostname(identity: str) -> str:
    return f'orion-{uuid.UUID(identity).hex}.local.'


def listener_address() -> tuple[str, int]:
    """Read the actual uvicorn CLI before falling back to environment defaults."""
    host = os.getenv("ORION_BIND_HOST", os.getenv("UVICORN_HOST", "127.0.0.1"))
    port = os.getenv("UVICORN_PORT", os.getenv("ORION_PUBLIC_PORT", "8910"))
    for index, argument in enumerate(sys.argv):
        for option in ("host", "port"):
            value = None
            if argument == f"--{option}" and index + 1 < len(sys.argv):
                value = sys.argv[index + 1]
            elif argument.startswith(f"--{option}="):
                value = argument.split("=", 1)[1]
            if value is not None:
                if option == "host":
                    host = value
                else:
                    port = value
    return host, int(port)


class HubDiscovery:
    def __init__(self, version: str):
        self.version = version
        self.task: asyncio.Task | None = None
        self.zc: AsyncZeroconf | None = None
        self.info: ServiceInfo | None = None
        self.addresses: list[str] = []

    def start(self) -> None:
        # Docker bridge multicast does not advertise a reachable host endpoint.
        if os.getenv("ORION_MDNS", "1").lower() in {"0", "false", "off", "no"}:
            return
        if Path("/.dockerenv").exists():
            logger.info("Hub mDNS skipped in Docker bridge; use a host-side advertiser")
            return
        self.task = asyncio.create_task(self._run(), name="hub-mdns")

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self._withdraw()

    async def _withdraw(self) -> None:
        current, self.zc = self.zc, None
        self.info = None
        self.addresses = []
        if current is not None:
            # async_close sends goodbye records and closes multicast sockets.
            await current.async_close()

    async def _ready(self, host: str, port: int) -> None:
        # Lifespan runs before uvicorn opens its listener, so wait in a task.
        probe = "127.0.0.1" if host == "0.0.0.0" else host
        while True:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(probe, port), 1)
                writer.close()
                await writer.wait_closed()
                return
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.5)

    async def _refresh(self, identity: str, name: str, host: str, port: int) -> None:
        addresses = await asyncio.to_thread(lan_addresses)
        if host != "0.0.0.0":
            addresses = [address for address in addresses if address == host]
        if addresses == self.addresses and self.zc is not None:
            return
        # Recreate sockets too: update_service alone cannot join a new interface.
        await self._withdraw()
        if not addresses:
            logger.info("Hub mDNS waiting for a reachable LAN IPv4 interface")
            return
        self.zc = AsyncZeroconf(interfaces=addresses, ip_version=IPVersion.V4Only)
        hostname = canonical_hostname(identity)
        from orion.kernel.local_security import tls_identity
        fingerprint = (await asyncio.to_thread(tls_identity))['fp']
        self.info = ServiceInfo(
            SERVICE_TYPE, f"Orion {identity}.{SERVICE_TYPE}",
            server=hostname, port=port, parsed_addresses=addresses,
            properties={"id": identity, "name": name, "path": "/api/v1", "version": self.version, "scheme": "https",
                        "fp": fingerprint},
        )
        broadcast = await self.zc.async_register_service(self.info, allow_name_change=True)
        await broadcast
        self.addresses = addresses
        logger.info("Hub mDNS: %s -> %s:%s (%s)", self.info.name, hostname, port, addresses)

    async def _run(self) -> None:
        try:
            host, port = listener_address()
            if host in {"127.0.0.1", "localhost", "::1", "::"}:
                logger.info("Hub mDNS skipped: requires an IPv4 LAN listener")
                return
            if port == int(os.getenv("HUB_PORT", "8909")):
                logger.warning("Hub mDNS refuses to advertise the owner port")
                return
            from orion.kernel.local_security import tls_identity
            identity = (await asyncio.to_thread(tls_identity))['id']
            name = os.getenv("ORION_MDNS_NAME", socket.gethostname()).strip() or "Orion"
            name = name.encode("utf-8")[:240].decode("utf-8", errors="ignore")
            await self._ready(host, port)
            while True:
                try:
                    await self._refresh(identity, name, host, port)
                except Exception:
                    logger.warning("Hub mDNS announcement failed; retrying in 30s", exc_info=True)
                    await self._withdraw()
                await asyncio.sleep(30)
        except Exception:
            logger.warning("Hub mDNS unavailable; Hub continues without discovery", exc_info=True)
        finally:
            await self._withdraw()
