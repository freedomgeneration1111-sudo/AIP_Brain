# AIP Implementation Status

**Date:** 2026-05-28
**Phase 0 Status:** Complete
**Overall Assessment:** The AIP 0.1 codebase has a well-designed three-layer architecture (foundation → orchestration → adapter) with sound Protocol-based dependency injection and comprehensive schema/protocol definitions. However, the implementation is approximately 35-40% scaffolding overall, with higher layers (evaluation, adaptive routing, CLI) reaching 60-90%. The most dangerous pattern is a **cascade of always-passing evaluation defaults** that makes the entire L3a quality pipeline effectively a no-op in default/CI mode: every evaluation silently falls back to scores above threshold, the DEFINER gate auto-approves, and artifacts are committed without real quality enforcement. The ModelSlotResolver has no real model provider dispatch — everything runs through deterministic CI fixtures. The workflow engine, L4 trajectory regulation (with caveats), and most SQLite stores are genuinely implemented and functional. The system no longer crashes on import or basic startup after Phase 0 P0 fixes.

## P0 Fixes Applied

| # | Bug | File | Fix |
|---|-----|------|-----|
| 1 | `or True` makes knowledge validation always pass | `orchestration/compilation.py:115` | Removed `or True` — validation now checks `len > 20 and "provenance" in content` |
| 2 | `ModelResolverProtocol.call()` sync but callers `await` it | `orchestration/model_provider_proxy.py:30` | Made `call()` async in Protocol; also fixed `resolve_slot` return type to `Any` |
| 3 | `register_provider()` called on `AdaptiveRouter` which lacks it | `orchestration/plugins.py:58-61` | Removed broken `register_provider` call on AdaptiveRouter; replaced with clear TODO comment |
| 4 | `run_until_complete()` inside async handler | `adapter/api/plugins.py:52` | Replaced with `await pm.health_check_all()` |
| 5a | Conditional FastAPI imports set `router = None` → decorator crash | `adapter/api/routes/chat.py` | Removed try/except ImportError; direct FastAPI import with `APIRouter()` |
| 5b | Same pattern | `adapter/api/routes/review.py` | Same fix |
| 5c | Same pattern | `adapter/api/routes/artifacts.py` | Same fix |
| 5d | Same pattern | `adapter/api/routes/admin.py` | Same fix; also fixed `get_weights()` → `get_routing_weights()` (async) |
| 5e | Same pattern | `adapter/api/routes/memory.py` | Same fix |
| 6 | `app.py` had conditional FastAPI import with dead `FastAPI is None` guard | `adapter/api/app.py` | Removed try/except; direct import; removed dead guard in `create_app` |
| 7 | `dependencies.py` had conditional FastAPI import | `adapter/api/dependencies.py` | Removed try/except; direct import; removed dead `Depends is None` guard |
| 8 | SQL injection via user dict keys in `update_entity` | `adapter/entity/sqlite_entity_store.py:126` | Added `_ALLOWED_COLUMNS` whitelist; only `entity_type`, `name`, `metadata` columns are interpolated into SQL |
| 9 | `get_definer_identity()` silently grants DEFINER access on error/absence | `adapter/auth/session_store.py:182-185` | Changed to return `None` instead of hardcoded `{"identity": "definer", "role": "definer"}` — callers must handle absence explicitly |

## Module Classification

