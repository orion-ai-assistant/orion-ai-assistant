# Phase 1 security

Scope: Hub backend only. No UI, refresh tokens, tunnel, or Flutter edits.

## Changes and configuration

- All privileged admin operations require an explicitly configured, matching `ADMIN_API_KEY`; an absent/empty key never grants access. Comparisons use `hmac.compare_digest`. Settings schema is authenticated metadata accessible to ordinary users; `/health` remains public.
- Startup rejects an empty JWT secret everywhere and the development JWT secret in remote/production, Docker compose, or non-loopback CLI binds. Local development logs an explicit warning. Configure a strong random `JWT_SECRET_KEY` before exposed deployment. Both compose API services set `ORION_HUB_MODE=remote`; native `run_local.py` already binds loopback. Programmatic servers/proxies must explicitly set `ORION_HUB_MODE=remote` because their bind host cannot be inferred from CLI arguments.
- Registration creates pending (`is_active=false`) accounts. Existing approved/active accounts are retained. The database default is migrated to false and inserts explicitly use false. The PC owner approves a known username using `PATCH /api/v1/auth/users/{username}/approval`, `X-Admin-Key: <configured owner key>`, JSON `{"approved":true}`. JSON false deactivates the account. HTTP JWT and audio WebSocket handshakes check the stored account is active; a JWT with subject `global` cannot grant admin rights.
- Login uses shared Redis Lua fixed-window counters per peer IP and exact account name: ten attempts per 15 minutes, the eleventh returns 429 with `Retry-After`; all attempts count, including successful ones. Redis failure rejects login with 503. The endpoint does not directly trust request forwarding headers. Trusted proxy configuration determines the ASGI peer address; shared NATs/proxies can share an IP limit. Username Unicode compatibility is retained; passwords longer than 72 UTF-8 bytes return 422 rather than bcrypt truncation/errors.
- Existing chat ownership checks protect state, history (including inline file attachments), rename/delete, and chat tools. Message submission to a foreign chat now returns 403/404 instead of an accepted failed job. SSE rejects foreign chat/user identifiers before subscribing and uses only the authenticated account channel. Explicit stop-turn IDs must match the accessible chat's active turn. Browser manager always hashes string account IDs, preventing a 64-character hex username from aliasing another account's profile directory; internal trusted Path restores remain supported.
- CORS retains the explicit `ORION_CORS_ORIGINS` allowlist plus loopback development origins and rejects `*` configuration. No credentialed wildcard is enabled.

## Existing client behavior and remaining risks

Hub dashboard stores `orion_token` in localStorage (`ui/js/api.js`). Flutter stores the token with FlutterSecureStorage under `orion_token:$hubUrl` (`main_app/app/lib/core/network/orion_api.dart`, inspected read-only); browser storage remains within the same-origin JavaScript/XSS trust boundary. No storage change was made. A later cookie/HttpOnly design needs coordinated CSRF, CORS, SSE and client work. [OWASP guidance](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html) explains JavaScript access to localStorage session data.

The existing dashboard registers then immediately attempts login; pending accounts receive an approval-required error detail and the auth modal stays open. Approval has an API workflow, not new UI. Query-string tokens/admin keys remain supported for current stream clients and can enter URL logs/history. Existing 24-hour access-token lifetime is unchanged. Active streams are checked at connection time; periodic stream revocation and device/session controls belong to Phase 3. Owner-approved users on this PC may use the existing shared desktop browser session; hashed per-account tool/config directories do not isolate the operating system browser's cookies. Pending registration is not throttled in this phase. Strong non-default secret quality is an operator responsibility.

## Validation

Final checkout: **222 passed, 9 failed, 10 skipped**, 53 subtests. The **14 new Phase 1 security tests all pass**; existing CORS tests also pass.

Baseline commit `a47802172763098325f94846969f8df4a7d6280e`, extracted through `git archive` into an isolated directory and run with the same interpreter: **208 passed, 9 failed, 10 skipped**, 38 subtests. The same nine failures occur before and after: seven `test_browser_manager.py` cases (removed `open_inspect_window` references and outdated endpoint discovery assumption), `test_browser_tools.py::BrowserToolTests::test_browser_control_uses_selected_existing_tab` (expected call lacks existing `inactive=True`), and `test_media_efficiency.py::MediaEfficiencyTests::test_browser_read_is_compact_with_explicit_detail_option` (AsyncMock snapshot used as a dict). No new regression was observed; unrelated browser tests were left unchanged.

Completion criteria:

| Criterion | Result |
| --- | --- |
| Foreign chat/file-history/tool/stream identifiers return 403/404 | Passed |
| Empty/unconfigured admin key cannot grant admin access | Passed |
| Remote/default-secret startup refused; local default warning | Passed |
| Pending registration cannot login/use signed JWT until approval | Passed |
| Login rate limit activates after ten attempts | Passed |
| Existing tests not regressed against baseline | Passed (same nine pre-existing failures) |
| Entire existing suite is green | Not met at baseline or final; nine pre-existing failures |
| Changes and residual-risk report | Passed |
 Automated tests use HTTP ASGI requests, real JWT encoding, and mocked DB/Redis adapters; registry SQL insert/default migration is asserted. They do not prove live PostgreSQL migration or real Redis TTL/concurrency behavior. Lua INCR+EXPIRE follows the [official Redis rate-limiter pattern](https://redis.io/docs/latest/commands/incr/). No running user server or worker was restarted, and no production data was changed.

Commands run in the isolated checkout with PYTHONPATH pointing to its Hub src and repository root and the existing Hub virtualenv interpreter: `python -m pytest services/hub/tests -q -p no:cacheprovider --tb=short`. Full-suite testing requires unsandboxed access to temporary fixture directories; the initial sandbox run produced permission errors and is excluded from regression evidence.
