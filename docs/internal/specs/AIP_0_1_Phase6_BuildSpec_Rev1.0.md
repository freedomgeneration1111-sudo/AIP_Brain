# AIP 0.1 Phase 6 BuildSpec

**Product:** AI Poiesis (AIP) version 0.1  
**Architecture Revision:** 5.2  
**Build Phase:** 6 — Surfaces: CLI, REST API, Chat, Review Queue, MCP & Autonomy Gate  
**Spec Revision:** 1.0  
**Date:** May 2026  
**Status:** Build Specification — for Grok Build execution  
**Supersedes:** N/A (initial Phase 6 spec)  
**DEFINER:** Moses Jorgensen

---

## Revision Log

### 1.0 — Initial

| Item | Decision | Rationale |
|---|---|---|
| FastAPI REST API | `adapter/api/` — app factory, DI container, project/session/artifact/event endpoints |
§7.2: "Adapter — FastAPI, CLI, MCP, UI, service composition"; §3: "MCP/API" surface; all REST access composes
Foundation and Orchestration through Protocol injection |
| CLI implementation | `adapter/cli/` — `aip init`, `aip status`, `aip config`, `aip project`, `aip session` |
§2.3: "uv sync; uv run aip init; uv run aip status"; `aip init` must detect RAM, configure vector backend,
initialize DB schemas, validate Ollama, validate model slots, print summary |
| Chat surface | `adapter/api/chat.py` — WebSocket chat with session context, DEFINER gate handling | §3:
"Chat" surface; §1.3: "context is assembled from explicit stores"; §8.1: "ACE Playbook loaded at session
start"; chat is the primary DEFINER interaction point |
| Review Queue surface | `adapter/api/review.py` — browse pending reviews, approve/reject artifacts, ECS
transitions | §3: "Review Queue" surface; §9.3: "REVIEWED → APPROVED → SUPERSEDED"; §1.7: "No UI may bypass
the DEFINER gates"; review queue enforces DEFINER sovereignty over canonical promotion |
| MCP server | `adapter/mcp/` — Model Context Protocol server exposing AIP tools | §3: "MCP/API" surface;
§7.2: "MCP" in adapter; Appendix D: "MCP ≠ bypass", "MCP ≠ vector_store.retrieve() directly"; all MCP calls
must go through AutonomyGate |
| Autonomy Gate | `adapter/autonomy/autonomy_gate.py` — implements AutonomyGate Protocol from §6 | §6:
AutonomyGate Protocol listed as required; §1.7: "No UI, workflow, Beast cadence, MCP call, or queued task may
bypass the DEFINER gates"; autonomy gate is the enforcement mechanism |
| Remaining Protocol adapters | `adapter/lexical/`, `adapter/canonical/`, `adapter/entity/` — FTS5
LexicalStore, CanonicalStore, EntityStore | §6: LexicalStore, CanonicalStore, EntityStore all listed as
required Protocols; not implemented in Phases 1–5; surfaces need these for full functionality |
| Config additions | `[api]`, `[cli]`, `[mcp]`, `[chat]`, `[autonomy]`, `[lexical]` sections in
`aip.config.toml` | §1.8 toggleable; all API server settings, CLI behavior, MCP tool definitions, autonomy
escalation thresholds configurable |

---

## Phase 6 Scope

Phase 6 delivers the Surfaces layer — the adapter-layer components that make AIP 0.1 usable by the DEFINER.
This is the final architectural phase. Every previous phase built the harness, persistence, orchestration, and
actors. Phase 6 exposes them through the interaction channels defined in §3: Chat, Admin Console, Project
Console, Review Queue, Artifact Browser, Memory Inspector, and MCP/API.

Phase 5 delivered the self-improving actor layer: Sexton classifies failures and curates the ACE Playbook, the
Adaptive Router optimizes model selection, Beast maintains the corpus on cadence, and the BudgetManager
governs token consumption. But the system is only accessible through Python function calls and database
queries. Phase 6 makes AIP 0.1 a product that the DEFINER can interact with through CLI commands, a REST API,
a chat interface, a review queue, and an MCP server — all enforcing the sovereignty and import boundary rules
that the architecture demands.

Phase 6 also delivers the remaining Protocol adapter implementations that surfaces need but no prior phase
claimed: LexicalStore (FTS5), CanonicalStore, EntityStore, and the AutonomyGate. These are adapter-layer
components that the surfaces compose; they are not surfaces themselves, but without them the surfaces would
have no data to surface.

**In scope:**

- CHUNK-8.0a: Schema additions — `SurfaceConfig`, `ApiRoute`, `McpToolDef`, `AutonomyEscalation`,
`ChatMessage`, `ReviewQueueEntry` dataclasses + Protocol amendments (`AutonomyGate` Protocol from §6,
`LexicalStore` Protocol methods, `CanonicalStore` new methods, `EntityStore` new methods) + Config extensions
(L1, append-only)
- CHUNK-8.0b: Remaining Protocol adapter implementations — `SqliteFts5LexicalStore`, `SqliteCanonicalStore`,
`SqliteEntityStore`, `AutonomyGateImpl` (adapter layer, composes Foundation Protocols)
- CHUNK-8.1: FastAPI application scaffold + Project & Session REST API — app factory, DI container,
middleware, CORS, health endpoint, project CRUD, session CRUD, WorkUnit listing (adapter)
- CHUNK-8.2: CLI implementation — `aip init` (§2.3), `aip status`, `aip config`, `aip project`, `aip session`
using Click/Typer (adapter)
- CHUNK-8.3: Chat surface — WebSocket chat with session context, DEFINER gate handling, ACE playbook loading,
multi-turn conversation (adapter)
- CHUNK-8.4: Review Queue + Artifact Browser API — review queue browse/filter, approve/reject, ECS
transitions, artifact listing/versioning, DEFINER sovereignty enforcement (adapter)
- CHUNK-8.5: MCP server — Model Context Protocol server exposing AIP tools, per Appendix D constraints, all
calls through AutonomyGate (adapter)
- CHUNK-8.6: Admin Console + Memory Inspector API — config management, Sexton audit results, Beast status,
router weights, trace events, event timeline, budget status, vector inspection (adapter)
- CHUNK-8.7: Integration test — full surface-to-backend round trip: CLI init → project create → chat session →
review → approve → artifact browse → MCP query → autonomy gate check
- CHUNK-8.8: Cross-cutting gates — network isolation, model-name gate, DEFINER sovereignty gate, import
boundary verification, Appendix D constraint verification

**Out of scope:**

