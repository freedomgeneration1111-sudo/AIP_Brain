# Phase 3 Exit Report — AIP Stabilization

## Summary

Phase 3 completes the AIP stabilization project. All critical stores are now using `aiosqlite`, the AdaptiveRouter makes real decisions based on outcome history, Type E failure streak detection works correctly, and the VectorStore protocol is complete with stale vector listing support.

---

## Priority 1: Complete Async SQLite Migration

### What was done
All remaining adapter stores using blocking `sqlite3` have been migrated to `aiosqlite`:

| Store | File | Status |
|-------|------|--------|
| `SqliteCanonicalStore` | `adapter/canonical/sqlite_canonical_store.py` | Migrated |
| `SqliteEntityStore` | `adapter/entity/sqlite_entity_store.py` | Migrated |
| `QueryableEventStore` | `adapter/event_store_queryable.py` | Migrated |
| `SqliteVigilStore` | `adapter/vigil/sqlite_vigil_store.py` | Migrated |
| `SqliteBudgetStore` | `adapter/budget_store_sqlite.py` | Migrated |
| `SqliteFts5LexicalStore` | `adapter/lexical/sqlite_fts5_store.py` | Migrated |
| `SqliteKnowledgeStore` | `adapter/knowledge/sqlite_knowledge_store.py` | Migrated |
| `SqliteVssVectorStore` | `adapter/vector/sqlite_vss_store.py` | Migrated |
| `SqliteSessionStore` | `adapter/auth/session_store.py` | Already aiosqlite (Phase 2) |
| `PgvectorStore` | `adapter/vector/pgvector_store.py` | Already async (uses asyncpg) |

### Migration pattern
Each store was migrated using a consistent pattern:
- **`__init__`**: Synchronous `sqlite3` connection for table creation (backward-compatible, fast, runs once)
- **All data methods**: Use `aiosqlite` for async-compatible database access
- **`_get_conn()`**: Returns an `aiosqlite.Connection` with `sqlite3.Row` row factory
- **Each method**: Opens its own connection, commits, and closes — no persistent connection held across the event loop

This pattern eliminates the risk of blocking the event loop while preserving backward compatibility (tests that construct stores without calling `await initialize()` still work because tables are created synchronously in `__init__`).

---

## Priority 2: Make AdaptiveRouter Actually Adaptive

### What was done

**`update_weights()`** — Now computes real weights from routing outcome history:
- Maintains an in-memory outcome history list: `(slot, domain, success, timestamp, latency_ms)`
- Groups outcomes by `(slot, domain)` pair
- Applies exponential decay to weight recent outcomes more heavily
- Computes success rate (70%) + latency score (30%) blend
- Updates `RoutingWeight` entries with `model_slot`, `domain`, `weight`, `sample_count`, `updated_at`

**`recommend_exploration_weight()`** — Derives from actual domain sample counts:
- Counts actual outcomes per domain from history (not hardcoded `count = 5`)
- Sparse domains (< `min_sample_count`) → high exploration (0.25)
- Dense, high-success domains (> 100 outcomes, > 85% success) → low exploration (0.05)
- Low-success domains (< 50% success) → moderate exploration (0.20)
- Default for well-sampled domains → config value (0.10)

**`_pick_non_optimal()`** — Prefers best-performing alternative:
- Queries weight table for the domain
- Selects the highest-weighted alternative slot
- Falls back to hardcoded list only when no weight data exists

**Logging** — Added structured logging:
- `logger.debug()` for exploration decisions
- `logger.info()` for weight updates
- `logger.debug()` for alternative selection

### Key change: outcome recording
`resolve_with_routing()` now calls `self._record_outcome(resolved_slot, domain, success, latency_ms)` after each model call, building up the history that `update_weights()` and `recommend_exploration_weight()` consume.

---

## Priority 3: Fix Remaining Infrastructure Issues

### 3a: substance_score default fixed

**Problem**: `substance_score` defaulted to 0.5 in two locations, but the Type E detection threshold is 0.4. Since 0.5 > 0.4, the detector would never fire for outcomes without an explicit `substance_score`.

**Fix**:
- `orchestration/l4/failure_streak.py:36`: Changed default from `0.5` to `0.3`
- `orchestration/trajectory/regulator.py:105`: Changed default from `0.5` to `0.3`

This ensures that when `claimed_done=True` and no `substance_score` is provided, the Type E detector treats the outcome as "low substance" and counts it toward a failure streak.

### 3b: list_stale_vectors() added to VectorStore protocol and implementations

Added `list_stale_vectors(threshold_days, domain, limit)` to:
- `foundation/protocols.py` — VectorStore protocol definition
- `adapter/vector/_in_memory.py` — Returns empty list (no timestamps in memory store)
- `adapter/vector/sqlite_vss_store.py` — Queries `vector_metadata.created_at` with cutoff filter
- `adapter/vector/pgvector_store.py` — Queries `vectors.updated_at` with cutoff filter

