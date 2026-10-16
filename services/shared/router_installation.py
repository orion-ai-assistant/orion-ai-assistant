"""Read-only Router recognition; selected source is never imported or executed."""
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib
from uuid import UUID


class RouterSelectionError(RuntimeError):
    """Repair or explicit selection required; never silently install a fallback."""


class RouterIdentityMismatch(ValueError):
    """Runtime/public identity does not match the selected registered target."""


@dataclass(frozen=True)
class RouterCandidate:
    root: str
    distribution: str
    status: str
    reason: str = ''
    version: str | None = None
    identity: str | None = None
    spki: str | None = None
    data_path: str | None = None
    state_path: str | None = None


@dataclass(frozen=True)
class LiveRouterEvidence:
    """Trusted host adapter result, never deserialize from selected files/network.

    The adapter checks process path/start time against root, pinned TLS, authorized
    DB/schema and API. UUID or an open port alone cannot produce this evidence.
    """
    root: str
    identity: str
    spki: str
    process_verified: bool
    tls_verified: bool
    api_compatible: bool
    database_verified: bool


def legacy_locator_path():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Orion' / 'router-installation.json'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'orion' / 'router-installation.json'


def _read_project(root):
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    metadata = project.get('project')
    if not isinstance(metadata, dict) or metadata.get('name') != 'orion-router':
        raise ValueError('Selected directory is not Orion Router')
    return metadata


