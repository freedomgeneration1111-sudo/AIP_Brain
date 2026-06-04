# AIP Status

**Version:** 0.1.0-alpha
**Architecture Revision:** 5.2
**Last Updated:** 2026-06-04

## Production Safety Status

Production configuration is **enforced programmatically**. Unsafe configs fail at startup with clear error messages.

### Blocked Configurations (Hard Failures)

| # | Unsafe Configuration | Error Code | Enforced Since |
|---|---|---|---|
| 1 | Production + auth disabled | `PROD_AUTH_DISABLED` | 2026-05-29 |
| 2 | Production + missing POSTGRES_PASSWORD | `PROD_MISSING_DB_PASSWORD` | 2026-05-29 |
| 3 | Production + weak/default password | `PROD_WEAK_DB_PASSWORD` | 2026-05-29 |
| 4 | Production + fixture embedding provider | `PROD_FIXTURE_PROVIDER` | 2026-05-29 |
| 5 | Production + fixture model provider | `PROD_FIXTURE_MODEL_PROVIDER` | 2026-05-29 |
| 6 | Public bind + auth disabled | `PUBLIC_NO_AUTH` | 2026-05-29 |
| 7 | Public bind + weak database password | `PUBLIC_WEAK_SECRET` | 2026-05-29 |

### Allowed Configurations

| Configuration | Condition |
|---|---|
| Laptop + localhost + auth disabled | Default for local development |
| Laptop + localhost + auth enabled | Also valid |
| Production + auth enabled + strong secrets | Required for deployment |

### Unsafe Override

`AIP_UNSAFE_ALLOW_PUBLIC_NO_AUTH=true` — allows public-bind + auth-disabled in laptop mode only. Does NOT bypass production auth requirements.

## Module Status

- **Tests:** 763 passing, 15 skipped (sqlite_vss extension), 0 failures
- **Architecture:** Three-layer (foundation → orchestration → adapter)
- **Default DB path:** `db/state.db` (SQLite, laptop profile)
- **Scaffolding:** ~5-8% overall (MCP dispatch, adaptive router, ScriptNode sandbox)
- **Docker:** Laptop and production profiles with programmatic config validation
- **Lint:** ruff format + ruff check (E, F, W, I) — all passing, blocking in CI

## Runtime Gap Closure (P9)

The following runtime gaps have been addressed. No known gap returns fake success from core runtime paths.

| Gap | Status | Implementation | Tests | Remaining limitation |
|---|---|---|---|---|
| A. Collaborator password transport | Fixed | Password moved from query param to request body via Pydantic model | test_collaborator_secret_transport.py | CLI already used hide_input |
| B. Performance API | Fixed | Returns BACKEND_UNAVAILABLE when not configured, DISABLED when profiling_enabled=False, real data when enabled | test_performance_api_contract.py | Per-component breakdown is estimated |
| C. Vector migration cursor scan | Fixed | list_all_ids() added to VectorStore protocol + all implementations; cursor-based migration when available, probe fallback | test_vector_migration_cursor_scan.py | Probe fallback may still miss vectors |
| D. ECS persistent store | Fixed | PersistentEcsStore with SQLite backend, aiosqlite, state cache + DB persistence | test_ecs_persistent_store.py | Postgres adapter deferred |
| E. MANUAL review queue | Fixed | ReviewQueueStore with SQLite persistence, DEFINER-only approval, no auto-approve | test_manual_review_queue.py | UI minimal (API/CLI only) |
| F. ScriptNode.run | Disabled | Production mode returns structured DISABLED; fixture mode returns safe no-op; YAML loader defaults to fixture mode | test_workflow_script_node_contract.py | Safe sandbox not yet implemented |
| G. Vigil model-slot re-evaluation | Fixed | on_model_slot_change marks affected canonicals for re-evaluation, writes trace events, respects batch size | test_vigil_model_slot_re_evaluation.py | Conservative: all canonicals marked as affected |
| H. Sexton intervention derivation | Fixed | Deterministic rules for A-F + 7 special conditions; unknown returns None | test_sexton_intervention_derivation.py | Complex multi-signal derivation deferred |

