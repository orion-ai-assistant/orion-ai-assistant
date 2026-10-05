# Hub Phase 6 local security

Hub uses two explicit transport policies. Native/mobile and LAN connections
require HTTPS/SPKI. This PC's local UI has a guarded HTTP exception:

| Address | Transport and purpose |
| --- | --- |
| `127.0.0.1:80` | HTTP launcher, `/hub`, `/panel`, `/orionrouter` shortcut |
| `127.0.0.1:8910/manage` | HTTP local owner HTML, local web UI and directory |
| `127.0.0.1:8909` | Existing direct owner HTTPS listener |
| `0.0.0.0:8911` | Public browser HTTPS, advertises its actual web port |
| `0.0.0.0:8443` | Pinned mobile HTTPS, paired device authentication |

`run_local.py` starts these listeners. It moves an old public 8910 configuration
to HTTPS 8911 to reserve 8910 for local HTTP. The HTTP exception requires an
actual loopback peer, localhost/loopback Host, and no forwarded headers; Docker
private-peer exceptions do not apply. It cannot accept native device credentials
or device-bound JWTs. TLS runners ignore forwarded scheme headers. Installer UI
remains independently HTTPS on 7171.

`ORION_SECURITY_DIR` stores the persistent P-256 key, certificate, Hub UUID and
SQLite device/pairing/Router trust database. Native default is `files/security/`;
Compose uses the shared `hub_security` volume. Back up this identity directory,
keep it private, and do not delete it during upgrades. Corrupt keys fail startup;
certificate renewal retains the private key and SHA-256(DER SPKI) lowercase hex
pin. Certificates include localhost, 127.0.0.1, ::1 and the advertised LAN IP
SANs. Missing SANs cause same-key renewal; old SANs are preserved for shared
Docker listeners. No private key or raw paired token is exposed by management
APIs.

Open `/manage/security` on the local owner listener to create a 120-second
single-use QR, revoke devices or establish Router trust by UUID and full SPKI
fingerprint. A Router must pass pin verification on its actual connection and
return the expected identity before trust is saved. Discovery never creates or
replaces trust. Changed Router keys require explicitly removing existing trust
and establishing it again.

For clients without a camera or working discovery, the pairing page also exposes
the exact QR JSON (including Hub UUID and HTTPS URL) in a selectable read-only
text area. Paste it into the client's QR-data field. The copy button uses the
clipboard when available; if clipboard access is unavailable it selects text for
manual copying instead. The same 120-second single-use limit applies.

## Protocol

- `_orion-mobile._tcp.local.` advertises `id`, `name`, `v=1`, `scheme=https`.
- QR uses exactly `v,id,url,fp,code,name` from the common plan.
- HTTPS `POST /api/v1/pair/claim` accepts `code,device_name,platform?`, tolerates
  optional client `device_id` but assigns its own UUID. Returns
  `device_id,device_token,hub_id,hub_name,name,fp`.
- `GET /api/v1/tls/identity` returns `v,id,name,fp`.
- Mobile requests carry `X-Device-Token`. Account login/registration remains
  separate and issues a device-bound JWT; protected user routes also require
  `Authorization: Bearer <JWT>`. WebSocket account tokens accept the existing
  `token` query parameter or the Authorization header.
- `DELETE /api/v1/devices/self` requires the device token and revokes only that
  device. It grants no account or owner privilege.
- Both local HTTP and public web HTTPS reject device credentials and signed
  device-bound JWTs. Mobile credentials remain isolated to the mobile listener.
  Forged forwarded headers cannot turn a LAN caller into a local HTTP peer.
- Owner APIs under `/api/v1/owner/secure`: `POST /pairing`, `GET /devices`,
  `DELETE /devices/{id}`, `GET/POST /routers`, `DELETE /routers/{id}`.

Pairing consumption and token creation commit in one SQLite write transaction.
Incorrect codes never consume valid sessions. Tokens contain 256 random bits;
storage retains only SHA-256 of the raw token. Per-peer (10 attempts/120 seconds)
and global (100 attempts/120 seconds) throttles are persistent. Device revocation
is checked by every new request and every 250 ms for active mobile requests,
SSE and WebSockets.

All Hub → Router REST, model catalogs, worker streaming, audio and WebSocket
requests use `pinned_router` with HTTPS/WSS, connection-level SPKI validation
before headers/body, no redirects and no environment proxies. Wrong pins never
send Router credentials or request bytes. Removing trust closes active Router
transports within the watcher interval. Direct local TTS HTTP fallback has been
removed: all Hub audio text goes through the trusted HTTPS Router.

## Deployment and verification

