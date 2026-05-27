# AIP 0.1 Phase 8 BuildSpec

**Product:** AI Poiesis (AIP) version 0.1
**Architecture Revision:** 5.2
**Build Phase:** 8 — Release Hardening: Knowledge Compilation, Plugin Architecture, Collaborator Access, Performance & Final Release
**Spec Revision:** 1.1
**Date:** May 2026
**Status:** Build Specification — for Grok Build execution
**Supersedes:** N/A (initial Phase 8 spec)
**DEFINER:** Moses Jorgensen

> **File Layout & Import Conventions.** All file paths in this spec are shown relative to the `src/aip/` package root. The actual filesystem layout is `src/aip/{foundation,orchestration,adapter}/…`; imports are absolute from the `aip` package root (e.g., `from aip.foundation.schemas import Chunk`, `from aip.adapter.vigil.sqlite_vigil_store import SqliteVigilStore`). The layering test (`tests/test_layering.py`) enforces these conventions at the AST level. When the spec writes `foundation/protocols.py`, it means `src/aip/foundation/protocols.py`. When the spec writes `adapter/knowledge/sqlite_knowledge_store.py`, it means `src/aip/adapter/knowledge/sqlite_knowledge_store.py`. Example code in the ANNEX uses the `aip.`-prefixed import form that the repo requires.

---

## Revision Log

### 1.0 — Initial

| Item | Decision | Rationale |
|---|---|---|
| Knowledge compilation | `orchestration/compilation.py` — compile raw corpus knowledge into structured, indexed, retrievable knowledge artifacts; implements the "Deferred Compiled Knowledge Layer" from §3 persistence | §3: "Deferred Compiled Knowledge Layer" is a named persistence concern that has never been implemented in Phases 1–7; Appendix D: "Deferred compiled knowledge reservation ≠ implemented Wiki/Codex/Vigil"; Phase 7's Vigil is a health monitoring actor, not a knowledge compilation engine; the architecture explicitly reserves this layer; Phase 8 delivers it |
| KnowledgeStore Protocol + adapter | `foundation/protocols.py` — `KnowledgeStore` Protocol; `adapter/knowledge/sqlite_knowledge_store.py` — `SqliteKnowledgeStore` | §6: the architecture lists storage abstraction contracts but KnowledgeStore was never defined; the Deferred Compiled Knowledge Layer requires its own Protocol-abstracted store; the store holds compiled knowledge artifacts, their provenance, compilation metadata, and cross-references to source canonicals |
| Plugin architecture | `orchestration/plugins.py` + `adapter/plugins/` — extensible model provider plugin system, plugin discovery, plugin configuration, plugin lifecycle | §4.1: "no hardcoded model names in application code" and "every model reference resolves through configuration"; the four named slots (synthesis/evaluation/sexton/embedding) are the 0.1 default; Phase 8 delivers the plugin framework that allows adding custom model providers without code changes; this is the natural extension of the Protocol-based ModelProvider abstraction from Phase 3 |
| Collaborator access | `adapter/auth/collaborator.py` — extend Phase 7 auth to support read-only and limited-write collaborators alongside the DEFINER | §1.7: "No UI may bypass the DEFINER gates" — collaborators never bypass DEFINER sovereignty; Phase 7's AuthConfig is single-DEFINER; Phase 8 extends the AuthStore Protocol and SqliteSessionStore to support multiple identities with role-based access (definer, collaborator, readonly); not full multi-tenant isolation (post-0.1) but the auth framework for AIP 0.1 to support more than one user |
| Performance optimization | `orchestration/perf.py` — critical path profiling, retrieval tuning, VectorStore query optimization, memory footprint analysis | §2.1: "AIP 0.1 must run on ordinary laptops, including machines with 4–6 GB RAM"; no phase has benchmarked the actual memory and performance footprint; Phase 7 delivers rate limiting but no optimization; Phase 8 delivers baseline performance metrics, optimization of the critical path (retrieve → synthesize → validate), and verification that the laptop-viable promise is met |
| Stabilization & edge cases | Cross-cutting fixes for issues revealed by Phase 7 acceptance tests, plus edge case handling, error recovery, and data migration safety | Phase 7's CHUNK-9.5 runs full acceptance tests against the entire system; real-world edge cases will surface; Phase 8 addresses them systematically; this includes SQLite concurrency handling, graceful degradation under memory pressure, and recovery from interrupted workflows |
| Documentation & release preparation | `docs/` — README, developer guide, API reference, deployment guide, architecture walkthrough, configuration reference | §2.3: "uv sync; uv run aip init; uv run aip status" — this is the first user experience; no phase has delivered comprehensive documentation; Phase 8 delivers the documentation that makes AIP 0.1 accessible to alpha testers and new developers |
| Final release verification | `tests/release/` — end-to-end release verification with knowledge compilation, plugin, collaborator, and performance scenarios; final §22 gate confirmation | Phase 7's acceptance tests verified the base system; Phase 8 adds verification for new Phase 8 features and re-verifies that Phase 8 changes did not regress any Phase 1–7 acceptance criteria; the release verification is the final gate before AIP 0.1 is declared "released" |

### 1.1 — Import Audit Delta

| Item | Decision | Rationale |
|---|---|---|
| File Layout & Import Conventions note | Added prominent blockquote after title block explaining that all paths are relative to `src/aip/` and imports use `aip.` prefix | Audit found that shorthand paths (e.g., `foundation/schemas.py`) vs. actual tree (`src/aip/foundation/schemas.py`) create cumulative mental translation load; after 9+ phases the gap between spec text and delivered tree is large enough to cause day-one breakage |
| ANNEX bare imports fixed | Changed `from foundation.schemas import ...` → `from aip.foundation.schemas import ...` and `from foundation.protocols import ...` → `from aip.foundation.protocols import ...` | These bare imports would fail the moment the test file is created; `test_layering.py` enforces `aip.`-prefixed imports |
| Path-qualifying parenthetical | Added "(paths shown relative to the `src/aip/` package root)" to CHUNK-10.0a heading and prose | First chunk is where coders orient; explicit qualification prevents literal interpretation of shorthand paths |
| 10.8 gate command note | Added NOTE that exact gate commands/file layout for cross-cutting tests will be confirmed during pre-10.8 CC against the delivered 9.7 pattern | Internal references echo older single-file gate style; the CC will reconcile against actual delivered structure |

---

## Phase 8 Scope

Phase 8 is the release hardening phase. Phase 7 was the capstone — it delivered every component deferred across Phases 1–6 and verified the system against the §22 acceptance criteria. But Phase 7 left three architecture-level gaps that prevent AIP 0.1 from being truly complete: (1) the Deferred Compiled Knowledge Layer from §3 has never been implemented, leaving the persistence architecture incomplete; (2) the model provider abstraction is hardcoded to four named slots with no extensibility path, contradicting the architecture's "no hardcoded model names" principle when it comes to adding new providers; and (3) the auth system is single-DEFINER with no path for collaborator access, limiting the system to a single user even for read-only scenarios like auditing.

Phase 8 closes these three gaps, and then performs the hardening work that turns "passing acceptance tests" into "production-ready software": performance optimization to verify the §2.1 laptop-viable promise, stabilization fixes for issues the acceptance tests revealed, comprehensive documentation, and a final release verification that confirms AIP 0.1 is ready for alpha testers.

**In scope:**

