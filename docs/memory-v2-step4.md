# Memory v2 step 4 (docs/hafiza-tasarimi.md §16): entities + facts — hand-over

Branches (nothing pushed, nothing merged; merge BOTH together, on top of steps 1-3):
- Hub: `worktree-agent-ad5d725d1c9056f57` (worktree `.claude/worktrees/agent-ad5d725d1c9056f57`), from main c79d818.
- Flutter: `memory-v2-step4` (worktree `../orion-ai-assistant-flutter/.claude/worktrees/memory-v2-step4`), from main d9f9b1d.

## What it is
The "Ben" collection: entities (person, place, org, pet, thing, the user = `self`) and facts (one short sentence about an
entity) with bi-temporal versions, every fact linked to a source, a sensitive-kind lock, a reversible entity merge and the
cards (proposals) that wait for the user. All behind `memory_v2` (per user); with it off nothing runs and `add_memory`'s
default (`type: note`) is today's behaviour. One implementation, `kernel/memory_entities.py` (read its docstring), used by
the page routes, the tool and the cards. Schema 4 -> 5 (backup first, additive): `orion_mem_entity`,
`orion_mem_entity_merge`, `orion_mem_fact`, `orion_mem_proposal` (every table has `user_id`, every read filters by it).

## Decisions worth knowing (where it differs from the design text)
- **A fact's vector is an ordinary `orion_memory` row** (`kind 'text'`, `source_kind 'fact'`, its own group, main collection
  `ben`, title = entity name, text = "Anne (Ayşe): <sentence>"). Page, map, counters, trash, model move and `search_memory`
  need no new code; `orion_mem_fact.group_id` points at it. A fact is live while that memory has a live row: removing it
  through the page's own delete/trash removes the fact, restoring brings it back, deleting for good removes the fact rows
  (`memory_trash.purge` -> `memory_entities.after_purge`). `memory_collections.SORTED_TO` sends fact memories to Ben when a
  user's collections are made again after a revert.
