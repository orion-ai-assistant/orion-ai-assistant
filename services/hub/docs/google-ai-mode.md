# Google AI Mode (experimental)

The separate **Google AI Modu** category contains `google_ai_mode({"query":"..."})`.
It opens Google Search with `q`, `udm=50`, and `hl=tr`, then reads Google's generated
answer and available source links from its AI-specific answer container. It does
not use Gemini API, DDGS, an intermediary paid API, or a second Orion model.
The existing **Web araması → Web'de araştır** tool stays separate.

Answer structure is preserved as Markdown in the single `answer` field, with
`answer_format: "markdown"`: headings, bold/italic emphasis, nested lists and tables.
Visible text is escaped before structural Markdown is generated; raw Google HTML,
scripts, hidden controls and styles are not returned. Both direct streaming and the
final tool result use this format. Existing plain-text records remain compatible.
This does not install a Markdown renderer in the dashboard; formatting syntax is
retained for a future renderer, not converted into Google's visual design.

This is a browser integration, not an official Google AI Mode API. Google's UI can
change, and signed-out availability varies. No generic search-page text is used
as a substitute if the AI answer container cannot be found. Answer completion is
inferred from stable text for two seconds after a completion marker, or four seconds
otherwise, with no visible busy marker; this is a heuristic,
not a server-provided completion guarantee. Source links may be incomplete if Google
places them outside the answer container. Returned answers are Google-generated
claims, not independently verified facts.

## Native setup

From the repository root (PowerShell):

```powershell
services/hub/.venv/Scripts/python.exe -m pip install './services/hub[google-ai]'
$env:PLAYWRIGHT_BROWSERS_PATH = '0'
services/hub/.venv/Scripts/python.exe -m playwright install chromium
```

Restart the Hub API and worker, refresh the tool UI and enable **Google AI Modu →
Google AI Modu ile sor**. The category is off by default. The worker and local login
helper need the browser dependency; catalog discovery imports it lazily. The default browser path
is Playwright's hermetic package directory (`0`), so worker and installation must
use the same virtual environment. An explicit `PLAYWRIGHT_BROWSERS_PATH` overrides it.

The optional `worker.google-ai.Dockerfile` installs Linux browser dependencies and
runs the worker as a non-root user. To use it, point the worker's compose
`build.dockerfile` at `services/hub/worker.google-ai.Dockerfile` and rebuild. Chromium
sandboxing remains enabled; the host/container must support it. The existing default
worker/API images do not acquire a browser dependency automatically.

## Behavior

- Without setup, opens an isolated, ephemeral, signed-out Chromium context. After
  explicit local setup, reuses a persistent profile specific to the authenticated
  Orion user. Personal Chrome profiles are never imported.
- Normal searches are always headless. The user agent is derived from the installed
  full Chromium, with its `HeadlessChrome/` token normalized to `Chrome/`; no version
  or platform is hardcoded. The default headless user agent received Google's
  unavailable page in local testing, while this compatible user agent loaded AI Mode.
- Only Google-owned host suffixes used by this page are allowed for requests; images,
  fonts and media are skipped. Source URLs are returned, not visited.
- Does not click consent, login or CAPTCHA controls or attempt to bypass them.
- Local session setup opens an unfiltered visible Chromium window so the user can
  sign in or complete verification themselves. Closing that window releases the
  profile for searches; leaving it open returns `busy`. The setup window expires
  after 15 minutes. Login and subsequent searches use the same full Chromium build.
- One browser call at a time per worker; overlapping calls return `busy`.
- A 25-second operation deadline sits inside the tool runner's 30-second timeout;
  cancellation closes the browser. No retry or automatic provider fallback is used.
- Calling either research tool completes the web phase for the current user request:
  both `research_web` and `google_ai_mode` are then removed from available schemas.
- Failed Google calls finish with a deterministic error message, not another model
  completion that could invent research or sources. The tool activity is marked error.
- Returns up to 5,000 UTF-8 bytes of answer text, up to six source links, truncation
  flag, search URL and elapsed time. `partial` means the answer was extracted but
  source links were not available.
- Failures are explicit: `not_configured`, `blocked`, `interaction_required`,
  `unavailable`, `timeout`, `provider_error`, or `busy`; no invented answer is returned.

## Local Google session

### Direct display (optional)

In **Ayarlar → Google AI Mode → Google yanıtını doğrudan akıt**, enable
`google_ai_direct_stream` (default: false). The initial Orion model still selects
the tool and constructs the query. While Google generates, changed answer text is
forwarded as snapshots approximately every 500 ms; revisions replace the previous
draft rather than appending duplicates. This reads the rendered DOM, not Google's
private token stream. When finished, extracted source links are appended and no
second Orion model call is made. The final direct response is saved in normal chat
history. For later model requests, its display-only assistant copy is omitted:
the answer and sources are sent once, in the paired tool result. The assistant
tool-call message and matching `tool_call_id` remain intact. Existing saved direct
responses are also deduplicated when they exactly match the rendered tool result;
normal Orion summaries are retained. Stop/cancellation closes the browser and retains the visible partial text
as a stopped turn. A failed provider replaces its unfinished preview with an error.

