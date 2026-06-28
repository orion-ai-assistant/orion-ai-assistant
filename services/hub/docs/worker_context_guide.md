# Worker JobContext Guide

This guide explains how to use `JobContext` while growing worker logic (LLM, tools, retrieval, vector DB) without turning `main.py` into a monolith.

## Current entrypoint

- Context class: `src/orion/worker/core/context.py`
- Worker loop: `src/orion/worker/main.py`

`main.py` should orchestrate flow only.
Runtime operations such as stream parsing and event publishing should go through `JobContext`.

## Golden rule

Worker logic should not call Redis publish directly.
Use:

- `await context.emit_token(token)`
- `await context.emit_done(status)`
- `await context.emit_error(message)`

## How to add new worker features

1. Parse and validate incoming job once in `JobContext`.
2. Keep transport-specific operations in `JobContext`.
3. Keep domain logic in worker modules (`agent/`, `tools/`, `retrieval/`, `pipelines/`).
4. Pass `context` to orchestration functions instead of passing many loose parameters.

## Suggested growth path

When you add LLM/RAG:

1. Keep adding orchestration in `worker/pipelines/`.
2. Let pipeline functions consume `context.prompt`, `context.chat_id`, `context.stream_mode`.
3. Emit realtime output through `context.emit_*`.
4. If dependency count grows (db, llm client, vector store), extend `JobContext` carefully with runtime fields.

## Anti-patterns to avoid

- Parsing `fields["..."]` in many places
- Recreating `JobQueueRecord`/payload parse multiple times
- Emitting raw dict events from random modules
- Putting runtime dependencies into `orion.contracts`

## Quick checklist before commit

- No direct `redis.publish(...)` outside `JobContext`
- No direct `fields["..."]` access in worker flow
- Stream events are produced via `StreamEvent` helpers
- Contracts remain DTO/schema-only
