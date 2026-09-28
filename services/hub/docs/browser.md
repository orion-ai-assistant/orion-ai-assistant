# Existing desktop browser connection

Orion uses only the user's existing browser profile. On native Windows Hub startup,
it can launch a saved browser minimized if that browser is not already running.
It never creates a login profile, imports cookies or closes the personal browser.
Raw CDP uses aiohttp; no Playwright, extension or downloaded browser is required.

## Setup

1. Run the Hub and worker locally under the same OS account as your browser.
2. In Orion Settings → General → Tarayıcı, click **Tarayıcıya bağlan**. Orion
   selects the browser running the dashboard when identifiable, or the foreground
   supported Windows browser; the user may choose another installed browser.
3. Orion opens `<browser>://inspect/#remote-debugging` in a new window of the
   selected browser. On Windows it tries to toggle only the inspect page checkbox
   named "Allow remote debugging for this browser instance" using UI Automation.
   If this desktop control is unavailable, the user toggles it on the open page.
   The browser's subsequent Allow dialog is automatically approved via UI Automation
   on Windows, or the user can confirm it manually. Orion minimizes only the new
   settings window after approval when it can identify that window.
   This grants broad profile access, not permission limited to a single tab.
4. Enable Google AI Modu, YouTube or Tarayıcı in the function selector.

The API owns one persistent Chrome connection, shared with workers through a
private authenticated loopback bridge. Settings opens the connection once; tool
calls only use that connection and never initiate another permission prompt.
The bridge is bound to 127.0.0.1 on an ephemeral port, has a random capability,
rejects browser Origin requests and does not accept arbitrary remote endpoints.
Its descriptor is local runtime data. Run one native API owner for this checkout.

The browser can be minimized; a foreground window is not required for reading.
The browser process must still be running. Browser/Hub shutdown or revoking
permission ends the connection; a new explicit connection may require Chrome's
approval again. This mechanism cannot provide permanent permission or operate a
browser process that is not running. Status checks the live shared socket, not
just a stale DevToolsActivePort file. An uncertain action is never replayed.
At startup Orion restores a saved browser in a minimized state if no process of
that browser is running, without attaching or triggering a hidden consent dialog.
It can connect when the user next clicks **Tarayıcıya bağlan**. If the browser
does not expose the settings window to the Hub's desktop session, Orion leaves
that window visible rather than guessing which personal window to minimize.

The explicit **Devam et** recovery action reconnects when necessary and checks
Browser.getVersion before clearing the manual pause or reporting readiness.
Failed setup retains the selected browser, so refreshing after a denied Brave
request cannot silently switch the selection to Chrome. Connection failures
distinguish timeouts, rejected handshakes and unreachable browser endpoints.

The browser decides which default/active profile it exposes. Only that profile's
site sessions are available. Signing into Google does not automatically authorize
all third-party sites offering Google login; such sites may need their own consent
and session. Incognito has separate cookies and is not used. Ctrl+N opens a normal
window; Ctrl+Shift+N opens an incognito window when browser policy permits.

## Visibility and lifecycle

Google AI creates an ephemeral `hidden: true, background: true` target in the
existing browser context, with a 1280×900 viewport. It has no tab-strip entry and
no new window; it is closed after the request, including failures. There is no
visible fallback if hidden targets are unsupported. This is **not incognito**:
normal cookies and site storage are shared, and it is not a privacy/history mode.
Browser permission dialogs remain visible and are not suppressed.

Verification pauses automation and returns a recovery card. It never automatically
activates a window. The user explicitly clicks **Google doğrulamasını aç** to open
the query visibly, completes verification, clicks **Devam et**, then retries.
YouTube uses a regular owned tab created in the background with `focus: false`.
Opening a requested video does not activate its tab, so the user can remain in
Orion while audio plays. General browsing uses a visible owned tab. Other
personal tabs are not navigated or minimized. Background playback still depends
on the site's media/autoplay rules; the tool verifies actual playback state.

Stopping Hub closes its socket connections through process shutdown and leaves
the personal browser and visible tabs open. Old Orion profile directories are
left untouched but never reused. Existing saved configurations require explicit
setup for the new connection mode. The shutdown gate still prevents new work
while Hub is stopping. Previously launched legacy windows can be closed manually.

## Implementation and validation

Discovery reads only DevToolsActivePort from the selected browser's standard user
data directory. It strictly validates the numeric port and browser endpoint path,
then connects to numeric loopback WebSocket directly. The opt-in endpoint need not
expose /json/version. No account data is read from profile files.

Per-user and shared-endpoint file locks serialize operations. Visible targets are
tracked by targetId scoped to endpoint UUID. All commands have deadlines, and
uncertain input is never replayed. Current helpers poll state and discard unused
CDP events on long-lived connections. Generic helpers do not yet support downloads,
cross-origin frames or closed shadow roots.

Unit tests also launch a separate worker process and verify it reuses the Settings connection without another Chrome connection. Bridge tests cover authentication, Origin rejection and no implicit reconnect. Unit tests cover endpoint validation, legacy migration, manual pauses, leases,
target reuse, hidden cleanup and no automatic foregrounding. With
ORION_BROWSER_TESTS=1, browser tests launch an isolated test-only profile under
.tmp/browser-tests, verify synthetic cookie sharing and hidden-page DOM reading,
and verify Hub shutdown does not close the test browser. Personal accounts and
the browser's manual approval dialog are not automated in these tests.

References: [Chrome opt-in connection](https://developer.chrome.com/docs/devtools/agents/use-cases/auto-connect),
[Target protocol](https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/Target.pdl).