Configure `ORION_MOBILE_ADVERTISE_HOST` to the host LAN IP for container
deployment. Docker bridge multicast may require host-network support or an mDNS
reflector; publishing a TCP port alone does not forward LAN multicast. Native
advertisement refreshes changed addresses every 15 seconds. Trusted Router
addresses are rediscovered by UUID on the same interval and remain pinned.
If multicast is unavailable, QR/bootstrap HTTPS addresses still work.

The self-contained Hub tests exercise atomic multi-connection pairing races,
invalid/expired codes, rate limiting, token-hash persistence, revoke, restart,
same-key certificate renewal, corrupt-key fail-closed and no trust resurrection
from concurrent discovery. Cross-repository live TLS verification is documented
separately in `phase6-integration.md`. Physical LAN/firewall and Android/iOS
device discovery need deployment verification; the owner web UI has automated
API regression coverage, but has not been visually exercised in a browser here.

## Starting this checkout

Code changes are in the Codex worktree, not automatically deployed to the primary
Hub checkout or existing processes. Install/sync `services/hub/pyproject.toml`
dependencies and start from this worktree with `python manager.py start local`,
or set `PYTHONPATH` to this checkout's `services/hub/src` plus repository root,
then run `python services/hub/run_local.py` using the Hub virtual environment.
Existing Redis/PostgreSQL settings and administrator/JWT secrets are still
required. Ensure older processes have released ports 80/8909/8910/8911/8443 before starting.
Standalone web listeners use `python -m orion.api.web_server --port 8909` and
`python -m orion.api.web_server --public --port 8911`; mobile uses
`python -m orion.api.mobile_server`. Compose starts all three TLS listeners with
the persistent shared security volume. Load-balanced Nginx uses TLS on its
published port and verifies the HTTPS upstream against the same identity cert.

Open `http://127.0.0.1:8910/manage` for local management or `http://localhost/`
for the launcher. `/hub` and `/panel` preserve existing local features;
`/orionrouter` redirects to `http://127.0.0.1:20128/dashboard`. LAN browser chat
uses `https://<LAN-IP>:8911/`; Flutter mobile pairing uses HTTPS 8443. Browsers use their own
certificate trust stores. Local loopback HTTP needs no certificate import.
For remote HTTPS, an untrusted self-signed certificate causes a browser warning
or a blocked Fetch request. Never import/share `hub-key.pem` or disable
certificate validation.
Leaf-certificate renewal/SAN updates may require re-importing the public cert or
reloading Nginx, depending on browser/platform trust policy. Mobile/Router SPKI
pins remain stable because the private key is unchanged. This work does not
automatically modify OS certificate stores.


The local launcher uses `ORION_LOCAL_SHORTCUT_PORT=80`; its optional disable flag
is `ORION_LOCAL_SHORTCUTS=0`. Local management uses `ORION_LOCAL_PORT=8910` and
network browser TLS uses `ORION_WEB_PORT=8911`. Run the local UI standalone with
`python -m orion.api.web_server --local --http-loopback --host 127.0.0.1 --port 8910`.
This explicit runner refuses public/non-loopback binds. HTTP owner cookies use
a separate `_local` name, HttpOnly/SameSite strict and scoped paths; HTTPS owner
cookies remain Secure. OwnerAPI retains the panel mount prefix. The local
directory returns this PC's HTTP loopback `/hub` row but all LAN rows remain
HTTPS; advertisements without scheme=https cannot become LAN entries. Web/mobile
discovery now share the persistent TLS UUID; first migration retains an existing
mdns-id when no TLS UUID was saved.



Final browser-trust policy: certificates are generated and persisted by the
Hub/Router themselves, but neither installation nor startup modifies Windows
certificate stores. No automatic root import, certificate-dialog automation,
UAC request or certificate-validation bypass is performed. Native Flutter and
Hub-to-Router traffic verify the saved SPKI pin independently of OS trust.
Flutter Web and browser management use normal browser certificate validation:
an untrusted self-signed certificate can block access or show a warning.
Accepting a browser warning does not cryptographically prove server identity.
Local HTTP requires no OS certificate changes. Native TLS/SPKI remains mandatory.

Same-computer native installation bootstrap: Router's native installer registers
its installation root and public certificate/UUID paths in the current user's
Orion/router-installation.json. Hub validates the registration and paths, reads
the local public key, and verifies the live loopback HTTPS UUID and SPKI before
recording trust. Startup and background retries support either installation
order. Advertised mDNS pins, PATH executables and arbitrary development checkouts
do not enroll trust. A selected remote Router, changed key/UUID, or explicit
trust revocation prevents automatic enrollment; owner confirmation remains
required in those cases. No Windows root-certificate import occurs.

Local browser account settings remain authenticated by the account JWT even if
an owner cookie from the same host is also present. Owner panel routes keep their
separate local, owner-session and CSRF controls. Router dashboard discovery
offers HTTPS navigation hints and never enrolls Hub trust or forwards admin keys
to a discovered peer.
