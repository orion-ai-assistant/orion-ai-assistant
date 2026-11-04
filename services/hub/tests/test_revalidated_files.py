from fastapi import FastAPI
from fastapi.testclient import TestClient

from orion.api.main import RevalidatedFiles


def test_static_pages_are_revalidated_after_an_update(tmp_path):
    (tmp_path / 'index.html').write_text('<p>v2</p>')
    app = FastAPI()
    app.mount('/admin', RevalidatedFiles(directory=tmp_path, html=True), name='admin')
    client = TestClient(app)
    first = client.get('/admin/')
    assert first.status_code == 200 and first.headers['cache-control'] == 'no-cache'
    again = client.get('/admin/', headers={'if-none-match': first.headers['etag']})
    assert again.status_code == 304  # Unchanged: nothing downloaded again.
