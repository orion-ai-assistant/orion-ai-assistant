# Memory v2 step 8 (docs/hafiza-tasarimi.md §16): search and the profile block — hand-over

Branch (nothing pushed, nothing merged): Hub `worktree-agent-a45fc4198f969b06b` (worktree `.claude/worktrees/agent-a45fc4198f969b06b`),
from main daa6125 (step 7 merged in). Flutter: NOT touched (no branch): nothing in this step needs a screen to work; the profile has
no page yet (API only, see below).

## What it is
1. **The approved ranking policy, one implementation.** The order of a result list IS the shown real %; a word match only marks
   (`match`, `spans`) and never lifts a card; a word search is the `keyword` query; "unlike" is a filter (memories that stand out
   against the unliked input's own pool are dropped, the rest keep their order and %). This was already the code path of
   `memory_groups.list_groups` / `rank_matches` / `group_match` (page, panel, tool all call it; facts, events and chat windows are ordinary
   `orion_memory` rows in it), so step 8 changed no ranking code: it adds the missing proof. New test
   `RankingConsistencyTests` (tests/test_memory_search_v2.py): facts, events, chat windows and notes in one pool; single text, a word that
   matches rows, together, separate, unlike, `in` me / experiences / chats / all three, keyword, `about`, `when`: for each the page's list,
   the tool and the detail panel (`part_scores`) give the same order, ids, % and strength; the mixed order is by real score across kinds
   (fact .90, event .80, event .70, chat .62, fact .55, chat .35: interleaved, not by kind); unlike leaves the rest in the same order with
   the same %.
2. **`search_memory` (English, defaults unchanged)** — all of these only with the user's `memory_v2` on (else ignored, like `when`):
   - `about` (name, relation, `#<id>`, `me`): only that entity's current facts, its events and those events' photos (a candidate set,
     `memory_events.about_keys`, ranked exactly like `when`; with `when` both apply). No query = its newest. Unknown name: empty answer with a
     note; two entities with the name: the usual "pass #id" error.
   - `as_of` (`YYYY-MM-DD`; a month or year means its LAST day): current facts that only began after that day are left out (`memory_entities.not_yet_groups`), and the past versions that were true that day come in `past_facts`.
   - `include_past`: past versions (replaced facts) in `past_facts`: `{id, about, category, statement, since, until, past: true}`. They have
     NO vector row (step 4 deletes it when a version is replaced), so they are found by the query's words (folded, 3+ letters; at least one,
     the more the earlier) and by `about`/`as_of`, and carry NO score: they are a separate list, never mixed into the scored results. An end date
     nobody gave falls back to the day Orion stopped counting the version (`expired_at`). Locked facts are never in it.
   - `budget` (100-8000 tokens): the answer is the entity card, then past versions (at most 30%), then the best results that fit, whole, in the
     order of their scores; `more` is true when some were left out; `budget: {limit, used}`; `page` > 1 is an error (no paging with a budget). The
     card may take at most 60% (facts, then events, dropped whole; `trimmed`). Tokens are estimated (`memory.estimate_tokens`: 3 characters per
     token, rounded up: a guess on the high side, the Hub carries no tokenizer). **Deviation from §9:** the design says "facts first, then events,
     then parts"; the approved ranking decision (single list by real %, no per-kind sections) wins, so the budget cuts the ONE ranked list from the
     bottom (the card and the past versions, which are facts, come first).
   - **Entity card** (`entity`): when a query names one entity (a name, relation or alias as a whole word of the query; a Turkish suffix of up to
     four letters on a name of 4+ letters is allowed: "annemin"; the user's own entity only when the whole query is one of its names: "ben" inside a
     sentence does not count), or `about` was given. `{id, name, kind, relation, aliases, facts[<=12: id, category, statement, since,
     past_versions?], recent_events[3], more_facts?, locked_facts?}`: locked facts only counted, never shown. Two entities fit: `entity_candidates`
     (names only), no card. This key is additive and only with `memory_v2` on.
   - The card and the past versions are read on the search's OWN connection (`list_groups(side=...)`): no extra connection, ~+20 ms measured.
