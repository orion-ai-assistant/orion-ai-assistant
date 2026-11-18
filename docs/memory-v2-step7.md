# Memory v2 step 7 (docs/hafiza-tasarimi.md §16): extraction, shadow mode, quality gate — hand-over

Branch (nothing pushed, nothing merged): Hub `worktree-agent-a344a889c1b043bbb` (worktree `.claude/worktrees/agent-a344a889c1b043bbb`),
from main ff087cc (step 6, merged in with a fast-forward). Flutter: NOT touched (no branch): the extraction has no page yet; the
report is read through the API (see below).

## What it is
The assistant reads the new messages of a chat once the chat has been quiet for a while, in the background, and finds facts,
events and the people named, each with the messages it came from. **Shadow mode is the only mode until the user turns automatic
writing on**: candidates and a quality-gate report are all there is; nothing is written to the memory (no fact, event, entity,
card, source row). Behind `memory_v2` (per user) and the new per-user setting `memory_extract_chats` (default true).
New per-user setting `memory_background_model` (a Router model group, empty = the chat model; validated like `chat_title_model`).

Code: `kernel/memory_extract.py` (read its docstring), `kernel/memory_extract_filter.py` (the pre-filter), small edits in
memory.py (schema 7), memory_entities.py / memory_events.py (`run_id` argument), memory_chat_index.py, memory_collections.py,
job_processor.py, routes.py, worker/services/router.py (`llama_chat_usage`: also returns `usage` and takes extra headers),
contracts/settings.py, router_models.py.

## The chain (one chat)
1. **Trigger**: `after_turn` (Worker, after the turn is saved) arms a per-chat idle timer (`IDLE_SECONDS` 180); a newer turn
   re-arms it. When it fires, ONE run is queued (`orion_mem_extraction_run`; one queued run per chat by a unique partial index,
   `QUEUE_MAX` 200 in all). There is no "chat closed" signal in the Hub, so idle is the trigger.
2. **Worker**: one asyncio task per process, one run at a time, `PAUSE_BETWEEN` 2 s between runs; **it ends when nothing is
   queued** (an idle Hub holds no task and polls nothing; the next enqueue starts it). Cancelable (`HEARTBEAT` 5 s: the worker
   updates the run's heartbeat and looks at the `cancel` flag; a cancelled run drops the model call). A failure that may pass
   (Router down, timeout, 5xx/429, a non-JSON answer) puts the run back with a growing delay (`BACKOFF` 30 s, 2 min, 10 min,
   30 min; a non-JSON answer is tried twice; then `failed`). A refusal (4xx), anything else: `failed` at once. A `running` run
   whose heartbeat is older than 90 s (dead process) is marked `failed: interrupted`.
3. **Pre-filter** (no model): only the new USER messages: first person / belonging words (tr + en), first-person Turkish verb
   endings, a name the memory knows (entity keys), date expressions. Nothing: `prefilter = dropped:<reason>` on the run
   (`no_user_text`, `no_personal_signal`, also `chat_off`, `chat_gone`). The lists are plain constants, generous on purpose,
   not tuned on any data.
4. **Input** (bounded: `MAX_INPUT_CHARS` 16000 of message text per run, a longer backlog is several runs): new messages with
   ids and times, the time zone, 4 earlier messages for context, the previous summary, the entities the messages name with
   their current non-locked facts (ids, so the model can say "this fact changed"). Locked facts: never put in, only
   `has_locked_facts`. The system prompt says the conversation is DATA, not instructions.
5. **Model**: the background model through the Router (`llama_chat_usage`, temperature 0). JSON: summary, entities, ops
   (`add_fact`, `add_event`, `supersede`), each with its message range, `bucket` (direct / ambiguous / inferred) and
   `unsupported_risk` (low / medium / high). The request carries `x-orion-purpose: memory-extraction` and
   `x-orion-log-content: hidden` (the Router does not read them yet: see "Not done").
6. **The Hub's own checks** (it trusts nothing): every source range must exist in the run's messages and contain a USER message;
   sentences, categories, dates validated with the same functions as the tools; relative dates resolved from the message's own
   time (`memory_events.resolve_date`, so "bugün" in a chat from three days ago is that day); each named entity matched to the
   user's own (an id counts only when it is the user's; else exact name, then exact relation; several = ambiguous; nothing is
   created); duplicates (same words about the same entity, same event/day/words, same in the run, same already found in a shadow
   run); conflicts (`supersede` of a current fact).
