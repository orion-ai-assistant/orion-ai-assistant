# Memory v2 step 5 (docs/hafiza-tasarimi.md §16): events + albums — hand-over

Branches (nothing pushed, nothing merged; merge BOTH together, on top of steps 1-4):
- Hub: `worktree-agent-a6f4ed240a39c576c` (worktree `.claude/worktrees/agent-a6f4ed240a39c576c`), from main a3dc4ff (step 4).
- Flutter: `memory-v2-step5` (worktree `../orion-ai-assistant-flutter-step5`), from main 6ec38bd (step 4).

## What it is
"Yaşadıklarım": events (a dated experience: title, text, day / month / year / approximate period, who took part, where, photos),
the timeline tab, albums (collections the user opens, a memory may be in several), yearly recurrences (birthdays,
anniversaries) and the `when` filter of `search_memory`. All behind `memory_v2` (per user); with it off nothing runs and
`add_memory`'s default (`type: note`) is today's behaviour. One implementation per area, used by the page routes, the tool and
the Flutter panel: `kernel/memory_events.py` and `kernel/memory_albums.py` (read their docstrings). Schema 5 -> 6 (backup first,
additive): `orion_mem_event`, `orion_mem_link`, `orion_mem_fact.recurring` (every table has `user_id`, every read filters by it).

## Decisions worth knowing (where it differs from the design text)
- **An event's vector is an ordinary `orion_memory` row** (`kind 'text'`, `source_kind 'event'`, its own group, main collection
  `yasadiklarim`, title = the event's title, text = "2026-11-17 Salı, Kasım 2026 (bugün): Annemin doğum günü. <text> Katılanlar: Ayşe,
  Ben. Yer: Kadıköy."). Like a fact: page, map, counters, trash, model move and `search_memory` need no new code; an event is live
  while that memory has a live row (the page's delete / the trash remove it, restoring brings it back, deleting for good removes
  the event rows: `memory_events.after_purge`, called next to the facts' one). Events are never locked (the design has no
  sensitive kinds for events). Text is one row: title + text at most 160 + 2000 characters (the design's windows for a long diary
  stay with notes: "keep a longer account as a note").
- **Dates (§4, decision 11).** The model writes ISO (`YYYY-MM-DD`, `YYYY-MM`, `YYYY`); `resolve_date` checks it against the
  message's own time (the tool: now, in the Hub PC's time zone; the extraction of step 7 will pass the message's time):
  an EXACT relative word in `said_as` from a short table (bugün, dün, evvelsi gün, yarın, geçen/last <weekday>, today,
  yesterday, tomorrow, last friday...) is resolved by the Hub and WINS over a date that disagrees (the answer carries
  `date_note`); every other phrase ("geçen yaz") keeps the model's date and its `precision` (approx). No date = refused, and the
  error says today's date and weekday. Stored: `occurred_on` (the first day), `occurred_end` (last day of a range / month / year),
  `precision` day | month | year | approx, `said_as`. A month / year covers its whole span, so overlap tests work with one
  rule. No threshold anywhere; a future date is allowed (a plan).
- **Versions, no overwrite.** The user's correction of an event (page `PUT`) is a new row (same `lineage`), the old row closed
  (`expired_at`, `superseded_by`) and its vector row deleted, the links move to the new version. The model cannot change an
  event (only add one); a duplicate (same folded title, same day and precision) adds its source and links and answers `same_as`.
- **Links** `orion_mem_link` (user_id in the key): event -> entity (`katılan`, `yer`; names resolved exactly like `about` of a
  fact: exact folded name / relation / alias, `#<id>`, unknown = a new person / place, two matches = `Ambiguous`) and event -> item
  (`fotoğraf`; an item is a memory, named by its group key; photos stay memories of their own, nothing is copied). An item in the
  trash shows greyed (`removed: true`) and is linked again on restore; deleted for good its link goes (and non-main album
  memberships of gone memories). **Entity merge moves the links** (what the design's `moved.links` was reserved for): recorded as
  `{event, role, dup}`, `undo_merge` puts them back (a link whose event was corrected since stays on the survivor).
- **Photo linking from the model:** `add_memory(type="event", event=<id>, items=[ids])` links to an existing event; `items` on a
  new event links at creation; `memory_context` on a photo lists the events it belongs to, on an event its photos and sources.
  Search results of events carry `event {id, date, end, precision, title, with, place, photos}` (one query, only when the page
  has an event result: no extra round trip otherwise).
- **`when` (§9)** `search_memory(when={from, to})` and `GET /memory?when_from=&when_to=`: the dated memories are named by one
  query (`memory_events.when_keys`), then they are the search's candidates (`when_sql`, plain group / row lookups; ranked
  exactly, see Numbers): events whose days overlap the range, facts that were true then (they must have a start; open-ended
  ones count) and the photos linked to such events. A partial date is a whole month / year (`from` = its first day, `to` = its last).
  Undated memories (notes, files) are never returned while it is set; it is ignored for a user without `memory_v2`. Works with
  `in`, `kind`, keywords and the newest-first list.
