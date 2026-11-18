# Memory v2 step 3 (docs/hafiza-tasarimi.md §16): the chats index (Sohbetler) — hand-over

Branches (nothing pushed, nothing merged; merge BOTH together, on top of steps 1-2):
- Hub: `claude/optimistic-villani-a56966` (worktree `.claude/worktrees/optimistic-villani-a56966`), from main b3a1c43.
- Flutter: `memory-chats-index` (worktree `../orion-ai-assistant-flutter/.claude/worktrees/memory-chats-index`), from main 25ca321.

## What it is
Chat messages are indexed into memory as windows: `kind 'text'`, `source_kind 'chat_index'`, ONE memory (group + item) per
chat, in the Sohbetler collection (`orion_mem_collection_item` main = 'sohbetler'). Windows = `memory_pieces.chat_pieces`
(the existing cutter of save_chat): message-bounded, ~300-token cores, 75-token context from the neighbour cores,
"Kullanıcı:"/"Asistan:" prefixes, a message longer than a core is split alone, `source_message_id`/`source_message_last` =
first/last message id of the window. Tool results (role tool) and failed turns are not in the transcript, so not embedded.
No "whole" vector for chats. Same path as every memory: `memory.embedding_target`, `memory._embed`, active vector space;
a model move copies the windows like any text rows (item order = (item_order, id)).

Code: `kernel/memory_chat_index.py` (new, read its docstring), small edits in memory.py, memory_collections.py,
memory_groups.py, routes.py, job_service.py, job_processor.py, tools.py, contracts/settings.py.

## Behaviour
- Behind `memory_v2` (per user) AND the new per-user setting `memory_index_chats` (default true). With v2 off: nothing runs.
- New chats: `job_processor.append_history` (after the turn is saved) -> `memory_chat_index.after_turn(chat_id, settings)`
  = a background asyncio task, waits DEBOUNCE 5 s, indexes (a burst of turns = one run + at most one more). Never blocks the
  chat. Missed ones (restart, Router down) are caught by the button (every chat with a message past `indexed_upto`).
- Incremental: a chat is cut whole each time (cheap CPU) but only changed windows are embedded: a stored window equals a new
  one when (previous core, core, next core, first id, last id) are equal; the stored prefix up to the first difference stays
  (same row ids), the rest is replaced, vectors of unchanged later windows reused. New turn = last 1-2 windows.
  Nothing is written until all vectors exist (cancel/failure keeps the old index). One chat at a time (advisory lock).
- Edit/delete: `delete_failed_turn` -> `changed(chat_id)` (re-index); `delete_chat` -> `forget_chat` (windows, state,
  membership gone, for good). There is NO message-edit feature in the Hub today: `memory_chat_index.changed(chat_id, settings)`
  is the hook an edit endpoint must call. A chat whose windows are in the trash is left alone (restore brings them back).
- Per-chat switch: `PUT /api/v1/chats/{id}/memory {enabled}` (+GET): off removes the windows and keeps the chat out.
- Backlog job ("Eski sohbetleri dizinle"): `GET /api/v1/memory/chats/index` (chats, indexed, windows, pending, enabled, job),
  `POST` same path starts, `POST .../cancel`. One job per user (`orion_mem_chat_index_jobs`, heartbeat like memory_move).
