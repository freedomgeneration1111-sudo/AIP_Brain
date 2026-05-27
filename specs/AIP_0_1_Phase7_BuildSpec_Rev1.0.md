# AIP 0.1 Phase 7 BuildSpec

**Product:** AI Poiesis (AIP) version 0.1  
**Architecture Revision:** 5.2  
**Build Phase:** 7 — Vigil Actor, Auth, Extended Workflows, Canonical Pipeline & Acceptance Verification  
**Spec Revision:** 1.0  
**Date:** May 2026  
**Status:** Build Specification — for Grok Build execution  
**Supersedes:** N/A (initial Phase 7 spec)  
**DEFINER:** Moses Jorgensen

---

## Revision Log

### 1.0 — Initial

| Item | Decision | Rationale |
|---|---|---|
| Vigil actor | `orchestration/actors/vigil.py` — compiled knowledge maintenance: detects stale canonical artifacts, triggers re-evaluation when model slots change, maintains entity consistency across the canonical corpus | §3 layer model: "Vigil — compiled knowledge maintenance, deferred"; deferred through Phases 1–6; Vigil is the last missing actor from the §3 orchestration layer; §16.1: "Vigil monitors canonical corpus health"; §1.8: "On every model slot upgrade: audit the harness for stale assumptions" — Vigil automates this for canonical artifacts |
| Authentication system | `adapter/auth/` — session-based authentication, DEFINER identity, API key support, role-based access control | §1.7: "No UI may bypass the DEFINER gates"; Phase 6 left auth as "placeholder (AIP 0.1 is single-user DEFINER)"; Phase 7 delivers the real auth system that enforces DEFINER sovereignty at the identity level; API key support enables MCP and CLI non-interactive access |
| Rate limiting | `adapter/middleware/rate_limiter.py` — token-bucket rate limiting per endpoint, per DEFINER, per IP; configurable per §1.8 | Phase 6 deferred: "single-user alpha; not needed yet"; Phase 7 delivers rate limiting for the multi-workflow, multi-surface system where Beast cadence, MCP calls, and DEFINER chat all compete for model budget; rate limiting prevents any single surface from starving others |
| Extended workflow templates | `workflows/` — additional YAML workflow templates beyond Workflow 0.1 | §11.1: "L5 YAML engine supports arbitrary workflow definitions"; Phase 2 delivered only `synthesis_session_v1.yaml`; Phase 7 delivers the workflows that exercise the full ECS lifecycle: incremental update, adversarial red-team, corpus maintenance |
| Canonical promotion pipeline | `orchestration/canonical_pipeline.py` — full REVIEWED→APPROVED→CANONICAL lifecycle with multi-stage verification, DEFINER approval gate, canonical indexing, and FTS5/VectorStore synchronization | §1.6: "Canonical artifacts require explicit approval and are stored separately from the original generated artifact"; §9.3: canonical promotion is the final ECS transition; Phase 6 delivered the CanonicalStore adapter and the AutonomyGate, but no orchestration component actually drives the canonical pipeline; Phase 7 wires the full promotion flow |
| Web UI scaffold | `adapter/api/static/` — minimal HTML+HTMX dashboard served by FastAPI, consuming the Phase 6 REST API | Phase 6 deferred: "Visual UI / web frontend (deferred — surfaces are API-first; a future web UI consumes the same REST API)"; Phase 7 delivers a minimal server-rendered UI that proves the REST API is complete and usable; NOT a full SPA — just HTMX pages that call the existing API endpoints |
| Full acceptance verification | `tests/acceptance/` — §22 acceptance gate verification: Sexton completeness, budget enforcement, ECS lifecycle, DEFINER sovereignty, Vigil corpus health, multi-surface isolation | §22: acceptance gates must be verified before AIP 0.1 is declared complete; Phases 1–6 each had their own integration tests, but no phase has verified the entire system end-to-end against the §22 acceptance criteria; Phase 7 is the verification phase |
| Production packaging | `deploy/` — Docker Compose, configuration profiles, health check orchestration, backup/restore | §2.1: "laptop-viable"; §2.2: "PostgreSQL 16 + pgvector is the required production path"; the system needs a deployment story; Phase 7 delivers Docker packaging that works for both laptop-viable (sqlite_vss + Ollama) and production (pgvector + API models) profiles |

---

## Phase 7 Scope

Phase 7 is the capstone phase. It delivers every component that was explicitly deferred across Phases 1–6, and then verifies that the complete AIP 0.1 system meets the §22 acceptance criteria.

Phase 6 made AIP 0.1 usable through surfaces — CLI, REST API, chat, review queue, and MCP. But it left several gaps: the Vigil actor (the last missing orchestration component from §3), real authentication (only a placeholder existed), rate limiting (not needed for single-surface alpha but essential when Beast cadence, MCP, and chat all run concurrently), the canonical promotion pipeline (CanonicalStore existed but nothing drove the REVIEWED→APPROVED→CANONICAL lifecycle end-to-end), and additional workflow templates (only Workflow 0.1 existed). Phase 7 closes all of these gaps, adds a minimal web UI that proves the REST API surface is complete, and then runs the full §22 acceptance verification.

Phase 7 also delivers production packaging — Docker Compose configurations for both the laptop-viable profile (sqlite_vss + Ollama + local models) and the production profile (pgvector + API models + authentication). This is the deployment story that makes AIP 0.1 installable and runnable by someone other than the DEFINER.

**In scope:**

- CHUNK-9.0a: Schema additions — `VigilConfig`, `AuthConfig`, `RateLimitConfig`, `CanonicalPromotionConfig`, `WorkflowTemplate`, `DeploymentProfile` dataclasses + Protocol amendments (`VigilStore` new Protocol, `AuthStore` new Protocol, `CanonicalPipeline` Protocol methods) + Config extensions (L1, append-only)
- CHUNK-9.0b: Authentication & authorization — `adapter/auth/` with session-based auth, API key auth, DEFINER identity, role-based access (adapter)
- CHUNK-9.0c: Rate limiting — `adapter/middleware/rate_limiter.py` with token-bucket algorithm, per-endpoint/per-DEFINER/per-IP limits, configurable per §1.8 (adapter)
- CHUNK-9.1: Vigil actor — `orchestration/actors/vigil.py` — compiled knowledge maintenance, stale canonical detection, model slot change audit trigger, entity consistency verification (L2/L5, orchestration)
- CHUNK-9.2: Canonical promotion pipeline — `orchestration/canonical_pipeline.py` — REVIEWED→APPROVED→CANONICAL lifecycle with multi-stage verification, DEFINER approval via AutonomyGate, canonical indexing in VectorStore + LexicalStore (L2/L3, orchestration)
- CHUNK-9.3: Extended workflow templates — additional YAML workflows beyond 0.1: incremental update, adversarial red-team, corpus maintenance (L5, orchestration/config)
- CHUNK-9.4: Web UI scaffold — minimal HTML+HTMX dashboard served by FastAPI, consuming the Phase 6 REST API: project overview, review queue, chat interface, admin console (adapter)
- CHUNK-9.5: Full acceptance test — §22 acceptance gate verification across all six prior phases, end-to-end system verification, DEFINER sovereignty audit, budget enforcement, Vigil health check (integration)
- CHUNK-9.6: Production packaging — Docker Compose, configuration profiles (laptop-viable + production), backup/restore scripts, health check orchestration (deployment)
- CHUNK-9.7: Cross-cutting gates — network isolation, model-name gate, import boundary verification, DEFINER sovereignty gate, Appendix D constraint verification, auth bypass prevention (cross-cutting)

**Out of scope:**

- Mobile surfaces (post-0.1)
- Full SPA web frontend with React/Next.js (the HTMX scaffold is sufficient for 0.1; a proper SPA is post-0.1)
- Multi-tenant / multi-user isolation (AIP 0.1 is single-DEFINER; multi-tenant is post-0.1)
- External SSO / OAuth integration (API key + session auth is sufficient for 0.1)
- Custom model provider plugins (the four named slots from §4.1 are the 0.1 scope)
- Performance benchmarking / load testing (beyond rate limiting; post-0.1)
- Internationalization / localization of the web UI

---

## Phase 6 Assumptions (Architectural Phase 6 = CHUNK-8.x series)

Phase 7 chunks depend on the following Phase 6 deliverables being merged and green:

| CHUNK-8.x | Deliverable | Phase 7 Dependency |
|---|---|---|
| 8.0a | `foundation/schemas.py` — `SurfaceConfig`, `ApiRoute`, `McpToolDef`, `AutonomyEscalation`, `ChatMessage`, `ReviewQueueEntry`, `AutonomyLevel`, `McpAutonomyLevel` | 9.0a appends; 9.0b (auth uses AutonomyLevel); 9.2 (canonical pipeline uses AutonomyEscalation) |
| 8.0a | `foundation/protocols.py` — `AutonomyGate`, `LexicalStore`, `CanonicalStore` methods, `EntityStore` methods | 9.0a appends; 9.1 (Vigil uses CanonicalStore, EntityStore); 9.2 (canonical pipeline uses AutonomyGate, CanonicalStore, LexicalStore) |
| 8.0b | `adapter/lexical/sqlite_fts5_store.py` — `SqliteFts5LexicalStore` | 9.2 (canonical pipeline indexes into LexicalStore); 9.4 (web UI search uses LexicalStore) |
| 8.0b | `adapter/canonical/sqlite_canonical_store.py` — `SqliteCanonicalStore` | 9.1 (Vigil reads canonicals); 9.2 (canonical pipeline writes canonicals) |
| 8.0b | `adapter/entity/sqlite_entity_store.py` — `SqliteEntityStore` | 9.1 (Vigil verifies entity consistency) |
| 8.0b | `adapter/autonomy/autonomy_gate.py` — `AutonomyGateImpl` | 9.0b (auth integrates with AutonomyGate); 9.2 (canonical pipeline escalates via AutonomyGate) |
| 8.1 | `adapter/api/app.py` — FastAPI app factory + DI container | 9.0b (auth middleware added to app); 9.0c (rate limiter middleware added to app); 9.4 (web UI static files served by app) |
| 8.2 | `adapter/cli/` — CLI commands | 9.5 (acceptance test exercises CLI); 9.6 (Docker entrypoint uses CLI) |
| 8.3 | `adapter/api/chat.py` — Chat surface | 9.0b (auth gates chat); 9.5 (acceptance test exercises chat) |
| 8.4 | `adapter/api/review.py` — Review Queue | 9.2 (canonical pipeline uses review queue); 9.5 (acceptance test exercises review) |
| 8.5 | `adapter/mcp/` — MCP server | 9.0b (auth gates MCP); 9.5 (acceptance test exercises MCP) |
| 8.6 | `adapter/api/admin.py` — Admin Console | 9.1 (admin shows Vigil status); 9.4 (web UI extends admin) |
| 8.7 | Integration test | 9.5 extends |
| 8.8 | Cross-cutting gates | 9.7 extends |

Phase 5 dependencies (transitive through Phase 6):

| CHUNK-7.x | Deliverable | Phase 7 Dependency |
|---|---|---|
| 7.0a | `foundation/schemas.py` — `SextonConfig`, `AcePlaybookEntry`, `BudgetConfig`, `RoutingWeight`, `BeastCadenceConfig`, `FailureClassification`, `BudgetScope` | 9.0a appends; 9.1 (Vigil reads FailureClassification); 9.5 (acceptance test verifies budget) |
| 7.0a | `foundation/protocols.py` — `BudgetStore`, `ProjectStore.list_projects` | 9.0a appends; 9.0c (rate limiter respects budget); 9.5 (acceptance test verifies budget enforcement) |
| 7.0b | `orchestration/budget.py` — `BudgetManager` | 9.0c (rate limiter consults budget); 9.5 (acceptance test) |
| 7.1 | `orchestration/actors/sexton.py` — `Sexton` | 9.1 (Vigil and Sexton are complementary: Sexton classifies failures, Vigil maintains canonicals; Vigil may trigger Sexton audit on stale canonicals) |
| 7.2 | `orchestration/ace_playbook.py` — `AcePlaybook` | 9.1 (Vigil may read playbook to check if canonical is covered by active rules) |
| 7.4 | `orchestration/router.py` — `AdaptiveRouter` | 9.1 (Vigil triggers re-evaluation when router adjusts model slots) |
| 7.5 | `orchestration/actors/beast.py` — `Beast` | 9.1 (Vigil and Beast are complementary: Beast maintains corpus vectors, Vigil maintains canonical knowledge; per Appendix D: "Beast ≠ Sexton" and "Vigil ≠ Beast") |