- **Albums** (kind `album` in `orion_mem_collection`, id `a_<hex>`; `memory_albums.py`): create / rename / remove / restore, put
  in / take out. A member is a NON-main row of `orion_mem_collection_item` (a memory's main collection never changes, its
  `group_id` / parts / vectors never change, so `open_memory_files` and `delete_memory(whole_memory)` work as before). Names are
  unique after folding, not a built-in's name and not an `in` word. Removing an album is soft (`deleted_at`, members kept), Geri al
  restores it whole (refused if the name was taken meanwhile). The model files a new note / event into an album that exists
  (`collection`), it never creates one (an unknown name lists what there is). Search takes an album's name or id in `in`.
- **Yearly recurrences (§4):** a fact with `recurring = 'yearly:MM-DD'` (`add_memory(type="fact", repeats="yearly", date=...)`, page
  `recurring`); a new version keeps it unless told otherwise (`""` stops it); the same sentence added again only adds the
  recurrence. `memory_events.upcoming` lists the next ones (29 February is 28 February in a year without one); locked facts
  never appear. Only facts repeat (an event is one occasion): the design's birthday example is an event ("bugün annemin doğum
  günüydü") plus a recurring fact, and the model makes both calls.
- Cards (§8): the model's events are written at once (a clearly stated event, source = the chat); no new card kind. Step 6
  generalises cards and "Son eklenenler" (events will show there with Geri al).

