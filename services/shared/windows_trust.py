"""Public HTTPS (the GitHub release feeds) verified by Windows itself, as Edge and Windows Update do.

Python's own check reads only the roots Windows already has; Windows downloads a missing root (GitHub's
Sectigo chain, 2026-11) only for its own connections, so Python failed now and then with "unable to get
issuer certificate". truststore hands the decision to Windows' certificate verifier.

Only for Orion's public downloads: the Hub/Router/phone connections pin their own certificates, and no
process-wide injection touches the Hub's TLS servers.
"""
import ssl


def context():
    try:
        import truststore
    except ImportError:  # A runtime from before truststore: Python's own check, as before.
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def tuf_fetcher():
    """python-tuf's fetcher on that context (its default has Python's own check)."""
    import tuf
    from tuf.ngclient import Urllib3Fetcher
    from tuf.ngclient._internal.proxy import ProxyEnvironment

    class WindowsTrustFetcher(Urllib3Fetcher):
        def __init__(self):
            super().__init__()
            # Same as the parent's, plus the context: tuf==6.0.0 passes these to every urllib3 pool.
            self._proxy_env = ProxyEnvironment(headers={'User-Agent': f'python-tuf/{tuf.__version__}'},
                                               ssl_context=context())
    return WindowsTrustFetcher()