Phase 4 dependencies (transitive through Phase 5/6):

| CHUNK-6.x | Deliverable | Phase 7 Dependency |
|---|---|---|
| 6.0a | `foundation/schemas.py` — `PgvectorConfig`, `EvaluationScore`, `FaithfulnessResult`, `DomainCoherenceResult`, `VectorBackendType` | 9.0a appends; 9.2 (canonical pipeline runs evaluation); 9.6 (deployment profiles use VectorBackendType) |
| 6.2 | `orchestration/nodes/adversarial_eval.py`, `faithfulness.py`, `domain_coherence.py` | 9.2 (canonical pipeline runs full evaluation before promotion); 9.3 (red-team workflow uses adversarial eval) |
| 6.3 | `adapter/vector/factory.py` — `create_vector_store` | 9.2 (canonical pipeline indexes into VectorStore via factory); 9.6 (Docker config uses factory) |
| 6.4 | Production hardening — health checks, graceful degradation | 9.5 (acceptance test verifies health checks); 9.6 (Docker health check uses API health endpoint) |

Phase 3 dependencies (transitive through Phase 4/5/6):

| CHUNK-5.x | Deliverable | Phase 7 Dependency |
|---|---|---|
| 5.0a | `foundation/schemas.py` — `TrajectorySignal`, `SessionContext`, `ModelSlotConfig` | 9.0a appends; 9.3 (extended workflows use ModelSlotConfig) |
| 5.0a | `foundation/protocols.py` — `TraceStore.query_events`, `ModelProvider`, `EmbeddingProvider` | 9.1 (Vigil queries trace events for stale canonicals); 9.2 (canonical pipeline uses ModelProvider for evaluation) |
| 5.0b | `adapter/model_slot_resolver.py` — `ModelSlotResolver` | 9.1 (Vigil monitors slot changes); 9.3 (workflows use ModelSlotResolver) |
| 5.7 | `orchestration/session.py` — `SessionManager` | 9.0b (auth integrates with SessionManager); 9.5 (acceptance test) |

Phase 2 dependencies (transitive through Phase 3/4/5/6):

| CHUNK-4.x | Deliverable | Phase 7 Dependency |
|---|---|---|
| 4.0a | `foundation/schemas.py` — `ReviewVerdict`, `Event`, `FailureTypeCode` | 9.0a appends; 9.2 (canonical pipeline uses ReviewVerdict); 9.5 (acceptance test) |
| 4.0b | `foundation/ecs_graph.py` — `VALID_TRANSITIONS`, `InvalidTransitionError` | 9.2 (canonical pipeline uses ECS transitions); 9.5 (acceptance test) |
| 4.5 | `orchestration/engine.py` — YAML workflow engine | 9.3 (extended workflows use engine); 9.5 (acceptance test) |
| 4.6 | `workflows/synthesis_session_v1.yaml` | 9.3 (new workflows reference 0.1 as template) |

Phase 1 dependencies (transitive through Phase 2/3/4/5/6):

| CHUNK-1.x | Deliverable | Phase 7 Dependency |
|---|---|---|
| 1.0a | `foundation/schemas.py` — `Chunk`, `ContractRule`, `RetrievalResult` | 9.0a appends; 9.1 (Vigil reads ContractRule for stale assumptions); 9.2 (canonical pipeline indexes Chunk) |
| 1.0a | `foundation/protocols.py` — `VectorStore`, `TraceStore`, `EcsStore`, `EventStore`, `ArtifactStore` | 9.0a appends; all Phase 7 components use existing Protocols |
| 1.2 | `orchestration/validation.py` — `structural_validate` | 9.2 (canonical pipeline runs structural validation); 9.5 (acceptance test) |

Phase 0 dependencies:

| CHUNK-0.x | Deliverable | Phase 7 Dependency |
|---|---|---|
| 0.2 | `config/aip.config.toml` — base config | 9.0a extends config with `[vigil]`, `[auth]`, `[rate_limit]`, `[canonical_pipeline]`, `[deployment]` sections |
| 0.3 | `db/routing_outcomes` table schema | 9.1 (Vigil reads routing_outcomes to detect model slot changes) |
| 0.5 | `db/trace_events` table schema | 9.1 (Vigil reads trace_events for canonical health) |

**Critical note on CHUNK-9.0a:** This chunk appends to `foundation/schemas.py` and amends `foundation/protocols.py` — the same append-only/amend-by-addition pattern as CHUNK-1.0a, CHUNK-4.0a, CHUNK-5.0a, CHUNK-6.0a, CHUNK-7.0a, and CHUNK-8.0a. No existing Phase 0, Phase 1, Phase 2, Phase 3, Phase 4, Phase 5, or Phase 6 code is deleted or rewritten.

**Continuity note:** Phase 7 components span all three layers. The Vigil actor and canonical pipeline are orchestration-layer components that compose existing Protocol implementations. The auth system and rate limiter are adapter-layer middleware that enforce cross-cutting concerns. The web UI scaffold is an adapter-layer surface. The acceptance tests and production packaging are cross-cutting. Per §7.2: orchestration may import foundation and adapter; adapter may import foundation but not orchestration; foundation never imports orchestration or adapter. The Vigil actor imports Foundation Protocols and schemas, and orchestration components (Sexton, Beast, AdaptiveRouter). It never imports adapter implementations directly — all storage access is via Protocol injection.

---

## Process Rules

These rules are binding for all work against this BuildSpec. They are inherited from Phase 1 Rev 1.3, Phase 2 Rev 1.2, Phase 3 Rev 1.1, Phase 4 Rev 1.0, Phase 5 Rev 1.0, and Phase 6 Rev 1.0 and must be followed without exception:

1. **Continuity Check.** Before starting any chunk, read `WORKLOG.md` and verify all DEPENDS-ON chunks are merged and green. This includes all Phase 1 (1.x), Phase 2 (4.x), Phase 3 (5.x), Phase 4 (6.x), Phase 5 (7.x), and Phase 6 (8.x) chunks. If any dependency is not met, block and report.

2. **WORKLOG append-only.** Every chunk completion appends a new work record to `WORKLOG.md`. Never overwrite, delete, or reorder existing entries. Each record must include Task ID, Agent, Task description, Work Log (concrete steps), and Stage Summary.

3. **Amend by addition — schemas.** New dataclasses, type aliases, and enums are appended to `foundation/schemas.py`. Never modify, reorder, or delete existing Phase 0/1/2/3/4/5/6 definitions. The test suite verifies this by importing Phase 0/1/2/3/4/5/6 types and asserting they still work.

4. **Amend by addition — protocols.** New methods are appended as stubs to existing Protocol classes in `foundation/protocols.py`. New Protocol classes (VigilStore, AuthStore) are added as new class definitions or appended method stubs. Never redeclare an existing Protocol class. The ANNEX shows individual method stubs for amendments and full class blocks for new Protocols only.

5. **Deterministic CI.** All gate tests must pass without network access, API keys, secrets, or external services. Auth tests use in-memory session stores and hashed API keys. Rate limiter tests use mock timers. Vigil tests use fixture canonical data. Web UI tests use FastAPI TestClient. Docker tests verify compose file syntax only (no real containers in CI).

6. **Push after each chunk.** After a chunk passes its gate test (`uv run pytest <test_file> -xvs`), commit and push before starting the next chunk. One chunk per commit.

7. **Import boundaries.** Foundation never imports orchestration or adapter. Adapter may import foundation but not orchestration. Orchestration may import both foundation and adapter. The layering test (`tests/test_layering.py`) enforces this. **Phase 7 addition:** Auth middleware is adapter-layer and imports only foundation. Vigil is orchestration-layer and imports foundation Protocols + schemas. The canonical pipeline is orchestration-layer. The web UI scaffold is adapter-layer. Docker/deployment files are not Python and are exempt from import boundary checks, but any Python scripts they call must respect boundaries.

8. **No hardcoded model names.** Per §4.1, all model references resolve through named slots from config. No model name may appear in any `orchestration/` or `foundation/` file. The test_no_hardcoded_model_names test enforces this. Vigil uses the `evaluation` model slot for re-evaluation; canonical pipeline uses the `evaluation` model slot.

9. **Qualified phase references.** Always use qualified terminology: "Architectural Phase 7" for the logical scope, "CHUNK-9.x" for build units, "repo 3.x" for historical commits. Never use bare "Phase 7" without qualification.

10. **Repo overlap reconciliation.** Before building any CHUNK-9.x, check whether repo 2.x or 3.x code already implements part of the spec (especially Vigil, auth, or workflow work). If overlap exists, extend existing code to meet the spec (amend by addition) rather than rewriting from scratch. Document reconciliation in WORKLOG.

11. **DEFINER sovereignty enforcement.** Per §1.7: "No UI, workflow, Beast cadence, MCP call, or queued task may bypass the DEFINER gates." The canonical promotion pipeline must go through the AutonomyGate for APPROVED→CANONICAL. The auth system must enforce DEFINER identity for all write/admin operations. Vigil is a read-only actor — it detects and reports stale canonicals but never modifies them autonomously. Per Appendix D: "Vigil ≠ autonomous modifier."

12. **Vigil is read-only.** Per §3: "Vigil — compiled knowledge maintenance." Per Appendix D: Vigil monitors and reports; it does not autonomously modify canonical artifacts. When Vigil detects a stale canonical, it creates a trace event and optionally triggers Sexton to re-evaluate, but it never directly promotes, demotes, or rewrites a canonical. This is the same pattern as Sexton: classification ≠ resolution. The harness corrects; Vigil improves awareness.

13. **Auth scope is 0.1.** AIP 0.1 is single-DEFINER. The auth system supports one DEFINER identity with API keys for non-interactive access (CLI, MCP). There is no multi-user isolation, no role hierarchy beyond DEFINER vs. unauthenticated, and no external SSO. Post-0.1 phases will extend this. The auth system must be designed to be extensible (Protocol-based) but must not over-engineer for multi-user scenarios that do not exist yet.

---

## Repo State Reconciliation

**Important:** This spec was written when the codebase was assumed to contain Phase 1 through Phase 6 code. The actual repo contains additional work from historical chunk series 2.x (YAML engine mechanics) and 3.x (L4/Sexton/ACE/budget foundation). This section documents the reconciliation.

### What this spec introduces that does NOT yet exist in code

| Type/Method | CHUNK | Notes |
|-------------|-------|-------|
| `VigilConfig` dataclass | 9.0a | New — no prior implementation |
| `AuthConfig` dataclass | 9.0a | New — no prior implementation |
| `RateLimitConfig` dataclass | 9.0a | New — no prior implementation |
| `CanonicalPromotionConfig` dataclass | 9.0a | New — no prior implementation |
| `WorkflowTemplate` dataclass | 9.0a | New — no prior implementation |
| `DeploymentProfile` dataclass | 9.0a | New — no prior implementation |
| `VigilStore` Protocol | 9.0a | New — listed in §6 but never defined |
| `AuthStore` Protocol | 9.0a | New — no prior Protocol definition |
| `adapter/auth/` — Authentication system | 9.0b | New — no prior auth implementation (Phase 6 had placeholder) |
| `adapter/middleware/rate_limiter.py` — Rate limiter | 9.0c | New — no prior rate limiting |
| `orchestration/actors/vigil.py` — `Vigil` | 9.1 | New — repo 3.x may have partial Vigil |
| `orchestration/canonical_pipeline.py` — `CanonicalPipeline` | 9.2 | New — no prior orchestration for canonical promotion |
| `workflows/incremental_update_v1.yaml` | 9.3 | New — no prior workflow templates beyond 0.1 |
| `workflows/adversarial_redteam_v1.yaml` | 9.3 | New — no prior workflow templates beyond 0.1 |
| `workflows/corpus_maintenance_v1.yaml` | 9.3 | New — no prior workflow templates beyond 0.1 |
| `adapter/api/static/` — Web UI scaffold | 9.4 | New — no prior web UI |
| `tests/acceptance/` — Full acceptance tests | 9.5 | New — no prior §22 acceptance verification |
| `deploy/` — Docker Compose + config profiles | 9.6 | New — no prior deployment packaging |
| Phase 7 cross-cutting gates | 9.7 | Extend CHUNK-8.8 |