| Module / Area | Category | Est. Scaffolding % | Key Observations | Recommended Next Action | Priority |
|---|---|---|---|---|---|
| **Workflow: runner.py** | Real/Mostly Working | 15% | Full sequential + parallel execution, condition branching, dialog pause/resume. Autonomy bypass wrapped in `except Exception: pass`. Budget hardcoded 100/call. Parallel merge `collect_all` is `pass`. | Fix silent autonomy failures. Implement parallel merge. | High |
| **Workflow: engine.py** | Partial/Hybrid | 35% | Clean facade but defaults to no-op stores. Inline `_NoopTraceStore`/`_NoopStore` duplicated with `workflow_01.py`. Dead code (`protocols` dict rebuilt as `safe_protocols`). | Extract shared no-op classes. Remove dead code. | Medium |
| **Workflow: context.py** | Partial/Hybrid | 30% | **DANGEROUS**: `budget_remaining=None` → infinite budget by default. `consume_budget()` fires-and-forgets `asyncio.create_task`. `request_autonomy()` always allows level ≤1. | Set finite default budget. Await budget store coroutine. Gate autonomy properly. | Critical |
| **Workflow: definition.py** | Real/Mostly Working | 0% | Clean dataclass — complete | None | Low |
| **Workflow: node.py** | Partial/Hybrid | 40% | **ScriptNode.run() is a complete placeholder** — stores code but never executes. AgentNode, ConditionNode, DialogNode are real. | Implement ScriptNode.run (safe exec pattern). | High |
| **Workflow: loader.py** | Real/Mostly Working | 5% | Full YAML parser with all node types | None | Low |
| **Workflow: instance.py** | Real/Mostly Working | 0% | Clean dataclasses with JSON serialization | None | Low |
| **Workflow: instance_store.py** | Real/Mostly Working | 15% | Working FileWorkflowInstanceStore | Add SQLite-backed implementation | Medium |
| **Workflow: workflow_01.py** | Mostly Scaffolding | 60% | **DANGEROUS**: `_AlwaysApproveDialogNode` always approves. `_CommitNode` uses placeholder synthesis. ScriptNodes with code="validate"/"adversarial" never execute. L4/Sexton result computed but unused. | Replace with real DialogNode. Wire real evaluation nodes. Use L4 result. | Critical |
| **Evaluation: evaluation.py** | Dead/Duplicate | 100% | Near-exact duplicate of `l3a_orchestrator.py` | Delete; keep l3a_orchestrator.py | High |
| **Evaluation: l3a_orchestrator.py** | Partial/Hybrid | 30% | Real 3-stage orchestration. Bug: duplicate failure type "A" for both faithfulness and domain_coherence failures. | Fix failure type codes to be distinct. | High |
| **Evaluation: canonical_pipeline.py** | Broken but Important | 50% | **DANGEROUS**: Default scores `faithfulness=0.91`, `domain_coherence=0.87` on any exception — above thresholds, guaranteeing pass. ECS state assumed "REVIEWED" on query failure. | Replace hardcoded fallback scores with explicit failure. Propagate errors. | Critical |
| **Nodes: faithfulness.py** | Partial/Hybrid | 50% | **DANGEROUS**: Default `faithfulness_score=0.85`, `context_coverage=0.80` when no resolver. Silent `except Exception: pass` → passing defaults. | Return 0.0/None without resolver. Add ci_fixture flag. | Critical |
| **Nodes: domain_coherence.py** | Partial/Hybrid | 50% | **DANGEROUS**: Default `coherence_score=0.90` with empty violations. Same silent fallback pattern. | Return 0.0/None without resolver. Add ci_fixture flag. | Critical |
| **Nodes: adversarial_eval.py** | Partial/Hybrid | 45% | **DANGEROUS**: CI fixture returns `overall=0.86`, individual 0.82-0.90. Production path also returns hardcoded scores — model response only used for `critique` text, not scores. | Parse model JSON for actual scores. Add ci_fixture flag. | Critical |
| **Nodes: synthesis.py** | Partial/Hybrid | 40% | Real model path + stub path. Fake token counts (`len/4`). Fake latency (`+ 12ms`). API inconsistency: Phase 1 returns SynthesisOutput, Phase 4 returns dict. | Unify return types. Add stub=True flag. | Medium |
| **Nodes: commit.py** | Real/Mostly Working | 15% | Real DEFINER decision + ECS + stores. Hardcoded `from_state="SPECIFIED"` in ECS transition. | Read artifact's current ECS state before transitioning. | Low |
| **Nodes: definer_gate.py** | Partial/Hybrid | 50% | **DANGEROUS CASCADE**: Gate checks validation_result.passed and eval_result.passed, but those always pass in CI mode (see above). MANUAL mode raises `NotImplementedError`. `approved_by="stub:auto_approve"` in audit logs. | Implement MANUAL mode. Add logging for auto-approvals. | Critical |
| **L4: monitor.py** | Real/Mostly Working | 15% | Solid D/F signal detection. **`combined_2of3` proxy fires with hardcoded `confidence=0.85`** — could trigger false interventions. | Replace hardcoded confidence with computed value. | High |
| **L4: regulator.py** | Real/Mostly Working | 10% | Clean 2-of-3 composition logic. Stateless, deterministic. Only action is `"context_reset"`. | Add alternative interventions. | Low |
| **L4: anxiety_detector.py** | Real/Mostly Working | 15% | Real heuristic: length drops over recent outputs. But fed from trace event `content`/`detail` fields, not actual model output text. | Wire to synthesis output storage. | Medium |
| **L4: failure_streak.py** | Partial/Hybrid | 45% | **DANGEROUS FAKE**: `substance_score` defaults to 0.5, but detector fires when `substance < 0.4`. Hardcoded 0.5 ≥ 0.4 always → Type E detection is dead. | Implement real substance scoring. Remove hardcoded 0.5. | Critical |
| **L4: loop_detector.py** | Real/Mostly Working | 10% | Real O(n²) subsequence pattern matching. Uses parameterized queries. Clean. | Consider performance optimization for large windows. | Low |
| **L4: reset.py** | Real/Mostly Working | 20% | Solid coordinator: detect → filter → log → recommend. `finally: pass` blocks swallow logging exceptions. `check_l4_and_surface_if_needed` catches all exceptions → L4 silently disabled on error. Circular import workaround. | Add logging on exception paths. Fix circular import. | High |
| **Trajectory: regulator.py** | Partial/Hybrid | 40% | **Multiple dangerous fakes**: (1) `substance_score=0.5` hardcoded — kills Type E detection. (2) Anxiety detector fed trace metadata strings, not model output. (3) Three `try/except Exception: pass` blocks silently eat detector errors. | Fix substance_score. Use real synthesis output. Add logging. | Critical |
| **Trajectory: context_reset.py** | Real/Mostly Working | 20% | Full 6-step §10.2 protocol. Step 2 is templated, not model-generated. Session ID unchanged after reset. | Generate new session_id. Wire Step 2 to model synthesis. | Medium |
| **Sexton: sexton.py** | Real/Mostly Working | 20% | Real deterministic + model-based classification. **Bug**: `run_classification_cycle()` writes events with wrong signatures (passes dict instead of kwargs to `write_event()`). `derive_intervention_rule()` returns None — stub. | Fix event write call signatures. Implement derive_intervention_rule. | High |
| **Sexton: sexton_audit.py** | Partial/Hybrid | 35% | Real stale assumption audit. `flag_deprecated_rules()` — playbook deprecation call is `pass` (no-op). Same event store write signature mismatch as sexton.py. | Fix event write signatures. Implement playbook deprecation. | High |
| **Actors: vigil.py** | Partial/Hybrid | 35% | Real canonical health monitoring. `detect_entity_inconsistencies` checks a flag that no code sets. `on_model_slot_change` only writes trace event — doesn't trigger re-evaluation. | Wire re-evaluation on slot change. Verify entity flag is set. | High |
| **Actors: beast.py** | Real/Mostly Working | 10% | **FIXED**: Real health checks (embedding provider, vector store, entity/canonical/project stores). Real corpus maintenance via `list_stale_vectors()` + re-embedding. Entity store properly injected via constructor. `project_store` is optional (global mode fallback). `run_cycle()` for periodic scheduling. Wired in lifespan. 58 tests pass. | Add background scheduler. Expand entity consistency checks. | Medium |
| **Store: sqlite_entity_store.py** | Partial/Hybrid | 30% | Full CRUD. **P0 FIXED**: SQL injection in `update_entity` column names — now whitelisted. Blocking sqlite3 in async. `finally: pass` blocks. | Migrate to aiosqlite. Remove `finally: pass`. | High |
| **Store: sqlite_fts5_store.py** | Real/Mostly Working | 10% | Cleanest store — real FTS5, parameterized queries, upsert via delete+reinsert. Blocking sqlite3 in async. | Migrate to aiosqlite. | Medium |
| **Store: sqlite_vss_store.py** | Partial/Hybrid | 35% | **DANGEROUS**: `store()` compat method stores with `[0.0] * dimensions` zero-vector. Blocking sqlite3 in async. No `initialize()` method. | Remove zero-vector compat method. Add `initialize()`. Migrate to aiosqlite. | High |
| **Store: pgvector_store.py** | Real/Mostly Working | 10% | Production-grade with asyncpg pooling, HNSW index. Minor: f-string interpolation for `dimensions` (integer from config, not user input). | Validate dimensions is int before interpolation. | Low |
| **Store: migrate.py** | Broken but Important | 70% | **DANGEROUS**: Uses `[0.0]*768` zero-vector for retrieval → doesn't scan all vectors. Falls back to zero-vector if no embedding in metadata → destroys real embeddings during migration. Batch loop may not advance properly. | Rewrite with cursor-based scanning. Preserve real embeddings. | Critical |
| **Store: vector/factory.py** | Real/Mostly Working | 5% | Clean degradation chain pgvector → vss → in-memory. Accesses private `store._vss_available` attribute. | Add public `vss_available` property. | Low |
| **Store: vector/_in_memory.py** | Real/Mostly Working | 5% | Clean in-memory with real cosine similarity. Explicitly documented as CI/dev fallback. | None | Low |
| **Store: vector/connection_manager.py** | Real/Mostly Working | 10% | Good retry+fallback with exponential backoff. May mutate caller's config dict. `shutdown()` calls `close()` that may be sync or async. | Handle both sync/async close(). Don't mutate config. | Medium |
| **Store: sqlite_canonical_store.py** | Real/Mostly Working | 15% | Good DEFINER enforcement. `INSERT OR REPLACE` silently overwrites — no version history for canonicals. `finally: pass` blocks. Blocking sqlite3. | Replace INSERT OR REPLACE with version-aware writes. Use aiosqlite. | High |
| **Store: budget_store_sqlite.py** | Partial/Hybrid | 35% | **Hardcoded limits**: session=500K, project=5M, daily=10M. Opens new connection per call — no pooling. `warning_threshold=0.80` hardcoded. No `initialize()`/`close()`. | Read limits from BudgetConfig. Use persistent connection. | High |
| **Store: ecs_store_guardrailed.py** | Partial/Hybrid | 35% | **In-memory state cache only** — all ECS state lost on restart. `current_state()` reads only from in-memory cache, never from underlying store. | Persist state to SQLite. Query store on cache miss. | High |
| **Store: artifact_store_versioned.py** | Real/Mostly Working | 10% | Solid versioned artifact store. Blocking sqlite3 in async. No async `close()`. | Migrate to aiosqlite. Add async close. | Medium |
| **Store: sqlite_vigil_store.py** | Partial/Hybrid | 25% | `__import__('datetime')` inline. `finally: pass` blocks. Naive timestamp comparison. Blocking sqlite3. | Fix imports. Fix timestamp comparison. Use aiosqlite. | Medium |
| **Store: event_store_queryable.py** | Real/Mostly Working | 15% | Clean append-only with indexes and parameterized queries. Blocking sqlite3 in async. `**kwargs` → metadata_json undocumented. | Migrate to aiosqlite. Document kwargs behavior. | Medium |
| **Store: sqlite_knowledge_store.py** | Partial/Hybrid | 40% | **DANGEROUS**: `dummy_embedding = [0.0]*384` for all APPROVED items — vector search non-functional. `search_compiled` vector search is `pass` placeholder. Provenance uses empty strings. State transitions bypassed ("For 0.1 we log but allow"). Blocking sqlite3. | Wire real embeddings. Implement vector search. Fix provenance. | Critical |
| **Store: auth/session_store.py** | Partial/Hybrid | 20% | **P0 FIXED**: `get_definer_identity()` no longer returns hardcoded DEFINER identity on error. Remaining: O(n) bcrypt scanning in `validate_api_key`. `finally: pass` blocks. `create_user` returns True even if user already existed. | Add rate limiting to validate_api_key. Fix create_user return. Use aiosqlite. | High |
| **API: app.py** | Partial/Hybrid | 20% | Real FastAPI factory with 11 routers. Lifespan wires stores + Beast actor. `_AuthStoreProxy` uses `__getattr__` — typos silently return None. Closes 7 stores on shutdown. | Replace AuthStoreProxy. Close remaining stores. | Medium |
| **API: dependencies.py** | Real/Mostly Working | 15% | Clean DI container. Orchestration components typed as `Any`. `get_container` returns empty container on miss. | Raise error when container stores not initialized. | Medium |
| **API: collaborators.py** | Partial/Hybrid | 15% | Functional CRUD with auth gates. **Password passed as query parameter** — logged in URLs. | Move password to POST body. | High |
| **API: performance.py** | Partial/Hybrid | 40% | Routes structurally correct but delegate to `container.performance_profiler` which is never initialized. | Implement PerformanceProfiler or mark routes as stub. | Medium |
| **CLI: main.py** | Real/Mostly Working | 5% | Clean Click group with 5 subcommands | None | Low |
| **CLI: session.py** | Mostly Scaffolding | 90% | All commands print "(scaffold)". No SessionManager integration. | Wire to SessionManager. | Medium |
| **CLI: project.py** | Mostly Scaffolding | 90% | All commands print "(scaffold)". No ProjectStore integration. | Wire to ProjectStore. | Medium |
| **CLI: init.py** | Real/Mostly Working | 25% | Most functional CLI — real RAM detection, config writing, DB schema creation. Pre-touches empty DB files. | Don't pre-touch DB files. Add schema versioning. | Medium |
| **CLI: config.py** | Mostly Scaffolding | 85% | Both read/write print "(scaffold)". No config file interaction. | Implement TOML read/write. | Medium |
| **CLI: status.py** | Mostly Scaffolding | 95% | Entirely hardcoded echo. Even "active_sessions: 0" is a literal string. | Wire to real protocol queries. | Low |
| **Adapter CLI: plugins.py** | Partial/Hybrid | 50% | Depends on HTTP request context in CLI. Uses deprecated `asyncio.get_event_loop().run_until_complete()`. | CLI container init + `asyncio.run()`. | High |
| **Adapter CLI: collaborators.py** | Partial/Hybrid | 50% | Same issues as plugins.py. Password prompt is good practice (hide_input=True). | Same: CLI container init + asyncio.run(). | High |
| **Foundation: schemas.py** | Real/Mostly Working | 5% | 730+ lines of comprehensive dataclass definitions. Duplicate imports at phase boundaries. `model_gen_assumption` fields are declarative, not fake logic. | Clean up duplicate imports at phase boundaries. | Low |
| **Foundation: protocols.py** | Partial/Hybrid | 10% | **Duplicate `ArtifactStore.read()`**: declared at line 156 (`id: str`) and line 165 (`id: str, version: int | None = None`) — second shadows first. 27 CHUNK- references in comments. | Resolve duplicate read() signature. Make versioned version canonical. | High |
| **Foundation: ecs_graph.py** | Real/Mostly Working | 0% | Pure validation logic — single source of truth for ECS. Gold standard module. | None | Low |
| **Foundation: validation.py** | Real/Mostly Working | 15% | Real structural validation with 3 default rules. `full_l3a_evaluation` dynamic import is fragile. | Expand validation rules. Make thresholds configurable. | Medium |
| **Orchestration: router.py** | Mostly Scaffolding | 65% | **"Adaptive" router is not adaptive**: `update_weights()` is `pass` (no-op). `recommend_exploration_weight()` uses hardcoded `count=5`. `_pick_non_optimal()` returns first alternative. Only budget enforcement is real. | Implement update_weights with routing_outcomes queries. Replace count=5. | Critical |
| **Orchestration: session.py** | Partial/Hybrid | 30% | Real create/advance/context utilization. Type E recovery discards inject_deterministic_recovery result. Token estimation is rough approximation. | Fix Type E recovery. Implement real token counting. | High |
| **Orchestration: budget.py** | Real/Mostly Working | 20% | `InMemoryBudgetStore.check_limit()` always returns True. `SimpleAutonomyGate.record_autonomy_use()` is `pass`. | Fix check_limit to actually check. Implement record_autonomy_use. | Medium |
| **Orchestration: retrieval.py** | Real/Mostly Working | 15% | Real four-factor reranking (semantic, recency, authority, frequency). `fake_embed()` uses SHA-256 for CI — documented. ACE rule boost minimal (0.15). | Replace fake_embed with real provider when available. Expand ACE matching. | Medium |
| **Orchestration: review.py** | Partial/Hybrid | 40% | **DANGEROUS**: `_automated_review()` returns APPROVED at confidence=1.0 when no eval_fn. `_definer_review()` always returns APPROVED at confidence=1.0. ECS transition logic IS real. | Require eval_fn in production. Mark CI fixture results explicitly. | Critical |
| **Orchestration: recovery.py** | Real/Mostly Working | 15% | Real SQLite-backed checkpoint and recovery. Artifact verification works. `str()`/`ast.literal_eval` serialization is fragile. | Use JSON serialization. Create table once at init. | Medium |
| **Orchestration: ace_playbook.py** | Real/Mostly Working | 10% | Full SQLite-backed CRUD with deprecation logic. Blocking sqlite3 in async. | Consider aiosqlite. | Low |
| **Orchestration: perf.py** | Partial/Hybrid | 45% | `profile_operation()` is real. `get_system_metrics()` returns hardcoded empty values. `get_memory_usage()` returns fake proportional breakdown (25%/10%/5%...). | Replace hardcoded breakdown with real measurement. | Medium |
| **Orchestration: embed_providers.py** | Mostly Scaffolding | 75% | **Always returns fake_embed** — even "real" provider path wraps fake_embed. Ollama never wired. | Wire OllamaEmbeddingClient when provider="ollama". | High |
| **Orchestration: compilation.py** | Partial/Hybrid | 30% | Real compilation flow. `or True` removed in Phase 0. Evaluation still returns hardcoded scores. | Improve validation logic. Wire real evaluation. | Medium |
| **Auth: middleware.py** | Real/Mostly Working | 10% | Proper Bearer + API key validation. Laptop profile correctly sets DEFINER. | None | Low |
| **Auth: collaborator.py** | Partial/Hybrid | 25% | Real role enforcement and bcrypt hashing. 4 `# type: ignore[attr-defined]`. max_collaborators limit not enforced. | Implement max_collaborators. Fix Protocol type narrowing. | Medium |
| **Auth: dependencies.py** | Real/Mostly Working | 10% | Proper FastAPI dependency injection. Falls back to DEFINER when unauthenticated (laptop profile). | None | Low |
| **Adapter: model_slot_resolver.py** | Broken but Important | 50% | ci_mode works with fixtures. **Real mode raises NotImplementedError** — no real model dispatch exists. Token counting is `len/4`. `latency_ms: 5` and `cost_usd: 0.0` hardcoded. | Implement real provider dispatch (Ollama, OpenAI). | Critical |
| **Adapter: autonomy_gate.py** | Real/Mostly Working | 15% | Full SQLite-backed AutonomyGate with audit trail. 6 `# type: ignore[arg-type]` on AutonomyLevel strings. | Fix AutonomyLevel type narrowing. | Medium |
| **Adapter: plugin_loader.py** | Real/Mostly Working | 15% | Real YAML discovery, loading, unloading. Sandbox mode works. | Strengthen container registration. | Low |
| **Adapter: yaml_plugin_provider.py** | Partial/Hybrid | 40% | Real httpx-based API call in `_call_impl()`. CI mode detection correct. Only supports OpenAI-compatible endpoint. | Add provider-specific dispatch. Fail-fast when httpx missing and not CI. | Medium |
| **Adapter: mcp/server.py** | Mostly Scaffolding | 70% | Tool registry real. `start()` just sets `_running=True`. All dispatch returns hardcoded results. Autonomy gate enforcement is real. | Implement real tool dispatch. Implement stdio/SSE transport. | High |
| **Adapter: mcp/tools/artifacts.py** | Real/Mostly Working | 10% | Real: calls `ecs_store.transition()` and `canonical_store.write_canonical()`. | None | Low |
| **Adapter: mcp/tools/search.py** | Real/Mostly Working | 10% | Real search across lexical and vector stores. Falls back to fake_embed. Silent exception swallowing. | Add logging for search failures. | Low |
| **Adapter: health.py** | Partial/Hybrid | 40% | Vector store health check is real. **Embedding status hardcoded healthy** with "nomic-embed-text:v1.5". Uptime is 0 unless manually set. | Implement real embedding health check. Add uptime tracking. | High |
| **Adapter: ollama_embed.py** | Real/Mostly Working | 20% | **Only real model integration in codebase** — OllamaEmbeddingClient works. MockOllamaEmbeddingClient for CI. `fake_embed_via_provider` duplicates SHA-256 algorithm from retrieval. | Unify fake embedding algorithms. Wire into embed_providers.py. | Medium |
| **Adapter: rate_limiter.py** | Real/Mostly Working | 5% | Proper token-bucket implementation. GET/health exempted (audit noted as security concern). In-memory buckets. | None | Low |