- Search: `POST /memory/search {collections:["chats"]}` / `GET /memory?collection=sohbetler` / tool `search_memory`
  `in: ["chats"]`; one chat: `in: ["chat:<chat id>"]` (collections entry `chat:<id>`, only that user's rows). Results carry
  `chat: {id, title, message_id}` (tool) / `parts[].chat` + `best_part.chat {id, message, message_last}` (API). Default
  searches (no collection / other collections) EXCLUDE chat windows (`source_kind <> 'chat_index'` in `filters_sql`, map too):
  past conversations are searched only on request (§3, §9), so today's results do not change when a user has an index.
- Counters/summary: chat windows are not counted (counter conditions `plain`, marker `counters_built_5`: one rebuild at the
  first start); chats have their own numbers in `orion_mem_chat_state`.
- Schema 3 -> 4 (backup first, additive): `orion_mem_chat_state (user_id, chat_id, memory_enabled, indexed_upto, group_id,
  item_id, windows, vector_space, indexed_at)`, `orion_mem_chat_index_jobs`, partial btree `idx_orion_memory_chat`.
- Indexes (background build, `_index_plan`): chat windows must not slow ordinary searches, so the general indexes became
  partial: `idx_orion_memory_ann_main` (HNSW `where source_kind <> 'chat_index'`) replaces `idx_orion_memory_ann` (dropped
  once the new one is valid: a rebuild of the big HNSW in the background at the first start on a real DB, minutes at 1M rows;
  searches keep using the old one meanwhile); `idx_orion_memory_ann_chats` (HNSW, chat windows only); newest/user btrees in
  `_plain`/`_chats` pairs (the old full `idx_orion_memory_newest` dropped when both are valid). Every vector search names
  the source kind literally (`filters_sql`), the only two ANN paths (`_nearest_groups`, `memory_filters.group_scores`)
  go through it. `memory_filters.sample_scores` (the query's typical score) now reads the newest rows in time order from
  the partial index (a materialized CTE; trash checked after) — walking the primary key backwards skipped every chat window.
- `collections.revert` also removes the user's chat windows/state/job (derived data).

## Numbers (throwaway PG, 80k ordinary rows + 80k chat windows in 4000 chats, one user, 768-d clustered vectors, p50 of 30)
`list_groups`, whole call incl. Python; "before" = the 80k ordinary rows alone, "after" = plus 80k chat windows:
| search | before | after |
|---|---|---|
| newest list | 64 ms | 63 ms |
| semantic, default (no collection) | 87 ms | 97 ms |
| semantic, kind=text | 88 ms | 86 ms |
| keyword, default | 69 ms | 71 ms |
| semantic `in chats` | - | 95 ms (820 ms before the chat HNSW/literal filter, 330 ms with no chat index) |
| semantic in ONE chat (`chat:<id>`) | - | 83 ms |
| keyword `in chats` | - | 98 ms |
First attempts showed what the work above fixed: the newest list went 64 -> 105 ms (chat rows are newest: skipped one
by one), default semantic 82 -> 110 ms (sample_scores walking past chat rows; the ANN itself is 1 ms). Script:
`scratchpad/perf_chat.py` (not in the repo; M=80000). NOT measured: 1M rows (the scale bench DB), concurrency, a real
embedding model. Baseline per call (~64 ms for a newest list) is the existing Python/summary cost at 80k rows on this PC.
Chat indexing cost itself (embedding) was not timed: tests use hand-made vectors.

## Flutter
Sohbetler strip: a note "x/y sohbet dizinli · n bölüm · k sohbet bekliyor" with "Eski sohbetleri dizinle", progress +
Durdur (polls with `jobPoll`), result card, "kapalı" note when the setting is off. Test: `memory_page_test.dart` (2 new).

## Tests
Hub `tests/test_memory_chat_index.py` (13, real throwaway PG): windows/ids/prefixes/no tool output/no failed turns,
long message alone, default vs `in chats` vs one chat vs other user, incremental embed counts, deleted message, chat
delete (owner / wrong owner / admin path), user isolation of state+job tables, per-chat switch, backlog + cancel, router
down, setting off + debounce, revert, trash. Flutter: analyze clean, memory tests pass.

## Not live-checked
Anything in the running app (dev Hub runs old code): the schema step + counters rebuild on the real DB, a real embedding
model/Router, a real turn triggering the index, the page strip against the real Hub, phone layout of the banner.
Not done: opening a chat AT the message from the page/panel (the data is there: `chat.message`; MemoryPage has no way to
open the chat view — needs a callback from home_page); no Flutter UI for the per-chat switch (API only) nor for the
`memory_index_chats` setting; chat rename does not rewrite the stored title of the windows (card title stale until the
next re-index of that chat).