- Visual UI / web frontend (deferred — surfaces are API-first; a future web UI consumes the same REST API)
- Vigil actor — compiled knowledge maintenance (deferred per §3: "Vigil — compiled knowledge maintenance,
deferred")
- Additional workflows beyond Workflow 0.1
- Mobile surfaces
- Authentication / authorization beyond placeholder (AIP 0.1 is single-user DEFINER; multi-user auth is
post-0.1)
- Rate limiting / API throttling (single-user alpha; not needed yet)
- Canonical promotion workflow beyond REVIEWED→APPROVED (full canonical pipeline is post-0.1)

---

## Phase 5 Assumptions (Architectural Phase 5 = CHUNK-7.x series)

Phase 6 chunks depend on the following Phase 5 deliverables being merged and green:

| CHUNK-7.x | Deliverable | Phase 6 Dependency |
|---|---|---|
| 7.0a | `foundation/schemas.py` — `SextonConfig`, `AcePlaybookEntry`, `BudgetConfig`, `RoutingWeight`,
`BeastCadenceConfig`, `FailureClassification`, `BudgetScope` | 8.0a appends; 8.1 (API returns BudgetConfig
data); 8.6 (admin returns SextonConfig, RoutingWeight) |
| 7.0a | `foundation/protocols.py` — `BudgetStore` Protocol, `ProjectStore.list_projects` | 8.0a appends; 8.1
(API uses BudgetStore, ProjectStore); 8.0b (implements remaining Protocols) |
| 7.0b | `orchestration/budget.py` — `BudgetManager` | 8.1 (API surfaces budget status); 8.6 (memory inspector
shows budget) |
| 7.1 | `orchestration/actors/sexton.py` — `Sexton` | 8.6 (admin console shows Sexton classification results)
|
| 7.2 | `orchestration/ace_playbook.py` — `AcePlaybook` | 8.3 (chat loads playbook at session start); 8.6
(admin shows playbook entries) |
| 7.3 | `orchestration/actors/sexton_audit.py` — `SextonAudit` | 8.6 (admin shows audit results) |
| 7.4 | `orchestration/router.py` — `AdaptiveRouter` | 8.6 (admin shows routing weights) |
| 7.5 | `orchestration/actors/beast.py` — `Beast` | 8.6 (admin shows Beast status) |
| 7.6 | Integration test | 8.7 extends |
| 7.7 | Network isolation gate | 8.8 extends |

Phase 4 dependencies (transitive through Phase 5):

| CHUNK-6.x | Deliverable | Phase 6 Dependency |
|---|---|---|
| 6.0a | `foundation/schemas.py` — `PgvectorConfig`, `EvaluationScore`, `FaithfulnessResult`,
`DomainCoherenceResult`, `VectorBackendType` | 8.0a appends; 8.6 (memory inspector shows evaluation scores) |
| 6.0a | `foundation/protocols.py` — `VectorStore.health_check`, `VectorStore.count` | 8.0b (LexicalStore
similar pattern); 8.2 (`aip status` uses health_check) |
| 6.0b | `adapter/vector/pgvector_store.py` — `PgvectorStore` | 8.0b (other adapters follow same pattern) |
| 6.1 | `orchestration/nodes/synthesis.py` — promoted with ModelSlotResolver | 8.3 (chat triggers synthesis) |
| 6.2 | `orchestration/nodes/adversarial_eval.py`, `faithfulness.py`, `domain_coherence.py` | 8.4 (review
queue shows evaluation results) |
| 6.3 | `adapter/vector/factory.py` — `create_vector_store` | 8.0b (follows factory pattern); 8.1 (DI
container uses factory) |
| 6.4 | Production hardening — health checks, graceful degradation | 8.2 (`aip status` reports health); 8.1
(API health endpoint) |
| 6.5 | Integration test | 8.7 extends |
| 6.6 | Network isolation gate | 8.8 extends |

Phase 3 dependencies (transitive through Phase 4/5):

| CHUNK-5.x | Deliverable | Phase 6 Dependency |
|---|---|---|
| 5.0a | `foundation/schemas.py` — `TrajectorySignal`, `SessionContext`, `ModelSlotConfig` | 8.0a appends; 8.3
(chat uses SessionContext); 8.6 (memory inspector shows TrajectorySignals) |
| 5.0a | `foundation/protocols.py` — `TraceStore.query_events`, `ModelProvider`, `EmbeddingProvider` | 8.1
(API uses TraceStore, ModelProvider); 8.0b (follows Protocol pattern) |
| 5.0b | `adapter/model_slot_resolver.py` — `ModelSlotResolver` | 8.1 (DI container wires ModelSlotResolver);
8.5 (MCP exposes model slot info) |
| 5.2–5.5 | L4 trajectory detectors + regulator | 8.6 (memory inspector shows trajectory data) |
| 5.6 | Context reset protocol | 8.3 (chat may trigger context reset) |
| 5.7 | `orchestration/session.py` — `SessionManager` | 8.3 (chat uses SessionManager); 8.1 (API
creates/resumes sessions) |
| 5.8 | Integration test | 8.7 extends |
| 5.9 | Network isolation gate | 8.8 extends |

Phase 2 dependencies (transitive through Phase 3/4/5):

| CHUNK-4.x | Deliverable | Phase 6 Dependency |
|---|---|---|
| 4.0a | `foundation/schemas.py` — `ReviewVerdict`, `Event`, `FailureTypeCode` | 8.0a appends; 8.4 (review
queue uses ReviewVerdict) |
| 4.0b | `foundation/ecs_graph.py` — `VALID_TRANSITIONS`, `InvalidTransitionError` | 8.4 (review queue
triggers ECS transitions) |
| 4.3 | `adapter/artifact_store_versioned.py` — `VersionedArtifactStore` | 8.4 (artifact browser reads
versions) |
| 4.4 | `adapter/event_store_queryable.py` — `QueryableEventStore` | 8.6 (memory inspector queries events) |
| 4.5 | `orchestration/engine.py` — YAML workflow engine | 8.3 (chat triggers workflow execution) |
| 4.6 | `workflows/synthesis_session_v1.yaml` | 8.3 (chat runs synthesis session); 8.7 (integration test) |

Phase 1 dependencies (transitive through Phase 2/3/4/5):

| CHUNK-1.x | Deliverable | Phase 6 Dependency |
|---|---|---|
| 1.0a | `foundation/schemas.py` — `Chunk`, `ContractRule`, `RetrievalResult` | 8.0a appends; 8.6 (memory
inspector shows ContractRules) |
| 1.0a | `foundation/protocols.py` — `VectorStore`, `TraceStore`, `EcsStore`, `EventStore`, `ArtifactStore` |
8.0a appends; 8.0b (implements remaining Protocols); all surfaces use existing Protocols |
| 1.0b | `adapter/vector/sqlite_vss_store.py` — `SqliteVssVectorStore` | 8.0b (follows adapter pattern) |
| 1.1 | `orchestration/retrieval.py` — `retrieve_for_synthesis` | 8.3 (chat uses retrieval); 8.5 (MCP search
tool) |
| 1.2 | `orchestration/validation.py` — `structural_validate` | 8.4 (review shows validation results) |

Phase 0 dependencies:

| CHUNK-0.x | Deliverable | Phase 6 Dependency |
|---|---|---|
| 0.2 | `config/aip.config.toml` — base config | 8.0a extends config with [api], [cli], [mcp], [chat],
[autonomy], [lexical] sections |
| 0.3 | `db/routing_outcomes` table schema | 8.6 (memory inspector shows routing outcomes) |
| 0.5 | `db/trace_events` table schema | 8.6 (memory inspector shows trace events) |

**Critical note on CHUNK-8.0a:** This chunk appends to `foundation/schemas.py` and amends
`foundation/protocols.py` — the same append-only/amend-by-addition pattern as CHUNK-1.0a, CHUNK-4.0a,
CHUNK-5.0a, CHUNK-6.0a, and CHUNK-7.0a. No existing Phase 0, Phase 1, Phase 2, Phase 3, Phase 4, or Phase 5
code is deleted or rewritten.

**Continuity note:** All Phase 6 components are adapter-layer. Per §7.2: "Adapter may compose Foundation and
Orchestration." Every surface (CLI, API, Chat, Review, MCP) imports Foundation Protocols and schemas, and
Orchestration components (SessionManager, BudgetManager, Sexton, Beast, AdaptiveRouter, AcePlaybook). No
surface imports adapter storage implementations directly — all storage access is via Protocol injection
through the DI container. The AutonomyGate is the cross-cutting enforcement mechanism that ensures no surface
bypasses DEFINER sovereignty (§1.7).

---

## Process Rules

These rules are binding for all work against this BuildSpec. They are inherited from Phase 1 Rev 1.3, Phase 2
Rev 1.2, Phase 3 Rev 1.1, Phase 4 Rev 1.0, and Phase 5 Rev 1.0 and must be followed without exception:

1. **Continuity Check.** Before starting any chunk, read `WORKLOG.md` and verify all DEPENDS-ON chunks are
merged and green. This includes all Phase 1 (1.x), Phase 2 (4.x), Phase 3 (5.x), Phase 4 (6.x), and Phase 5
(7.x) chunks. If any dependency is not met, block and report.

2. **WORKLOG append-only.** Every chunk completion appends a new work record to `WORKLOG.md`. Never overwrite,
delete, or reorder existing entries. Each record must include Task ID, Agent, Task description, Work Log
(concrete steps), and Stage Summary.

3. **Amend by addition — schemas.** New dataclasses, type aliases, and enums are appended to
`foundation/schemas.py`. Never modify, reorder, or delete existing Phase 0/1/2/3/4/5 definitions. The test
suite verifies this by importing Phase 0/1/2/3/4/5 types and asserting they still work.

4. **Amend by addition — protocols.** New methods are appended as stubs to existing Protocol classes in
`foundation/protocols.py`. New Protocol classes (AutonomyGate, LexicalStore full definition, CanonicalStore
full definition) are added as new class definitions or appended method stubs. Never redeclare an existing
Protocol class. The ANNEX shows individual method stubs for amendments and full class blocks for new Protocols
only.

5. **Deterministic CI.** All gate tests must pass without network access, API keys, secrets, or external
services. API tests use FastAPI TestClient (in-process). CLI tests use Click CliRunner. WebSocket tests use
httpx AsyncClient. MCP tests use in-process transport. No real HTTP server is started in CI.

6. **Push after each chunk.** After a chunk passes its gate test (`uv run pytest <test_file> -xvs`), commit
and push before starting the next chunk. One chunk per commit.

7. **Import boundaries.** Foundation never imports orchestration or adapter. Adapter may import foundation but
not orchestration. Orchestration may import both foundation and adapter. The layering test
(`tests/test_layering.py`) enforces this. **Surface-specific addition:** All surface components (CLI, API,
Chat, Review, MCP) are adapter-layer. They import Foundation Protocols and schemas, and Orchestration
components. They never import other adapter implementations directly — all cross-adapter access goes through
Protocol injection via the DI container.

8. **No hardcoded model names.** Per §4.1, all model references resolve through named slots from config. No
model name may appear in any `orchestration/` or `foundation/` file. The test_no_hardcoded_model_names test
enforces this. MCP tools that expose model information read from ModelSlotResolver, not hardcoded strings.

9. **Qualified phase references.** Always use qualified terminology: "Architectural Phase 6" for the logical
scope, "CHUNK-8.x" for build units, "repo 3.x" for historical commits. Never use bare "Phase 6" without
qualification.

10. **Repo overlap reconciliation.** Before building any CHUNK-8.x, check whether repo 2.x or 3.x code already
implements part of the spec (especially CLI, API, or MCP work). If overlap exists, extend existing code to
meet the spec (amend by addition) rather than rewriting from scratch. Document reconciliation in WORKLOG.

11. **DEFINER sovereignty enforcement.** Per §1.7: "No UI, workflow, Beast cadence, MCP call, or queued task
may bypass the DEFINER gates." Every surface action that modifies canonical state (approve artifact, promote
to canonical, modify config, escalate autonomy) must go through the AutonomyGate. The test suite verifies that
no surface endpoint or CLI command can bypass the gate. Per Appendix D: "UI ≠ authority" — the UI surfaces
present information and relay DEFINER decisions; it does not make autonomous decisions.

12. **MCP constraint enforcement.** Per Appendix D: "MCP ≠ bypass" and "MCP ≠ vector_store.retrieve()
directly." MCP tools go through the same Protocol layer as all other surfaces. MCP tools do not get direct
database access, direct vector store access, or bypass AutonomyGate. The test suite verifies this.

13. **AIP init contract.** Per §2.3, `aip init` must: detect available RAM and suggest a hardware profile,
configure vector backend based on profile, initialize database schemas, validate Ollama connectivity, validate
model slot configuration, and print a clear summary of what is local vs API-dependent. This is the first user
experience and must be reliable.

---

## Repo State Reconciliation

**Important:** This spec was written when the codebase was assumed to contain Phase 1, Phase 2, Phase 3, Phase
4, and Phase 5 code. The actual repo contains additional work from historical chunk series 2.x (YAML engine
mechanics) and 3.x (L4/Sexton/ACE/budget foundation). This section documents the reconciliation.

### What this spec introduces that does NOT yet exist in code

| Type/Method | CHUNK | Notes |
|-------------|-------|-------|
| `SurfaceConfig` dataclass | 8.0a | New — no prior implementation |
| `ApiRoute` dataclass | 8.0a | New — no prior implementation |
| `McpToolDef` dataclass | 8.0a | New — no prior implementation |
| `AutonomyEscalation` dataclass | 8.0a | New — no prior implementation |
| `ChatMessage` dataclass | 8.0a | New — no prior implementation |
| `ReviewQueueEntry` dataclass | 8.0a | New — no prior implementation |
| `AutonomyGate` Protocol | 8.0a | Listed in §6 but never defined or implemented |
| `LexicalStore` Protocol full definition | 8.0a | Listed in §6 but never defined with methods |
| `CanonicalStore` new methods | 8.0a | Listed in §6, may have basic stubs from Phase 0 |
| `EntityStore` new methods | 8.0a | Listed in §6, may have basic stubs from Phase 0 |
| `adapter/lexical/sqlite_fts5_store.py` — `SqliteFts5LexicalStore` | 8.0b | New — no prior FTS5
implementation |
| `adapter/canonical/sqlite_canonical_store.py` — `SqliteCanonicalStore` | 8.0b | New — no prior canonical
store implementation |
| `adapter/entity/sqlite_entity_store.py` — `SqliteEntityStore` | 8.0b | New — no prior entity store
implementation |
| `adapter/autonomy/autonomy_gate.py` — `AutonomyGateImpl` | 8.0b | New — no prior autonomy gate
implementation |
| `adapter/api/` — FastAPI application | 8.1 | New — no prior API server |
| `adapter/cli/` — CLI commands | 8.2 | New — no prior CLI (except possibly `aip init` placeholder) |
| `adapter/api/chat.py` — Chat surface | 8.3 | New — no prior chat surface |
| `adapter/api/review.py` — Review Queue | 8.4 | New — no prior review queue surface |
| `adapter/mcp/` — MCP server | 8.5 | New — no prior MCP server |
| `adapter/api/admin.py` — Admin Console | 8.6 | New — no prior admin surface |
| Integration test | 8.7 | New — full surface round trip |
| Phase 6 cross-cutting gates | 8.8 | Extend CHUNK-7.7 |

### What already exists that may overlap

| Repo Series | What It Built | Overlap With |
|-------------|--------------|-------------|
| Repo 2.x (CHUNK-2.1–2.13) | YAML engine mechanics | No overlap with surfaces |
| Repo 3.x (CHUNK-3.1–3.12) | L4/Sexton/ACE/budget foundation | No direct overlap; surfaces consume these
orchestration components |

**Build strategy:** No significant overlap is expected. The adapter layer has no prior surface
implementations. If any CLI or API scaffolding exists in historical commits, extend it to meet the spec rather
than replacing it. The spec is the authoritative target; existing code is a head start, not a conflict.
Document all reconciliations in WORKLOG.

---

## Dependency DAG

