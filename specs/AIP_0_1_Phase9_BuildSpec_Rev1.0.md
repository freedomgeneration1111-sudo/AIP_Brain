AIP 0.1 Phase 9 BuildSpec
Rev 1.0

Product: AI Poiesis (AIP) v0.1
Architecture Revision: 5.2
Phase: 9 — Integration Remediation & Spec Reconciliation
Build Units: CHUNK-11.x
Date: May 2026
DEFINER: Moses Jorgensen
Status: Build Specification — forward-carrying

# 0. Document Purpose and Phase 9 Rationale

Phase 9 is the first post-Phase-8 remediation phase. A full code-against-spec inventory across Phases 1 through 8 has revealed 52 failing tests, 4 collection errors, systematic import boundary violations, a broken FastAPI application factory, incomplete adapter wiring, and several spec-to-code gaps where deliverables were partially implemented but never closed. Phase 9 exists to close those gaps without introducing new features.

Phase 9 does not add new architectural capability. It delivers what Phases 1-8 specified but did not fully complete, and it remediates the regressions and cross-cutting violations that accumulated across eight phases of iterative development. The phase is structured as a series of targeted fix chunks, each with its own gate test, following the same discipline as all prior phases.

## 0.1 Scope

Phase 9 addresses three categories of work:

1. SPEC-TO-CODE GAPS — deliverables specified in Phases 1-8 that exist as stubs, scaffolds, or missing implementations.

2. CROSS-CUTTING VIOLATIONS — import boundary violations, hardcoded model names, forbidden network imports, and broken gate tests that accumulated across phases.

3. INTEGRATION REMEDIATION — broken FastAPI wiring, async event loop issues, missing database schemas, and end-to-end pipeline gaps that prevent the system from running as a coherent whole.

## 0.2 What Phase 9 Is Not

Phase 9 is NOT a feature phase. No new schemas, protocols, config sections, or architectural concepts are introduced. No new YAML workflows are added. The Phase 9 chunk numbering follows CHUNK-11.x to continue the established sequence (Phase 1=1.x, Phase 2=2.x/4.x, Phase 3=5.x, Phase 4=6.x, Phase 5=7.x, Phase 6=8.x, Phase 7=9.x, Phase 8=10.x, Phase 9=11.x).

# 1. Phase 1-8 Inventory Summary

## 1.1 Test Results (Post-Phase-8 Baseline)

Total tests: 433
Passed: 367
Failed: 52
Errors: 4
Skipped: 10

Pass rate: 84.8% (367/433)
Phase 9 target: 100% pass rate (all gate tests green)

## 1.2 Failure Classification

| Category | Count | Severity | Root Cause |
| --- | --- | --- | --- |
| NameError: review not defined (app.py) | 10 | CRITICAL | Missing import in FastAPI app factory |
| RuntimeError: no current event loop | 12 | HIGH | Async tests run without event loop fixture |
| Import boundary violations | 7 | HIGH | foundation imports orchestration; orchestration imports adapter |
| Hardcoded model names | 5 | HIGH | Model names in docstrings/comments flagged by gate test |
| Forbidden network imports (httpx) | 4 | HIGH | httpx imported in adapter/cli code |
| sqlite_vss extension unavailable | 2 | MEDIUM | vss0.so not present in CI environment |
| Missing trace.db schema | 1 | HIGH | db/trace.db not initialized before test |
| Missing NodeResult import | 2 | CRITICAL | Unresolved name in workflow_01.py and runner.py |
| AttributeError: from_suspended | 1 | HIGH | SequentialRunner missing classmethod |
| model_gen_assumption format | 2 | MEDIUM | Assumption text lacks specific model name reference |
| Broken API integration tests | 4 | HIGH | Cascading from app.py NameError |
| Sexton classification returns empty | 2 | MEDIUM | Sexton CI mode not producing classifications |

## 1.3 Code-to-Spec Gap Analysis by Phase

### Phase 1 — Workflow 0.1 Node Implementation

DONE:
- schemas.py: Chunk, RetrievalResult, all Phase 1 dataclasses (append-only) ✓
- protocols.py: VectorStore, TraceStore, EventStore, ArtifactStore protocols ✓
- sqlite_vss VectorStore adapter ✓ (requires vss0.so at runtime)
- retrieve_for_synthesis with low-confidence gate + four-factor reranking ✓
- structural_validate (L3a Stage 1) ✓
- Synthesis node stub ✓
- Adversarial eval stub ✓
- DEFINER gate stub ✓
- Commit stub ✓

