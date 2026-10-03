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

Docker publishes private `api` on **127.0.0.1:HUB_PORT only**, and `public_api`
on `ORION_PUBLIC_PORT` (default 8910). The private container flag allows Docker's
private NAT peer only in a container, with a loopback Host and no forwarded
headers. This relies on the supplied host-loopback publishing and trusted
Docker network; do not expose that private service directly. Public replicas
and the load balancer use `public_app`, not the owner app.

Point any tunnel/reverse proxy at **8910**, never the private listener at 8909.
The public app uses a path allowlist and blocks the home, owner assets, admin,
owner APIs, old approval APIs, browser controls, docs and administrator-key
credentials even if the tunnel peer is localhost. Ordinary authenticated user
settings endpoints remain available (`/api/v1/admin/settings` and schema are
legacy names). The dashboard is `/dashboard/`; `/health` is public.
An externally forwarded request to the private owner app is also rejected,
but no application can reliably distinguish a proxy deliberately configured
to strip all proxy headers, rewrite Host to localhost, and use the private
listener. Correct listener isolation is part of the deployment contract.

## Inviting and managing people

Unlock People and invitations; enter the phone-reachable public URL, such as
`http://192.168.1.20:8910` or your HTTPS domain. Choose 30 minutes, 24 hours or
7 days and create a one-account invitation. Copy/share its URL or scan the QR.
QR generation is local, with no third-party QR service. There is no localhost
phone link. The public URL can be prefilled with `ORION_PUBLIC_URL`.

The link is `<public-url>/dashboard/#invite=<random-token>`. Its secret is in
the URL fragment, not the request path/query or Referrer. PostgreSQL stores
only its SHA-256 digest. Tokens are generated from 32 random bytes. Creating
the link constitutes the owner's approval for one account. The recipient
only sees account creation; no owner password or admin page is involved.
Account insert with `is_active=true` and invitation consumption occur in one
transaction under a row lock. A conflict in username rolls back consumption.
Revoked, expired and used invitations cannot create users. Existing pending
accounts can still be approved from the People list; active accounts can have
access disabled. New open registrations are rejected before account creation.
Disabling an account denies new authenticated requests and WebSocket handshakes;
already open streams are not terminated yet (planned session-revocation work).

## Flutter contract (client work deferred)

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
is verified separately with HTTP checks after restart.
