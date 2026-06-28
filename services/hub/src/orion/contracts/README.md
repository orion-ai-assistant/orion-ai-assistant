# Contracts Boundary Guide

`orion.contracts` is the shared language between API and worker.

## What belongs here

- DTOs and schemas only
- Event contracts
- Queue record contracts
- Shared constants used by both sides

## What does NOT belong here

- Business logic
- Redis/DB/LLM clients
- Runtime context objects
- Agent, tool, retrieval, pipeline code
- Environment/settings loading

## Organization rule

Keep contracts grouped by protocol/flow, not by service ownership:

- `http.py` for HTTP request/response models
- `queue.py` for stream/queue payload models
- `events.py` for realtime event models
- `constants.py` for shared names/keys

Do not create `contracts/api` or `contracts/worker` unless one side has contracts that are truly private and never shared.

## Evolution rule (OCP-friendly)

Prefer adding new models/files instead of modifying stable wire contracts.
When a breaking contract change is unavoidable, introduce a versioned model (for example, `JobCreateRequestV2`) and migrate consumers gradually.