GAPS:
- model_gen_assumption fields use generic text instead of model-name-prefixed format ✗
- sqlite_vss extension fallback logic has double-suffix bug (./vss0.so.so) ✗
- Hardcoded model name detection too aggressive (flags docstrings/comments) ✗

### Phase 2 — ECS Lifecycle, YAML Workflow Engine & Review Loop

DONE:
- ECS state graph + VALID_TRANSITIONS + GuardrailedEcsStore ✓
- Review node (automated + DEFINER modes) ✓
- Re-synthesis loop with failure context injection ✓
- VersionedArtifactStore ✓
- QueryableEventStore ✓
- YAML workflow loader with Jinja2 resolution ✓
- synthesis_session_v1.yaml ✓

GAPS:
- Integration test fails due to workflow engine issues (NodeResult missing) ✗
- SequentialRunner.from_suspended classmethod missing ✗

### Phase 3 — Embedding Slot, L4 Trajectory Regulation & Multi-Turn Sessions

DONE:
- ModelSlotResolver with ci_mode ✓
- OllamaEmbeddingClient + MockOllamaEmbeddingClient ✓
- Loop detector (Type D) ✓
- Context anxiety detector (Type F) ✓
- Failure streak detector (Type E) ✓
- Trajectory regulator (2-of-3 rule) ✓
- Context reset protocol (6-step) ✓
- SessionManager ✓

GAPS:
- foundation/validation.py imports from orchestration (layer violation) ✗
- orchestration/sexton/sexton.py imports from adapter (layer violation) ✗
- orchestration/router.py imports from adapter (layer violation) ✗
- orchestration/plugins.py imports from adapter (layer violation) ✗
- Hardcoded "Qwen" in sexton docstring, "DeepSeek" in synthesis docstring ✗

### Phase 4 — pgvector Adapter, Node Promotion & Production Hardening

DONE:
- PgvectorStore with asyncpg connection pool ✓
- VectorStore factory with graceful degradation ✓
- Migration tool (structure) ✓
- Synthesis node with ModelSlotResolver integration ✓
- Evaluation pipeline (faithfulness + domain coherence stubs) ✓
- ConnectionManager with retry + fallback ✓

GAPS:
- Graceful degradation test fails (sqlite_vss fallback also fails) ✗
- Migration tool batch logic simplified/incomplete ✗
- Faithfulness and domain coherence evaluations return hardcoded CI fixtures ✗

### Phase 5 — Sexton Actor, ACE Playbook, Adaptive Router & Beast Cadence

DONE:
- Sexton failure classification (dual-mode) ✓
- ACE Playbook (SQLite-backed) ✓
- Sexton stale rule audit ✓
- Adaptive Router (structure) ✓
- BudgetManager + SqliteBudgetStore ✓
- Beast actor (structure) ✓

GAPS:
- Sexton CI mode returns empty classification list for some trace events ✗
- Beast batch logic simplified ✗
- Router exploration_weight not fully wired to routing_outcomes table ✗

### Phase 6 — Surfaces: CLI, REST API, Chat, Review Queue, MCP & Autonomy Gate

DONE:
- FastAPI app factory (structure) ✓
- CLI: init, status, config, project, session ✓
- AipContainer DI ✓
- Health, Projects, Sessions routes ✓
- Review route (structure) ✓
- Artifacts route (structure) ✓
- Admin route (structure) ✓
- Memory route (structure) ✓
- MCP server with autonomy enforcement ✓
- AutonomyGateImpl ✓
- FTS5 LexicalStore ✓
- SqliteCanonicalStore ✓
- SqliteEntityStore ✓

GAPS:
- app.py missing import for review, artifacts, admin, memory, chat routes ✗ (CRITICAL)
- chat.py had IndentationError (fixed in inventory) ✗
- MCP tool implementations (search, artifacts) are stubs ✗
- Admin/Memory routes are stubs ✗
- API integration tests all fail due to app.py NameError ✗

### Phase 7 — Vigil Actor, Auth, Extended Workflows, Canonical Pipeline & Acceptance Verification

