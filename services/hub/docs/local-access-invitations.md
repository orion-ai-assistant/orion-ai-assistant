# Local owner home and invite-only registration

Open `http://localhost:8909/` on the owner PC. Home links to the dashboard,
People and invitations (`/manage/`), and the existing technical admin (`/admin/`).
Both management screens explicitly ask for the same configured `ADMIN_API_KEY`
on entry. The existing password, including `orionadmin` if currently configured,
is preserved. It is not embedded in HTML or stored in browser storage. The
server issues an opaque, 15-minute HttpOnly/SameSite=Strict cookie backed by
Redis. People sessions do not authorize technical admin operations. Existing
bearer user sessions remain separate. Changing the administrator key or ending
the owner session invalidates that session. Browser owner requests must have
the exact local origin; forwarded and non-loopback owner access is rejected.

## Private vs public entry points

Native `run_local.py` runs the owner Hub on loopback port `HUB_PORT` (8909), and
`orion.api.main:public_app` on `ORION_PUBLIC_PORT` (8910). Missing/default JWT
signing secrets are generated once and saved to `services/.env.global.local`
before launch; custom secrets and the administrator password are preserved.
Generating a new JWT key invalidates old user tokens; users sign in again.
PostgreSQL and an already running Redis are reused.

The private listener does not host dashboard files. Home/people chat links
point to the public port; `/dashboard` and `/dashboard/*` on the private port
redirect there, preserving path and query. The public listener's `/` redirects
to `/dashboard/`. Private management pages serve their countdown script from
`/owner-assets/auth_wait.js`, keeping owner login entirely on the private port.

The private listener also rejects normal client APIs with 403: user login,
registration/profile, chats/messages/history, SSE, tools/models, and audio
WebSocket connections. A valid user JWT or administrator credential does not
enable these client APIs on the private listener. Shared settings/schema paths
require administrator authority there; ordinary users access them on the public
listener. Desktop browser/session controls on the private listener also require
administrator authority. `/health` and `/ready` remain operational probes, not
evidence that a port supports Flutter/chat. Flutter's saved Hub URL must use
the public port (8910 by default); changing the Flutter build default is deferred
client work. No API redirect silently forwards login passwords to another port.

Docker publishes private `api` on **127.0.0.1:HUB_PORT only**, and `public_api`
on `ORION_PUBLIC_PORT` (default 8910). The private container flag allows Docker's
private NAT peer only in a container, with a loopback Host and no forwarded
headers. This relies on the supplied host-loopback publishing and trusted
Docker network; do not expose that private service directly. Public replicas
and the load balancer use `public_app`, not the owner app.

Point any tunnel/reverse proxy at **8910**, never the private listener at 8909.
The public app uses a path allowlist and blocks the owner home, owner assets, admin,
owner APIs, old approval APIs, browser controls, docs and administrator-key
credentials even if the tunnel peer is localhost. Ordinary authenticated user
settings endpoints remain available (`/api/v1/admin/settings` and schema are
legacy names). The dashboard is `/dashboard/`; `/health` is public.
An externally forwarded request to the private owner app is also rejected,
but no application can reliably distinguish a proxy deliberately configured
to strip all proxy headers, rewrite Host to localhost, and use the private
listener. Correct listener isolation is part of the deployment contract.

## Inviting and managing people

Unlock People and invitations, choose 30 minutes, 24 hours or 7 days, then
create a one-account invitation. The PC's current IPv4 network address and
`ORION_PUBLIC_PORT` are detected automatically; no address input is needed.
The detected address is displayed for reference. Copy/share the URL or scan
the QR. Phone and PC must be on the same reachable LAN for a LAN invitation.
QR generation is local, with no third-party QR service. There is no localhost
phone link. A configured `ORION_PUBLIC_URL` takes precedence for tunnel/domain
deployments. Docker requires this setting because a container cannot discover
the host's phone-reachable address reliably. Native detection uses a UDP route
probe without sending packets, with hostname IPv4 resolution as a fallback.
No network address results in a clear error rather than an unusable loopback
link. The address is resolved again on each invitation, so IP changes are
picked up for newly created links; old links still contain their original IP.

The link is `<public-url>/dashboard/#invite=<random-token>`. Its secret is in
the URL fragment, not the request path/query or Referrer. PostgreSQL stores
only its SHA-256 digest. Tokens are generated from 32 random bytes. Creating
the link constitutes the owner's approval for one account. The recipient
only sees account creation; no owner password or admin page is involved.
Account insert with `is_active=true` and invitation consumption occur in one
transaction under a row lock. A conflict in username rolls back consumption.

