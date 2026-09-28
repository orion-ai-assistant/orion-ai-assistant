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

## Setup and browser lifecycle

See [Existing desktop browser connection](browser.md). Google AI uses the same
existing signed-in browser context as YouTube, through raw CDP. There is no
separate login profile or Playwright/headless browser.

Each query uses an ephemeral hidden target, which has no tab-strip entry or visible
window. The reader polls the AI container and streams Markdown snapshots through
`google_ai_direct_stream`. No shared network/media filters are installed.

CAPTCHA, sign-in and consent return `verification_required` and pause automation.
The hidden target is cleaned up; no window is brought forward. Click **Google
doğrulamasını aç** to open the query visibly in the existing browser, complete the
interaction, click **Devam et**, and resend the question. Unsupported hidden-target
creation produces an error instead of opening a visible fallback window.

Old profile directories are left untouched and unused. Cookies are never copied.
Google sessions can expire and individual sites can require separate authorization.