## Dangerous Fakes (High Risk)

These components appear functional but contain fake logic that silently produces passing/healthy results. They are the highest-risk items because they create a false sense of security and can allow broken or malicious artifacts to pass quality gates:

1. **canonical_pipeline.py** — Default evaluation scores of 0.91/0.87 on any exception. Artifacts pass the promotion gate without being evaluated. The entire canonical promotion path can be silently bypassed.

2. **faithfulness.py** — Returns hardcoded scores (faithfulness=0.85, context_coverage=0.80) when no model_resolver is provided. Since the default config runs in ci_mode, faithfulness checks always "pass" with plausible-looking numbers.

3. **domain_coherence.py** — Returns hardcoded coherence=0.90 with empty violations when no resolver is provided. Same dangerous pattern as faithfulness.py.

4. **adversarial_eval.py** — CI fixture returns framework_integrity=0.88, overall=0.86. The production path also returns hardcoded scores — the model response is only used for the `critique` text field, NOT for the scores.

5. **definer_gate.py** — The gate logic checks validation_result.passed and eval_result.passed, but since evaluation always passes in CI mode, the gate effectively always approves. MANUAL mode raises NotImplementedError. `approved_by="stub:auto_approve"` appears in audit logs.

6. **review.py** — The `_automated_review()` function returns APPROVED at confidence=1.0 when no eval_fn is provided. The `_definer_review()` function always returns APPROVED at confidence=1.0 (CI fixture). Artifacts bypass the quality gate silently.

