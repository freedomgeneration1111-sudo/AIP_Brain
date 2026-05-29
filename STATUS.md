# AIP Status

**Version:** 0.1.0-alpha
**Architecture Revision:** 5.2
**Last Updated:** 2026-05-29

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

- **Tests:** 763 passing, 15 skipped (sqlite_vss extension)
- **Architecture:** Three-layer (foundation → orchestration → adapter)
- **Scaffolding:** ~5-8% overall
- **Docker:** Laptop and production profiles with programmatic config validation

## Runtime Gap Closure (P9)

The following runtime gaps have been addressed. No known gap returns fake success.

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
