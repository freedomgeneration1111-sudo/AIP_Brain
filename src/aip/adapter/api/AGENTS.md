# API — Agent Navigation

## Purpose

FastAPI-facing adapter boundary. Routes translate authenticated external requests
into foundation-backed services and return explicit, source-grounded responses.

## Architecture Constraints

- Import from foundation and adapter only; never import orchestration directly.
- All async database access uses `aiosqlite`.
- Every `/admin/*` route requires its auth gate.

## Contracts

- `routes/model_council.py` preserves the Model Council request/response schema,
  including retrieval telemetry and advisory-only Fusion output.
- Augmented retrieval uses `session_id` when present and accepts legacy
  `turn_id` as a backward-compatible session-scope signal.
- `routes/wiki.py` queries all supported wiki artifact ID families without
  changing ECS state filters.

## Data Flows (In / Out)

- GUI/API clients → FastAPI routes → adapter stores and foundation protocols.
- Model Council route → GUI consumers with selected-model results, Fusion output,
  provenance telemetry, and retrieval warnings.

## Known Gotchas

- Reflowing SQL or structured logs must not alter predicates, ordering, bound
  parameters, or logger arguments.
- Compression task labels must be descriptive; avoid the ambiguous name `l`.

## Last Cycle

- Created during the merge lint repair to document route-level formatting and
  Model Council task-label safety.

## Key Files

- `routes/model_council.py` — Multi-Cast dispatch, Fusion, and telemetry.
- `routes/wiki.py` — wiki artifact listing and aggregate statistics.

## Work Guidance

- Keep route changes additive and preserve DEFINER gates and response contracts.
- Check GUI consumers before changing response fields.

## How to Test

```bash
uv run ruff check src/aip/adapter/api
uv run pytest tests/test_model_council_fusion.py tests/test_panel_dispatch_remediation.py
```