7. **beast.py** — **FIXED**: Health check now performs real connectivity probes (embedding provider, vector store, entity/canonical/project stores). Corpus maintenance uses `list_stale_vectors()` + re-embedding instead of health-check loop. Entity store properly injected via constructor. Wired in application lifespan.

8. **health.py** — Embedding status always returns `{"status": "healthy"}` with model name "nomic-embed-text:v1.5" without actually checking if Ollama is running.

9. **router.py** ("Adaptive Router") — The "adaptive" component is not adaptive at all. `update_weights()` is `pass` (no-op). `recommend_exploration_weight()` uses hardcoded `count=5`. Exploration/exploitation is `random.random() < 0.10`.

10. **trajectory/regulator.py** — `substance_score=0.5` is hardcoded, which is always ≥ 0.4 threshold. This means the FailureStreakDetector (Type E) can NEVER fire — the entire Type E signal path is dead.

**Root Cause**: The entire L3a evaluation pipeline silently passes everything by default because: (1) ModelSlotResolver runs in ci_mode by default, (2) evaluation nodes return plausible hardcoded scores when no real resolver is available, (3) there is no CI-mode flag in results to distinguish real from fixture scores, and (4) the DEFINER gate and review process trust these scores without verification.

## Additional Issues Discovered

### Critical (P0/P1)
1. **SQL injection in `sqlite_entity_store.update_entity`** — **FIXED in Phase 0**: Column names from user-provided dict keys were interpolated directly into SQL. Now whitelisted.
2. **DEFINER identity fallback grants admin access** — **FIXED in Phase 0**: `get_definer_identity()` returned `{"identity": "definer", "role": "definer"}` on error or when no definer exists. Now returns `None`.
3. **ModelSlotResolver real mode is NotImplementedError** — The single most important blocker for production. Every model call goes through ci_mode fixtures. No real model provider (Ollama, OpenAI, Anthropic) is wired.