- CHUNK-10.0a: Schema additions — `KnowledgeCompilationConfig`, `PluginConfig`, `CollaboratorConfig`, `PerformanceConfig`, `ReleaseMetadata` dataclasses + Protocol amendments (`KnowledgeStore` new Protocol, `PluginProvider` new Protocol) + Config extensions (L1, append-only)
- CHUNK-10.0b: Knowledge compilation store adapter + plugin adapter — `SqliteKnowledgeStore`, `PluginLoader`, `YamlPluginProvider` (adapter)
- CHUNK-10.1: Knowledge compiler — `orchestration/compilation.py` — compile raw corpus knowledge into structured, indexed, retrievable knowledge artifacts; implements §3 "Deferred Compiled Knowledge Layer"; uses synthesis + evaluation model slots; writes to KnowledgeStore + VectorStore + LexicalStore (L2/L5, orchestration)
- CHUNK-10.2: Plugin architecture — `orchestration/plugins.py` — extensible model provider plugin system, plugin discovery from `plugins/` directory, plugin configuration, plugin lifecycle management, plugin-to-slot binding (L2, orchestration)
- CHUNK-10.3: Collaborator access — extend Phase 7 auth to support `collaborator` and `readonly` roles alongside `definer`; user management API; DEFINER sovereignty enforcement across all roles (adapter)
- CHUNK-10.4: Performance optimization — critical path profiling, retrieval tuning (α/β/γ/δ weight optimization), VectorStore query optimization, memory footprint analysis, SQLite WAL tuning, async batch operations (cross-cutting)
- CHUNK-10.5: Stabilization & edge cases — fix issues from Phase 7 acceptance tests, SQLite concurrency handling, graceful degradation under memory pressure, interrupted workflow recovery, data migration safety, idempotency guarantees (cross-cutting)
- CHUNK-10.6: Documentation & release preparation — `docs/README.md`, `docs/DEVELOPER_GUIDE.md`, `docs/API_REFERENCE.md`, `docs/DEPLOYMENT_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, `docs/CHANGELOG.md` (documentation)
- CHUNK-10.7: Release verification — end-to-end release verification with knowledge compilation, plugin, collaborator, and performance scenarios; final §22 gate confirmation; regression testing (integration)
- CHUNK-10.8: Cross-cutting gates — network isolation, model-name gate, import boundary verification, DEFINER sovereignty gate across collaborators, Appendix D constraint verification (including "Deferred compiled knowledge reservation ≠ implemented Wiki/Codex/Vigil" now resolved), plugin isolation, auth bypass prevention with collaborator roles

**Out of scope:**

- Full SPA web frontend with React/Next.js (the Phase 7 HTMX scaffold is sufficient for 0.1; a proper SPA is post-0.1)
- Full multi-tenant isolation with data partitioning (AIP 0.1 collaborators share the DEFINER's data; multi-tenant is post-0.1)
- External SSO / OAuth integration (Phase 7's session + API key auth extended with collaborators is sufficient for 0.1)
- Mobile surfaces (post-0.1)
- Custom workflow node plugins (only model provider plugins in 0.1; workflow node plugins are post-0.1)
- Internationalization / localization (post-0.1)
- Advanced observability integration (Laminar, custom dashboards) (post-0.1)
- AIP 0.2 architecture reset (separate effort that starts after 0.1 is released)

---

## Phase 7 Assumptions (Architectural Phase 7 = CHUNK-9.x series)

Phase 8 chunks depend on the following Phase 7 deliverables being merged and green:

| CHUNK-9.x | Deliverable | Phase 8 Dependency |
|---|---|---|
| 9.0a | `foundation/schemas.py` — `VigilConfig`, `AuthConfig`, `RateLimitConfig`, `CanonicalPromotionConfig`, `WorkflowTemplate`, `DeploymentProfile`, `VigilHealthStatus`, `AuthRole` | 10.0a appends; 10.1 (compilation uses VigilConfig for staleness); 10.3 (collaborator extends AuthRole) |
| 9.0a | `foundation/protocols.py` — `VigilStore`, `AuthStore` | 10.0a appends; 10.1 (compiler uses VigilStore for health data); 10.3 (extends AuthStore for collaborators) |
| 9.0b | `adapter/auth/` — Authentication system (SqliteSessionStore, AuthMiddleware, FastAPI dependencies) | 10.3 (extends auth for collaborators); 10.7 (release verification) |
| 9.0c | `adapter/middleware/rate_limiter.py` — Rate limiting | 10.4 (performance optimization tunes rate limiter); 10.7 (release verification) |
| 9.1 | `orchestration/actors/vigil.py` — `Vigil` actor | 10.1 (knowledge compiler is complementary to Vigil: Vigil monitors health, compiler produces knowledge); 10.5 (stabilization) |
| 9.1 | `adapter/vigil/sqlite_vigil_store.py` — `SqliteVigilStore` | 10.1 (compiler reads VigilStore for staleness data) |
| 9.2 | `orchestration/canonical_pipeline.py` — `CanonicalPipeline` | 10.1 (compiler reads canonicals as source material); 10.5 (stabilization) |
| 9.3 | `workflows/` — Extended workflow templates + `WorkflowRegistry` | 10.1 (compiler may be triggered via workflow); 10.6 (documentation references workflows) |
| 9.4 | `adapter/api/static/` — Web UI scaffold | 10.3 (collaborator management in web UI); 10.6 (documentation references UI); 10.7 (release verification) |
| 9.5 | `tests/acceptance/` — Full acceptance tests | 10.5 (stabilization addresses issues found); 10.7 (release verification extends) |
| 9.6 | `deploy/` — Docker Compose + config profiles | 10.4 (performance tuning adjusts Docker profiles); 10.6 (documentation references deployment); 10.7 (release verification) |
| 9.7 | Phase 7 cross-cutting gates | 10.8 extends |

Phase 6 dependencies (transitive through Phase 7):

| CHUNK-8.x | Deliverable | Phase 8 Dependency |
|---|---|---|
| 8.0a | `foundation/schemas.py` — `SurfaceConfig`, `ApiRoute`, `McpToolDef`, `AutonomyEscalation`, `AutonomyLevel`, `McpAutonomyLevel` | 10.0a appends; 10.3 (collaborator access uses AutonomyLevel) |
| 8.0a | `foundation/protocols.py` — `AutonomyGate`, `LexicalStore`, `CanonicalStore`, `EntityStore` methods | 10.0a appends; 10.1 (compiler indexes into LexicalStore); 10.3 (collaborator access goes through AutonomyGate) |
| 8.0b | `adapter/lexical/sqlite_fts5_store.py` — `SqliteFts5LexicalStore` | 10.1 (compiler indexes compiled knowledge into FTS5); 10.4 (performance optimization tunes FTS5) |
| 8.0b | `adapter/canonical/sqlite_canonical_store.py` — `SqliteCanonicalStore` | 10.1 (compiler reads canonicals as source) |
| 8.1 | `adapter/api/app.py` — FastAPI app factory + DI container | 10.2 (plugin loader registered in DI); 10.3 (collaborator API routes); 10.4 (performance endpoints) |
| 8.2 | `adapter/cli/` — CLI commands | 10.2 (CLI plugin management); 10.3 (CLI collaborator management); 10.6 (documentation references CLI) |
| 8.4 | `adapter/api/review.py` — Review Queue | 10.1 (compiled knowledge enters review queue); 10.3 (collaborator can view review queue) |
| 8.5 | `adapter/mcp/` — MCP server | 10.1 (MCP tools for knowledge search); 10.3 (MCP collaborator access) |

Phase 5 dependencies (transitive through Phase 6/7):

| CHUNK-7.x | Deliverable | Phase 8 Dependency |
|---|---|---|
| 7.0a | `foundation/schemas.py` — `SextonConfig`, `AcePlaybookEntry`, `BudgetConfig`, `RoutingWeight`, `FailureClassification` | 10.0a appends; 10.1 (compiler respects budget); 10.4 (performance optimization tunes routing) |
| 7.0a | `foundation/protocols.py` — `BudgetStore`, `ProjectStore.list_projects` | 10.0a appends; 10.1 (compiler uses BudgetStore); 10.4 (performance benchmarking uses budget data) |
| 7.1 | `orchestration/actors/sexton.py` — `Sexton` | 10.1 (compiler may trigger Sexton classification on compilation failures) |
| 7.4 | `orchestration/router.py` — `AdaptiveRouter` | 10.2 (plugin loader integrates with AdaptiveRouter for new slots); 10.4 (performance optimization tunes router) |

Phase 4 dependencies (transitive through Phase 5/6/7):

| CHUNK-6.x | Deliverable | Phase 8 Dependency |
|---|---|---|
| 6.0a | `foundation/schemas.py` — `PgvectorConfig`, `EvaluationScore`, `VectorBackendType` | 10.0a appends; 10.1 (compiler uses EvaluationScore); 10.4 (performance optimization tunes pgvector) |
| 6.2 | `orchestration/nodes/adversarial_eval.py`, `faithfulness.py`, `domain_coherence.py` | 10.1 (compiler runs faithfulness evaluation on compiled knowledge) |
| 6.3 | `adapter/vector/factory.py` — `create_vector_store` | 10.1 (compiler indexes into VectorStore via factory); 10.4 (performance optimization tunes factory) |

Phase 3 dependencies (transitive through Phase 4/5/6/7):

| CHUNK-5.x | Deliverable | Phase 8 Dependency |
|---|---|---|
| 5.0a | `foundation/schemas.py` — `ModelSlotConfig` | 10.0a appends; 10.2 (plugin binds to ModelSlotConfig) |
| 5.0a | `foundation/protocols.py` — `ModelProvider`, `EmbeddingProvider` | 10.0a appends; 10.1 (compiler uses ModelProvider); 10.2 (PluginProvider extends ModelProvider) |
| 5.0b | `adapter/model_slot_resolver.py` — `ModelSlotResolver` | 10.2 (plugin loader integrates with ModelSlotResolver) |

Phase 2 dependencies (transitive through Phase 3/4/5/6/7):

| CHUNK-4.x | Deliverable | Phase 8 Dependency |
|---|---|---|
| 4.0a | `foundation/schemas.py` — `ReviewVerdict`, `Event` | 10.0a appends; 10.1 (compiled knowledge goes through review) |
| 4.5 | `orchestration/engine.py` — YAML workflow engine | 10.1 (compiler may be invoked as workflow node) |

Phase 1 dependencies (transitive through Phase 2/3/4/5/6/7):

| CHUNK-1.x | Deliverable | Phase 8 Dependency |
|---|---|---|
| 1.0a | `foundation/schemas.py` — `Chunk`, `ContractRule`, `RetrievalResult` | 10.0a appends; 10.1 (compiler produces Chunks); 10.4 (performance optimization tunes retrieval) |
| 1.0a | `foundation/protocols.py` — `VectorStore`, `TraceStore`, `EcsStore`, `EventStore`, `ArtifactStore` | 10.0a appends; all Phase 8 components use existing Protocols |
| 1.1 | `orchestration/retrieval.py` — `retrieve_for_synthesis` | 10.1 (compiler uses retrieval for source material); 10.4 (performance optimization tunes retrieval) |
| 1.2 | `orchestration/validation.py` — `structural_validate` | 10.1 (compiler validates compiled knowledge) |

Phase 0 dependencies:

| CHUNK-0.x | Deliverable | Phase 8 Dependency |
|---|---|---|
| 0.2 | `config/aip.config.toml` — base config | 10.0a extends config with `[knowledge]`, `[plugins]`, `[collaborator]`, `[performance]`, `[release]` sections |
| 0.5 | `db/trace_events` table schema | 10.1 (compiler writes trace events); 10.4 (performance profiling reads trace events) |

**Critical note on CHUNK-10.0a:** This chunk appends to `foundation/schemas.py` and amends `foundation/protocols.py` — the same append-only/amend-by-addition pattern as CHUNK-1.0a, CHUNK-4.0a, CHUNK-5.0a, CHUNK-6.0a, CHUNK-7.0a, CHUNK-8.0a, and CHUNK-9.0a. No existing Phase 0, Phase 1, Phase 2, Phase 3, Phase 4, Phase 5, Phase 6, or Phase 7 code is deleted or rewritten.

**Continuity note:** Phase 8 components span all three layers. The knowledge compiler is an orchestration-layer component that composes existing Protocol implementations (ModelProvider, EmbeddingProvider, VectorStore, LexicalStore, CanonicalStore, VigilStore, TraceStore, ArtifactStore, EcsStore, EventStore). The plugin architecture is orchestration-layer with adapter-layer loading. The collaborator access is adapter-layer middleware that extends Phase 7's auth system. Performance optimization is cross-cutting. Documentation is not code (exempt from import boundaries). Per §7.2: orchestration may import foundation and adapter; adapter may import foundation but not orchestration; foundation never imports orchestration or adapter.

---

## Process Rules

These rules are binding for all work against this BuildSpec. They are inherited from Phase 1 Rev 1.3, Phase 2 Rev 1.2, Phase 3 Rev 1.1, Phase 4 Rev 1.0, Phase 5 Rev 1.0, Phase 6 Rev 1.0, and Phase 7 Rev 1.0 and must be followed without exception:

1. **Continuity Check.** Before starting any chunk, read `WORKLOG.md` and verify all DEPENDS-ON chunks are merged and green. This includes all Phase 1 (1.x), Phase 2 (4.x), Phase 3 (5.x), Phase 4 (6.x), Phase 5 (7.x), Phase 6 (8.x), and Phase 7 (9.x) chunks. If any dependency is not met, block and report.

2. **WORKLOG append-only.** Every chunk completion appends a new work record to `WORKLOG.md`. Never overwrite, delete, or reorder existing entries. Each record must include Task ID, Agent, Task description, Work Log (concrete steps), and Stage Summary.

3. **Amend by addition — schemas.** New dataclasses, type aliases, and enums are appended to `foundation/schemas.py`. Never modify, reorder, or delete existing Phase 0/1/2/3/4/5/6/7 definitions. The test suite verifies this by importing Phase 0/1/2/3/4/5/6/7 types and asserting they still work.

4. **Amend by addition — protocols.** New methods are appended as stubs to existing Protocol classes in `foundation/protocols.py`. New Protocol classes (KnowledgeStore, PluginProvider) are added as new class definitions or appended method stubs. Never redeclare an existing Protocol class. The ANNEX shows individual method stubs for amendments and full class blocks for new Protocols only.

5. **Deterministic CI.** All gate tests must pass without network access, API keys, secrets, or external services. Knowledge compiler tests use fixture canonical data and mock model providers. Plugin tests use in-memory plugin configurations. Collaborator tests use in-memory session stores. Performance tests use synthetic benchmarks with deterministic timers. Docker tests verify compose file syntax only (no real containers in CI).

6. **Push after each chunk.** After a chunk passes its gate test (`uv run pytest <test_file> -xvs`), commit and push before starting the next chunk. One chunk per commit.

7. **Import boundaries.** Foundation never imports orchestration or adapter. Adapter may import foundation but not orchestration. Orchestration may import both foundation and adapter. The layering test (`tests/test_layering.py`) enforces this. **Phase 8 addition:** The knowledge compiler is orchestration-layer and imports Foundation Protocols and schemas, and other orchestration components (Sexton, Vigil, CanonicalPipeline). The plugin loader is orchestration-layer; the plugin adapter is adapter-layer. Collaborator access is adapter-layer. Performance optimization touches all layers but must respect import boundaries.

8. **No hardcoded model names.** Per §4.1, all model references resolve through named slots from config. No model name may appear in any `orchestration/` or `foundation/` file. The test_no_hardcoded_model_names test enforces this. The knowledge compiler uses the `synthesis` and `evaluation` model slots. Plugins register new slot names through configuration, not code.

9. **Qualified phase references.** Always use qualified terminology: "Architectural Phase 8" for the logical scope, "CHUNK-10.x" for build units, "repo 3.x" for historical commits. Never use bare "Phase 8" without qualification.

10. **Repo overlap reconciliation.** Before building any CHUNK-10.x, check whether repo 2.x or 3.x code already implements part of the spec (especially knowledge compilation or plugin work). If overlap exists, extend existing code to meet the spec (amend by addition) rather than rewriting from scratch. Document reconciliation in WORKLOG.

11. **DEFINER sovereignty enforcement.** Per §1.7: "No UI, workflow, Beast cadence, MCP call, or queued task may bypass the DEFINER gates." Collaborators never bypass DEFINER sovereignty. The `collaborator` role can read and create drafts but cannot approve artifacts, modify configuration, or escalate autonomy. The `readonly` role can only read. All write and admin operations require the `definer` role. The AutonomyGate enforces this at the protocol level; the auth system enforces it at the identity level.

12. **Knowledge compilation ≠ canonical promotion.** Per Appendix D's spirit: compiled knowledge is a distinct concern from canonical artifacts. Compiled knowledge artifacts are synthesized summaries, cross-references, and structured representations derived from canonical sources. They go through their own ECS lifecycle (SPECIFIED → COMPILED → REVIEWED → APPROVED). They are stored in the KnowledgeStore, not the CanonicalStore. A compiled knowledge artifact references its source canonicals via provenance but is a separate entity.

13. **Plugin isolation.** Plugins run in the same process as the AIP application. They must not import orchestration or adapter code directly — they interact with AIP through the `PluginProvider` Protocol and configuration. A misbehaving plugin must not crash the AIP process. Plugin errors are caught, logged to trace_events, and the plugin is disabled gracefully. The DEFINER is notified.

14. **Performance is a release gate.** The §2.1 laptop-viable promise is a hard requirement. If the performance benchmarks show that AIP 0.1 cannot run on 4 GB RAM, Phase 8 is not complete until it can. The performance benchmark is a gate test, not a nice-to-have.

---

## Repo State Reconciliation

**Important:** This spec was written when the codebase was assumed to contain Phase 1 through Phase 7 code. The actual repo contains additional work from historical chunk series 2.x (YAML engine mechanics) and 3.x (L4/Sexton/ACE/budget foundation). This section documents the reconciliation.

### What this spec introduces that does NOT yet exist in code

| Type/Method | CHUNK | Notes |
|-------------|-------|-------|
| `KnowledgeCompilationConfig` dataclass | 10.0a | New — no prior implementation |
| `PluginConfig` dataclass | 10.0a | New — no prior implementation |
| `CollaboratorConfig` dataclass | 10.0a | New — no prior implementation |
| `PerformanceConfig` dataclass | 10.0a | New — no prior implementation |
| `ReleaseMetadata` dataclass | 10.0a | New — no prior implementation |
| `KnowledgeStore` Protocol | 10.0a | New — §3 "Deferred Compiled Knowledge Layer" never had a Protocol |
| `PluginProvider` Protocol | 10.0a | New — no prior Protocol for plugin abstraction |
| `adapter/knowledge/sqlite_knowledge_store.py` — `SqliteKnowledgeStore` | 10.0b | New — no prior knowledge store implementation |
| `adapter/plugins/` — Plugin loader + adapters | 10.0b | New — no prior plugin infrastructure |
| `orchestration/compilation.py` — `KnowledgeCompiler` | 10.1 | New — §3 "Deferred Compiled Knowledge Layer" never implemented |
| `orchestration/plugins.py` — `PluginManager` | 10.2 | New — no prior plugin management |
| `adapter/auth/collaborator.py` — Collaborator management | 10.3 | New — extends Phase 7 auth |
| `orchestration/perf.py` — Performance profiling | 10.4 | New — no prior performance infrastructure |
| Cross-cutting stabilization fixes | 10.5 | New — addresses Phase 7 acceptance test findings |
| `docs/` — Documentation | 10.6 | New — no prior comprehensive documentation |
| `tests/release/` — Release verification | 10.7 | New — extends Phase 7 acceptance tests |
| Phase 8 cross-cutting gates | 10.8 | Extend CHUNK-9.7 |

### What already exists that may overlap

| Repo Series | What It Built | Overlap With |
|-------------|--------------|-------------|
| Repo 2.x (CHUNK-2.1–2.13) | YAML engine mechanics | 10.1 (compiler may use YAML engine for compilation workflow) |
| Repo 3.x (CHUNK-3.1–3.12) | L4/Sexton/ACE/budget foundation | 10.1 (compiler may overlap with partial knowledge compilation from repo 3.x); 10.4 (performance infrastructure may overlap with repo 3.x profiling stubs) |

**Build strategy:** Where repo 3.x code already exists (especially any knowledge compilation scaffolding), extend it to meet the spec rather than replacing it. The spec is the authoritative target; existing code is a head start, not a conflict. Document all reconciliations in WORKLOG.

---

## Dependency DAG

```
CHUNK-9.0a ── CHUNK-9.0b ── CHUNK-9.0c ── CHUNK-9.1 ── CHUNK-9.2 ── CHUNK-9.3 ── CHUNK-9.4 ── CHUNK-9.5 ── CHUNK-9.6 ── CHUNK-9.7
     │              │              │            │            │            │            │            │            │            │
     │              │              │            │            │            │            │            │            │            │
