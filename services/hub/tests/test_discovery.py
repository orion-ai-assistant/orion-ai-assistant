from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orion.api import discovery


class ConfigurationTests(unittest.TestCase):
    def test_identity_survives_restart_and_corruption_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mdns-id"
            identity = discovery.device_id(path)
            self.assertEqual(discovery.device_id(path), identity)
            path.write_text("broken", encoding="ascii")
            with self.assertRaises(ValueError):
                discovery.device_id(path)
            self.assertEqual(path.read_text(), "broken")

    def test_only_active_physical_private_ipv4_addresses_are_selected(self):
        entries = {
            "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.20"),
                      SimpleNamespace(family=socket.AF_INET6, address="fe80::1")],
            "Ethernet": [SimpleNamespace(family=socket.AF_INET, address="172.16.1.5")],
            "vEthernet (WSL)": [SimpleNamespace(family=socket.AF_INET, address="172.20.1.1")],
            "Docker": [SimpleNamespace(family=socket.AF_INET, address="10.1.1.1")],
            "Offline": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.8")],
            "Loopback": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
            "Public": [SimpleNamespace(family=socket.AF_INET, address="8.8.8.8")],
        }
        stats = {name: SimpleNamespace(isup=name != "Offline") for name in entries}
        with patch.dict("os.environ", {"ORION_MDNS_INTERFACES": ""}), \
             patch.object(discovery.psutil, "net_if_addrs", return_value=entries), \
             patch.object(discovery.psutil, "net_if_stats", return_value=stats):
            self.assertEqual(discovery.lan_addresses(), ["172.16.1.5", "192.168.1.20"])
            with patch.dict("os.environ", {"ORION_MDNS_INTERFACES": "Wi-Fi"}):
                self.assertEqual(discovery.lan_addresses(), ["192.168.1.20"])

    def test_hub_no_longer_advertises_a_browser_entry(self):
        # Only the Orion app listener (mobile_server, _orion-mobile._tcp) is advertised on the LAN.
        self.assertFalse(hasattr(discovery, "HubDiscovery"))
        self.assertFalse(hasattr(discovery, "SERVICE_TYPE"))