### High (P1)
4. **Type E (failure streak) detection is completely non-functional** — `substance_score=0.5` hardcoded in trajectory/regulator.py. Since the detector checks `substance < 0.4`, it can never fire.
5. **Vector migration destroys real embeddings** — migrate_vectors uses zero-vectors for retrieval and as fallback, destroying actual embeddings during migration.
6. **Knowledge store uses zero-vector embeddings** — `store_compiled` inserts `[0.0]*384`, making semantic search useless for compiled knowledge.
7. **All SQLite stores use blocking sqlite3 in async methods** — Every SQLite adapter calls synchronous `sqlite3.connect()` and `conn.execute()` inside `async` methods. Under load, this will freeze the event loop.
8. **Sexton/sexton_audit event store write calls use wrong signatures** — Pass dicts to `write_event()` but the Protocol expects keyword arguments. Will crash at runtime with real EventStore.
9. **ECS state lost on process restart** — `ecs_store_guardrailed.py` uses an in-memory cache with no persistence. `current_state()` never queries the underlying store.
10. **ArtifactStore.read() defined twice in protocols.py** — Signature conflict: line 156 (`id: str`) and line 165 (`id: str, version: int | None = None`). Second shadows first.

### Medium (P2)
11. **embed_providers.py routes everything to fake_embed** — Even when config specifies "real" provider, code returns fake embeddings. OllamaEmbeddingClient exists but is never wired.
12. **BudgetStore limits are hardcoded** — Session=500K, project=5M, daily=10M hardcoded rather than read from BudgetConfig.
13. **CLI session/project commands are all scaffolding** — No actual backend integration.
14. **AipContainer wiring** — **PARTIALLY FIXED**: Lifespan now wires vector_store, embedding_provider, entity_store, canonical_store, event_store, project_store, model_provider, and Beast actor. Some stores (lexical, ECS, budget, autonomy, auth) still not wired.
15. **evaluation.py is a duplicate of l3a_orchestrator.py** — Identical content, only docstrings differ.
16. **WorkflowContext has infinite budget by default** — `budget_remaining=None` means `consume_budget()` always returns True.
17. **Password passed as query parameter in collaborators.py** — Gets logged in server access logs.
18. **51 `assert True` no-op tests** across 23 test files — Tests that test nothing.
19. **25 `# type: ignore` annotations** across 13 source files — Many silence real type problems.
20. **94 files with CHUNK- references** in docstrings — Build-process artifacts that should be cleaned.