CHUNK-10.0a ───── CHUNK-10.0b ─┼────────────┼────────────┼────────────┼────────────┤            │            │            │
     │              │             │            │            │            │            │            │            │
     │              │             ├──── CHUNK-10.1 (knowledge compiler) │            │            │            │            │
     │              │             │            │            │            │            │            │            │
     │              │             ├──── CHUNK-10.2 (plugin architecture)│            │            │            │            │
     │              │             │            │            │            │            │            │            │
     │              ├──── CHUNK-10.3 (collaborator access)  │            │            │            │            │            │
     │              │                          │            │            │            │            │            │
     │              ├──── CHUNK-10.4 (performance optimization)       │            │            │            │            │
     │              │                                       │            │            │            │            │
     └────────────────────────────────────────────────────── CHUNK-10.5 ─┘            │            │
                                                              (stabilization)          │            │
                                                                       │              │
                                                                  CHUNK-10.6 ────────┤
                                                                   (documentation)    │
                                                                       │              │
                                                                  CHUNK-10.7 ────────┤
                                                                   (release verify)   │
                                                                       │              │
                                                                  CHUNK-10.8 ────────┘
                                                                   (gates)

Linearized build order:
  10.0a → 10.0b (after 10.0a, CHUNK-9.0b, CHUNK-8.1)
       → 10.0b → 10.1 (after 10.0b, CHUNK-9.1, CHUNK-9.2)
       → 10.0b → 10.2 (after 10.0b, CHUNK-5.0b, CHUNK-8.1)
       → 10.0b → 10.3 (after 10.0b, CHUNK-9.0b)
       → 10.0b → 10.4 (after 10.0b, CHUNK-9.0c)
       → 10.5 (after 10.1, 10.2, 10.3, 10.4, CHUNK-9.5)
       → 10.6 (after 10.5)
       → 10.7 (after 10.5, 10.6, CHUNK-9.5)
       → 10.8 (after all)

Parallel groups:
  Group A: [10.0a]                                             — schema + protocol additions
  Group B: [10.0b] (after 10.0a, CHUNK-9.0b, CHUNK-8.1)       — knowledge store adapter + plugin adapter
  Group C: [10.1] (after 10.0b, CHUNK-9.1, CHUNK-9.2)         — Knowledge compiler
  Group D: [10.2] (after 10.0b, CHUNK-5.0b, CHUNK-8.1)        — Plugin architecture
  Group E: [10.3] (after 10.0b, CHUNK-9.0b)                    — Collaborator access
  Group F: [10.4] (after 10.0b, CHUNK-9.0c)                    — Performance optimization
  Group G: [10.5] (after 10.1, 10.2, 10.3, 10.4, CHUNK-9.5)   — Stabilization
  Group H: [10.6] (after 10.5)                                  — Documentation
  Group I: [10.7] (after 10.5, 10.6, CHUNK-9.5)                — Release verification
  Group J: [10.8] (after all)                                   — Cross-cutting gates
```

The key architectural insight: **Groups C–F are independent parallel paths** that all depend on the schema/protocol additions (10.0a) and adapter implementations (10.0b), but are independent of each other. The knowledge compiler (10.1) does not depend on plugins (10.2) or collaborators (10.3) or performance (10.4). They all converge at the stabilization chunk (10.5), which addresses issues found across all four paths. Documentation (10.6) comes after stabilization because it should document the final, stable API. Release verification (10.7) comes last before the cross-cutting gates (10.8) to confirm the entire system still meets §22 criteria after all Phase 8 changes.

---

## CHUNK-10.0a: Schema Additions + Protocol Amendments + Config Extensions (paths shown relative to the src/aip/ package root)

```
CHUNK-10.0a: Schema Additions + Protocol Amendments + Config Extensions
PHASE: 8
DEPENDS-ON: CHUNK-9.0a, CHUNK-8.0a
CODER-PROFILE: L1
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  foundation/schemas.py (append only — do not modify existing Phase 0/1/2/3/4/5/6/7 enums or dataclasses)
  foundation/protocols.py (amend by addition — add methods to existing Protocol classes + add new Protocols)
INTERFACES:
  @dataclass
  class KnowledgeCompilationConfig:
      compilation_model_slot: str               # which model slot to use for compilation (default: "synthesis")
      evaluation_model_slot: str                # which model slot for compiled knowledge evaluation (default: "evaluation")
      max_source_canonicals: int                # max canonicals to compile in a single batch
      compilation_confidence_threshold: float   # minimum confidence for compiled knowledge to enter review
      auto_index_on_approval: bool              # auto-index in VectorStore + LexicalStore after approval
      model_gen_assumption: str | None          # §1.8
  @dataclass
  class PluginConfig:
      plugins_dir: str                          # directory containing plugin configurations
      enabled: bool                             # toggle plugin system on/off (per §1.8)
      auto_discover: bool                       # automatically discover plugins in plugins_dir
      sandbox_mode: bool                        # catch plugin errors without crashing AIP
      model_gen_assumption: str | None          # §1.8
  @dataclass
  class CollaboratorConfig:
      enabled: bool                             # toggle collaborator access on/off (per §1.8)
      max_collaborators: int                    # max number of collaborator accounts (0.1 limit)
      collaborator_can_create_drafts: bool      # collaborators can create draft artifacts
      collaborator_can_submit_review: bool      # collaborators can submit artifacts for review
      collaborator_can_approve: bool            # collaborators can approve artifacts (default: False — DEFINER only)
      readonly_can_search: bool                 # readonly users can search the corpus
  @dataclass
  class PerformanceConfig:
      profiling_enabled: bool                   # toggle performance profiling on/off (per §1.8)
      max_memory_mb: int                        # target maximum memory footprint
      retrieval_timeout_seconds: float          # timeout for retrieval operations
      batch_embed_size: int                     # batch size for embedding operations
      sqlite_wal_mode: bool                     # use WAL mode for SQLite concurrency
      sqlite_busy_timeout_ms: int               # SQLite busy timeout for concurrent access
      vector_query_limit: int                   # max results from vector queries
      fts5_query_limit: int                     # max results from FTS5 queries
  @dataclass
  class ReleaseMetadata:
      release_version: str                      # "0.1.0"
      release_date: str                         # ISO 8601
      release_status: str                       # "alpha" / "beta" / "stable"
      architecture_revision: str                # "5.2"
      acceptance_gates_passed: list[str]        # list of §22 gate IDs that passed
      known_limitations: list[str]              # documented limitations
      breaking_changes: list[str]               # breaking changes from prior versions
  # Type aliases
  CompilationState = Literal["SPECIFIED", "COMPILED", "REVIEWED", "APPROVED", "FAILED"]
  PluginStatus = Literal["loaded", "error", "disabled"]
  CollaboratorRole = Literal["definer", "collaborator", "readonly"]
  # Protocol amendments in foundation/protocols.py (append method stubs only — do NOT redeclare class):
  # KnowledgeStore: new Protocol (does not exist in Phase 0/1/2/3/4/5/6/7)
  class KnowledgeStore(Protocol):
      async def store_compiled(self, knowledge_id: str, content: str, source_canonical_ids: list[str], domain: str, metadata: dict) -> None: ...
      async def get_compiled(self, knowledge_id: str) -> dict | None: ...
      async def list_compiled(self, domain: str | None = None, state: CompilationState | None = None) -> list[dict]: ...
      async def update_state(self, knowledge_id: str, new_state: CompilationState) -> None: ...
      async def get_provenance(self, knowledge_id: str) -> list[dict]: ...
      async def search_compiled(self, query: str, domain: str | None = None, limit: int = 10) -> list[dict]: ...
  # PluginProvider: new Protocol
  class PluginProvider(Protocol):
      async def call_model(self, prompt: str, config: dict) -> str: ...
      async def health_check(self) -> dict: ...
      def get_slot_name(self) -> str: ...
      def get_provider_name(self) -> str: ...
TESTS:
  tests/test_phase8_schema_additions.py
GATE: uv run pytest tests/test_phase8_schema_additions.py -xvs
```

### Prose

This chunk establishes the shared data types, protocol amendments, and configuration extensions that all subsequent Phase 8 chunks depend on (paths shown relative to the `src/aip/` package root). It does nine things:

**1. Append `KnowledgeCompilationConfig` dataclass to `foundation/schemas.py`.** The `KnowledgeCompilationConfig` dataclass captures all knowledge compilation configuration: the model slot used for compilation (defaults to "synthesis" — the same slot used for primary generation, per §4.1), the model slot used for evaluation of compiled knowledge (defaults to "evaluation"), the maximum number of source canonicals to compile in a single batch (bounding the compilation cost, same pattern as VigilConfig.max_re_evaluate_batch_size), the minimum confidence threshold for compiled knowledge to enter the review queue, whether to auto-index in VectorStore and LexicalStore after approval (same pattern as CanonicalPromotionConfig.auto_reindex_on_promotion), and a `model_gen_assumption` field per §1.8. The compilation criteria encode assumptions about model synthesis quality — for example, "the synthesis model can reliably produce structured knowledge summaries from multiple canonical sources." When model slots change, Vigil should re-audit these thresholds alongside canonical promotion thresholds.

**2. Append `PluginConfig` dataclass.** The `PluginConfig` dataclass captures plugin system configuration: the directory containing plugin configurations (YAML files that define new model providers), whether the plugin system is enabled (toggleable per §1.8), whether to auto-discover plugins in the directory, whether to run plugins in sandbox mode (catching errors without crashing AIP), and a `model_gen_assumption` field per §1.8. Plugins are a §1.8 concern — they encode assumptions about model providers, and a plugin that addresses a model limitation that no longer exists should be disabled. The `sandbox_mode` flag is critical for stability: a misbehaving plugin must not crash the AIP process.

**3. Append `CollaboratorConfig` dataclass.** The `CollaboratorConfig` dataclass captures collaborator access configuration: whether collaborator access is enabled (toggleable per §1.8), the maximum number of collaborator accounts (a 0.1 limit — AIP 0.1 is not designed for large-scale multi-user access), whether collaborators can create drafts, submit for review, or approve artifacts, and whether readonly users can search the corpus. The key constraint: `collaborator_can_approve` defaults to `False` — per §1.7, only the DEFINER can approve artifacts and promote them to canonical. Collaborators can contribute but not approve.

**4. Append `PerformanceConfig` dataclass.** The `PerformanceConfig` dataclass captures performance tuning parameters: whether profiling is enabled (toggleable per §1.8), the target maximum memory footprint (for the laptop-viable profile, §2.1), timeouts for retrieval operations, batch sizes for embedding, SQLite WAL mode configuration, SQLite busy timeout for concurrent access, and query limits for vector and FTS5 searches. These parameters are the knobs that the performance optimization chunk (10.4) adjusts. The `sqlite_wal_mode` flag is particularly important for the multi-surface concurrent access that Phase 6 introduced — without WAL mode, concurrent reads and writes to SQLite can cause contention.

**5. Append `ReleaseMetadata` dataclass.** The `ReleaseMetadata` dataclass captures release metadata: the version string, release date, release status (alpha/beta/stable), the architecture revision, the list of §22 acceptance gates that passed, known limitations, and breaking changes. This is written by the release verification chunk (10.7) when AIP 0.1 passes all acceptance gates. It serves as the release manifest — the definitive record of what AIP 0.1 is and what it has been verified to do.

**6. Add `CompilationState`, `PluginStatus`, and `CollaboratorRole` type aliases.** `CompilationState` is a `Literal["SPECIFIED", "COMPILED", "REVIEWED", "APPROVED", "FAILED"]` that tracks the lifecycle of a compiled knowledge artifact. This is distinct from the ECS states for generated artifacts — compiled knowledge goes through COMPILED (not GENERATED) because it is a synthesis of existing canonical material, not a fresh generation. `PluginStatus` is a `Literal["loaded", "error", "disabled"]` that tracks plugin health. `CollaboratorRole` extends Phase 7's `AuthRole` with the `collaborator` role — a middle ground between `definer` (full access) and `readonly` (read-only access).

**7. Add `KnowledgeStore` Protocol in `foundation/protocols.py`.** This is a new Protocol that abstracts the persistence needs of the Deferred Compiled Knowledge Layer from §3. The `store_compiled` method writes a compiled knowledge artifact with its source canonical IDs (provenance), domain, and metadata. The `get_compiled` and `list_compiled` methods retrieve compiled knowledge, optionally filtered by domain and compilation state. The `update_state` method transitions the compilation state (analogous to EcsStore.transition but for compiled knowledge). The `get_provenance` method returns the list of source canonicals that were used to compile the knowledge artifact — this is the §1.5 provenance chain for compiled knowledge. The `search_compiled` method searches compiled knowledge by query and domain. This is a separate Protocol from `CanonicalStore` — CanonicalStore manages canonical artifacts, while KnowledgeStore manages compiled knowledge artifacts. Per Process Rule 12 and Appendix D: "Deferred compiled knowledge reservation ≠ implemented Wiki/Codex/Vigil" — KnowledgeStore resolves this non-collapse rule by providing the actual implementation.

**8. Add `PluginProvider` Protocol in `foundation/protocols.py`.** This is a new Protocol that abstracts a plugin-provided model provider. The `call_model` method sends a prompt to the plugin's model and returns the response. The `health_check` method verifies the plugin's model is accessible. The `get_slot_name` and `get_provider_name` methods identify the plugin. This Protocol extends the `ModelProvider` abstraction from Phase 3 — any class that implements `PluginProvider` can be registered as a model slot provider via the PluginManager. The key difference from `ModelProvider` is that `PluginProvider` carries slot and provider identity, which the Adaptive Router needs for routing decisions.

**9. Extend `AuthStore` Protocol with collaborator methods.** The existing `AuthStore` Protocol from Phase 7 is amended with new method stubs: `list_users` (return all user identities), `create_user` (create a collaborator or readonly user), `update_user_role` (change a user's role), `revoke_user` (remove a user). These methods extend the single-DEFINER auth to support multiple identities while maintaining the DEFINER as the sovereign authority. The `create_user` method can only create `collaborator` or `readonly` roles — the `definer` role is created through the configuration file, not through the API.

**Config additions.** Phase 8 extends `config/aip.config.toml` with:

```toml
[knowledge]
compilation_model_slot = "synthesis"
evaluation_model_slot = "evaluation"
max_source_canonicals = 10
compilation_confidence_threshold = 0.60
auto_index_on_approval = true
model_gen_assumption = "Synthesis model can reliably produce structured knowledge summaries from multiple canonical sources"

