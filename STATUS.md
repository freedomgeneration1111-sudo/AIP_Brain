# AIP Project Status

**Date:** 2026-05-29
**Phase:** 9 stabilization complete (alpha)
**Overall Maturity:** Functional alpha — core architecture sound, evaluation honest, storage async-safe. Several stubs remain in non-critical paths.

## What Is Runnable Today

### Fully Functional
- **API server** (`uvicorn aip.adapter.api.app:create_app --factory`): 11 routers, auth middleware, rate limiting, CORS
- **CLI** (`aip init`, `aip status`, `aip session start`, `aip project list/show/create`, `aip config show`): Working commands backed by real SQLite stores
- **Artifact lifecycle**: Specification → Synthesis → Validation → Evaluation → DEFINER Gate → Commit → Canonical Promotion, all with real storage and ECS enforcement
- **Evaluation pipeline**: Faithfulness, domain coherence, and adversarial evaluation with real model dispatch. CI fixture data flagged and blocked from production promotion.
- **DEFINER gate**: AUTO_APPROVE_STUB mode (CI/production differentiated) and MANUAL mode (raises `ManualReviewRequired` with full context for UI integration)
- **Autonomy gate**: All privileged writes audited, non-DEFINER escalation blocked
- **Beast actor**: Health checks, corpus re-embedding, entity consistency, background scheduler
- **Vector search**: pgvector (production) / sqlite-vss (laptop) / in-memory (CI) factory with automatic fallback
- **All SQLite stores**: Migrated to `aiosqlite` — no blocking `sqlite3` in async methods

### Functional with Caveats
- **Model provider dispatch**: Works for Ollama and OpenAI-compatible endpoints. Anthropic not yet supported. Streaming not yet supported.
- **Chat API**: Functional but system prompt is templated, not model-generated for summarization
- **MCP server**: Tool registry and autonomy gate enforcement work, but tool dispatch returns hardcoded results
- **Performance API**: Routes exist but delegate to an uninitialized `PerformanceProfiler`
- **Workflow engine**: Sequential + parallel execution works, but `ScriptNode.run()` is a placeholder and `_AlwaysApproveDialogNode` always approves in `workflow_01.py`

### Not Yet Functional
- **MANUAL mode UI**: `ManualReviewRequired` exception carries full context, but no review queue store or human approval UI exists yet
- **Adaptive router**: `update_weights()` is a no-op, exploration/exploitation uses `random.random() < 0.10`
- **ECS persistence**: `ecs_store_guardrailed.py` uses in-memory cache — state lost on process restart
- **Sexton intervention rules**: `derive_intervention_rule()` returns None (stub)
- **Vector migration**: `migrate.py` uses zero-vectors, destroying real embeddings during migration
- **Knowledge store**: `store_compiled` inserts `[0.0]*384` zero-vectors, making semantic search non-functional for compiled knowledge

## Major Stubs & Scaffolding

| Component | Issue | Risk | Estimated Scaffolding % |
|---|---|---|---|
| `workflow_01.py` | `_AlwaysApproveDialogNode` always approves, ScriptNodes never execute | Critical | 60% |
| `router.py` | "Adaptive" router is not adaptive — `update_weights()` is `pass` | Critical | 65% |
| `ecs_store_guardrailed.py` | In-memory only, state lost on restart | High | 35% |
| `migrate.py` | Zero-vector retrieval destroys real embeddings | Critical | 70% |
| `sqlite_knowledge_store.py` | Zero-vector embeddings for all APPROVED items | Critical | 35% |
| `mcp/server.py` | All dispatch returns hardcoded results | High | 70% |
| `health.py` | Embedding status hardcoded healthy | High | 40% |
| `perf.py` | Memory usage returns fake proportional breakdown | Medium | 45% |
| `context.py` | Infinite budget by default (`budget_remaining=None`) | Critical | 30% |

## Test Suite

- **640 tests** total (627 passing, 3 pre-existing failures in layer-import boundary checks)
- **51 `assert True` no-op tests** across 23 test files
- Key test files: `test_definer_gate.py` (15 tests), `test_commit_node.py` (7 tests), `test_definer_sovereignty.py` (11 acceptance tests)

## How to Run Locally

```bash
# Install
uv sync

# Run tests
uv run pytest

# Start API server (laptop profile, auth disabled by default)
uv run uvicorn aip.adapter.api.app:create_app --factory --reload

# Initialize project
uv run aip init

# Check system status
uv run aip status
```

## How to Run with Docker

```bash
# Laptop profile (SQLite + Ollama)
cd deploy
docker compose --profile laptop up --build

# Production profile (PostgreSQL + pgvector)
# IMPORTANT: Set POSTGRES_PASSWORD before running!
export POSTGRES_PASSWORD=$(openssl rand -hex 32)
docker compose -f deploy/docker-compose.production.yml up --build -d
```

## Deployment Security Notes

- **Production profile** requires `POSTGRES_PASSWORD` to be explicitly set — no insecure defaults.
- **Auth middleware**: When `auth_enabled=false` (laptop profile default), all requests are treated as DEFINER. A warning is logged on every request.
- **Rate limiting**: Enabled by default (60 req/min, burst 10).
- **Autonomy gate**: Always enforced regardless of auth setting.

## Remaining High-Priority Work

1. **Fix `WorkflowContext` infinite budget** — `budget_remaining=None` means `consume_budget()` always returns True
2. **Implement Adaptive Router** — `update_weights()` is a no-op, exploration weight is hardcoded
3. **Build review queue UI** for MANUAL mode — `ManualReviewRequired` exception provides all needed context
4. **Fix vector migration** — cursor-based batching, preserve real embeddings
5. **Fix knowledge store embeddings** — accept real embeddings in `store_compiled`
6. **Add PENDING state to ECS graph** — artifacts waiting for human review need a proper lifecycle state
7. **Persist ECS state** — survive process restart with SQLite-backed store
8. **Fix Sexton event write signatures** — passes dicts instead of kwargs to `write_event()`