## Corpus Status (as of 2026-06-04)

| Source Account | Turns | Tagged | Notes |
|----------------|-------|--------|-------|
| claude_export_june_2026 | 2,691 | 2,691 | Primary corpus |
| claude_export_2024_2025 | 52 | 52 | Previous account |
| aip_v0.1_seed | 23 | 23 | AIP self-knowledge Q&A |
| **Total** | **2,766** | **2,766** | 100% tagged |

Beast domain registry: v1.1 — 28 domains, 17 connectors
Vector store: 50 vectors (from embed --limit 50) — embedding pipeline Phase 1.4
Knowledge graph: NOT BUILT — Phase 2B
Unclassified turns: 267 (parse failures + low-confidence — pending retag)
Residual aip_methodology turns: 5 (pending cleanup retag)
Embedded turns: 50
Unembedded turns: 2716

## Known Scaffolding

These surfaces have real structure (tool listing, auth gates, Protocol declarations) but their dispatch
logic returns scaffold or NOT_IMPLEMENTED responses rather than delegating to real services:

| Surface | What's Real | What's Scaffold |
|---|---|---|
| MCP tool dispatch | Tool listing, autonomy gate enforcement, layering discipline | aip_search returns empty; aip_artifact_approve returns hardcoded True; other tools return ok=True |
| Adaptive router | Budget enforcement, route existence | update_weights() is no-op; exploration/exploitation is random |
| ScriptNode | Type declaration, fixture mode, YAML parsing | Production execution disabled (returns DISABLED) |
| MCP start/stop | _running flag | No stdio/SSE transport implementation |

## Deployment Profiles

| Profile | Database | Auth | Vector | Models | Bind |
|---|---|---|---|---|---|
| laptop | SQLite | Optional (default: off) | sqlite_vss | Ollama | 127.0.0.1 |
| production | PostgreSQL | Required | pgvector | API | 0.0.0.0 |

## Required Environment Variables (Production)

- `POSTGRES_PASSWORD` — **Required**. No default fallback.
- `AIP_PROFILE=production` — Activates production validation rules.

## Lint & Formatting Baseline

The repo enforces a clean formatting and lint baseline in CI.

**Rules:** E (errors), F (pyflakes), W (warnings), I (import sorting)
**Formatter:** ruff format (line-length=120, target=py311)
**CI:** Both `ruff format --check .` and `ruff check .` are blocking gates.

### Contributor Workflow

```bash
# Format code
uv run ruff format .

# Fix auto-fixable lint issues
uv run ruff check . --fix

# Run tests
uv run pytest

# Verify everything passes (same as CI)
uv run ruff format --check .
uv run ruff check .
uv run pytest -q --tb=short
```

## Production-Readiness Statement

AIP v0.1 is **alpha software**. It is suitable for local development, evaluation, and testing. It is not
production-ready for deployment with real user data. Specific blockers:

1. MCP tool dispatch is scaffold — no real search/approval/config operations through MCP
2. Adaptive router does not adapt — exploration/exploitation is random
3. No sandbox for ScriptNode execution
4. No review queue web UI for MANUAL mode
5. Per-component performance metrics are estimated, not measured

## Dogfood Loop

AIP runs its own ingest → ask → review → export pipeline on AIP development
conversations. The project eats its own dog food: architecture decisions, design
discussions, and meeting transcripts are ingested into `db/state.db` and queried
via `aip ask` to ground future design work in prior decisions.

## Next Priorities

1. Complete Beast corpus retag with registry v1.1
   (aip hall model rename, new domains: ancient_archaeology, agi_philosophy)
2. Tag 52 untagged turns from claude_export_2024_2025 and 32 seed turns
3. Embedding pipeline: embed corpus_turns.searchable_text,
   hybrid FTS5+vector scoring in augmented chat retrieval
4. DEFINER profile injection in augmented chat system prompt
5. Beast wiki generation: domain-level first (28 articles),
   markdown editor UI, approval workflow
6. Knowledge graph: SQLite adjacency tables, bridge tags as seed edges,
   HippoRAG-inspired PPR retrieval, Cytoscape.js visualization
