# AIP Implementation Status

**Date:** 2026-05-28
**Phase 0 Status:** Complete
**Overall Assessment:** The AIP 0.1 codebase has a well-designed three-layer architecture (foundation → orchestration → adapter) with sound Protocol-based dependency injection and comprehensive schema/protocol definitions. However, the implementation is approximately 30% scaffolding overall. The most critical gap is that the entire L3a evaluation pipeline silently passes artifacts with plausible-looking hardcoded scores when running in default CI mode, and the ModelSlotResolver has no real model provider dispatch — everything runs through deterministic CI fixtures. The workflow engine, L4 trajectory regulation, and most SQLite stores are genuinely implemented and functional. The system no longer crashes on import or basic startup after Phase 0 P0 fixes.

## P0 Fixes Applied

| # | Bug | File | Fix |
|---|-----|------|-----|
| 1 | `or True` makes knowledge validation always pass | `orchestration/compilation.py:115` | Removed `or True` — validation now checks `len > 20 and "provenance" in content` |
| 2 | `ModelResolverProtocol.call()` sync but callers `await` it | `orchestration/model_provider_proxy.py:30` | Made `call()` async in Protocol; also fixed `resolve_slot` return type to `Any` |
| 3 | `register_provider()` called on `AdaptiveRouter` which lacks it | `orchestration/plugins.py:61` | Removed broken `register_provider` call; replaced with clear TODO comment |
| 4 | `run_until_complete()` inside async handler | `adapter/api/plugins.py:52` | Replaced with `await pm.health_check_all()` |
| 5a | Conditional FastAPI imports set `router = None` → decorator crash | `adapter/api/routes/chat.py` | Removed try/except ImportError; direct FastAPI import with `APIRouter()` |
| 5b | Same pattern | `adapter/api/routes/review.py` | Same fix |
| 5c | Same pattern | `adapter/api/routes/artifacts.py` | Same fix |
| 5d | Same pattern | `adapter/api/routes/admin.py` | Same fix; also fixed `get_weights()` → `get_routing_weights()` (async) |
| 5e | Same pattern | `adapter/api/routes/memory.py` | Same fix |
| 6 | `app.py` had conditional FastAPI import with dead `FastAPI is None` guard | `adapter/api/app.py` | Removed try/except; direct import; removed dead guard in `create_app` |
| 7 | `dependencies.py` had conditional FastAPI import | `adapter/api/dependencies.py` | Removed try/except; direct import; removed dead `Depends is None` guard |

## Module Classification