DONE:
- Vigil actor (structure) ✓
- AuthMiddleware (Bearer + API key) ✓
- Rate limiting middleware ✓
- CanonicalPipeline (10-step sequence structure) ✓
- Extended workflow YAMLs (3 additional) ✓
- WorkflowRegistry ✓
- Web UI HTML pages (5 pages) ✓

GAPS:
- SqliteSessionStore is a stub ✗
- Auth dependencies (get_current_identity, require_definer) are stubs ✗
- Vigil detect_stale_canonicals returns empty results ✗
- Canonical pipeline some steps simplified ✗
- Acceptance test fails (depends on broken app.py) ✗

### Phase 8 — Release Hardening: Knowledge, Plugins, Collaborators, Performance

DONE:
- SqliteKnowledgeStore with provenance + dual indexing ✓
- KnowledgeCompiler (structure) ✓
- PluginManager with sandbox ✓
- PluginLoader + YamlPluginProvider (scaffolds) ✓
- CollaboratorConfig + role enforcement ✓
- PerformanceProfiler (scaffold) ✓
- Deployment: Dockerfile, docker-compose.yml, backup/restore scripts ✓

GAPS:
- Knowledge compiler tests all fail (async event loop) ✗
- Knowledge store tests fail (async event loop) ✗
- Plugin adapter CI mode incomplete ✗
- Performance profiler returns hardcoded metrics ✗
- Collaborator tests fail (async event loop) ✗
- Plugin loader is a stub ✗
- YamlPluginProvider is a stub ✗

# 2. Phase 9 Deliverables

Phase 9 delivers CHUNK-11.x. Each chunk targets a specific category of remediation. Chunks are ordered by dependency: cross-cutting violations first, then broken wiring, then stub promotion, then integration verification.

## CHUNK-11.0a: Cross-Cutting Gate Fixes

Layer: L1/L2

Delivers:
- Fix model_gen_assumption fields in validation.py and adversarial_eval.py to include specific model name references (e.g., "DeepSeek-V3 and Qwen3 models may produce...") so gate tests pass
- Fix sqlite_vss extension loading to handle missing vss0.so gracefully in CI (skip test when extension unavailable rather than fail)
- Update hardcoded-model-name detection regex to exclude docstrings, comments, and model_gen_assumption fields (only flag application logic references)
- Add httpx to allowed network import list for adapter layer (adapter is the correct layer for HTTP calls per §7.2; only foundation and orchestration are network-free)
- Fix foundation/validation.py to remove orchestration imports — move full_l3a_evaluation() to orchestration layer where it belongs

Gate test: tests/test_phase9_cross_cutting_gates.py
  - test_no_hardcoded_model_names_in_application_logic (revised regex)
  - test_network_imports_only_in_adapter (revised allow-list)
  - test_import_boundaries_three_layer (foundation no orchestration imports)
  - test_model_gen_assumption_includes_model_reference
  - test_sqlite_vss_graceful_skip_in_ci

## CHUNK-11.0b: Layer Violation Remediation

Layer: L1/L2

Delivers:
- Move full_l3a_evaluation() from foundation/validation.py to orchestration/l3a_orchestrator.py (new file). foundation/validation.py retains only structural_validate() and dataclasses.
- Create orchestration/model_provider_proxy.py that re-exports ModelSlotResolver via Protocol so orchestration code does not import adapter directly. Sexton, router, and plugins import from the proxy instead of aip.adapter.model_slot_resolver.
- Update all orchestration imports that currently reference adapter.model_slot_resolver to use orchestration.model_provider_proxy
- Add gate test verifying no orchestration file imports from adapter

Gate test: tests/test_phase9_layer_violations.py
  - test_foundation_no_orchestration_imports
  - test_orchestration_no_adapter_imports
  - test_adapter_may_import_foundation_and_orchestration
  - test_full_l3a_evaluation_in_orchestration_not_foundation

## CHUNK-11.1: FastAPI Application Factory Repair

Layer: Adapter (L6/Surfaces)

Delivers:
- Fix app.py: add missing imports for review, artifacts, admin, memory, chat route modules
- Fix chat.py: verify indentation fix (already applied) and add proper async WebSocket handling
- Wire AipContainer to provide real adapter instances (VectorStore, EcsStore, EventStore, ArtifactStore, CanonicalStore, EntityStore, LexicalStore, AutonomyGate, BudgetStore)
- Add db/ directory initialization to create required SQLite databases at startup
- Ensure all route modules have working stubs that return valid JSON

