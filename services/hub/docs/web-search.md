# Web research tools

The `web_search` category exposes only **Web'de araştır (`research_web`)**.
Search and page readers remain internal implementation helpers, not selectable model tools.
Saved selections using `search_web`, `read_page` or `read_pages` migrate on load to
`research_web`. An explicitly saved `research_web=false` is respected, and a disabled
web category stays disabled. Old queued jobs also map their enabled tool IDs to research.
Restart the hub API/worker after deploying these changes so both discover the new catalog.

## Research in one tool call

```json
{"query": "Türkiye teknoloji gelişmeleri", "recency": "day"}
```

Only `query` is required. The backend performs one general DDGS search (up to 15
candidates), deduplicates URLs including common tracking variants, prefers distinct
domains in search ranking order, and reads up to five sources concurrently. It
reuses the guarded page reader. Selection is a simple ranking/diversity heuristic,
not a guarantee that these are the objectively best sources.

The model receives only compact source records: original URL, title, optional date,
read status, evidence kind, excerpt and truncation flag. Each excerpt has at most
1,200 UTF-8 bytes; each serialized source is capped at 3,000 bytes. The raw search
list and full page bodies do not enter the model conversation. No additional model
is called to summarize or rank content. These are shortened excerpts, not generated
summaries. If a page fails, its search snippet is retained and explicitly marked
`search_snippet`, with the failure in `read_status`. Mixed reading outcomes produce
`partial`; search failures retain the original status/error and return no sources.

Search keeps its 15-second deadline; concurrent page reads each have a 10-second
research deadline. The enclosing tool has a 30-second limit. Cancellation propagates
to all page readers. A retrieval timestamp is included so research need not first
request the clock tool. Recency filters and publisher dates still require judgment.

After the first `research_web` or separate `google_ai_mode` attempt, all web tools are removed from the model's
available schemas for this user request. Further web calls, including extras in
the same generated batch, are rejected without execution. This also applies after
an error, preventing retry loops; a new user request starts fresh. Other enabled
tools are unaffected. The obsolete three-search tool budget has been removed.
Ordinary research can now use two model calls: research request, then final
answer. This is not a universal two-call guarantee when other tools are enabled or
the model chooses a different workflow.

Two focused offline tests cover the backend search/dedup/parallel-read/compact-output
flow and the two-response conversation with web reentry blocked. The live-check
script now enables only `research_web` and records model-call count and per-call
input JSON bytes/available router metrics. Bytes are not token counts. The new
research flow has not yet been benchmarked live; historical figures below predate it.

## Internal search helper (not a model tool)

```json
{
  "query": "Türkiye teknoloji haberleri",
  "recency": "day",
  "max_results": 30
}
```

- Only `query` is required. There is no model-facing news mode or provider configuration.
- `recency`: `day`, `week`, `month`, `year`, or `null` (default). Support varies by engine;
  a recency filter does not establish that an article was published on today's local date.
- `max_results`: 1–30, default 10. Invalid/duplicate URLs and the output byte budget can reduce the count.
  Requests above 10 use shorter snippets so more links fit in one response.

All queries use general DDGS `text()` search, with worldwide `wt-wt` region and
an internal mapping from recency to DDGS time filters. Relevant language/place belongs
in the query. There is no keyword classifier or news-specific routing. More results
can reduce model round trips, but do not guarantee lower provider latency.

Backend selection is internal: DDGS `auto`, with no additional service or browser fallback.
The configured selection is logged as `backend=auto`; DDGS's normalized results do not
identify the actual engine for each result. `source` means the news publisher, not the engine.

Results include `title`, `url`, `snippet`, nullable `date` and nullable `source`.
Top-level status is `ok`, `no_results`, `timeout`, or `provider_error`.
Empty responses, or DDGS's exact `No results found.` exception, produce `no_results`;
this explicitly does not establish that matching news does not exist. Known timeout
exceptions produce `timeout`; rate limits and other errors produce `provider_error`.
Available exception details remain in `error`, including an HTTP status when exposed.

Each `ToolConversation` allows one research attempt, which performs one internal
search. The general 8-round/16-call guard still applies to the conversation.

Search runs in a worker thread with a 5-second DDGS HTTP timeout and a 15-second
await deadline, inside the tool runner's 20-second limit. Python cannot forcibly stop
an already running thread; DDGS requests may finish after cancellation/deadline.
No Orion-level retry loop is added. DDGS may contact multiple engines internally.