```
CHUNK-7.0a ── CHUNK-7.0b ── CHUNK-7.1 ── CHUNK-7.2 ── CHUNK-7.3 ── CHUNK-7.4 ── CHUNK-7.5 ── CHUNK-7.6 ── CHUNK-7.7
     │              │            │            │            │            │            │            │            │
     │              │            │            │            │            │            │            │            │
CHUNK-8.0a ────── CHUNK-8.0b ─┼────────────┼────────────┼────────────┼────────────┼────────────┤
     │              │           │            │            │            │            │            │
     │              │           ├──── CHUNK-8.1 (API scaffold + project/session)   │            │
     │              │           │            │            │            │            │            │
     │              │           ├──── CHUNK-8.2 (CLI)                              │            │
     │              │           │            │            │            │            │            │
     │              │           ├──── CHUNK-8.3 (chat)   │            │            │            │
     │              │           │            │            │            │            │            │
     │              │           ├──── CHUNK-8.4 (review + artifact)               │            │
     │              │           │            │            │            │            │            │
     │              │           ├──── CHUNK-8.5 (MCP)    │            │            │            │
     │              │           │            │            │            │            │            │
     │              │           └──── CHUNK-8.6 (admin + memory inspector)         │            │
     │              │                                                        │            │
     └────────────────────────────────────────────────────── CHUNK-8.7 ─┘            │
                                                              (integration)          │
                                                                       │              │
                                                                  CHUNK-8.8 ─────────┘
                                                                   (gates)

Linearized build order:
  8.0a → 8.0b → 8.1 (parallel with 8.2) → 8.2
       → 8.1 → 8.3 (after 8.1, CHUNK-5.7)
       → 8.1 → 8.4 (after 8.1, CHUNK-4.0b)
       → 8.1 → 8.5 (after 8.1, 8.0b)
       → 8.1 → 8.6 (after 8.1, CHUNK-7.1)
       → 8.7 (after 8.3, 8.4, 8.5, 8.6, CHUNK-7.6)
       → 8.8 (after all)

Parallel groups:
  Group A: [8.0a]                                              — schema + protocol additions
  Group B: [8.0b] (after 8.0a)                                 — remaining Protocol adapters
  Group C: [8.1] (after 8.0b, CHUNK-5.7, CHUNK-6.3)            — API scaffold + project/session
  Group D: [8.2] (after 8.0b, CHUNK-6.4)                       — CLI
  Group E: [8.3] (after 8.1, CHUNK-5.7)                        — Chat surface
  Group F: [8.4] (after 8.1, CHUNK-4.0b)                       — Review Queue + Artifact Browser
  Group G: [8.5] (after 8.1, 8.0b)                             — MCP server
  Group H: [8.6] (after 8.1, CHUNK-7.1)                        — Admin + Memory Inspector
  Group I: [8.7] (after 8.3, 8.4, 8.5, 8.6, CHUNK-7.6)        — integration test
  Group J: [8.8] (after all)                                   — cross-cutting gates
```

The key architectural insight: **Groups C–D are a parallel pair**, and **Groups E–H are independent parallel
paths that all depend on Group C (the API scaffold).** The API scaffold (8.1) establishes the DI container,
app factory, and base routing that all other surfaces build on. Once the scaffold is in place, the five
surface areas (chat, review, MCP, admin, and CLI) can be built independently since they compose different
orchestration components and access different Protocols. They all converge at the integration test (8.7),
which verifies the complete surface-to-backend round trip including DEFINER sovereignty enforcement.

---

## CHUNK-8.0a: Schema Additions + Protocol Amendments + Config Extensions

```
CHUNK-8.0a: Schema Additions + Protocol Amendments + Config Extensions
PHASE: 6
DEPENDS-ON: CHUNK-7.0a, CHUNK-6.0a
CODER-PROFILE: L1
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  foundation/schemas.py (append only — do not modify existing Phase 0/1/2/3/4/5 enums or dataclasses)
  foundation/protocols.py (amend by addition — add methods to existing Protocol classes + add new Protocols)
INTERFACES:
  @dataclass
  class SurfaceConfig:
      api_host: str                       # FastAPI bind host
      api_port: int                       # FastAPI bind port
      api_cors_origins: list[str]         # CORS allowed origins
      api_workers: int                    # Uvicorn worker count
      chat_max_history_turns: int         # max turns retained in chat history
      review_page_size: int               # default page size for review queue
      artifact_page_size: int             # default page size for artifact browser
  @dataclass
  class ApiRoute:
      method: str                         # GET / POST / PUT / DELETE / WebSocket
      path: str                           # URL path (e.g., "/api/v1/projects")
      handler: str                        # handler function reference
      auth_required: bool                 # whether this route requires DEFINER auth
      autonomy_gate: bool                 # whether this route goes through AutonomyGate
  @dataclass
  class McpToolDef:
      tool_name: str                      # MCP tool name (e.g., "aip_search")
      description: str                    # human-readable tool description
      input_schema: dict                  # JSON Schema for tool inputs
      autonomy_level: str                 # "read" / "write" / "admin" — determines gate behavior
      model_gen_assumption: str | None    # §1.8
  @dataclass
  class AutonomyEscalation:
      escalation_id: str
      action_type: str                    # "approve_artifact" / "modify_config" / "escalate_autonomy" / "terminate_project"
      requested_by: str                   # "definer" / "mcp" / "workflow"
      resource_id: str                    # artifact_id, config_key, project_id
      current_level: str                  # "none" / "read" / "write" / "admin"
      requested_level: str                # what level is being requested
      granted: bool                       # whether the escalation was granted
      reason: str                         # why granted or denied
      model_gen_assumption: str | None    # §1.8
      created_at: str                     # ISO 8601
  @dataclass
  class ChatMessage:
      message_id: str
      session_id: str
      role: str                           # "user" / "assistant" / "system"
      content: str
      artifacts_referenced: list[str]     # artifact IDs mentioned in this message
      tokens_used: int
      created_at: str                     # ISO 8601
  @dataclass
  class ReviewQueueEntry:
      artifact_id: str
      artifact_version: int
      ecs_state: str                      # "GENERATED" / "REVIEWED"
      domain: str
      project_id: str
      review_type: str                    # "definer" / "adversarial"
      evaluation_scores: list[dict]       # serialized EvaluationScore list
      created_at: str                     # ISO 8601
  # Type aliases
  AutonomyLevel = Literal["none", "read", "write", "admin"]
  McpAutonomyLevel = Literal["read", "write", "admin"]
  # Protocol amendments in foundation/protocols.py (append method stubs only — do NOT redeclare class):
  # AutonomyGate: new Protocol from §6 (does not exist in Phase 0/1/2/3/4/5)
  class AutonomyGate(Protocol):
      async def check(self, action_type: str, resource_id: str, requested_level: AutonomyLevel, requested_by: str) -> AutonomyEscalation: ...
      async def escalate(self, action_type: str, resource_id: str, requested_level: AutonomyLevel, requested_by: str) -> AutonomyEscalation: ...
      async def audit_log(self, limit: int = 100) -> list[AutonomyEscalation]: ...
  # LexicalStore: new Protocol from §6 (listed but never defined with methods)
  class LexicalStore(Protocol):
      async def search(self, query: str, domain: str | None = None, limit: int = 10) -> list[Chunk]: ...
      async def index_document(self, doc_id: str, content: str, domain: str, metadata: dict) -> None: ...
      async def delete_document(self, doc_id: str) -> None: ...
  # CanonicalStore: add methods to existing Protocol stub
  async def read_canonical(self, artifact_id: str) -> dict | None: ...
  async def write_canonical(self, artifact_id: str, content: dict, approved_by: str) -> None: ...
  async def list_canonical(self, domain: str | None = None) -> list[dict]: ...
  # EntityStore: add methods to existing Protocol stub
  async def get_entity(self, entity_id: str) -> dict | None: ...
  async def list_entities(self, entity_type: str | None = None) -> list[dict]: ...
  async def update_entity(self, entity_id: str, updates: dict) -> None: ...
TESTS:
  tests/test_phase6_schema_additions.py
GATE: uv run pytest tests/test_phase6_schema_additions.py -xvs
```

### Prose

This chunk establishes the shared data types, protocol amendments, and configuration extensions that all
subsequent Phase 6 chunks depend on. It does eight things:

**1. Append `SurfaceConfig` dataclass to `foundation/schemas.py`.** The `SurfaceConfig` dataclass captures all
surface-layer configuration: the FastAPI bind host and port, CORS allowed origins, Uvicorn worker count, chat
history retention limit, and default page sizes
for the review queue and artifact browser. These parameters are toggleable per §1.8 and map to the `[api]` and
`[chat]` config sections. The `chat_max_history_turns` parameter controls how many turns the chat surface
retains before summarization — this is a context window management concern that directly affects token
consumption and model behavior, so it carries an implicit model_gen_assumption (models degrade when context
fills). The page size parameters ensure the surfaces do not return unbounded result sets that would stress the
laptop-viable hardware profile (§2.1). Append only — do not modify or reorder any existing definitions.
```

**2. Append `ApiRoute` dataclass.** The `ApiRoute` dataclass captures a single REST API route definition: the
HTTP method, URL path, handler reference, and two critical boolean flags — `auth_required` and
`autonomy_gate`. The `auth_required` flag marks routes that require DEFINER authentication (write operations,
config changes). The `autonomy_gate` flag marks routes that must go through the AutonomyGate before execution
(canonical promotion, autonomy escalation, project termination). Per §1.7, no surface may bypass the DEFINER
gates, and the `autonomy_gate` flag is the mechanism that enforces this at the routing level. Read-only routes
(list projects, browse artifacts) have `autonomy_gate = False` but may still have `auth_required = True`
depending on deployment policy.

**3. Append `McpToolDef` dataclass.** The `McpToolDef` dataclass captures a single MCP tool definition: the
tool name, description, input schema (JSON Schema), and an `autonomy_level` field that determines how the
AutonomyGate treats calls to this tool. The three levels are "read" (no gate check), "write" (gate logs but
does not block), and "admin" (gate blocks without DEFINER approval). The `model_gen_assumption` field per §1.8
captures whether this tool compensates for a model limitation — for example, an MCP tool that retrieves
context that the model would otherwise hallucinate. Per Appendix D: "MCP ≠ bypass" and "MCP ≠
vector_store.retrieve() directly" — MCP tools go through Protocols, not direct storage access.

**4. Append `AutonomyEscalation` dataclass.** The `AutonomyEscalation` dataclass captures a single autonomy
escalation request: the action type, who requested it, the resource being accessed, the current and requested
autonomy levels, whether it was granted, the reason, and a `model_gen_assumption` field per §1.8. This is the
audit trail for all DEFINER sovereignty decisions. Every time a surface action requires elevated autonomy
(approve artifact, modify config, terminate project), an `AutonomyEscalation` record is created. Per §1.7: "No
UI, workflow, Beast cadence, MCP call, or queued task may bypass the DEFINER gates" — the escalation record is
the proof that the gate was checked.

**5. Append `ChatMessage` and `ReviewQueueEntry` dataclasses.** The `ChatMessage` dataclass captures a single chat message: the message ID, session ID, role (user/assistant/system), content, referenced artifacts, tokens consumed, and timestamp. This is the surface-layer representation of a conversation turn — it maps to the conversation corpus in the persistence layer (§5) but is a distinct surface dataclass that may include additional presentation fields. The `ReviewQueueEntry` dataclass captures a single entry in the review queue: the artifact ID, version, ECS state, domain, project, review type (DEFINER or adversarial), evaluation scores, and timestamp. This aggregates data from multiple stores (ArtifactStore, EcsStore, evaluation nodes) into a single surface-ready object.
```

**6. Add `AutonomyLevel` and `McpAutonomyLevel` type aliases.** These `Literal` type aliases define the
autonomy level hierarchy: "none" < "read" < "write" < "admin" for general autonomy, and "read" / "write" /
"admin" for MCP tool autonomy. The AutonomyGate uses these to determine whether to allow, log, or block an
action.

**7. Add `AutonomyGate` and `LexicalStore` Protocols in `foundation/protocols.py`.** These are new Protocols,
not amendments. The `AutonomyGate` Protocol from §6 implements the DEFINER sovereignty enforcement: `check`
returns an escalation record without blocking, `escalate` requests elevation and may block, and `audit_log`
returns recent escalation records. The `LexicalStore` Protocol from §6 implements full-text search: `search`
returns Chunk results filtered by query and domain, `index_document` adds a document to the FTS5 index, and
`delete_document` removes it. These Protocols have been listed in §6 since Rev 5.0 but never defined with
method signatures — Phase 6 is the first phase that needs them.

**8. Amend `CanonicalStore` and `EntityStore` Protocols.** Phase 0 listed these Protocols but did not define
their methods. Phase 6 adds the read/write methods that surfaces need: `read_canonical`, `write_canonical`,
`list_canonical` for CanonicalStore; `get_entity`, `list_entities`, `update_entity` for EntityStore.

**Config additions.** Phase 6 extends `config/aip.config.toml` with:

