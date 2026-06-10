# AIP Status

**Version:** 0.1.0-alpha
**Architecture Revision:** 6.4
**Last Updated:** 2026-06-11
**Release:** Alpha Test Release
**Project Mode:** MAINTENANCE — active development phase complete; see docs/Maintenance_Protocol.md

> This document reflects the state at the close of Sprint 6.4 and the alpha test release.
> The project has entered maintenance mode. No further feature sprints are planned.
> See ROADMAP.md for the maintenance mode section and docs/Maintenance_Protocol.md for operational procedures.

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

- **Tests:** 1002+ passing, 23 skipped (sqlite_vss extension + pre-existing governance), 2 pre-existing failures
- **Architecture:** Three-layer (foundation → orchestration → adapter)
- **Default DB path:** `db/state.db` (SQLite, laptop profile)
- **Scaffolding:** ~5-8% overall (MCP dispatch, adaptive router, ScriptNode sandbox)
- **Docker:** Laptop and production profiles with programmatic config validation
- **Lint:** ruff format + ruff check (E, F, W, I) — all passing, blocking in CI
- **Retrieval:** Hybrid (FTS5 + Vector + Corpus) with RRF fusion; configurable channel weights in `aip.config.toml` (`[retrieval.channel_weights]`)
- **Eval harness:** `aip eval retrieval` with --mode flag (hybrid / fts-only / all); baseline comparator available via `--save-baseline`

## Actor Status (post ADR-011 refactor, post Sprint 6.4)

ADR-011 (2026-06-06) redefined actor role boundaries. All three actors are built and wired.
DEBT-006 (Sexton wiring) is resolved — the new Sexton actor was already wired in app.py; docs
were stale. Chunk 3 (2026-06-11) added honest state reporting and fixed an L4 signature mismatch.

| Actor | Role (ADR-011) | Code State | Wired in app.py | Notes |
|-------|---------------|------------|-----------------|-------|
| Beast | Active synthesis support — context advisory, on-demand wiki draft | ✅ Refactored | ✅ Scheduled (heartbeat only) | Maintenance ops removed per ADR-011 |
| Sexton | Background maintenance — tagging, embedding, wiki, graph, classification | ✅ Built (actors/sexton.py, 2,100+ lines, all 5 ops) | ✅ Scheduled (300s) | All 5 vigil ops wired and running. Reports honest state (active/degraded/disabled/failed) |
| Vigil | Quality evaluation — synthesis citation quality, retrieval quality gate | ✅ Refactored + retrieval quality gate (Sprint 6.4) | ✅ Scheduled (hourly) | Now includes retrieval quality sampling with alerting |

## Retrieval Quality (Sprint 6.4)

| Component | Status | Notes |
|-----------|--------|-------|
| RetrievalEvalHarness | ✅ Complete | `aip eval retrieval` CLI with --mode flag |
| Golden queries | ✅ Updated | `tests/retrieval_goldens/golden_queries.json` with corpus-mapped IDs |
| Channel weight tuning | ✅ Script | `scripts/retrieval_weight_tuning.py` grid search |
| Config weights | ✅ Wired | `[retrieval.channel_weights]` in `aip.config.toml` → `OrchestratorConfig` |
| Baseline benchmark | ✅ Saved | `docs/retrieval_benchmark_baseline.json` |
| Vigil quality gate | ✅ Wired | Periodic precision@5 sampling with alerting on degradation |
| A/B comparison | ✅ Available | `aip eval retrieval-ab` for side-by-side config comparison |
| Budget tuning | ✅ Available | `aip eval budget-tune` for per-channel budget adjustments |

**Current channel weight defaults:** vector=0.6, fts=0.4, corpus=0.4
**Embedding coverage:** ~1.8% (50/2766 turns). Hybrid improvement over FTS5-only will be
measurable after full embedding pass completes (requires embedding provider configuration and
sustained server uptime for Sexton cycles to process the backlog).

## Runtime Gap Closure (P9)

All P9 runtime gaps have been addressed. No known gap returns fake success from core runtime paths.

| Gap | Status | Implementation | Tests |
|---|---|---|---|
| A. Collaborator password transport | ✅ Fixed | Password moved from query param to request body | test_collaborator_secret_transport.py |
| B. Performance API | ✅ Fixed | Returns BACKEND_UNAVAILABLE when not configured | test_performance_api_contract.py |
| C. Vector migration cursor scan | ✅ Fixed | list_all_ids() added to VectorStore protocol | test_vector_migration_cursor_scan.py |
| D. ECS persistent store | ✅ Fixed | PersistentEcsStore with SQLite backend | test_ecs_persistent_store.py |
| E. MANUAL review queue | ✅ Fixed | ReviewQueueStore with SQLite persistence | test_manual_review_queue.py |
| F. ScriptNode.run | ✅ Disabled | Production mode returns DISABLED; fixture mode safe no-op | test_workflow_script_node_contract.py |
| G. Vigil model-slot re-evaluation | ✅ Fixed | on_model_slot_change marks canonicals for re-evaluation | test_vigil_model_slot_re_evaluation.py |
| H. Sexton intervention derivation | ✅ Fixed | Deterministic rules for A-F + 7 special conditions | test_sexton_intervention_derivation.py |

