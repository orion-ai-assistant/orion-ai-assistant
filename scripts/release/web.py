"""Reading the public feeds and inputs over HTTPS, also when Python cannot verify GitHub's certificate.

Python checks certificates against the roots Windows has already downloaded; Windows fetches a missing root
(GitHub's Sectigo chain, 2026-11) only when its own TLS needs it. So the same request may fail in Python
and pass in Windows. On that one error the request is repeated with Windows' own curl.exe (Schannel), which
verifies the certificate itself: verification is never turned off.
"""
import io
import os
from pathlib import Path
import ssl
import subprocess
import urllib.error
import urllib.request

CURL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'curl.exe'


def _unverified_issuer(error):
    reason = getattr(error, 'reason', error)
    return isinstance(reason, ssl.SSLCertVerificationError) and CURL.is_file()


def _curl(url, timeout, output=None):
    command = [str(CURL), '--silent', '--show-error', '--location', '--max-time', str(timeout),
               '--write-out', '%{http_code}', '--output', str(output) if output else '-', url]
    result = subprocess.run(command, capture_output=True, timeout=timeout + 30)
    body, status = (result.stdout[:-3], result.stdout[-3:]) if not output else (b'', result.stdout[-3:])
    if result.returncode:
        raise urllib.error.URLError(result.stderr.decode('utf-8', 'replace').strip() or 'curl failed')
    code = int(status or 0)
    if code >= 400:
        raise urllib.error.HTTPError(url, code, 'HTTP ' + str(code), {}, io.BytesIO())
    return body


def read(url, timeout=30):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as error:
        if isinstance(error, urllib.error.HTTPError) or not _unverified_issuer(error):
            raise
        return _curl(url, timeout)


def download(url, target, timeout=60):
    """Streams `url` into the new file `target` (it must not exist)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, Path(target).open('xb') as output:
            while chunk := response.read(1 << 20):
                output.write(chunk)
    except urllib.error.URLError as error:
        if isinstance(error, urllib.error.HTTPError) or not _unverified_issuer(error):
            raise
        Path(target).unlink(missing_ok=True)
        _curl(url, max(timeout, 1800), output=target)