```toml
[api]
host = "127.0.0.1"
port = 8000
cors_origins = ["http://localhost:3000"]
workers = 1
chat_max_history_turns = 50
review_page_size = 20
artifact_page_size = 20

[cli]
color = true
pager = true
output_format = "table"              # table / json / yaml

[mcp]
enabled = true
transport = "stdio"                   # stdio / sse
max_concurrent_tools = 5

[chat]
system_prompt_path = "prompts/chat_system.md"
max_context_turns = 50
auto_summarize_at = 40               # trigger summarization at this turn count

[autonomy]
default_level = "read"               # default autonomy for new sessions
escalation_requires_definer = true    # admin actions require DEFINER approval
audit_retention_days = 90
model_gen_assumption = "Models should not autonomously escalate to admin actions"

[lexical]
db_path = "db/lexical.db"
fts5_tokenizer = "unicode61"
```

The gate test verifies: (a) all new dataclasses can be instantiated with required fields, (b) `McpToolDef`
carries `model_gen_assumption` field per §1.8, (c) `AutonomyEscalation` carries `model_gen_assumption` field
per §1.8, (d) `AutonomyGate` Protocol has `check`, `escalate`, `audit_log` methods, (e) `LexicalStore`
Protocol has `search`, `index_document`, `delete_document` methods, (f) `CanonicalStore` has `read_canonical`,
`write_canonical`, `list_canonical` methods, (g) `EntityStore` has `get_entity`, `list_entities`,
`update_entity` methods, (h) existing Phase 0/1/2/3/4/5 schema enums and dataclasses are not broken, (i)
existing Protocol methods still exist.

### ANNEX

**`foundation/schemas.py` (append to existing file):**

```python
# --- Phase 6 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type aliases for autonomy levels
AutonomyLevel = Literal["none", "read", "write", "admin"]
McpAutonomyLevel = Literal["read", "write", "admin"]


@dataclass
class SurfaceConfig:
    """Configuration for AIP surfaces (API, CLI, Chat, MCP).

    Per §1.8: all parameters toggleable via config.
    Per §2.1: surfaces must respect laptop-viable hardware profile.
    Per §7.2: surfaces are adapter-layer, composing Foundation and Orchestration.
    """
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    api_workers: int = 1
    chat_max_history_turns: int = 50
    review_page_size: int = 20
    artifact_page_size: int = 20


@dataclass
class ApiRoute:
    """A single REST API route definition.

    Per §1.7: autonomy_gate=True routes enforce DEFINER sovereignty.
    Per §7.2: all routes are adapter-layer compositions.
    """
    method: str
    path: str
    handler: str
    auth_required: bool = False
    autonomy_gate: bool = False


@dataclass
class McpToolDef:
    """A single MCP tool definition.

    Per §3: MCP/API surface.
    Per Appendix D: "MCP ≠ bypass", "MCP ≠ vector_store.retrieve() directly."
    Per §1.8: model_gen_assumption tags what model limitation this tool compensates for.
    """
    tool_name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    autonomy_level: McpAutonomyLevel = "read"
    model_gen_assumption: str | None = None


@dataclass
class AutonomyEscalation:
    """A single autonomy escalation request and its resolution.

    Per §1.7: "No UI, workflow, Beast cadence, MCP call, or queued task
    may bypass the DEFINER gates."
    Per §1.8: model_gen_assumption tags what assumption this escalation encodes.
    """
    escalation_id: str
    action_type: str
    requested_by: str
    resource_id: str
    current_level: AutonomyLevel = "none"
    requested_level: AutonomyLevel = "read"
    granted: bool = False
    reason: str = ""
    model_gen_assumption: str | None = None
    created_at: str = ""  # REQUIRED — ISO 8601


@dataclass
class ChatMessage:
    """A single chat message in the DEFINER conversation surface.

    Per §3: Chat surface is the primary DEFINER interaction point.
    Per §1.3: context is assembled from explicit stores, not long chat history.
    """
    message_id: str
    session_id: str
    role: str  # user / assistant / system
    content: str = ""
    artifacts_referenced: list[str] = field(default_factory=list)
    tokens_used: int = 0
    created_at: str = ""  # REQUIRED — ISO 8601


@dataclass
class ReviewQueueEntry:
    """A single entry in the review queue surface.

    Per §3: Review Queue surface.
    Per §9.3: ECS transitions REVIEWED→APPROVED or REVIEWED→FAILED.
    Per §1.7: canonical promotion requires DEFINER approval.
    """
    artifact_id: str
    artifact_version: int = 1
    ecs_state: str = "GENERATED"
    domain: str = ""
    project_id: str = ""
    review_type: str = "definer"  # definer / adversarial
    evaluation_scores: list[dict] = field(default_factory=list)
    created_at: str = ""  # REQUIRED — ISO 8601
```
<!-- ESTIMATED_TOKENS: ~350 -->

**`foundation/protocols.py` (append method stubs to existing Protocol classes + add new Protocols):**

```python
# --- Phase 6 amendments (append method stubs only — do NOT redeclare existing Protocol classes) ---

# CanonicalStore — add method stubs to existing Protocol class
# (existing methods from Phase 0 remain unchanged)
    async def read_canonical(self, artifact_id: str) -> dict | None:
        """Read a canonical artifact by ID.

        Returns None if no canonical version exists.
        Canonical artifacts are DEFINER-approved per §1.6.
        """
        ...

    async def write_canonical(
        self, artifact_id: str, content: dict, approved_by: str
    ) -> None:
        """Write a canonical artifact.

        Only called after DEFINER approval (ECS APPROVED state).
        approved_by must be "definer" — enforced by AutonomyGate.
        """
        ...

    async def list_canonical(
        self, domain: str | None = None
    ) -> list[dict]:
        """List canonical artifacts, optionally filtered by domain.

        Returns list of dicts with artifact_id, domain, approved_by, created_at.
        """
        ...


# EntityStore — add method stubs to existing Protocol class
    async def get_entity(self, entity_id: str) -> dict | None:
        """Get an entity by ID.

        Returns None if entity does not exist.
        """
        ...

    async def list_entities(
        self, entity_type: str | None = None
    ) -> list[dict]:
        """List entities, optionally filtered by type.

        Returns list of dicts with entity_id, entity_type, name, metadata.
        """
        ...

    async def update_entity(
        self, entity_id: str, updates: dict
    ) -> None:
        """Update entity fields.

        updates is a dict of field→value pairs to apply.
        """
        ...


# --- Phase 6 new Protocols (not amendments — these are new classes) ---


class AutonomyGate(Protocol):
    """DEFINER sovereignty enforcement per §1.7 and §6.

    No UI, workflow, Beast cadence, MCP call, or queued task may
    bypass the DEFINER gates. The AutonomyGate is the mechanism
    that enforces this for all surface actions.

    Per Appendix D: "UI ≠ authority."
    """

    async def check(
        self,
        action_type: str,
        resource_id: str,
        requested_level: "AutonomyLevel",
        requested_by: str,
    ) -> "AutonomyEscalation":
        """Check whether an action is allowed at the current autonomy level.

        Returns an AutonomyEscalation record with granted=True/False.
        Does not block — use escalate() for blocking gate.
        """
        ...

    async def escalate(
        self,
        action_type: str,
        resource_id: str,
        requested_level: "AutonomyLevel",
        requested_by: str,
    ) -> "AutonomyEscalation":
        """Request autonomy escalation for an action.

        Blocks until DEFINER approves if escalation_requires_definer is True.
        Returns an AutonomyEscalation record with the resolution.
        """
        ...

    async def audit_log(self, limit: int = 100) -> list["AutonomyEscalation"]:
        """Return recent autonomy escalation records for audit.

        Used by admin console and DEFINER review.
        """
        ...


class LexicalStore(Protocol):
    """Full-text search abstraction per §6.

    Abstracts SQLite FTS5 so that orchestration and adapter code
    never import sqlite3 directly for search operations.

    Per §8.1: supports domain-filtered retrieval.
    Per §2.1: laptop-viable, local-only.
    """

    async def search(
        self,
        query: str,
        domain: str | None = None,
        limit: int = 10,
    ) -> list["Chunk"]:
        """Full-text search for documents matching query.

        Returns Chunk results with score = FTS5 rank.
        Optionally filtered by domain.
        """
        ...

    async def index_document(
        self,
        doc_id: str,
        content: str,
        domain: str,
        metadata: dict,
    ) -> None:
        """Add or update a document in the FTS5 index.

        Idempotent — re-indexing the same doc_id updates content.
        """
        ...

    async def delete_document(self, doc_id: str) -> None:
        """Remove a document from the FTS5 index.

        Per Appendix D: "Supersession ≠ deletion" — but stale
        FTS5 entries should be cleaned up.
        """
        ...
```
<!-- ESTIMATED_TOKENS: ~350 -->

**`tests/test_phase6_schema_additions.py`:**

```python
"""Verify Phase 6 schema additions do not break Phase 0, 1, 2, 3, 4, or 5."""
import pytest

from foundation.schemas import (
    AcePlaybookEntry,
    ApiRoute,
    ArtifactStore,
    AutonomyEscalation,
    AutonomyLevel,
    BeastCadenceConfig,
    BudgetConfig,
    BudgetScope,
    ChatMessage,
    Chunk,
    ContractRule,
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
)
from foundation.protocols import (
    ArtifactStore,
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
)


def test_surface_config_dataclass():
    sc = SurfaceConfig(
        api_host="127.0.0.1",
        api_port=8000,
        chat_max_history_turns=50,
    )
    assert sc.api_port == 8000
    assert sc.chat_max_history_turns == 50


def test_api_route_dataclass():
    ar = ApiRoute(
        method="POST",
        path="/api/v1/artifacts/{artifact_id}/approve",
        handler="approve_artifact",
        auth_required=True,
        autonomy_gate=True,
    )
    assert ar.autonomy_gate is True
    assert ar.auth_required is True


def test_mcp_tool_def_carries_model_gen_assumption():
    """Per §1.8: every MCP tool definition must carry model_gen_assumption."""
    td = McpToolDef(
        tool_name="aip_search",
        description="Search AIP memory for relevant context",
        autonomy_level="read",
        model_gen_assumption="Models may hallucinate without retrieved context",
    )
    assert td.model_gen_assumption is not None
    assert td.autonomy_level == "read"


def test_autonomy_escalation_carries_model_gen_assumption():
    """Per §1.8: every autonomy escalation must carry model_gen_assumption."""
    ae = AutonomyEscalation(
        escalation_id="esc-001",
        action_type="approve_artifact",
        requested_by="mcp",
        resource_id="artifact-42",
        current_level="read",
        requested_level="admin",
        granted=False,
        reason="DEFINER approval required for canonical promotion",
        model_gen_assumption="Models should not autonomously approve artifacts",
        created_at="2026-05-28T10:00:00Z",
    )
    assert ae.model_gen_assumption is not None
    assert ae.granted is False


def test_chat_message_dataclass():
    cm = ChatMessage(
        message_id="msg-001",
        session_id="sess-001",
        role="user",
        content="Generate a design document for the API layer",
    )
    assert cm.role == "user"
    assert cm.artifacts_referenced == []


def test_review_queue_entry_dataclass():
    rqe = ReviewQueueEntry(
        artifact_id="art-42",
        ecs_state="REVIEWED",
        domain="software_architecture",
        project_id="proj-1",
        review_type="definer",
    )
    assert rqe.ecs_state == "REVIEWED"
    assert rqe.review_type == "definer"


def test_autonomy_level_type_alias():
    """AutonomyLevel must accept the defined levels."""
    al_none: AutonomyLevel = "none"
    al_read: AutonomyLevel = "read"
    al_write: AutonomyLevel = "write"
    al_admin: AutonomyLevel = "admin"
    assert al_admin == "admin"


def test_mcp_autonomy_level_type_alias():
    """McpAutonomyLevel must accept the defined levels."""
    ml_read: McpAutonomyLevel = "read"
    ml_write: McpAutonomyLevel = "write"
    ml_admin: McpAutonomyLevel = "admin"
    assert ml_admin == "admin"


def test_phase0_through_phase5_enums_still_work():
    """Phase 0/1/2/3/4/5 enums must not be broken by Phase 6 additions."""
    assert EcsState.GENERATED is not None
    assert FailureType.C is not None


def test_phase0_through_phase5_dataclasses_still_work():
    """Phase 0/1/2/3/4/5 dataclasses must not be broken by Phase 6 additions."""
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


def test_autonomy_gate_protocol_methods():
    """Phase 6: AutonomyGate must have check, escalate, audit_log methods."""
    assert hasattr(AutonomyGate, "check"), "AutonomyGate missing check method"
    assert hasattr(AutonomyGate, "escalate"), "AutonomyGate missing escalate method"
    assert hasattr(AutonomyGate, "audit_log"), "AutonomyGate missing audit_log method"


def test_lexical_store_protocol_methods():
    """Phase 6: LexicalStore must have search, index_document, delete_document methods."""
    assert hasattr(LexicalStore, "search"), "LexicalStore missing search method"
    assert hasattr(LexicalStore, "index_document"), "LexicalStore missing index_document method"
    assert hasattr(LexicalStore, "delete_document"), "LexicalStore missing delete_document method"


def test_canonical_store_new_methods():
    """Phase 6: CanonicalStore must have read_canonical, write_canonical, list_canonical."""
    assert hasattr(CanonicalStore, "read_canonical"), "CanonicalStore missing read_canonical"
    assert hasattr(CanonicalStore, "write_canonical"), "CanonicalStore missing write_canonical"
    assert hasattr(CanonicalStore, "list_canonical"), "CanonicalStore missing list_canonical"


def test_entity_store_new_methods():
    """Phase 6: EntityStore must have get_entity, list_entities, update_entity."""
    assert hasattr(EntityStore, "get_entity"), "EntityStore missing get_entity"
    assert hasattr(EntityStore, "list_entities"), "EntityStore missing list_entities"
    assert hasattr(EntityStore, "update_entity"), "EntityStore missing update_entity"


def test_existing_protocol_methods_preserved():
    """Phase 1/2/3/4/5 methods must still exist after Phase 6 amendments."""
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
```
<!-- ESTIMATED_TOKENS: ~400 -->