3. **Profile block** (`kernel/memory_profile.py`, read its docstring) behind `memory_v2` + the new setting `memory_profile` (default true).
   Added to the system message of EVERY chat turn (`job_processor`: `system_prompt + "\n\n" + block`; no system prompt: the block alone).
   Pinned lines (always in, the user's own text, optionally tied to a fact) first; then current facts by kind: kimlik, ilişki, iş, yer, tercih,
   the rest; inside a kind more sources first, then the most recently confirmed, then the newest id. At most 300 tokens for the whole block
   (header included); a fact is never cut: one that does not fit is left out whole and the next that does fit is tried. Never in it: past versions,
   trashed facts, locked facts, removed/merged entities, another user's anything. Pinned alone past 300: all pinned still go in, `overflow` is true
   (the page is to warn), the generated part is empty. A pinned line tied to a fact that was replaced / removed says `replaced` / `gone` in the API
   and is not sent (the user pins the new one); a line tied to a locked fact cannot be pinned.
   Rebuilt from the facts at every turn (one query), so changed / removed / trashed / restored facts show in the very next turn and nothing is
   stale; the text is byte for byte the same while nothing changed (prompt cache). `orion_mem_profile` (schema 8, additive, backup first)
   keeps `pinned` and the last built `generated` / `tokens` / `built_at`, written only when the block changed. Failure of any kind: the chat goes
   without the block (never breaks).
   **Router log:** the chat request carries `x-orion-memory-profile: tokens=N; facts=1,2,3` (`memory_profile.log_header`; passed as
   `extra_headers` of `llama_stream_chat_typed`) and the Hub logs `Memory profile: user=.. tokens=.. facts=.. pinned=..` (never the text). The
   **Router does not read the header yet** (separate repo): until it does, the Router's request log still holds the whole system message.
   Hiding the block's text there (and "show content" per request) is the Router-side half, not done.
4. **API** (all `memory_v2`; 400 when off, 404 for what is not the user's): `GET /api/v1/memory/profile` ({enabled, limit, text, tokens, overflow,
   left_out, pinned[{text, fact_id, state}], generated[{fact_id, text}]}), `PUT /api/v1/memory/profile/pinned {items}`, `POST .../profile/pin
   {fact_id, text?}`, `DELETE .../profile/pin/{fact_id}`. Limits: 30 pinned lines of at most 300 characters.

## Measured (this PC, Python 3.14, throwaway PG, scratch script not in the repo)
20 000 rows, 80 clusters of normalised random 768-d vectors (noise 0.35), mixed: facts 4/20, events 2/20, chat windows 10/20, notes 4/20;
HNSW partial indexes built; 4 query vectors near different clusters, median of 12 warm calls. Before = main daa6125 (step 7) source,
after = this branch.

| case | before ms | after ms |
|---|---|---|
| page: list_groups q (20 results) | 466 | 476 |
| tool: default | 473 | 495 |
| tool: in me + experiences | 520 | 547 |
| tool: in chats | 436 | 465 |
| tool: two queries together | 475 | 499 |
| tool: unlike | 667 | 676 |
| tool: when | 337 | 347 |
| tool: budget 1500 (new) | - | 494 |
| tool: about (new) | - | 636 |
| tool: as_of + include_past (new) | - | 539 |
| tool: query that names an entity (card) (new) | - | 627 |
| profile build, 3000 facts (new) | - | 145 |

The page's path is unchanged (noise). The tool pays ~+20 ms for v2 users (the entity-name read on the search's own connection). **Honest
caveat: the 150 ms target is not met by this harness at all, before or after** (about 470 ms on 20k rows here: PG backend start-up alone is ~45 ms
per connection on this Windows PC and `ensure_memory_table` adds ~20 ms each; every search opens several). `about` pays one more connection to
resolve the name (~+65 ms) and ranks its candidates exactly. Profile build: 145 ms with 3000 facts (mostly one connection); a real profile
reads at most 300 candidate rows. Memory: nothing is cached or held; a budget search looks at 40 ranked memories instead of 10.

## Tests
`tests/test_memory_search_v2.py` (25, real throwaway PG): the consistency suite above; defaults unchanged; `include_past` / `as_of` (a year,
a missing end date, locked never, bad date); `about` (entity only, unknown, ambiguous, `#id`, no query); entity card (relation, suffix,
"ben", longer words, locked counted not shown, two entities); `memory_v2` off ignores everything; budget (prefix of the unbudgeted order, whole
results only, `more`, no paging, card trimmed whole facts); profile (kind order, 300-token limit, whole facts, a fitting later fact still
goes in, locked / past / trashed / other user's never, restored fact returns, stable text and store-on-change, pinned first and surviving
rebuilds, overflow, replaced/removed pinned lines, what cannot be pinned, off means off, system message and header, the log line has no text,
routes, 3000-fact cost). The wider selection (`pytest tests/ -k "memory or tool or embed or pending or chat or extract"`): 512 passed, 46 skipped in 16 min
(the 25 above included; nothing else changed).

## Not live-checked
A real chat with the block: the model's use of it, the prompt-cache effect on OpenAI/DeepSeek, whether 300 tokens is the right size (nothing
was tuned: the design's number); the Router's log showing `x-orion-memory-profile` (the Router ignores it today); the tool descriptions with a
real model (does it use `about` / `as_of` / `budget` sensibly); a big real database (the 150 ms target, the exact-ranked `about` set on a user with
thousands of events); a Turkish tokenizer's real token counts against the 3-characters estimate; the page: there is NO profile page (view, edit,
pin: API only), the search box has no `about` / `as_of` / `include_past`.

## Left for later
Profile screen (Ayarlar: view, edit, pin, the overflow warning), page-side `about`/`as_of`, Router-side log hiding with "show content", past
versions by meaning (would need their vectors back: the design's `current=false`, rejected in step 4 for index cost), a tokenizer-based count.