| Module / Area | Category | Est. Scaffolding % | Key Observations | Recommended Next Action | Priority |
|---|---|---|---|---|---|
| **Workflow: runner.py** | Real/Mostly Working | 10% | Full sequential + parallel execution, condition branching, dialog pause/resume | Implement ScriptNode.run | Medium |
| **Workflow: engine.py** | Real/Mostly Working | 15% | Clean high-level facade wiring runner, stores, L4, budget | Replace inline no-op classes with defaults | Low |
| **Workflow: context.py** | Real/Mostly Working | 10% | Full variable store, budget/autonomy delegation, parallel fork | Consider fully-async consume_budget | Low |
| **Workflow: definition.py** | Real/Mostly Working | 0% | Clean dataclass — complete | None | Low |
| **Workflow: node.py** | Partial/Hybrid | 20% | All nodes real except ScriptNode (placeholder) | Implement ScriptNode.run | Medium |
| **Workflow: loader.py** | Real/Mostly Working | 5% | Full YAML parser with all node types | None | Low |
| **Workflow: instance.py** | Real/Mostly Working | 0% | Clean dataclasses with JSON serialization | None | Low |
| **Workflow: instance_store.py** | Real/Mostly Working | 5% | Working FileWorkflowInstanceStore | Add SQLite-backed implementation | Medium |
| **Evaluation: evaluation.py** | Dead/Duplicate | 100% | Identical to l3a_orchestrator.py | Delete; keep l3a_orchestrator.py | High |
| **Evaluation: l3a_orchestrator.py** | Partial/Hybrid | 40% | Real 3-stage orchestration; CI fixtures return passing scores | Add CI-mode flag in results | High |
| **Evaluation: canonical_pipeline.py** | Partial/Hybrid | 35% | Real 10-step pipeline; **DANGEROUS: default scores 0.91/0.87 on exception** | Remove default passing scores | Critical |
| **Nodes: synthesis.py** | Partial/Hybrid | 40% | Real model path + stub CI path; fabricated token counts | Add CI-mode flag to SynthesisOutput | Medium |
| **Nodes: faithfulness.py** | Partial/Hybrid | 45% | **DANGEROUS: hardcoded 0.85/0.80 when no resolver** | Return 0.0/None without resolver | Critical |
| **Nodes: domain_coherence.py** | Partial/Hybrid | 45% | **DANGEROUS: hardcoded 0.90 with empty violations** | Return 0.0/None without resolver | Critical |
| **Nodes: adversarial_eval.py** | Partial/Hybrid | 50% | **DANGEROUS: CI fixture scores 0.86-0.90 look like real eval** | Return explicit "not evaluated" state | Critical |
| **Nodes: commit.py** | Real/Mostly Working | 5% | Real DEFINER decision + ECS + ArtifactStore + EventStore | None | Low |
| **Nodes: definer_gate.py** | Partial/Hybrid | 40% | AUTO_APPROVE works; MANUAL raises NotImplementedError | Implement MANUAL mode | High |
| **L4: monitor.py** | Real/Mostly Working | 5% | Full D/F signal detection, hedging heuristics, 2-of-3 proxy | None | Low |
| **L4: regulator.py** | Real/Mostly Working | 5% | Clean 2-of-3 rule implementation | None | Low |
| **L4: anxiety_detector.py** | Real/Mostly Working | 5% | Real Type F detection with configurable thresholds | None | Low |
| **L4: failure_streak.py** | Real/Mostly Working | 5% | Real Type E detection with streak threshold | None | Low |
| **L4: loop_detector.py** | Real/Mostly Working | 5% | Real Type D detection with pattern repetition analysis | None | Low |
| **L4: reset.py** | Real/Mostly Working | 10% | Full L4ResetCoordinator with Sexton integration | None | Low |
| **Trajectory: regulator.py** | Real/Mostly Working | 10% | Composes 3 L4 detectors with 2-of-3 rule | Extract real substance scoring | Medium |
| **Trajectory: context_reset.py** | Real/Mostly Working | 10% | Full 6-step context reset protocol | None | Low |
| **Sexton: sexton.py** | Real/Mostly Working | 15% | Full deterministic + model-based classification, ACE derivation | Persist ACE rules to store | Medium |
| **Sexton: sexton_audit.py** | Real/Mostly Working | 15% | Real stale assumption audit | Wire flag_deprecated_rules | Medium |
| **Actors: beast.py** | Partial/Hybrid | 55% | **DANGEROUS: hardcoded health checks**; minimal real re-indexing | Wire real health checks | High |
| **Actors: vigil.py** | Real/Mostly Working | 10% | Real canonical health monitoring, stale detection, read-only | None | Low |
| **Store: sqlite_entity_store.py** | Real/Mostly Working | 5% | Full CRUD with SQLite | None | Low |
| **Store: sqlite_fts5_store.py** | Real/Mostly Working | 0% | Complete FTS5 implementation — production quality | None | Low |
| **Store: sqlite_vss_store.py** | Real/Mostly Working | 5% | Full VSS with graceful extension fallback | None | Low |
| **Store: pgvector_store.py** | Real/Mostly Working | 5% | Production-grade with asyncpg pooling, HNSW index | None | Low |
| **Store: migrate.py** | Broken but Important | 60% | Broken pagination (loops forever); zero-vector embeddings | Cursor-based batching; preserve real embeddings | High |
| **Store: vector/factory.py** | Real/Mostly Working | 5% | Clean degradation chain pgvector→vss→in-memory | None | Low |
| **Store: sqlite_canonical_store.py** | Real/Mostly Working | 0% | Full DEFINER enforcement, supersession support | None | Low |
| **Store: budget_store_sqlite.py** | Real/Mostly Working | 5% | Full append-only budget ledger | Read limits from BudgetConfig | Medium |
| **Store: event_store_queryable.py** | Real/Mostly Working | 0% | Complete append-only event store with indexing | None | Low |
| **Store: ecs_store_guardrailed.py** | Real/Mostly Working | 5% | Guards every transition against ECS graph | None | Low |
| **Store: artifact_store_versioned.py** | Real/Mostly Working | 0% | Complete versioned artifact store | None | Low |
| **Store: sqlite_vigil_store.py** | Real/Mostly Working | 5% | Full VigilStore with staleness queries | None | Low |
| **Store: sqlite_knowledge_store.py** | Partial/Hybrid | 30% | Full CRUD; zero-vector embeddings; search_compiled unimplemented | Accept real embedding; implement vector search | High |
| **API: app.py** | Partial/Hybrid | 30% | Real FastAPI factory with 11 routers; container wiring minimal | Complete AipContainer wiring | High |
| **CLI: main.py** | Real/Mostly Working | 5% | Clean Click group with 5 subcommands | None | Low |
| **CLI: session.py** | Mostly Scaffolding | 95% | All commands echo "(scaffold)" | Implement with SessionManager | High |
| **CLI: project.py** | Mostly Scaffolding | 95% | All commands echo "(scaffold)" | Implement with ProjectStore | High |
| **Foundation: schemas.py** | Real/Mostly Working | 0% | 730+ lines of comprehensive dataclass definitions | None | Low |
| **Foundation: protocols.py** | Real/Mostly Working | 0% | Complete runtime_checkable Protocol definitions | None | Low |
| **Foundation: validation.py** | Real/Mostly Working | 5% | Real structural validation with 3 default rules | None | Low |
| **Foundation: ecs_graph.py** | Real/Mostly Working | 0% | Pure validation logic — single source of truth for ECS | None | Low |
| **Orchestration: budget.py** | Real/Mostly Working | 10% | BudgetManager + InMemoryBudgetStore + SimpleAutonomyGate | None | Low |
| **Orchestration: router.py** | Partial/Hybrid | 50% | Budget enforcement works; exploration/exploitation stubbed | Implement real routing weights | Medium |
| **Orchestration: session.py** | Real/Mostly Working | 10% | SessionManager with trajectory regulation integration | None | Low |
| **Orchestration: retrieval.py** | Real/Mostly Working | 5% | Full four-factor reranking with ACE rule boost | None | Low |
| **Orchestration: re_synthesize.py** | Real/Mostly Working | 5% | Complete re-synthesis loop with failure-type corrections | None | Low |
| **Orchestration: recovery.py** | Real/Mostly Working | 10% | WorkflowRecovery with checkpoint and interrupted discovery | Use JSON serialization | Medium |
| **Orchestration: review.py** | Partial/Hybrid | 40% | **DANGEROUS: auto-APPROVED at 1.0 confidence when no eval_fn** | Require eval_fn in production | Critical |
| **Orchestration: embed_providers.py** | Broken but Important | 80% | All providers route to fake_embed; Ollama never wired | Wire OllamaEmbeddingClient | High |
| **Orchestration: perf.py** | Partial/Hybrid | 40% | profile_operation works; **memory breakdown is estimated** | Implement real per-component tracking | Medium |
| **Orchestration: ace_playbook.py** | Real/Mostly Working | 5% | Full SQLite-backed ACE Playbook with deprecation | None | Low |
| **Auth: session_store.py** | Real/Mostly Working | 5% | Complete SQLite auth with bcrypt, sessions, API keys | None | Low |
| **Auth: middleware.py** | Real/Mostly Working | 5% | Proper Bearer + API key validation; laptop fallback correct | None | Low |
| **Auth: collaborator.py** | Real/Mostly Working | 10% | Real collaborator management with DEFINER sovereignty | None | Low |
| **Auth: dependencies.py** | Real/Mostly Working | 5% | Proper FastAPI dependencies with laptop fallback | None | Low |
| **Adapter: health.py** | Partial/Hybrid | 40% | **DANGEROUS: embedding status always "healthy"** | Implement real Ollama check | High |
| **Adapter: model_slot_resolver.py** | Broken but Important | 50% | ci_mode works; real mode raises NotImplementedError | Implement real provider dispatch | Critical |
| **Adapter: autonomy_gate.py** | Real/Mostly Working | 5% | Full SQLite-backed AutonomyGate with audit | None | Low |
| **Adapter: ollama_embed.py** | Real/Mostly Working | 10% | Real Ollama client with httpx; MockOllamaEmbeddingClient for CI | None | Low |
| **Adapter: plugin_loader.py** | Real/Mostly Working | 10% | Real YAML plugin discovery with sandbox_mode | None | Low |
| **Adapter: yaml_plugin_provider.py** | Real/Mostly Working | 10% | Real YAML-driven provider with httpx async calls | None | Low |
| **Adapter: rate_limiter.py** | Real/Mostly Working | 0% | Proper token-bucket with per-endpoint overrides | None | Low |
| **Adapter: mcp/server.py** | Mostly Scaffolding | 75% | Tool registry real; all dispatch returns hardcoded results | Implement real tool dispatch | Medium |
| **Adapter: connection_manager.py** | Real/Mostly Working | 5% | Real lifecycle manager with exponential backoff | None | Low |
| **Adapter: _in_memory.py** | Real/Mostly Working | 0% | Clean in-memory VectorStore for CI/development | None | Low |
| **Orchestration: compilation.py** | Partial/Hybrid | 30% | Real compilation flow; `or True` removed in Phase 0 | Improve validation logic | Medium |

