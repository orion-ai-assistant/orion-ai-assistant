import asyncio
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from orion.api import discovery


class ConfigurationTests(unittest.TestCase):
    def test_hostname_is_derived_from_installation_uuid(self):
        first = '12345678-1234-4321-8123-123456789abc'
        second = '00000000-0000-4000-8000-000000000001'
        self.assertEqual(discovery.canonical_hostname(first), 'orion-12345678123443218123123456789abc.local.')
        self.assertNotEqual(discovery.canonical_hostname(first), discovery.canonical_hostname(second))

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

    def test_actual_listener_cli_overrides_environment(self):
        with patch.dict("os.environ", {"UVICORN_PORT": "8910", "ORION_BIND_HOST": "127.0.0.1"}), \
             patch.object(discovery.sys, "argv", ["uvicorn", "--host=0.0.0.0", "--port", "9123"]):
            self.assertEqual(discovery.listener_address(), ("0.0.0.0", 9123))

    def test_disabled_discovery_creates_no_task(self):
        with patch.dict("os.environ", {"ORION_MDNS": "0"}):
            publisher = discovery.HubDiscovery("test")
            publisher.start()
            self.assertIsNone(publisher.task)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_change_rejoins_interfaces_and_removal_withdraws(self):
        async def registered(*args, **kwargs):
            future = asyncio.get_running_loop().create_future()
            future.set_result(None)
            return future

        first, second = Mock(), Mock()
        for zc in (first, second):
            zc.async_register_service = AsyncMock(side_effect=registered)
            zc.async_close = AsyncMock()
        publisher = discovery.HubDiscovery("0.1.0")
        identity = "12345678-1234-4321-8123-123456789abc"
        with patch.object(discovery, "AsyncZeroconf", side_effect=[first, second]) as factory, \
             patch.object(discovery, "lan_addresses", return_value=["192.168.1.20"]) as addresses:
            await publisher._refresh(identity, "Ev bilgisayarı", "0.0.0.0", 8910)
            info = publisher.info
            self.assertEqual(info.port, 8910)
            self.assertEqual(info.type, "_orion._tcp.local.")
            self.assertEqual(info.properties[b"id"].decode(), identity)
            self.assertEqual(info.properties[b"name"].decode(), "Ev bilgisayarı")
            self.assertEqual(info.properties[b"path"], b"/api/v1")
            self.assertEqual(info.server, discovery.canonical_hostname(identity))
            self.assertEqual(first.async_register_service.await_count, 1)
            await publisher._refresh(identity, "Ev bilgisayarı", "0.0.0.0", 8910)
            self.assertEqual(factory.call_count, 1)
            addresses.return_value = ["192.168.1.21"]
            await publisher._refresh(identity, "Ev bilgisayarı", "0.0.0.0", 8910)
            first.async_close.assert_awaited_once()
            self.assertEqual(publisher.info.parsed_addresses(), ["192.168.1.21"])
            addresses.return_value = []
            await publisher._refresh(identity, "Ev bilgisayarı", "0.0.0.0", 8910)
            second.async_close.assert_awaited_once()
            self.assertIsNone(publisher.zc)

    async def test_loopback_and_owner_listener_never_publish(self):
        for host, port in [("127.0.0.1", 8910), ("0.0.0.0", 8909)]:
            with patch.dict("os.environ", {"HUB_PORT": "8909"}), \
                 patch.object(discovery, "listener_address", return_value=(host, port)), \
                 patch.object(discovery, "AsyncZeroconf") as factory:
                await discovery.HubDiscovery("test")._run()
                factory.assert_not_called()

    async def test_close_cancels_readiness_wait(self):
        publisher = discovery.HubDiscovery("test")
        publisher.task = asyncio.create_task(publisher._ready("127.0.0.1", 1))
        await asyncio.sleep(0)
        await publisher.close()
        self.assertTrue(publisher.task.cancelled())

    async def test_single_interface_binding_does_not_publish_other_interfaces(self):
        publisher = discovery.HubDiscovery("test")
        with patch.object(discovery, "lan_addresses", return_value=["192.168.1.20"]), \
             patch.object(discovery, "AsyncZeroconf") as factory:
            await publisher._refresh("id", "name", "192.168.1.30", 8910)
            factory.assert_not_called()