Gate test: tests/test_phase9_app_factory.py
  - test_create_app_returns_fastapi_instance
  - test_health_endpoint_200
  - test_projects_crud_round_trip
  - test_review_queue_returns_valid_json
  - test_artifacts_list_returns_valid_json
  - test_admin_returns_valid_json
  - test_memory_returns_valid_json

## CHUNK-11.2: Async Event Loop Fix

Layer: Cross-cutting (test infrastructure)

Delivers:
- Add pytest-asyncio configuration to pyproject.toml with asyncio_mode="auto"
- Convert all async test functions to use @pytest.mark.asyncio decorator consistently
- Fix KnowledgeStore, KnowledgeCompiler, CollaboratorAccess, PluginManager, and PerformanceProfiler tests to use proper async fixtures
- Ensure all test teardown properly closes async resources

Gate test: tests/test_phase9_async_fix.py
  - test_knowledge_store_crud_no_event_loop_error
  - test_knowledge_compiler_produces_artifact
  - test_collaborator_role_enforcement
  - test_plugin_manager_health_check
  - test_performance_profiler_metrics

## CHUNK-11.3: Workflow Engine Completion

Layer: L5 (Orchestration)

Delivers:
- Fix NodeResult missing import in workflow_01.py and runner.py
- Add SequentialRunner.from_suspended classmethod (spec reference: CHUNK-2.9)
- Fix commit node AttributeError when event_store is None (defensive guard)
- Verify dialog pause/resume cycle works end-to-end
- Ensure all five node types (script, agent, condition, dialog, parallel) are exercised in integration test

Gate test: tests/test_phase9_workflow_completion.py
  - test_workflow_01_happy_path_all_node_types
  - test_dialog_pause_and_resume
  - test_from_suspended_resumes_correctly
  - test_commit_with_none_stores_graceful
  - test_finally_and_on_error_handlers

## CHUNK-11.4: Trace Database Schema Initialization

Layer: L1 (Foundation)

Delivers:
- Add db/ initialization to aip init CLI command: create events.db, state.db, trace.db with required schemas
- Add trace_events table creation per §5.9 schema definition
- Add routing_outcomes table creation per §4.3
- Add initialization function callable from both CLI and test fixtures
- Ensure trace.db test passes without manual setup

Gate test: tests/test_phase9_trace_init.py
  - test_trace_events_table_exists_after_init
  - test_trace_events_failure_type_constrained
  - test_trace_events_outcome_constrained
  - test_routing_outcomes_table_exists
  - test_indexes_present

## CHUNK-11.5: Sexton Classification Completion

Layer: L2/L4 (Orchestration)

Delivers:
- Fix Sexton CI mode to produce classification results for all six failure types (A-F)
- Ensure Sexton reads from trace_events and writes back failure_type when NULL
- Verify ACE playbook derivation from classification results
- Add deterministic fixture trace data that exercises all failure type classifications

Gate test: tests/test_phase9_sexton_completion.py
  - test_sexton_classifies_type_a_through_f
  - test_sexton_writes_failure_type_back
  - test_sexton_derives_ace_rules
  - test_no_unclassified_failures_remain

## CHUNK-11.6: Adapter Stub Promotion

Layer: Adapter

Delivers:
- Promote SqliteSessionStore from stub to working implementation (bcrypt hashing, session create/validate/revoke, API key create/validate/revoke/list)
- Promote Auth dependencies (get_current_identity, require_definer) from stubs to working FastAPI dependencies
- Promote MCP search tool and artifacts tool from stubs to working implementations that delegate to LexicalStore and ArtifactStore respectively
- Promote PluginLoader from stub to working YAML-based plugin discovery
- Promote YamlPluginProvider from stub to working model provider via httpx
- Promote PerformanceProfiler from hardcoded stubs to real system metrics (psutil)

Gate test: tests/test_phase9_adapter_promotion.py
  - test_session_store_crud_with_bcrypt
  - test_auth_dependencies_enforce_roles
  - test_mcp_search_uses_lexical_store
  - test_mcp_artifacts_uses_artifact_store
  - test_plugin_loader_discovers_yaml
  - test_profiler_returns_real_metrics

## CHUNK-11.7: Vigil and Canonical Pipeline Completion

Layer: L2/L3 (Orchestration)