---

## CHUNK-8.0b: Remaining Protocol Adapter Implementations

```
CHUNK-8.0b: Remaining Protocol Adapter Implementations
PHASE: 6
DEPENDS-ON: CHUNK-8.0a, CHUNK-6.0b
CODER-PROFILE: L2
CONTEXT-BUDGET: ~6,000 tokens
FILES:
  adapter/lexical/sqlite_fts5_store.py
  adapter/lexical/__init__.py
  adapter/canonical/sqlite_canonical_store.py
  adapter/canonical/__init__.py
  adapter/entity/sqlite_entity_store.py
  adapter/entity/__init__.py
  adapter/autonomy/autonomy_gate.py
  adapter/autonomy/__init__.py
  tests/test_remaining_adapters.py
INTERFACES:
  class SqliteFts5LexicalStore(LexicalStore):
      def __init__(self, db_path: str) -> None: ...
      async def search(self, query: str, domain: str | None = None, limit: int = 10) -> list[Chunk]: ...
      async def index_document(self, doc_id: str, content: str, domain: str, metadata: dict) -> None: ...
      async def delete_document(self, doc_id: str) -> None: ...
      async def initialize(self) -> None: ...
      async def close(self) -> None: ...

  class SqliteCanonicalStore(CanonicalStore):
      def __init__(self, db_path: str) -> None: ...
      async def read_canonical(self, artifact_id: str) -> dict | None: ...
      async def write_canonical(self, artifact_id: str, content: dict, approved_by: str) -> None: ...
      async def list_canonical(self, domain: str | None = None) -> list[dict]: ...
      async def initialize(self) -> None: ...
      async def close(self) -> None: ...

  class SqliteEntityStore(EntityStore):
      def __init__(self, db_path: str) -> None: ...
      async def get_entity(self, entity_id: str) -> dict | None: ...
      async def list_entities(self, entity_type: str | None = None) -> list[dict]: ...
      async def update_entity(self, entity_id: str, updates: dict) -> None: ...
      async def initialize(self) -> None: ...
      async def close(self) -> None: ...

  class AutonomyGateImpl(AutonomyGate):
      def __init__(self, config: dict, escalation_store: EscalationStore) -> None: ...
      async def check(self, action_type: str, resource_id: str, requested_level: AutonomyLevel, requested_by: str) -> AutonomyEscalation: ...
      async def escalate(self, action_type: str, resource_id: str, requested_level: AutonomyLevel, requested_by: str) -> AutonomyEscalation: ...
      async def audit_log(self, limit: int = 100) -> list[AutonomyEscalation]: ...
TESTS:
  tests/test_remaining_adapters.py
GATE: uv run pytest tests/test_remaining_adapters.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the remaining Protocol adapters that all Phase 6 surfaces need but no prior phase
delivered. There are four adapters: LexicalStore (FTS5), CanonicalStore, EntityStore, and AutonomyGate. These
are adapter-layer components per §7.2 — they compose Foundation Protocols and schemas, and are consumed by the
surface components in subsequent chunks.

**SqliteFts5LexicalStore.** The `SqliteFts5LexicalStore` implements the `LexicalStore` Protocol using SQLite
FTS5. The `initialize()` method creates the `fts_documents` table with columns `doc_id TEXT PRIMARY KEY,
content TEXT, domain TEXT, metadata JSON, created_at TEXT` and the FTS5 virtual table `CREATE VIRTUAL TABLE
fts_index USING fts5(content, domain, metadata, tokenize=unicode61)`. The `index_document` method inserts into
both the documents table and the FTS5 index. The `search` method queries `SELECT doc_id, content, domain,
metadata, rank FROM fts_index WHERE fts_index MATCH ?` with optional `AND domain = ?` filter, mapping results
to `Chunk` dataclasses with the FTS5 rank as the score. The `delete_document` method removes from both tables.
The FTS5 tokenizer defaults to `unicode61` from config but is configurable per §1.8. FTS5 is local,
deterministic, and requires no external service — consistent with §2.1 laptop-viable requirements.

**SqliteCanonicalStore.** The `SqliteCanonicalStore` implements the `CanonicalStore` Protocol using SQLite.
The `initialize()` method creates the `canonical_artifacts` table with columns `artifact_id TEXT PRIMARY KEY,
content JSON, approved_by TEXT, domain TEXT, created_at TEXT, superseded_by TEXT`. The `write_canonical`
method inserts a canonical record — but only after verifying `approved_by == "definer"`, enforcing §1.7's
DEFINER sovereignty requirement. The `read_canonical` method returns the latest non-superseded canonical for a
given artifact_id. The `list_canonical` method returns all canonicals, optionally filtered by domain. Per
§1.6: "Canonical artifacts require explicit approval and are stored separately from the original generated
artifact" — this store is distinct from the `VersionedArtifactStore` from Phase 2.

**SqliteEntityStore.** The `SqliteEntityStore` implements the `EntityStore` Protocol using SQLite. The
`initialize()` method creates the `entities` table with columns `entity_id TEXT PRIMARY KEY, entity_type TEXT,
name TEXT, metadata JSON, created_at TEXT, updated_at TEXT`. The `get_entity`, `list_entities`, and
`update_entity` methods provide basic CRUD operations. Per §5: "Entity / Operations Store" is listed as a
separate persistence concern from the Project Store — Appendix D: "Entity store ≠ project store."

**AutonomyGateImpl.** The `AutonomyGateImpl` implements the `AutonomyGate` Protocol. It maintains an in-memory
escalation log (backed by a simple SQLite `autonomy_escalations` table) and enforces the autonomy level
hierarchy: "none" < "read" < "write" < "admin". The `check` method evaluates whether the requested action is
allowed at the current session's autonomy level without blocking — it returns an `AutonomyEscalation` with
`granted=True` if the current level is sufficient, or `granted=False` if escalation would be needed. The
`escalate` method is the blocking gate: if `escalation_requires_definer` is True and the requested level is
"admin", it creates a pending escalation and returns `granted=False` — the surface must then prompt the
DEFINER for approval. For "read" and "write" levels, escalation is automatically granted with a log entry. The
`audit_log` method returns recent escalation records for the admin console. The escalation store is separate
from the event store — it is a governance audit trail, not a system event log.

**Escalation store.** The `AutonomyGateImpl` writes to a dedicated `autonomy_escalations` table in `state.db`
with columns `escalation_id TEXT PRIMARY KEY, action_type TEXT, requested_by TEXT, resource_id TEXT,
current_level TEXT, requested_level TEXT, granted INTEGER, reason TEXT, model_gen_assumption TEXT, created_at
TEXT`. This table is the DEFINER sovereignty audit trail.

The gate test verifies: (a) `SqliteFts5LexicalStore` implements `LexicalStore` Protocol, (b) FTS5 search
returns ranked results, (c) `SqliteCanonicalStore` implements `CanonicalStore` Protocol, (d) `write_canonical`
rejects non-DEFINER approval, (e) `SqliteEntityStore` implements `EntityStore` Protocol, (f)
`AutonomyGateImpl` implements `AutonomyGate` Protocol, (g) autonomy gate blocks admin-level actions without
DEFINER approval, (h) autonomy gate auto-grants read-level actions, (i) autonomy gate audit log returns
escalation records, (j) adapter layer does not import orchestration, (k) existing Phase 0/1/2/3/4/5 adapter
tests still pass.

---

## CHUNK-8.1: FastAPI Application Scaffold + Project & Session REST API

```
CHUNK-8.1: FastAPI Application Scaffold + Project & Session REST API
PHASE: 6
DEPENDS-ON: CHUNK-8.0b, CHUNK-5.7, CHUNK-6.3
CODER-PROFILE: L3
CONTEXT-BUDGET: ~6,000 tokens
FILES:
  adapter/api/app.py (FastAPI app factory + DI container)
  adapter/api/routes/projects.py
  adapter/api/routes/sessions.py
  adapter/api/routes/health.py
  adapter/api/dependencies.py (Protocol injection)
  adapter/api/__init__.py
  tests/test_api_projects_sessions.py
INTERFACES:
  # app factory
  def create_app(config: dict) -> FastAPI: ...
  # DI container
  class AipContainer:
      def __init__(self, config: dict) -> None: ...
      vector_store: VectorStore
      ecs_store: EcsStore
      artifact_store: ArtifactStore
      event_store: EventStore
      trace_store: TraceStore
      budget_store: BudgetStore
      project_store: ProjectStore
      entity_store: EntityStore
      lexical_store: LexicalStore
      canonical_store: CanonicalStore
      autonomy_gate: AutonomyGate
      model_provider: ModelProvider
      embedding_provider: EmbeddingProvider
      session_manager: SessionManager
      budget_manager: BudgetManager
      adaptive_router: AdaptiveRouter
      sexton: Sexton
      beast: Beast
      ace_playbook: AcePlaybook
  # Health endpoint
  GET /api/v1/health → { status, vector_backend, model_slots, uptime_seconds }
  # Projects
  GET    /api/v1/projects → list[dict]
  POST   /api/v1/projects → dict (autonomy_gate: write)
  GET    /api/v1/projects/{project_id} → dict
  GET    /api/v1/projects/{project_id}/work_units → list[dict]
  # Sessions
  POST   /api/v1/sessions → dict (creates session, loads ACE playbook)
  GET    /api/v1/sessions/{session_id} → dict
  GET    /api/v1/sessions/{session_id}/context → SessionContext
TESTS:
  tests/test_api_projects_sessions.py
