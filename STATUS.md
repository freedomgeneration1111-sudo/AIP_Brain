# AIP Status

**Version:** 0.1.0-alpha
**Architecture Revision:** 5.3
**Last Updated:** 2026-06-06

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

- **Tests:** 1002 passing, 23 skipped (sqlite_vss extension + pre-existing governance), 2 pre-existing failures
- **Architecture:** Three-layer (foundation → orchestration → adapter)
- **Default DB path:** `db/state.db` (SQLite, laptop profile)
- **Scaffolding:** ~5-8% overall (MCP dispatch, adaptive router, ScriptNode sandbox)
- **Docker:** Laptop and production profiles with programmatic config validation
- **Lint:** ruff format + ruff check (E, F, W, I) — all passing, blocking in CI

## Actor Status (post ADR-011 refactor)

ADR-011 (2026-06-06) redefined actor role boundaries. The code refactor is committed
(`3d4bd44` through `0c289a9`). One wiring gap remains open — see DEBT-006.

| Actor | Role (ADR-011) | Code State | Wired in app.py | Notes |
|-------|---------------|------------|-----------------|-------|
| Beast | Active synthesis support — context advisory, on-demand wiki draft | ✅ Refactored | ✅ Scheduled (heartbeat only) | Maintenance ops removed per ADR-011 |
| Sexton | Background maintenance — tagging, embedding, wiki, graph, classification | ✅ Built (actors/sexton.py, 1,341 lines, all 5 ops) | ❌ **NOT WIRED** — app.py still calls old sexton/sexton.py::run_classification_cycle() | **DEBT-006** — tagging, embedding, wiki, graph are NOT running |
| Vigil | Quality evaluation — synthesis citation quality, profile amendments | ✅ Refactored | ✅ Scheduled (hourly) | Maintenance ops removed per ADR-011 |

**DEBT-006 impact:** Automatic corpus tagging, embedding, wiki generation, and graph extraction are
not running. Only failure classification (old Sexton) fires every 300s. The new full-maintenance
`actors/sexton.py` is dead code until wired.

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

## Corpus Status (as of 2026-06-06)

### Worktree (`~/.grok/worktrees/moses-aip-brain/aip-brain`)

| Source Account | Turns | Tagged | Notes |
|----------------|-------|--------|-------|
| claude_export_june_2026 | 2,691 | 2,691 | Primary corpus |
| claude_export_2024_2025 | 52 | 52 | Previous account |
| aip_v0.1_seed | 23 | 23 | AIP self-knowledge Q&A |
| **Total** | **2,766** | **2,766** | 100% tagged |

Beast domain registry: v1.1 — 28 domains, 17 connectors
Vector store: 50 vectors (from embed --limit 50) — embedding pipeline Phase 1.4
Knowledge graph: **36 nodes, 17 edges** — worktree (post full bridge seed run)

### Seed Repo (`~/AIP_Brain`)

Knowledge graph: **28 nodes, 5 edges** — fresh seed corpus only
  - 6 DOMAIN nodes, 7 PROJECT, 6 PERSON, 6 CONCEPT, 3 MANUSCRIPT
  - Bridge edges: 5 | Extracted edges: 0 (Beast extraction needs active API key)
  - Visualization: /graph-viz (Cytoscape.js, dark-mode, filterable)
  - aip_methodology orphan node present (pre-rename artifact — see TECH_DEBT.md#DEBT-001)

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

## Wiki Articles Status (as of 2026-06-06)

| Domain | Status | Words |
|--------|--------|-------|
| theology_research | APPROVED | 1,266 |
| nbcm | GENERATED | 708 |
| ministry | GENERATED | 871 |
| scripture_linguistics | GENERATED | 841 |
| All others (22) | NO WIKI — no tagged turns yet | — |

Beast generates wikis from tagged turns only. 2,649 turns are still untagged.
After corpus retag completes (blocked on DEBT-006 fix), remaining domain wikis will
generate in Sexton vigil cycles.

## Knowledge Graph Status (as of 2026-06-06)

| Component | Status | Notes |
|-----------|--------|-------|
| graph_nodes / graph_edges tables | COMPLETE | state.db, synchronous GraphStore |
| Bridge seed (`--build-from-bridges`) | COMPLETE | 28 nodes, 5 edges (seed) / 36 nodes, 17 edges (worktree) |
| Entity alias registry | COMPLETE | 22 entries from entity_aliases.md |
| Beast extraction (`--extract`) | COMPLETE (infra) | Requires active Beast LLM API key |
| PPR retrieval | COMPLETE (infra) | GraphRetriever with networkx pagerank |
| Graph API endpoints | COMPLETE | /api/v1/graph/data, /neighbors, /stats |
| Cytoscape.js visualization | COMPLETE | /graph-viz standalone dark-mode page |
| Chat augmentation | COMPLETE | Domain neighbor injection in augmented chat |

## Next Priorities — Bug Registry

Active bugs to fix, in order. Each gets one commit.

| # | Bug | File | Fix |
|---|-----|------|-----|
| BUG-001 | Project lost on restart: `aip init` creates no default project — `list_projects()` returns empty → GUI shows `NO_PROJECT_MEMORY` | `src/aip/cli/init.py` | After DB init, instantiate `SqliteProjectStore` and call `create_project("default", "Default")` |
| BUG-002 | `chat.py` uses `get_default_db_path()` for GraphStore instead of `container.config.get("db_path")` — path mismatch if config differs | `src/aip/adapter/api/routes/chat.py` | Replace `get_default_db_path()` with `container.config.get("db_path", ...)` pattern from `graph.py` commit `f269407` |
| BUG-003 | `actors/sexton.py` never instantiated — app.py still wires old `sexton/sexton.py::run_classification_cycle()` — tagging, embedding, wiki, graph NOT running | `src/aip/adapter/api/app.py` | Instantiate `actors/sexton.Sexton` with all required stores; schedule `run_cycle()` every 300s — see TECH_DEBT.md#DEBT-006 |
| BUG-004 | `GraphStore` has no Protocol in `storage.py`; `adapter/graph_store.py` uses synchronous `sqlite3` — blocks async routes | `src/aip/foundation/protocols/storage.py`, `src/aip/adapter/graph_store.py` | Add `GraphStore` Protocol; convert `graph_store.py` to `aiosqlite`; inject via container — see TECH_DEBT.md#DEBT-005 |

**Test strategy:** Bugs 1-3 on `~/AIP_Brain` (fresh seed) first. When confirmed working, pull to worktree.