def registered_router_root():
    """Canonical installed selection precedes legacy; bad targets require repair.

    Legacy minimal pyproject records remain supported without requiring TLS.
    Development components never silently become the installed selection.
    """
    from services.shared.installation_registry import Registry
    record = Registry().read()
    if record is not None:
        routers = [item for item in record['components']
                   if item['kind'] == 'router' and item['distribution'] != 'development']
        if not routers:
            return None
        if len(routers) != 1:
            raise RouterSelectionError('Multiple registered Routers require an explicit selection')
        selected = routers[0]
        if selected['distribution'] in {'remote', 'docker'}:
            return None
        candidate = _inspect_registered_component(selected)
        if candidate.status not in {'closed-unverified', 'ready'}:
            raise RouterSelectionError(f'Registered Router target requires repair: {candidate.status}')
        return candidate.root
    else:
        try:
            record = json.loads(legacy_locator_path().read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise RouterSelectionError('Router registration requires repair') from exc
        if (not isinstance(record, dict) or record.get('v') != 1
                or record.get('kind') != 'orion-router-local-installation'):
            raise RouterSelectionError('Unsupported Router registration; repair required')
        raw_root = record.get('root')
    try:
        if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
            raise ValueError('Router root must be absolute')
        root = Path(raw_root).resolve(strict=True)
        _read_project(root)
        return str(root)
    except (OSError, ValueError, TypeError) as exc:
        raise RouterSelectionError('Registered Router target requires repair') from exc


def inspect_router_folder(root, *, distribution='legacy-source', expected_identity=None,
                          expected_spki=None, identity_path=None, certificate_path=None,
                          data_path=None, state_path=None, supported_major=0,
                          live_verifier=None):
    """Recognize local source without running it, writing files or granting trust.

    Package signatures require the stage 05 verifier. Optional live_verifier is a
    trusted host adapter; omission always means closed-unverified. Explicit paths
    support existing installations configured with external persistent storage.
    """
    if distribution not in {'legacy-source', 'development', 'packaged'}:
        raise ValueError('Local Router distribution must be explicit')
    raw_root = Path(root)
    candidate = RouterCandidate(str(raw_root), distribution, 'missing-broken')
    try:
        if not raw_root.is_absolute():
            raise ValueError('Router root must be absolute')
        directory = raw_root.resolve(strict=True)
        candidate = replace(candidate, root=str(directory))
        if distribution == 'packaged':
            return replace(candidate, status='incompatible', reason='Signed package verification is required')
        project = _read_project(directory)
        version = project.get('version')
        if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?', version):
            raise ValueError('Missing or invalid Router version metadata')
        candidate = replace(candidate, version=version)
        if int(version.split('.')[0]) != supported_major:
            return replace(candidate, status='incompatible', reason='Unsupported Router major version')
        for filename in ('main.py', 'core/config.py', 'bin/prod.py', 'bin/stop.py'):
            path = (directory / filename).resolve(strict=True)
            if not path.is_relative_to(directory) or not path.is_file():
                raise ValueError('Router entry point escapes the selected root')
            with path.open('rb') as stream:
                stream.read(1)
        data = Path(data_path) if data_path is not None else directory / 'persistent'
        state = Path(state_path) if state_path is not None else directory / '.orion-lifecycle'
        if not data.is_absolute() or not state.is_absolute():
            raise ValueError('Router data/state paths must be absolute')
        candidate = replace(candidate, data_path=str(data.resolve()), state_path=str(state.resolve()))
        identity_file = Path(identity_path) if identity_path is not None else data / 'mdns-id'
        certificate_file = Path(certificate_path) if certificate_path is not None else data / 'tls/certificate.pem'
        if not identity_file.is_absolute() or not certificate_file.is_absolute():
            raise ValueError('Router identity paths must be absolute')
        identity = str(UUID(identity_file.read_text(encoding='ascii').strip()))
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        certificate = x509.load_pem_x509_certificate(certificate_file.read_bytes())
        key = certificate.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError('Router TLS identity must use P-256')
        spki = hashlib.sha256(key.public_bytes(serialization.Encoding.DER,
                            serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        candidate = replace(candidate, identity=identity, spki=spki)
        common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(common_names) != 1 or common_names[0].value != 'Orion Router ' + identity:
            return replace(candidate, status='identity-mismatch', reason='Router certificate subject differs from persistent UUID')
        if ((expected_identity is not None and identity != str(UUID(expected_identity)))
                or (expected_spki is not None and spki != expected_spki)):
            return replace(candidate, status='identity-mismatch', reason='Recorded UUID/TLS identity differs')
        candidate = replace(candidate, status='closed-unverified', reason='Unsigned source recognized; runtime unverified')
        if live_verifier is None:
            return candidate
        evidence = live_verifier(candidate)
        if evidence is None:
            return candidate
        if not isinstance(evidence, LiveRouterEvidence):
            raise ValueError('Invalid live verification evidence')
        if (str(Path(evidence.root).resolve()) != candidate.root
                or evidence.identity != identity or evidence.spki != spki):
            return replace(candidate, status='identity-mismatch', reason='Live Router differs from selected installation')
        if not evidence.api_compatible:
            return replace(candidate, status='incompatible', reason='Live Router API is incompatible')
        if (expected_identity is not None and expected_spki is not None
                and evidence.process_verified and evidence.tls_verified and evidence.database_verified):
            return replace(candidate, status='ready', reason='Owned process, pinned TLS, API and DB verified')
        return candidate
    except RouterIdentityMismatch as exc:
        return replace(candidate, status='identity-mismatch', reason=str(exc))
    except PermissionError:
        return replace(candidate, status='access-denied', reason='Router files or process evidence are inaccessible')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return replace(candidate, status='missing-broken', reason=str(exc))
    except ImportError:
        return replace(candidate, status='incompatible', reason='TLS verification dependency is unavailable')


def discover_router_candidates(*, registered_root=None, legacy_roots=(), development_roots=()):
    """Only caller-approved known locations; no PATH, port or drive scan."""
    candidates, seen = [], set()
    roots = ([(registered_root, 'legacy-source')] if registered_root is not None else [])
    roots += [(root, 'legacy-source') for root in legacy_roots]
    roots += [(root, 'development') for root in development_roots]
    for root, distribution in roots:
        key = os.path.normcase(str(Path(root).resolve()))
        if key not in seen:
            seen.add(key)
            candidates.append(inspect_router_folder(root, distribution=distribution))
    return candidates


def select_router_candidate(candidates, *, root=None, distribution='installed'):
    """Choose without moving/merging data, stopping processes or choosing by port."""
    if distribution not in {'installed', 'development'}:
        raise ValueError('Selection mode must be installed or development')
    choices = [item for item in candidates
               if (item.distribution == 'development') == (distribution == 'development')]
    if root is not None:
        target = os.path.normcase(str(Path(root).resolve()))
        choices = [item for item in choices if os.path.normcase(item.root) == target]
    if len(choices) != 1:
        raise RouterSelectionError('An explicit Router selection is required')
    selected = choices[0]
    if selected.status not in {'ready', 'closed-unverified'}:
        raise RouterSelectionError(f'Selected Router requires repair: {selected.status}')
    return selected


def known_legacy_roots():
    if os.name == 'nt':
        local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
        return [local / 'OrionRouter']
    return [Path.home() / '.orion-router', Path.home() / '.local/share/OrionRouter']


def _inspect_registered_component(source):
    kwargs = {'distribution': source['distribution'], 'data_path': source['data_path'],
              'state_path': source['state_path'], 'expected_identity': source.get('router_uuid'),
              'expected_spki': source.get('tls_spki_sha256')}
    for key in ('identity_path', 'certificate_path'):
        if source.get(key) is not None:
            kwargs[key] = source[key]
    return inspect_router_folder(source['code_path'], **kwargs)


def find_router_script():
    """Installed native wrapper only; no PATH or development checkout fallback."""
    registered = registered_router_root()
    if registered is not None:
        roots = [Path(registered)]
    else:
        # A canonical record exists: absence of a local Router is authoritative.
        from services.shared.installation_registry import Registry
        if Registry().read() is not None:
            return None, None
        roots = []
        for root in known_legacy_roots():
            try:
                root.stat()
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                raise RouterSelectionError('Known Router location is inaccessible') from exc
            roots.append(root)
    if not roots:
        return None, None
    if registered is not None:
        from services.shared.installation_registry import Registry
        record = Registry().read()
        if record is not None:
            source = next(item for item in record['components'] if item['kind'] == 'router'
                          and item['distribution'] != 'development')
            candidate = select_router_candidate([_inspect_registered_component(source)])
        else:
            source = json.loads(legacy_locator_path().read_text(encoding='utf-8'))
            kwargs = {key: source[key] for key in ('identity_path', 'certificate_path') if source.get(key) is not None}
            candidate = select_router_candidate([inspect_router_folder(registered, **kwargs)])
    else:
        candidate = select_router_candidate(discover_router_candidates(legacy_roots=roots))
    wrapper = Path(candidate.root) / ('orionrouter.ps1' if os.name == 'nt' else 'orionrouter')
    try:
        resolved = wrapper.resolve(strict=True)
        if not resolved.is_relative_to(Path(candidate.root)) or not resolved.is_file():
            raise ValueError('Router wrapper escapes selected root')
        with resolved.open('rb') as stream:
            stream.read(1)
    except (OSError, ValueError) as exc:
        raise RouterSelectionError('Selected Router wrapper requires repair') from exc
    return ('win' if os.name == 'nt' else 'unix'), str(resolved)