GATE: uv run pytest tests/test_api_projects_sessions.py tests/test_layering.py -xvs
```

### Prose

This chunk creates the FastAPI application scaffold — the DI container, app factory, middleware, and the first
set of REST endpoints (health, projects, sessions). All subsequent surface chunks build on this scaffold by
adding route modules that use the same DI container.

**App factory and DI container.** The `create_app(config)` function creates a `FastAPI` instance with: (1)
CORS middleware configured from `SurfaceConfig.api_cors_origins`, (2) a lifespan event handler that
initializes all adapter implementations (VectorStore, LexicalStore, CanonicalStore, EntityStore, BudgetStore,
AutonomyGate) and wires them into the `AipContainer`, (3) route registration
for all modules, (4)
exception handlers that map domain errors to HTTP status codes. The `AipContainer` class is the dependency
injection container that holds all Protocol instances. Each route module receives the container via FastAPI's
`Depends()` mechanism. The container is created during lifespan startup and disposed during shutdown — this
ensures that asyncpg pools, SQLite connections, and other resources are properly managed.
```

**Dependency injection pattern.** The `dependencies.py` module provides `get_container()` as a FastAPI
dependency. Route handlers use `container: AipContainer = Depends(get_container)` to access Protocol
instances. This ensures that no route handler imports adapter implementations directly — all access is through
the Protocol interfaces on the container. This preserves the three-layer import boundary.

**Health endpoint.** `GET /api/v1/health` returns a dict with: `status: "ok"`, `vector_backend` (provider name
from config), `model_slots` (list of configured slot names), `uptime_seconds` (since app startup). This
endpoint does not require authentication and does not go through the AutonomyGate — it is a read-only system
status check. The `aip status` CLI command (CHUNK-8.2) calls this endpoint in networked mode, or reads the
same data locally in offline mode.

**Projects endpoints.** `GET /api/v1/projects` returns all projects via `ProjectStore.list_projects()`. `POST
/api/v1/projects` creates a new project — this is a write operation that goes through the AutonomyGate with
`requested_level="write"`, `action_type="create_project"`. Write-level actions are auto-granted (the gate logs
but does not block) unless the session's autonomy level is "none". `GET /api/v1/projects/{project_id}` returns
a single project. `GET /api/v1/projects/{project_id}/work_units` returns WorkUnits for a project.

**Sessions endpoints.** `POST /api/v1/sessions` creates a new session via `SessionManager.create_session()`.
On session creation, the ACE Playbook is loaded for the session's domain — this implements §8.1: "ACE Playbook
/ SQLite — procedural intervention rules, loaded at session start." The session endpoint also initializes a
`BudgetManager` session scope. `GET /api/v1/sessions/{session_id}` returns session state. `GET
/api/v1/sessions/{session_id}/context` returns the `SessionContext` including turn count, context window
estimate, and artifacts produced.

The gate test verifies: (a) `create_app` returns a valid FastAPI instance, (b) health endpoint returns 200
with status info, (c) projects CRUD works through TestClient, (d) session creation loads ACE playbook, (e)
AutonomyGate is checked on POST /projects, (f) DI container has all required Protocol instances, (g) adapter
layer does not import orchestration implementations (only types), (h) existing tests still pass.

---

## CHUNK-8.2: CLI Implementation

```
CHUNK-8.2: CLI Implementation
PHASE: 6
DEPENDS-ON: CHUNK-8.0b, CHUNK-6.4
CODER-PROFILE: L2
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  adapter/cli/main.py (Click/Typer group)
  adapter/cli/init.py (aip init — §2.3)
  adapter/cli/status.py (aip status)
  adapter/cli/config.py (aip config)
  adapter/cli/project.py (aip project)
  adapter/cli/session.py (aip session)
  adapter/cli/__init__.py
  tests/test_cli.py
INTERFACES:
  # Click/Typer group
  @click.group()
  def aip(): ...

  # aip init — §2.3 installation contract
  @aip.command()
  def init(): ...
  # Must: detect RAM, configure vector backend, initialize DB schemas,
  #       validate Ollama, validate model slots, print summary

  # aip status
  @aip.command()
  def status(): ...
  # Prints: vector backend health, model slots, active sessions, budget status

  # aip config
  @aip.command()
  def config(key: str | None, value: str | None): ...
  # Read/write config values; write goes through AutonomyGate

  # aip project
  @click.group()
  def project(): ...
  @project.command()
  def list(): ...
  @project.command()
  def create(name: str, domain: str): ...
  @project.command()
  def show(project_id: str): ...

  # aip session
  @click.group()
  def session(): ...
  @session.command()
  def start(project_id: str, domain: str): ...
  @session.command()
  def resume(session_id: str): ...
  @session.command()
  def list(): ...
TESTS:
  tests/test_cli.py
GATE: uv run pytest tests/test_cli.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the CLI — the primary offline interaction surface for AIP 0.1. Per §2.3, the
installation contract is `uv sync; uv run aip init; uv run aip status`. The CLI uses Click (or Typer for type
hints) and follows the adapter-layer pattern: it composes Foundation Protocols and Orchestration components
through the DI container, but never imports adapter storage implementations directly.

**`aip init` — the installation contract.** This is the most critical CLI command. Per §2.3, `aip init` must:
(1) detect available RAM and suggest a hardware profile (4GB → sqlite_vss + API synthesis; 6GB → pgvector
tuned + local evaluation; 8GB+ → pgvector preferred), (2) configure the vector backend based on the detected
profile (write `[vector_backend]` provider in config), (3) initialize all database schemas (events.db,
state.db, trace.db, vectors.db/tables, ace_playbook.db, lexical.db), (4) validate Ollama connectivity by
calling the Ollama API endpoint (gracefully degrade if unavailable — print a warning, not an error), (5)
validate model slot configuration by resolving each slot and confirming the provider/model exists, (6) print a
clear summary of what is local vs API-dependent. The `aip init` command is the first user experience and must
be reliable, informative, and non-blocking — if Ollama is not running, it should say "Ollama not detected.
Sexton and embedding will use API fallback. Run `ollama serve` to enable local models." rather than crashing.

**`aip status`.** Prints system status: vector backend health (calls VectorStore.health_check()), model slot
resolution (lists all configured slots), active sessions (from SessionManager), budget status (from
BudgetManager), Beast last run time, Sexton classification stats. This command reads from Protocols directly —
it does not go through the REST API (offline mode is important for a local-first tool per §2.1).

**`aip config`.** Reads or writes config values. `aip config` with no arguments lists all config sections.
`aip config key` reads a value. `aip config key value` writes a value. Write operations go through the
AutonomyGate with `action_type="modify_config"`, `requested_level="admin"` — per §1.7, config changes are
DEFINER-gated. The CLI is an interactive surface; if the gate blocks, the CLI prompts "This operation requires
DEFINER approval. Approve? [y/N]" — this is the escalation flow.

**`aip project`.** Subcommand group for project management. `aip project list` lists all projects. `aip
project create --name X --domain Y` creates a new project. `aip project show PROJECT_ID` shows project details
including WorkUnits and artifact counts. All commands use Protocol injection (ProjectStore, EcsStore,
ArtifactStore).

**`aip session`.** Subcommand group for session management. `aip session start --project-id X --domain Y`
creates a new session and prints the session_id. `aip session resume SESSION_ID` resumes an existing session.
`aip session list` shows active sessions. Session start loads the ACE Playbook per §8.1.

The gate test uses Click's `CliRunner` to test all commands in-process. The test verifies: (a) `aip init`
creates all database files, (b) `aip status` prints health info, (c) `aip project create` creates a project,
(d) `aip session start` creates a session, (e) config write goes through AutonomyGate, (f) adapter layer does
not import orchestration, (g) existing tests still pass.

---

## CHUNK-8.3: Chat Surface

```
CHUNK-8.3: Chat Surface
PHASE: 6
DEPENDS-ON: CHUNK-8.1, CHUNK-5.7
CODER-PROFILE: L3
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  adapter/api/routes/chat.py
  tests/test_api_chat.py
INTERFACES:
  # WebSocket chat endpoint
  WS /api/v1/chat/{session_id}
  # Message format:
  # Client → Server: { "type": "message", "content": "..." }
  # Server → Client: { "type": "response", "content": "...", "artifacts": [...], "tokens_used": N }
  # Server → Client: { "type": "gate", "gate_type": "definer_review", "artifact_id": "...", "preview": "..." }
  # Client → Server: { "type": "gate_response", "approved": true/false }
  # Server → Client: { "type": "context_reset", "reason": "...", "summary": "..." }
TESTS:
  tests/test_api_chat.py
GATE: uv run pytest tests/test_api_chat.py -xvs
```

### Prose

This chunk implements the chat surface — the primary DEFINER interaction point for AIP 0.1. The chat surface is a WebSocket endpoint that supports multi-turn conversations
with session context management, DEFINER gate handling, and ACE playbook integration.

**WebSocket chat endpoint.** The `WS /api/v1/chat/{session_id}` endpoint accepts a session_id (created via the
session API or CLI) and opens a persistent WebSocket connection. The client sends messages as JSON with `type:
"message"` and `content: str`. The server processes each message through the synthesis pipeline: (1) load ACE
playbook for the session's domain (already loaded at session start), (2) retrieve context via
`retrieve_for_synthesis`, (3) run the synthesis workflow (Workflow 0.1 from Appendix F) which includes
structural validation, (4) if the workflow produces a DEFINER gate (dialog node), send a `type: "gate"`
message to the client with the artifact preview, (5) the client responds with `type: "gate_response"`
indicating approval or rejection, (6) if approved, commit the artifact and advance ECS state, (7) send `type:
"response"` with the final content, referenced artifacts, and tokens consumed.

**DEFINER gate handling in chat.** When the synthesis workflow reaches a `dialog` node (the DEFINER review
gate), the chat surface pauses the workflow and sends a gate message to the client. The client UI (which is a
future deliverable — the chat surface provides the API, not the visual UI) presents the gate to the DEFINER
and sends back the response. This implements the `dialog` node contract from §11.1: "dialog nodes must produce
an event before resuming." The chat surface writes an Event before resuming the workflow.

**Context reset in chat.** If the trajectory regulator (L4) triggers a context reset (§10.2), the chat surface
sends a `type: "context_reset"` message to the client with the reason and a progress summary. The client then
starts a new logical conversation with the progress summary as seed context. The old session is archived in
trace.db with the reset event.

**Session context management.** Each chat message updates the `SessionContext` (turn count, context tokens
estimate, artifacts produced). The chat surface monitors context window utilization and triggers summarization
when `chat_max_history_turns` is reached. Summarization is a deterministic operation (not an LLM call) that
compresses older turns into a summary block.

**Budget integration.** Each model call within the chat workflow records token consumption through the BudgetManager. If the budget is exhausted (budget_hard_stop = True), the chat surface sends an error message to the client and blocks further synthesis calls.

The gate test uses FastAPI TestClient with WebSocket support to test: (a) connect to chat WebSocket, (b) send
message → receive response, (c) gate message appears when dialog node reached, (d) gate response resumes
workflow, (e) context reset message sent when triggered, (f) budget exhaustion blocks synthesis, (g) ACE
playbook loaded at session start.

---

## CHUNK-8.4: Review Queue + Artifact Browser API

```
CHUNK-8.4: Review Queue + Artifact Browser API
PHASE: 6
DEPENDS-ON: CHUNK-8.1, CHUNK-4.0b
CODER-PROFILE: L3
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  adapter/api/routes/review.py
  adapter/api/routes/artifacts.py
  tests/test_api_review_artifacts.py
INTERFACES:
  # Review Queue
  GET    /api/v1/reviews → list[ReviewQueueEntry] (paginated, filterable by domain/project/state)
  POST   /api/v1/reviews/{artifact_id}/approve → dict (autonomy_gate: admin, triggers ECS REVIEWED→APPROVED)
  POST   /api/v1/reviews/{artifact_id}/reject → dict (autonomy_gate: write, triggers ECS REVIEWED→FAILED)
  # Artifact Browser
  GET    /api/v1/artifacts → list[dict] (paginated, filterable by domain/project/ecs_state)
  GET    /api/v1/artifacts/{artifact_id} → dict
  GET    /api/v1/artifacts/{artifact_id}/versions → list[dict]
  GET    /api/v1/artifacts/{artifact_id}/evaluation → EvaluationScore + FaithfulnessResult +
