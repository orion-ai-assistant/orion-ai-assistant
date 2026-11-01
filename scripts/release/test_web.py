"""Feed reads fall back to Windows' own TLS when Python cannot verify the issuer, and only then."""
import ssl
import urllib.error

import pytest

import web


def failing(reason):
    def urlopen(url, timeout):
        raise reason
    return urlopen


def test_an_unverified_issuer_is_read_through_windows(monkeypatch):
    monkeypatch.setattr(web.urllib.request, 'urlopen', failing(urllib.error.URLError(ssl.SSLCertVerificationError(1, 'x'))))
    monkeypatch.setattr(web, '_curl', lambda url, timeout, output=None: b'{"ok": 1}')
    assert web.read('https://github.com/x') == b'{"ok": 1}'


def test_other_failures_are_not_retried(monkeypatch):
    monkeypatch.setattr(web, '_curl', lambda *args, **kwargs: pytest.fail('retried'))
    missing = urllib.error.HTTPError('u', 404, 'nf', {}, None)
    monkeypatch.setattr(web.urllib.request, 'urlopen', failing(missing))
    with pytest.raises(urllib.error.HTTPError):
        web.read('https://github.com/x')
    monkeypatch.setattr(web.urllib.request, 'urlopen', failing(urllib.error.URLError(ConnectionRefusedError())))
    with pytest.raises(urllib.error.URLError):
        web.read('https://github.com/x')


def test_windows_curl_reads_the_live_feed_and_reports_missing_files():
    if not web.CURL.is_file():
        pytest.skip('Windows curl.exe only')
    base = 'https://github.com/orion-ai-assistant/orion-ai-assistant/releases/download/feed/'
    assert b'"signed"' in web._curl(base + 'timestamp.json', 30)
    with pytest.raises(urllib.error.HTTPError) as error:
        web._curl(base + 'no-such-file.json', 30)
    assert error.value.code == 404