Delivers:
- Implement Vigil.detect_stale_canonicals to query canonical store with staleness threshold
- Implement Vigil.check_canonical_health to evaluate faithfulness of stale canonicals
- Complete CanonicalPipeline 10-step sequence: all steps must be real (not simplified)
- Wire Vigil.on_model_slot_change to trigger Sexton stale rule audit per §1.8

Gate test: tests/test_phase9_vigil_canonical.py
  - test_vigil_detects_stale_canonicals_by_threshold
  - test_vigil_health_check_evaluates_faithfulness
  - test_canonical_pipeline_all_10_steps
  - test_vigil_on_slot_change_triggers_sexton_audit

## CHUNK-11.8: Integration Verification & Acceptance Gate Re-run

Layer: Integration

Delivers:
- Full end-to-end test: Workflow 0.1 runs from retrieval through canonical promotion
- Re-run all §22 acceptance gates [01]-[35] and verify pass
- Verify all Phase 1-8 gate tests still pass (regression check)
- Verify laptop-viable constraint: system starts and operates with 4GB RAM profile
- Verify production hardening: graceful degradation when pgvector unavailable

Gate test: tests/test_phase9_acceptance.py
  - test_workflow_01_end_to_end_with_canonical_promotion
  - test_acceptance_gates_01_through_35
  - test_all_prior_phase_tests_still_pass
  - test_laptop_viable_4gb_profile
  - test_graceful_degradation_pgvector_unavailable

## CHUNK-11.9: Cross-Cutting Gates (Phase 9)

Layer: Cross-cutting

Delivers:
- Network isolation gate: adapter layer may import httpx; foundation and orchestration may not
- Model name gate: no hardcoded model names in application logic; docstrings/comments exempt
- Import boundary gate: three-layer enforcement (foundation <- adapter <- orchestration corrected to: foundation has no upward imports; orchestration imports foundation only; adapter imports foundation only, never orchestration directly)
- DEFINER sovereignty gate: no surface bypasses AutonomyGate for canonical modifications
- Appendix D constraint verification

Gate test: tests/test_phase9_cross_cutting.py
  - test_network_imports_allowed_in_adapter_only
  - test_no_hardcoded_models_in_application_logic
  - test_three_layer_import_boundaries
  - test_definer_sovereignty_on_canonical_writes
  - test_appendix_d_constraints

# 3. Dependency DAG

CHUNK-11.0a (cross-cutting gate fixes) and CHUNK-11.0b (layer violations) have no dependencies and may be built in parallel.

CHUNK-11.1 (app factory) depends on 11.0b (layer violations must be fixed first so app.py imports resolve correctly).

CHUNK-11.2 (async fix) has no code dependencies but should be done early to unblock Phase 8 test fixes.

CHUNK-11.3 (workflow engine) depends on 11.0a (NodeResult import fix).

CHUNK-11.4 (trace init) has no dependencies.

CHUNK-11.5 (sexton) depends on 11.4 (trace schema must exist).

CHUNK-11.6 (adapter stubs) depends on 11.1 (app factory must be working).

CHUNK-11.7 (vigil/canonical) depends on 11.5 (sexton must classify for Vigil triggers).

CHUNK-11.8 (integration) depends on ALL prior chunks.

CHUNK-11.9 (cross-cutting) runs after 11.8.

Parallel groups:
  Group A: 11.0a, 11.0b, 11.2, 11.4 (no dependencies)
  Group B: 11.1, 11.3 (depend on Group A)
  Group C: 11.5, 11.6 (depend on Group B)
  Group D: 11.7 (depends on 11.5)
  Group E: 11.8 (depends on all)
  Group F: 11.9 (depends on 11.8)

# 4. Acceptance Gates for Phase 9

Phase 9 extends §22 with the following gates. Gates [01]-[35] from Rev 5.2 must all pass.

[36] Import boundaries enforced:
foundation has zero imports from orchestration or adapter.
orchestration imports from foundation only (via proxy for model provider).
adapter imports from foundation only.
tests/test_phase9_layer_violations.py passes.

[37] FastAPI application factory functional:
create_app() returns working FastAPI instance.
All route modules import and mount without error.
Health, Projects, Sessions, Review, Artifacts, Admin, Memory endpoints respond.
tests/test_phase9_app_factory.py passes.