## Corpus Status (as of 2026-06-10)

| Source Account | Turns | Tagged | Embedded | Notes |
|----------------|-------|--------|----------|-------|
| claude_export_june_2026 | 2,691 | 2,691 | 50 | Primary corpus |
| claude_export_2024_2025 | 52 | 52 | 0 | Previous account |
| aip_v0.1_seed | 23 | 23 | 0 | AIP self-knowledge Q&A |
| **Total** | **2,766** | **2,766** | **50** | 100% tagged, ~1.8% embedded |

Beast domain registry: v1.1 — 28 domains, 17 connectors
Vector store: 50 vectors (from `embed --limit 50`)
Knowledge graph: 36 nodes, 17 edges (worktree)

### Embedding Gap

2,716 turns remain unembedded. The Sexton actor is wired and will process embedding batches
automatically when an embedding provider is configured and the server is running. At ~50 turns
per cycle (every 300s), completing the full embedding pass requires approximately 17 hours of
continuous operation.

## Known Scaffolding

| Surface | What's Real | What's Scaffold |
|---|---|---|
| MCP tool dispatch | Tool listing, autonomy gate enforcement, layering discipline, real dispatch via Protocols | MCP server not wired into runtime; autonomy_gate=None fail-open risk for write/admin tools |
| Adaptive router | Budget enforcement, route existence | update_weights() is no-op; exploration/exploitation is random |
| ScriptNode | Type declaration, fixture mode, YAML parsing | Production execution disabled (returns DISABLED) |
| MCP start/stop | _running flag | No stdio/SSE transport implementation |

## Deployment Profiles

| Profile | Database | Auth | Vector | Models | Bind |
|---|---|---|---|---|---|
| laptop | SQLite | Optional (default: off) | sqlite_vss | Ollama / OpenRouter | 127.0.0.1 |
| production | PostgreSQL | Required | pgvector | API | 0.0.0.0 |

## Required Environment Variables (Production)

- `POSTGRES_PASSWORD` — **Required**. No default fallback.
- `AIP_PROFILE=production` — Activates production validation rules.

## Lint & Formatting Baseline

**Rules:** E (errors), F (pyflakes), W (warnings), I (import sorting)
**Formatter:** ruff format (line-length=120, target=py311)
**CI:** Both `ruff format --check .` and `ruff check .` are blocking gates.

### Contributor Workflow

```bash
uv run ruff format .
uv run ruff check . --fix
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pytest -q --tb=short
```

## Production-Readiness Statement

AIP v0.1 is **alpha software** released for testing and evaluation. It is suitable for local
development, single-user dogfood usage, and alpha tester evaluation. It is **not** production-ready
for deployment with real user data. Known limitations that alpha testers should be aware of:

1. **Embedding coverage is ~1.8%** (50/2,766 turns) — retrieval quality is limited until full
   embedding pass completes (requires embedding provider configuration and sustained uptime).
   FTS5 search works well; hybrid retrieval improvement will be measurable after full embedding.
   Sexton actor is wired and will process embeddings automatically when the provider is available.
2. **MCP tool dispatch is built but not runtime-wired** — real search and approval dispatch exists but is not reachable via API/CLI; autonomy_gate=None fail-open risk must be hardened before wiring
3. **Adaptive router does not adapt** — exploration/exploitation is random
4. **No sandbox for ScriptNode execution** — production mode returns DISABLED
5. **No review queue web UI for MANUAL mode** — CLI review works (`aip review list/approve/reject`)
6. **Per-component performance metrics are estimated**, not measured

## Pre-existing Test Failures

Two test files have known failures when run in the full suite (they pass in isolation):

- `test_model_slot_resolver.py`: 4 tests fail in full suite due to env var pollution (pass in isolation)
- `test_sqlite_vss_graceful_skip.py`: fails in full suite due to global state pollution (passes in isolation)

## Dogfood Loop

AIP runs its own ingest → ask → review → export pipeline on AIP development
conversations. The project eats its own dog food: architecture decisions, design
discussions, and meeting transcripts are ingested into `db/state.db` and queried
via `aip ask` to ground future design work in prior decisions.

## Knowledge Graph Status

| Component | Status | Notes |
|-----------|--------|-------|
| graph_nodes / graph_edges tables | COMPLETE | state.db, synchronous GraphStore |
| Bridge seed (`--build-from-bridges`) | COMPLETE | 36 nodes, 17 edges (worktree) |
| Entity alias registry | COMPLETE | 22 entries from entity_aliases.md |
| Beast extraction (`--extract`) | COMPLETE (infra) | Requires active Beast LLM API key |
| PPR retrieval | COMPLETE (infra) | GraphRetriever with networkx pagerank |
| Graph API endpoints | COMPLETE | /api/v1/graph/data, /neighbors, /stats |
| Cytoscape.js visualization | COMPLETE | /graph-viz standalone dark-mode page |
| Chat augmentation | COMPLETE | Domain neighbor injection in augmented chat |

## Bug Registry

All known bugs have been documented. See TECH_DEBT.md for the full debt register including
bug cross-references. DEBT-006/BUG-003 (Sexton not wired) is resolved as of Chunk 3.