DomainCoherenceResult
TESTS:
  tests/test_api_review_artifacts.py
GATE: uv run pytest tests/test_api_review_artifacts.py -xvs
```

### Prose

This chunk implements the Review Queue and Artifact Browser REST API endpoints. The Review Queue is the
DEFINER's primary interface for artifact governance — it surfaces artifacts that need review and allows the
DEFINER to approve or reject them. The Artifact Browser provides read-only access to the artifact catalog with
versioning and evaluation details.

**Review Queue.** `GET /api/v1/reviews` returns a paginated list of `ReviewQueueEntry` objects. The entries
are constructed by querying artifacts with ECS state "GENERATED" or "REVIEWED" that have not yet been
approved. Each entry aggregates data from ArtifactStore (version, content preview), EcsStore (current state),
and the evaluation pipeline (scores from L3a/L3b). The endpoint supports filtering by domain, project, and ECS
state. Pagination uses `?page=N&page_size=M` parameters with defaults from `SurfaceConfig.review_page_size`.

**Approve and reject.** `POST /api/v1/reviews/{artifact_id}/approve` is the most sovereignty-sensitive
endpoint in the system. It: (1) goes through the AutonomyGate with `action_type="approve_artifact"`,
`requested_level="admin"` — admin-level actions require DEFINER approval per §1.7, (2) calls
`EcsStore.transition(artifact_id, "REVIEWED", "APPROVED", superseded_by=None)`, (3) calls
`CanonicalStore.write_canonical(artifact_id, content, approved_by="definer")` — this creates the canonical
version, (4) writes an Event recording the approval, (5) returns the updated artifact state. The `reject`
endpoint follows the same pattern but transitions to "FAILED" state and does not create a canonical. Reject is
a write-level action (not admin) because it does not promote to canonical — per §1.6, canonical promotion is
the DEFINER's authority.

**Artifact Browser.** The artifact browsing endpoints are read-only and do not go through the AutonomyGate.
`GET /api/v1/artifacts` returns a paginated list of artifacts with filtering by domain, project, and ECS
state. `GET /api/v1/artifacts/{artifact_id}` returns a single artifact with its metadata and ECS state. `GET
/api/v1/artifacts/{artifact_id}/versions` returns all versions of an artifact from the VersionedArtifactStore
(Phase 2). `GET /api/v1/artifacts/{artifact_id}/evaluation` returns the evaluation results from L3a/L3b — the
faithfulness score, domain coherence score, and adversarial evaluation result. This enables the DEFINER to
make informed review decisions.

**ECS state display.** The artifact browser shows the current ECS state for each artifact. Per §9.3, the valid
states are SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED, with a branch to FAILED. The browser does
not allow direct ECS state changes — all state transitions go through the Review Queue (approve/reject) or the
workflow engine (generate, validate). This prevents unauthorized state manipulation through the API.

The gate test verifies: (a) review queue returns pending artifacts, (b) approve triggers ECS
REVIEWED→APPROVED, (c) approve triggers canonical write, (d) approve goes through AutonomyGate (admin level),
(e) reject triggers ECS REVIEWED→FAILED, (f) artifact list returns paginated results, (g) artifact versions
endpoint works, (h) artifact evaluation endpoint returns scores, (i) read-only endpoints do not require
AutonomyGate.

---

## CHUNK-8.5: MCP Server

```
CHUNK-8.5: MCP Server
PHASE: 6
DEPENDS-ON: CHUNK-8.1, CHUNK-8.0b
CODER-PROFILE: L2
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  adapter/mcp/server.py (MCP server implementation)
  adapter/mcp/tools/search.py (aip_search tool)
  adapter/mcp/tools/projects.py (aip_project_list, aip_project_create tools)
  adapter/mcp/tools/artifacts.py (aip_artifact_list, aip_artifact_approve tools)
  adapter/mcp/tools/trace.py (aip_trace_query tool)
  adapter/mcp/tools/config.py (aip_config_read, aip_config_write tools)
  adapter/mcp/__init__.py
  tests/test_mcp_server.py
INTERFACES:
  # MCP server
  class AipMcpServer:
      def __init__(self, container: AipContainer) -> None: ...
      async def start(self, transport: str = "stdio") -> None: ...
      async def stop(self) -> None: ...
      def list_tools(self) -> list[McpToolDef]: ...
  # MCP tools
  aip_search(query: str, domain: str | None) → list[dict]        # read, uses LexicalStore + VectorStore
  aip_project_list(status: str | None) → list[dict]              # read, uses ProjectStore
  aip_project_create(name: str, domain: str) → dict              # write, uses ProjectStore + AutonomyGate
  aip_artifact_list(project_id: str, ecs_state: str | None) → list[dict]  # read, uses ArtifactStore
  aip_artifact_approve(artifact_id: str) → dict                   # admin, uses EcsStore + CanonicalStore +
AutonomyGate
  aip_trace_query(session_id: str, limit: int) → list[dict]      # read, uses TraceStore
  aip_config_read(key: str) → dict                                # read, reads from config
  aip_config_write(key: str, value: str) → dict                   # admin, uses AutonomyGate
TESTS:
  tests/test_mcp_server.py
GATE: uv run pytest tests/test_mcp_server.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the Model Context Protocol (MCP) server that exposes AIP as a set of tools that external
applications (IDEs, AI assistants, custom integrations) can call. MCP is the integration surface defined in
§3: "MCP/API" and §7.2: "MCP". Per Appendix D, two critical constraints apply: "MCP ≠ bypass" (MCP tools go
through the same Protocol layer as all other surfaces) and "MCP ≠ vector_store.retrieve() directly" (MCP tools
access storage through Protocols, not direct database connections).

**AipMcpServer.** The MCP server is an adapter-layer component that receives an `AipContainer` (the DI
container from CHUNK-8.1) and registers all AIP tools. The server supports two transports: `stdio` (for CLI
integration — `uv run aip mcp` starts the server) and `sse` (for HTTP integration — connects to the FastAPI
app). The `list_tools()` method returns `McpToolDef` definitions for all registered tools, including the
`autonomy_level` and `model_gen_assumption` fields.

**Tool definitions and autonomy levels.** Each MCP tool is classified by autonomy level: "read" tools (search,
list, query) do not go through the AutonomyGate; "write" tools (create project) go through the gate but are
auto-granted; "admin" tools (approve artifact, write config) require DEFINER approval. The autonomy level is
declared in the `McpToolDef` and enforced by the `AipMcpServer` before dispatching each tool call. This is the
same enforcement mechanism as the REST API — no MCP tool can bypass the AutonomyGate.

**aip_search tool.** This is the primary retrieval tool. It combines `LexicalStore.search` (FTS5) and
`VectorStore.retrieve` (semantic) to provide hybrid search. The tool: (1) runs FTS5 search for lexical
matches, (2) runs vector retrieval for semantic matches, (3) merges and deduplicates results, (4) returns a
list of Chunk-like dicts. Per Appendix D: "MCP ≠ vector_store.retrieve() directly" — the tool goes through the
Protocol, not the implementation. The tool is classified as `autonomy_level="read"` — retrieval does not
modify state.

**aip_artifact_approve tool.** This is the most sensitive MCP tool. It approves an artifact for canonical
promotion — equivalent to the REST API's `POST /api/v1/reviews/{artifact_id}/approve`. It: (1) goes through
the AutonomyGate with `requested_level="admin"`, (2) if granted, triggers the ECS transition and canonical
write, (3) if not granted, returns an error indicating DEFINER approval is required. This tool is classified
as `autonomy_level="admin"` and carries `model_gen_assumption="Models should not autonomously approve
artifacts"` — per §1.7, canonical promotion is the DEFINER's authority.

**aip_trace_query tool.** This tool queries trace events for observability. It reads from `TraceStore.query_events()` and returns structured trace data. This is useful
for debugging sessions and understanding model behavior. It is classified as `autonomy_level="read"`.

The gate test verifies: (a) MCP server starts and lists tools, (b) each tool returns expected output type, (c)
read tools do not go through AutonomyGate, (d) write tools go through AutonomyGate (auto-granted), (e) admin
tools go through AutonomyGate (require DEFINER approval), (f) `aip_search` returns merged FTS5 + vector
results, (g) `aip_artifact_approve` triggers ECS transition, (h) adapter layer does not import orchestration,
(i) no MCP tool imports storage implementations directly.

---

## CHUNK-8.6: Admin Console + Memory Inspector API

```
CHUNK-8.6: Admin Console + Memory Inspector API
PHASE: 6
DEPENDS-ON: CHUNK-8.1, CHUNK-7.1
CODER-PROFILE: L3
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  adapter/api/routes/admin.py
  adapter/api/routes/memory.py
  tests/test_api_admin_memory.py
INTERFACES:
  # Admin Console
  GET    /api/v1/admin/config → dict (current config)
  PATCH  /api/v1/admin/config → dict (autonomy_gate: admin)
  GET    /api/v1/admin/sexton/classifications → list[FailureClassification]
  GET    /api/v1/admin/sexton/audit → list[AutonomyEscalation] (stale rule audit results)
  GET    /api/v1/admin/sexton/playbook → list[AcePlaybookEntry]
  GET    /api/v1/admin/beast/status → dict (last run, next scheduled, health)
  GET    /api/v1/admin/router/weights → list[RoutingWeight]
  GET    /api/v1/admin/budget → dict (session/project/daily budget status)
  GET    /api/v1/admin/autonomy/log → list[AutonomyEscalation]
  # Memory Inspector
  GET    /api/v1/memory/trace/{session_id} → list[dict] (trace events for session)
  GET    /api/v1/memory/events/{project_id} → list[dict] (event timeline for project)
  GET    /api/v1/memory/search → list[Chunk] (combined lexical + vector search)
  GET    /api/v1/memory/entities → list[dict] (entity catalog)
  GET    /api/v1/memory/canonical → list[dict] (canonical artifact catalog)
TESTS:
  tests/test_api_admin_memory.py
GATE: uv run pytest tests/test_api_admin_memory.py -xvs
```

### Prose

This chunk implements the Admin Console and Memory Inspector API endpoints. The Admin Console is the DEFINER's
governance surface — it provides visibility into all actors (Sexton, Beast, Adaptive Router), configuration,
and the autonomy escalation audit trail. The Memory Inspector is the DEFINER's observability surface — it
provides read access to trace data, events, search results, entities, and canonical artifacts.

**Admin Console.** The admin endpoints are a mix of read-only and write operations. `GET /api/v1/admin/config` returns the current configuration as a nested dict. `PATCH /api/v1/admin/config` updates config values — this is an admin-level action that goes through the AutonomyGate. Per §1.7, config changes are DEFINER-gated. The Sexton endpoints
return classification results, stale rule audit results, and ACE playbook entries — all read-only,
    sourced from the Sexton actor and AcePlaybook orchestration components. The Beast status endpoint returns the last cadence run time, next scheduled run, and health check results. The Router weights endpoint returns current domain routing weights from the AdaptiveRouter. The Budget endpoint returns current budget status across all scopes. The Autonomy log endpoint returns recent `AutonomyEscalation` records — this is the DEFINER's audit trail