## Dangerous Fakes (High Risk)

These components appear functional but contain fake logic that silently produces passing results:

1. **canonical_pipeline.py** — Default evaluation scores of 0.91/0.87 on any exception. Artifacts pass the promotion gate without being evaluated. This means the entire canonical promotion path can be silently bypassed.

2. **faithfulness.py** — Returns hardcoded scores (faithfulness=0.85, context_coverage=0.80) when no model_resolver is provided. Since the default config runs in ci_mode, faithfulness checks always "pass" with plausible-looking numbers.

3. **domain_coherence.py** — Returns hardcoded coherence=0.90 with empty violations when no resolver is provided. Same dangerous pattern as faithfulness.py.

4. **adversarial_eval.py** — CI fixture returns framework_integrity=0.88, overall=0.86. These scores look like real adversarial evaluation results but are always-passing fixtures.

5. **review.py** — The _automated_review function returns APPROVED at confidence=1.0 when no eval_fn is provided. The _definer_review function always returns APPROVED at confidence=1.0 (CI fixture). Artifacts bypass the quality gate silently.

6. **beast.py** — Health check returns hardcoded `{"connected": True, "latency_ms": 5}` for Ollama and "ok" for all databases without actually checking them. The system appears healthy when it may not be.

7. **health.py** — Embedding status always returns `{"status": "healthy"}` with model name "nomic-embed-text:v1.5" without actually checking if Ollama is running.