Also added `health_check()` to `SqliteVssVectorStore` (was missing from the Protocol implementation).

---

## Priority 4: Final Polish

### 4a: Removed stale `# type: ignore` comments
- `adapter/auth/collaborator.py`: Removed 4 `# type: ignore[attr-defined]` comments that were no longer needed (the `AuthStore` protocol now defines `create_user`, `update_user_role`, `revoke_user`, `list_users`)

### 4b: Removed dead code
- `adapter/knowledge/sqlite_knowledge_store.py`: Removed the dead `try: pass except Exception: pass` block in `search_compiled()` and replaced it with a comment explaining the vector search is deferred to the 10.1 compiler

### 4c: Added logging to API route handlers
- `adapter/api/routes/admin.py`: Added `logger.warning()` with `exc_info=True` to all 6 exception handlers that previously silently swallowed errors
- `adapter/api/routes/memory.py`: Added `logger.warning()` with `exc_info=True` to all 5 exception handlers that previously silently swallowed errors

### 4d: Dead code scan results
Scanned the entire `src/aip/` directory for remaining issues:
- **`pass`-only function bodies**: 5 found — all are intentional (Click CLI groups, close() no-ops, TrajectoryRegulator stateless init)
- **TODO/FIXME/HACK**: 0 found
- **Placeholder comments**: 18 found — most are legitimate phase-fenced code awaiting future integration (10.1 compiler, embedding providers, etc.)
- **Scaffold API/CLI responses**: 14 found — these are scaffold surface code awaiting real wiring; left in place as they are documented and don't break anything

---

## Test Suite Results

```
511 passed, 9 skipped, 0 failures
```

Skipped tests are for `asyncpg` (requires PostgreSQL) and `sqlite_vss` extension availability — not available in the CI environment.

---

## Exit Criteria Verification

| Criterion | Status |
|-----------|--------|
| All critical stores using `aiosqlite` | ✅ Complete — all 9 SQLite stores migrated |
| `AdaptiveRouter.update_weights()` has real implementation | ✅ Complete — uses outcome history with exponential decay |
| Type E detection works reliably | ✅ Complete — substance_score default fixed to 0.3 |
| VectorStore protocol issues resolved | ✅ Complete — `list_stale_vectors()` added to protocol + all implementations |
| Test suite passes cleanly | ✅ Complete — 511 passed, 0 failures |

---

## Files Modified in Phase 3

| File | Change |
|------|--------|
| `src/aip/adapter/canonical/sqlite_canonical_store.py` | Migrated to aiosqlite |
| `src/aip/adapter/entity/sqlite_entity_store.py` | Migrated to aiosqlite |
| `src/aip/adapter/event_store_queryable.py` | Migrated to aiosqlite |
| `src/aip/adapter/vigil/sqlite_vigil_store.py` | Migrated to aiosqlite |
| `src/aip/adapter/budget_store_sqlite.py` | Migrated to aiosqlite |
| `src/aip/adapter/lexical/sqlite_fts5_store.py` | Migrated to aiosqlite |
| `src/aip/adapter/knowledge/sqlite_knowledge_store.py` | Migrated to aiosqlite, removed dead try:pass block |
| `src/aip/adapter/vector/sqlite_vss_store.py` | Migrated to aiosqlite, added health_check + list_stale_vectors |
| `src/aip/adapter/vector/pgvector_store.py` | Added list_stale_vectors |
| `src/aip/adapter/vector/_in_memory.py` | Added list_stale_vectors |
| `src/aip/foundation/protocols.py` | Added list_stale_vectors to VectorStore protocol |
| `src/aip/orchestration/router.py` | Real update_weights, recommend_exploration_weight, outcome recording, logging |
| `src/aip/orchestration/l4/failure_streak.py` | Fixed substance_score default 0.5 → 0.3 |
| `src/aip/orchestration/trajectory/regulator.py` | Fixed substance_score default 0.5 → 0.3 |
| `src/aip/adapter/auth/collaborator.py` | Removed 4 stale # type: ignore comments |
| `src/aip/adapter/api/routes/admin.py` | Added logging for exception handlers |
| `src/aip/adapter/api/routes/memory.py` | Added logging for exception handlers |

---

## Known Remaining Items (out of scope for Phase 3)

1. **Scaffold API/CLI surfaces** — Many routes still return placeholder data (review, chat, projects, etc.). These require real store wiring in future phases.
2. **Placeholder embeddings** — `SqliteKnowledgeStore` uses `[0.0] * 384` dummy embeddings. Real embedding requires the 10.1 compiler.
3. **ScriptNode.run()** — Returns fake success without executing. Needs real implementation.
4. **bcrypt dependency** — Not declared in `pyproject.toml` but required by `session_store.py`.
5. **httpx/fastapi dependencies** — Used in adapter but not declared in `pyproject.toml`.