Structured `web_search` INFO logs include turn ID, query, parameters, backend,
status, elapsed milliseconds, usable result count, exception type and HTTP status.
Queries appear in logs intentionally; apply the deployment's normal log retention/access policy.
DDGS/primp INFO logs can reveal individual engine failures or HTTP responses that
DDGS does not expose on its final exception; `http_status=null` means unavailable,
not that the upstream succeeded.

## Internal page readers (not model tools)

`read_pages({"urls":["https://...", "https://..."]})` reads 1–5 pages concurrently
and returns a `pages` array in input order, plus total elapsed time. Every entry has
its own status; a timeout, blocked URL or other page failure preserves the successful
entries. Each reader uses the same SSRF checks and independent 15-second deadline.
Cancellation of the batch cancels all in-flight readers.

Both readers return at most 4,000 UTF-8 bytes of text per page. Batch entries also
have a 6,000-byte serialized JSON budget (including metadata/escaping), so five pages
fit within the runner's 32 KiB ceiling. Further shortening sets `truncated=true`.
Research selects and reads pages internally; the model does not call these helpers.
The runner already executes all tool calls in one model response before requesting
the next model response; the batch tool additionally makes these HTTP reads parallel.

`read_page({"url":"https://..."})` returns final URL, title, readable text,
optional publication metadata, HTTP status, elapsed time, and a truncation flag.
The minimal HTML extractor prefers `article`, then `main`, then body text,
excluding navigation, scripts, styles and common non-content elements.
Publication metadata is a publisher claim; missing metadata stays null.
The model can also inspect dates in visible article text.

Only public HTTP(S), ports 80/443, without URL credentials are accepted. IP literals
and actual DNS answers are checked; private/mixed DNS answers are rejected at the
connector. Every redirect is revalidated (maximum five). The dedicated session has
no environment proxy/authentication or cookies. The reader does not execute JavaScript.
It accepts HTML/plain text, caps decompressed response data at 1 MiB, caps returned
text at 4,000 UTF-8 bytes, and enforces a 15-second total deadline across redirects.
This keeps JSON output under the runner's 32 KiB limit, including escaping.

HTTP success does not guarantee useful article text. JS-only pages can return
`no_results`; challenge/login pages may still contain text. The model must treat
all retrieved content as untrusted source material and verify that it is an article.

## Historical live acceptance check — 2026-09-27, before contract simplification

The measurements below used the earlier `search_type/region/timelimit` contract.
They do not measure the simplified general-search contract above. Its follow-up
validation uses two focused offline tests: schema/recency mapping and 30-link output.

Run from the repository root with the hub environment and `PYTHONPATH=services/hub/src;.`
(use `:` instead of `;` on POSIX):

```text
python services/hub/scripts/check_web_research.py --model gpt-6-luna --output .tmp/web-research-live.json
```

This uses the existing router and actual tool conversation without modifying saved
chats/settings. It invokes the selected model and public sites. The JSON report
records the exact prompt, model, tool arguments/results, timings and final answer.

Prompt: **Bugün Türkiye’de teknoloji alanındaki en önemli 3 haberi web’de ara ve kaynaklarıyla özetle.**

Observed with DDGS 9.16.0 and `gpt-6-luna`, with network access:

| Operation | Status | Results | Time |
| --- | --- | --- | --- |
| News, tr-tr, day | ok | 10 | 817 ms |
| News, tr-tr, day, revised query | no_results | 0 | 1,139 ms |
| Text, tr-tr, day | ok | 10 | 1,301 ms |
| Read Megabayt article | ok | article text + date | 524 ms |
| Read Kantan.News page | no_results | no extracted text | 361 ms |
| Read Patronlar Dünyası article | ok | text with visible date; metadata null | 1,149 ms |
| Read Sabah article | ok | article text + date | 85 ms |

Total: **22,376 ms**, exactly three searches, four page reads, final Turkish answer
with three linked articles. Both populated publication-date fields were September 27;
the third article contained `27.09.2026` in its visible text. The model summarized
ASELSAN's mobile X-ray device, technology/R&D figures from a ministerial speech,
and restrictions on fake online engagement services. This checks the research flow,
not an objective ranking of the day's three most important stories.

During the second search, primp logged DuckDuckGo news HTTP 403 and Yahoo/Bing HTTP 200,
but DDGS surfaced only `No results found.` with no HTTP status. That demonstrates why
the adapter must preserve uncertainty instead of declaring that no matching news exists.
One successful run is insufficient to estimate long-term provider reliability.
An earlier sandboxed attempt hit connection-refused errors and is not counted as
evidence of provider blocking or search quality.