## Mobile web dashboard

At widths up to 760px, navigation becomes a menu drawer with chat/settings
access, backdrop, close button, Escape and focus containment. Selecting a chat
or tab closes it; switching to desktop restores the normal sidebar. The chat
composer uses the visible viewport height (VisualViewport when available),
so the browser bar and software keyboard do not push its controls below the
screen. Safe-area padding, 44px touch controls, scrollable messages/settings
and 16px form inputs improve small-screen use. Existing desktop styles remain
in place. Browser/mobile hardware verification is still required: no browser
automation surface is connected here. Focused Python checks: 56 passed;
countdown/registration/mobile behavior checks: 6 passed. After management-only
API enforcement, focused Python checks: 58 passed, including valid-JWT denial
on the private port, WebSocket rejection and public-user/private-admin settings.
Revoked, expired and used invitations cannot create users. Existing pending
accounts can still be approved from the People list; active accounts can have
access disabled. New open registrations are rejected before account creation.
Disabling an account denies new authenticated requests and WebSocket handshakes;
already open streams are not terminated yet (planned session-revocation work).

## Flutter contract (client work deferred)

Tracked follow-up: [Flutter invitation link handling](../../../files/flutter-davet-yonlendirme.md).
This is deferred work; the current QR opens the web dashboard.

- Open the invite URL using an app/universal-link handler later. Without that
  Flutter integration, QR/link opens the web dashboard. Hub cannot determine
  whether the Flutter app is installed; no fabricated custom URI is launched.
- `POST /api/v1/auth/invitations/preview` JSON
  `{"invitation_token":"..."}` returns `label` and `expires_at`, or 410.
- `POST /api/v1/auth/register` JSON
  `{"username":"...","password":"...","invitation_token":"..."}` returns
  201 `{"access_token":"...","token_type":"bearer"}`. Save that token using
  the existing login flow and fetch `/api/v1/auth/me`; no second login required.
- Missing invite: 403. Username conflict: 409 (invite stays valid). Invalid,
  revoked, used or expired invite: 410. Field validation: 422. DB outage: 503.
- Existing `/auth/login` stays usable. Account and owner limits are 5 attempts
  per 30-second window; login IP limit is 10. Registration limit is 10 and
  invitation preview limit 30 per IP/window. All login attempts count, including
  successful ones. Redis Lua returns the actual remaining TTL in `Retry-After`
  on 429; CORS exposes that header. Countdown waits until a wall-clock deadline
  so background tabs do not extend it. Redis failures reject login with 503.

## Validation and limitations

Owner/auth/CORS HTTP tests, atomic redemption/rollback unit tests and real
PostgreSQL/Redis tests cover wrong passwords, countdown TTL, expiry, logout,
key rotation, permission scope, CSRF, hostile origins, public/loopback-tunnel
isolation, encoded admin-key query parameters, QR output, invite-only signup,
auto-login, duplicate username, revoked/expired links and concurrent redemption.
Live tests create a UUID schema and Redis key namespace and remove them;
`ORION_LIVE_ACCESS_TESTS=1` opts in. During initial live validation a connection
mock was incomplete and three disposable test users/invites were created in
the main schema; those exact records were verified and removed. Both connection
bindings are now patched and the corrected isolated tests pass.

The baseline already has nine Python browser/media failures. The existing Node
scripts also have missing test mocks; comparing the same command with HEAD
shows 30 pass/3 fail before, and 33 pass/3 fail after (new tests all pass), with
the same existing asynchronous script errors. No user TTS or Flutter edits are
included. Docker engine and browser automation surfaces are unavailable here,
so Docker startup, real mobile QR scanning and screenshot/layout QA remain
manual checks; no screenshots or actual Flutter app opening are claimed.

Final checks: focused security/owner/invitation/CORS 52 passed; isolated real
PostgreSQL/Redis 3 passed; new UI countdown/registration tests 3 passed. Full
Python suite: 257 passed, 13 skipped, the same 9 existing failures, 53 subtests.
Compose YAML and private/public listener command checks passed. Native activation
was verified after restart: private home/people/admin return 200, owner login
with the existing configured key and logout work, unauthenticated people API
returns 401, and public people/admin/owner API return 403. Public root now redirects
to the dashboard, and the private dashboard redirects to the public port. Public dashboard
and both health endpoints return 200; registration without an invite is rejected.
Six wrong login attempts return 429 with `Retry-After: 30`. Native startup reuses
the existing databases and persists its new signing key; previous JWTs require
login again. No tunnel configuration exists in this repository: an external
tunnel must target the public listener on 8910, not the private owner listener.
