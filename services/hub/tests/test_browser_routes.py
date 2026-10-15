import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI

from orion.api import browser_routes
from orion.api.auth_routes import get_current_user
from orion.browser.manager import BrowserBusy, BrowserUnavailable


class BrowserRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.include_router(browser_routes.router)
        self.app.dependency_overrides[get_current_user] = lambda: 'authenticated-alice'
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_open_validates_browser_and_uses_authenticated_user(self):
        manager = Mock()
        manager.open_manual = AsyncMock()
        with patch.object(browser_routes, 'require_local'), patch.object(browser_routes, 'BrowserManager', return_value=manager) as factory:
            response = await self.client.post('/api/v1/browser/open', json={'browser': 'edge', 'user_id': 'other'})
            self.assertEqual(response.status_code, 200)
            factory.assert_called_once_with('authenticated-alice')
            manager.open_manual.assert_awaited_once_with('edge')
            response = await self.client.post('/api/v1/browser/open', json={'browser': 'arbitrary.exe'})
            self.assertEqual(response.status_code, 422)

    async def test_remote_request_rejected_before_browser_access(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('203.0.113.1', 123)), base_url='http://test') as remote:
            with patch.object(browser_routes, 'BrowserManager') as factory:
                self.assertEqual((await remote.post('/api/v1/browser/open', json={})).status_code, 403)
                factory.assert_not_called()

    async def test_busy_is_conflict(self):
        manager = Mock()
        manager.resume = AsyncMock(side_effect=BrowserBusy('busy'))
        with patch.object(browser_routes, 'require_local'), patch.object(browser_routes, 'BrowserManager', return_value=manager):
            self.assertEqual((await self.client.post('/api/v1/browser/resume')).status_code, 409)

    async def test_explicit_chrome_is_not_overridden_by_brave_header(self):
        manager = Mock(open_manual=AsyncMock())
        with patch.object(browser_routes, 'require_local'), patch.object(browser_routes, 'BrowserManager', return_value=manager):
            response = await self.client.post('/api/v1/browser/open', json={'browser': 'chrome'}, headers={'X-Client-Browser': 'brave'})
        self.assertEqual(response.status_code, 200)
        manager.open_manual.assert_awaited_once_with('chrome')

    async def test_saved_choice_wins_when_browser_is_not_explicit(self):
        manager = Mock(open_manual=AsyncMock(), status=AsyncMock(return_value={'saved': True, 'browser': 'edge'}))
        with patch.object(browser_routes, 'require_local'), patch.object(browser_routes, 'BrowserManager', return_value=manager):
            response = await self.client.post('/api/v1/browser/open', json={}, headers={'X-Client-Browser': 'brave'})
        self.assertEqual(response.status_code, 200)
        manager.open_manual.assert_awaited_once_with('edge')

    async def test_disconnected_resume_cannot_return_ready_message(self):
        manager = Mock()
        manager.resume = AsyncMock(side_effect=BrowserUnavailable('connection closed'))
        with patch.object(browser_routes, 'require_local'), patch.object(browser_routes, 'BrowserManager', return_value=manager):
            response = await self.client.post('/api/v1/browser/resume')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('message', response.json())

    async def test_panel_opens_only_fixed_local_app_window_without_owner_auth(self):
        with patch.object(browser_routes, 'require_local'), patch.object(browser_routes, 'get_env', return_value='9321'), patch.object(browser_routes, 'open_window', return_value=True) as window:
            response = await self.client.post('/api/v1/browser/panel/open', json={'url': 'https://attacker.example', 'admin_key': 'ignored'})
        self.assertEqual(response.status_code, 200)
        window.assert_called_once_with('http://localhost:9321/panel', allow_tab_fallback=False)
        self.assertNotIn('set-cookie', response.headers)

    async def test_panel_remote_request_never_opens_host_window(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('192.168.1.50', 123)), base_url='http://localhost') as remote:
            with patch.object(browser_routes, 'open_window') as window:
                self.assertEqual((await remote.post('/api/v1/browser/panel/open')).status_code, 403)
                window.assert_not_called()

    async def test_panel_missing_browser_reports_failure_without_normal_tab(self):
        with patch.object(browser_routes, 'require_local'), patch.object(browser_routes, 'open_window', return_value=False):
            response = await self.client.post('/api/v1/browser/panel/open')
        self.assertEqual(response.status_code, 503)
        self.assertIn('Chrome, Edge veya Brave', response.json()['detail'])
