"""LAN address helpers. Hubs advertise only the Orion app listener (mobile_server)."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket
import uuid

import psutil

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