[38] Async test infrastructure fixed:
No "no current event loop" errors in any test.
All async tests use proper pytest-asyncio fixtures.
Knowledge store, knowledge compiler, collaborator, plugin tests pass.

[39] Workflow engine complete:
All five node types exercised in integration.
Dialog pause/resume works.
SequentialRunner.from_suspended works.
tests/test_phase9_workflow_completion.py passes.

[40] Trace database initialization:
aip init creates events.db, state.db, trace.db with required schemas.
trace_events table matches §5.9 schema.
routing_outcomes table matches §4.3 schema.
tests/test_phase9_trace_init.py passes.

[41] Sexton classification complete:
Sexton CI mode classifies all six failure types.
No unclassified failures remain after Sexton run.
ACE playbook derivation works.
tests/test_phase9_sexton_completion.py passes.

[42] Adapter stubs promoted:
SqliteSessionStore works with bcrypt.
Auth dependencies enforce roles.
MCP tools delegate to stores.
PluginLoader discovers YAML plugins.
PerformanceProfiler returns real metrics.
tests/test_phase9_adapter_promotion.py passes.

[43] Vigil and canonical pipeline complete:
Vigil detects stale canonicals.
Canonical pipeline all 10 steps are real.
Vigil triggers Sexton on model slot change.
tests/test_phase9_vigil_canonical.py passes.

[44] Full acceptance re-verification:
All gates [01]-[35] pass.
All Phase 9 gates [36]-[43] pass.
100% test pass rate (0 failures, 0 errors).
tests/test_phase9_acceptance.py passes.

# 5. Process Rules (Binding)

Phase 9 inherits all binding rules from Phases 1-8. The following are emphasized or added:

1. CONTINUITY CHECK — Read WORKLOG.md before each chunk. Phase 9 chunks must document which Phase 1-8 gap they close.

2. WORKLOG APPEND-ONLY — Never overwrite. Each chunk appends.

3. AMEND BY ADDITION — schemas.py and protocols.py are NOT modified in Phase 9. No new dataclasses or protocols are added. This is a remediation phase.

4. DETERMINISTIC CI — All gate tests pass without network, API keys, Ollama, or PostgreSQL.

5. PUSH AFTER EACH CHUNK — Each chunk is a commit boundary.

6. IMPORT BOUNDARIES — Three-layer enforcement is the PRIMARY deliverable of Phase 9.

7. NO HARDCODED MODEL NAMES — In application logic only. Docstrings, comments, and model_gen_assumption fields are exempt from the gate test.

8. NO NEW FEATURES — Phase 9 does not add capabilities. It closes gaps.

9. REGRESSION GUARANTEE — Every chunk must pass all prior phase tests plus its own gate.

10. LAYER VIOLATION PROXY — Orchestration may not import adapter. A proxy module in orchestration re-exports adapter protocols through a foundation-defined interface.

11. NETWORK IMPORT SCOPING — httpx is permitted in adapter layer ONLY. Foundation and orchestration remain network-free. The gate test uses a per-layer allow-list.

12. ASYNC FIXTURE STANDARD — All async tests use @pytest.mark.asyncio. No manual event loop creation. pytest-asyncio asyncio_mode="auto" in pyproject.toml.

13. STUB PROMOTION ACCOUNTABILITY — Each promoted stub must reference the original spec chunk it was specified in (e.g., "Promotes CHUNK-8.0b SqliteSessionStore").

14. 100% GATE TARGET — Phase 9 is not complete until all 433 tests pass with zero failures and zero errors.

# 6. Configuration Changes

Phase 9 adds no new config sections. The following changes to pyproject.toml are required:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

No changes to aip.config.toml. Phase 9 fixes code, not configuration.

# 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Layer refactoring breaks existing tests | Medium | High | Run full test suite after each chunk. No chunk merged unless all prior tests pass. |
| Import proxy introduces circular dependency | Low | High | Proxy module imports only from foundation.protocols. No circular path possible. |
| Async fixture migration causes subtle test ordering issues | Medium | Medium | Use asyncio_mode="auto" for deterministic fixture scoping. Run with -x flag. |
| Promoting stubs reveals deeper design issues | Medium | Medium | Chunk 11.6 is the largest. Break into sub-chunks if stubs reveal gaps beyond spec. |
| sqlite_vss unavailable in all CI environments | High | Low | Skip sqlite_vss tests gracefully. pgvector is the production path. |
