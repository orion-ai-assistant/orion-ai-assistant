import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from orion.api.router_connection import Selection, select

ID = '12345678-1234-4321-8123-123456789abc'
FP = 'a' * 64


class SelectionSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def attempt(self, trusted, fingerprint=None, verification_error=None):
        record = {'id': ID, 'name': 'Router', 'url': 'https://192.168.1.20:9443/'}
        manager = SimpleNamespace(lock=asyncio.Lock(), directory=SimpleNamespace(list=AsyncMock(return_value=[record])))
        store = MagicMock()
        store.routers.return_value = trusted
        verifier = AsyncMock(return_value={'id': ID, 'name': 'Router', 'fp': FP}, side_effect=verification_error)
        with patch('orion.api.router_connection.association', return_value=manager), patch('orion.api.router_connection.SecurityStore', return_value=store), patch('orion.api.router_connection.verify_router', verifier), patch('orion.api.router_connection.write_connection') as write:
            result = await select(Selection(id=ID, fp=fingerprint), object())
            return result, verifier, write, store

    async def test_known_router_reuses_pin_but_verifies_live_tls(self):
        result, verifier, write, store = await self.attempt([{'id': ID, 'fp': FP}])
        verifier.assert_awaited_once_with('https://192.168.1.20:9443', ID, FP)
        self.assertEqual(result['selected']['fp'], FP)
        write.assert_called_once()

    async def test_new_router_needs_explicit_pin(self):
        with self.assertRaises(HTTPException) as error:
            await self.attempt([])
        self.assertEqual(error.exception.status_code, 400)

    async def test_known_router_changed_live_key_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            await self.attempt([{'id': ID, 'fp': FP}], verification_error=ValueError('wrong live key'))
        self.assertEqual(error.exception.status_code, 409)