- **Past versions have no vector row** (the design's `current=false` flag would rewrite a vector row and its index entries;
  we delete the closed version's row instead). Versions stay in `orion_mem_fact` (text, `valid_from/valid_to`,
  `recorded_at/expired_at`, `superseded_by`, `lineage` = first version's id). Only the current version is searched by
  meaning; searching the past (`as_of`, `include_past`) is step 8 and will work from the fact table.
- **Sensitive (§11.1) = locked = no vector row at all**, so no search, map, count, trash card, profile or extraction input can
  show it. `search_memory(sensitive=true)` adds `locked_facts` matched by WORDS only (statement, category, entity name; no
  words = newest). The page lists them as `locked` without text; `?reveal=true` returns it (the page asks on a tap). Removal
  of a locked fact is `deleted_at` (+ `restore`), purged after the trash days (`purge_removed_all`, called from
  `purge_expired`). A model never writes one: `add_memory(category=<locked>)` opens a card; `origin='extraction'` + locked is
  refused in code (step 7 relies on it). `kimlik_no`: a number of 8+ digits is refused (only "it exists" is stored).
- **No fuzzy thresholds anywhere.** Duplicate fact = same sentence after Turkish folding on the same entity (`same_as`, and the
  new source is added). Entity match = exact folded name/relation/alias (`keys` column); two matches -> `Ambiguous`
  (tool answer lists candidates, `about: "#<id>"` chooses; HTTP 409). Duplicate entities for the batch card = share an exact
  folded name/alias, same kind, never `self`.
- **Source rule**: `add_fact` raises without a chat / item / `by_user`. The tool's source is the chat id; the message range
  stays null (the turn's own message is saved after the tool runs) — step 7's extraction fills first/last message.
- **Cards** (`orion_mem_proposal`; step 6 generalises): actions `supersede` (add_memory `replaces`, ALWAYS a card), `add`
  (locked kind from the model), `merge_entities` (batch card from `POST /memory/entities/duplicates`; each pair answered on
  its own). Answer: `POST /memory/proposals/answer {accept, reject, edits{id: own sentence}}`; a card is answered once
  (`already`), a failed one goes back to `pending` with `error`; the sentence is removed from a card once decided. The
  model asking the same thing twice opens one card. The user's own page changes open no card.
- Entity merge: one transaction; facts move, the merged name/relation/aliases become aliases of the survivor, what moved is
  recorded (`orion_mem_entity_merge.moved`, with an empty `links` list for step 5); `undo_merge` puts the facts back (a fact
  moved on since stays) and removes the added aliases; refused if the merged entity was merged again. The user's own entity
  is never merged with another kind. The embedded text of a moved fact keeps its old label (no re-embedding).

## API (all `memory_v2`; 400 when off, 404 for anything not the user's)
`GET/POST /api/v1/memory/entities`, `GET/PUT/PATCH .../entities/{id}` (`?reveal=true`), `POST .../entities/merge`,
`POST .../entities/merges/{id}/undo`, `POST .../entities/duplicates`, `POST /api/v1/memory/facts`, `GET .../facts/{id}`
(history, sources, `?reveal=true`), `POST .../facts/{id}/replace`, `POST|DELETE .../facts/{id}/remove`,
`POST .../facts/{id}/restore`, `GET /api/v1/memory/proposals?state=&ids=`, `POST .../proposals/answer`.
Tool: `add_memory` gains `type (note|fact)`, `about`, `about_kind`, `relation`, `category` (English names), `since`,
`replaces`; answers `{added: {fact_id,...}}`, `{same_as}` or `{status: awaiting_user_confirmation, proposal_id}`.
`search_memory` gains `sensitive`; fact results carry `fact {id, about, category, since}`; `memory_context` on a fact row adds
`fact {versions, sources}`. English descriptions.

## Flutter
Ben pill (collections strip) shows `MemoryBenPanel` (`memory_ben.dart`): entity list, entity card (facts by kind, "geçmiş (n)"
sheet with dates and sources, locked facts blurred until a tap, add / Güncelle / Kaldır with Geri al, rename, "Birleştir…"
with Geri al, "Birleştirmeyi geri al"), "Yinelenenleri bul", "Onay bekleyenler". `MemoryProposalCard`
(`memory_proposals.dart`): single card = Onayla / Düzenle / Reddet, batch card = a box per pair (none checked first),
"Seçilenleri uygula"; also shown in the chat under the tool activity (`toolActivityProposalIds`).

## Numbers (throwaway PG, 40k ordinary notes, one user, random 768-d vectors, HNSW + trigram indexes built, p50 of 25, tool path)
Whole `search_memory` call, p50 of 25 (script `scratchpad/bench_facts.py`, not in the repo). "Before" = 40k notes, no facts;
"after" = plus 3000 facts (30 of them near the query), same queries:
| search | before | after |
|---|---|---|
| default (whole memory) | 420 ms | 494 ms (477 ms with the fact lookup patched out: the lookup costs ~17 ms) |
| `in: ["me"]` | 417 ms (empty Ben) | 531 ms (10 fact results) |
| keyword "kedi" | 323 ms | 327 ms |
The absolute numbers are far above 150 ms because the bench is the worst case for HNSW: RANDOM 768-d vectors (no cluster
structure, the step-3 bench used clustered ones: 87 ms) on a busy PC. What to read: the fact lookup is one query on the
search's own connection (first version opened its own: +85 ms, so it moved into `list_groups`), keyword search is unchanged,
the vector searches grow ~15% with 7.5% more rows and results that are facts. NOT measured: a real embedding model, the
clustered 80k/1M bench DBs, concurrency, locked-fact word search (a scan of the user's locked facts only).

## Tests
Hub `tests/test_memory_entities.py` (14, real throwaway PG): fact + source + vector + Ben membership + search with its entity,
refused writes, `same_as`, defaults with v2 off, ambiguity, replaces card (nothing changes until accepted, one open card,
history, closed version has no vector), reject / edit / user's own change, locked fact (card first, no vector, in no search or
page, only `sensitive=true`, masked / reveal / remove / restore), merge + undo, batch card, trash / restore / purge with history,
routes as HTTP, user separation across every new table, one user's purge leaves the other's. Flutter `test/memory_ben_test.dart`
(6): list + card + locked reveal + history, add / remove / Geri al / merge / undo, single card with Düzenle, batch card,
proposal-id extraction, Ben pill.

## Not live-checked
Anything in the running app: the schema 4 -> 5 step on the real DB, a real embedding model writing a fact, the model actually
choosing `type=fact` / `replaces` / `sensitive` (tool wording is untested with a real chat model), the chat card inside a real
streamed turn, the Ben tab against the real Hub on a phone. Not done (later steps): events and `about` / `as_of` /
`include_past` search arguments (steps 5, 8), "Son eklenenler" + Geri al for model writes (step 6), extraction (step 7), profile
block, Router log hiding of locked results (step 8: the Router logs a tool result's content; `locked_facts` are visible there
until §9.2's log rule is built), entity removal / entity trash cards (§11.3), editing an entity's label re-embedding its facts,
a Flutter switch for the per-chat memory (still API only), a `claimed` proposal whose process died stays `claimed`.
