# AIP 0.1 Phase 5 BuildSpec

**Product:** AI Poiesis (AIP) version 0.1  
**Architecture Revision:** 5.2  
**Build Phase:** 5 — Sexton Actor, ACE Playbook, Adaptive Router & Beast Cadence  
**Spec Revision:** 1.0  
**Date:** May 2026  
**Status:** Build Specification — for Grok Build execution  
**Supersedes:** N/A (initial Phase 5 spec)  
**DEFINER:** Moses Jorgensen

---

## Revision Log

### 1.0 — Initial

| Item | Decision | Rationale |
|---|---|---|
| Sexton failure classification | `orchestration/actors/sexton.py` — Sexton reads trace_events where
`failure_type IS NOT NULL` or where `outcome='failure' AND failure_type IS NULL`, classifies A–F per Appendix
E, writes back | §16.1: "Sexton classifies trace_events by failure_type A–F (Appendix E)"; §22 acceptance gate
[33]: "Sexton can read trace_events filtered by failure_type IS NOT NULL and produce ACE playbook entries. No
failure remains permanently unclassified." |
| ACE Playbook | `orchestration/ace_playbook.py` — SQLite-backed procedural intervention rules, loaded at
session start, curated by Sexton | §8.1: "ACE Playbook / SQLite — procedural intervention rules, loaded at
session start, curated by Sexton"; §16.1: "ACE playbook curation — derive and update procedural intervention
rules"; Type B failures in Appendix E: "L2 intervention: Add or strengthen playbook entry" |
| Stale rule audit | `orchestration/actors/sexton_audit.py` — audits ContractRule.model_gen_assumption per
§1.8 | §1.8: "On every model slot upgrade: audit the harness for stale assumptions"; §16.1: "stale rule audit
— audit ContractRule.model_gen_assumption per §1.8"; §7.1: "Rules compensating for model limitations carry
model_gen_assumption. Sexton audits those assumptions when model slots change." |
| Adaptive router | `orchestration/router.py` — implements §4.3 routing with exploration_weight, domain-based
weight tables | §4.3: "Default routing: highest-weight model for domain; Exploration: with probability =
exploration_weight, route to non-optimal model; Sexton role: recommend exploration_weight adjustments per
domain"; routing_outcomes table exists from Phase 0; routing logic deferred from Phase 3 and Phase 4 |
| Token budget system | `orchestration/budget.py` — per-session and per-project token budgets, BudgetStore
Protocol implementation | §6: BudgetStore Protocol listed; §5.10: state.db stores budgets; §11.1: "parallel
nodes inherit the parent workflow's budget"; acceptance gate [34] requires structural validation nodes consume
zero tokens (budget enforcement); budget tracking is prerequisite for Beast cadence and Sexton cost awareness
|
| Beast actor | `orchestration/actors/beast.py` — cadence-based corpus and entity maintenance | §3 layer
model: "Beast — cadence / corpus / entity maintenance"; §5.10: state.db stores cadence_state; Beast is listed
as a separate actor from Sexton per Appendix D: "Beast ≠ Sexton" |
| Config additions | `[sexton]`, `[ace_playbook]`, `[router]`, `[budget]`, `[beast]` sections in
`aip.config.toml` | §1.8 toggleable; all Sexton thresholds, router exploration weights, budget limits, Beast
cadence intervals configurable |

---

## Phase 5 Scope

Phase 5 delivers the autonomous actor layer that makes the harness self-improving. Sexton reads trace data to
classify failures, curates the ACE Playbook with procedural intervention rules, and audits stale model
assumptions. The Adaptive Router uses accumulated routing_outcomes data to optimize model selection per
domain. Beast maintains the corpus and entity stores on cadence. The token budget system provides the
financial and resource governance that all actors and workflows respect.

Phase 4 delivered production-grade persistence (pgvector), real model integration in all node stubs, and L3a
Stage 2/3 evaluation. But the system still operates reactively — failures are detected and corrected within
sessions, but no actor reads the failure history to improve future behavior. Phase 5 introduces the feedback
loop: Sexton classifies failures and derives rules, the ACE Playbook loads those rules at session start to
prevent recurrence, the Adaptive Router optimizes model selection, and Beast maintains the corpus so retrieval
quality does not degrade over time.

**In scope:**

- CHUNK-7.0a: Schema additions — `SextonConfig`, `AcePlaybookEntry`, `BudgetConfig`, `RoutingWeight`,
`BeastCadenceConfig`, `FailureClassification` dataclasses + Protocol amendments (`BudgetStore` methods,
`ProjectStore.list_projects`, `EntityStore` new methods) + Config extensions (L1, append-only)
- CHUNK-7.0b: Token budget system — `BudgetManager` with per-session/per-project limits, BudgetStore SQLite
implementation, budget enforcement in workflow engine (L2/L5, orchestration)
- CHUNK-7.1: Sexton failure classification — reads trace_events, classifies A–F per Appendix E, writes back to
trace_events, produces structured `FailureClassification` output (L2/L4, orchestration, uses sexton model
slot)
- CHUNK-7.2: ACE Playbook — SQLite-backed procedural intervention rules, `load_playbook(domain)` for session
start, `add_entry`, `deprecate_entry`, Sexton curates entries from failure classifications (L2, orchestration)
- CHUNK-7.3: Sexton stale rule audit — reads ContractRules with `model_gen_assumption IS NOT NULL`, compares
against current model slot capabilities, flags deprecated rules for DEFINER review (L1/L2, orchestration, uses
sexton model slot)
- CHUNK-7.4: Adaptive router — implements §4.3 routing with domain weight tables, exploration_weight, Sexton
adjustment recommendations, routing_outcomes read/write (L2/L5, orchestration)
- CHUNK-7.5: Beast actor — cadence-based corpus maintenance (re-index stale vectors), entity store
maintenance, cadence scheduling (L2/L5, orchestration)
- CHUNK-7.6: Integration test — full cycle: failure → Sexton classification → ACE entry → session replay with
playbook → router optimization → Beast cadence run
- CHUNK-7.7: Network isolation and model-name gate — cross-cutting test extending CHUNK-6.6

**Out of scope:**

