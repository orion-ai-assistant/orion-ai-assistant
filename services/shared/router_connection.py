"""Selected Router state shared by the native Hub API and worker processes."""
import json
import os
from pathlib import Path
import tempfile
import time
import uuid
from urllib.parse import urlsplit


def connection_path():
    from .runtime_layout import data_root
    return Path(os.getenv('ORION_ROUTER_CONNECTION_FILE', str(
        data_root(Path(__file__).resolve().parents[2]) / 'services/hub/data/router-connection.json')))


def validate_url(value):
    try:
        uri = urlsplit(value.strip())
        port = uri.port
    except (ValueError, AttributeError):
        raise ValueError('Router adresi geçersiz.') from None
    if (uri.scheme != 'https' or not uri.hostname or uri.username or uri.password
            or uri.query or uri.fragment or uri.path not in {'', '/', '/v1', '/v1/'}
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError('Router adresi yalnızca HTTPS adresi ve port içermeli.')
    # Router's existing API rejects the literal localhost Host; its panel does not.
    netloc = ('127.0.0.1' + (f':{port}' if port is not None else '')) if uri.hostname == 'localhost' else uri.netloc
    return f'{uri.scheme}://{netloc}'


def read_connection():
    path = connection_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['mode'] not in {'discovery', 'manual'}:
            raise ValueError('Invalid selection mode')
        if data['mode'] == 'discovery':
            data['id'] = str(uuid.UUID(data['id']))
            float(data.get('last_seen', 0))
        if data.get('url'):
            data['url'] = validate_url(data['url'])
        elif data['mode'] == 'manual':
            raise ValueError('Missing manual endpoint')
        return data
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise RuntimeError('Router bağlantı kaydı okunamadı; panelden kontrol edin.') from error


def write_connection(data):
    path = connection_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def selected_router_url():
    data = read_connection()
    if data is None:
        return None
    if data['mode'] == 'manual':
        return data['url']
    # A saved UUID is never replaced by environment defaults when it disappears.
    if not data.get('url') or time.time() - float(data.get('last_seen', 0)) > 75:
        raise RuntimeError('Kayıtlı Router bulunamadı; bağlantı otomatik değiştirilmedi.')
    return data['url']