## Codebase Metrics

| Metric | Count |
|--------|-------|
| Total test files | 52+ |
| Tests passing | 526 |
| `assert True` no-op tests | 51 |
| `finally: pass` blocks (src/) | 15+ |
| `# type: ignore` (src/) | 25 |
| Files with CHUNK- references (src/) | 94 |
| `model_gen_assumption` references (src/) | 94 (18 files) |
| Modules classified as Real/Mostly Working | 31 |
| Modules classified as Partial/Hybrid | 26 |
| Modules classified as Mostly Scaffolding | 6 |
| Modules classified as Broken but Important | 3 |
| Modules classified as Dead/Duplicate | 1 |

## Recommendations for Phase 1

### Priority 1: Fix the Evaluation Pipeline (Critical)
- Remove default passing scores from `canonical_pipeline.py`, `faithfulness.py`, `domain_coherence.py`, `adversarial_eval.py`
- Add explicit `ci_fixture` flag to all evaluation results so callers can distinguish real from fixture scores
- Make `review.py` require `eval_fn` in production; mark CI fixture results explicitly
- Implement MANUAL mode in `definer_gate.py`
- This is the most important work because the current system silently promotes unevaluated artifacts

### Priority 2: Wire Real Model Providers (Critical)
- Implement real provider dispatch in `ModelSlotResolver` (Ollama, OpenAI-compatible, Anthropic)
- Wire `OllamaEmbeddingClient` into `embed_providers.py`
- Without this, no component can use real models

