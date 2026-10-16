"""Read-only, local Router readiness verifier; never starts selected code."""
import hashlib
import http.client
import json
from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from services.shared.process_lifecycle import (
    Runtime, ProcessReference, canonical, child_running, living, owns_listener,
)
from services.shared.router_installation import LiveRouterEvidence, RouterIdentityMismatch


def _fingerprint(certificate):
    return hashlib.sha256(certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()


def _local_identity(port, certificate_path, expected_spki):
    """HTTPSConnection uses no proxies or redirects and validates the peer pin."""
    if _fingerprint(x509.load_pem_x509_certificate(Path(certificate_path).read_bytes())) != expected_spki:
        raise RouterIdentityMismatch('Router public certificate changed since inspection')
    context = ssl.create_default_context(cafile=str(certificate_path))
    connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=2)
    try:
        try:
            connection.connect()
        except ssl.SSLCertVerificationError as exc:
            raise RouterIdentityMismatch('Live Router TLS certificate verification failed') from exc
        peer = x509.load_der_x509_certificate(connection.sock.getpeercert(binary_form=True))
        if _fingerprint(peer) != expected_spki:
            raise RouterIdentityMismatch('Live Router TLS identity mismatch')
        connection.request('GET', '/api/v1/tls/identity', headers={'Accept': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Router identity API unavailable')
        payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError('Router identity response is too large')
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError('Router identity API response invalid')
        return value
    finally:
        connection.close()


def _schema_ready(database):
    # Metadata only; no migration, DDL or provider calls.
    # One statement: psql -c / PQexec exposes only the final statement result.
    return database.sql("SELECT (to_regclass('public.router_virtual_keys') IS NOT NULL "
                        "AND to_regclass('public.router_configs') IS NOT NULL)::text").strip() == 'true'


def verify_running_router(candidate, *, port, certificate_path, database,
                          component='router-prod', schema_probe=None):
    """Return trusted evidence, None when closed, or raise on identity/access errors.

    ``database`` must be an already authorized Postgres adapter for the application
    database; only ready() and read-only SQL are invoked. Lifecycle records can
    name an administrative control database (such as postgres) on the same
    cluster. The explicit adapter's cluster paths, port and user must match;
    application database compatibility is proved by its schema probe instead.
    No recorded database executable is loaded. The
    registry/source inspection caller must supply its existing UUID and SPKI pins.
    This function reads Runtime without acquiring its lock or creating state.
    """
    if component not in {'router-prod', 'router-dev'} or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid local Router verification target')
    if candidate.status != 'closed-unverified' or not candidate.identity or not candidate.spki:
        raise ValueError('Router source and public identity must be inspected first')
    runtime = Runtime(candidate.root, component)
    if candidate.state_path is not None:
        runtime.directory = Path(candidate.state_path) / component
        runtime.path = runtime.directory / 'processes.json'
    record = runtime.read()
    if record is None or record.get('status') == 'stopped':
        return None
    if not living(record['supervisor']):
        return None
    children = record['children']
    backend = children.get('backend')
    if not backend or not all(child_running(child) for child in children.values()):
        return None
    entrypoint = Path(candidate.root) / 'main.py'
    if canonical(backend.get('entrypoint', '')) != canonical(entrypoint):
        raise RouterIdentityMismatch('Router backend entry point mismatch')
    # The live OS command must identify the same entry point, not just a JSON label.
    with ProcessReference(backend['process']) as reference:
        if not any(canonical(arg) == canonical(entrypoint) for arg in reference.process.cmdline()[1:] if not arg.startswith('-')):
            raise RouterIdentityMismatch('Router process command does not identify selected root')
    if not owns_listener(backend['process'], port):
        return None
    selected_database = record.get('database')
    if not isinstance(selected_database, dict):
        raise ValueError('Router database provenance is missing')
    for field in ('ctl', 'data'):
        if canonical(selected_database[field]) != canonical(getattr(database, field)):
            raise RouterIdentityMismatch('Authorized database differs from Router process record')
    for field in ('port', 'user'):
        if selected_database[field] != getattr(database, field):
            raise RouterIdentityMismatch('Authorized database differs from Router process record')
    identity = _local_identity(port, certificate_path, candidate.spki)
    if identity.get('id') != candidate.identity or identity.get('fp') != candidate.spki:
        raise RouterIdentityMismatch('Live Router UUID/TLS identity mismatch')
    api_compatible = type(identity.get('v')) is int and identity['v'] == 1
    database_verified = bool(database.ready()) and bool((schema_probe or _schema_ready)(database))
    # Recheck ownership after TLS and SQL to detect exit/replacement during probes.
    process_verified = (living(record['supervisor']) and child_running(backend)
                        and owns_listener(backend['process'], port))
    return LiveRouterEvidence(candidate.root, candidate.identity, candidate.spki,
                              process_verified, True, api_compatible, database_verified)
