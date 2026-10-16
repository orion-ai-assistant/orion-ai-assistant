from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from installer.app.routes import router


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_read_preferences_does_not_enable_startup():
    with patch('installer.app.config.installation_registry') as store, patch('services.shared.launcher.preferences') as write:
        store.return_value.read.return_value = {'preferences': {'start_with_windows': False, 'show_client': True}}
        response = client().get('/api/installation/launcher')
        assert response.status_code == 200
        assert response.json()['preferences']['start_with_windows'] is False
        write.assert_not_called()


def test_preferences_require_strict_booleans_and_surface_failure():
    with patch('services.shared.launcher.preferences', side_effect=RuntimeError('Shortcut ownership mismatch')) as write:
        assert client().post('/api/installation/launcher', json={'start_with_windows': 'yes', 'show_client': True}).status_code == 422
        write.assert_not_called()
        response = client().post('/api/installation/launcher', json={'start_with_windows': True, 'show_client': False})
        assert response.status_code == 409
        assert response.json()['detail'] == 'Shortcut ownership mismatch'