### Priority 3: Fix Broken Infrastructure (High)
- Fix `substance_score` default in `trajectory/regulator.py` — Type E detection is completely non-functional
- Fix vector migration (cursor-based batching, preserve real embeddings)
- Fix knowledge store embeddings (accept real embedding in `store_compiled`)
- Fix Sexton/sexton_audit event store write signatures
- Wire AipContainer with real store instances from config
- Implement real health checks in `beast.py` and `health.py`
- Fix ECS store persistence (survive process restart)

### Priority 4: Remove Dead Code and Fix Anti-Patterns (High)
- Delete `evaluation.py` (duplicate of `l3a_orchestrator.py`)
- Resolve duplicate `ArtifactStore.read()` in `protocols.py`
- Remove `finally: pass` blocks throughout
- Fix `WorkflowContext` infinite budget default

### Safe to Work On Now
- Workflow engine (runner, engine, context) — all real and stable
- L4 trajectory regulation (loop_detector, anxiety_detector) — real implementations
- Most SQLite stores — all functional, improvements are additive
- Auth layer — complete and working
- Foundation (schemas, protocols, validation, ecs_graph) — complete

### Avoid Until Later
- CLI session/project commands (depends on AipContainer wiring)
- MCP server (depends on real tool dispatch through container)
- AdaptiveRouter weights (depends on routing_outcomes table and real model dispatch)
- Vector migration (needs complete rewrite)