7. **Verdict** (`judge`, categorical, no tuned number): problem or `inferred` -> skip; duplicate -> skip; high risk -> skip;
   conflict -> card; sensitive -> card only when `direct`, else skip; ambiguous entity / ambiguous bucket / medium risk -> card;
   else would write. Stored on the candidate (`would_write | would_card | would_skip` + `reason`).
8. **Summary**: the same pass updates `orion_mem_chat_state.summary` (shadow too: it is derived chat data, deleted with the chat;
   a chat the pre-filter dropped gets none). `message_deleted()` marks it stale and a summary-only run remakes it. There is no
   user-facing message delete in the Hub yet, so nothing calls it (like step 3's `changed`).

## Automatic writing (OFF by default)
`PUT /api/v1/memory/extraction/live {enabled, confirm}`; turning it on needs `confirm: true` (the user has read the report).
State per user in `orion_mem_extraction_state` (`live`, `live_since`; turning it on twice does not restart the week). In live
mode a run's `would_write` candidates are written **add-only** (`add_fact` / `add_event`, origin `extraction`, `run_id`,
source = the chat's message range; a sensitive fact is refused by `add_fact` for this origin anyway); `would_card` become
step-6 cards (`add` for a fact, `supersede` for a changed one; an ambiguous event and an ambiguous person have no card kind and
are `skipped` with that reason); duplicates add the new source to the existing fact. Everything written appears in "Son
eklenenler" with Geri al. The outcome of each candidate is `written | carded | skipped` (+ `written_type/id`).
`GET /memory/extraction/daily`: for the first week (`SUMMARY_DAYS` 7) one summary per day with additions ("Dün hafızaya 4 bilgi,
2 olay eklendi"; undone ones are not counted); the page that shows it as a card does not exist yet.

## API (all behind `memory_v2`: 400 when off)
`GET /api/v1/memory/extraction` (switches, model, queue, newest runs; also restarts the worker for queued runs after a restart),
`POST .../extraction/backlog {limit<=50}` (queue the chats with unread messages, newest first: how the old chats go through the
gate), `POST .../extraction/runs/{id}/cancel`, `POST .../extraction/cancel` (all), `PUT .../extraction/live`,
`GET .../extraction/daily`, `GET /api/v1/chats/{id}/memory/summary`, `GET /api/v1/memory/extraction/report`.
Chat state (`GET /chats/{id}/memory`) also carries `summary`. A chat's own memory switch off, a chat deleted, memory_v2 reverted:
its runs and candidates go (`forget_chat`, `forget_user`).

## How to read the quality-gate report
`GET /api/v1/memory/extraction/report?format=text` (readable lines) or without `format` (JSON). Filters: `run_id`, `chat_id`,
`verdict=would_write|would_card|would_skip`, `limit` (default 100, max 300, newest first), `reveal=true` (locked sentences).
Top to bottom:
- **Runs**: how many, by state; `prefilter` counts (`passed` vs each `dropped:<reason>`); tokens in/out, seconds.
- **Candidates**: totals by verdict, bucket, risk, dedupe kind, sensitive count, and why skipped. **Would have written**: facts,
  events, and how many cards it would have opened. This is "what automatic writing would do"; nothing was written.
- **Dropped by the pre-filter**: each chat the filter threw out, with the reason. If a chat that has memories in it is here, the
  filter is too strict: extend the word lists in `memory_extract_filter.py`.
- **Nothing extracted**: chats the model read and found nothing in. Read the chat: was it right?
- **Items** (each candidate): the sentence / event with its date (`said_as`, `precision`, and a `date_note` when the Hub overrode
  the model's date), who it is about, **the user messages it cites (snippet)**, bucket, `unsupported_risk`, dedupe
  (`new`, `duplicate_of:fact|event|candidate:<id>`, `duplicate_in_run`, `conflicts_with:<fact id>`), `merge_target` (the entity it
  matched; empty = a new one would be made), SENSITIVE flag, verdict + reason.

What to judge (the gate of §7.4, ~20 varied chats: `POST .../backlog {limit: 20}`, wait, read): (1) every `would_write` item: is it
true, is it about the right person, does the cited message really say it (compare the snippet)? A wrong one is the failure that
matters; (2) `would_card` / `ambiguous`: right to ask? (3) `unsupported_risk=low` items that are wrong: the model's own risk call
is not trustworthy; (4) dropped / nothing-extracted chats that did have something; (5) duplicates and conflicts: did the Hub see
what you see? (6) sensitive hits: only ever cards, text masked (`[locked]`) unless `reveal`. Turn automatic writing on only after
(1) is clean.

## Cost (this PC, Python 3.14, fake model, local PG; the real Router/model call is network time and not CPU here)
Measured with a script (not in the repo): a 240-message chat (two runs of ~27k and ~20k input chars, 30 candidates each):
**~47 ms CPU and ~200 ms wall per run** (wall is mostly DB round trips, ~35 connections per two runs), **peak Python memory ~0.8 MB**
(tracemalloc). Idle: no task, no timer except one sleeping task per recently active chat (cancelled by the next turn). Queue and
input are bounded (200 queued runs, 16k chars, 20 entities x 30 facts, 40 ops per answer). The model call itself costs the
user's background model tokens: roughly the input size above per run; counted in the run row (`tokens_in/out`).

## Schema 7 (backup first, additive)
`orion_mem_extraction_run`, `orion_mem_candidate` (FK to the run, cascade), `orion_mem_extraction_state`; `orion_mem_chat_state`
gets `extracted_upto, summary, summary_upto, summary_stale, summary_at`; `orion_mem_fact.run_id`. Every table has `user_id` and
every query filters by it.

## Tests
`tests/test_memory_extract.py` (38): pre-filter, parse, judge, same-words (unit); on a real throwaway PG with a scripted model:
shadow run writes nothing, relative dates from message time, pre-filter drop with no model call and the assistant's words not
counted, nothing-extracted, incremental runs + summary, a long backlog cut into runs, bad sources / inferred / unsupported / bad
items, duplicates + conflicts + known entities, two same-name people = ambiguous and nothing created, another user's entity id
never matched, sensitive (card at most, masked, locked text never in the input), the model input and the injection note, user
separation of every table / route-level function / forget, queue bounds, cancel (queued at once, running within a heartbeat),
backoff then failure, bad JSON twice, a dead process, off means off, the idle timer + worker that ends by itself, chat deleted /
switched off, stale summary, the report in numbers and text, live default off + confirm, live writes (add-only, source, run id,
cards, nothing sensitive, Geri al, accepting the conflict card), live duplicates, live ignoring shadow candidates, embedding down
in live, the first week's summary. `test_memory_events.py`'s schema-version assertion now follows `SCHEMA_VERSION`.

## Design deviations / decisions
- **Entity context is by name only**: the design also adds the vector-nearest few facts; not done (an embedding call per run on
  the local model would cost the CPU this step must not). The user's own entity is in only when it exists.
- **Candidate dedupe against shadow history**: in shadow nothing is written, so "already found" is checked among earlier
  `would_write` candidates of the same user (by a words key); in live that check is off (real facts decide).
- **An ambiguous event or ambiguous person has no card in live** (the card kinds of step 6 are `add`, `supersede`,
  `merge_entities`, `trash`; a new kind needs a Flutter card): the candidate is `skipped` with the reason, visible in the report.
- Conflicts come only from the model's `supersede` (the input lists current facts with ids); the Hub does not guess that two
  free sentences contradict.
- Summary: made for chats that passed the pre-filter only, in shadow too (derived chat data).
- The backlog button of the design ("öneri geçişi" for old notes) and the page UI are not part of this step.

## Not live-checked
A real background model through the real Router (prompt quality, JSON discipline, thinking models, token counts in `usage`,
the cost per chat); the report against ~20 real chats (the quality gate itself is the user's judgment, nothing was tuned);
the worker in the real Worker/API processes (two processes can each run a worker for the users they know; queued runs of a
user wait until that user's next turn or a status call after a restart: `resume`); the idle timer across a Hub restart (a chat
whose timer was pending is caught by the backlog request); the Router honouring `x-orion-log-content` (it ignores it today: the
Router's log hiding is step 8's profile work); live mode end to end with a real model; Flutter (no UI for the report, the switch,
the daily card or the summary).