for all sovereignty decisions.
```

**Memory Inspector.** All memory inspector endpoints are read-only and do not go through the AutonomyGate. The
trace endpoint returns trace events for a specific session, allowing the DEFINER to understand what happened
during a session (what nodes were executed, what failures occurred, what interventions were applied). The
events endpoint returns an event timeline for a project. The search endpoint provides the same hybrid FTS5 +
vector search as the MCP `aip_search` tool but returns results in a web-friendly format. The entities endpoint
returns the entity catalog from `EntityStore.list_entities()`. The canonical endpoint returns the canonical
artifact catalog from `CanonicalStore.list_canonical()`.

**Observability design.** The memory inspector is designed for the DEFINER's use case, not for external
monitoring. Per §5.9: "For harness observability, not general state" — the trace data is for understanding
model behavior and diagnosing failures, not for building dashboards or alerting systems. The inspector
surfaces the same data that Sexton reads for failure classification, creating a shared observability
foundation.

The gate test verifies: (a) admin config read returns current config, (b) admin config write goes through
AutonomyGate, (c) Sexton classification results are readable, (d) Beast status is readable, (e) Router weights
are readable, (f) Budget status is readable, (g) Autonomy log returns escalation records, (h) trace events for
a session are readable, (i) hybrid search returns results, (j) entities and canonical catalogs are readable,
(k) all read-only endpoints skip AutonomyGate.

---

## CHUNK-8.7: Integration Test

```
CHUNK-8.7: Integration Test
PHASE: 6
DEPENDS-ON: CHUNK-8.3, CHUNK-8.4, CHUNK-8.5, CHUNK-8.6, CHUNK-7.6
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  tests/test_phase6_integration.py
INTERFACES:
  # Test scenarios:
  # 1. Full CLI round trip: init → project create → session start → status
  # 2. Full API round trip: create project → create session → chat → review → approve → browse
  # 3. MCP tool round trip: search → list artifacts → approve (with autonomy gate) → trace query
  # 4. Admin + memory inspector: config read → Sexton status → Beast status → trace inspection
  # 5. DEFINER sovereignty enforcement: verify no surface can bypass autonomy gates
  # 6. Appendix D constraint verification: UI ≠ authority, MCP ≠ bypass, MCP ≠ vector_store.retrieve() directly
  # 7. Cross-surface consistency: same data via API, CLI, and MCP
TESTS:
  tests/test_phase6_integration.py
GATE: uv run pytest tests/test_phase6_integration.py -xvs
```

### Prose

This chunk implements the Phase 6 integration test — a comprehensive end-to-end test that verifies the
complete surface-to-backend round trip across all surface channels (CLI, REST API, Chat, MCP). It extends the
Phase 5 integration test (CHUNK-7.6) which verified the self-improvement cycle; Phase 6's integration test
verifies that surfaces correctly expose and control that self-improving system.

**Scenario 1: Full CLI round trip.** Uses Click's `CliRunner` to execute: `aip init` → verify database files
created → `aip project create --name test --domain software_architecture` → verify project in list → `aip
session start --project-id X --domain software_architecture` → verify session created → `aip status` → verify
all subsystems reported. This tests the installation contract from §2.3.

**Scenario 2: Full API round trip.** Uses FastAPI `TestClient` to execute: `POST /api/v1/projects` → `POST
/api/v1/sessions` → `WS /api/v1/chat/{session_id}` (send message, receive response, handle gate) → `POST
/api/v1/reviews/{artifact_id}/approve` (with AutonomyGate) → `GET /api/v1/artifacts/{artifact_id}` (verify
APPROVED state and canonical exists). This tests the complete synthesis → review → approval pipeline through
surfaces.

**Scenario 3: MCP tool round trip.** Uses the MCP server in-process to execute: `aip_search` →
`aip_artifact_list` → `aip_artifact_approve` (verify admin-level gate blocks without DEFINER) →
`aip_trace_query`. This tests that MCP tools correctly go through Protocols and AutonomyGate.

**Scenario 4: Admin + memory inspector.** Uses TestClient to execute: `GET /api/v1/admin/config` → `GET
/api/v1/admin/sexton/classifications` → `GET /api/v1/admin/beast/status` → `GET
/api/v1/memory/trace/{session_id}` → `GET /api/v1/memory/search`. This tests that the admin and memory
inspector surfaces correctly compose orchestration components.

**Scenario 5: DEFINER sovereignty enforcement.** Verifies that: (a) approving an artifact without DEFINER auth
is blocked, (b) writing config without DEFINER auth is blocked, (c) MCP admin tools without DEFINER auth are
blocked, (d) the autonomy escalation audit log records all blocked attempts. This is the §1.7 enforcement
test.

**Scenario 6: Appendix D constraint verification.** Verifies that: (a) "UI ≠ authority" — no surface endpoint
can approve an artifact without the AutonomyGate, (b) "MCP ≠ bypass" — MCP tools cannot bypass Protocol
access, (c) "MCP ≠ vector_store.retrieve() directly" — MCP search tool uses LexicalStore and VectorStore
Protocols, not direct database access. These are the architectural invariants that surfaces must not violate.

**Scenario 7: Cross-surface consistency.** Verifies that the same data is accessible and consistent across
API, CLI, and MCP. For example, a project created via CLI should be visible via API `GET /api/v1/projects` and
MCP `aip_project_list`.

The gate test verifies all seven scenarios pass. All tests use in-process adapters (TestClient, CliRunner,
in-process MCP) — no external services or network access required, consistent with the deterministic CI rule.

---

## CHUNK-8.8: Cross-Cutting Gates

```
CHUNK-8.8: Cross-Cutting Gates — Network Isolation, Model-Name, DEFINER Sovereignty, Import Boundary, Appendix D
PHASE: 6
DEPENDS-ON: CHUNK-8.7, all prior chunks
CODER-PROFILE: L1
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  tests/test_phase6_gates.py
INTERFACES:
  # Gate tests:
  # 1. Network isolation: no outbound connections from adapter/ (except ModelProvider in production mode)
  # 2. Model-name gate: no hardcoded model names in adapter/ surface code
  # 3. DEFINER sovereignty gate: no surface can bypass AutonomyGate for admin actions
  # 4. Import boundary: adapter/ surfaces import Foundation and Orchestration correctly
  # 5. Appendix D constraints: UI ≠ authority, MCP ≠ bypass, MCP ≠ vector_store.retrieve() directly
  # 6. Config toggleability: all Phase 6 config sections are read and respected
  # 7. Existing Phase 0/1/2/3/4/5 gate tests still pass
TESTS:
  tests/test_phase6_gates.py
GATE: uv run pytest tests/test_phase6_gates.py tests/test_layering.py tests/test_storage_contracts.py -xvs
```

### Prose

This chunk implements the cross-cutting gate tests for Phase 6 — the final quality gate that verifies
architectural invariants across all surfaces. It extends the gate tests from CHUNK-7.7 (Phase 5) with
surface-specific checks.

**Network isolation.** Verifies that no adapter/ surface code makes outbound network connections during CI
mode. The FastAPI TestClient runs in-process. The MCP server runs in-process. The CLI uses CliRunner. The only
network-accessing code is `ModelProvider` (which is stubbed in CI mode) and the Ollama health check in `aip
init` (which is gracefully handled). No surface code imports `httpx`, `requests`, `urllib`, or `aiohttp`
outside of the model provider adapter.

**Model-name gate.** Verifies that no adapter/ surface code contains hardcoded model names (Claude, DeepSeek,
Qwen, etc.). All model references come from config through `ModelSlotResolver`. MCP tools that expose model
information read from the resolver, not from constants.

**DEFINER sovereignty gate.** This is the most critical Phase 6 gate. It verifies that: (a) every REST
endpoint with `autonomy_gate=True` in its `ApiRoute` definition actually checks the `AutonomyGate` before
executing, (b) every MCP tool with `autonomy_level="admin"` actually checks the `AutonomyGate`, (c) the CLI
config write command goes through the AutonomyGate, (d) there is no alternative code path that bypasses the
gate — no direct ECS transition, no direct canonical write, no direct config modification without gate
approval. This is the §1.7 enforcement: "No UI, workflow, Beast cadence, MCP call, or queued task may bypass
the DEFINER gates."

**Import boundary.** Verifies that: (a) `adapter/api/` does not
import from `adapter/mcp/` or `adapter/cli/` (surfaces are independent), (b) `adapter/api/` imports only
Foundation Protocols and schemas, and Orchestration types, (c) no surface code imports `asyncpg`, `sqlite3`,
or any database driver directly — all storage access is through Protocol injection, (d) the existing
`test_layering.py` still passes
with all Phase 6 code.
```

**Appendix D constraints.** Verifies: (a) "UI ≠ authority" — test that a raw HTTP POST to
`/api/v1/reviews/{id}/approve` without AutonomyGate approval is rejected, (b) "MCP ≠ bypass" — test that MCP
tool execution goes through the same Protocol layer as REST endpoints, (c) "MCP ≠ vector_store.retrieve()
directly" — inspect the MCP search tool's code and verify it calls `LexicalStore.search` and
`VectorStore.retrieve` through Protocol methods, not direct SQL or database access.

**Config toggleability.** Verifies that all Phase 6 config sections (`[api]`, `[cli]`, `[mcp]`, `[chat]`,
`[autonomy]`, `[lexical]`) are read and respected. Changing `api_port` in config changes the actual bind port.
Changing `chat_max_history_turns` changes the retention limit. Changing `autonomy.escalation_requires_definer`
to False auto-grants admin-level escalations. This is the §1.8 toggleability requirement.

**Existing gates.** Verifies that all Phase 0/1/2/3/4/5 gate tests still pass with Phase 6 code installed. This is the backward-compatibility guarantee — each phase builds on the previous, never breaks it.

The gate test verifies all seven gate categories pass. This is the final gate before the AIP 0.1 build is complete.

---

## Acceptance Criteria Mapping

Phase 6 directly satisfies the following architectural acceptance gates from §22:

```text
[01] Storage contracts pass:
     All Protocol implementations (including LexicalStore, CanonicalStore,
     EntityStore, AutonomyGate) pass the storage contract test suite.
     tests/test_storage_contracts.py

[02] Import boundaries pass:
     All adapter surfaces respect the three-layer import boundary.
     tests/test_layering.py

[03] Workflow 0.1 executable (extended by surfaces):
     Chat surface can trigger Workflow 0.1 end-to-end.
     Review queue can approve/reject artifacts from Workflow 0.1.

[35] Workflow 0.1 executable:
     Appendix F workflow runs end-to-end via chat surface.
     dialog gate pauses and resumes correctly through chat WebSocket.
     All five node types exercised through surface interaction.

[NEW — Phase 6 gates:]
[36] Installation contract implemented:
     `uv run aip init` detects RAM, configures vector backend,
     initializes schemas, validates Ollama, validates model slots,
     prints summary per §2.3.

[37] DEFINER sovereignty enforced:
     No surface endpoint or CLI command can bypass the AutonomyGate
     for admin-level actions. Autonomy escalation audit log is
     maintained and queryable.

[38] MCP constraints enforced:
     MCP tools do not bypass Protocols. MCP tools do not access
     storage directly. Per Appendix D: "MCP ≠ bypass",
     "MCP ≠ vector_store.retrieve() directly."

[39] Surface round trip functional:
     Full CLI → API → Chat → Review → MCP round trip passes
     integration test with deterministic fixtures.

[40] Cross-surface consistency:
     Same data accessible via API, CLI, and MCP with consistent
     results.
```

---

## Config Additions Summary

Phase 6 adds the following config sections to `aip.config.toml`:

```toml
[api]
host = "127.0.0.1"
port = 8000
cors_origins = ["http://localhost:3000"]
workers = 1
chat_max_history_turns = 50
review_page_size = 20
artifact_page_size = 20

[cli]
color = true
pager = true
output_format = "table"              # table / json / yaml

[mcp]
enabled = true
transport = "stdio"                   # stdio / sse
max_concurrent_tools = 5

[chat]
system_prompt_path = "prompts/chat_system.md"
max_context_turns = 50
auto_summarize_at = 40               # trigger summarization at this turn count

[autonomy]
default_level = "read"               # default autonomy for new sessions
escalation_requires_definer = true    # admin actions require DEFINER approval
audit_retention_days = 90
model_gen_assumption = "Models should not autonomously escalate to admin actions"

[lexical]
db_path = "db/lexical.db"
fts5_tokenizer = "unicode61"
```

All parameters are toggleable per §1.8. The `[autonomy]` section carries a `model_gen_assumption` field per §1.8 because the autonomy escalation behavior encodes assumptions about what actions models should be allowed to take autonomously — as models improve, these assumptions may need revision.
