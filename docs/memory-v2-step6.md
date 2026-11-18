# Memory v2 step 6 (docs/hafiza-tasarimi.md §16): proposals and visibility — hand-over

Branches (nothing pushed, nothing merged; merge BOTH together, on top of steps 1-5):
- Hub: `worktree-agent-accb35840a5abbdb5` (worktree `.claude/worktrees/agent-accb35840a5abbdb5`), from main 9e13fc5 (step 5).
  Two commits: the step itself, and (separate) the embedding-lookup fix.
- Flutter: `memory-v2-step6` (worktree `../orion-ai-assistant-flutter-step6`), from main a17f541 (step 5).

## What it is
One card for everything that waits for the user, and "Son eklenenler" (what the assistant wrote by itself) with Geri al.
Behind `memory_v2` (per user); a user without it keeps today's `delete_memory` confirmation (`memory_pending`) and nothing new
runs. No new table, so no new schema step (schema stays 6).

- `kernel/memory_proposals.py` (new; read its docstring): the card machinery moved out of `memory_entities` (which keeps thin
  wrappers `propose / proposals / decide` and `propose_supersede / propose_add / propose_merges / carry_out`). Actions: `add`,
  `supersede`, `merge_entities` (step 4) and new `trash`. A decided card loses what it showed (`statement`, `old`, `preview`)
  and keeps `result` (`fact_id`, or `trash_id` + `deleted`). The same open question is one card (also for `trash`).
- **delete_memory** for a v2 user opens a `trash` card (`propose_trash`, labels from `memory_pending.describe`, shared with the
  old confirmation); nothing moves until Sil; the answer moves the parts / whole memories to the trash as one removal and the
  card carries `trash_id`, and `trash` = `in | restored | gone` (computed on read), so Geri al works from the card. The tool
  answer has `proposal_id` only (no `confirmation_id`, so the chat shows ONE card); `confirmation_id="<proposal id>"` asks the
  outcome. A memory gone meanwhile: the card stays pending with `error`.
- **`kernel/memory_recent.py`** (new): what counts as an addition = a fact / event with `origin <> 'user'` that is the FIRST
  version of its lineage (a version accepted on a card is the user's choice), plus notes the model saved
  (`source_kind='assistant'`). Listed newest first, 30 days, with the source chat (title) and, for a locked fact, no text.
  `undo` = the ordinary removal (trash; locked fact: `{restore: {fact}}`), only for these additions (404 otherwise, the user's own
  writing is theirs to remove on the page). Notes carry no source chat (the tool never recorded one): shown as "Asistan ekledi".
- Three small partial indexes (background build with the others, `_index_plan`): `idx_orion_memory_assistant`,
  `idx_orion_mem_fact_auto`, `idx_orion_mem_event_auto`.

## API (all `memory_v2`; 400 when off, 404 for what is not the user's)
`GET /api/v1/memory/review` ({pending, recent}: the page's two numbers), `GET /memory/recent?days=&limit=&offset=`,
`POST /memory/recent/undo {type: fact|event|note, id}` -> `{trash_id}` (undo of the undo: `POST /memory/trash/restore`) or
`{restore: {fact}}` (`POST /memory/facts/{id}/restore`). Proposals routes (list / answer) are unchanged, now served by
`memory_proposals` and carrying `trash` and `trash` cards. Tool: `delete_memory` description unchanged (English); results above.

## Flutter (`memory_review.dart`, `memory_proposals.dart`)
- `MemoryProposalCard` is the one card (page, Ben tab, chat): `trash` shows what would go (+ pictures through the chat's attachment
  loader), Sil / Vazgeç (no Düzenle), after Sil "Çöp kutusuna taşındı" + Geri al (shown as "Geri alındı" at once, a refusal puts it
  back). `memoryProposalCards()` builds the list (batch = one card) for the review sheet and the Ben tab alike.
- The memory page has two buttons under the collection strip: "Onay bekleyenler (n)" (accent when n > 0) and "Son eklenenler (n)"
  (last week); they open `MemoryReviewSheet` (bottom sheet, two tabs). Answered cards stay in the sheet until it closes (Geri al).
  "Son eklenenler": rows with type, text, source chat, time; Geri al strikes the row at once and turns into Geri yükle; further
  presses on a row queue behind the running request and the LAST choice wins (a failure restores the row and says why).
  Light: `ListView.builder`, 50 per page + "Daha fazla göster", one request per answer, no polling.
- `MemoryPage` reloads the two numbers after each list load and after the sheet changes something; a failure keeps the old ones.

## Embedding lookup fix (separate commit)
Cause (measured, profile in this checkout): every `embedding_target` call did (1) `local_parts.embedding_profile()` (~21 ms warm: the
registry read a dozen times, the catalog, model folders) and (2) when no Router connection is selected / the catalog fetch failed
(Router down, not connected: nothing is cached on failure) `selected_router -> bootstrap_local_router ->
read_local_router_candidate -> inspect_router_folder -> verify_registered_package -> _scan`, a pass over every file of the Router
install (~23k files): 190-230 ms per call, in a thread, before failing with MemoryUnavailable. The Router on this PC is a REGISTERED
PACKAGED one, so this IS the installed-product path (a source checkout of the Router is the same function).
Fix: `local_parts.embedding_profile_cached()` (remembered while size+mtime of catalog, parts config, registry and location registry
are unchanged, plus 30 s; replaced function = miss, so tests that patch it are unaffected) used by memory (vector space, context) and
`embed_text` (prefixes); `router_bootstrap._inspected` remembers a GOOD inspection while the Router's inventory record is identical
(an install / update / repair rewrites it; 60 s net; refusals never cached; `forget_local_router_candidate()`).
Numbers (this PC, p of 3-4 warm calls, whole `memory.embedding_target`): Router unreachable 193-230 ms -> 4.7 ms (first call per
process still ~0.9-2 s: imports + first scan); catalog answered, local model: 21 ms -> 1.1 ms (first call 90-100 ms).
Not measured: a Router that is up and connected on an installed layout (the catalog is cached 15 s there, so the scan only ran on a
refresh); a long search benchmark at 40k rows (the step-4/5 bench DBs were not rebuilt).

## Tests
Hub `tests/test_memory_proposals.py` (13, real throwaway PG): delete card (nothing moves until Sil, same question one card, outcome by
`confirmation_id`, Geri al state `in -> restored`, labels gone after the answer), Vazgeç, whole memory, a memory gone meanwhile, v2 off
keeps today's confirmation, every action in one list and one answer route, three simultaneous answers carry out once, recent list
(source, never the user's own, paging, 30-day window, badges), accepted version / locked fact, Geri al for fact / event / note and its
restore, removed additions not listed + off means off, the routes as HTTP, user separation (list, answer, undo, count, delete of
another user's row), cost. `tests/test_embedding_lookup_cache.py` (5). Flutter `test/memory_review_test.dart` (10), and
`memory_page_test.dart`'s fake no longer records the review request among the list's requests.

## Not live-checked
Anything in the running app: the review buttons and sheet against the real Hub on a phone, a real chat model choosing
`delete_memory` under v2 and the card inside a streamed turn, the three new partial indexes building on a big real DB, the first
`delete_memory` after upgrade for a user with an old `memory_pending` card in a chat (it still answers through its own route).
Not done (later steps): the daily summary card of the first week and extraction (step 7), proposal actions `expire`, `move`,
`restore` of the design (no tool or page asks them yet), the chat-delete / message-delete card of §11.3, thumbnails in the page's
review sheet (the page has no attachment loader; the chat card has), source chat opening from a row.