When disabled, the tool waits for a stable answer and returns it to the Orion model
as before. The setting does not enable the Google tool automatically and does not
change the independent normal web-research tool.

Live streaming validation: first text at 5,001 ms, five DOM updates before completion,
and an `ok` result with six sources at 13,110 ms. No visible browser window was opened.

Restart both the native Hub API and worker after installing full Chromium. In
Settings, open **Google Search / AI Mode → Google hesabıyla giriş yap**, or use
the same controls under **Fonksiyonlar → Google AI Modu**. Sign in directly on
Google's page, then close the Chromium window. Use **Durumu yenile** to check
whether the profile is available. A saved profile does not prove authentication;
Google may reject automated-browser sign-in or require CAPTCHA again.

Blocked and timed-out tool results include session controls. Choose **Google
doğrulamasını aç** to open the failed query, complete verification, close the window,
and resend the question. In settings, the editable test query defaults to a simple
capital-city question; login also continues to this search. There is no automatic
replay of the failed tool call.

Profiles live in `services/hub/data/google-profiles/<hashed-Orion-user>/`, covered
by the existing `data/` gitignore rule. Chromium manages cookies/storage; they are
never returned over HTTP or copied into logs. These directories contain sensitive
session data and should stay local, outside backups/shared folders. Searches made
with a signed-in profile use that Google account. **Google oturumunu sil** removes
only the current Orion user's local profile; it does not revoke Google's sessions
on other devices. Cross-process OS locks protect search, setup and deletion.

The API and worker must run from the same native installation and OS account.
Session endpoints require Orion authentication and loopback access with a desktop;
Docker and remote-client browser login are unsupported. The login helper runs in
a separate process so native Windows API event-loop limitations do not prevent
Playwright subprocess startup. Install the optional browser dependency in the API
environment too if API and worker use different virtual environments.

One-call manual check (no LLM invocation):

```powershell
$env:PYTHONPATH = 'services/hub/src;.'
services/hub/.venv/Scripts/python.exe services/hub/scripts/check_google_ai_mode.py --query 'dünyadaki en büyük canlı nedir' --output .tmp/google-ai-mode-live.json
```

References: [Google AI Mode help](https://support.google.com/websearch/answer/16011537)
and [Playwright browser installation](https://playwright.dev/python/docs/browsers).

## Local validation — 2026-09-27

One focused offline test passed (separate catalog/default-off behavior, source-link
normalization, safe query encoding and explicit failure results). Playwright 1.63.0
and its Chromium headless shell were installed into the project virtual environment.

One live call using `dünyadaki en büyük canlı nedir` reached Google's traffic/CAPTCHA
challenge and returned `blocked` in 4,073 ms. No CAPTCHA was solved, personal browser
profile imported, or automatic retry attempted. This validates honest failure reporting,
not successful live answer extraction at that stage. The original local report is
`.tmp/google-ai-mode-live.json`.

Local session update: six Google tests and 19 existing tool tests passed, as did the
tool-selector UI test. A real full-Chromium check verified a synthetic persistent
cookie survives browser restart and the settings login/verification/delete buttons
dispatch the expected requests. Real Google account login and successful AI Mode
extraction after CAPTCHA still required manual validation at that stage.

Reader fix: support both `main-col` and `aimc` answer containers, ignore hidden/UI
text, accept short answers, and decode citation records from HTML-escaped streaming
comments. A subsequent live headless reader check returned `ok`, 2,192 answer
characters and three source links in 12,903 ms using the existing local profile.
The report is `.tmp/google-ai-reader-live.json`. This validates that particular
query/session; it is not a guarantee against future Google UI changes or challenges.

Markdown display: the reader tracks inherited bold/italic formatting while walking
the DOM, so nested equivalent elements do not introduce duplicate delimiters.
This is structural conversion, not textual correction or model rewriting. Existing
answer size limits and source extraction limits still apply.
The tool query and verification input accept 1–2,000 characters. This is Orion's
application limit, not a documented Google limit, and does not limit the user's
chat message or the length of Google's answer.

Assistant content now renders through locally vendored Marked and DOMPurify
(versions, download origins and SHA-256 hashes are in `ui/js/vendor/versions.json`).
Only text-formatting tags and safe external links are allowed. Streaming renders
are coalesced at 100 ms and flushed on completion; history uses the same renderer.
Raw Markdown is retained separately for snapshot offsets and persistence. Tool,
thinking, audio and metrics elements are not replaced by rendered answer HTML.
No additional model message is introduced.

Validation: five Google DOM tests, three real-browser Markdown UI tests, six direct
stream tests and six history projection tests passed. A local visual check covered
headings, emphasis, lists, tables, sources and code blocks. No new live Google query
was used for this formatting change.