[plugins]
plugins_dir = "plugins"
enabled = true
auto_discover = true
sandbox_mode = true
model_gen_assumption = "Plugins encode assumptions about model provider capabilities"

[collaborator]
enabled = false                                   # true in production profile
max_collaborators = 5
collaborator_can_create_drafts = true
collaborator_can_submit_review = true
collaborator_can_approve = false                  # DEFINER only per §1.7
readonly_can_search = true

[performance]
profiling_enabled = false                         # true for benchmarking
max_memory_mb = 4096                              # §2.1 laptop-viable target
retrieval_timeout_seconds = 30.0
batch_embed_size = 32
sqlite_wal_mode = true
sqlite_busy_timeout_ms = 5000
vector_query_limit = 50
fts5_query_limit = 50

[release]
version = "0.1.0"
status = "alpha"
```

The gate test verifies: (a) all new dataclasses can be instantiated with required fields, (b) `KnowledgeCompilationConfig` carries `model_gen_assumption` field per §1.8, (c) `PluginConfig` carries `model_gen_assumption` field per §1.8, (d) `CollaboratorConfig` has collaborator access flags, (e) `PerformanceConfig` has memory and query parameters, (f) `ReleaseMetadata` has release fields, (g) `KnowledgeStore` Protocol has all six methods, (h) `PluginProvider` Protocol has all four methods, (i) `AuthStore` has collaborator methods, (j) existing Phase 0/1/2/3/4/5/6/7 schema enums and dataclasses are not broken, (k) existing Protocol methods still exist.

### ANNEX

**`foundation/schemas.py` (append to existing file):**

```python
# --- Phase 8 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type aliases for compiled knowledge lifecycle
CompilationState = Literal["SPECIFIED", "COMPILED", "REVIEWED", "APPROVED", "FAILED"]

# Type aliases for plugin status
PluginStatus = Literal["loaded", "error", "disabled"]

# Type aliases for collaborator roles (extends AuthRole from Phase 7)
CollaboratorRole = Literal["definer", "collaborator", "readonly"]


@dataclass
class KnowledgeCompilationConfig:
    """Configuration for the knowledge compilation system.

    Per §3: Deferred Compiled Knowledge Layer — finally implemented.
    Per §1.8: model_gen_assumption tags what the compilation criteria assume.
    Per Appendix D: compiled knowledge ≠ canonical artifact.
    """
    compilation_model_slot: str = "synthesis"
    evaluation_model_slot: str = "evaluation"
    max_source_canonicals: int = 10
    compilation_confidence_threshold: float = 0.60
    auto_index_on_approval: bool = True
    model_gen_assumption: str | None = None


@dataclass
class PluginConfig:
    """Plugin system configuration.

    Per §4.1: no hardcoded model names — plugins provide extensibility.
    Per §1.8: enabled and sandbox_mode are toggleable.
    Per §1.8: model_gen_assumption tags what model limitations plugins compensate for.
    """
    plugins_dir: str = "plugins"
    enabled: bool = True
    auto_discover: bool = True
    sandbox_mode: bool = True
    model_gen_assumption: str | None = None


@dataclass
class CollaboratorConfig:
    """Collaborator access configuration.

    Per §1.7: collaborators never bypass DEFINER sovereignty.
    Per §1.8: enabled is toggleable.
    Per Process Rule 11: collaborator_can_approve defaults to False.
    """
    enabled: bool = False
    max_collaborators: int = 5
    collaborator_can_create_drafts: bool = True
    collaborator_can_submit_review: bool = True
    collaborator_can_approve: bool = False
    readonly_can_search: bool = True


@dataclass
class PerformanceConfig:
    """Performance tuning configuration.

    Per §2.1: laptop-viable — must work on 4-6 GB RAM.
    Per §1.8: profiling_enabled is toggleable.
    """
    profiling_enabled: bool = False
    max_memory_mb: int = 4096
    retrieval_timeout_seconds: float = 30.0
    batch_embed_size: int = 32
    sqlite_wal_mode: bool = True
    sqlite_busy_timeout_ms: int = 5000
    vector_query_limit: int = 50
    fts5_query_limit: int = 50


@dataclass
class ReleaseMetadata:
    """AIP 0.1 release metadata.

    Written by release verification (CHUNK-10.7) when all §22 gates pass.
    Serves as the definitive release manifest.
    """
    release_version: str = "0.1.0"
    release_date: str = ""  # REQUIRED — ISO 8601
    release_status: str = "alpha"
    architecture_revision: str = "5.2"
    acceptance_gates_passed: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    breaking_changes: list[str] = field(default_factory=list)
```
<!-- ESTIMATED_TOKENS: ~400 -->

**`foundation/protocols.py` (append method stubs to existing Protocol classes + add new Protocols):**

```python
# --- Phase 8 amendments (append method stubs only — do NOT redeclare existing Protocol classes) ---

# AuthStore: add collaborator management methods (Phase 8 amendment)
    async def list_users(self) -> list[dict]:
        """List all user identities.

        Returns list of dicts with: identity, role, created_at, last_active_at.
        """
        ...

    async def create_user(self, identity: str, role: "CollaboratorRole", password_hash: str | None = None) -> bool:
        """Create a collaborator or readonly user.

        The 'definer' role cannot be created through this method —
        it is defined in the configuration file.
        Returns True if created, False if identity already exists.
        """
        ...

    async def update_user_role(self, identity: str, new_role: "CollaboratorRole") -> bool:
        """Update a user's role.

        Cannot change the DEFINER's role.
        Returns True if updated, False if user not found.
        """
        ...

    async def revoke_user(self, identity: str) -> bool:
        """Remove a user. Cannot revoke the DEFINER.

        Revokes all sessions and API keys for the user.
        Returns True if revoked, False if user not found or is DEFINER.
        """
        ...

# --- Phase 8 new Protocols (not amendments — these are new classes) ---