- UI / MCP / CLI surfaces (Phase 6)
- Vigil actor — compiled knowledge maintenance (deferred per §3: "Vigil — compiled knowledge maintenance,
deferred")
- Additional workflows beyond Workflow 0.1
- Canonical store implementation (future phase)
- LexicalStore (FTS5) implementation (future phase)
- Model provider HTTP dispatch (Phase 3 provided structured placeholder; real HTTP calls are deployment
concern, not spec concern)

---

## Phase 4 Assumptions (Architectural Phase 4 = CHUNK-6.x series)

Phase 5 chunks depend on the following Phase 4 deliverables being merged and green:

| CHUNK-6.x | Deliverable | Phase 5 Dependency |
|---|---|---|
| 6.0a | `foundation/schemas.py` — `PgvectorConfig`, `MigrationStatus`, `EvaluationScore`,
`FaithfulnessResult`, `DomainCoherenceResult`, `VectorBackendType` | 7.0a appends |
| 6.0a | `foundation/protocols.py` — `VectorStore.health_check`, `VectorStore.count` | 7.5 (Beast uses count
for corpus maintenance) |
| 6.0b | `adapter/vector/pgvector_store.py` — `PgvectorStore` | 7.5 (Beast re-indexes via VectorStore) |
| 6.1 | `orchestration/nodes/synthesis.py` — promoted with ModelSlotResolver | 7.4 (router intercepts model
calls) |
| 6.2 | `orchestration/nodes/adversarial_eval.py` — promoted; `orchestration/nodes/faithfulness.py`,
`domain_coherence.py` | 7.1 (Sexton reads L3a/L3b trace events) |
| 6.3 | `adapter/vector/factory.py` — `create_vector_store` | 7.5 (Beast uses factory) |
| 6.4 | Production hardening — health checks, graceful degradation | 7.5 (Beast checks health before
maintenance) |
| 6.5 | Integration test | 7.6 extends |
| 6.6 | Network isolation gate | 7.7 extends |

Phase 3 dependencies (transitive through Phase 4):

| CHUNK-5.x | Deliverable | Phase 5 Dependency |
|---|---|---|
| 5.0a | `foundation/schemas.py` — `TrajectorySignal`, `SessionContext`, `ModelSlotConfig` | 7.0a appends; 7.1
(Sexton reads TrajectorySignals) |
| 5.0a | `foundation/protocols.py` — `TraceStore.query_events`, `ModelProvider`, `EmbeddingProvider` | 7.1
(Sexton queries TraceStore); 7.3 (audit uses ModelProvider slot) |
| 5.0b | `adapter/model_slot_resolver.py` — `ModelSlotResolver` | 7.1 (Sexton calls via sexton slot); 7.4
(router resolves slots) |
| 5.2–5.5 | L4 trajectory detectors + regulator | 7.1 (Sexton reads Type D/E/F trace events from L4) |
| 5.6 | Context reset protocol | 7.1 (Sexton measures intervention effectiveness) |
| 5.7 | `orchestration/session.py` — `SessionManager` | 7.0b (budget tracks per-session) |
| 5.8 | Integration test | 7.6 extends |
| 5.9 | Network isolation gate | 7.7 extends |

Phase 2 dependencies (transitive through Phase 3/4):

| CHUNK-4.x | Deliverable | Phase 5 Dependency |
|---|---|---|
| 4.0a | `foundation/schemas.py` — `ReviewVerdict`, `Event`, `FailureTypeCode` | 7.0a appends; 7.1 (Sexton
uses FailureTypeCode) |
| 4.0b | `foundation/ecs_graph.py` — `VALID_TRANSITIONS`, `InvalidTransitionError` | 7.6 (integration test
uses ECS transitions) |
| 4.5 | `orchestration/engine.py` — YAML workflow engine | 7.0b (budget enforcement hooks into engine); 7.4
(router intercepts engine model calls) |
| 4.6 | `workflows/synthesis_session_v1.yaml` | 7.6 (integration test runs workflow) |

Phase 1 dependencies (transitive through Phase 2/3/4):

| CHUNK-1.x | Deliverable | Phase 5 Dependency |
|---|---|---|
| 1.0a | `foundation/schemas.py` — `Chunk`, `ContractRule`, `RetrievalResult` | 7.0a appends; 7.3 (Sexton
audits ContractRule); 7.5 (Beast re-indexes Chunk) |
| 1.0a | `foundation/protocols.py` — `VectorStore`, `TraceStore`, `EcsStore`, `EventStore`, `ArtifactStore` |
7.0a appends; 7.1, 7.2, 7.5 all use existing Protocols |
| 1.0b | `adapter/vector/sqlite_vss_store.py` — `SqliteVssVectorStore` | 7.5 (Beast maintains vectors in both
backends) |
| 1.2 | `orchestration/validation.py` — `structural_validate` | 7.2 (ACE Playbook entries affect validation
behavior) |

Phase 0 dependencies:

| CHUNK-0.x | Deliverable | Phase 5 Dependency |
|---|---|---|
| 0.2 | `config/aip.config.toml` — base config | 7.0a extends config |
| 0.3 | `db/routing_outcomes` table schema | 7.4 (router reads/writes routing_outcomes) |
| 0.5 | `db/trace_events` table schema | 7.1 (Sexton reads trace_events) |

**Critical note on CHUNK-7.0a:** This chunk appends to `foundation/schemas.py` and amends
`foundation/protocols.py` — the same append-only/amend-by-addition pattern as CHUNK-1.0a, CHUNK-4.0a,
CHUNK-5.0a, and CHUNK-6.0a. No existing Phase 0, Phase 1, Phase 2, Phase 3, or Phase 4 code is deleted or
rewritten.

**Continuity note:** The Sexton and Beast actors are orchestration-layer components that compose existing
Protocol implementations. They never import adapter implementations directly — they receive Protocol instances
via dependency injection. This preserves the three-layer import boundary. The Adaptive Router intercepts model
slot resolution (it wraps ModelSlotResolver), but it does not replace it — the router adds a routing decision
layer on top of the existing slot resolution.

---

## Process Rules

These rules are binding for all work against this BuildSpec. They are inherited from Phase 1 Rev 1.3, Phase 2
Rev 1.2, Phase 3 Rev 1.1, and Phase 4 Rev 1.0 and must be followed without exception:

1. **Continuity Check.** Before starting any chunk, read `WORKLOG.md` and verify all DEPENDS-ON chunks are
merged and green. This includes all Phase 1 (1.x), Phase 2 (4.x), Phase 3 (5.x), and Phase 4 (6.x) chunks. If
any dependency is not met, block and report.

2. **WORKLOG append-only.** Every chunk completion appends a new work record to `WORKLOG.md`. Never overwrite,
delete, or reorder existing entries. Each record must include Task ID, Agent, Task description, Work Log
(concrete steps), and Stage Summary.

3. **Amend by addition — schemas.** New dataclasses, type aliases, and enums are appended to
`foundation/schemas.py`. Never modify, reorder, or delete existing Phase 0/1/2/3/4 definitions. The test suite
verifies this by importing Phase 0/1/2/3/4 types and asserting they still work.

4. **Amend by addition — protocols.** New methods are appended as stubs to existing Protocol classes in
`foundation/protocols.py`. New Protocol classes (BudgetStore, ProjectStore methods) are added as new class
definitions or appended method stubs. Never redeclare an existing Protocol class. The ANNEX shows individual
method stubs for amendments and full class blocks for new Protocols only.

5. **Deterministic CI.** All gate tests must pass without network access, API keys, secrets, or external
services. The `ci_mode` flag on ModelSlotResolver controls this for model calls. Sexton classification tests
use fixture trace data. Beast cadence tests use mock timers. Router tests use seeded exploration weights.

6. **Push after each chunk.** After a chunk passes its gate test (`uv run pytest <test_file> -xvs`), commit
and push before starting the next chunk. One chunk per commit.

7. **Import boundaries.** Foundation never imports orchestration or adapter. Adapter may import foundation but
not orchestration. Orchestration may import both foundation and adapter. The layering test
(`tests/test_layering.py`) enforces this. **Actor-specific addition:** Sexton, Beast, and the Adaptive Router
are orchestration components. They import foundation Protocols and schemas. They import adapter
ModelSlotResolver (which is in the adapter layer). They never import adapter storage implementations directly
— all storage access is via Protocol injection.

8. **No hardcoded model names.** Per §4.1, all model references resolve through named slots from config. No
model name may appear in any `orchestration/` or `foundation/` file. The test_no_hardcoded_model_names test
enforces this. Sexton uses the `sexton` model slot; Beast uses no model slot (it is a cadence scheduler, not
an LLM consumer).

9. **Qualified phase references.** Always use qualified terminology: "Architectural Phase 5" for the logical
scope, "CHUNK-7.x" for build units, "repo 3.x" for historical commits. Never use bare "Phase 5" without
qualification.

10. **Repo overlap reconciliation.** Before building any CHUNK-7.x, check whether repo 3.x code already
implements part of the spec (especially Sexton, ACE, budget work). If overlap exists, extend existing code to
meet the spec (amend by addition) rather than rewriting from scratch. Document reconciliation in WORKLOG.

11. **Sexton classification ≠ resolution.** Per Appendix D: "Failure classification ≠ failure resolution. The
harness corrects; Sexton improves rules." Sexton classifies and derives rules; it never modifies the live
workflow. ACE Playbook entries take effect at the next session start, not mid-session.

12. **Model-gen-assumption tagging.** Every ACE Playbook entry and every ContractRule that Sexton audits must
carry `model_gen_assumption` per §1.8. This is not optional. The test suite verifies that all new entries
carry this field.

---

## Repo State Reconciliation

**Important:** This spec was written when the codebase was assumed to contain Phase 1, Phase 2, Phase 3, and
Phase 4 code. The actual repo contains additional work from historical chunk series 2.x (YAML engine
mechanics) and 3.x (L4/Sexton/ACE/budget foundation). This section documents the reconciliation.

### What this spec introduces that does NOT yet exist in code

| Type/Method | CHUNK | Notes |
|-------------|-------|-------|
| `SextonConfig` dataclass | 7.0a | New — no prior implementation |
| `AcePlaybookEntry` dataclass | 7.0a | New — no prior implementation |
| `BudgetConfig` dataclass | 7.0a | New — no prior implementation |
| `RoutingWeight` dataclass | 7.0a | New — no prior implementation |
| `BeastCadenceConfig` dataclass | 7.0a | New — no prior implementation |
| `FailureClassification` dataclass | 7.0a | New — no prior implementation |
| `BudgetStore` Protocol methods | 7.0a | New — BudgetStore listed in §6 but not implemented |
| `ProjectStore.list_projects` method | 7.0a | New method stub |
| `orchestration/budget.py` — `BudgetManager` | 7.0b | New — no prior budget enforcement |
| `orchestration/actors/sexton.py` — `Sexton` | 7.1 | New — repo 3.x may have partial Sexton |
| `orchestration/ace_playbook.py` — `AcePlaybook` | 7.2 | New — repo 3.x may have partial ACE |
| `orchestration/actors/sexton_audit.py` — `SextonAudit` | 7.3 | New — no prior audit implementation |
| `orchestration/router.py` — `AdaptiveRouter` | 7.4 | New — routing_outcomes table exists, logic does not |
| `orchestration/actors/beast.py` — `Beast` | 7.5 | New — no prior Beast implementation |
| Integration test | 7.6 | New — full actor cycle test |
| Phase 5 network isolation gate | 7.7 | Extend CHUNK-6.6 |

### What already exists that may overlap

| Repo Series | What It Built | Overlap With |
|-------------|--------------|-------------|
| Repo 3.x (CHUNK-3.1–3.12) | L4/Sexton/ACE/budget foundation | CHUNK-7.1 (Sexton), 7.2 (ACE Playbook), 7.0b
(budget) |

**Build strategy:** Where repo 3.x code already exists (especially Sexton, ACE, budget work), extend it to
meet the spec rather than replacing it. The spec is the authoritative target; existing code is a head start,
not a conflict. Document all reconciliations in WORKLOG.

---

## Dependency DAG

```
CHUNK-6.0a ── CHUNK-6.0b ── CHUNK-6.1 ── CHUNK-6.2 ── CHUNK-6.3 ── CHUNK-6.4 ── CHUNK-6.5 ── CHUNK-6.6
     │              │            │            │            │            │            │            │
     │              │            │            │            │            │            │            │
CHUNK-7.0a ────── CHUNK-7.0b ─┼────────────┼────────────┼────────────┼────────────┼────────────┤
     │              │           │            │            │            │            │            │
     │              │           ├──── CHUNK-7.1 (sexton)  │            │            │            │
     │              │           │            │            │            │            │            │
     │              │           │  CHUNK-7.2 (ace playbook)             │            │            │
     │              │           │  (after 7.1 — Sexton curates)         │            │            │
     │              │           │            │            │            │            │            │
     │              │           ├──── CHUNK-7.3 (sexton audit)          │            │            │
     │              │           │            │            │            │            │            │
     │              ├──── CHUNK-7.4 (router) │            │            │            │            │
     │              │           │            │            │            │            │            │
     │              ├──── CHUNK-7.5 (beast)  │            │            │            │            │
     │              │           │            │            │            │            │            │
     └────────────────────────────────────────────────────── CHUNK-7.6 ─┘            │
                                                              (integration)          │
                                                                       │              │
                                                                  CHUNK-7.7 ─────────┘
                                                                   (gate)

Linearized build order:
  7.0a → 7.0b (parallel with 7.1 after 5.0b + 6.0a) → 7.1 → 7.2 → 7.3
       → 7.0b → 7.4 (parallel with 7.5)
       → 7.5 (after 7.0b)
       → 7.6 (after 7.3, 7.4, 7.5, 6.5)
       → 7.7 (after all)

Parallel groups:
  Group A: [7.0a]                                    — schema + protocol additions
  Group B: [7.0b] (after 7.0a, CHUNK-5.7)           — budget system
  Group C: [7.1] (after 7.0a, CHUNK-5.0b, CHUNK-6.2) — Sexton classification
  Group D: [7.2] (after 7.1)                         — ACE Playbook (Sexton curates)
  Group E: [7.3] (after 7.1)                         — Sexton stale rule audit
  Group F: [7.4] (after 7.0b, CHUNK-5.0b)            — Adaptive router
  Group G: [7.5] (after 7.0b, CHUNK-6.0b)            — Beast actor
  Group H: [7.6] (after 7.3, 7.4, 7.5, CHUNK-6.5)   — integration test
  Group I: [7.7] (after all)                          — cross-cutting gate
```

The key architectural insight: **Groups C–E and Groups F–G are independent parallel paths.** The Sexton path
(7.1 → 7.2 → 7.3) touches the orchestration actor layer and reads trace data. The router/Beast path (7.4, 7.5)
touches the orchestration scheduling layer and reads routing_outcomes / vector data. Both paths depend on 7.0b
(budget). All paths converge at the integration test (7.6), which verifies the full self-improvement cycle.

---

## CHUNK-7.0a: Schema Additions + Protocol Amendments + Config Extensions

```
CHUNK-7.0a: Schema Additions + Protocol Amendments + Config Extensions
PHASE: 5
DEPENDS-ON: CHUNK-6.0a, CHUNK-5.0a
CODER-PROFILE: L1
CONTEXT-BUDGET: ~3,500 tokens
FILES:
  foundation/schemas.py (append only — do not modify existing Phase 0/1/2/3/4 enums or dataclasses)
  foundation/protocols.py (amend by addition — add methods to existing Protocol classes)
INTERFACES:
  @dataclass
  class SextonConfig:
      classification_batch_size: int     # how many trace events to classify per run
      classification_interval_seconds: int  # cadence for Sexton classification runs
      audit_on_slot_change: bool         # trigger stale rule audit when model slot config changes
      max_unclassified_before_alert: int # alert DEFINER if unclassified failures exceed this
  @dataclass
  class AcePlaybookEntry:
      entry_id: str
      domain: str
      failure_type: str                 # A–F per Appendix E
      intervention: str                 # what to do when this pattern is detected
      condition: str                    # when this rule applies (Jinja2 expression)
      model_gen_assumption: str | None  # §1.8 — what model limitation this rule compensates for
      source_trace_ids: list[str]       # trace events that produced this entry
      confidence: float                 # 0.0–1.0 — Sexton's confidence in this rule
      created_at: str                   # ISO 8601
      deprecated_at: str | None         # ISO 8601 — set when Sexton deprecates
      deprecated_reason: str | None     # why deprecated (e.g., "model slot upgrade made assumption stale")
  @dataclass
  class BudgetConfig:
      session_token_limit: int          # max tokens per session
      project_token_limit: int          # max tokens per project
      daily_token_limit: int            # max tokens per day
      budget_warning_threshold: float   # 0.0–1.0 — warn when this fraction consumed
      budget_hard_stop: bool            # true = block when limit reached; false = warn only
  @dataclass
  class RoutingWeight:
      model_slot: str                   # which model slot
      domain: str                       # which domain
      weight: float                     # 0.0–1.0 — routing weight (higher = preferred)
      exploration_weight: float         # 0.0–1.0 — probability of exploring non-optimal
      sample_count: int                 # number of routing_outcomes used to compute this weight
      updated_at: str                   # ISO 8601
  @dataclass
  class BeastCadenceConfig:
      corpus_reindex_interval_seconds: int  # how often Beast re-indexes stale vectors
      entity_maintenance_interval_seconds: int  # how often Beast validates entity consistency
      health_check_interval_seconds: int   # how often Beast checks subsystem health
      max_reindex_batch_size: int          # max vectors to re-index per cadence run
  @dataclass
  class FailureClassification:
      trace_event_id: int               # the trace event being classified
      failure_type: str                 # A–F per Appendix E
      confidence: float                 # 0.0–1.0 — Sexton's confidence in classification
      rationale: str                    # why Sexton assigned this type
      model_slot_used: str              # always "sexton"
      tokens_consumed: int
      model_gen_assumption: str | None  # §1.8
      classified_at: str                # ISO 8601
  # Type aliases
  FailureTypeCode = Literal["A", "B", "C", "D", "E", "F"]  # already defined in Phase 2; re-declaration for reference only
  BudgetScope = Literal["session", "project", "daily"]
  # Protocol amendments in foundation/protocols.py (append method stubs only — do NOT redeclare class):
  # BudgetStore: add new Protocol class (does not exist in Phase 0/1/2/3/4)
  class BudgetStore(Protocol):
      async def get_budget(self, scope: BudgetScope, scope_id: str) -> dict: ...
      async def record_usage(self, scope: BudgetScope, scope_id: str, tokens_used: int, cost_usd: float, model_slot: str) -> None: ...
      async def check_limit(self, scope: BudgetScope, scope_id: str) -> bool: ...
  # ProjectStore: add list_projects method stub to existing class
  async def list_projects(self, status: str | None = None) -> list[dict]: ...
TESTS:
  tests/test_phase5_schema_additions.py
GATE: uv run pytest tests/test_phase5_schema_additions.py -xvs
```

### Prose

This chunk establishes the shared data types, protocol amendments, and configuration extensions that all
subsequent Phase 5 chunks depend on. It does seven things:

**1. Append `SextonConfig` dataclass to `foundation/schemas.py`.** The `SextonConfig` dataclass captures all
Sexton-specific configuration: the classification batch size (how many trace events to process per
classification run), the classification interval (cadence between runs), whether to trigger a stale rule audit
when model slot config changes, and the threshold for alerting the DEFINER when too many failures remain
unclassified. These parameters map to the `[sexton]` config section. The classification batch size controls
the cost of each Sexton run — a local Qwen3-Coder model is cheap, but the batch size still needs to be bounded
to avoid runaway token consumption. The audit_on_slot_change flag implements §1.8's requirement that "on every
model slot upgrade: audit the harness for stale assumptions" — when the config changes, Sexton should be
triggered to audit. Append only — do not modify or reorder any existing definitions.

**2. Append `AcePlaybookEntry` dataclass.** The `AcePlaybookEntry` dataclass captures a single procedural
intervention rule in the ACE Playbook. Each entry is tied to a specific domain and failure type (A–F per
Appendix E), describes the intervention to apply when the condition is met, and carries the source trace event
IDs that led Sexton to derive this rule. The `model_gen_assumption` field is mandatory per §1.8 — every
procedural rule must declare which model limitation it compensates for. The `confidence` field captures
Sexton's confidence in the rule, which is derived from how many trace events produced it and how consistent
they are. The `deprecated_at` and `deprecated_reason` fields support the §1.8 lifecycle: when a model slot
upgrade makes an assumption stale, Sexton deprecates the entry rather than deleting it (supersession, not
deletion, per Appendix D). This dataclass is the schema
for the SQLite `ace_playbook` table that CHUNK-7.2 creates.
```

**3. Append `BudgetConfig`, `RoutingWeight`, `BeastCadenceConfig`,
    and `FailureClassification` dataclasses.** The `BudgetConfig` dataclass captures token budget parameters: per-session, per-project, and per-daily limits; the warning threshold (fraction consumed before surfacing a warning to the DEFINER); and whether budget limits are hard stops (block further model calls) or soft (warn only). The `RoutingWeight` dataclass captures a single domain×model routing weight from the routing_outcomes table,
with the exploration_weight per §4.3. The `BeastCadenceConfig` dataclass captures Beast's cadence intervals
for corpus re-indexing, entity maintenance, and health checking. The `FailureClassification` dataclass captures Sexton's classification output
for a single trace event: the assigned failure type, confidence, rationale, and the model_gen_assumption per §1.8.
```

**4. Add `BudgetScope` type alias.** A `Literal["session", "project", "daily"]` type alias that the
BudgetManager uses to scope budget tracking. This maps to the three budget dimensions in `BudgetConfig`.

**5. Add `BudgetStore` Protocol in `foundation/protocols.py`.** This is a new Protocol, not an amendment to an
existing one. It abstracts budget read/write operations so that orchestration code never imports SQLite
directly. The `get_budget` method returns current budget status (consumed, remaining, limit). The
`record_usage` method records token consumption after each model call. The `check_limit` method returns
whether the budget has remaining capacity (used by the workflow engine before dispatching model calls).
BudgetStore is listed in §6 as a required Protocol.

**6. Amend `ProjectStore` Protocol.** Phase 0 defined `ProjectStore` but did not specify methods beyond the
basic CRUD. Phase 5 adds `list_projects(status)` → `list[dict]` — a method that returns all projects,
optionally filtered by status. Beast needs this to iterate over projects for corpus maintenance.

**7. Config additions.** Phase 5 extends `config/aip.config.toml` with:

```toml
[sexton]
classification_batch_size = 50
classification_interval_seconds = 300     # 5 minutes
audit_on_slot_change = true
max_unclassified_before_alert = 10

[ace_playbook]
db_path = "db/ace_playbook.db"
auto_derive = true                        # Sexton auto-derives entries from classifications
min_confidence = 0.70                     # minimum Sexton confidence to auto-promote entry

[router]
default_exploration_weight = 0.10
min_sample_count = 10                     # need at least 10 routing_outcomes before adjusting weights
weight_decay = 0.95                       # exponential decay for old routing outcomes
domain_overrides = {}                     # per-domain exploration_weight overrides

[budget]
session_token_limit = 500000
project_token_limit = 5000000
daily_token_limit = 10000000
budget_warning_threshold = 0.80
budget_hard_stop = true

[beast]
corpus_reindex_interval_seconds = 3600    # 1 hour
entity_maintenance_interval_seconds = 1800  # 30 minutes
health_check_interval_seconds = 60
max_reindex_batch_size = 1000
```

The gate test verifies: (a) all new dataclasses can be instantiated with required fields, (b)
`AcePlaybookEntry` carries `model_gen_assumption` field per §1.8, (c) `FailureClassification` carries
`model_gen_assumption` field per §1.8, (d) `SextonConfig` has all classification parameters, (e)
`BudgetConfig` has session/project/daily limits, (f) `RoutingWeight` has domain, weight, exploration_weight,
(g) `BudgetStore` Protocol has `get_budget`, `record_usage`, `check_limit` methods, (h) `ProjectStore` has
`list_projects` method, (i) existing Phase 0/1/2/3/4 schema enums and dataclasses are not broken, (j) existing
Protocol methods still exist.

### ANNEX

**`foundation/schemas.py` (append to existing file):**

```python
# --- Phase 5 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type alias for budget scoping
BudgetScope = Literal["session", "project", "daily"]


@dataclass
class SextonConfig:
    """Configuration for the Sexton failure classification actor.

    Per §16.1: Sexton reads trace_events and classifies failures A-F.
    Per §1.8: Sexton audits stale model assumptions on slot changes.
    """
    classification_batch_size: int = 50
    classification_interval_seconds: int = 300
    audit_on_slot_change: bool = True
    max_unclassified_before_alert: int = 10


@dataclass
class AcePlaybookEntry:
    """A single procedural intervention rule in the ACE Playbook.

    Per §8.1: procedural intervention rules, loaded at session start.
    Per §16.1: curated by Sexton.
    Per §1.8: every rule must carry model_gen_assumption.
    Per Appendix E Type B: "Add or strengthen playbook entry."
    """
    entry_id: str
    domain: str
    failure_type: str  # A-F per Appendix E
    intervention: str
    condition: str  # Jinja2 expression
    model_gen_assumption: str | None = None
    source_trace_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = ""
    deprecated_at: str | None = None
    deprecated_reason: str | None = None


@dataclass
class BudgetConfig:
    """Token budget configuration.

    Per §6: BudgetStore Protocol required.
    Per §11.1: parallel nodes inherit parent budget.
    Per §1.8: all limits toggleable via config.
    """
    session_token_limit: int = 500000
    project_token_limit: int = 5000000
    daily_token_limit: int = 10000000
    budget_warning_threshold: float = 0.80
    budget_hard_stop: bool = True


@dataclass
class RoutingWeight:
    """A single domain x model routing weight.

    Per §4.3: default routing uses highest-weight model for domain.
    Per §4.3: exploration_weight controls probability of non-optimal routing.
    Per §16.1: Sexton recommends exploration_weight adjustments per domain.
    """
    model_slot: str
    domain: str
    weight: float = 0.5
    exploration_weight: float = 0.10
    sample_count: int = 0
    updated_at: str = ""


@dataclass
class BeastCadenceConfig:
    """Configuration for the Beast maintenance actor.

    Per §3: Beast — cadence / corpus / entity maintenance.
    Per §5.10: state.db stores cadence_state.
    """
    corpus_reindex_interval_seconds: int = 3600
    entity_maintenance_interval_seconds: int = 1800
    health_check_interval_seconds: int = 60
    max_reindex_batch_size: int = 1000


@dataclass
class FailureClassification:
    """Sexton's classification output for a single trace event.

    Per §16.1: Sexton assigns appropriate Type A-F label.
    Per §5.9: writes back to trace_events.failure_type.
    Per §1.8: every classification carries model_gen_assumption.
    """
    trace_event_id: int
    failure_type: str  # A-F per Appendix E
    confidence: float = 0.0
    rationale: str = ""
    model_slot_used: str = "sexton"
    tokens_consumed: int = 0
    model_gen_assumption: str | None = None
    classified_at: str = ""  # REQUIRED — ISO 8601
```
<!-- ESTIMATED_TOKENS: ~300 -->

**`foundation/protocols.py` (append method stubs to existing Protocol classes + add new Protocol):**

```python
# --- Phase 5 amendments (append method stubs only — do NOT redeclare existing Protocol classes) ---

# ProjectStore — add list_projects method stub to existing class
# (existing methods from Phase 0/1 remain unchanged)
    async def list_projects(self, status: str | None = None) -> list[dict]:
        """List projects, optionally filtered by status.

        Used by Beast for corpus maintenance iteration.
        Returns list of dicts with project_id, name, status, etc.
        """
        ...


# --- Phase 5 new Protocol (not amendment — this is a new class) ---


class BudgetStore(Protocol):
    """Abstraction for token budget read/write operations.

    Per §6: BudgetStore Protocol required.
    Per §11.1: parallel nodes inherit parent budget.
    Per §7.2: orchestration may depend on Foundation protocols.
    """

    async def get_budget(self, scope: BudgetScope, scope_id: str) -> dict:
        """Get current budget status.

        Args:
            scope: Budget scope (session/project/daily).
            scope_id: Scope identifier (session_id, project_id, or date string).

        Returns:
            dict with consumed, remaining, limit, warning_threshold.
        """
        ...

    async def record_usage(
        self,
        scope: BudgetScope,
        scope_id: str,
        tokens_used: int,
        cost_usd: float,
        model_slot: str,
    ) -> None:
        """Record token consumption after a model call.

        Called by the workflow engine after each agent node completes.
        Writes to budget_ledger table in state.db.
        """
        ...

    async def check_limit(self, scope: BudgetScope, scope_id: str) -> bool:
        """Check whether budget has remaining capacity.

        Returns True if budget is not exhausted, False if at/past limit.
        Used by workflow engine before dispatching model calls.
        When budget_hard_stop is True, returning False blocks the call.
        """
        ...
```
<!-- ESTIMATED_TOKENS: ~250 -->

**`tests/test_phase5_schema_additions.py`:**

```python
"""Verify Phase 5 schema additions do not break Phase 0, 1, 2, 3, or 4."""
import pytest

from foundation.schemas import (
    AcePlaybookEntry,
    BeastCadenceConfig,
    BudgetConfig,
    BudgetScope,
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
    MigrationCheckpoint,
    MigrationStatus,
    ModelSlotConfig,
    PgvectorConfig,
    RetrievalResult,
    ReviewContext,
    ReviewVerdict,
    RoutingWeight,
    SessionContext,
    SextonConfig,
    TrajectorySignal,
    VectorBackendType,
)
from foundation.protocols import (
    ArtifactStore,
    BudgetStore,
    EmbeddingProvider,
    EcsStore,
    EntityStore,
    EventStore,
    ModelProvider,
    ProjectStore,
    TraceStore,
    VectorStore,
)


def test_sexton_config_dataclass():
    sc = SextonConfig(
        classification_batch_size=50,
        classification_interval_seconds=300,
        audit_on_slot_change=True,
        max_unclassified_before_alert=10,
    )
    assert sc.classification_batch_size == 50
    assert sc.audit_on_slot_change is True


def test_ace_playbook_entry_carries_model_gen_assumption():
    """Per §1.8: every ACE Playbook entry must carry model_gen_assumption."""
    entry = AcePlaybookEntry(
        entry_id="ace-001",
        domain="software_architecture",
        failure_type="A",
        intervention="Inject domain contract before synthesis",
        condition="domain == 'software_architecture' and output_schema_missing",
        model_gen_assumption="Models may adopt wrong domain role without explicit framing",
        confidence=0.85,
        created_at="2026-05-28T10:00:00Z",
    )
    assert entry.model_gen_assumption is not None
    assert entry.failure_type == "A"
    assert entry.deprecated_at is None


def test_ace_playbook_entry_deprecation():
    """Entries can be deprecated with reason."""
    entry = AcePlaybookEntry(
        entry_id="ace-002",
        domain="code_generation",
        failure_type="C",
        intervention="Validate JSON output structure",
        condition="output_format == 'json'",
        model_gen_assumption="Models may produce invalid JSON",
        deprecated_at="2026-06-01T10:00:00Z",
        deprecated_reason="Model slot upgrade improved JSON reliability",
    )
    assert entry.deprecated_at is not None
    assert "upgrade" in entry.deprecated_reason


def test_budget_config_dataclass():
    bc = BudgetConfig(
        session_token_limit=500000,
        project_token_limit=5000000,
        daily_token_limit=10000000,
        budget_warning_threshold=0.80,
        budget_hard_stop=True,
    )
    assert bc.session_token_limit == 500000
    assert bc.budget_hard_stop is True


def test_routing_weight_dataclass():
    rw = RoutingWeight(
        model_slot="synthesis",
        domain="software_architecture",
        weight=0.80,
        exploration_weight=0.10,
        sample_count=25,
    )
    assert rw.weight == 0.80
    assert rw.exploration_weight == 0.10


def test_beast_cadence_config_dataclass():
    bcc = BeastCadenceConfig(
        corpus_reindex_interval_seconds=3600,
        entity_maintenance_interval_seconds=1800,
        health_check_interval_seconds=60,
        max_reindex_batch_size=1000,
    )
    assert bcc.corpus_reindex_interval_seconds == 3600


def test_failure_classification_carries_model_gen_assumption():
    """Per §1.8: every Sexton classification must carry model_gen_assumption."""
    fc = FailureClassification(
        trace_event_id=42,
        failure_type="A",
        confidence=0.92,
        rationale="Output domain mismatch — synthesis adopted wrong role",
        model_slot_used="sexton",
        tokens_consumed=150,
        model_gen_assumption="Local Qwen3-Coder may misclassify domain-adjacent failures",
        classified_at="2026-05-28T10:00:00Z",
    )
    assert fc.model_gen_assumption is not None
    assert fc.failure_type == "A"


def test_budget_scope_type_alias():
    """BudgetScope must accept session, project, daily."""
    s: BudgetScope = "session"
    p: BudgetScope = "project"
    d: BudgetScope = "daily"
    assert s == "session"
    assert p == "project"
    assert d == "daily"


def test_phase0_phase1_phase2_phase3_phase4_enums_still_work():
    """Phase 0/1/2/3/4 enums must not be broken by Phase 5 additions."""
    assert EcsState.GENERATED is not None
    assert FailureType.C is not None


def test_phase1_phase2_phase3_phase4_dataclasses_still_work():
    """Phase 1/2/3/4 dataclasses must not be broken by Phase 5 additions."""
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
    pc = PgvectorConfig(connection_string="postgresql://localhost/aip")
    assert pc.hnsw_m == 16
    es = EvaluationScore(
        dimension="faithfulness",
        score=0.85,
        model_gen_assumption="Models may miss subtle factual contradictions",
    )
    assert es.model_gen_assumption is not None


def test_budgetstore_protocol_has_methods():
    """Phase 5: BudgetStore must have get_budget, record_usage, check_limit."""
    assert hasattr(BudgetStore, "get_budget"), "BudgetStore missing get_budget"
    assert hasattr(BudgetStore, "record_usage"), "BudgetStore missing record_usage"
    assert hasattr(BudgetStore, "check_limit"), "BudgetStore missing check_limit"


def test_projectstore_protocol_has_list_projects():
    """Phase 5: ProjectStore must have list_projects method."""
    assert hasattr(ProjectStore, "list_projects"), "ProjectStore missing list_projects"


def test_existing_protocol_methods_preserved():
    """Phase 1/2/3/4 methods must still exist after Phase 5 amendments."""
    assert hasattr(VectorStore, "upsert"), "VectorStore missing upsert (Phase 1)"
    assert hasattr(VectorStore, "health_check"), "VectorStore missing health_check (Phase 4)"
    assert hasattr(VectorStore, "count"), "VectorStore missing count (Phase 4)"
    assert hasattr(EventStore, "write_event"), "EventStore missing write_event (Phase 1)"
    assert hasattr(EventStore, "query"), "EventStore missing query (Phase 2)"
    assert hasattr(ArtifactStore, "list_versions"), "ArtifactStore missing list_versions (Phase 2)"
    assert hasattr(EcsStore, "current_state"), "EcsStore missing current_state (Phase 2)"
    assert hasattr(TraceStore, "query_events"), "TraceStore missing query_events (Phase 3)"
    assert hasattr(ModelProvider, "call"), "ModelProvider missing call (Phase 3)"
    assert hasattr(EmbeddingProvider, "embed"), "EmbeddingProvider missing embed (Phase 3)"
```
<!-- ESTIMATED_TOKENS: ~350 -->

---

## CHUNK-7.0b: Token Budget System

```
CHUNK-7.0b: Token Budget System
PHASE: 5
DEPENDS-ON: CHUNK-7.0a, CHUNK-5.7
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,500 tokens
FILES:
  orchestration/budget.py
  adapter/budget_store_sqlite.py
  tests/test_budget_system.py
INTERFACES:
  class BudgetManager:
      def __init__(self, config: BudgetConfig, budget_store: BudgetStore) -> None: ...
      async def check_before_call(self, scope: BudgetScope, scope_id: str, estimated_tokens: int) -> bool: ...
      async def record_consumption(self, scope: BudgetScope, scope_id: str, tokens_used: int, cost_usd: float, model_slot: str) -> None: ...
      async def get_status(self, scope: BudgetScope, scope_id: str) -> dict: ...
      async def is_hard_stop(self) -> bool: ...
  class SqliteBudgetStore(BudgetStore):
      def __init__(self, db_path: str) -> None: ...
      async def get_budget(self, scope: BudgetScope, scope_id: str) -> dict: ...
      async def record_usage(self, scope: BudgetScope, scope_id: str, tokens_used: int, cost_usd: float, model_slot: str) -> None: ...
      async def check_limit(self, scope: BudgetScope, scope_id: str) -> bool: ...
TESTS:
  tests/test_budget_system.py
GATE: uv run pytest tests/test_budget_system.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the token budget system that provides financial and resource governance for all model
calls. Phase 4 wired real model calls into all node stubs, but there is no mechanism to track or limit token
consumption. Phase 5 introduces budget tracking so that sessions, projects, and daily operations cannot
consume unlimited tokens without the DEFINER's awareness. The budget system implements §6's `BudgetStore`
Protocol and integrates with the workflow engine from CHUNK-4.5 to enforce limits before each model call.

**SqliteBudgetStore.** The `SqliteBudgetStore` class implements the `BudgetStore` Protocol from
`foundation/protocols.py`. It uses SQLite (state.db per §5.10)
for persistence. The `get_budget` method queries the `budget_ledger` table — which has columns `id INTEGER
PRIMARY KEY AUTOINCREMENT, scope TEXT, scope_id TEXT, tokens_used INTEGER, cost_usd REAL, model_slot TEXT,
created_at TEXT` — and returns a dict with `consumed` (total tokens used), `remaining` (limit minus consumed),
`limit` (from config), and `warning_threshold` (from config). The `record_usage` method inserts a row into the
ledger after each model call. The `check_limit` method queries the consumed total and returns `consumed <
limit` (or always True if `budget_hard_stop` is False).
```

**BudgetManager.** The `BudgetManager` class composes `BudgetConfig` and a `BudgetStore` instance. The
`check_before_call` method: (1) calls `budget_store.get_budget(scope, scope_id)`, (2) compares consumed +
estimated_tokens against the limit, (3) if `budget_hard_stop` is True and the call would exceed the limit,
returns False (blocking the call), (4) if the call would exceed `warning_threshold` fraction of the limit,
writes a warning event to the EventStore, (5) returns True otherwise. The `record_consumption` method calls
`budget_store.record_usage` after the model call completes. The `get_status` method returns a comprehensive
budget summary. The `is_hard_stop` method returns whether the budget is configured to block or just warn.

**Workflow engine integration.** The budget system is designed to be called by the workflow engine (CHUNK-4.5)
before dispatching agent nodes. The integration is not implemented in this chunk — it is a hook that CHUNK-7.4
(router) and the integration test (CHUNK-7.6) will exercise. The BudgetManager is an orchestration-layer
component that receives Protocol instances via dependency injection.

**Budget scoping.** The three budget scopes are: `session` (per SessionContext), `project` (per project_id),
and `daily` (per date). Each scope is tracked independently. A session budget might be 500K tokens, a project
budget 5M, and a daily budget 10M. A model call consumes tokens against all three scopes simultaneously — if
any scope is exhausted and `budget_hard_stop` is True, the call is blocked. This three-dimensional tracking
ensures that a single long session cannot consume an entire project's budget, and a single day cannot exceed
operational limits.

**CI mode.** In CI mode, budget tracking still works but the limits are set very high (effectively unlimited) to avoid interference with deterministic testing. The gate test verifies budget enforcement
with artificial limits.

The gate test verifies: (a) `SqliteBudgetStore` implements `BudgetStore` Protocol, (b) `get_budget` returns
correct consumed/remaining/limit, (c) `record_usage` accumulates correctly, (d) `check_limit` returns False
when limit exceeded and `budget_hard_stop=True`, (e) `check_limit` returns True when limit exceeded and
`budget_hard_stop=False`, (f) `BudgetManager.check_before_call` blocks when budget exhausted, (g) warning
event emitted when threshold crossed, (h) multi-scope tracking works independently, (i) adapter layer does not
import orchestration, (j) existing tests still pass.

### ANNEX

**`adapter/budget_store_sqlite.py`:**

```python
"""SQLite-backed BudgetStore implementation.

Per §6: BudgetStore Protocol required.
Per §5.10: state.db stores budgets.
Per §7.2: adapter may import foundation but not orchestration.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from foundation.protocols import BudgetStore
from foundation.schemas import BudgetScope


class SqliteBudgetStore(BudgetStore):
    """SQLite implementation of BudgetStore Protocol.

    Uses state.db for persistence. Budget ledger is append-only
    (consumption records are never deleted, only summed).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    model_slot TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_budget_scope
                ON budget_ledger(scope, scope_id)
            """)
            conn.commit()
        finally:
            conn.close()

    async def get_budget(self, scope: BudgetScope, scope_id: str) -> dict:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(tokens_used), 0), COALESCE(SUM(cost_usd), 0.0) "
                "FROM budget_ledger WHERE scope = ? AND scope_id = ?",
                (scope, scope_id),
            ).fetchone()
            consumed_tokens = row[0] if row else 0
            consumed_cost = row[1] if row else 0.0
            return {
                "consumed_tokens": consumed_tokens,
                "consumed_cost": consumed_cost,
            }
        finally:
            conn.close()

    async def record_usage(
        self,
        scope: BudgetScope,
        scope_id: str,
        tokens_used: int,
        cost_usd: float,
        model_slot: str,
    ) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO budget_ledger (scope, scope_id, tokens_used, cost_usd, model_slot) "
                "VALUES (?, ?, ?, ?, ?)",
                (scope, scope_id, tokens_used, cost_usd, model_slot),
            )
            conn.commit()
        finally:
            conn.close()

    async def check_limit(self, scope: BudgetScope, scope_id: str) -> bool:
        """Always returns True — limit checking is done by BudgetManager with config."""
        return True
```
<!-- ESTIMATED_TOKENS: ~300 -->

**`orchestration/budget.py`:**

```python
"""Token budget management for model call governance.

Per §6: BudgetStore Protocol required.
Per §11.1: parallel nodes inherit parent budget.
Per §1.8: all limits toggleable via config.
"""
from __future__ import annotations

from foundation.protocols import BudgetStore, EventStore
from foundation.schemas import BudgetConfig, BudgetScope


class BudgetManager:
    """Manages token budget enforcement across sessions, projects, and daily limits.

    Composes BudgetStore (persistence) and BudgetConfig (limits).
    Optionally writes warning events to EventStore when thresholds are crossed.
    """

    def __init__(
        self,
        config: BudgetConfig,
        budget_store: BudgetStore,
        event_store: EventStore | None = None,
    ) -> None:
        self._config = config
        self._store = budget_store
        self._event_store = event_store

    def _get_limit(self, scope: BudgetScope) -> int:
        limits = {
            "session": self._config.session_token_limit,
            "project": self._config.project_token_limit,
            "daily": self._config.daily_token_limit,
        }
        return limits[scope]

    async def check_before_call(
        self,
        scope: BudgetScope,
        scope_id: str,
        estimated_tokens: int,
    ) -> bool:
        """Check whether a model call can proceed within budget.

        Returns False if budget_hard_stop=True and the call would exceed the limit.
        Emits warning event if threshold is crossed.
        """
        budget = await self._store.get_budget(scope, scope_id)
        consumed = budget["consumed_tokens"]
        limit = self._get_limit(scope)
        remaining = limit - consumed

        # Check warning threshold
        if consumed / limit >= self._config.budget_warning_threshold:
            if self._event_store:
                await self._event_store.write_event({
                    "event_type": "budget_warning",
                    "scope": scope,
                    "scope_id": scope_id,
                    "consumed": consumed,
                    "limit": limit,
                    "fraction": consumed / limit,
                })

        # Check hard stop
        if self._config.budget_hard_stop and (consumed + estimated_tokens) > limit:
            return False

        return True

    async def record_consumption(
        self,
        scope: BudgetScope,
        scope_id: str,
        tokens_used: int,
        cost_usd: float,
        model_slot: str,
    ) -> None:
        """Record token consumption after a model call."""
        await self._store.record_usage(scope, scope_id, tokens_used, cost_usd, model_slot)

    async def get_status(self, scope: BudgetScope, scope_id: str) -> dict:
        """Get comprehensive budget status for a scope."""
        budget = await self._store.get_budget(scope, scope_id)
        consumed = budget["consumed_tokens"]
        limit = self._get_limit(scope)
        return {
            "scope": scope,
            "scope_id": scope_id,
            "consumed_tokens": consumed,
            "consumed_cost": budget["consumed_cost"],
            "limit": limit,
            "remaining": limit - consumed,
            "fraction_used": consumed / limit if limit > 0 else 1.0,
            "warning_threshold": self._config.budget_warning_threshold,
            "hard_stop": self._config.budget_hard_stop,
        }

    def is_hard_stop(self) -> bool:
        """Return whether budget is configured to block calls when limit is reached."""
        return self._config.budget_hard_stop
```
<!-- ESTIMATED_TOKENS: ~250 -->

---

## CHUNK-7.1: Sexton Failure Classification

```
CHUNK-7.1: Sexton Failure Classification
PHASE: 5
DEPENDS-ON: CHUNK-7.0a, CHUNK-5.0b, CHUNK-6.2
CODER-PROFILE: L2
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  orchestration/actors/sexton.py
  tests/test_sexton_classification.py
INTERFACES:
  class Sexton:
      def __init__(self, config: SextonConfig, model_resolver: ModelSlotResolver, trace_store: TraceStore,
event_store: EventStore) -> None: ...
      async def classify_failures(self) -> list[FailureClassification]: ...
      async def classify_trace_event(self, trace_event_id: int) -> FailureClassification: ...
      async def count_unclassified(self) -> int: ...
      async def run_classification_cycle(self) -> None: ...
TESTS:
  tests/test_sexton_classification.py
GATE: uv run pytest tests/test_sexton_classification.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the Sexton failure classification actor — the core of Phase 5. Sexton reads trace_events
where failures have occurred, classifies each failure according to the Appendix E taxonomy (Types A–F), writes
the classification back to trace_events, and produces structured `FailureClassification` outputs that feed the
ACE Playbook (CHUNK-7.2) and the stale rule audit (CHUNK-7.3).

**Sexton class.** The `Sexton` class is an orchestration-layer actor that receives `SextonConfig`,
`ModelSlotResolver`, `TraceStore`, and `EventStore` via dependency injection. It never imports adapter
implementations directly. The `sexton` model slot (Qwen3-Coder local per §4.1) is used for classification
calls. Per §16.1: "Sexton's model slot (Qwen3-Coder local) is appropriate for this task. Failure
classification is pattern-matching against structured trace data, not frontier reasoning."

**Classification flow.** The `classify_failures` method: (1) queries
`trace_store.query_events(session_id=None, node_type=None, limit=classification_batch_size)` to find trace
events where `outcome='failure' AND failure_type IS NULL` (unclassified failures), (2) for each unclassified
event, constructs a classification prompt containing the trace event data (node_type, model_slot, model_name,
token counts, cost, outcome, failure_detail, intervention_applied, intervention_type), (3) calls
`model_resolver.call("sexton", messages)` with the classification prompt, (4) parses the response to extract
the failure type (A–F), confidence, and rationale, (5) writes the classification back to
`trace_events.failure_type` via the TraceStore, (6) produces a `FailureClassification` dataclass for each
classified event. The classification prompt includes the full Appendix E taxonomy so that the model has the
definitions and examples.

**Single-event classification.** The `classify_trace_event(trace_event_id)` method classifies a single specific trace event. This is used when the DEFINER wants to trigger classification
for a specific failure rather than waiting for the batch cadence.

**Counting unclassified.** The `count_unclassified` method queries the number of trace events where `outcome='failure' AND failure_type IS NULL`. If this count exceeds `max_unclassified_before_alert`, Sexton writes an alert event to the EventStore, surfacing to the DEFINER.

**Classification cycle.** The `run_classification_cycle` method is the main entry point for cadence-based
execution. It: (1) counts unclassified failures, (2) if the count is zero, returns immediately, (3) if the
count exceeds the alert threshold, writes an alert, (4) calls `classify_failures` to process a batch, (5)
writes a Sexton completion event to the EventStore with the count classified, tokens consumed, and duration.

**Prompt design.** The classification prompt follows a structured format: system message explains Sexton's
role and the Appendix E taxonomy, user message provides the trace event data in JSON format, and the assistant
response is expected in a structured JSON format: `{"failure_type": "A"|"B"|"C"|"D"|"E"|"F", "confidence":
0.0-1.0, "rationale": "...", "model_gen_assumption": "..."}`. The `model_gen_assumption` field is mandatory
per §1.8 — even Sexton's own classifications must declare what model limitation they compensate for (e.g.,
"Local Qwen3-Coder may misclassify domain-adjacent Type A and Type B failures").

**CI mode.** When `model_resolver._ci_mode == True`, Sexton uses deterministic classification fixtures instead
of real model calls. The fixture classification is derived from the trace event's node_type and outcome fields
— e.g., node_type="L3a" with outcome="failure" maps to failure_type="C" (Output Malformation), node_type="L4"
with outcome="failure" maps to failure_type="D" (Session Drift). This preserves deterministic CI while testing
the full classification pipeline.

**Writing back to trace_events.** Sexton does NOT modify trace_events directly via SQL. It calls
`trace_store.query_events` to read and writes the classification back through the TraceStore's existing write
path. Per the architecture, all storage access goes through injected Protocols (§11.1 node contract invariant:
"All storage access goes through injected protocols").

The gate test verifies: (a) `Sexton` can be instantiated with config and Protocol instances, (b)
`classify_failures` processes unclassified trace events, (c) classification produces `FailureClassification`
with model_gen_assumption per §1.8, (d) `count_unclassified` returns correct count, (e)
`run_classification_cycle` processes a batch and writes events, (f) alert is triggered when unclassified count
exceeds threshold, (g) CI mode returns deterministic classifications, (h) Sexton does not import adapter
implementations, (i) existing tests still pass.

---

## CHUNK-7.2: ACE Playbook

```
CHUNK-7.2: ACE Playbook
PHASE: 5
DEPENDS-ON: CHUNK-7.1
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,500 tokens
FILES:
  orchestration/ace_playbook.py
  tests/test_ace_playbook.py
INTERFACES:
  class AcePlaybook:
      def __init__(self, db_path: str, config: AcePlaybookConfig) -> None: ...
      async def load_playbook(self, domain: str | None = None) -> list[AcePlaybookEntry]: ...
      async def add_entry(self, entry: AcePlaybookEntry) -> str: ...
      async def deprecate_entry(self, entry_id: str, reason: str) -> None: ...
      async def derive_from_classification(self, classification: FailureClassification, trace_event: dict) ->
AcePlaybookEntry | None: ...
      async def get_active_entries(self, domain: str, failure_type: str | None = None) ->
list[AcePlaybookEntry]: ...
TESTS:
  tests/test_ace_playbook.py
GATE: uv run pytest tests/test_ace_playbook.py -xvs
```

### Prose

This chunk implements the ACE Playbook — the SQLite-backed procedural intervention rule store that Sexton
curates and sessions load at startup. Per §8.1: "ACE Playbook / SQLite — procedural intervention rules, loaded
at session start, curated by Sexton." The ACE Playbook is the mechanism by which Sexton's failure
classifications become operational improvements: a classified failure pattern is derived into an intervention
rule, and that rule is loaded into the session context to prevent recurrence.

**AcePlaybook class.** The `AcePlaybook` class is an orchestration-layer component that manages the
`ace_playbook` SQLite table. The table schema is: `entry_id TEXT PRIMARY KEY, domain TEXT, failure_type TEXT,
intervention TEXT, condition TEXT, model_gen_assumption TEXT, source_trace_ids TEXT (JSON array), confidence
REAL, created_at TEXT, deprecated_at TEXT, deprecated_reason TEXT`. The class provides CRUD operations plus
the crucial `derive_from_classification` method.

**Loading the playbook.** The `load_playbook(domain)` method queries all non-deprecated entries, optionally
filtered by domain. This is called at session start per §8.1. The returned entries are injected into the
session context as procedural rules. When the workflow engine encounters a condition that matches an entry's
`condition` expression, it applies the intervention (e.g., injects a contract rule, adjusts the confidence
threshold, adds a verification step).

**Deriving entries from classifications.** The `derive_from_classification(classification, trace_event)`
method is the bridge between Sexton's classification output and the playbook. It: (1) takes a
`FailureClassification` and the originating trace event, (2) constructs an `AcePlaybookEntry` with the same
failure_type, domain (from the trace event's session metadata), an intervention derived from the Appendix E
intervention recommendations (e.g., Type A → "Strengthen ContractRule for this domain", Type B → "Add or
strengthen playbook entry", Type C → "Add pattern to L3a Stage 1 detection rules"), a condition expression
derived from the trace event's context, and the classification's model_gen_assumption, (3) sets the
source_trace_ids to include the trace event ID, (4) sets confidence to the classification's confidence, (5) if
`auto_derive=True` in config and confidence >= `min_confidence`, auto-promotes the entry (adds it to the
playbook), otherwise returns it for DEFINER review. This implements §16.1's "ACE playbook curation — derive
and update procedural intervention rules."

**Deprecation.** The `deprecate_entry(entry_id, reason)` method sets `deprecated_at` and `deprecated_reason`
on an entry. This is called by Sexton's stale rule audit (CHUNK-7.3) when a model slot upgrade makes an
assumption stale, or by the DEFINER when they want to disable a rule. Per §1.8 and Appendix D ("Supersession ≠
deletion"), entries are deprecated, not deleted.

**Active entries.** The `get_active_entries(domain, failure_type)` method returns all non-deprecated entries
for a domain, optionally filtered by failure_type. This is the query used by `load_playbook` internally and by the workflow engine to check for applicable rules during execution.

**Integration with Sexton.** CHUNK-7.1 produces `FailureClassification` outputs. This chunk provides the
`derive_from_classification` method that converts those outputs into playbook entries. The integration test
(CHUNK-7.6) exercises the full cycle: failure → classification → derivation → session replay with playbook.

The gate test verifies: (a) `AcePlaybook` creates and queries the SQLite table, (b) `load_playbook` returns
non-deprecated entries, (c) `add_entry` inserts with model_gen_assumption per §1.8, (d) `deprecate_entry` sets
deprecated_at/reason, (e) `derive_from_classification` produces valid entries from FailureClassification, (f)
auto-derive works when confidence >= min_confidence, (g) deprecated entries are excluded from `load_playbook`,
(h) domain filtering works, (i) failure_type filtering works.

---

## CHUNK-7.3: Sexton Stale Rule Audit

```
CHUNK-7.3: Sexton Stale Rule Audit
PHASE: 5
DEPENDS-ON: CHUNK-7.1
CODER-PROFILE: L1
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  orchestration/actors/sexton_audit.py
  tests/test_sexton_audit.py
INTERFACES:
  class SextonAudit:
      def __init__(self, model_resolver: ModelSlotResolver, event_store: EventStore) -> None: ...
      async def audit_stale_assumptions(self, contract_rules: list[ContractRule], playbook_entries:
list[AcePlaybookEntry], current_model_slots: dict[str, ModelSlotConfig]) -> list[dict]: ...
      async def flag_deprecated_rules(self, audit_results: list[dict]) -> None: ...
TESTS:
  tests/test_sexton_audit.py
GATE: uv run pytest tests/test_sexton_audit.py -xvs
```

### Prose

This chunk implements Sexton's stale rule audit — the mechanism that enforces §1.8's requirement: "On every
model slot upgrade: audit the harness for stale assumptions before diagnosing broken behavior. A rule
compensating for a limitation the new model does not have is overhead, not safety." The audit reads all
ContractRules and ACE Playbook entries that carry `model_gen_assumption`, compares those assumptions against
the current model slot capabilities, and flags rules whose assumptions may be stale.

**SextonAudit class.** The `SextonAudit` class receives `ModelSlotResolver` and `EventStore` via injection. It
is triggered by config changes (when `SextonConfig.audit_on_slot_change == True`) or by a manual DEFINER
command. It does not run on a cadence — it runs reactively when model slots change, because that is the only
time assumptions can become stale.

**Audit flow.** The `audit_stale_assumptions` method: (1) takes the list of ContractRules with
`model_gen_assumption IS NOT NULL` and the list of active AcePlaybookEntries with `model_gen_assumption IS NOT
NULL`, (2) takes the current model slot configurations (from `ModelSlotResolver.list_slots()`), (3) constructs
a prompt for each rule/entry that asks the `sexton` model slot whether the assumption is still valid given the
current model capabilities, (4) the prompt includes the rule text, the assumption, and the current model slot
assignment (provider, model name, but NOT API keys), (5) the model responds with a structured assessment:
`{"still_valid": true|false, "confidence": 0.0-1.0, "reason": "..."}`, (6) collects all results into a list of
audit result dicts: `{"rule_id": "...", "type": "contract_rule"|"playbook_entry", "assumption": "...",
"still_valid": bool, "confidence": float, "reason": str, "model_slot": "..."}`, (7) returns the results.

**Flagging deprecated rules.** The `flag_deprecated_rules` method takes the audit results and: (1) for each
result where `still_valid == False` and `confidence >= 0.70`, writes a "stale_assumption_detected" event to
the EventStore (surfacing to the DEFINER), (2) for ContractRules, writes the event with `rule_id` and
suggestion to deprecate, (3) for ACE Playbook entries, calls `AcePlaybook.deprecate_entry(entry_id,
reason=f"Stale assumption: {reason}")`. The DEFINER retains final authority over ContractRule deprecation
(§1.7: "the DEFINER remains the authority over system-shaping corrections"), but ACE Playbook entries can be
auto-deprecated since they are procedural rules, not architectural constraints.

**CI mode.** In CI mode, the audit uses deterministic logic instead of model calls: any rule whose
`model_gen_assumption` contains keywords like "may not", "cannot", "tends to", "often" is flagged as
potentially stale if the model slot has changed since the rule was created. This simple heuristic tests the
pipeline without requiring real model calls.

The gate test verifies: (a) `SextonAudit` can be instantiated with Protocol instances, (b)
`audit_stale_assumptions` processes ContractRules and PlaybookEntries, (c) stale rules are identified and
flagged, (d) events are written to EventStore for DEFINER review, (e) playbook entries are deprecated when
assumptions are stale, (f) CI mode uses deterministic heuristics, (g) audit results carry model_gen_assumption
per §1.8.

---

## CHUNK-7.4: Adaptive Router

```
CHUNK-7.4: Adaptive Router
PHASE: 5
DEPENDS-ON: CHUNK-7.0b, CHUNK-5.0b
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,500 tokens
FILES:
  orchestration/router.py
  tests/test_adaptive_router.py
INTERFACES:
  class AdaptiveRouter:
      def __init__(self, model_resolver: ModelSlotResolver, budget_manager: BudgetManager, config: dict) ->
None: ...
      async def resolve_with_routing(self, slot_name: str, domain: str, messages: list[dict], **kwargs) ->
dict: ...
      async def update_weights(self) -> None: ...
      async def get_routing_weights(self, domain: str | None = None) -> list[RoutingWeight]: ...
      async def recommend_exploration_weight(self, domain: str) -> float: ...
TESTS:
  tests/test_adaptive_router.py
GATE: uv run pytest tests/test_adaptive_router.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the Adaptive Router — the component that optimizes model selection per domain based on
accumulated routing_outcomes data. Per §4.3: "Default routing: highest-weight model for domain (exploitation).
Exploration: with probability = exploration_weight, route to non-optimal model. Sexton role: recommend
exploration_weight adjustments per domain." The routing_outcomes table exists from Phase 0, but no code reads
it to adjust routing behavior. Phase 5 closes this gap.

**AdaptiveRouter class.** The `AdaptiveRouter` class wraps the existing `ModelSlotResolver` and adds a routing decision layer on top. It does not replace ModelSlotResolver — it composes it. The router receives `ModelSlotResolver`, `BudgetManager`, and config (from `[router]` section) via injection. The `resolve_with_routing` method is the primary entry point
for model calls that respect routing optimization.
```

**Routing flow.** The `resolve_with_routing(slot_name, domain, messages, **kwargs)` method: (1) checks budget
via `budget_manager.check_before_call` for all three scopes (session, project, daily), (2) if budget is
exhausted and hard_stop is True, returns a budget-exceeded error dict, (3) queries the current routing weights
for the domain and slot, (4) with probability = `exploration_weight`, routes to a non-optimal model slot
(exploration per §4.3), (5) with probability = 1 - `exploration_weight`, routes to the highest-weight model
slot (exploitation), (6) calls `model_resolver.call(resolved_slot, messages, **kwargs)`, (7) records the
outcome (success/failure, tokens, cost, latency) to the `routing_outcomes` table, (8) records token
consumption via `budget_manager.record_consumption`, (9) returns the result. If the primary slot is overridden
by routing, the override is transparent to the caller — the caller requested `slot_name` but the router
resolved to a different slot for optimization reasons.

**Weight updates.** The `update_weights` method: (1) queries the `routing_outcomes` table for recent outcomes
(last N days, controlled by `weight_decay`), (2) groups by domain and model, (3) computes success rate,
average latency, average cost per token, (4) applies exponential decay to older outcomes, (5) updates the
in-memory routing weight table and persists to `routing_weights` table in state.db, (6) produces
`RoutingWeight` dataclass instances for each domain×model combination.

**Exploration weight recommendations.** The `recommend_exploration_weight(domain)` method implements §4.3's
Sexton role: "increase when domain data is sparse, decrease when domain data is dense and stable." It: (1)
counts the total routing outcomes for the domain, (2) if the count is below `min_sample_count`, returns a
higher exploration weight (0.20–0.30) to encourage data gathering, (3) if the count is above 10x
`min_sample_count` and success rate is stable, returns a lower exploration weight (0.05) to exploit the
known-optimal model, (4) returns the default exploration weight (0.10) otherwise.

**Budget integration.** The router is the single point where budget checking and token recording happen for
all model calls. This centralizes budget enforcement — instead of every node checking budget independently,
the router checks on behalf of all callers. The workflow engine and individual nodes call
`router.resolve_with_routing` instead of `model_resolver.call` directly.

The gate test verifies: (a) `AdaptiveRouter` composes `ModelSlotResolver` without replacing it, (b)
`resolve_with_routing` checks budget before routing, (c) exploitation routes to highest-weight model, (d)
exploration routes to non-optimal model
with correct probability, (e) `update_weights` computes correct weights from routing_outcomes, (f)
`recommend_exploration_weight` increases
for sparse domains, (g) `recommend_exploration_weight` decreases
for dense domains, (h) budget-exceeded calls are blocked when hard_stop=True, (i) CI mode uses deterministic
routing, (j) router does not
import adapter implementations, (k) existing ModelSlotResolver tests still pass.
```

---

## CHUNK-7.5: Beast Actor

```
CHUNK-7.5: Beast Actor
PHASE: 5
DEPENDS-ON: CHUNK-7.0b, CHUNK-6.0b
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  orchestration/actors/beast.py
  tests/test_beast_actor.py
INTERFACES:
  class Beast:
      def __init__(self, config: BeastCadenceConfig, vector_store: VectorStore, embedding_provider:
EmbeddingProvider, project_store: ProjectStore) -> None: ...
      async def run_corpus_maintenance(self) -> dict: ...
      async def run_entity_maintenance(self) -> dict: ...
      async def run_health_check(self) -> dict: ...
TESTS:
  tests/test_beast_actor.py
GATE: uv run pytest tests/test_beast_actor.py -xvs
```

### Prose

This chunk implements the Beast actor — the cadence-based maintenance agent that keeps the corpus, entity
store, and subsystem health in good order. Per §3: "Beast — cadence / corpus / entity maintenance." Beast is
not an LLM consumer — it is a deterministic scheduler that performs maintenance tasks on a regular cadence. It
uses the `sexton` model slot only for optional classification tasks (e.g., detecting stale entity names), but
its primary operations are deterministic.

**Beast class.** The `Beast` class receives `BeastCadenceConfig`, `VectorStore`, `EmbeddingProvider`, and `ProjectStore` via injection. It runs three maintenance cadences: corpus re-indexing, entity maintenance, and health checking.

**Corpus maintenance.** The `run_corpus_maintenance` method: (1) iterates over all projects via
`project_store.list_projects()`, (2) for each project, queries `vector_store.count(domain=project_id)` to get
the total vector count, (3) identifies stale vectors — vectors whose `updated_at` is older than
`corpus_reindex_interval_seconds` — by querying the vector store (the health_check and count methods from
CHUNK-6.0a provide the data), (4) for stale vectors, re-embeds the source content via
`embedding_provider.embed()` and calls `vector_store.upsert()` with the updated vector, (5) processes at most
`max_reindex_batch_size` vectors per cadence run, (6) returns a dict with `projects_checked`,
`vectors_reindexed`, `vectors_skipped`, `errors`.

**Entity maintenance.** The `run_entity_maintenance` method: (1) queries the entity store for all entities,
(2) validates entity consistency — checks that referenced artifacts still exist, that entity names are not
stale (an entity name is stale if no artifact has referenced it in the last N days), (3) marks stale entities
for DEFINER review by writing an event to the EventStore, (4) returns a dict with `entities_checked`,
`stale_entities`, `consistency_errors`.

**Health checking.** The `run_health_check` method: (1) calls `vector_store.health_check()` to verify the
vector backend is healthy, (2) checks database connectivity for all three databases (events.db, state.db,
trace.db), (3) checks Ollama connectivity via a simple embed call, (4) returns a comprehensive health dict
with `vector_backend: {connected, pool_size, latency_ms}`, `databases: {events: ok, state: ok, trace: ok}`,
`ollama: {connected, latency_ms}`, `overall: ok|degraded|down`.

**Cadence scheduling.** Beast itself does not implement a timer or scheduler. It exposes the three `run_*`
methods, and the calling code (CLI daemon, systemd timer, or simple asyncio loop) invokes them at the
configured intervals. This keeps Beast testable — the gate test calls the methods directly without waiting for
timers.

**CI mode.** In CI mode, Beast uses mock Protocol instances. The corpus maintenance test uses a FakeVectorStore with known stale vectors. The health check test uses healthy mock backends.

The gate test verifies: (a) `Beast` can be instantiated
with Protocol instances, (b) `run_corpus_maintenance` re-indexes stale vectors, (c) batch size limit is respected, (d) `run_entity_maintenance` identifies stale entities, (e) `run_health_check` returns comprehensive status, (f) Beast does not
import adapter implementations, (g) Beast uses VectorStore.count from Phase 4, (h) existing tests still pass.
```

---

## CHUNK-7.6: Integration Test

```
CHUNK-7.6: Integration Test
PHASE: 5
DEPENDS-ON: CHUNK-7.3, CHUNK-7.4, CHUNK-7.5, CHUNK-6.5
CODER-PROFILE: L1
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  tests/test_phase5_integration.py
INTERFACES:
  # Test scenarios only — no new interfaces
TESTS:
  tests/test_phase5_integration.py
GATE: uv run pytest tests/test_phase5_integration.py -xvs
```

### Prose

This chunk implements the Phase 5 integration test that verifies the full self-improvement cycle. The test
exercises the complete loop: a failure occurs in a workflow → Sexton classifies it → an ACE Playbook entry is
derived → a new session loads the playbook → the failure does not recur → the Adaptive Router optimizes model
selection → Beast maintains the corpus. This is the first test that exercises all actor layers working
together.

**Test scenario 1: Failure classification and ACE derivation.** (1) Run a workflow that produces a known
failure (e.g., structural validation failure, Type C), (2) call `sexton.classify_failures()`, (3) verify the
classification matches Type C, (4) call `playbook.derive_from_classification()`, (5) verify an ACE Playbook
entry is created with the correct failure_type, intervention, and model_gen_assumption.

**Test scenario 2: Playbook-loaded session prevents recurrence.** (1) Load the ACE Playbook from scenario 1,
(2) run the same workflow with the same inputs that previously produced a Type C failure, (3) verify that the
playbook intervention is applied (e.g., the contract rule is injected before synthesis), (4) verify the
failure does not recur.

**Test scenario 3: Adaptive router optimization.** (1) Populate `routing_outcomes` with seeded data showing
that the synthesis slot has a 90% success rate for domain "software_architecture" and the evaluation slot has
a 60% success rate, (2) call `router.update_weights()`, (3) verify that the synthesis slot has a higher weight
for that domain, (4) call `router.recommend_exploration_weight("software_architecture")`, (5) verify the
recommendation is reasonable for the sample count.

**Test scenario 4: Beast corpus maintenance.** (1) Insert stale vectors into a test VectorStore, (2) call
`beast.run_corpus_maintenance()`, (3) verify that stale vectors were re-indexed, (4) call
`beast.run_health_check()`, (5) verify the health report shows all subsystems healthy.

**Test scenario 5: Budget enforcement.** (1) Set a very low session token limit, (2) run a workflow that
exceeds the limit, (3) verify that the budget blocks the call when `hard_stop=True`, (4) verify a warning
event was written when the threshold was crossed.

**Test scenario 6: Stale rule audit.** (1) Create ContractRules with model_gen_assumption fields, (2) change
the model slot configuration, (3) call `sexton_audit.audit_stale_assumptions()`, (4) verify that stale
assumptions are flagged, (5) verify playbook entries are deprecated.

All tests use CI mode (deterministic fixtures) and do not require network access. The integration test extends
the Phase 4 integration test (CHUNK-6.5) by adding actor-level verification on top of the production-grade
pipeline.

The gate test verifies: (a) all six scenarios pass, (b) the full classification → derivation → prevention
cycle works, (c) router optimization uses routing_outcomes data, (d) Beast maintenance operates on stale
vectors, (e) budget enforcement blocks excessive consumption, (f) stale rule audit flags deprecated
assumptions, (g) all Protocol interactions are via injection (no direct imports of adapter implementations),
(h) existing Phase 0–4 tests still pass.

---

## CHUNK-7.7: Network Isolation and Model-Name Gate

```
CHUNK-7.7: Network Isolation and Model-Name Gate
PHASE: 5
DEPENDS-ON: CHUNK-7.6
CODER-PROFILE: L1
CONTEXT-BUDGET: ~2,000 tokens
FILES:
  tests/test_phase5_network_isolation.py
INTERFACES:
  # Test-only — no new interfaces
TESTS:
  tests/test_phase5_network_isolation.py
GATE: uv run pytest tests/test_phase5_network_isolation.py -xvs
```

### Prose

This chunk extends the network isolation and model-name gate tests for Phase 5 code. It follows the same
pattern as CHUNK-1.7, CHUNK-4.8, CHUNK-5.9, and CHUNK-6.6 — verifying that no Phase 5 code introduces network
dependencies in CI or hardcodes model names.

**Network isolation.** The test verifies that: (1) `Sexton` with CI mode does not make network calls, (2)
`AdaptiveRouter` with CI mode does not make network calls, (3) `Beast` with mock backends does not make
network calls, (4) `BudgetManager` does not make network calls, (5) `AcePlaybook` SQLite operations are
local-only, (6) `SextonAudit` with CI mode does not make network calls.

**Model-name gate.** The test verifies that: (1) no file in `orchestration/actors/` contains hardcoded model
names (Claude, Qwen, DeepSeek, OpenAI, GPT, Sonnet, etc.), (2) no file in `orchestration/ace_playbook.py`
contains hardcoded model names, (3) no file in `orchestration/router.py` contains hardcoded model names, (4)
no file in `orchestration/budget.py` contains hardcoded model names, (5) the `sexton` model slot name is the
only reference to a specific slot in Sexton code, (6) all model references in router code go through
`ModelSlotResolver.resolve()`.

**Import boundary verification.** The test extends `tests/test_layering.py` with: (1)
`orchestration/actors/sexton.py` does not import adapter implementations, (2) `orchestration/actors/beast.py`
does not import adapter implementations, (3) `orchestration/router.py` does not import adapter
implementations, (4) `orchestration/ace_playbook.py` does not import adapter implementations, (5) all
orchestration components import only foundation Protocols and schemas.

The gate test verifies: (a) all network isolation checks pass, (b) no hardcoded model names in Phase 5 code,
(c) import boundaries are respected, (d) all Phase 0–4 gate tests still pass.

---

## Config Additions Summary

Phase 5 adds the following sections to `config/aip.config.toml`:

```toml
[sexton]
classification_batch_size = 50
classification_interval_seconds = 300
audit_on_slot_change = true
max_unclassified_before_alert = 10

[ace_playbook]
db_path = "db/ace_playbook.db"
auto_derive = true
min_confidence = 0.70

[router]
default_exploration_weight = 0.10
min_sample_count = 10
weight_decay = 0.95
domain_overrides = {}

[budget]
session_token_limit = 500000
project_token_limit = 5000000
daily_token_limit = 10000000
budget_warning_threshold = 0.80
budget_hard_stop = true

[beast]
corpus_reindex_interval_seconds = 3600
entity_maintenance_interval_seconds = 1800
health_check_interval_seconds = 60
max_reindex_batch_size = 1000
```

All parameters are toggleable per §1.8. The `[budget]` section controls token governance. The `[router]`
section controls routing behavior. The `[sexton]` section controls classification cadence. The `[beast]`
section controls maintenance cadence. The `[ace_playbook]` section controls auto-derivation thresholds.

---

## Acceptance Criteria Mapping

| Architecture Gate | BuildSpec Chunk | Verification |
|---|---|---|
| [31] Harness evolution principle applied | 7.0a, 7.2, 7.3 | Every AcePlaybookEntry and FailureClassification
carries model_gen_assumption |
| [32] Trace event schema complete | Phase 0 (already delivered) | trace_events table with all columns |
| [33] Sexton classifies failure types | 7.1, 7.2 | Sexton reads trace_events, classifies A–F, produces ACE
entries, no unclassified failures remain |
| [34] Slot assignment implemented | 7.4 | Router respects §4.1 slot assignment, Sexton uses "sexton" slot,
structural validation consumes zero tokens |
| [35] Workflow 0.1 executable | 7.6 | Integration test runs full cycle with actors |