## API (all `memory_v2`; 400 when off, 404 for anything not the user's)
`GET /api/v1/memory/events?start=&end=&entity_id=&limit=&offset=` ({events, more, years}), `GET .../events/upcoming?days=`,
`POST .../events`, `GET|PUT|PATCH .../events/{id}`, `POST .../events/{id}/remove` (= `DELETE`; answer `trash_id`: undo through
`POST /memory/trash/restore`), `POST .../events/{id}/items {add, remove}`; `GET|POST /memory/albums`, `PUT|PATCH .../albums/{id}`,
`POST .../albums/{id}/remove` (= `DELETE`), `POST .../albums/{id}/restore`, `POST .../albums/{id}/items {add, remove}`,
`GET /memory/groups/{group}/albums`; `GET /memory` gains `when_from`, `when_to`; fact bodies gain `recurring`.
Tool: `add_memory` gains `type=event` and `date`, `end_date`, `precision`, `said_as`, `participants`, `place`, `mood`, `items`,
`event`, `collection`, `repeats` (English names and descriptions; defaults keep today's behaviour); `search_memory` gains `when`.

## Flutter (`memory_events.dart`, `memory_albums.dart`)
Yaşadıklarım pill shows `MemoryEventsPanel`: "Yaklaşanlar" (the next 30 days of recurrences), a "Yeni olay" button, filters by year
and by person / place (the latest choice wins: an older answer is dropped), the timeline newest first with a header per month, each
card with its date as it was said (`17 Kas 2026 Sal`, `5 – 8 Haz 2026`, `Kasım 2026`, `yaklaşık Temmuz 2025`, the user's own words),
people, place and photos (4 + "+n", a photo in the trash a grey square), more events load while scrolling. The card menu / sheet:
Düzenle (same form), Kaldır (the card folds away at once, "Geri al" restores through the trash), Fotoğraf bağla (a picker of the
recent pictures and videos), unlink (the photo stays). Albums: a "+ Albüm" pill makes one and opens it; an album shows its
members with the ordinary list and has Yeniden adlandır / Albümü kaldır (with Geri al); a memory's detail has "Albümler"
(checkboxes flip at once, a refusal puts them back).

## Numbers
Throwaway PG (persistent cluster, loaded once and used by both code versions), ONE user, 40k ordinary notes + 3000 facts + 3000
events (every one of them contains the query's words, so the word match and the tagging have real work), 768-d CLUSTERED vectors
(300 centres, noise ~0.3: not random), HNSW + trigram indexes built, the whole `search_memory` tool call, p50 of 40, three rounds
with the two versions alternating (order flipped every round, so the PC's load hits both). "Before" = step 4 code (a3dc4ff),
"after" = this branch, the SAME database. Scripts `scratchpad/bench_load.py`, `bench_time.py`, `bench_tag.py` (not in the repo).
| search | before (3 rounds) | after (3 rounds) |
|---|---|---|
| default (whole memory) | 165 / 166 / 157 ms | 165 / 178 / 164 ms |
| `in: ["me"]` (all facts) | 173 / 202 / 172 ms | 175 / 177 / 176 ms |
| `in: ["experiences"]` | 170 / 169 / 171 ms (events untagged) | 171 / 170 / 173 ms (every result an event, tagged) |
| keyword "kedi" | 105 / 112 / 111 ms | 102 / 105 / 108 ms |
| newest first | 66 / 63 / 63 ms | 67 / 67 / 72 ms |
| `when` one month (new) | - | 103 / 101 / 108 ms |
| `when` + `in: ["experiences"]` (new) | - | 118 / 107 / 113 ms |
What to read: the default search is unchanged within the noise (the default path adds one lookup only when a page has an event
card: `events_on`, 0.20 ms on its own; `facts_on` 0.22 ms; a round trip to the database is 0.09 ms). Nothing needed fixing; the
~150 ms target is where it was (about 160 ms here, almost all of it the existing text + vector + summary work at 46k rows).
**The 420 / 494 ms of the step-4 note was mostly not the search.** Those runs left the embedding lookup (`memory.embedding_target`,
which asks the Router installation) unpatched, and on a source checkout it scans the install directories in a thread on every
call (~300 ms per search, ~6.4 s of an 8.9 s profile of 20 keyword searches, `release_packages.py`); the first bench of this step
had the same artefact (370 ms default) until the profile showed it. The numbers above stub only that lookup. Whether the real Hub
pays that scan per search on an installed layout is NOT measured here (worth a look: it is outside memory).
`when` first took 590 ms: a date range is far more selective than the vector index's filtered scan can use (it walks the graph up
to its limit), so a `when` search now names its own candidates first (`memory_events.when_keys`, one query) and ranks them
exactly (`_nearest_groups(listed=True)`, the order expression is not the index's), by plain group / row lookups: its cost follows
the number of dated memories, not the memory's size (a range covering the whole timeline ranks every dated memory exactly).
NOT measured: 1M rows, concurrency, a real embedding model, a `when` range covering tens of thousands of events.

## Tests
Hub `tests/test_memory_events.py` (24, real throwaway PG): date resolution (forms, precision, relative words from a message's own
day, a disagreeing date, no date), event written with source / vector / Yaşadıklarım / entities / search tag, relative words and
approx through the tool, refused values and defaults (`type=note` unchanged, v2 off), duplicate event, ambiguous name, `when`
(events, facts, photos, partial dates, keyword path, `in`, undated notes, bad range, v2 off), photos (link, twice, not the user's,
trash greyed, restore, purge, unlink never deletes), versions and link follow-up, remove / restore / purge with history, timeline
(order, years, pages, entity filter, range), albums (unique names, members, main collection untouched, tool `collection`, rename,
soft remove, restore, name taken), album forgets purged memories, yearly recurrences (tool, upcoming, versions, locked excluded),
entity merge moving event links and undo, the routes as HTTP, schema 5 -> 6 keeping what is there, user separation of every
new table (`orion_mem_event`, `orion_mem_link`, album rows, recurrences), one user's purge leaves the other's.
Flutter `test/memory_events_test.dart` (7): date wording, timeline (headers, precision, people, place, photos, upcoming, year and
person filters), write / correct / remove with Geri al, event sheet with photo link / unlink, the Yaşadıklarım pill, albums
(strip, bar, rename, remove with Geri al), album checkboxes in the memory detail.

## Not live-checked
Anything in the running app: the schema 5 -> 6 step on the real DB, a real embedding model writing an event, the model actually
choosing `type=event` / `said_as` / `repeats` / `items` (tool wording untested with a real chat model; the Hub only tells it
today's date in an error), the Yaşadıklarım tab and album bars against the real Hub on a phone, the photo picker with real
thumbnails. Not done (later steps): "Son eklenenler" + Geri al for model writes and a card for an event the model is unsure of
(step 6), extraction of events from chats with the message's time (step 7), a Flutter UI for an event's past versions (the
API returns them), the page search box does not search inside the timeline (the Ben panel is the same), `as_of` /
`include_past`, the entity card's "events of this person" link (the timeline filter is the way for now), opening the source
chat from an event, `forget_memory`-style removal of an entity with its events (§11.3), events in the map as their own node
colour.