class KnowledgeStore(Protocol):
    """Abstraction for the Deferred Compiled Knowledge Layer.

    Per §3: "Deferred Compiled Knowledge Layer" — persistence concern.
    Per §1.5: compiled knowledge must track provenance to source canonicals.
    Per Appendix D: compiled knowledge ≠ canonical artifact.
    Per Process Rule 12: CompilationState is distinct from ECS states.
    """

    async def store_compiled(
        self,
        knowledge_id: str,
        content: str,
        source_canonical_ids: list[str],
        domain: str,
        metadata: dict,
    ) -> None:
        """Store a compiled knowledge artifact with provenance.

        metadata includes: compilation_model_slot, evaluation_scores,
        compilation_timestamp, confidence.
        """
        ...

    async def get_compiled(self, knowledge_id: str) -> dict | None:
        """Get a compiled knowledge artifact by ID.

        Returns dict with: knowledge_id, content, source_canonical_ids,
        domain, state, metadata, created_at, updated_at.
        """
        ...

    async def list_compiled(
        self,
        domain: str | None = None,
        state: "CompilationState | None" = None,
    ) -> list[dict]:
        """List compiled knowledge artifacts, optionally filtered.

        Ordered by created_at descending (newest first).
        """
        ...

    async def update_state(
        self, knowledge_id: str, new_state: "CompilationState"
    ) -> None:
        """Transition the compilation state.

        Valid transitions:
          SPECIFIED → COMPILED → REVIEWED → APPROVED
                              ↘ FAILED
        Analogous to ECS transitions but for compiled knowledge.
        """
        ...

    async def get_provenance(self, knowledge_id: str) -> list[dict]:
        """Get the source canonicals for a compiled knowledge artifact.

        Returns list of dicts with: canonical_id, domain, title,
        evaluation_scores, canonical_state.
        This is the §1.5 provenance chain for compiled knowledge.
        """
        ...

    async def search_compiled(
        self, query: str, domain: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Search compiled knowledge by query and domain.

        Uses VectorStore + LexicalStore under the hood.
        Returns ranked results with relevance scores.
        """
        ...


class PluginProvider(Protocol):
    """Abstraction for a plugin-provided model provider.

    Per §4.1: model references resolve through configuration.
    Per §1.8: plugins encode assumptions about model provider capabilities.
    Per Process Rule 13: plugins must not crash the AIP process.
    """

    async def call_model(self, prompt: str, config: dict) -> str:
        """Send a prompt to the plugin's model and return the response.

        config includes: temperature, max_tokens, and any provider-specific
        parameters from the plugin YAML configuration.
        """
        ...

    async def health_check(self) -> dict:
        """Verify the plugin's model is accessible.

        Returns dict with: status ("ok"/"error"), latency_ms,
        model_name, provider_name.
        """
        ...

    def get_slot_name(self) -> str:
        """Return the model slot this plugin provides for.

        e.g., "synthesis", "evaluation", "sexton", "embedding",
        or a custom slot name.
        """
        ...

    def get_provider_name(self) -> str:
        """Return the plugin's provider name.

        e.g., "custom-openai-compatible", "local-vllm", "azure-openai".
        """
        ...
```
<!-- ESTIMATED_TOKENS: ~400 -->

**`tests/test_phase8_schema_additions.py`:**

```python
"""Verify Phase 8 schema additions do not break Phase 0, 1, 2, 3, 4, 5, 6, or 7."""
import pytest

from aip.foundation.schemas import (
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
    CollaboratorConfig,
    CollaboratorRole,
    CompilationState,
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
    KnowledgeCompilationConfig,
    McpAutonomyLevel,
    McpToolDef,
    MigrationCheckpoint,
    MigrationStatus,
    ModelSlotConfig,
    PerformanceConfig,
    PgvectorConfig,
    PluginConfig,
    PluginStatus,
    RateLimitConfig,
    ReleaseMetadata,
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
from aip.foundation.protocols import (
    ArtifactStore,
    AuthStore,
    AutonomyGate,
    BudgetStore,
    CanonicalStore,
    EmbeddingProvider,
    EcsStore,
    EntityStore,
    EventStore,
    KnowledgeStore,
    LexicalStore,
    ModelProvider,
    PluginProvider,
    ProjectStore,
    TraceStore,
    VectorStore,
    VigilStore,
)


def test_knowledge_compilation_config_dataclass():
    kcc = KnowledgeCompilationConfig(
        compilation_model_slot="synthesis",
        evaluation_model_slot="evaluation",
        max_source_canonicals=10,
        compilation_confidence_threshold=0.60,
        auto_index_on_approval=True,
        model_gen_assumption="Synthesis model produces structured summaries",
    )
    assert kcc.compilation_model_slot == "synthesis"
    assert kcc.model_gen_assumption is not None


def test_plugin_config_dataclass():
    pc = PluginConfig(
        plugins_dir="plugins",
        enabled=True,
        auto_discover=True,
        sandbox_mode=True,
        model_gen_assumption="Plugins encode model provider capabilities",
    )
    assert pc.sandbox_mode is True
    assert pc.model_gen_assumption is not None


def test_collaborator_config_dataclass():
    cc = CollaboratorConfig(
        enabled=False,
        max_collaborators=5,
        collaborator_can_create_drafts=True,
        collaborator_can_approve=False,
    )
    assert cc.collaborator_can_approve is False  # DEFINER only per §1.7


def test_performance_config_dataclass():
    pfc = PerformanceConfig(
        profiling_enabled=False,
        max_memory_mb=4096,
        sqlite_wal_mode=True,
    )
    assert pfc.max_memory_mb == 4096


def test_release_metadata_dataclass():
    rm = ReleaseMetadata(
        release_version="0.1.0",
        release_date="2026-05-28",
        release_status="alpha",
        architecture_revision="5.2",
    )
    assert rm.release_version == "0.1.0"


def test_compilation_state_type_alias():
    specified: CompilationState = "SPECIFIED"
    compiled: CompilationState = "COMPILED"
    reviewed: CompilationState = "REVIEWED"
    approved: CompilationState = "APPROVED"
    failed: CompilationState = "FAILED"
    assert compiled == "COMPILED"


def test_plugin_status_type_alias():
    loaded: PluginStatus = "loaded"
    error: PluginStatus = "error"
    disabled: PluginStatus = "disabled"
    assert loaded == "loaded"


def test_collaborator_role_type_alias():
    definer: CollaboratorRole = "definer"
    collab: CollaboratorRole = "collaborator"
    readonly: CollaboratorRole = "readonly"
    assert collab == "collaborator"


def test_knowledge_compilation_config_carries_model_gen_assumption():
    """Per §1.8: compilation criteria must carry model_gen_assumption."""
    kcc = KnowledgeCompilationConfig(
        model_gen_assumption="Synthesis model produces reliable summaries"
    )
    assert kcc.model_gen_assumption is not None


def test_plugin_config_carries_model_gen_assumption():
    """Per §1.8: plugins must carry model_gen_assumption."""
    pc = PluginConfig(
        model_gen_assumption="Plugins compensate for limited model provider options"
    )
    assert pc.model_gen_assumption is not None


def test_collaborator_cannot_approve_by_default():
    """Per §1.7: collaborators cannot approve artifacts by default."""
    cc = CollaboratorConfig()
    assert cc.collaborator_can_approve is False


def test_phase0_through_phase7_enums_still_work():
    """Phase 0/1/2/3/4/5/6/7 enums must not be broken by Phase 8 additions."""
    assert EcsState.GENERATED is not None
    assert FailureType.C is not None


def test_phase0_through_phase7_dataclasses_still_work():
    """Phase 0/1/2/3/4/5/6/7 dataclasses must not be broken by Phase 8 additions."""
    c = Chunk(id="x", content="hello", score=0.9, metadata={"k": "v"}, domain="test")
    assert c.id == "x"
    v = ReviewVerdict(artifact_id="a1", verdict="APPROVED", reviewer="definer")
    assert v.verdict == "APPROVED"
    sc = SurfaceConfig(api_host="127.0.0.1", api_port=8000)
    assert sc.api_port == 8000
    vc = VigilConfig(stale_threshold_days=30)
    assert vc.stale_threshold_days == 30


def test_knowledge_store_protocol_methods():
    """Phase 8: KnowledgeStore must have required methods."""
    assert hasattr(KnowledgeStore, "store_compiled"), "KnowledgeStore missing store_compiled"
    assert hasattr(KnowledgeStore, "get_compiled"), "KnowledgeStore missing get_compiled"
    assert hasattr(KnowledgeStore, "list_compiled"), "KnowledgeStore missing list_compiled"
    assert hasattr(KnowledgeStore, "update_state"), "KnowledgeStore missing update_state"
    assert hasattr(KnowledgeStore, "get_provenance"), "KnowledgeStore missing get_provenance"
    assert hasattr(KnowledgeStore, "search_compiled"), "KnowledgeStore missing search_compiled"


def test_plugin_provider_protocol_methods():
    """Phase 8: PluginProvider must have required methods."""
    assert hasattr(PluginProvider, "call_model"), "PluginProvider missing call_model"
    assert hasattr(PluginProvider, "health_check"), "PluginProvider missing health_check"
    assert hasattr(PluginProvider, "get_slot_name"), "PluginProvider missing get_slot_name"
    assert hasattr(PluginProvider, "get_provider_name"), "PluginProvider missing get_provider_name"


def test_auth_store_collaborator_methods():
    """Phase 8: AuthStore must have collaborator management methods."""
    assert hasattr(AuthStore, "list_users"), "AuthStore missing list_users"
    assert hasattr(AuthStore, "create_user"), "AuthStore missing create_user"
    assert hasattr(AuthStore, "update_user_role"), "AuthStore missing update_user_role"
    assert hasattr(AuthStore, "revoke_user"), "AuthStore missing revoke_user"


def test_existing_protocol_methods_preserved():
    """Phase 1/2/3/4/5/6/7 methods must still exist after Phase 8 amendments."""
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
    assert hasattr(AutonomyGate, "check"), "AutonomyGate missing check (Phase 6)"
    assert hasattr(LexicalStore, "search"), "LexicalStore missing search (Phase 6)"
    assert hasattr(VigilStore, "get_canonical_health"), "VigilStore missing get_canonical_health (Phase 7)"
    assert hasattr(AuthStore, "create_session"), "AuthStore missing create_session (Phase 7)"
```
<!-- ESTIMATED_TOKENS: ~500 -->

---

## CHUNK-10.0b: Knowledge Store Adapter + Plugin Adapter

```
CHUNK-10.0b: Knowledge Store Adapter + Plugin Adapter
PHASE: 8
DEPENDS-ON: CHUNK-10.0a, CHUNK-9.0b, CHUNK-8.1
CODER-PROFILE: L2
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  adapter/knowledge/sqlite_knowledge_store.py
  adapter/knowledge/__init__.py
  adapter/plugins/plugin_loader.py
  adapter/plugins/yaml_plugin_provider.py
  adapter/plugins/__init__.py
  tests/test_knowledge_store.py
  tests/test_plugin_adapter.py
INTERFACES:
  class SqliteKnowledgeStore(KnowledgeStore):
      def __init__(self, db_path: str, vector_store: VectorStore, lexical_store: LexicalStore) -> None: ...
      async def store_compiled(self, knowledge_id: str, content: str, source_canonical_ids: list[str], domain: str, metadata: dict) -> None: ...
      async def get_compiled(self, knowledge_id: str) -> dict | None: ...
      async def list_compiled(self, domain: str | None = None, state: CompilationState | None = None) -> list[dict]: ...
      async def update_state(self, knowledge_id: str, new_state: CompilationState) -> None: ...
      async def get_provenance(self, knowledge_id: str) -> list[dict]: ...
      async def search_compiled(self, query: str, domain: str | None = None, limit: int = 10) -> list[dict]: ...
      async def initialize(self) -> None: ...
      async def close(self) -> None: ...
  class PluginLoader:
      def __init__(self, config: PluginConfig) -> None: ...
      def discover_plugins(self) -> list[dict]: ...
      def load_plugin(self, plugin_config_path: str) -> PluginProvider | None: ...
      def list_loaded_plugins(self) -> list[dict]: ...
      def unload_plugin(self, slot_name: str) -> None: ...
  class YamlPluginProvider(PluginProvider):
      def __init__(self, config_path: str) -> None: ...
      async def call_model(self, prompt: str, config: dict) -> str: ...
      async def health_check(self) -> dict: ...
      def get_slot_name(self) -> str: ...
      def get_provider_name(self) -> str: ...
TESTS:
  tests/test_knowledge_store.py
  tests/test_plugin_adapter.py
GATE: uv run pytest tests/test_knowledge_store.py tests/test_plugin_adapter.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the two adapter-layer components that the knowledge compiler (10.1) and plugin manager (10.2) need: the `SqliteKnowledgeStore` and the `PluginLoader` with `YamlPluginProvider`. These are pure adapter-layer implementations of the Protocols defined in CHUNK-10.0a.

**SqliteKnowledgeStore.** The `SqliteKnowledgeStore` implements the `KnowledgeStore` Protocol using SQLite. The `initialize()` method creates two tables: `compiled_knowledge` (knowledge_id TEXT PRIMARY KEY, content TEXT, source_canonical_ids TEXT — JSON array, domain TEXT, state TEXT, metadata TEXT — JSON, created_at TEXT, updated_at TEXT) and `compiled_knowledge_provenance` (knowledge_id TEXT, canonical_id TEXT, canonical_domain TEXT, canonical_title TEXT, canonical_evaluation_scores TEXT — JSON, canonical_state TEXT, PRIMARY KEY (knowledge_id, canonical_id)). The provenance table implements §1.5's provenance chain for compiled knowledge — every compiled artifact is linked to its source canonicals.

The `store_compiled` method writes to both tables: the knowledge artifact and its provenance links. It also indexes the content in VectorStore (via `vector_store.upsert`) and LexicalStore (via `lexical_store.index_document`) if the state is "APPROVED". This dual-indexing is the same pattern as the canonical pipeline from Phase 7 — compiled knowledge is searchable through both semantic (vector) and lexical (FTS5) paths once approved.

The `search_compiled` method queries VectorStore and LexicalStore in parallel, then merges results using the same four-factor reranking from §8.3. This provides unified search across both compiled and canonical knowledge. The `update_state` method validates compilation state transitions (SPECIFIED→COMPILED→REVIEWED→APPROVED, COMPILED→FAILED) and updates the database. The `get_provenance` method joins with the provenance table to return source canonical details.

**PluginLoader.** The `PluginLoader` scans the `plugins/` directory for YAML configuration files. Each plugin config YAML has the structure:

```yaml
slot_name: "synthesis"
provider_name: "custom-openai-compatible"
base_url: "https://api.custom-provider.com/v1"
api_key_env: "CUSTOM_PROVIDER_API_KEY"  # environment variable name, not the key itself
model: "custom-model-name"
parameters:
  temperature: 0.7
  max_tokens: 4096
health_check_prompt: "Respond with OK"
```

The `discover_plugins` method returns a list of plugin metadata dicts. The `load_plugin` method reads the YAML, validates it, creates a `YamlPluginProvider` instance, and registers it with the DI container. The `unload_plugin` method removes a plugin from the slot. If `sandbox_mode` is True, plugin loading errors are caught and logged without crashing AIP.

**YamlPluginProvider.** The `YamlPluginProvider` implements the `PluginProvider` Protocol for YAML-configured model providers. The `call_model` method makes an HTTP POST to the provider's `base_url` with the prompt and configuration parameters. It uses `httpx.AsyncClient` for async HTTP. The `health_check` method sends a minimal prompt to verify the provider is accessible. The `get_slot_name` and `get_provider_name` methods return the values from the YAML config. In CI mode (deterministic, no network), the `call_model` method returns a fixture response.

**Security note.** Plugin API keys are read from environment variables, not stored in the YAML file. The YAML file contains the environment variable name (e.g., `api_key_env: "CUSTOM_PROVIDER_API_KEY"`), and the provider reads the actual key from `os.environ`. This prevents API keys from being committed to version control. Per §4.1, no API keys appear in application code.

The gate test verifies: (a) `SqliteKnowledgeStore` implements `KnowledgeStore` Protocol, (b) compiled knowledge can be stored and retrieved, (c) provenance links are created, (d) compilation state transitions work, (e) search returns ranked results, (f) `PluginLoader` discovers plugins in the directory, (g) `YamlPluginProvider` implements `PluginProvider` Protocol, (h) plugin loading in sandbox mode catches errors, (i) adapter layer does not import orchestration.

---

## CHUNK-10.1: Knowledge Compiler

```
CHUNK-10.1: Knowledge Compiler
PHASE: 8
DEPENDS-ON: CHUNK-10.0b, CHUNK-9.1, CHUNK-9.2
CODER-PROFILE: L3
CONTEXT-BUDGET: ~6,000 tokens
FILES:
  orchestration/compilation.py
  tests/test_knowledge_compiler.py
INTERFACES:
  class KnowledgeCompiler:
      def __init__(self, config: KnowledgeCompilationConfig, knowledge_store: KnowledgeStore, canonical_store: CanonicalStore, vector_store: VectorStore, lexical_store: LexicalStore, model_provider: ModelProvider, embedding_provider: EmbeddingProvider, trace_store: TraceStore, event_store: EventStore, ecs_store: EcsStore, vigil_store: VigilStore) -> None: ...
      async def compile_from_canonicals(self, domain: str, topic: str, source_canonical_ids: list[str] | None = None) -> dict: ...
      async def compile_domain_summary(self, domain: str) -> dict: ...
      async def compile_cross_reference(self, knowledge_id: str) -> dict: ...
      async def evaluate_compiled(self, knowledge_id: str) -> dict: ...
      async def list_compilation_candidates(self, domain: str | None = None) -> list[dict]: ...
      async def run(self) -> None: ...
TESTS:
  tests/test_knowledge_compiler.py
GATE: uv run pytest tests/test_knowledge_compiler.py -xvs
```

### Prose

This chunk implements the knowledge compiler — the orchestration component that fulfills the "Deferred Compiled Knowledge Layer" from §3. The knowledge compiler synthesizes raw canonical knowledge into structured, indexed, retrievable compiled knowledge artifacts. This is Vigil's deeper purpose: Phase 7's Vigil monitors canonical health; the knowledge compiler produces knowledge from canonicals. Per Appendix D: "Deferred compiled knowledge reservation ≠ implemented Wiki/Codex/Vigil" — this chunk resolves that non-collapse rule.

**KnowledgeCompiler class.** The `KnowledgeCompiler` is an orchestration component that composes multiple Protocol instances, following the same pattern as `CanonicalPipeline` from Phase 7. It is the single entry point for compiling canonical knowledge into structured knowledge artifacts.

**compile_from_canonicals.** The `compile_from_canonicals` method is the primary compilation entry point. It: (1) retrieves source canonicals from the CanonicalStore (if `source_canonical_ids` is None, retrieves all canonicals in the specified domain up to `config.max_source_canonicals`), (2) assembles a compilation prompt that includes the canonical content, the topic, and a structured output template, (3) calls the synthesis model slot via ModelProvider to produce a compiled knowledge artifact, (4) runs structural validation (deterministic Python checks per §9.1 Stage 1), (5) stores the compiled artifact in KnowledgeStore with state="COMPILED", (6) records a trace event with `node_type="knowledge_compiler"`. The compilation prompt instructs the model to synthesize the source canonicals into a structured knowledge artifact: a concise, cross-referenced summary that preserves the provenance of each claim. This is the "Wiki/Codex" functionality from Appendix D — compiled knowledge is structured and cross-referenced, not just concatenated.

**compile_domain_summary.** The `compile_domain_summary` method produces a domain-level summary: a single compiled knowledge artifact that summarizes the current state of knowledge in a domain. This is useful for onboarding new projects and for Vigil's health checks — a domain summary provides a high-level view of what the system knows about a domain. The method retrieves all approved canonicals in the domain, synthesizes them into a structured summary, and stores the result in KnowledgeStore.

**compile_cross_reference.** The `compile_cross_reference` method produces cross-references for a compiled knowledge artifact: links to other compiled knowledge and canonicals in related domains. This is the "cross-referenced" part of the Wiki/Codex functionality — compiled knowledge is not isolated but connected to related knowledge through explicit cross-references. The method: (1) retrieves the compiled artifact, (2) identifies related domains and topics through VectorStore similarity search, (3) produces a cross-reference artifact that links the compiled knowledge to related entries, (4) stores the cross-reference in KnowledgeStore as metadata on the original artifact.

**evaluate_compiled.** The `evaluate_compiled` method runs faithfulness and domain coherence evaluation on a compiled knowledge artifact, analogous to `CanonicalPipeline.evaluate_for_promotion`. It: (1) retrieves the compiled artifact and its source canonicals, (2) runs faithfulness evaluation against the source canonicals, (3) runs domain coherence evaluation, (4) returns the evaluation scores. If the scores are above the configured thresholds, the artifact is a candidate for approval (transition from COMPILED to REVIEWED). If not, the artifact is transitioned to FAILED with the failure reason recorded.

**list_compilation_candidates.** The `list_compilation_candidates` method returns canonical artifacts that would benefit from compilation: domains with many canonicals that have no corresponding compiled knowledge, or canonicals that have been recently updated since their last compilation.

**Knowledge compiler run.** The `run` method is called on cadence (by Beast or a cron scheduler). It: (1) queries the KnowledgeStore for domains without recent compilations, (2) for each such domain, calls `compile_domain_summary`, (3) evaluates each compilation, (4) records compilation metrics in trace_events, (5) respects the budget system — compilation consumes tokens from the synthesis and evaluation model slots, and the BudgetManager gates the compilation if the budget is exhausted.

**Integration with existing components.** The knowledge compiler composes with Vigil (Vigil detects stale canonicals, compiler re-compiles), with Sexton (compilation failures are classified by Sexton), with the canonical pipeline (compiled knowledge references canonicals), and with the workflow engine (compilation can be triggered by the `on_canonical_stale` workflow trigger from Phase 7's WorkflowTemplate). Per §1.8, compilation criteria carry `model_gen_assumption` — the compilation process assumes the synthesis model can produce structured summaries from multiple sources, and this assumption should be audited when model slots change.

The gate test verifies: (a) `compile_from_canonicals` produces a compiled knowledge artifact, (b) `compile_domain_summary` produces a domain summary, (c) `compile_cross_reference` produces cross-references, (d) `evaluate_compiled` runs faithfulness and coherence checks, (e) compilation state transitions work (COMPILED→REVIEWED→APPROVED, COMPILED→FAILED), (f) provenance is recorded in KnowledgeStore, (g) compiled knowledge is indexed in VectorStore and LexicalStore after approval, (h) budget is respected — compilation stops when budget is exhausted, (i) trace events are written for each compilation step, (j) the compiler does not modify canonical artifacts (read-only on CanonicalStore), (k) orchestration layer imports follow boundary rules.

---

## CHUNK-10.2: Plugin Architecture

```
CHUNK-10.2: Plugin Architecture
PHASE: 8
DEPENDS-ON: CHUNK-10.0b, CHUNK-5.0b, CHUNK-8.1
CODER-PROFILE: L3
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  orchestration/plugins.py
  adapter/cli/plugins.py
  adapter/api/plugins.py
  tests/test_plugin_manager.py
INTERFACES:
  class PluginManager:
      def __init__(self, config: PluginConfig, plugin_loader: PluginLoader, model_slot_resolver: ModelSlotResolver, adaptive_router: AdaptiveRouter | None = None) -> None: ...
      def register_plugin(self, plugin: PluginProvider) -> None: ...
      def unregister_plugin(self, slot_name: str, provider_name: str) -> None: ...
      def get_plugin(self, slot_name: str) -> PluginProvider | None: ...
      def list_plugins(self) -> list[dict]: ...
      async def health_check_all(self) -> dict: ...
  # CLI additions
  aip plugin list
  aip plugin enable <slot_name> <config_path>
  aip plugin disable <slot_name>
  aip plugin health
  # API additions
  GET  /api/v1/plugins              → list plugins
  POST /api/v1/plugins/enable       → enable plugin
  POST /api/v1/plugins/disable      → disable plugin
  GET  /api/v1/plugins/health       → health check all plugins
TESTS:
  tests/test_plugin_manager.py
GATE: uv run pytest tests/test_plugin_manager.py -xvs
```

### Prose

This chunk implements the plugin architecture that makes the model provider system extensible. The four named slots from §4.1 (synthesis/evaluation/sexton/embedding) are the 0.1 default, configured through `aip.config.toml`. Phase 8 delivers the plugin framework that allows adding custom model providers without code changes — only a YAML configuration file and an environment variable for the API key.

**PluginManager.** The `PluginManager` is an orchestration component that manages the lifecycle of plugin-provided model providers. The `register_plugin` method: (1) validates the plugin's slot name and provider name, (2) registers the `PluginProvider` with the `ModelSlotResolver` so that model calls to the specified slot are routed to the plugin, (3) if an `AdaptiveRouter` is available, registers the plugin as a routing option for the slot's domain, (4) records the plugin registration in a trace event. The `unregister_plugin` method removes the plugin from the resolver and router. The `get_plugin` method returns the plugin for a given slot. The `list_plugins` method returns metadata for all loaded plugins. The `health_check_all` method calls each plugin's `health_check` and returns a consolidated status dict.

**Integration with ModelSlotResolver.** The `ModelSlotResolver` from Phase 3 resolves named slots (e.g., "synthesis") to provider/model pairs from configuration. The PluginManager extends this by allowing plugins to override or supplement the configuration-based resolution. When a plugin is registered for a slot, the PluginManager wraps the plugin's `call_model` method as a `ModelProvider` and registers it with the resolver. This means existing code that uses `model_slot_resolver.resolve("synthesis")` automatically gets the plugin-provided model — no code changes needed.

**Integration with AdaptiveRouter.** If the `AdaptiveRouter` from Phase 5 is available, the PluginManager registers the plugin as a routing option. The router can then route requests to the plugin-provided model based on domain weights and exploration probability. This is the natural extension of the routing system — new model providers become routing options without code changes.

**CLI plugin management.** The `aip plugin list` command shows all loaded plugins with their slot names, provider names, and status. The `aip plugin enable` command loads a plugin from a YAML configuration file and registers it. The `aip plugin disable` command unregisters and unloads a plugin. The `aip plugin health` command runs health checks on all loaded plugins.

**API plugin management.** The REST API exposes the same plugin management operations as the CLI. The enable and disable endpoints require DEFINER authentication (admin-level autonomy) — per §1.7, changing model configuration is a DEFINER authority.

**Sandbox mode.** When `config.sandbox_mode` is True, the PluginManager wraps each plugin's `call_model` in a try/except block. If the plugin raises an exception, the error is caught, logged to trace_events with `node_type="plugin"` and `failure_type="C"` (Output Malformation — the plugin produced an error instead of a valid response), and the plugin is disabled gracefully. The DEFINER is notified through the admin console. The request is then routed to the fallback model provider for the slot. This ensures a misbehaving plugin cannot crash the AIP process or cause data loss.

The gate test verifies: (a) `PluginManager.register_plugin` registers with ModelSlotResolver, (b) `PluginManager.unregister_plugin` removes from resolver, (c) `get_plugin` returns the correct plugin, (d) `list_plugins` returns all loaded plugins, (e) `health_check_all` checks all plugins, (f) sandbox mode catches plugin errors without crashing, (g) CLI commands work for plugin management, (h) API endpoints work for plugin management, (i) API endpoints require DEFINER auth for enable/disable, (j) plugin routing integrates with AdaptiveRouter, (k) no hardcoded model names in plugin code.

---

## CHUNK-10.3: Collaborator Access

```
CHUNK-10.3: Collaborator Access
PHASE: 8
DEPENDS-ON: CHUNK-10.0b, CHUNK-9.0b
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  adapter/auth/collaborator.py
  adapter/api/collaborators.py
  adapter/cli/collaborators.py
  tests/test_collaborator_access.py
INTERFACES:
  class CollaboratorManager:
      def __init__(self, auth_store: AuthStore, config: CollaboratorConfig, autonomy_gate: AutonomyGate) -> None: ...
      async def create_collaborator(self, identity: str, role: CollaboratorRole, password: str) -> dict: ...
      async def update_role(self, identity: str, new_role: CollaboratorRole, requested_by: str) -> dict: ...
      async def revoke_collaborator(self, identity: str, requested_by: str) -> dict: ...
      async def list_collaborators(self) -> list[dict]: ...
  # API additions
  GET    /api/v1/collaborators              → list collaborators
  POST   /api/v1/collaborators              → create collaborator
  PUT    /api/v1/collaborators/{identity}    → update role
  DELETE /api/v1/collaborators/{identity}    → revoke collaborator
  # CLI additions
  aip collaborator list
  aip collaborator add <identity> --role <role>
  aip collaborator update <identity> --role <role>
  aip collaborator remove <identity>
TESTS:
  tests/test_collaborator_access.py
GATE: uv run pytest tests/test_collaborator_access.py tests/test_layering.py -xvs
```

### Prose

This chunk extends the Phase 7 authentication system to support collaborator and readonly users alongside the DEFINER. The key architectural constraint: collaborators never bypass DEFINER sovereignty (§1.7). The DEFINER remains the sole authority over canonical promotion, configuration changes, autonomy escalation, and project termination. Collaborators can create draft artifacts and submit them for review, but they cannot approve, promote, or modify configuration. Readonly users can search the corpus and view artifacts but cannot create or modify anything.

**CollaboratorManager.** The `CollaboratorManager` is an adapter-layer component that manages collaborator identities through the `AuthStore` Protocol. The `create_collaborator` method: (1) checks that `config.enabled` is True, (2) checks that the current collaborator count is below `config.max_collaborators`, (3) asserts that `role` is not "definer" (the DEFINER role can only be set through configuration), (4) hashes the password with bcrypt, (5) calls `auth_store.create_user` with the identity, role, and password hash, (6) returns a dict with the collaborator's identity, role, and creation status. The `update_role` method changes a collaborator's role, but cannot change the DEFINER's role. The `revoke_collaborator` method removes a collaborator and revokes all their sessions and API keys. The `list_collaborators` method returns all non-DEFINER users.

**Role-based access enforcement.** The existing `AuthMiddleware` from Phase 7 is extended to support the new roles. The `require_definer` FastAPI dependency now rejects "collaborator" and "readonly" roles for admin-level operations. A new `require_collaborator_or_above` dependency allows "definer" and "collaborator" roles but rejects "readonly" for write operations. The `get_current_identity` dependency allows all authenticated users for read operations.

**Collaborator permissions.** When `config.collaborator_can_create_drafts` is True, collaborators can create artifacts in SPECIFIED or GENERATED state, but they cannot transition artifacts to REVIEWED (only the DEFINER can submit for review) unless `config.collaborator_can_submit_review` is True. When `config.collaborator_can_approve` is True (default False), collaborators can approve artifacts — but this is strongly discouraged per §1.7. The AutonomyGate enforces these constraints at the protocol level regardless of the config settings: the gate checks the authenticated role before allowing state transitions.

**API endpoints.** The collaborator API endpoints require DEFINER authentication — only the DEFINER can manage collaborator accounts. The create, update, and delete operations are admin-level autonomy gate actions. The list endpoint requires at least collaborator-level access.

**When collaborator access is disabled.** When `config.enabled` is False (the default for the laptop-viable profile), the system behaves exactly as Phase 7 — every request is treated as the DEFINER. Enabling collaborator access is a configuration change that takes effect on restart. This follows the §1.8 toggleable pattern.

The gate test verifies: (a) `CollaboratorManager.create_collaborator` creates a collaborator user, (b) collaborators cannot be created with "definer" role, (c) `update_role` changes collaborator roles but not DEFINER, (d) `revoke_collaborator` removes user and their sessions, (e) collaborator count is limited by `max_collaborators`, (f) `require_definer` rejects collaborator and readonly roles, (g) `require_collaborator_or_above` allows definer and collaborator, (h) collaborators can create drafts when config allows, (i) collaborators cannot approve when config disallows, (j) readonly users can search when config allows, (k) AutonomyGate enforces role-based constraints, (l) adapter layer does not import orchestration.

---

## CHUNK-10.4: Performance Optimization

```
CHUNK-10.4: Performance Optimization
PHASE: 8
DEPENDS-ON: CHUNK-10.0b, CHUNK-9.0c
CODER-PROFILE: L3
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  orchestration/perf.py
  adapter/api/performance.py
  tests/test_performance.py
  tests/benchmarks/bench_retrieval.py
  tests/benchmarks/bench_vectorstore.py
  tests/benchmarks/bench_memory.py
INTERFACES:
  class PerformanceProfiler:
      def __init__(self, config: PerformanceConfig, trace_store: TraceStore) -> None: ...
      async def profile_operation(self, operation_name: str, operation: callable) -> dict: ...
      async def get_system_metrics(self) -> dict: ...
      async def get_slow_operations(self, threshold_ms: int = 1000) -> list[dict]: ...
      async def get_memory_usage(self) -> dict: ...
  # API additions
  GET /api/v1/performance/metrics      → system metrics
  GET /api/v1/performance/slow         → slow operations
  GET /api/v1/performance/memory       → memory usage
TESTS:
  tests/test_performance.py
  tests/benchmarks/bench_retrieval.py
  tests/benchmarks/bench_vectorstore.py
  tests/benchmarks/bench_memory.py
GATE: uv run pytest tests/test_performance.py tests/benchmarks/ -xvs
```

### Prose

This chunk delivers performance optimization for AIP 0.1, addressing the §2.1 laptop-viable requirement that "AIP 0.1 must run on ordinary laptops, including machines with 4–6 GB RAM." No prior phase has benchmarked the actual memory and performance footprint. Phase 7 delivers rate limiting but no optimization. Phase 8 delivers the profiling infrastructure and the optimizations that ensure AIP 0.1 meets its performance targets.

**PerformanceProfiler.** The `PerformanceProfiler` is an orchestration component that provides performance profiling and metrics. The `profile_operation` method wraps an async callable, measures its execution time, records the result in trace_events, and returns a dict with the operation name, duration_ms, success status, and any error details. This is the profiling hook that the performance optimization uses to measure the critical path. The `get_system_metrics` method returns current system metrics: CPU usage, memory usage, active sessions, database sizes, and model slot health. The `get_slow_operations` method queries trace_events for operations exceeding the threshold. The `get_memory_usage` method returns a detailed breakdown of memory usage by component.

**Critical path optimization.** The critical path in AIP is: retrieve → synthesize → validate. The performance optimization focuses on reducing latency at each stage:

1. **Retrieval optimization.** The retrieval path (`retrieve_for_synthesis`) is optimized by: (a) tuning the four-factor reranking weights (α/β/γ/δ from §8.3) based on empirical performance data, (b) setting `vector_query_limit` and `fts5_query_limit` from `PerformanceConfig` to bound the initial retrieval set, (c) using batch embedding for multi-query retrieval, (d) caching frequently-retrieved results within explicit TTL (per §7.3 anti-token-burn doctrine). The `retrieval_timeout_seconds` from config sets an upper bound on retrieval time — if retrieval exceeds the timeout, the system falls back to a reduced result set rather than hanging.

2. **VectorStore query optimization.** The pgvector HNSW index parameters (ef_construction, m) are tuned based on dataset size. For the laptop-viable profile, the parameters are conservative (lower m for smaller memory footprint). For the production profile, the parameters are tuned for throughput. The `SqliteVssVectorStore` uses optimized query parameters for the sqlite_vss extension. The `batch_embed_size` from config controls the batch size for embedding operations — larger batches are more efficient but consume more memory.

3. **SQLite WAL mode.** The `sqlite_wal_mode` flag from `PerformanceConfig` enables Write-Ahead Logging for all SQLite databases. WAL mode allows concurrent readers and a single writer, which is critical for the multi-surface concurrent access that Phase 6 introduced (chat + review + Beast cadence + MCP). The `sqlite_busy_timeout_ms` sets the busy timeout — when a write is blocked by another write, the waiting connection retries for up to the timeout before failing. The default of 5000ms is tuned for the laptop-viable profile.

4. **Memory footprint.** The `get_memory_usage` method tracks memory usage by component: VectorStore index size, LexicalStore FTS5 index size, active session contexts, model provider connection pools, and compiled knowledge cache. The `max_memory_mb` from config is the target — if memory usage exceeds this target, the profiler logs a warning and suggests optimizations (e.g., reducing vector dimensions, pruning stale indexes, compacting SQLite databases).

**Benchmark suite.** The benchmark tests are in `tests/benchmarks/`. They are deterministic (no network, no API keys) and measure: (a) retrieval latency for 100/1000/10000 documents, (b) VectorStore query latency for different result sizes, (c) memory footprint for the laptop-viable profile. The benchmarks use synthetic data generated by fixtures. They are not gate tests in the strict sense — they produce performance metrics that are recorded in the test output. However, the `test_performance.py` gate test asserts that: (1) retrieval completes within `retrieval_timeout_seconds`, (2) memory usage is below `max_memory_mb`, (3) SQLite WAL mode is enabled, (4) batch embedding produces correct results.

**Performance API.** The `/api/v1/performance/` endpoints are admin-level (require DEFINER authentication). They expose the profiler's metrics for the admin console and web UI. The metrics are read-only — performance tuning is done through configuration, not through the API.

The gate test verifies: (a) `PerformanceProfiler.profile_operation` measures operation time, (b) `get_system_metrics` returns system metrics, (c) `get_slow_operations` returns operations exceeding threshold, (d) `get_memory_usage` returns memory breakdown, (e) retrieval completes within timeout, (f) batch embedding works correctly, (g) SQLite WAL mode is enabled, (h) memory usage is reported, (i) benchmarks produce metrics, (j) performance API endpoints work with DEFINER auth.

---

## CHUNK-10.5: Stabilization & Edge Case Hardening

```
CHUNK-10.5: Stabilization & Edge Case Hardening
PHASE: 8
DEPENDS-ON: CHUNK-10.1, CHUNK-10.2, CHUNK-10.3, CHUNK-10.4, CHUNK-9.5
CODER-PROFILE: L3
CONTEXT-BUDGET: ~6,000 tokens
FILES:
  orchestration/recovery.py
  adapter/db/sqlite_concurrency.py
  tests/test_stabilization.py
  tests/test_edge_cases.py
INTERFACES:
  class WorkflowRecovery:
      async def recover_interrupted_workflow(self, session_id: str) -> dict: ...
      async def get_interrupted_workflows(self) -> list[dict]: ...
      async def checkpoint_workflow(self, session_id: str, node_id: str, state: dict) -> None: ...
  class SqliteConcurrencyManager:
      def __init__(self, db_paths: list[str], config: PerformanceConfig) -> None: ...
      async def initialize_all(self) -> None: ...
      async def check_all_health(self) -> dict: ...
      def get_connection(self, db_path: str) -> aiosqlite.Connection: ...
TESTS:
  tests/test_stabilization.py
  tests/test_edge_cases.py
GATE: uv run pytest tests/test_stabilization.py tests/test_edge_cases.py -xvs
```

### Prose

This chunk delivers stabilization fixes and edge case hardening for the issues that Phase 7's acceptance tests revealed, plus the issues that the new Phase 8 features (knowledge compiler, plugins, collaborators, performance optimization) introduce. This is the "real-world hardening" phase that turns "passing acceptance tests" into "production-ready software."

**WorkflowRecovery.** The `WorkflowRecovery` class handles interrupted workflows — a critical edge case that no prior phase has addressed. When the AIP process crashes or is restarted, any running workflow is left in an indeterminate state. The `checkpoint_workflow` method writes the current workflow state (current node ID, inputs, outputs, and ECS states of artifacts) to a checkpoints table in state.db. The `recover_interrupted_workflow` method: (1) queries the checkpoints table for the session, (2) verifies that all prior node outputs still exist, (3) resumes the workflow from the last completed node, (4) records the recovery in trace_events. The `get_interrupted_workflows` method returns all sessions with incomplete checkpoints.

**SqliteConcurrencyManager.** The `SqliteConcurrencyManager` centralizes SQLite connection management for all databases (state.db, events.db, trace.db, and any adapter-specific databases). It: (1) enables WAL mode on all databases during `initialize_all`, (2) sets the busy timeout from `PerformanceConfig.sqlite_busy_timeout_ms`, (3) provides connection pooling through `aiosqlite`, (4) runs periodic integrity checks via `check_all_health`, (5) handles the "database is locked" error by retrying with exponential backoff. This addresses the concurrency issues that the Phase 7 acceptance tests likely revealed: when chat, Beast cadence, and MCP all write to state.db simultaneously, SQLite can return "database is locked" errors without WAL mode and proper busy timeout handling.

**Edge case handling.** The edge case test file covers scenarios that the acceptance tests may not have exercised:

1. **Empty retrieval results.** When the VectorStore and LexicalStore have no matching results, the system must return `INSUFFICIENT_MEMORY` (per §8.2) rather than crashing or returning an empty synthesis.

2. **Concurrent ECS transitions.** When two surfaces attempt to transition the same artifact simultaneously (e.g., chat and review queue both approving the same artifact), the second transition must fail gracefully with `InvalidTransitionError`, not corrupt the state.

3. **Model provider timeout.** When a model provider (API-based or plugin) times out, the system must: (a) log a trace event with `failure_type` and `outcome="timeout"`, (b) attempt the fallback model slot if one is configured, (c) return a meaningful error to the surface, not hang indefinitely.

4. **Database corruption recovery.** When SQLite detects corruption (via `PRAGMA integrity_check`), the system must: (a) log a critical trace event, (b) attempt to recover from the last backup (using the backup scripts from Phase 7's CHUNK-9.6), (c) surface the issue to the DEFINER through the admin console, (d) not silently continue with corrupted data.

5. **Budget exhaustion mid-workflow.** When the BudgetManager rejects a model call that is in the middle of a multi-node workflow, the workflow must be paused (not aborted) and the DEFINER notified. The workflow can be resumed when the budget resets (daily reset or DEFINER increase).

6. **Plugin failure during compilation.** When a plugin-provided model fails during knowledge compilation, the compilation must be aborted and the artifact left in the COMPILED state (not FAILED — it may succeed on retry with the default model slot). A trace event is recorded for Sexton to classify.

7. **Idempotency.** All state-changing operations must be idempotent: promoting an already-canonical artifact is a no-op, compiling an already-compiled knowledge artifact with the same source canonicals is a no-op, approving an already-approved artifact is a no-op. This prevents double-processing from retries and concurrent access.

The gate test verifies: (a) `WorkflowRecovery.recover_interrupted_workflow` resumes from the last checkpoint, (b) `SqliteConcurrencyManager` enables WAL mode on all databases, (c) concurrent writes do not cause "database is locked" errors with WAL mode, (d) empty retrieval returns INSUFFICIENT_MEMORY, (e) concurrent ECS transitions fail gracefully, (f) model provider timeout is handled, (g) budget exhaustion pauses workflow, (h) plugin failure during compilation is handled, (i) idempotent operations are no-ops on repeat calls, (j) all edge cases produce meaningful error messages.

---

## CHUNK-10.6: Documentation & Release Preparation

```
CHUNK-10.6: Documentation & Release Preparation
PHASE: 8
DEPENDS-ON: CHUNK-10.5
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  docs/README.md
  docs/DEVELOPER_GUIDE.md
  docs/API_REFERENCE.md
  docs/DEPLOYMENT_GUIDE.md
  docs/ARCHITECTURE.md
  docs/CONFIGURATION.md
  docs/CHANGELOG.md
  tests/test_documentation.py
INTERFACES:
  # No code interfaces — documentation only
TESTS:
  tests/test_documentation.py
GATE: uv run pytest tests/test_documentation.py -xvs
```

### Prose

This chunk delivers the comprehensive documentation that makes AIP 0.1 accessible to alpha testers and new developers. No prior phase has delivered documentation beyond inline code comments and the architecture specification itself. The §2.3 installation contract (`uv sync; uv run aip init; uv run aip status`) is the first user experience, and the documentation must guide users through it successfully.

**README.md.** The top-level README is the entry point for anyone discovering AIP. It covers: what AIP is (an AI-assisted knowledge synthesis system with sovereign DEFINER control), the quick start (§2.3 installation contract), the architecture overview (three layers, core doctrines), and links to the detailed documentation. The README is designed to be read in under 5 minutes and to give the reader enough context to decide whether to invest further time.

**DEVELOPER_GUIDE.md.** The developer guide covers: setting up the development environment, the three-layer architecture and import boundary rules, how to add a new Protocol and adapter, how to add a new workflow YAML template, how to add a new model provider plugin, the test suite structure and deterministic CI requirements, the ECS state machine and artifact lifecycle, the §1.8 harness evolution principle, and the contributor workflow. This is the onboarding document for developers who want to contribute to AIP.

**API_REFERENCE.md.** The API reference documents every REST API endpoint: the URL, method, request body, response body, authentication requirements, autonomy gate requirements, and example requests/responses. It is generated from the FastAPI OpenAPI schema but also includes the conceptual documentation that OpenAPI cannot capture (e.g., why the approve endpoint requires DEFINER authentication, what the ECS state transitions mean, how the canonical pipeline works). The API reference also documents the CLI commands (`aip init`, `aip status`, `aip config`, `aip project`, `aip session`, `aip plugin`, `aip collaborator`), the MCP tools (`aip_search`, `aip_project_list`, `aip_artifact_approve`, `aip_trace_query`, `aip_config_read/write`), and the WebSocket chat protocol.

**DEPLOYMENT_GUIDE.md.** The deployment guide documents the two deployment profiles (laptop-viable and production), the Docker Compose setup, the configuration file reference, the backup and restore procedures, the health check endpoints, and the monitoring and observability options. It includes troubleshooting for common issues (Ollama not running, pgvector not installed, memory pressure, database locked errors).

**ARCHITECTURE.md.** The architecture walkthrough is a narrative guide to AIP 0.1 Architecture Rev 5.2. It explains the core doctrines (§1.1–§1.8), the layer model (§3), the model abstraction layer (§4), the persistence architecture (§5), the storage abstraction contracts (§6), the L1–L6 harness layers (§7–§11), the actors (§16), and the failure taxonomy (Appendix E). It is designed to be read alongside the architecture specification and to provide the "why" behind each design decision.

**CONFIGURATION.md.** The configuration reference documents every configuration section in `aip.config.toml`: models, vector_backend, api, cli, mcp, chat, autonomy, lexical, sexton, ace_playbook, router, budget, beast, vigil, auth, rate_limit, canonical_pipeline, deployment, knowledge, plugins, collaborator, performance, and release. Each section includes the parameter names, types, defaults, and the architectural rationale (which § section it maps to, which §1.8 model_gen_assumption it carries).

**CHANGELOG.md.** The changelog documents every build phase (1–8), the chunks delivered, the key features added, and any breaking changes. It is the historical record of how AIP 0.1 was built.

**Test strategy.** The gate test verifies: (a) all documentation files exist, (b) all documentation files have non-trivial content (> 100 words), (c) the README includes the quick start commands, (d) the API reference includes all documented endpoints, (e) the deployment guide includes both profiles, (f) the configuration reference includes all config sections, (g) no documentation file contains placeholder text (e.g., "TODO", "FIXME", "TBD"), (h) internal links between documentation files are valid.

---

## CHUNK-10.7: Release Verification

```
CHUNK-10.7: Release Verification
PHASE: 8
DEPENDS-ON: CHUNK-10.5, CHUNK-10.6, CHUNK-9.5
CODER-PROFILE: L3
CONTEXT-BUDGET: ~8,000 tokens
FILES:
  tests/release/test_release_gates.py
  tests/release/test_knowledge_compilation_e2e.py
  tests/release/test_plugin_integration.py
  tests/release/test_collaborator_access_e2e.py
  tests/release/test_performance_gates.py
  tests/release/test_regression_phase1_through_7.py
TESTS:
  tests/release/
GATE: uv run pytest tests/release/ -xvs
```

### Prose

This chunk delivers the final release verification that confirms AIP 0.1 is ready for release. Phase 7's acceptance tests (CHUNK-9.5) verified the base system against the §22 acceptance criteria. Phase 8's release verification adds verification for the new Phase 8 features and re-verifies that Phase 8 changes did not regress any Phase 1–7 acceptance criteria. This is the final gate before AIP 0.1 is declared "released."

**test_release_gates.** This test verifies the §22 acceptance gates in the context of the complete system including Phase 8 features: (1) knowledge compilation produces structured, retrievable compiled knowledge, (2) plugins can be loaded and used as model providers, (3) collaborator access is enforced across all surfaces, (4) performance meets the §2.1 laptop-viable requirements, (5) all documentation is complete and valid, (6) the `ReleaseMetadata` dataclass is populated with the correct version, date, and gate results.

**test_knowledge_compilation_e2e.** This test exercises the full knowledge compilation lifecycle: (1) create a project with multiple canonical artifacts in a domain, (2) run `compile_domain_summary` to produce a compiled knowledge artifact, (3) evaluate the compiled artifact, (4) approve the compiled artifact through the DEFINER gate, (5) verify the compiled artifact is indexed in VectorStore and LexicalStore, (6) verify the compiled artifact's provenance links to the source canonicals, (7) verify Vigil detects the compiled artifact as healthy, (8) modify a source canonical, (9) verify Vigil detects the compiled artifact as potentially stale, (10) re-compile the knowledge.

**test_plugin_integration.** This test exercises the plugin architecture: (1) create a YAML plugin configuration for a mock model provider, (2) load the plugin via the PluginManager, (3) verify the plugin is registered with the ModelSlotResolver, (4) make a model call that routes to the plugin, (5) verify the response is correct, (6) disable the plugin, (7) verify the model call falls back to the default provider, (8) test sandbox mode by loading a plugin that raises an exception, (9) verify the error is caught and the plugin is disabled gracefully.

**test_collaborator_access_e2e.** This test exercises the collaborator access system: (1) create a collaborator user, (2) authenticate as the collaborator, (3) verify the collaborator can create a draft artifact, (4) verify the collaborator cannot approve artifacts, (5) verify the collaborator cannot modify configuration, (6) verify the collaborator cannot access admin endpoints, (7) create a readonly user, (8) verify the readonly user can search the corpus, (9) verify the readonly user cannot create artifacts, (10) revoke the collaborator, (11) verify the collaborator's sessions are invalidated.

**test_performance_gates.** This test verifies the performance requirements from §2.1: (1) the system starts within 30 seconds, (2) the `aip init` command completes within 60 seconds, (3) retrieval completes within `retrieval_timeout_seconds`, (4) memory usage is below `max_memory_mb` after initialization, (5) concurrent access (5 simultaneous requests) does not cause errors, (6) the performance metrics are accessible through the API.

**test_regression_phase1_through_7.** This test re-runs the critical Phase 1–7 acceptance tests to verify that Phase 8 changes did not introduce regressions: (1) the ECS lifecycle still works end-to-end, (2) DEFINER sovereignty is still enforced, (3) budget enforcement still works, (4) Vigil health monitoring still works, (5) the canonical pipeline still promotes artifacts correctly, (6) all surfaces (CLI, API, chat, review, MCP) still work, (7) all cross-cutting gates still pass. This is the regression safety net.

**ReleaseMetadata.** When all release tests pass, the test produces a `ReleaseMetadata` dataclass instance with the release version ("0.1.0"), the release date, the release status ("alpha"), the architecture revision ("5.2"), the list of §22 acceptance gates that passed, the known limitations (e.g., "single-DEFINER with optional collaborators", "no multi-tenant isolation", "no custom workflow node plugins"), and breaking changes (none for 0.1.0 since this is the initial release). This metadata is the release manifest.

The gate test runs all release tests in sequence. If any test fails, the entire gate fails — AIP 0.1 is not released until all criteria are met. The release tests use the FastAPI TestClient and Click CliRunner, with all model calls in CI mode (deterministic fixtures, no network). The tests are designed to be run in CI and to produce clear, actionable failure messages.

---

## CHUNK-10.8: Cross-Cutting Gates

```
CHUNK-10.8: Cross-Cutting Gates
PHASE: 8
DEPENDS-ON: CHUNK-10.7
CODER-PROFILE: L1
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  tests/test_phase8_network_isolation.py
  tests/test_phase8_model_name_gate.py
  tests/test_phase8_import_boundaries.py
  tests/test_phase8_definer_sovereignty.py
  tests/test_phase8_appendix_d.py
  tests/test_phase8_plugin_isolation.py
  tests/test_phase8_auth_bypass.py
TESTS:
  tests/test_phase8_network_isolation.py
  tests/test_phase8_model_name_gate.py
  tests/test_phase8_import_boundaries.py
  tests/test_phase8_definer_sovereignty.py
  tests/test_phase8_appendix_d.py
  tests/test_phase8_plugin_isolation.py
  tests/test_phase8_auth_bypass.py
GATE: uv run pytest tests/test_phase8_*.py -xvs
NOTE: The exact gate commands and file layout for these cross-cutting tests will be confirmed during the pre-10.8 Continuity Check against the delivered 9.7 pattern and the as-built test directory structure.
```

### Prose

This chunk delivers the Phase 8 cross-cutting gate tests that extend the Phase 7 gate (CHUNK-9.7) to cover all Phase 8 components. These gates are the final verification that the complete AIP 0.1 system — including knowledge compilation, plugins, collaborators, and performance optimization — respects the architectural invariants that have been enforced throughout the build.

**Network isolation.** Verifies that no Phase 8 component makes network calls in CI mode: (1) knowledge compiler runs without network access, (2) plugin loader runs without network access in CI mode, (3) collaborator access runs without network access, (4) performance profiler runs without network access, (5) SqliteConcurrencyManager runs without network access, (6) documentation has no network dependencies.

**Model name gate.** Verifies that no hardcoded model names appear in any `orchestration/` or `foundation/` file added in Phase 8: (1) knowledge compiler references model slots by name only, (2) plugin loader references model names from configuration only, (3) plugin YAML files are in `plugins/` directory (not in `orchestration/` or `foundation/`), (4) performance profiler references model slots by name only.

**Import boundaries.** Verifies that Phase 8 code respects the three-layer import boundaries: (1) `adapter/knowledge/` does not import orchestration, (2) `adapter/plugins/` does not import orchestration, (3) `adapter/auth/collaborator.py` does not import orchestration, (4) `orchestration/compilation.py` does not import adapter implementations directly, (5) `orchestration/plugins.py` does not import adapter implementations directly (imports PluginLoader Protocol), (6) `orchestration/perf.py` does not import adapter implementations directly, (7) documentation files are not Python and are exempt.

**DEFINER sovereignty.** Verifies that Phase 8 components enforce DEFINER sovereignty across all roles: (1) knowledge compiler does not autonomously approve compiled knowledge (DEFINER must approve), (2) plugins cannot bypass the AutonomyGate, (3) collaborators cannot approve artifacts (unless config explicitly allows), (4) collaborators cannot modify configuration, (5) readonly users cannot create or modify anything, (6) plugin enable/disable requires DEFINER authentication, (7) collaborator management requires DEFINER authentication.

**Appendix D constraints.** Verifies Phase 8 compliance with Appendix D, including the resolved non-collapse rule: (1) "Deferred compiled knowledge reservation ≠ implemented Wiki/Codex/Vigil" — Phase 8 resolves this by implementing the KnowledgeStore and KnowledgeCompiler, but the non-collapse rule still applies: compiled knowledge artifacts are distinct from canonical artifacts, and the KnowledgeStore is distinct from the CanonicalStore, (2) "Beast ≠ Sexton" — no change, still separate, (3) "Vigil ≠ Beast" — no change, still separate, (4) "UI ≠ authority" — the web UI relays DEFINER decisions for collaborator management but does not make autonomous decisions, (5) "MCP ≠ bypass" — MCP tools still go through Protocol layer, including collaborator role enforcement, (6) "Supersession ≠ deletion" — compiled knowledge supersession preserves the original, (7) "Harness complexity ≠ harness quality" — plugins and knowledge compilation are toggleable per §1.8.

**Plugin isolation.** Verifies that plugins are properly isolated: (1) a plugin that raises an exception does not crash AIP, (2) a plugin that returns invalid JSON does not corrupt data, (3) a plugin that times out does not block other model calls, (4) a plugin that accesses the filesystem is restricted to its configured directory, (5) plugin errors are logged to trace_events, (6) the DEFINER is notified of plugin failures through the admin console.

**Auth bypass prevention with collaborator roles.** Verifies that the auth system cannot be bypassed with the new collaborator roles: (1) a collaborator cannot escalate their role to definer, (2) a readonly user cannot escalate to collaborator, (3) modifying `request.state.auth_role` directly does not grant elevated access, (4) the DEFINER role can only be set through the configuration file, not through the API or CLI, (5) expired collaborator sessions are rejected, (6) revoked collaborator API keys are rejected, (7) collaborator access is only available when `config.enabled == True`.

The gate test runs all seven test files. If any gate fails, the Phase 8 build is blocked. These gates are the final check before AIP 0.1 is declared released — they confirm that the entire system, from Phase 1 through Phase 8, respects every architectural invariant defined in AIP 0.1 Architecture Rev 5.2.