### What already exists that may overlap

| Repo Series | What It Built | Overlap With |
|-------------|--------------|-------------|
| Repo 2.x (CHUNK-2.1–2.13) | YAML engine mechanics | 9.3 (extended workflows use engine) |
| Repo 3.x (CHUNK-3.1–3.12) | L4/Sexton/ACE/budget foundation | 9.1 (Vigil may overlap with partial Vigil from repo 3.x); 9.2 (canonical pipeline may overlap with canonical scaffolding) |

**Build strategy:** Where repo 3.x code already exists (especially Vigil or canonical scaffolding), extend it to meet the spec rather than replacing it. The spec is the authoritative target; existing code is a head start, not a conflict. Document all reconciliations in WORKLOG.

---

## Dependency DAG

```
CHUNK-8.0a ── CHUNK-8.0b ── CHUNK-8.1 ── CHUNK-8.2 ── CHUNK-8.3 ── CHUNK-8.4 ── CHUNK-8.5 ── CHUNK-8.6 ── CHUNK-8.7 ── CHUNK-8.8
     │              │            │            │            │            │            │            │            │            │
     │              │            │            │            │            │            │            │            │            │
CHUNK-9.0a ────── CHUNK-9.0b ─┼────────────┼────────────┼────────────┼────────────┼────────────┤            │            │
     │              │           │            │            │            │            │            │            │            │
     │        CHUNK-9.0c ──────┤            │            │            │            │            │            │            │
     │              │           │            │            │            │            │            │            │            │
     │              ├──── CHUNK-9.1 (vigil)  │            │            │            │            │            │            │
     │              │           │            │            │            │            │            │            │            │
     │              ├──── CHUNK-9.2 (canonical pipeline)  │            │            │            │            │            │
     │              │                        │            │            │            │            │            │            │
     │              ├──── CHUNK-9.3 (workflows)           │            │            │            │            │            │
     │              │                        │            │            │            │            │            │            │
     │              ├──── CHUNK-9.4 (web UI) │            │            │            │            │            │            │
     │              │                        │            │            │            │            │            │            │
     └────────────────────────────────────────────────────── CHUNK-9.5 ─┘            │            │
                                                              (acceptance)          │            │
                                                                       │              │
                                                                  CHUNK-9.6 ─────────┤
                                                                   (packaging)        │
                                                                       │              │
                                                                  CHUNK-9.7 ─────────┘
                                                                   (gates)

Linearized build order:
  9.0a → 9.0b (parallel with 9.0c after 9.0a) → 9.0c
       → 9.0b → 9.1 (after 9.0b, 9.0a, CHUNK-7.1)
       → 9.0b → 9.2 (after 9.0b, 9.0a, CHUNK-8.4)
       → 9.0b → 9.3 (after 9.0b, 9.0a, CHUNK-4.5)
       → 9.0b → 9.4 (after 9.0b, 9.0c, CHUNK-8.1)
       → 9.5 (after 9.1, 9.2, 9.3, 9.4, CHUNK-8.7)
       → 9.6 (after 9.5)
       → 9.7 (after all)

Parallel groups:
  Group A: [9.0a]                                              — schema + protocol additions
  Group B: [9.0b] (after 9.0a, CHUNK-8.1)                     — auth system
  Group C: [9.0c] (after 9.0a, CHUNK-8.1)                     — rate limiter (parallel with 9.0b)
  Group D: [9.1] (after 9.0b, 9.0a, CHUNK-7.1)               — Vigil actor
  Group E: [9.2] (after 9.0b, 9.0a, CHUNK-8.4)               — Canonical pipeline
  Group F: [9.3] (after 9.0b, 9.0a, CHUNK-4.5)               — Extended workflows
  Group G: [9.4] (after 9.0b, 9.0c, CHUNK-8.1)               — Web UI scaffold
  Group H: [9.5] (after 9.1, 9.2, 9.3, 9.4, CHUNK-8.7)       — Full acceptance test
  Group I: [9.6] (after 9.5)                                   — Production packaging
  Group J: [9.7] (after all)                                   — Cross-cutting gates
```

The key architectural insight: **Groups B–C are a parallel pair** (auth and rate limiting are independent middleware), and **Groups D–G are independent parallel paths** that all depend on the auth system being in place. Once 9.0b (auth) is merged, the Vigil actor, canonical pipeline, extended workflows, and web UI can all be built independently. They all converge at the full acceptance test (9.5), which verifies the complete system against §22 acceptance criteria. Production packaging (9.6) comes after acceptance, since the Docker images must contain a verified system.

---

## CHUNK-9.0a: Schema Additions + Protocol Amendments + Config Extensions

```
CHUNK-9.0a: Schema Additions + Protocol Amendments + Config Extensions
PHASE: 7
DEPENDS-ON: CHUNK-8.0a, CHUNK-7.0a
CODER-PROFILE: L1
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  foundation/schemas.py (append only — do not modify existing Phase 0/1/2/3/4/5/6 enums or dataclasses)
  foundation/protocols.py (amend by addition — add methods to existing Protocol classes + add new Protocols)
INTERFACES:
  @dataclass
  class VigilConfig:
      canonical_health_check_interval_seconds: int  # how often Vigil checks canonical health
      stale_threshold_days: int                    # days before a canonical is considered potentially stale
      re_evaluate_on_slot_change: bool             # trigger re-evaluation when model slots change
      max_re_evaluate_batch_size: int              # max canonicals to re-evaluate per Vigil run
      entity_consistency_check: bool               # verify entity consistency across canonicals
  @dataclass
  class AuthConfig:
      auth_enabled: bool                           # toggle auth on/off (per §1.8)
      session_timeout_seconds: int                 # session expiration time
      api_key_enabled: bool                        # allow API key authentication
      bcrypt_rounds: int                           # bcrypt hashing rounds for API keys
      definer_identity: str                        # the DEFINER's identity (username)
  @dataclass
  class RateLimitConfig:
      enabled: bool                                # toggle rate limiting on/off (per §1.8)
      requests_per_minute: int                     # global requests per minute limit
      burst_size: int                              # token bucket burst size
      per_endpoint_overrides: dict[str, int]       # endpoint → RPM override
      model_budget_protection: bool                # prevent rate limiting from violating budget
  @dataclass
  class CanonicalPromotionConfig:
      require_faithfulness_check: bool             # run faithfulness evaluation before promotion
      faithfulness_threshold: float                # minimum faithfulness score for promotion
      require_domain_coherence: bool               # run domain coherence before promotion
      domain_coherence_threshold: float            # minimum coherence score for promotion
      require_definer_approval: bool               # AutonomyGate check for DEFINER approval
      auto_reindex_on_promotion: bool              # re-index in VectorStore + LexicalStore after promotion
      model_gen_assumption: str | None             # §1.8 — what assumption the promotion criteria encode
  @dataclass
  class WorkflowTemplate:
      template_id: str                             # unique identifier for the workflow template
      name: str                                    # human-readable name
      description: str                             # what this workflow does
      yaml_path: str                               # path to the YAML workflow definition
      trigger: str                                 # "manual" / "on_artifact_generated" / "on_canonical_stale" / "on_cadence"
      domains: list[str]                           # which domains this workflow applies to
      model_gen_assumption: str | None             # §1.8
  @dataclass
  class DeploymentProfile:
      profile_name: str                            # "laptop-viable" / "production"
      vector_backend: VectorBackendType            # sqlite_vss or pgvector
      model_provider: str                          # "ollama" / "api"
      auth_enabled: bool                           # whether auth is enabled in this profile
      workers: int                                 # number of API workers
      memory_limit_mb: int                         # memory limit for the profile
  # Type aliases
  VigilHealthStatus = Literal["healthy", "stale", "degraded", "unknown"]
  AuthRole = Literal["definer", "readonly", "unauthenticated"]
  # Protocol amendments in foundation/protocols.py (append method stubs only — do NOT redeclare class):
  # VigilStore: new Protocol from §6 (does not exist in Phase 0/1/2/3/4/5/6)
  class VigilStore(Protocol):
      async def get_canonical_health(self, artifact_id: str) -> dict: ...
      async def list_stale_canonicals(self, threshold_days: int) -> list[dict]: ...
      async def record_vigil_check(self, canonical_count: int, stale_count: int, status: VigilHealthStatus) -> None: ...
      async def get_last_vigil_check(self) -> dict | None: ...
  # AuthStore: new Protocol
  class AuthStore(Protocol):
      async def create_session(self, identity: str, role: AuthRole) -> str: ...
      async def validate_session(self, session_token: str) -> dict | None: ...
      async def revoke_session(self, session_token: str) -> None: ...
      async def create_api_key(self, identity: str, role: AuthRole, key_name: str) -> str: ...
      async def validate_api_key(self, api_key: str) -> dict | None: ...
      async def revoke_api_key(self, key_name: str) -> None: ...
      async def list_api_keys(self) -> list[dict]: ...
TESTS:
  tests/test_phase7_schema_additions.py
GATE: uv run pytest tests/test_phase7_schema_additions.py -xvs
```

### Prose

This chunk establishes the shared data types, protocol amendments, and configuration extensions that all subsequent Phase 7 chunks depend on. It does nine things:

**1. Append `VigilConfig` dataclass to `foundation/schemas.py`.** The `VigilConfig` dataclass captures all Vigil-specific configuration: the canonical health check interval (how often Vigil scans the canonical corpus for stale entries), the stale threshold in days (how old a canonical must be before Vigil flags it), whether to trigger re-evaluation when model slots change (implementing §1.8's requirement that "on every model slot upgrade: audit the harness for stale assumptions"), the maximum batch size for re-evaluation (bounding the cost of a Vigil run, same pattern as SextonConfig), and whether to verify entity consistency across canonicals (detecting when an entity referenced by a canonical has been updated but the canonical has not). These parameters map to the `[vigil]` config section. Append only — do not modify or reorder any existing definitions.

**2. Append `AuthConfig` dataclass.** The `AuthConfig` dataclass captures authentication configuration: whether auth is enabled (toggleable per §1.8, defaulting to `False` for the laptop-viable profile where the DEFINER is the only user), session timeout, whether API keys are supported (for CLI and MCP non-interactive access), bcrypt hashing rounds for API key storage, and the DEFINER identity string. Per §1.7, the auth system must enforce DEFINER identity for all write and admin operations — the `definer_identity` field is the username that maps to the DEFINER role. Per Process Rule 13, AIP 0.1 is single-DEFINER — there is no multi-user isolation, but the Protocol-based design (`AuthStore`) enables multi-user extension in post-0.1 phases.

**3. Append `RateLimitConfig` dataclass.** The `RateLimitConfig` dataclass captures rate limiting configuration: whether rate limiting is enabled (toggleable per §1.8), the global requests-per-minute limit, the token bucket burst size, per-endpoint overrides (some endpoints like health checks should not be rate-limited), and model budget protection (when True, the rate limiter consults the BudgetManager before allowing model-consuming requests — preventing rate limiting from accidentally starving the budget system). The `per_endpoint_overrides` is a dict mapping endpoint path patterns to RPM values, allowing fine-grained control without modifying code.

**4. Append `CanonicalPromotionConfig` dataclass.** The `CanonicalPromotionConfig` dataclass captures the canonical promotion pipeline configuration: whether faithfulness and domain coherence checks are required before promotion, the threshold scores for each check, whether DEFINER approval is required (via AutonomyGate), whether to auto-re-index in VectorStore and LexicalStore after promotion, and a `model_gen_assumption` field per §1.8. The promotion criteria encode assumptions about model evaluation quality — for example, "faithfulness evaluation at 0.70 threshold assumes the evaluation model can reliably detect hallucination above this level." When model slots change, Vigil should re-audit these thresholds. Per §1.6: "Canonical artifacts require explicit approval and are stored separately" — the promotion pipeline is the gate that enforces this.

**5. Append `WorkflowTemplate` dataclass.** The `WorkflowTemplate` dataclass captures metadata about a YAML workflow template: the template ID, name, description, the YAML file path, the trigger type (manual, on_artifact_generated, on_canonical_stale, on_cadence), applicable domains, and a `model_gen_assumption` field per §1.8. This enables the admin console and CLI to list available workflows and their triggers without parsing YAML files. The `on_canonical_stale` trigger enables Vigil to automatically launch a re-evaluation workflow when it detects stale canonicals — this is the Vigil-to-workflow integration point.

**6. Append `DeploymentProfile` dataclass.** The `DeploymentProfile` dataclass captures a deployment configuration: the profile name (laptop-viable or production), the vector backend type, the model provider mode (local Ollama vs. API), whether auth is enabled, worker count, and memory limit. This maps to the Docker Compose profiles that CHUNK-9.6 creates. The `VectorBackendType` from Phase 4 is reused here.

**7. Add `VigilHealthStatus` and `AuthRole` type aliases.** `VigilHealthStatus` is a `Literal["healthy", "stale", "degraded", "unknown"]` that the Vigil actor uses to report canonical corpus health. `AuthRole` is a `Literal["definer", "readonly", "unauthenticated"]` that the auth system uses for role-based access. The DEFINER role has full access; the readonly role can read but not write; unauthenticated can access only public endpoints.

**8. Add `VigilStore` Protocol in `foundation/protocols.py`.** This is a new Protocol that abstracts the Vigil actor's persistence needs. The `get_canonical_health` method returns health metadata for a specific canonical artifact (last evaluated, model slot used, evaluation scores). The `list_stale_canonicals` method returns all canonicals older than the threshold. The `record_vigil_check` and `get_last_vigil_check` methods track when Vigil last ran and what it found. This is a separate Protocol from `CanonicalStore` — CanonicalStore manages canonical data, while VigilStore manages Vigil's health metadata. Per Appendix D: "Vigil ≠ Beast" and "Vigil ≠ Sexton" — VigilStore is a distinct persistence concern.

**9. Add `AuthStore` Protocol in `foundation/protocols.py`.** This is a new Protocol that abstracts authentication persistence. The `create_session` / `validate_session` / `revoke_session` methods manage session-based auth. The `create_api_key` / `validate_api_key` / `revoke_api_key` / `list_api_keys` methods manage API key auth. The Protocol returns dicts (not dataclasses) for flexibility — session data includes tokens, expiry, role, and identity, which may vary across implementations. This is a new Protocol, not an amendment to any existing Protocol.

**Config additions.** Phase 7 extends `config/aip.config.toml` with:

```toml
[vigil]
canonical_health_check_interval_seconds = 7200    # 2 hours
stale_threshold_days = 30
re_evaluate_on_slot_change = true
max_re_evaluate_batch_size = 20
entity_consistency_check = true

[auth]
auth_enabled = false                              # true in production profile
session_timeout_seconds = 86400                   # 24 hours
api_key_enabled = true
bcrypt_rounds = 12
definer_identity = "definer"

[rate_limit]
enabled = false                                   # true in production profile
requests_per_minute = 60
burst_size = 10
per_endpoint_overrides = {"/api/v1/health" = 600}
model_budget_protection = true

[canonical_pipeline]
require_faithfulness_check = true
faithfulness_threshold = 0.70
require_domain_coherence = true
domain_coherence_threshold = 0.60
require_definer_approval = true
auto_reindex_on_promotion = true
model_gen_assumption = "Evaluation models reliably detect faithfulness and coherence above configured thresholds"

[deployment]
profile = "laptop-viable"                         # or "production"
```

The gate test verifies: (a) all new dataclasses can be instantiated with required fields, (b) `CanonicalPromotionConfig` carries `model_gen_assumption` field per §1.8, (c) `WorkflowTemplate` carries `model_gen_assumption` field per §1.8, (d) `VigilConfig` has all health check parameters, (e) `AuthConfig` has session and API key parameters, (f) `RateLimitConfig` has RPM and burst parameters, (g) `DeploymentProfile` has profile parameters, (h) `VigilStore` Protocol has `get_canonical_health`, `list_stale_canonicals`, `record_vigil_check`, `get_last_vigil_check` methods, (i) `AuthStore` Protocol has all session and API key methods, (j) existing Phase 0/1/2/3/4/5/6 schema enums and dataclasses are not broken, (k) existing Protocol methods still exist.

### ANNEX

**`foundation/schemas.py` (append to existing file):**

```python
# --- Phase 7 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type aliases for Vigil health status
VigilHealthStatus = Literal["healthy", "stale", "degraded", "unknown"]

# Type aliases for authentication roles
AuthRole = Literal["definer", "readonly", "unauthenticated"]


@dataclass
class VigilConfig:
    """Configuration for the Vigil compiled knowledge maintenance actor.

    Per §3: Vigil — compiled knowledge maintenance.
    Per §1.8: On every model slot upgrade: audit the harness for stale assumptions.
    Per Appendix D: Vigil ≠ Beast, Vigil ≠ Sexton, Vigil is read-only.
    """
    canonical_health_check_interval_seconds: int = 7200
    stale_threshold_days: int = 30
    re_evaluate_on_slot_change: bool = True
    max_re_evaluate_batch_size: int = 20
    entity_consistency_check: bool = True


@dataclass
class AuthConfig:
    """Authentication configuration.

    Per §1.7: No UI may bypass the DEFINER gates.
    Per §1.8: auth_enabled is toggleable.
    Per Process Rule 13: AIP 0.1 is single-DEFINER.
    """
    auth_enabled: bool = False
    session_timeout_seconds: int = 86400
    api_key_enabled: bool = True
    bcrypt_rounds: int = 12
    definer_identity: str = "definer"


@dataclass
class RateLimitConfig:
    """Rate limiting configuration.

    Per §1.8: enabled is toggleable.
    Token bucket algorithm with per-endpoint overrides.
    Model budget protection prevents rate limiting from starving budget.
    """
    enabled: bool = False
    requests_per_minute: int = 60
    burst_size: int = 10
    per_endpoint_overrides: dict[str, int] = field(default_factory=dict)
    model_budget_protection: bool = True


@dataclass
class CanonicalPromotionConfig:
    """Canonical promotion pipeline configuration.

    Per §1.6: Canonical artifacts require explicit approval.
    Per §9.3: REVIEWED → APPROVED → CANONICAL lifecycle.
    Per §1.8: model_gen_assumption tags what the promotion criteria assume.
    """
    require_faithfulness_check: bool = True
    faithfulness_threshold: float = 0.70
    require_domain_coherence: bool = True
    domain_coherence_threshold: float = 0.60
    require_definer_approval: bool = True
    auto_reindex_on_promotion: bool = True
    model_gen_assumption: str | None = None


@dataclass
class WorkflowTemplate:
    """Metadata for a YAML workflow template.

    Per §11.1: L5 YAML engine supports arbitrary workflow definitions.
    Per §1.8: model_gen_assumption tags what model limitation the workflow compensates for.
    """
    template_id: str
    name: str
    description: str = ""
    yaml_path: str = ""
    trigger: str = "manual"
    domains: list[str] = field(default_factory=list)
    model_gen_assumption: str | None = None


@dataclass
class DeploymentProfile:
    """A deployment configuration profile.

    Per §2.1: laptop-viable profile must work on constrained hardware.
    Per §2.2: production profile uses PostgreSQL 16 + pgvector.
    """
    profile_name: str
    vector_backend: str = "sqlite_vss"  # VectorBackendType values
    model_provider: str = "ollama"
    auth_enabled: bool = False
    workers: int = 1
    memory_limit_mb: int = 4096
```
<!-- ESTIMATED_TOKENS: ~400 -->

**`foundation/protocols.py` (append method stubs to existing Protocol classes + add new Protocols):**

```python
# --- Phase 7 amendments (append method stubs only — do NOT redeclare existing Protocol classes) ---

# --- Phase 7 new Protocols (not amendments — these are new classes) ---


class VigilStore(Protocol):
    """Abstraction for Vigil actor persistence needs.

    Per §6: VigilStore Protocol listed as required.
    Per §3: Vigil — compiled knowledge maintenance.
    Per Appendix D: VigilStore is separate from CanonicalStore.
    """

    async def get_canonical_health(
        self, artifact_id: str
    ) -> dict:
        """Get health metadata for a specific canonical artifact.

        Returns dict with: last_evaluated, model_slot_used, faithfulness_score,
        domain_coherence_score, created_at, status.
        """
        ...

    async def list_stale_canonicals(
        self, threshold_days: int
    ) -> list[dict]:
        """List canonicals older than the threshold.

        Returns list of dicts with: artifact_id, domain, created_at,
        last_evaluated, model_slot_used, days_since_evaluation.
        Ordered by days_since_evaluation descending (stalest first).
        """
        ...

    async def record_vigil_check(
        self,
        canonical_count: int,
        stale_count: int,
        status: "VigilHealthStatus",
    ) -> None:
        """Record a Vigil health check run.

        Writes to vigil_checks table in state.db.
        Used by admin console and DEFINER review.
        """
        ...

    async def get_last_vigil_check(self) -> dict | None:
        """Get the most recent Vigil health check result.

        Returns dict with: check_time, canonical_count, stale_count,
        status, re_evaluate_count, entity_issues_found.
        """
        ...


class AuthStore(Protocol):
    """Abstraction for authentication persistence.

    Per §1.7: DEFINER sovereignty requires identity enforcement.
    Per Process Rule 13: AIP 0.1 is single-DEFINER.
    Per §1.8: auth_enabled is toggleable.
    """

    async def create_session(
        self, identity: str, role: "AuthRole"
    ) -> str:
        """Create a new authenticated session.

        Returns a session token string.
        Token expires after session_timeout_seconds in config.
        """
        ...

    async def validate_session(
        self, session_token: str
    ) -> dict | None:
        """Validate a session token.

        Returns dict with: identity, role, expires_at if valid.
        Returns None if token is invalid or expired.
        """
        ...

    async def revoke_session(
        self, session_token: str
    ) -> None:
        """Revoke a session (logout).

        Subsequent validate_session calls return None.
        """
        ...

    async def create_api_key(
        self, identity: str, role: "AuthRole", key_name: str
    ) -> str:
        """Create a new API key.

        Returns the raw API key string (shown once, then hashed).
        The raw key is never stored — only the bcrypt hash.
        """
        ...

    async def validate_api_key(
        self, api_key: str
    ) -> dict | None:
        """Validate an API key.

        Returns dict with: identity, role, key_name if valid.
        Returns None if key is invalid or revoked.
        """
        ...

    async def revoke_api_key(
        self, key_name: str
    ) -> None:
        """Revoke an API key by name.

        Subsequent validate_api_key calls return None.
        """
        ...

    async def list_api_keys(self) -> list[dict]:
        """List all API keys (without revealing the key values).

        Returns list of dicts with: key_name, identity, role, created_at, last_used_at.
        """
        ...
```
<!-- ESTIMATED_TOKENS: ~400 -->

**`tests/test_phase7_schema_additions.py`:**

```python
"""Verify Phase 7 schema additions do not break Phase 0, 1, 2, 3, 4, 5, or 6."""
import pytest

from foundation.schemas import (
    AcePlaybookEntry,
    ApiRoute,
    AuthConfig,
    AuthRole,
    AutonomyEscalation,
    AutonomyLevel,
    BeastCadenceConfig,
    BudgetConfig,
    BudgetScope,
    CanonicalPromotionConfig,
    ChatMessage,
    Chunk,
    ContractRule,
    DeploymentProfile,
    DomainCoherenceResult,
    EcsState,
    EcsTransition,
    EvaluationScore,
    Event,
    FailureClassification,
    FailureType,
    FaithfulnessResult,
    McpAutonomyLevel,
    McpToolDef,
    MigrationCheckpoint,
    MigrationStatus,
    ModelSlotConfig,
    PgvectorConfig,
    RateLimitConfig,
    RetrievalResult,
    ReviewContext,
    ReviewQueueEntry,
    ReviewVerdict,
    RoutingWeight,
    SessionContext,
    SextonConfig,
    SurfaceConfig,
    TrajectorySignal,
    VectorBackendType,
    VigilConfig,
    VigilHealthStatus,
    WorkflowTemplate,
)
from foundation.protocols import (
    ArtifactStore,
    AuthStore,
    AutonomyGate,
    BudgetStore,
    CanonicalStore,
    EmbeddingProvider,
    EcsStore,
    EntityStore,
    EventStore,
    LexicalStore,
    ModelProvider,
    ProjectStore,
    TraceStore,
    VectorStore,
    VigilStore,
)


def test_vigil_config_dataclass():
    vc = VigilConfig(
        canonical_health_check_interval_seconds=7200,
        stale_threshold_days=30,
        re_evaluate_on_slot_change=True,
        max_re_evaluate_batch_size=20,
        entity_consistency_check=True,
    )
    assert vc.stale_threshold_days == 30
    assert vc.re_evaluate_on_slot_change is True


def test_auth_config_dataclass():
    ac = AuthConfig(
        auth_enabled=False,
        session_timeout_seconds=86400,
        api_key_enabled=True,
        definer_identity="definer",
    )
    assert ac.auth_enabled is False
    assert ac.definer_identity == "definer"


def test_rate_limit_config_dataclass():
    rlc = RateLimitConfig(
        enabled=False,
        requests_per_minute=60,
        burst_size=10,
    )
    assert rlc.requests_per_minute == 60
    assert rlc.model_budget_protection is True


def test_canonical_promotion_config_carries_model_gen_assumption():
    """Per §1.8: canonical promotion criteria must carry model_gen_assumption."""
    cpc = CanonicalPromotionConfig(
        require_faithfulness_check=True,
        faithfulness_threshold=0.70,
        require_domain_coherence=True,
        domain_coherence_threshold=0.60,
        require_definer_approval=True,
        model_gen_assumption="Evaluation models reliably detect faithfulness above 0.70",
    )
    assert cpc.model_gen_assumption is not None
    assert cpc.faithfulness_threshold == 0.70


def test_workflow_template_carries_model_gen_assumption():
    """Per §1.8: every workflow template must carry model_gen_assumption."""
    wt = WorkflowTemplate(
        template_id="incremental_update_v1",
        name="Incremental Update",
        description="Update an existing artifact with new context",
        yaml_path="workflows/incremental_update_v1.yaml",
        trigger="manual",
        domains=["software_architecture"],
        model_gen_assumption="Models produce better incremental updates when given prior context",
    )
    assert wt.model_gen_assumption is not None
    assert wt.trigger == "manual"


def test_deployment_profile_dataclass():
    dp = DeploymentProfile(
        profile_name="laptop-viable",
        vector_backend="sqlite_vss",
        model_provider="ollama",
    )
    assert dp.profile_name == "laptop-viable"
    assert dp.auth_enabled is False


def test_vigil_health_status_type_alias():
    healthy: VigilHealthStatus = "healthy"
    stale: VigilHealthStatus = "stale"
    degraded: VigilHealthStatus = "degraded"
    unknown: VigilHealthStatus = "unknown"
    assert healthy == "healthy"


def test_auth_role_type_alias():
    definer: AuthRole = "definer"
    readonly: AuthRole = "readonly"
    unauth: AuthRole = "unauthenticated"
    assert definer == "definer"


def test_phase0_through_phase6_enums_still_work():
    """Phase 0/1/2/3/4/5/6 enums must not be broken by Phase 7 additions."""
    assert EcsState.GENERATED is not None
    assert FailureType.C is not None


def test_phase0_through_phase6_dataclasses_still_work():
    """Phase 0/1/2/3/4/5/6 dataclasses must not be broken by Phase 7 additions."""
    c = Chunk(id="x", content="hello", score=0.9, metadata={"k": "v"}, domain="test")
    assert c.id == "x"
    v = ReviewVerdict(artifact_id="a1", verdict="APPROVED", reviewer="definer")
    assert v.verdict == "APPROVED"
    s = TrajectorySignal(
        signal_type="loop",
        session_id="s1",
        failure_type="D",
        confidence=0.85,
        detail="Repeated pattern",
        detected_at="2026-05-28T10:00:00Z",
    )
    assert s.signal_type == "loop"
    fc = FailureClassification(
        trace_event_id=42,
        failure_type="A",
        confidence=0.92,
        model_gen_assumption="Test",
        classified_at="2026-05-28T10:00:00Z",
    )
    assert fc.failure_type == "A"
    sc = SurfaceConfig(api_host="127.0.0.1", api_port=8000)
    assert sc.api_port == 8000


def test_vigil_store_protocol_methods():
    """Phase 7: VigilStore must have required methods."""
    assert hasattr(VigilStore, "get_canonical_health"), "VigilStore missing get_canonical_health"
    assert hasattr(VigilStore, "list_stale_canonicals"), "VigilStore missing list_stale_canonicals"
    assert hasattr(VigilStore, "record_vigil_check"), "VigilStore missing record_vigil_check"
    assert hasattr(VigilStore, "get_last_vigil_check"), "VigilStore missing get_last_vigil_check"


def test_auth_store_protocol_methods():
    """Phase 7: AuthStore must have session and API key methods."""
    assert hasattr(AuthStore, "create_session"), "AuthStore missing create_session"
    assert hasattr(AuthStore, "validate_session"), "AuthStore missing validate_session"
    assert hasattr(AuthStore, "revoke_session"), "AuthStore missing revoke_session"
    assert hasattr(AuthStore, "create_api_key"), "AuthStore missing create_api_key"
    assert hasattr(AuthStore, "validate_api_key"), "AuthStore missing validate_api_key"
    assert hasattr(AuthStore, "revoke_api_key"), "AuthStore missing revoke_api_key"
    assert hasattr(AuthStore, "list_api_keys"), "AuthStore missing list_api_keys"


def test_existing_protocol_methods_preserved():
    """Phase 1/2/3/4/5/6 methods must still exist after Phase 7 amendments."""
    assert hasattr(VectorStore, "upsert"), "VectorStore missing upsert (Phase 1)"
    assert hasattr(VectorStore, "health_check"), "VectorStore missing health_check (Phase 4)"
    assert hasattr(EventStore, "write_event"), "EventStore missing write_event (Phase 1)"
    assert hasattr(EventStore, "query"), "EventStore missing query (Phase 2)"
    assert hasattr(ArtifactStore, "list_versions"), "ArtifactStore missing list_versions (Phase 2)"
    assert hasattr(EcsStore, "current_state"), "EcsStore missing current_state (Phase 2)"
    assert hasattr(TraceStore, "query_events"), "TraceStore missing query_events (Phase 3)"
    assert hasattr(ModelProvider, "call"), "ModelProvider missing call (Phase 3)"
    assert hasattr(EmbeddingProvider, "embed"), "EmbeddingProvider missing embed (Phase 3)"
    assert hasattr(BudgetStore, "check_limit"), "BudgetStore missing check_limit (Phase 5)"
    assert hasattr(ProjectStore, "list_projects"), "ProjectStore missing list_projects (Phase 5)"
    assert hasattr(AutonomyGate, "check"), "AutonomyGate missing check (Phase 6)"
    assert hasattr(LexicalStore, "search"), "LexicalStore missing search (Phase 6)"
    assert hasattr(CanonicalStore, "write_canonical"), "CanonicalStore missing write_canonical (Phase 6)"
    assert hasattr(EntityStore, "get_entity"), "EntityStore missing get_entity (Phase 6)"
```
<!-- ESTIMATED_TOKENS: ~500 -->

---

## CHUNK-9.0b: Authentication & Authorization System

```
CHUNK-9.0b: Authentication & Authorization System
PHASE: 7
DEPENDS-ON: CHUNK-9.0a, CHUNK-8.1
CODER-PROFILE: L2
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  adapter/auth/session_store.py
  adapter/auth/api_key_store.py
  adapter/auth/middleware.py
  adapter/auth/__init__.py
  adapter/auth/dependencies.py
  tests/test_auth.py
INTERFACES:
  class SqliteSessionStore(AuthStore):
      def __init__(self, db_path: str, config: AuthConfig) -> None: ...
      async def create_session(self, identity: str, role: AuthRole) -> str: ...
      async def validate_session(self, session_token: str) -> dict | None: ...
      async def revoke_session(self, session_token: str) -> None: ...
      async def create_api_key(self, identity: str, role: AuthRole, key_name: str) -> str: ...
      async def validate_api_key(self, api_key: str) -> dict | None: ...
      async def revoke_api_key(self, key_name: str) -> None: ...
      async def list_api_keys(self) -> list[dict]: ...
      async def initialize(self) -> None: ...
      async def close(self) -> None: ...
  class AuthMiddleware:
      def __init__(self, app: ASGIApp, auth_store: AuthStore, config: AuthConfig) -> None: ...
  async def get_current_identity(request: Request) -> dict: ...
  async def require_definer(request: Request) -> dict: ...
TESTS:
  tests/test_auth.py
GATE: uv run pytest tests/test_auth.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the authentication and authorization system that enforces DEFINER identity across all surfaces. Phase 6 left auth as a placeholder — all API endpoints were accessible without authentication, and DEFINER sovereignty was enforced only by the AutonomyGate (which checks autonomy levels, not identity). Phase 7 delivers the real auth system that binds identity to autonomy level, ensuring that the "requested_by" field in AutonomyEscalation is authenticated, not just claimed.

**SqliteSessionStore.** The `SqliteSessionStore` implements the `AuthStore` Protocol using SQLite. The `initialize()` method creates two tables: `sessions` (session_token TEXT PRIMARY KEY, identity TEXT, role TEXT, created_at TEXT, expires_at TEXT) and `api_keys` (key_name TEXT PRIMARY KEY, identity TEXT, role TEXT, key_hash TEXT, created_at TEXT, last_used_at TEXT, revoked INTEGER DEFAULT 0). The `create_session` method generates a cryptographically random session token using `secrets.token_urlsafe(32)`, calculates the expiry time based on `config.session_timeout_seconds`, and inserts a row into the sessions table. The `validate_session` method queries the session by token, checks that `expires_at > now()` and returns the identity and role. The `revoke_session` method deletes the session row (logout). Sessions are lightweight — no JWT complexity, just opaque tokens stored in SQLite with expiry.

**API key management.** The `create_api_key` method generates a key using `secrets.token_urlsafe(32)`, hashes it with bcrypt (using the configured number of rounds), stores the hash and metadata, and returns the raw key. The raw key is never stored — this is a one-time display, like GitHub personal access tokens. The `validate_api_key` method iterates through non-revoked keys, checks the bcrypt hash, and returns the identity and role if it matches. Bcrypt comparison is timing-safe, preventing timing attacks. The `list_api_keys` method returns metadata without hashes.

**AuthMiddleware.** The `AuthMiddleware` is a Starlette middleware that intercepts all requests to the FastAPI application. It checks for two authentication methods: (1) `Authorization: Bearer <token>` header for session tokens, (2) `X-API-Key: <key>` header for API keys. If auth is disabled (`config.auth_enabled == False`), the middleware passes all requests through with `identity="definer"` and `role="definer"` — this is the laptop-viable profile behavior where the single user is always the DEFINER. If auth is enabled, the middleware validates the credential and attaches the identity and role to `request.state.auth_identity` and `request.state.auth_role`. Unauthenticated requests to protected endpoints receive 401 responses.

**FastAPI dependencies.** The `get_current_identity` dependency extracts the authenticated identity from `request.state.auth_identity`. The `require_definer` dependency asserts that `request.state.auth_role == "definer"` and raises 403 if not. These dependencies are used by the API route handlers from Phase 6 — routes with `auth_required=True` use `require_definer`, and routes without use `get_current_identity` (which allows any authenticated user). The DI container from CHUNK-8.1 is updated to inject the `AuthStore` and `AuthConfig` into the middleware.

**When auth is disabled.** The critical design decision: when `auth_enabled == False`, every request is treated as the DEFINER. This means the laptop-viable profile works without any authentication setup — the user just runs `aip init` and starts using the system. The DEFINER sovereignty is still enforced by the AutonomyGate, which checks autonomy levels regardless of whether auth is enabled. Auth adds identity verification on top of autonomy level enforcement — it is a defense-in-depth measure, not a replacement for the gate.

The gate test verifies: (a) `SqliteSessionStore` implements `AuthStore` Protocol, (b) session creation returns a valid token, (c) session validation returns identity and role, (d) session revocation prevents subsequent validation, (e) expired sessions are rejected, (f) API key creation returns a raw key, (g) API key validation works with the raw key, (h) API key revocation prevents subsequent validation, (i) `AuthMiddleware` attaches identity to requests when auth is enabled, (j) `AuthMiddleware` passes all requests as DEFINER when auth is disabled, (k) `require_definer` raises 403 for non-DEFINER roles, (l) adapter layer does not import orchestration.

---

## CHUNK-9.0c: Rate Limiting

```
CHUNK-9.0c: Rate Limiting
PHASE: 7
DEPENDS-ON: CHUNK-9.0a, CHUNK-8.1
CODER-PROFILE: L2
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  adapter/middleware/rate_limiter.py
  adapter/middleware/__init__.py
  tests/test_rate_limiter.py
INTERFACES:
  class TokenBucketRateLimiter:
      def __init__(self, config: RateLimitConfig) -> None: ...
      def allow_request(self, key: str) -> bool: ...
      def get_remaining(self, key: str) -> int: ...
  class RateLimitMiddleware:
      def __init__(self, app: ASGIApp, rate_limiter: TokenBucketRateLimiter, config: RateLimitConfig) -> None: ...
TESTS:
  tests/test_rate_limiter.py
GATE: uv run pytest tests/test_rate_limiter.py tests/test_layering.py -xvs
```

### Prose

This chunk implements token-bucket rate limiting for the FastAPI application. The rate limiter protects against accidental overload when multiple surfaces (chat, Beast cadence, MCP calls) compete for model budget. It also protects the API from burst traffic that could exhaust the token budget before the BudgetManager detects it.

**TokenBucketRateLimiter.** The `TokenBucketRateLimiter` implements a classic token bucket algorithm. The bucket starts full at `burst_size` tokens. Tokens are replenished at a rate of `requests_per_minute / 60` tokens per second. Each request consumes one token. If the bucket is empty, the request is rejected with HTTP 429 Too Many Requests. The `key` parameter allows per-DEFINER or per-IP rate limiting — the middleware extracts the key from the authenticated identity (when auth is enabled) or the client IP (when auth is disabled). The `get_remaining` method returns the current token count for rate limit headers (`X-RateLimit-Remaining`).

**Per-endpoint overrides.** The `RateLimitConfig.per_endpoint_overrides` dict maps URL path patterns to RPM values. For example, `/api/v1/health` might have 600 RPM (health checks are cheap), while `/api/v1/chat` might have 10 RPM (chat triggers model calls that consume budget). The middleware matches the request path against the override patterns before applying the rate limit.

**Model budget protection.** When `config.model_budget_protection == True`, the rate limiter does not reject requests that would not consume model tokens (read-only requests, health checks). This prevents the rate limiter from accidentally blocking legitimate read traffic when the model budget is exhausted. The rate limiter and the BudgetManager work in concert: the rate limiter prevents burst overload, and the BudgetManager prevents budget exhaustion.

**When rate limiting is disabled.** When `config.enabled == False`, the middleware passes all requests through without checking. This is the default for the laptop-viable profile where the DEFINER is the only user. Rate limiting becomes important in the production profile or when MCP/Beast/concurrent chat sessions are active.

The gate test verifies: (a) `TokenBucketRateLimiter` correctly allows requests within limit, (b) requests are rejected when bucket is empty, (c) bucket replenishes over time, (d) per-endpoint overrides are applied, (e) disabled rate limiter passes all requests, (f) `RateLimitMiddleware` returns 429 on rate limit exceeded, (g) rate limit headers are included in responses, (h) adapter layer does not import orchestration.

---

## CHUNK-9.1: Vigil Actor

```
CHUNK-9.1: Vigil Actor
PHASE: 7
DEPENDS-ON: CHUNK-9.0a, CHUNK-9.0b, CHUNK-7.1, CHUNK-8.0b
CODER-PROFILE: L3
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  orchestration/actors/vigil.py
  adapter/vigil/sqlite_vigil_store.py
  adapter/vigil/__init__.py
  tests/test_vigil.py
INTERFACES:
  class Vigil:
      def __init__(self, config: VigilConfig, vigil_store: VigilStore, canonical_store: CanonicalStore, entity_store: EntityStore, model_provider: ModelProvider, trace_store: TraceStore, sexton: Sexton | None = None) -> None: ...
      async def check_canonical_health(self) -> dict: ...
      async def detect_stale_canonicals(self) -> list[dict]: ...
      async def detect_entity_inconsistencies(self) -> list[dict]: ...
      async def on_model_slot_change(self, slot_name: str, old_config: ModelSlotConfig, new_config: ModelSlotConfig) -> None: ...
      async def run(self) -> None: ...
  class SqliteVigilStore(VigilStore):
      def __init__(self, db_path: str) -> None: ...
      async def get_canonical_health(self, artifact_id: str) -> dict: ...
      async def list_stale_canonicals(self, threshold_days: int) -> list[dict]: ...
      async def record_vigil_check(self, canonical_count: int, stale_count: int, status: VigilHealthStatus) -> None: ...
      async def get_last_vigil_check(self) -> dict | None: ...
      async def initialize(self) -> None: ...
      async def close(self) -> None: ...
TESTS:
  tests/test_vigil.py
GATE: uv run pytest tests/test_vigil.py -xvs
```

### Prose

This chunk implements the Vigil actor — the last missing orchestration component from §3. Vigil is a read-only actor that monitors canonical corpus health, detects stale canonicals (artifacts that were promoted under a previous model slot and may no longer meet current quality thresholds), verifies entity consistency across canonicals, and triggers re-evaluation when model slots change. Per Appendix D: "Vigil ≠ Beast" (Beast maintains vectors, Vigil maintains knowledge) and "Vigil ≠ Sexton" (Sexton classifies failures, Vigil monitors canonicals). And per Process Rule 12: Vigil is read-only — it never modifies canonical artifacts directly.

**Vigil class.** The `Vigil` class is an orchestration actor that composes multiple Protocol instances. The `check_canonical_health` method: (1) queries the VigilStore for all canonical artifacts, (2) checks each canonical's last evaluation date against `config.stale_threshold_days`, (3) checks each canonical's model slot against the current model configuration, (4) returns a dict with `total_count`, `stale_count`, `healthy_count`, `degraded_count`, and overall `status` (VigilHealthStatus). The `detect_stale_canonicals` method returns a detailed list of stale canonicals with their artifact IDs, domains, evaluation dates, and model slots used. The `detect_entity_inconsistencies` method queries the EntityStore for all entities referenced by canonicals and checks whether any entity has been updated since the canonical was created — if so, the canonical may be inconsistent with its source entities.

**Model slot change handler.** The `on_model_slot_change` method is called by the admin console or configuration system when a model slot is updated (e.g., upgrading from `qwen3-coder:14b` to `qwen3-coder:32b`). Per §1.8: "On every model slot upgrade: audit the harness for stale assumptions." This method: (1) queries the VigilStore for canonicals that were evaluated using the old model slot, (2) if `config.re_evaluate_on_slot_change` is True, creates trace events for each stale canonical (triggering Sexton to re-classify if failures are found), (3) if the batch exceeds `config.max_re_evaluate_batch_size`, logs a warning that some canonicals may need manual re-evaluation. This is the automated implementation of §1.8's audit requirement — previously, this was a manual step that the DEFINER had to remember.

**Vigil run.** The `run` method is called on cadence (by Beast or a cron scheduler). It: (1) calls `check_canonical_health`, (2) if entity consistency check is enabled, calls `detect_entity_inconsistencies`, (3) records the check result in the VigilStore, (4) if stale canonicals are found, creates trace events that Sexton can read and classify, (5) optionally triggers a re-evaluation workflow (if a `on_canonical_stale` trigger workflow exists). The Vigil run is bounded in cost by `max_re_evaluate_batch_size` — it never triggers more than this many re-evaluations per run.

**SqliteVigilStore.** The `SqliteVigilStore` implements the `VigilStore` Protocol using SQLite. The `initialize()` method creates two tables: `canonical_health` (artifact_id TEXT PRIMARY KEY, last_evaluated TEXT, model_slot_used TEXT, faithfulness_score REAL, domain_coherence_score REAL, created_at TEXT, status TEXT) and `vigil_checks` (check_id INTEGER PRIMARY KEY AUTOINCREMENT, check_time TEXT, canonical_count INTEGER, stale_count INTEGER, status TEXT, re_evaluate_count INTEGER, entity_issues_found INTEGER). The health table is populated by the canonical promotion pipeline (CHUNK-9.2) when artifacts are promoted to canonical — each promotion writes the evaluation metadata that Vigil later reads.

**Interaction with Sexton.** Vigil and Sexton are complementary but distinct. Sexton reads trace_events to classify failures (Types A–F). Vigil reads canonical health metadata to detect staleness. When Vigil detects a stale canonical, it creates a trace event with `node_type="vigil"` and `failure_type="A"` (Missing Context — the canonical was evaluated under an outdated model and may be missing quality that the current model would detect). Sexton then reads this trace event and classifies it. This separation ensures Vigil does not duplicate Sexton's classification logic — Vigil reports, Sexton classifies.

The gate test verifies: (a) `Vigil` detects stale canonicals based on threshold, (b) `Vigil` detects entity inconsistencies, (c) `Vigil` triggers re-evaluation on model slot change, (d) `Vigil` records health checks in VigilStore, (e) `Vigil` is read-only — it never modifies canonical artifacts, (f) `SqliteVigilStore` implements VigilStore Protocol, (g) Vigil respects batch size limits, (h) Vigil creates trace events for stale canonicals that Sexton can read, (i) orchestration layer imports follow boundary rules.

---

## CHUNK-9.2: Canonical Promotion Pipeline

```
CHUNK-9.2: Canonical Promotion Pipeline
PHASE: 7
DEPENDS-ON: CHUNK-9.0a, CHUNK-9.0b, CHUNK-8.4, CHUNK-6.2
CODER-PROFILE: L3
CONTEXT-BUDGET: ~6,000 tokens
FILES:
  orchestration/canonical_pipeline.py
  tests/test_canonical_pipeline.py
INTERFACES:
  class CanonicalPipeline:
      def __init__(self, config: CanonicalPromotionConfig, autonomy_gate: AutonomyGate, canonical_store: CanonicalStore, artifact_store: ArtifactStore, ecs_store: EcsStore, event_store: EventStore, vector_store: VectorStore, lexical_store: LexicalStore, model_provider: ModelProvider, embedding_provider: EmbeddingProvider, vigil_store: VigilStore) -> None: ...
      async def evaluate_for_promotion(self, artifact_id: str) -> dict: ...
      async def promote_to_canonical(self, artifact_id: str, approved_by: str) -> dict: ...
      async def reject_promotion(self, artifact_id: str, reason: str) -> dict: ...
      async def list_promotion_candidates(self) -> list[dict]: ...
      async def get_promotion_status(self, artifact_id: str) -> dict: ...
TESTS:
  tests/test_canonical_pipeline.py
GATE: uv run pytest tests/test_canonical_pipeline.py -xvs
```

### Prose

This chunk implements the canonical promotion pipeline that drives the REVIEWED→APPROVED→CANONICAL lifecycle end-to-end. Phase 6 delivered the CanonicalStore adapter and the AutonomyGate, but no orchestration component actually used them to promote artifacts. The review queue surface (CHUNK-8.4) could display REVIEWED artifacts and accept DEFINER approval, but the backend logic that translates "DEFINER approved this artifact" into "canonical promotion with evaluation, indexing, and health recording" did not exist. Phase 7 delivers this pipeline.

**CanonicalPipeline class.** The `CanonicalPipeline` is an orchestration component that composes multiple Protocol instances. It is the single entry point for promoting an artifact from REVIEWED to CANONICAL. The pipeline enforces the following sequence: (1) verify the artifact is in REVIEWED state via EcsStore.current_state, (2) run faithfulness evaluation if `config.require_faithfulness_check` is True, (3) run domain coherence evaluation if `config.require_domain_coherence` is True, (4) check evaluation scores against thresholds, (5) request DEFINER approval via AutonomyGate.escalate if `config.require_definer_approval` is True, (6) write the canonical to CanonicalStore.write_canonical, (7) transition the ECS state from REVIEWED to APPROVED, (8) re-index the artifact in VectorStore and LexicalStore if `config.auto_reindex_on_promotion` is True, (9) write canonical health metadata to VigilStore for future Vigil checks, (10) record the promotion event in EventStore.

**evaluate_for_promotion.** The `evaluate_for_promotion` method runs steps 1–4 without performing the promotion. It returns a dict with the artifact ID, current ECS state, faithfulness score (if checked), domain coherence score (if checked), whether the artifact passes the thresholds, and whether DEFINER approval would be required. This is a read-only evaluation that the review queue surface can use to show the DEFINER the promotion readiness before they click "Approve."

**promote_to_canonical.** The `promote_to_canonical` method runs the full pipeline (steps 1–10). The `approved_by` parameter must be `"definer"` — the AutonomyGate enforces this at the autonomy level, and the pipeline also checks it directly as a belt-and-suspenders defense. If any step fails, the promotion is aborted and a trace event is recorded. The pipeline is transactional in the sense that if the promotion fails at step 8 (re-indexing), the canonical record is still written but a warning trace event is logged so that Beast or Vigil can retry the indexing later. Per §1.6: "Canonical artifacts require explicit approval and are stored separately from the original generated artifact" — the canonical store is distinct from the artifact store, and promotion is an explicit act, not an automatic one.

**reject_promotion.** The `reject_promotion` method records a promotion rejection with a reason. This does not change the ECS state — the artifact remains in REVIEWED. The DEFINER can request re-synthesis or manual edits before re-attempting promotion.

**list_promotion_candidates.** The `list_promotion_candidates` method returns all artifacts in REVIEWED state that are eligible for promotion. This is a convenience method for the review queue surface.

**get_promotion_status.** The `get_promotion_status` method returns the current promotion readiness for a specific artifact — it runs the evaluation checks and returns the result without performing any state changes.

**Integration with Vigil.** Step 9 writes health metadata to the VigilStore. This includes the artifact ID, the evaluation scores, the model slot used for evaluation, and the timestamp. When Vigil runs its health checks (CHUNK-9.1), it reads this metadata to detect staleness. Without this step, Vigil would have no baseline to compare against.

The gate test verifies: (a) `CanonicalPipeline.evaluate_for_promotion` runs faithfulness and coherence checks, (b) artifacts below threshold are rejected for promotion, (c) `promote_to_canonical` writes to CanonicalStore, (d) `promote_to_canonical` transitions ECS state, (e) `promote_to_canonical` re-indexes in VectorStore and LexicalStore, (f) `promote_to_canotional` records health in VigilStore, (g) `promote_to_canonical` requires DEFINER approval, (h) `reject_promotion` does not change ECS state, (i) `list_promotion_candidates` returns REVIEWED artifacts, (j) pipeline is idempotent — promoting an already-canonical artifact is a no-op, (k) evaluation scores carry `model_gen_assumption` per §1.8.

---

## CHUNK-9.3: Extended Workflow Templates

```
CHUNK-9.3: Extended Workflow Templates
PHASE: 7
DEPENDS-ON: CHUNK-9.0a, CHUNK-9.0b, CHUNK-4.5
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  workflows/incremental_update_v1.yaml
  workflows/adversarial_redteam_v1.yaml
  workflows/corpus_maintenance_v1.yaml
  orchestration/workflow_registry.py
  tests/test_extended_workflows.py
INTERFACES:
  class WorkflowRegistry:
      def __init__(self, workflows_dir: str) -> None: ...
      def list_templates(self) -> list[WorkflowTemplate]: ...
      def get_template(self, template_id: str) -> WorkflowTemplate | None: ...
      def load_workflow(self, template_id: str) -> dict: ...
TESTS:
  tests/test_extended_workflows.py
GATE: uv run pytest tests/test_extended_workflows.py -xvs
```

### Prose

This chunk delivers additional YAML workflow templates beyond Workflow 0.1 and a `WorkflowRegistry` that makes them discoverable. Phase 2 delivered only `synthesis_session_v1.yaml`. Phase 7 adds three more workflows that exercise the full ECS lifecycle and the new canonical pipeline.

**Incremental update workflow** (`incremental_update_v1.yaml`). This workflow updates an existing artifact with new context. It: (1) retrieves the existing artifact and its context, (2) retrieves new context that may be relevant, (3) synthesizes an updated version that incorporates the new information while preserving the structure and intent of the original, (4) runs structural validation, (5) runs faithfulness and domain coherence evaluation, (6) presents the update for DEFINER review. This is the workflow that Vigil would trigger when it detects a stale canonical — instead of re-generating from scratch, it incrementally updates the existing canonical with current knowledge.

**Adversarial red-team workflow** (`adversarial_redteam_v1.yaml`). This workflow runs an adversarial evaluation against an existing canonical artifact. It: (1) loads the canonical artifact, (2) generates adversarial challenges using the evaluation model slot, (3) scores the canonical's resilience to each challenge, (4) flags any challenges that reveal weaknesses, (5) presents the results for DEFINER review. This workflow is useful when Vigil or Sexton suggests that a canonical may be degraded — the red-team workflow provides a deeper evaluation than the standard faithfulness/coherence checks.

**Corpus maintenance workflow** (`corpus_maintenance_v1.yaml`). This workflow performs comprehensive corpus maintenance. It: (1) scans all artifacts for orphaned or superseded entries, (2) verifies that all canonical artifacts have corresponding VectorStore and LexicalStore entries, (3) re-indexes any missing entries, (4) removes stale FTS5 documents, (5) records a maintenance report. This workflow is a more thorough version of Beast's cadence maintenance — it can be triggered manually or on a weekly cadence.

**WorkflowRegistry.** The `WorkflowRegistry` class scans the `workflows/` directory and reads metadata from each YAML file's frontmatter (or from a companion `workflows/registry.toml` config). It provides `list_templates` (returning `WorkflowTemplate` dataclasses), `get_template` (returning a specific template by ID), and `load_workflow` (reading the YAML file and returning the parsed workflow dict). The registry is used by the admin console to display available workflows and by the CLI's `aip workflow list` command.

**Workflow YAML structure.** Each workflow YAML file includes a frontmatter section with metadata that maps to `WorkflowTemplate` fields: `template_id`, `name`, `description`, `trigger`, `domains`, and `model_gen_assumption`. The body defines the node graph using the same format as `synthesis_session_v1.yaml` from Phase 2. The YAML engine (CHUNK-4.5) loads and executes these templates without modification — the new workflows are pure configuration, not code changes.

The gate test verifies: (a) all three new workflow YAML files are valid and loadable by the YAML engine, (b) `WorkflowRegistry.list_templates` returns all four workflows (0.1 + three new), (c) `WorkflowRegistry.get_template` returns the correct template by ID, (d) each workflow's trigger is correctly specified, (e) each workflow carries `model_gen_assumption` per §1.8, (f) the incremental update workflow can be executed with a fixture artifact, (g) the corpus maintenance workflow detects orphaned entries.

---

## CHUNK-9.4: Web UI Scaffold

```
CHUNK-9.4: Web UI Scaffold
PHASE: 7
DEPENDS-ON: CHUNK-9.0b, CHUNK-9.0c, CHUNK-8.1
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  adapter/api/static/css/main.css
  adapter/api/static/js/app.js
  adapter/api/templates/dashboard.html
  adapter/api/templates/review.html
  adapter/api/templates/chat.html
  adapter/api/templates/admin.html
  adapter/api/routes/web.py
  tests/test_web_ui.py
INTERFACES:
  # Routes added to FastAPI app
  GET  /                          → dashboard
  GET  /review                    → review queue
  GET  /chat                      → chat interface
  GET  /admin                     → admin console
  # HTMX partial endpoints
  GET  /partials/projects         → project list fragment
  GET  /partials/review-queue     → review queue fragment
  GET  /partials/budget-status    → budget status fragment
  GET  /partials/vigil-status     → Vigil health fragment
TESTS:
  tests/test_web_ui.py
GATE: uv run pytest tests/test_web_ui.py -xvs
```

### Prose

This chunk delivers a minimal web UI scaffold served by the FastAPI application. The UI uses HTML + HTMX (a lightweight JavaScript library that allows AJAX requests directly from HTML attributes) to consume the Phase 6 REST API. This is NOT a full React/Next.js SPA — it is a server-rendered UI that proves the REST API is complete and usable. The HTMX approach means every interaction is a standard HTTP request to the existing API endpoints, with HTMX swapping the response HTML into the page. No JavaScript framework is needed.

**Dashboard** (`/`). The dashboard shows: project overview with artifact counts by ECS state, recent events (from EventStore), budget status (from BudgetManager), Vigil health status (from VigilStore), and model slot configuration. The dashboard auto-refreshes every 30 seconds using HTMX polling (`hx-get="/partials/dashboard" hx-trigger="every 30s"`).

**Review queue** (`/review`). The review queue shows all artifacts in REVIEWED state, with their evaluation scores, domain, and project. Each entry has an "Approve" and "Reject" button that calls the `/api/v1/artifacts/{id}/approve` and `/api/v1/artifacts/{id}/reject` endpoints. The approve button goes through the AutonomyGate. The review queue is the primary DEFINER interaction point for canonical promotion — it surfaces the information that the DEFINER needs to make approval decisions.

**Chat interface** (`/chat`). The chat interface provides a text input and a message history area. Messages are sent via HTMX to the `/api/v1/chat/{session_id}/message` endpoint, and the response is appended to the message area. This is a simpler interface than the WebSocket chat (CHUNK-8.3) — it uses HTTP POST for each message, which is easier to implement in HTMX. The WebSocket chat remains available for real-time streaming.

**Admin console** (`/admin`). The admin console shows: Vigil health status, Sexton audit results, Beast cadence status, Adaptive Router weights, budget configuration, and workflow registry. Each section is an HTMX partial that calls the corresponding admin API endpoint.

**Static files.** The CSS and JS are minimal. `main.css` provides a clean, readable layout with CSS custom properties for theming. `app.js` provides minimal JavaScript for features that HTMX cannot handle (e.g., WebSocket connections for chat streaming, copy-to-clipboard for API keys). The total JavaScript footprint is under 5KB uncompressed.

**Why HTMX, not React?** Per Phase 6's out-of-scope note: "a future web UI consumes the same REST API." The HTMX scaffold proves the REST API is complete. A full React SPA is a post-0.1 deliverable that would require a separate build system (npm, webpack), a separate deployment concern (CDN, SSR), and a separate technology stack that is not part of the AIP Python codebase. HTMX keeps the UI in the Python world and proves the API works.

The gate test verifies: (a) all four pages return 200 with valid HTML, (b) HTMX partial endpoints return HTML fragments, (c) review approve/reject buttons call the correct API endpoints, (d) chat message submission works, (e) admin console displays Vigil/Sexton/Beast status, (f) dashboard auto-refresh is configured, (g) static files (CSS/JS) are served correctly, (h) auth middleware protects web UI routes when enabled.

---

## CHUNK-9.5: Full Acceptance Test

```
CHUNK-9.5: Full Acceptance Test
PHASE: 7
DEPENDS-ON: CHUNK-9.1, CHUNK-9.2, CHUNK-9.3, CHUNK-9.4, CHUNK-8.7
CODER-PROFILE: L3
CONTEXT-BUDGET: ~8,000 tokens
FILES:
  tests/acceptance/test_acceptance_gates.py
  tests/acceptance/test_ecs_lifecycle.py
  tests/acceptance/test_definer_sovereignty.py
  tests/acceptance/test_budget_enforcement.py
  tests/acceptance/test_vigil_health.py
  tests/acceptance/test_multi_surface_isolation.py
  tests/acceptance/test_canonical_pipeline_e2e.py
TESTS:
  tests/acceptance/test_acceptance_gates.py
  tests/acceptance/test_ecs_lifecycle.py
  tests/acceptance/test_definer_sovereignty.py
  tests/acceptance/test_budget_enforcement.py
  tests/acceptance/test_vigil_health.py
  tests/acceptance/test_multi_surface_isolation.py
  tests/acceptance/test_canonical_pipeline_e2e.py
GATE: uv run pytest tests/acceptance/ -xvs
```

### Prose

This chunk delivers the full §22 acceptance verification that confirms AIP 0.1 meets all acceptance criteria. Phases 1–6 each had their own integration tests that verified the specific phase's deliverables, but no phase has verified the entire system end-to-end. Phase 7 is the verification phase — it proves that all six prior phases work together as a coherent system.

**test_acceptance_gates.** This test file verifies the §22 acceptance gates explicitly: (1) Sexton can read trace_events filtered by failure_type IS NOT NULL and produce ACE playbook entries, and no failure remains permanently unclassified (gate [33]), (2) structural validation nodes consume zero tokens (budget enforcement, gate [34]), (3) all ECS transitions in VALID_TRANSITIONS are reachable through the workflow engine, (4) the AutonomyGate blocks all autonomy violations, (5) Vigil detects stale canonicals and creates trace events, (6) all Protocol implementations pass the Protocol compliance tests.

**test_ecs_lifecycle.** This test exercises the complete artifact lifecycle: SPECIFIED → GENERATED → REVIEWED → APPROVED → CANONICAL → SUPERSEDED. It verifies: (1) each transition is valid per the ECS graph, (2) invalid transitions raise InvalidTransitionError, (3) the complete lifecycle can be driven through the REST API, CLI, and chat surface, (4) events are recorded at each transition, (5) the canonical pipeline promotes REVIEWED → CANONICAL correctly, (6) supersession works correctly.

**test_definer_sovereignty.** This test verifies §1.7's DEFINER sovereignty requirement across all surfaces: (1) no API endpoint can bypass the AutonomyGate for admin-level actions, (2) no CLI command can bypass the AutonomyGate, (3) no MCP tool can bypass the AutonomyGate, (4) no workflow can bypass the AutonomyGate, (5) no Beast cadence can bypass the AutonomyGate, (6) the auth system enforces DEFINER identity for write/admin operations, (7) unauthenticated or readonly roles are rejected for admin operations.

**test_budget_enforcement.** This test verifies budget enforcement across the system: (1) model calls respect the session, project, and daily limits, (2) budget_hard_stop=True blocks model calls when the limit is reached, (3) budget_hard_stop=False allows over-budget calls with warnings, (4) structural validation consumes zero tokens, (5) the rate limiter respects model budget protection, (6) budget status is accurately reported through the API, CLI, and admin console.

**test_vigil_health.** This test verifies Vigil's canonical health monitoring: (1) Vigil detects stale canonicals after the threshold, (2) Vigil detects entity inconsistencies, (3) Vigil triggers re-evaluation on model slot change, (4) Vigil's health check is recorded in VigilStore, (5) Vigil's stale canonical reports create trace events that Sexton can read, (6) Vigil does not modify canonical artifacts (read-only verification).

**test_multi_surface_isolation.** This test verifies that concurrent surface access does not cause data corruption or state inconsistency: (1) concurrent chat and review queue access, (2) concurrent MCP and CLI access, (3) Beast cadence running concurrently with chat, (4) budget enforcement under concurrent access, (5) rate limiting under concurrent burst traffic, (6) SQLite WAL mode handles concurrent writes correctly.

**test_canonical_pipeline_e2e.** This test exercises the full canonical promotion pipeline end-to-end: (1) create a project, (2) generate an artifact, (3) run the workflow to produce a REVIEWED artifact, (4) evaluate the artifact for promotion, (5) approve the promotion through the DEFINER gate, (6) verify the canonical is indexed in VectorStore and LexicalStore, (7) verify the canonical health is recorded in VigilStore, (8) verify Vigil detects the canonical as healthy, (9) trigger a model slot change, (10) verify Vigil detects the canonical as potentially stale, (11) run the incremental update workflow to update the canonical.

The gate test runs all acceptance tests in sequence. If any test fails, the entire gate fails — AIP 0.1 is not accepted until all §22 criteria are met. The acceptance tests use the FastAPI TestClient and Click CliRunner, with all model calls in CI mode (deterministic fixtures, no network). The tests are designed to be run in CI and to produce clear, actionable failure messages.

---

## CHUNK-9.6: Production Packaging

```
CHUNK-9.6: Production Packaging
PHASE: 7
DEPENDS-ON: CHUNK-9.5
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  deploy/Dockerfile
  deploy/docker-compose.yml
  deploy/docker-compose.laptop.yml
  deploy/docker-compose.production.yml
  deploy/configs/aip.config.laptop.toml
  deploy/configs/aip.config.production.toml
  deploy/scripts/backup.sh
  deploy/scripts/restore.sh
  deploy/scripts/health-check.sh
  tests/test_deployment.py
INTERFACES:
  # Docker Compose services
  aip-api:       FastAPI application
  aip-ollama:    Ollama model server (laptop profile)
  aip-postgres:  PostgreSQL 16 + pgvector (production profile)
  aip-cli:       CLI entry point
TESTS:
  tests/test_deployment.py
GATE: uv run pytest tests/test_deployment.py -xvs
```

### Prose

This chunk delivers the production packaging that makes AIP 0.1 installable and runnable. The core deliverable is a set of Docker Compose configurations for two deployment profiles: laptop-viable (single machine, sqlite_vss, Ollama, no auth) and production (PostgreSQL + pgvector, API models, auth enabled). The packaging is the deployment story that makes AIP 0.1 accessible to someone other than the DEFINER running Python directly.

**Dockerfile.** The Dockerfile uses a multi-stage build: (1) a builder stage that installs Python dependencies via `uv sync`, (2) a runtime stage that copies the built application and runs `uv run aip api` to start the FastAPI server. The image is based on `python:3.12-slim` for small size. Health check is configured as `curl -f http://localhost:8000/api/v1/health || exit 1`.

**docker-compose.yml (base).** The base compose file defines shared configuration: the `aip-api` service with the FastAPI application, volume mounts for `db/` and `config/`, and environment variables for profile selection. It does not define Ollama or PostgreSQL — those are in the profile-specific overrides.

**docker-compose.laptop.yml.** The laptop-viable profile adds: (1) an `aip-ollama` service running `ollama/ollama` with volume mount for model storage, (2) a configuration override that sets `vector_backend.provider = "sqlite_vss"`, `auth.auth_enabled = false`, `models.synthesis.provider = "ollama"`, and `models.synthesis.base_url = "http://aip-ollama:11434"`. The laptop profile is designed to run on a single machine with at least 8GB RAM (4GB for Ollama + 4GB for AIP + system overhead).

**docker-compose.production.yml.** The production profile adds: (1) an `aip-postgres` service running `pgvector/pgvector:pg16` with volume mount for data persistence, (2) a configuration override that sets `vector_backend.provider = "pgvector"`, `vector_backend.connection_string = "postgresql://aip:aip@aip-postgres:5432/aip"`, `auth.auth_enabled = true`, `rate_limit.enabled = true`, and model provider endpoints pointing to external API services. The production profile assumes an external Ollama or API model service is available.

**Backup and restore.** The `backup.sh` script: (1) stops the API service, (2) copies the `db/` directory, (3) dumps the PostgreSQL database (if using pgvector), (4) creates a timestamped tar archive, (5) restarts the API service. The `restore.sh` script reverses the process. These scripts ensure the DEFINER can recover from data loss.

**Health check script.** The `health-check.sh` script calls the `/api/v1/health` endpoint and reports the status of all subsystems: VectorStore, EcsStore, EventStore, BudgetStore, and Vigil health. It can be used as a Docker health check or a cron-monitored script.

**Test strategy.** The gate test verifies: (a) the Dockerfile builds without errors (syntax check only — no real Docker build in CI), (b) the docker-compose files are valid YAML with correct service definitions, (c) the configuration files are valid TOML, (d) the backup script runs successfully with fixture data, (e) the restore script recovers from backup, (f) the health check script parses the API response correctly, (g) the deployment profiles have the correct settings for their target environment. Docker builds are not run in CI — the test verifies file syntax and structure, not runtime behavior.

---

## CHUNK-9.7: Cross-Cutting Gates

```
CHUNK-9.7: Cross-Cutting Gates
PHASE: 7
DEPENDS-ON: CHUNK-9.5, CHUNK-9.6
CODER-PROFILE: L1
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  tests/test_phase7_network_isolation.py
  tests/test_phase7_model_name_gate.py
  tests/test_phase7_import_boundaries.py
  tests/test_phase7_definer_sovereignty.py
  tests/test_phase7_appendix_d.py
  tests/test_phase7_auth_bypass.py
TESTS:
  tests/test_phase7_network_isolation.py
  tests/test_phase7_model_name_gate.py
  tests/test_phase7_import_boundaries.py
  tests/test_phase7_definer_sovereignty.py
  tests/test_phase7_appendix_d.py
  tests/test_phase7_auth_bypass.py
GATE: uv run pytest tests/test_phase7_*.py -xvs
```

### Prose

This chunk delivers the Phase 7 cross-cutting gate tests that extend the Phase 6 gate (CHUNK-8.8) to cover all Phase 7 components. These gates are the final verification that the complete AIP 0.1 system respects the architectural invariants that have been enforced throughout the build.

**Network isolation.** Verifies that no Phase 7 component makes network calls in CI mode: (1) Vigil runs without network access, (2) Auth system runs without network access, (3) Rate limiter runs without network access, (4) Canonical pipeline runs without network access, (5) Web UI serves static files without network access, (6) Deployment scripts do not make network calls in check mode.

**Model name gate.** Verifies that no hardcoded model names appear in any `orchestration/` or `foundation/` file added in Phase 7: (1) Vigil references model slots by name only, (2) Canonical pipeline references model slots by name only, (3) Extended workflows reference model slots by name only, (4) Deployment configurations reference model names only in the `[models.<slot>]` config sections (which is the correct place for them).

**Import boundaries.** Verifies that Phase 7 code respects the three-layer import boundaries: (1) `adapter/auth/` does not import orchestration, (2) `adapter/middleware/` does not import orchestration, (3) `adapter/vigil/` does not import orchestration, (4) `orchestration/actors/vigil.py` does not import adapter implementations directly, (5) `orchestration/canonical_pipeline.py` does not import adapter implementations directly, (6) `adapter/api/routes/web.py` does not import orchestration.

**DEFINER sovereignty.** Verifies that Phase 7 components enforce DEFINER sovereignty: (1) Canonical pipeline requires DEFINER approval for promotion, (2) Vigil is read-only — it cannot modify canonical artifacts, (3) Auth system enforces DEFINER identity for write operations, (4) Rate limiter does not bypass AutonomyGate, (5) Web UI approve/reject buttons go through AutonomyGate.

**Appendix D constraints.** Verifies Phase 7 compliance with Appendix D: (1) "Vigil ≠ Beast" — Vigil and Beast are separate actors with distinct responsibilities, (2) "Vigil ≠ Sexton" — Vigil and Sexton are separate actors with distinct responsibilities, (3) "UI ≠ authority" — the web UI relays DEFINER decisions but does not make autonomous decisions, (4) "MCP ≠ bypass" — MCP tools still go through Protocol layer (no change from Phase 6), (5) "Supersession ≠ deletion" — canonical promotion preserves the original artifact, (6) "Entity store ≠ project store" — they remain separate.

**Auth bypass prevention.** Verifies that the auth system cannot be bypassed: (1) modifying request.state.auth_identity directly does not grant access, (2) expired session tokens are rejected, (3) revoked API keys are rejected, (4) role escalation is not possible through the API, (5) the DEFINER role cannot be assigned through the API (only through direct database access or config), (6) auth-disabled mode only works when `auth_enabled == False` in config — changing the config requires restart.

The gate test runs all six test files. If any gate fails, the Phase 7 build is blocked. These gates are the final check before AIP 0.1 is declared complete.