**Root Cause**: The entire L3a evaluation pipeline silently passes everything by default because: (1) ModelSlotResolver runs in ci_mode by default, (2) evaluation nodes return plausible hardcoded scores when no real resolver is available, and (3) there is no CI-mode flag in results to distinguish real from fixture scores.

## Additional Issues Discovered

1. **ModelSlotResolver real mode is NotImplementedError** — The single most important blocker for production. Every model call goes through ci_mode fixtures. No real model provider (Ollama, OpenAI, Anthropic) is wired.

2. **embed_providers.py routes everything to fake_embed** — Even when config specifies a "real" provider, the code returns fake embeddings. The OllamaEmbeddingClient exists in the adapter layer but is never wired into orchestration.

3. **Vector migration is broken** — migrate_vectors uses zero-vectors for retrieval and upsert, which would destroy real embeddings during any migration attempt.

4. **Knowledge store uses zero-vector embeddings** — store_compiled inserts `[0.0]*384` into the vector store, making semantic search useless for compiled knowledge.

5. **CLI session/project commands are all scaffolding** — No actual backend integration exists beyond the Click group structure.

6. **AipContainer wiring is minimal** — The app.py lifespan creates a container but doesn't wire real store instances; all routes likely get None stores unless tests manually inject them.

7. **BudgetStore limits are hardcoded** — Session=500K, project=5M, daily=10M are hardcoded in SqliteBudgetStore rather than read from BudgetConfig, creating a disconnect between configuration and enforcement.

8. **evaluation.py is a duplicate of l3a_orchestrator.py** — Identical content, only docstrings differ. Should be deleted.

## Recommendations for Phase 1

### Priority 1: Fix the Evaluation Pipeline (Critical)
- Remove default passing scores from canonical_pipeline.py, faithfulness.py, domain_coherence.py, adversarial_eval.py
- Add explicit CI-mode flag to all evaluation results so callers can distinguish real from fixture scores
- Make review.py require eval_fn in production; mark CI fixture results explicitly
- This is the most important work because the current system silently promotes unevaluated artifacts

### Priority 2: Wire Real Model Providers (Critical)
- Implement real provider dispatch in ModelSlotResolver (Ollama, OpenAI-compatible, Anthropic)
- Wire OllamaEmbeddingClient into embed_providers.py
- Without this, no component can use real models

### Priority 3: Fix Broken Infrastructure (High)
- Fix vector migration (cursor-based batching, preserve real embeddings)
- Fix knowledge store embeddings (accept real embedding in store_compiled)
- Wire AipContainer with real store instances from config
- Implement real health checks in beast.py and health.py

### Priority 4: Remove Dead Code (High)
- Delete evaluation.py (duplicate of l3a_orchestrator.py)
- Update any imports that reference evaluation.py

### Safe to Work On Now
- Workflow engine (runner, engine, context) — all real and stable
- L4 trajectory regulation — all detectors are real implementations
- SQLite stores — all functional, improvements are additive
- Auth layer — complete and working
- Foundation (schemas, protocols, validation, ecs_graph) — complete

### Avoid Until Later
- CLI session/project commands (depends on AipContainer wiring)
- MCP server (depends on real tool dispatch through container)
- AdaptiveRouter weights (depends on routing_outcomes table)
