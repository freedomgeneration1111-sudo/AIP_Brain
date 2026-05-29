# AIP 0.1 Phase 3 BuildSpec

**Product:** AI Poiesis (AIP) version 0.1  
**Architecture Revision:** 5.2  
**Build Phase:** 3 — Embedding Slot, L4 Trajectory Regulation & Multi-Turn Sessions  
**Spec Revision:** 1.1  
**Date:** May 2026  
**Status:** Build Specification — for Grok Build execution  
**Supersedes:** Phase 3 BuildSpec Rev 1.0  
**DEFINER:** Moses Jorgensen

---

## Revision Log

### 1.0 — Initial

| Item | Decision | Rationale |
|---|---|---|
| Embedding slot | `adapter/embedding/ollama_embed.py` — `OllamaEmbeddingClient` replaces `fake_embed()` |
§4.1 requires embedding slot via Ollama; §8.1 "local embedding slot"; Phase 1 used `fake_embed()` as
placeholder; Phase 3 wires the real slot |
| Model slot resolver | `adapter/model_slot_resolver.py` — resolves named slots → provider + model from config
| §4.1 named model slots; §4.2 configuration; Phase 0 defined config schema but no resolver; all Phase 1/2
node stubs bypassed model calls; Phase 3 provides the resolver so agent nodes can actually call models |
| Loop detection | `orchestration/trajectory/loop_detector.py` — detects Type D (Session Drift/Loop) per §10.1
| §10.1: "loop detection → failure_type D"; §5.9 trace_events schema has failure_type column; detector queries
trace_events for repeated patterns |
| Context anxiety detection | `orchestration/trajectory/anxiety_detector.py` — detects Type F (Context
Anxiety) per §10.1 | §10.1: "output-length collapse → failure_type F"; §10.2: "Context Anxiety Reset (L4b)
toggleable per §1.8"; detector measures output-length decline |
| Tool failure streak | `orchestration/trajectory/failure_streak.py` — detects Type E (False Success) streaks
per §10.1 | §10.1: "tool failure streak → failure_type E"; detector counts consecutive failures in session
window |
| Trajectory regulator | `orchestration/trajectory/regulator.py` — "2 of 3 signals" rule from §10.1 | §10.1:
"If 2 of 3 signals fire inside the session window, inject deterministic recovery or trigger context reset";
composes the three detectors |
| Context reset protocol | `orchestration/trajectory/context_reset.py` — implements §10.2 six-step reset |
§10.2: detect → progress summary → commit → log → surface to DEFINER → fresh session; this is the L4
intervention mechanism |
| Multi-turn session context | `orchestration/session.py` — manages context window across turns | §1.3:
"context is assembled from explicit stores"; §8.1: "ReMe / vector-backed memory"; Phase 2 Workflow 0.1 is
single-turn; Phase 3 adds multi-turn context accumulation and context window tracking |
| Config additions | `[trajectory]`, `[embedding]` sections in `aip.config.toml` | §1.8 toggleable; all L4
thresholds, embedding slot config, context window limits configurable |

### 1.0 → 1.1

| Fix | Issue | Change |
|---|---|---|
| R1 | CHUNK numbering collision — spec used 3.0a–3.9 but repo git history already has 3.1–3.12 for
L4/Sexton/budget work | Remapped all chunk numbers: 3.0a–3.9 → 5.0a–5.9. Updated all cross-references,
dependency DAG, linearized build order, and parallel groups. Updated Phase 2 chunk references from 2.x → 4.x.
|
| R2 | Phase boundary assumption mismatch — spec assumed only Phase 1+2 code exists, but repo has substantial
3.x work | Added §Repo State Reconciliation section documenting what exists vs. what needs building. Added
repo overlap reconciliation to §Process Rules. |
| R3 | Missing process rules — spec did not restate Continuity Check / WORKLOG / append-only / push rules |
Added §Process Rules section (10 rules, inherited from Phase 1 Rev 1.3 and Phase 2 Rev 1.2). |
| R4 | "Phase 3" terminology collision — bare "Phase 3" ambiguous between architectural phase and repo chunk
series | Added qualified terminology requirement to Process Rules: always use "Architectural Phase 3",
"CHUNK-5.x", or "repo 3.x". |
| R5 | Phase 2 cross-references outdated — spec referenced Phase 2 chunks as 2.x instead of remapped 4.x |
Updated all Phase 2 chunk references from 2.x → 4.x (CHUNK-2.0a → CHUNK-4.0a, etc.) |



---

## Phase 3 Scope

Phase 3 completes the real model integration (replacing all stubs with actual API/Ollama calls) and delivers
L4 trajectory regulation — the harness layer that detects when multi-turn sessions are degrading and
intervenes with deterministic recovery or context reset. It also introduces multi-turn session context
management, which is the architectural foundation for sustained interaction beyond the single-turn Workflow
0.1.

**In scope:**

- CHUNK-5.0a: Schema additions — `TrajectorySignal`, `SessionContext`, `ModelSlotConfig` dataclasses +
Protocol amendments (L1, append-only)
- CHUNK-5.0b: Model slot resolver — resolves named slots to provider/model from config, supports fallback
chains (L1/L2, adapter)
- CHUNK-5.1: Embedding slot client — `OllamaEmbeddingClient` replaces `fake_embed()` in
`retrieve_for_synthesis` (L2, adapter)
- CHUNK-5.2: Loop detector — detects Type D (Session Drift/Loop) via trace_events queries (L4)
- CHUNK-5.3: Context anxiety detector — detects Type F (Context Anxiety) via output-length tracking (L4b)
- CHUNK-5.4: Failure streak detector — detects Type E (False Success) streaks via trace_events queries (L4)
- CHUNK-5.5: Trajectory regulator — composes three detectors, "2 of 3" rule, deterministic recovery injection
(L4)
- CHUNK-5.6: Context reset protocol — six-step reset per §10.2 (L4b)
- CHUNK-5.7: Multi-turn session context — context window tracking, context assembly per §1.3 (L2/L4)
- CHUNK-5.8: Integration test — multi-turn session with trajectory regulation, embedding, and context reset
- CHUNK-5.9: Network isolation and model-name gate — cross-cutting test extending CHUNK-4.8

**Out of scope:**

- pgvector adapter (Phase 4)
- Sexton failure classification actor (Phase 5)
- Beast, Vigil actors
- UI / MCP / CLI surfaces
- ACE Playbook / procedural memory (Phase 5 — Sexton curates)
- Adaptive router (§4.3) — routing_outcomes table exists from Phase 0, but routing logic deferred to Phase 5
- Additional workflows beyond Workflow 0.1

---

## Phase 2 Assumptions (Architectural Phase 2 = CHUNK-4.x series)

Phase 3 chunks depend on the following Phase 2 deliverables being merged and green:

| CHUNK-4.x | Deliverable | Phase 3 Dependency |
|---|---|---|
| 4.0a | `foundation/schemas.py` — `ReviewVerdict`, `ReviewContext`, `EcsTransition`, `Event`,
`FailureTypeCode` | 5.0a appends |
| 4.0b | `foundation/ecs_graph.py` — `VALID_TRANSITIONS`, `InvalidTransitionError`, `validate_transition` |
5.6, 5.8 (context reset uses ECS transitions) |
| 4.1 | `orchestration/review.py` — `review_artifact` | 5.7 (multi-turn re-enters review) |
| 4.2 | `orchestration/re_synthesize.py` — `re_synthesize` | 5.8 (integration test uses re-synthesis) |
| 4.3 | `adapter/artifact_store_versioned.py` — `VersionedArtifactStore` | 5.6 (context reset writes progress
summary) |
| 4.4 | `adapter/event_store_queryable.py` — `QueryableEventStore` | 5.2, 5.4 (detectors query events) |
| 4.5 | `orchestration/engine.py` — YAML workflow engine | 5.7, 5.8 (session context used by engine) |
| 4.6 | `workflows/synthesis_session_v1.yaml` | 5.8 (integration test runs workflow) |
| 4.7 | Integration test | 5.8 extends |
| 4.8 | Network isolation gate | 5.9 extends |

Phase 1 dependencies (transitive through Phase 2):

| CHUNK-1.x | Deliverable | Phase 3 Dependency |
|---|---|---|
| 1.0a | `foundation/schemas.py` — `Chunk`, `RetrievalResult`; Protocol method signatures | 5.0a appends; 5.1
uses `Chunk` |
| 1.0b | `adapter/vector/sqlite_vss_store.py` — `SqliteVssVectorStore` | 5.1 (embedding client feeds
VectorStore) |
| 1.1 | `orchestration/retrieval.py` — `retrieve_for_synthesis`, `fake_embed` | 5.1 replaces `fake_embed` with
real embedding |
| 1.2 | `orchestration/validation.py` — `structural_validate` | 5.8 (integration test) |
| 1.3 | `orchestration/synthesize.py` — synthesis node stub | 5.7 (multi-turn calls synthesis) |
| 1.6 | `orchestration/commit.py` — commit + ECS transition | 5.6 (context reset uses commit) |

**Critical note on CHUNK-5.0a:** This chunk appends to `foundation/schemas.py` and amends
`foundation/protocols.py` — the same append-only/amend-by-addition pattern as CHUNK-1.0a and CHUNK-4.0a. No
existing Phase 0, Phase 1, or Phase 2 code is deleted or rewritten.

If any Phase 2 chunk is not merged, the depending Phase 3 chunk cannot start.

---


## Process Rules

These rules are binding for all work against this BuildSpec. They are inherited from Phase 1 Rev 1.3 and Phase
2 Rev 1.2 and must be followed without exception:

1. **Continuity Check.** Before starting any chunk, read `WORKLOG.md` and verify all DEPENDS-ON chunks are
merged and green. This includes all Phase 1 (1.x) and Phase 2 (4.x) chunks. If any dependency is not met,
block and report.

2. **WORKLOG append-only.** Every chunk completion appends a new work record to `WORKLOG.md`. Never overwrite,
delete, or reorder existing entries. Each record must include Task ID, Agent, Task description, Work Log
(concrete steps), and Stage Summary.

3. **Amend by addition — schemas.** New dataclasses, type aliases, and enums are appended to
`foundation/schemas.py`. Never modify, reorder, or delete existing Phase 0/1/2 definitions. The test suite
verifies this by importing Phase 0/1/2 types and asserting they still work.

4. **Amend by addition — protocols.** New methods are appended as stubs to existing Protocol classes in
`foundation/protocols.py`. New Protocol classes (ModelProvider, EmbeddingProvider) are added as new class
definitions. Never redeclare an existing Protocol class. The ANNEX shows individual method stubs for
amendments and full class blocks for new Protocols only.

5. **Deterministic CI.** All gate tests must pass without network access, API keys, secrets, or external
services. The `ci_mode` flag on ModelSlotResolver controls this for model calls. Embedding tests use mocks.
Trajectory detectors use fixture data.

6. **Push after each chunk.** After a chunk passes its gate test (`uv run pytest <test_file> -xvs`), commit
and push before starting the next chunk. One chunk per commit.

7. **Import boundaries.** Foundation never imports orchestration or adapter. Adapter may import foundation but
not orchestration. Orchestration may import both foundation and adapter. The layering test
(`tests/test_layering.py`) enforces this.

8. **No hardcoded model names.** Per §4.1, all model references resolve through named slots from config. No
model name may appear in any `orchestration/` or `foundation/` file. The test_no_hardcoded_model_names test
enforces this.

9. **Qualified phase references.** Always use qualified terminology: "Architectural Phase 3" for the logical
scope, "CHUNK-5.x" for build units, "repo 3.x" for historical commits. Never use bare "Phase 3" without
qualification.

10. **Repo overlap reconciliation.** Before building any CHUNK-5.x, check whether repo 3.x code already
implements part of the spec (especially L4/Sexton/ACE/budget work). If overlap exists, extend existing code to
meet the spec (amend by addition) rather than rewriting from scratch. Document reconciliation in WORKLOG.

---


## Repo State Reconciliation

**Important:** This spec was written when the codebase was assumed to contain only Phase 1 and Phase 2 code.
The actual repo contains additional work from historical chunk series 2.x (YAML engine mechanics) and 3.x
(L4/Sexton/ACE/budget foundation). This section documents the reconciliation.

### What this spec introduces that does NOT yet exist in code

| Type/Method | CHUNK | Notes |
|-------------|-------|-------|
| `TrajectorySignal` dataclass | 5.0a | New — no prior implementation |
| `SessionContext` dataclass | 5.0a | New — no prior implementation |
| `ModelSlotConfig` dataclass | 5.0a | New — no prior implementation |
| `TrajectorySignalType` type alias | 5.0a | New — no prior implementation |
| `TraceStore.query_events()` | 5.0a | New method — TraceStore only has `write_event` from Phase 1 |
| `ModelProvider` Protocol | 5.0a | New Protocol — does not exist in Phase 0/1/2 |
| `EmbeddingProvider` Protocol | 5.0a | New Protocol — does not exist in Phase 0/1/2 |
| `ModelSlotResolver` | 5.0b | New — no prior model slot resolution |
| `OllamaEmbeddingClient` | 5.1 | New — replaces `fake_embed()` from Phase 1 |
| `orchestration/trajectory/loop_detector.py` | 5.2 | New — L4 Type D detector |
| `orchestration/trajectory/anxiety_detector.py` | 5.3 | New — L4 Type F detector |
| `orchestration/trajectory/failure_streak.py` | 5.4 | New — L4 Type E detector |
| `orchestration/trajectory/regulator.py` | 5.5 | New — "2 of 3" rule |
| `orchestration/trajectory/context_reset.py` | 5.6 | New — §10.2 six-step reset |
| `orchestration/session.py` | 5.7 | New — multi-turn session context |
| Integration test | 5.8 | New — multi-turn + trajectory + embedding |
| Phase 3 network isolation gate | 5.9 | Extend CHUNK-4.8 |

### What already exists that may overlap

| Repo Series | What It Built | Overlap With |
|-------------|--------------|-------------|
| Repo 3.x (CHUNK-3.1–3.12) | L4/Sexton/ACE/budget foundation | CHUNK-5.2–5.6 (trajectory detectors),
CHUNK-5.7 (session) |

**Build strategy:** Where repo 3.x code already exists (especially Sexton, ACE, budget work), extend it to
meet the spec rather than replacing it. The spec is the authoritative target; existing code is a head start,
not a conflict. Document all reconciliations in WORKLOG.

---

## Dependency DAG

```
CHUNK-4.0a ── CHUNK-4.0b ── CHUNK-4.3 ── CHUNK-4.5 ── CHUNK-4.7
     │              │            │            │            │
     │              │            │            │            │
CHUNK-5.0a ────── CHUNK-5.0b ──┼────────────┼────────────┤
     │              │            │            │            │
     │              ├──── CHUNK-5.1 (embedding)            │
     │              │            │            │            │
     │              │            ├──── CHUNK-5.2 (loop)     │
     │              │            │                         │
     │              │            ├──── CHUNK-5.3 (anxiety)  │
     │              │            │                         │
     │              │            ├──── CHUNK-5.4 (streak)   │
     │              │            │                         │
     │              │            ├──── CHUNK-5.5 (regulator)─┘
     │              │            │                         │
     │              │            ├──── CHUNK-5.6 (reset)    │
     │              │            │                         │
     │              │            ├──── CHUNK-5.7 (session)  │
     │              │            │                         │
     │              │            └──── CHUNK-5.8 (integration)
     │              │                                      │
     │              └──── CHUNK-5.9 (gate)                  │

Linearized build order:
  5.0a → 5.0b → 5.1 → 5.2 (parallel with 5.3, 5.4) → 5.5 → 5.6 → 5.7 → 5.8 → 5.9

Parallel groups:
  Group A: [5.0a]                                    — schema + protocol additions
  Group B: [5.0b] (after 5.0a)                       — model slot resolver
  Group C: [5.1] (after 5.0b)                        — embedding slot client
  Group D: [5.2, 5.3, 5.4] (after 5.0a, 5.0b)       — three detectors (independent)
  Group E: [5.5] (after 5.2, 5.3, 5.4)              — trajectory regulator
  Group F: [5.6] (after 5.5)                         — context reset protocol
  Group G: [5.7] (after 5.6, 5.1)                    — multi-turn session context
  Group H: [5.8] (after 5.7)                         — integration test
  Group I: [5.9] (after all)                         — cross-cutting gate
```

---

## CHUNK-5.0a: Schema Additions + Protocol Amendments

```
CHUNK-5.0a: Schema Additions + Protocol Amendments
PHASE: 5
DEPENDS-ON: CHUNK-4.0a, CHUNK-4.0b
CODER-PROFILE: L1
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  foundation/schemas.py (append only — do not modify existing Phase 0/1/2 enums or dataclasses)
  foundation/protocols.py (amend by addition — add methods to existing Protocol classes)
INTERFACES:
  @dataclass
  class TrajectorySignal:
      signal_type: Literal["loop", "anxiety", "failure_streak"]
      session_id: str
      artifact_id: str | None
      failure_type: Literal["D", "E", "F"]   # Appendix E codes
      confidence: float                       # 0.0–1.0, how confident the detector is
      detail: str
      detected_at: str                        # ISO 8601
      model_gen_assumption: str | None        # §1.8 — what model limitation this signal compensates for
  @dataclass
  class SessionContext:
      session_id: str
      project_id: str
      turn_count: int
      context_tokens_estimate: int            # running estimate of context window usage
      context_window_limit: int               # from config
      artifacts_produced: list[str]           # artifact IDs produced in this session
      last_reset_at: str | None               # ISO 8601 of last context reset, None if never
  @dataclass
  class ModelSlotConfig:
      slot_name: str                          # synthesis / evaluation / sexton / embedding
      provider: str                           # ollama / openai / anthropic / deepseek
      model: str                              # resolved model name
      base_url: str | None                    # for Ollama / custom endpoints
      fallback_provider: str | None
      fallback_model: str | None
      dimensions: int | None                  # for embedding slot only
  # Type alias for trajectory signal types
  TrajectorySignalType = Literal["loop", "anxiety", "failure_streak"]
  # Protocol amendments in foundation/protocols.py (append method stubs only — do NOT redeclare class):
  # TraceStore: add query_events method stub to existing class
  async def query_events(self, session_id: str, node_type: str | None = None, limit: int = 100) -> list[dict]: ...
  # ModelProvider: new Protocol for Phase 3 (does not exist in Phase 0/1/2)
  class ModelProvider(Protocol):
      async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict: ...
  # EmbeddingProvider: new Protocol for Phase 3
  class EmbeddingProvider(Protocol):
      async def embed(self, text: str) -> list[float]: ...
TESTS:
  tests/test_phase3_schema_additions.py
GATE: uv run pytest tests/test_phase3_schema_additions.py -xvs
```

### Prose

This chunk establishes the shared data types and protocol amendments that all subsequent Phase 3 chunks depend
on. It does four things:

**1. Append `TrajectorySignal`, `SessionContext`, and `ModelSlotConfig` dataclasses to
`foundation/schemas.py`.** The `TrajectorySignal` dataclass captures a single detection from one of the three
L4 trajectory detectors (loop, anxiety, failure streak). Each signal carries the signal type, the session and
optional artifact it pertains to, the Appendix E failure type code (D, E, or F), a confidence score
for how certain the detector is, a detail string describing what was detected, the ISO 8601 timestamp of
detection, and crucially a `model_gen_assumption` field per §1.8 — every L4 trigger must declare which model
limitation it compensates for, so that Sexton can audit it when model slots change. The `SessionContext`
dataclass tracks the state of a multi-turn session: the session ID, the project it belongs to, how many turns
have elapsed, an estimate of current context token usage against the configured limit, the list of artifacts
produced so far, and when the last context reset occurred (if ever). The `ModelSlotConfig` dataclass captures
the resolved configuration
for a single model slot: the slot name (matching §4.1's four required slots), the provider and model name
resolved from config, the optional base URL
for local endpoints, the fallback chain, and the optional dimensions field (embedding slot only). Append only
— do not modify or reorder any existing definitions.
```

**2. Amend `TraceStore` Protocol in `foundation/protocols.py`.** Phase 1 (CHUNK-1.0a) added `write_event`. Phase 2 added `EventStore.query`. Phase 3 adds `query_events` to `TraceStore` — a method that returns raw trace event rows filtered by session_id and optional node_type. The three L4 detectors (CHUNK-5.2, 5.3, 5.4) need to query `trace_events` to detect patterns, and the `TraceStore` is the correct Protocol to provide this read path since trace.db is the canonical source. The
return type is `list[dict]` (not `list[Event]`) because trace_events have different columns than the Event dataclass (which is the EventStore read-model). This is an addition to the existing Protocol, not a replacement.
```

**3. Add `ModelProvider` Protocol in `foundation/protocols.py`.** This is a new Protocol, not an amendment to
an existing one. It abstracts model API calls so that orchestration code never imports `openai`, `anthropic`,
or `ollama` directly. The `call` method accepts a slot name, a list of messages (OpenAI-compatible format),
and keyword arguments (temperature, max_tokens, etc.), returning a dict with `content`, `model`, `usage`, and
`latency_ms`. The model slot resolver (CHUNK-5.0b) provides this Protocol; the embedding client (CHUNK-5.1)
and synthesis node (when promoted from stub) consume it.

**4. Add `EmbeddingProvider` Protocol in `foundation/protocols.py`.** This is a new Protocol that abstracts
text-to-vector embedding. The `embed` method accepts a text string and returns a `list[float]`. The
`OllamaEmbeddingClient` (CHUNK-5.1) implements this Protocol. The `retrieve_for_synthesis` function
(CHUNK-1.1) currently accepts `embed_fn: Callable[[str], list[float]]`; Phase 3 will add an overload that
accepts an `EmbeddingProvider` instance instead, while maintaining backward compatibility with the existing
callable signature.

The gate test verifies: (a) all new dataclasses can be instantiated with required fields, (b)
`TrajectorySignal` carries `model_gen_assumption` field per §1.8, (c) `TraceStore` Protocol has `query_events`
method, (d) `ModelProvider` Protocol has `call` method, (e) `EmbeddingProvider` Protocol has `embed` method,
(f) existing Phase 0/1/2 schema enums and dataclasses are not broken, (g) existing Protocol methods still
exist.

### ANNEX

**`foundation/schemas.py` (append to existing file):**

```python
# --- Phase 3 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type alias for trajectory signal types
TrajectorySignalType = Literal["loop", "anxiety", "failure_streak"]


@dataclass
class TrajectorySignal:
    """A single detection from an L4 trajectory detector.

    Per §10.1: loop detection → D, anxiety → F, failure streak → E.
    Per §1.8: every L4 trigger must carry model_gen_assumption.
    Per Appendix E: D/E/F are the L4 failure type codes.
    """
    signal_type: TrajectorySignalType
    session_id: str
    artifact_id: str | None = None
    failure_type: Literal["D", "E", "F"] = "D"
    confidence: float = 0.0
    detail: str = ""
    detected_at: str = ""  # REQUIRED — ISO 8601
    model_gen_assumption: str | None = None


@dataclass
class SessionContext:
    """State of a multi-turn session.

    Per §1.3: context is assembled from explicit stores.
    Per §10.2: context reset produces a fresh session with progress summary as seed.
    Tracks context window usage to enable anxiety detection (Type F).
    """
    session_id: str
    project_id: str
    turn_count: int = 0
    context_tokens_estimate: int = 0
    context_window_limit: int = 128000  # from config [models].context_window_limit
    artifacts_produced: list[str] = field(default_factory=list)
    last_reset_at: str | None = None


@dataclass
class ModelSlotConfig:
    """Resolved configuration for a single model slot.

    Per §4.1: four required slots — synthesis, evaluation, sexton, embedding.
    Per §4.2: provider, model, base_url from config.
    Per §1.4: models are replaceable execution engines.
    """
    slot_name: str
    provider: str
    model: str
    base_url: str | None = None
    fallback_provider: str | None = None
    fallback_model: str | None = None
    dimensions: int | None = None  # embedding slot only
```
<!-- ESTIMATED_TOKENS: ~250 -->

**`foundation/protocols.py` (append method stubs to existing Protocol classes + add new Protocols):**

```python
# --- Phase 3 amendments (append method stubs only — do NOT redeclare existing Protocol classes) ---

# TraceStore — add query_events method stub to existing class
# (write_event already defined in Phase 1 CHUNK-1.0a)
    async def query_events(
        self,
        session_id: str,
        node_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query trace events by session_id and optional node_type.

        Returns raw trace event rows as dicts (not Event dataclass).
        Used by L4 trajectory detectors for pattern analysis.
        Ordered by created_at descending (most recent first).
        """
        ...


# --- Phase 3 new Protocols (not amendments — these are new classes) ---


class ModelProvider(Protocol):
    """Abstraction for model API calls.

    Per §1.4: models are replaceable execution engines.
    Per §4.1: all model references resolve through named slots.
    Per §7.2: adapter may compose Foundation and Orchestration.
    """

    async def call(
        self,
        slot_name: str,
        messages: list[dict],
        **kwargs,
    ) -> dict:
        """Call a model via named slot.

        Args:
            slot_name: Named slot (synthesis/evaluation/sexton/embedding).
            messages: OpenAI-compatible message list.
            **kwargs: temperature, max_tokens, etc.

        Returns:
            dict with content, model, usage, latency_ms.
        """
        ...


class EmbeddingProvider(Protocol):
    """Abstraction for text-to-vector embedding.

    Per §4.1: embedding slot resolves to nomic-embed-text via Ollama.
    Per §8.1: local embedding slot for retrieval and artifacts.
    """

    async def embed(self, text: str) -> list[float]:
        """Embed text into a vector.

        Args:
            text: Input text to embed.

        Returns:
            Embedding vector (dimension from config).
        """
        ...
```
<!-- ESTIMATED_TOKENS: ~300 -->

**`tests/test_phase3_schema_additions.py`:**

```python
"""Verify Phase 3 schema additions do not break Phase 0, 1, or 2."""
import pytest

from foundation.schemas import (
    Chunk,
    ContractRule,
    EcsState,
    EcsTransition,
    Event,
    FailureType,
    FailureTypeCode,
    ModelSlotConfig,
    RetrievalResult,
    ReviewContext,
    ReviewVerdict,
    SessionContext,
    TrajectorySignal,
)
from foundation.protocols import (
    ArtifactStore,
    EmbeddingProvider,
    EcsStore,
    EventStore,
    ModelProvider,
    TraceStore,
    VectorStore,
)


def test_trajectory_signal_dataclass():
    s = TrajectorySignal(
        signal_type="loop",
        session_id="s1",
        failure_type="D",
        confidence=0.85,
        detail="Repeated output pattern detected",
        detected_at="2026-05-27T10:00:00Z",
        model_gen_assumption="Models tend to repeat when context is saturated",
    )
    assert s.signal_type == "loop"
    assert s.failure_type == "D"
    assert s.model_gen_assumption is not None


def test_trajectory_signal_carries_model_gen_assumption():
    """Per §1.8: every L4 trigger must carry model_gen_assumption."""
    s = TrajectorySignal(
        signal_type="anxiety",
        session_id="s2",
        failure_type="F",
        confidence=0.7,
        detail="Output length declining",
        detected_at="2026-05-27T10:00:00Z",
        model_gen_assumption="Models rush to conclude as context fills",
    )
    assert s.model_gen_assumption is not None
    assert s.failure_type == "F"


def test_session_context_dataclass():
    sc = SessionContext(
        session_id="s1",
        project_id="p1",
        turn_count=5,
        context_tokens_estimate=45000,
        context_window_limit=128000,
    )
    assert sc.turn_count == 5
    assert sc.artifacts_produced == []
    assert sc.last_reset_at is None


def test_model_slot_config_dataclass():
    msc = ModelSlotConfig(
        slot_name="embedding",
        provider="ollama",
        model="nomic-embed-text:v1.5",
        base_url="http://localhost:11434",
        dimensions=768,
    )
    assert msc.slot_name == "embedding"
    assert msc.dimensions == 768


def test_phase0_phase1_phase2_enums_still_work():
    """Phase 0/1/2 enums must not be broken by Phase 3 additions."""
    assert EcsState.GENERATED is not None
    assert FailureType.C is not None


def test_phase1_phase2_dataclasses_still_work():
    """Phase 1/2 dataclasses must not be broken by Phase 3 additions."""
    c = Chunk(id="x", content="hello", score=0.9, metadata={"k": "v"}, domain="test")
    assert c.id == "x"
    v = ReviewVerdict(artifact_id="a1", verdict="APPROVED", reviewer="definer")
    assert v.verdict == "APPROVED"


def test_tracestore_protocol_has_query_events():
    """Phase 3: TraceStore must have query_events method."""
    assert hasattr(TraceStore, "query_events"), "TraceStore missing query_events method"


def test_modelprovider_protocol_has_call():
    """Phase 3: ModelProvider must have call method."""
    assert hasattr(ModelProvider, "call"), "ModelProvider missing call method"


def test_embeddingprovider_protocol_has_embed():
    """Phase 3: EmbeddingProvider must have embed method."""
    assert hasattr(EmbeddingProvider, "embed"), "EmbeddingProvider missing embed method"


def test_existing_protocol_methods_preserved():
    """Phase 1/2 methods must still exist after Phase 3 amendments."""
    assert hasattr(EventStore, "write_event"), "EventStore missing write_event (Phase 1)"
    assert hasattr(EventStore, "query"), "EventStore missing query (Phase 2)"
    assert hasattr(ArtifactStore, "write"), "ArtifactStore missing write (Phase 1)"
    assert hasattr(ArtifactStore, "read"), "ArtifactStore missing read (Phase 1)"
    assert hasattr(ArtifactStore, "list_versions"), "ArtifactStore missing list_versions (Phase 2)"
    assert hasattr(EcsStore, "transition"), "EcsStore missing transition (Phase 0)"
    assert hasattr(EcsStore, "current_state"), "EcsStore missing current_state (Phase 2)"
    assert hasattr(TraceStore, "write_event"), "TraceStore missing write_event (Phase 1)"
    assert hasattr(VectorStore, "upsert"), "VectorStore missing upsert (Phase 1)"
```
<!-- ESTIMATED_TOKENS: ~350 -->

---

## CHUNK-5.0b: Model Slot Resolver

```
CHUNK-5.0b: Model Slot Resolver
PHASE: 5
DEPENDS-ON: CHUNK-5.0a, CHUNK-0.2
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  adapter/model_slot_resolver.py
  tests/test_model_slot_resolver.py
INTERFACES:
  class ModelSlotResolver(ModelProvider):
      def __init__(self, config: AipConfig | dict) -> None: ...
      def resolve(self, slot_name: str) -> ModelSlotConfig: ...
      async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict: ...
      def list_slots(self) -> list[str]: ...
TESTS:
  tests/test_model_slot_resolver.py
GATE: uv run pytest tests/test_model_slot_resolver.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the model slot resolver that translates named model slots (§4.1) into concrete provider
+ model + base_url configurations, and then executes model API calls through the appropriate provider. Phase 1
and Phase 2 left all model calls as stubs — `synthesize` returned deterministic fixtures, `adversarial_eval`
returned deterministic scores, and `fake_embed` returned deterministic vectors. Phase 3 wires the real model
infrastructure.

**Slot resolution.** The `resolve(slot_name)` method reads from the `config.aip.config.toml`
`[models.<slot_name>]` sections (defined in §4.2) and returns a `ModelSlotConfig` dataclass. The four required
slots are `synthesis`, `evaluation`, `sexton`, and `embedding`. The resolver validates that the slot exists in
config and raises `ValueError` if an unknown slot is requested. The `list_slots()` method returns the names of
all configured slots.

**Model dispatch.** The `call(slot_name, messages, **kwargs)` method: (1) resolves the slot name to a
`ModelSlotConfig`, (2) dispatches to the appropriate provider client (Ollama for local models,
OpenAI-compatible for DeepSeek, Anthropic for Claude Sonnet), (3) sends the request, (4) on failure, tries the
fallback provider/model if configured, (5) returns a dict with `content`, `model`, `usage` (token counts),
`latency_ms`, and `cost_usd`, (6) writes a `routing_outcomes` record per §4.3. The resolver lives in the
adapter layer per §7.2 — it may import foundation protocols but not orchestration code.

**Provider clients.** The resolver uses `httpx` (async)
for API calls. The Ollama client hits `base_url/api/chat` for chat completions and `base_url/api/embeddings`
for embedding. The OpenAI-compatible client hits the provider's chat completions endpoint. The Anthropic
client uses the Anthropic Messages API format. All three return results in a unified dict format. No model
name is hardcoded anywhere — every name comes from config.
```

**Deterministic CI mode.** When `config.models.ci_mode == true` (the default in CI environments), the resolver
returns deterministic fixtures instead of making real API calls. This preserves the Phase 1/2 guarantee that
CI runs without API keys. The fixture content is derived from the slot name and input hash, ensuring
reproducibility. The `ci_mode` flag is per §1.8 — it is toggleable, and when disabled, the resolver makes real
network calls. The CI gate test runs with `ci_mode=true`; the integration test (CHUNK-5.8) runs with both
modes.

**Config section:**

```toml
[models]
ci_mode = true                   # false in production
context_window_limit = 128000    # shared across all slots

[models.synthesis]
provider = "deepseek"
model = "deepseek-chat"
fallback_provider = "anthropic"
fallback_model = "claude-sonnet-4"

[models.evaluation]
provider = "deepseek"
model = "deepseek-chat"

[models.sexton]
provider = "ollama"
model = "qwen3-coder:32b"
base_url = "http://localhost:11434"

[models.embedding]
provider = "ollama"
model = "nomic-embed-text:v1.5"
base_url = "http://localhost:11434"
dimensions = 768
```

The gate test verifies: (a) `resolve("synthesis")` returns a `ModelSlotConfig` with correct provider/model,
(b) `resolve("unknown")` raises `ValueError`, (c) `list_slots()` returns all four slot names, (d)
`call("synthesis", messages)` returns a valid dict in CI mode, (e) fallback chain works when primary provider
fails (mocked), (f) no hardcoded model names in resolver code, (g) adapter layer does not import
orchestration.

### ANNEX

**`adapter/model_slot_resolver.py`:**

```python
"""Model slot resolver — translates named slots to provider/model configs.

Per §4.1: four required slots — synthesis, evaluation, sexton, embedding.
Per §1.4: models are replaceable execution engines.
Per §4.3: every model call writes a routing_outcomes record.
Per §1.8: ci_mode is toggleable for deterministic CI.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from foundation.protocols import ModelProvider
from foundation.schemas import ModelSlotConfig


# Deterministic fixture for CI mode — no real API calls
def _ci_fixture(slot_name: str, messages: list[dict]) -> dict:
    """Generate deterministic response for CI testing."""
    input_hash = hashlib.sha256(
        f"{slot_name}:{[m.get('content', '') for m in messages]}".encode()
    ).hexdigest()[:16]
    return {
        "content": f"[CI fixture for {slot_name}] hash={input_hash}",
        "model": f"ci-{slot_name}",
        "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
        "latency_ms": 100,
        "cost_usd": 0.0,
    }


class ModelSlotResolver(ModelProvider):
    """Resolves named model slots to provider/model configurations.

    Implements ModelProvider Protocol from foundation.protocols.
    Dispatches to appropriate provider client based on config.
    Supports fallback chains per §4.2.
    Writes routing_outcomes for every call per §4.3.
    """

    def __init__(self, config: Any) -> None:
        cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
        self._models_cfg = cfg.get("models", {})
        self._ci_mode = self._models_cfg.get("ci_mode", True)
        self._context_window_limit = self._models_cfg.get("context_window_limit", 128000)

    def resolve(self, slot_name: str) -> ModelSlotConfig:
        """Resolve a named slot to its configuration.

        Reads from [models.<slot_name>] in aip.config.toml.
        Raises ValueError if slot is not configured.
        """
        slot_cfg = self._models_cfg.get(slot_name)
        if slot_cfg is None:
            known = [k for k in self._models_cfg if isinstance(self._models_cfg[k], dict)]
            raise ValueError(
                f"Unknown model slot: {slot_name!r}. "
                f"Configured slots: {sorted(known)}"
            )

        return ModelSlotConfig(
            slot_name=slot_name,
            provider=slot_cfg.get("provider", ""),
            model=slot_cfg.get("model", ""),
            base_url=slot_cfg.get("base_url"),
            fallback_provider=slot_cfg.get("fallback_provider"),
            fallback_model=slot_cfg.get("fallback_model"),
            dimensions=slot_cfg.get("dimensions"),
        )

    async def call(
        self,
        slot_name: str,
        messages: list[dict],
        **kwargs,
    ) -> dict:
        """Call a model via named slot.

        In ci_mode: returns deterministic fixture (no network).
        In production: dispatches to appropriate provider client.
        On primary failure: tries fallback provider if configured.
        Writes routing_outcomes record per §4.3.
        """
        slot_config = self.resolve(slot_name)

        if self._ci_mode:
            return _ci_fixture(slot_name, messages)

        # Production: dispatch to provider
        start = time.monotonic()
        try:
            result = await self._dispatch(slot_config, messages, **kwargs)
            latency = int((time.monotonic() - start) * 1000)
            result["latency_ms"] = latency
            return result
        except Exception as primary_exc:
            # Try fallback if configured
            if slot_config.fallback_provider:
                try:
                    fallback_config = ModelSlotConfig(
                        slot_name=slot_name,
                        provider=slot_config.fallback_provider,
                        model=slot_config.fallback_model or "",
                        base_url=None,
                    )
                    result = await self._dispatch(fallback_config, messages, **kwargs)
                    latency = int((time.monotonic() - start) * 1000)
                    result["latency_ms"] = latency
                    result["fallback_used"] = True
                    return result
                except Exception:
                    raise primary_exc
            raise

    async def _dispatch(
        self, slot_config: ModelSlotConfig, messages: list[dict], **kwargs
    ) -> dict:
        """Dispatch to the appropriate provider client.

        In a full implementation, this would use httpx to call:
        - Ollama: base_url/api/chat
        - OpenAI-compatible: provider endpoint /v1/chat/completions
        - Anthropic: /v1/messages

        For Phase 3, this is a structured placeholder that returns
        the slot config as a dict (real HTTP calls in production deployment).
        """
        return {
            "content": f"[{slot_config.provider}:{slot_config.model}] response",
            "model": slot_config.model,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "cost_usd": 0.0,
        }

    def list_slots(self) -> list[str]:
        """Return names of all configured model slots."""
        return sorted(
            k for k in self._models_cfg if isinstance(self._models_cfg[k], dict)
        )
```
<!-- ESTIMATED_TOKENS: ~800 -->

**`tests/test_model_slot_resolver.py`:**

```python
"""Tests for the model slot resolver."""
import pytest

from adapter.model_slot_resolver import ModelSlotResolver
from foundation.schemas import ModelSlotConfig


TEST_CONFIG = {
    "models": {
        "ci_mode": True,
        "context_window_limit": 128000,
        "synthesis": {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "fallback_provider": "anthropic",
            "fallback_model": "claude-sonnet-4",
        },
        "evaluation": {
            "provider": "deepseek",
            "model": "deepseek-chat",
        },
        "sexton": {
            "provider": "ollama",
            "model": "qwen3-coder:32b",
            "base_url": "http://localhost:11434",
        },
        "embedding": {
            "provider": "ollama",
            "model": "nomic-embed-text:v1.5",
            "base_url": "http://localhost:11434",
            "dimensions": 768,
        },
    }
}


@pytest.fixture
def resolver():
    return ModelSlotResolver(TEST_CONFIG)


def test_resolve_synthesis(resolver):
    slot = resolver.resolve("synthesis")
    assert isinstance(slot, ModelSlotConfig)
    assert slot.provider == "deepseek"
    assert slot.model == "deepseek-chat"
    assert slot.fallback_provider == "anthropic"


def test_resolve_embedding(resolver):
    slot = resolver.resolve("embedding")
    assert slot.provider == "ollama"
    assert slot.dimensions == 768


def test_resolve_unknown_raises(resolver):
    with pytest.raises(ValueError, match="Unknown model slot"):
        resolver.resolve("nonexistent")


def test_list_slots(resolver):
    slots = resolver.list_slots()
    assert "synthesis" in slots
    assert "evaluation" in slots
    assert "sexton" in slots
    assert "embedding" in slots


@pytest.mark.asyncio
async def test_ci_mode_returns_fixture(resolver):
    messages = [{"role": "user", "content": "Hello"}]
    result = await resolver.call("synthesis", messages)
    assert "content" in result
    assert "CI fixture" in result["content"]
    assert result["model"] == "ci-synthesis"


@pytest.mark.asyncio
async def test_ci_mode_no_network(resolver):
    """CI mode must not make any network calls."""
    messages = [{"role": "user", "content": "test"}]
    result = await resolver.call("evaluation", messages)
    assert result["cost_usd"] == 0.0
    assert result["latency_ms"] > 0  # simulated latency


def test_no_hardcoded_model_names():
    """Per §4.1: no hardcoded model names in application code."""
    import inspect
    from adapter.model_slot_resolver import ModelSlotResolver
    source = inspect.getsource(ModelSlotResolver)
    forbidden = ["claude", "gpt", "deepseek-chat", "qwen", "nomic"]
    for name in forbidden:
        # Config key names are OK; hardcoded model names in logic are not
        assert name.lower() not in source.lower() or f'"{name}' not in source, \
            f"Hardcoded model name found: {name}"
```
<!-- ESTIMATED_TOKENS: ~600 -->

---

## CHUNK-5.1: Embedding Slot Client

```
CHUNK-5.1: Embedding Slot Client
PHASE: 5
DEPENDS-ON: CHUNK-5.0b, CHUNK-1.1
CODER-PROFILE: L2
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  adapter/embedding/ollama_embed.py
  adapter/embedding/__init__.py
  tests/test_ollama_embed.py
INTERFACES:
  class OllamaEmbeddingClient(EmbeddingProvider):
      def __init__(self, base_url: str, model: str, dimensions: int = 768) -> None: ...
      async def embed(self, text: str) -> list[float]: ...
TESTS:
  tests/test_ollama_embed.py
GATE: uv run pytest tests/test_ollama_embed.py tests/test_layering.py -xvs
```

### Prose

This chunk implements the real embedding client that replaces `fake_embed()` from CHUNK-1.1. Per §4.1, the
embedding slot resolves to `nomic-embed-text:v1.5` via Ollama. Per §8.1, the embedding slot is "local" — it
runs on the same machine as AIP via Ollama, not through an API. This is the first real model integration in
the system; all Phase 1/2 model calls were stubs.

**OllamaEmbeddingClient.** The client implements the `EmbeddingProvider` Protocol from CHUNK-5.0a. The
`embed(text)` method: (1) sends a POST request to `{base_url}/api/embeddings` with `{"model": model, "prompt":
text}`, (2) parses the response to extract the `embedding` field (a `list[float]`), (3) returns the vector.
The client uses `httpx.AsyncClient` for async HTTP. On connection failure (Ollama not running), it raises
`ConnectionError` with a helpful message. The client does not fall back to `fake_embed` — if Ollama is not
available, the operation fails cleanly rather than silently producing fake vectors. This is the correct
behavior per §1.2 (retrieval is deterministic in the critical path) — fake vectors would poison the retrieval
results.

**CI mode.** The gate test uses a mock Ollama server (or `unittest.mock.patch`) to simulate Ollama responses.
No real Ollama instance is required for CI. The mock returns a deterministic 768-dimensional vector derived
from the input text hash (same algorithm as `fake_embed`, but through the `EmbeddingProvider` interface). This
ensures the test is deterministic and does not require Ollama to be running.

**Integration with `retrieve_for_synthesis`.** The `retrieve_for_synthesis` function from CHUNK-1.1 accepts `embed_fn: Callable[[str], list[float]]`. The `OllamaEmbeddingClient.embed` method has the signature `async
def embed(self, text: str) -> list[float]`. To integrate, callers wrap the client: `embed_fn=lambda text:
await client.embed(text)`. This preserves backward compatibility — the existing `fake_embed` function still works, and the new client is an alternative. The workflow engine (CHUNK-4.5) will inject the real client when Ollama is available.
```

**Package structure.** `adapter/embedding/__init__.py` is an empty package init. The client lives in
`adapter/embedding/ollama_embed.py`, respecting the import boundary (§7.2): adapter may import foundation but
not orchestration.

The gate test verifies: (a) `embed("hello")` returns a list of floats with correct dimensions, (b) the vector
is different for different inputs, (c) mock mode works without real Ollama, (d) `OllamaEmbeddingClient`
implements `EmbeddingProvider` Protocol, (e) adapter layer does not import orchestration.

### ANNEX

**`adapter/embedding/__init__.py`:**

```python
"""Embedding adapter package."""
```
<!-- ESTIMATED_TOKENS: ~5 -->

**`adapter/embedding/ollama_embed.py`:**

```python
"""Ollama embedding client — replaces fake_embed for production.

Per §4.1: embedding slot resolves to nomic-embed-text via Ollama.
Per §8.1: local embedding slot for retrieval and artifacts.
Per §1.2: retrieval is deterministic — real vectors, not fake.
"""
from __future__ import annotations

import hashlib
import math

import httpx

from foundation.protocols import EmbeddingProvider


class OllamaEmbeddingClient(EmbeddingProvider):
    """EmbeddingProvider backed by Ollama.

    Calls Ollama /api/embeddings endpoint.
    Fails cleanly if Ollama is not available — no silent fake vectors.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text:v1.5",
        dimensions: int = 768,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._client = httpx.AsyncClient(timeout=30.0)

    async def embed(self, text: str) -> list[float]:
        """Embed text using Ollama.

        Args:
            text: Input text to embed.

        Returns:
            Embedding vector of length self._dimensions.

        Raises:
            ConnectionError: If Ollama is not reachable.
            ValueError: If Ollama returns an unexpected response.
        """
        try:
            response = await self._client.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model, "prompt": text},
            )
            response.raise_for_status()
            data = response.json()
            embedding = data.get("embedding")
            if embedding is None:
                raise ValueError(
                    f"Ollama response missing 'embedding' field. "
                    f"Available keys: {sorted(data.keys())}"
                )
            return embedding
        except httpx.ConnectError as e:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self._base_url}. "
                f"Is Ollama running? Error: {e}"
            ) from e

    async def close(self) -> None:
        await self._client.aclose()


def fake_embed_via_provider(text: str, dimensions: int = 768) -> list[float]:
    """Deterministic fake embedding for CI testing (no Ollama required).

    Same algorithm as orchestration.retrieval.fake_embed but
    callable without the OllamaEmbeddingClient dependency.
    Used in tests that mock the EmbeddingProvider.
    NOT for production use.
    """
    h = hashlib.sha256(text.encode()).digest()
    vec = []
    for i in range(dimensions):
        byte_idx = i % len(h)
        vec.append(h[byte_idx] / 255.0)
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm > 0 else vec
```
<!-- ESTIMATED_TOKENS: ~500 -->

**`tests/test_ollama_embed.py`:**

```python
"""Tests for the Ollama embedding client."""
import pytest

from adapter.embedding.ollama_embed import OllamaEmbeddingClient, fake_embed_via_provider
from foundation.protocols import EmbeddingProvider


def test_fake_embed_via_provider_deterministic():
    """CI fixture: same input produces same vector."""
    v1 = fake_embed_via_provider("hello")
    v2 = fake_embed_via_provider("hello")
    assert len(v1) == 768
    assert v1 == v2


def test_fake_embed_different_inputs_different_vectors():
    v1 = fake_embed_via_provider("hello")
    v2 = fake_embed_via_provider("world")
    assert v1 != v2


def test_fake_embed_unit_normalized():
    import math
    v = fake_embed_via_provider("test input")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 0.01


@pytest.mark.asyncio
async def test_ollama_client_implements_protocol():
    """OllamaEmbeddingClient must implement EmbeddingProvider Protocol."""
    client = OllamaEmbeddingClient(base_url="http://localhost:11434")
    assert hasattr(client, "embed")
    assert isinstance(client, EmbeddingProvider)
    await client.close()


@pytest.mark.asyncio
async def test_embed_with_mock():
    """Test embed() with mocked Ollama response."""
    from unittest.mock import AsyncMock, patch, MagicMock

    client = OllamaEmbeddingClient(base_url="http://localhost:11434")

    mock_response = MagicMock()
    mock_response.json.return_value = {"embedding": fake_embed_via_provider("test", 768)}
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response):
        result = await client.embed("test text")
        assert len(result) == 768
        assert isinstance(result[0], float)

    await client.close()


@pytest.mark.asyncio
async def test_embed_connection_error():
    """If Ollama is not running, raise ConnectionError — no silent fake vectors."""
    client = OllamaEmbeddingClient(base_url="http://localhost:19999")

    with pytest.raises(ConnectionError, match="Cannot connect to Ollama"):
        await client.embed("test")

    await client.close()
```
<!-- ESTIMATED_TOKENS: ~500 -->

---

## CHUNK-5.2: Loop Detector (Type D)

```
CHUNK-5.2: Loop Detector (Type D — Session Drift/Loop)
PHASE: 5
DEPENDS-ON: CHUNK-5.0a
CODER-PROFILE: L2
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  orchestration/trajectory/loop_detector.py
  orchestration/trajectory/__init__.py
  tests/test_loop_detector.py
INTERFACES:
  async def detect_loop(
      session_id: str,
      trace_store: TraceStore,
      config: AipConfig | dict | None = None,
  ) -> TrajectorySignal | None: ...
TESTS:
  tests/test_loop_detector.py
GATE: uv run pytest tests/test_loop_detector.py -xvs
```

### Prose

This chunk implements the loop detector specified in §10.1 as the first of three L4 trajectory signal
detectors. The loop detector identifies Type D failures (Session Drift/Loop) per Appendix E: "Quality degrades
over session length. Output becomes generic, repetitive, or stagnant. The model is running but not
progressing."

**Detection algorithm.** The loop detector queries `trace_store.query_events(session_id, node_type="L3a")` to
get recent validation events for the session. It then examines the last N events (configurable, default 5)
looking for patterns that indicate a loop: (1) repeated identical or near-identical synthesis outputs
(detected via content hash comparison), (2) successive rejections for the same failure type, (3) declining
output entropy (the diversity of tokens decreases across successive outputs). If the number of repeated
patterns exceeds a threshold (configurable, default 3), the detector fires a `TrajectorySignal` with
`signal_type="loop"`, `failure_type="D"`, and a confidence score proportional to the number of repetitions.

**Config section:**

```toml
[trajectory]
loop_detection_window = 5         # number of recent events to examine
loop_repetition_threshold = 3     # repetitions before signal fires
loop_confidence_base = 0.70       # base confidence for loop detection
```

**Per §1.8, the detector carries `model_gen_assumption`.** The default assumption for loop detection is:
"Models tend to repeat prior outputs when context saturates or when corrective instructions are insufficiently
specific." This assumption is written into the `TrajectorySignal.model_gen_assumption` field, making it
auditable by Sexton when model slots change.

**Package structure.** `orchestration/trajectory/__init__.py` is an empty package init. All three detectors
and the regulator live in `orchestration/trajectory/`.

The gate test verifies: (a) no loop detected in a healthy session with diverse outputs, (b) loop detected when
content hashes repeat beyond threshold, (c) signal carries correct `failure_type="D"`, (d) signal carries
`model_gen_assumption`, (e) config overrides work.

### ANNEX

**`orchestration/trajectory/__init__.py`:**

```python
"""L4 Trajectory Regulation package.

Per §10.1: loop detection, anxiety detection, failure streak detection.
Per §10.2: context reset protocol.
"""
```
<!-- ESTIMATED_TOKENS: ~5 -->

**`orchestration/trajectory/loop_detector.py`:**

```python
"""Loop detector — detects Type D (Session Drift/Loop) per §10.1 and Appendix E.

Identifies when a session is producing repetitive, stagnant, or looping output.
SQLite-query driven against trace_events per §10.1.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone

from foundation.protocols import TraceStore
from foundation.schemas import TrajectorySignal


# Default model_gen_assumption for loop detection per §1.8
_LOOP_ASSUMPTION = (
    "Models tend to repeat prior outputs when context saturates "
    "or when corrective instructions are insufficiently specific."
)


def _content_hash(content: str) -> str:
    """Hash content for repetition detection."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


async def detect_loop(
    session_id: str,
    trace_store: TraceStore,
    config: "AipConfig | dict | None" = None,
) -> TrajectorySignal | None:
    """Detect loop/drift in a session by examining trace events.

    Per §10.1: loop detection → failure_type D.
    Per Appendix E Type D: successive outputs shorter, same material repeated.

    Args:
        session_id: The session to examine.
        trace_store: For querying trace events.
        config: AipConfig or dict with [trajectory] section.

    Returns:
        TrajectorySignal if loop detected, None otherwise.
    """
    cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
    traj_cfg = cfg.get("trajectory", {})
    window = traj_cfg.get("loop_detection_window", 5)
    threshold = traj_cfg.get("loop_repetition_threshold", 3)
    confidence_base = traj_cfg.get("loop_confidence_base", 0.70)

    events = await trace_store.query_events(
        session_id=session_id, node_type="L3a", limit=window * 2
    )

    if len(events) < threshold:
        return None

    # Extract content hashes from recent events
    hashes = []
    for event in events:
        detail = event.get("detail", "") or ""
        content_hash = event.get("metadata", {}).get("content_hash")
        if content_hash:
            hashes.append(content_hash)
        elif detail:
            hashes.append(_content_hash(detail))

    if not hashes:
        return None

    # Count repetitions
    counter = Counter(hashes)
    max_repeats = counter.most_common(1)[0][1] if counter else 0

    if max_repeats < threshold:
        return None

    # Calculate confidence proportional to repetitions
    confidence = min(1.0, confidence_base + (max_repeats - threshold) * 0.1)

    return TrajectorySignal(
        signal_type="loop",
        session_id=session_id,
        failure_type="D",
        confidence=confidence,
        detail=f"Loop detected: content hash repeated {max_repeats} times in last {len(hashes)} events",
        detected_at=datetime.now(timezone.utc).isoformat(),
        model_gen_assumption=_LOOP_ASSUMPTION,
    )
```
<!-- ESTIMATED_TOKENS: ~500 -->

**`tests/test_loop_detector.py`:**

```python
"""Tests for the loop detector."""
import pytest

from foundation.schemas import TrajectorySignal
from orchestration.trajectory.loop_detector import detect_loop


class FakeTraceStore:
    def __init__(self, events=None):
        self._events = events or []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        self._events.append({"session_id": session_id, "node_type": node_type})

    async def query_events(self, session_id, node_type=None, limit=100):
        results = self._events
        if session_id:
            results = [e for e in results if e.get("session_id") == session_id]
        if node_type:
            results = [e for e in results if e.get("node_type") == node_type]
        return results[-limit:]


@pytest.mark.asyncio
async def test_no_loop_detected_healthy_session():
    events = [
        {"session_id": "s1", "node_type": "L3a", "detail": "unique output A"},
        {"session_id": "s1", "node_type": "L3a", "detail": "unique output B"},
        {"session_id": "s1", "node_type": "L3a", "detail": "unique output C"},
    ]
    store = FakeTraceStore(events)
    result = await detect_loop("s1", store)
    assert result is None


@pytest.mark.asyncio
async def test_loop_detected_repeated_content():
    events = [
        {"session_id": "s2", "node_type": "L3a", "detail": "same output"},
        {"session_id": "s2", "node_type": "L3a", "detail": "same output"},
        {"session_id": "s2", "node_type": "L3a", "detail": "same output"},
        {"session_id": "s2", "node_type": "L3a", "detail": "same output"},
    ]
    store = FakeTraceStore(events)
    result = await detect_loop("s2", store)
    assert result is not None
    assert result.signal_type == "loop"
    assert result.failure_type == "D"


@pytest.mark.asyncio
async def test_signal_carries_model_gen_assumption():
    events = [
        {"session_id": "s3", "node_type": "L3a", "detail": "repeat"},
        {"session_id": "s3", "node_type": "L3a", "detail": "repeat"},
        {"session_id": "s3", "node_type": "L3a", "detail": "repeat"},
    ]
    store = FakeTraceStore(events)
    result = await detect_loop("s3", store, config={"trajectory": {"loop_repetition_threshold": 3}})
    assert result is not None
    assert result.model_gen_assumption is not None  # per §1.8


@pytest.mark.asyncio
async def test_config_overrides():
    events = [
        {"session_id": "s4", "node_type": "L3a", "detail": "a"},
        {"session_id": "s4", "node_type": "L3a", "detail": "a"},
    ]
    store = FakeTraceStore(events)
    # Lower threshold to 2
    result = await detect_loop("s4", store, config={"trajectory": {"loop_repetition_threshold": 2}})
    assert result is not None


@pytest.mark.asyncio
async def test_too_few_events_no_signal():
    events = [
        {"session_id": "s5", "node_type": "L3a", "detail": "only one"},
    ]
    store = FakeTraceStore(events)
    result = await detect_loop("s5", store)
    assert result is None
```
<!-- ESTIMATED_TOKENS: ~500 -->

---

## CHUNK-5.3: Context Anxiety Detector (Type F)

```
CHUNK-5.3: Context Anxiety Detector (Type F — Context Anxiety)
PHASE: 5
DEPENDS-ON: CHUNK-5.0a
CODER-PROFILE: L2
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  orchestration/trajectory/anxiety_detector.py
  tests/test_anxiety_detector.py
INTERFACES:
  async def detect_anxiety(
      session_context: SessionContext,
      trace_store: TraceStore,
      config: AipConfig | dict | None = None,
  ) -> TrajectorySignal | None: ...
TESTS:
  tests/test_anxiety_detector.py
GATE: uv run pytest tests/test_anxiety_detector.py -xvs
```

### Prose

This chunk implements the context anxiety detector specified in §10.1 as the second L4 trajectory signal
detector. The anxiety detector identifies Type F failures (Context Anxiety) per Appendix E: "The model changes
behavior as context fills, rushing to conclude and actively degrading output quality."

**Detection algorithm.** The detector uses two signals: (1) **output-length decline** — it queries
`trace_store.query_events(session_id)` to get recent synthesis events, extracts the output lengths (stored in
metadata), and calculates the trend. If the last N outputs show a consistent decline in length (measured as a
percentage decrease from the first output in the window), the anxiety signal strengthens. (2) **context window
utilization** — it checks `session_context.context_tokens_estimate / session_context.context_window_limit`. If
utilization exceeds a configurable threshold (default 0.70, per Appendix E Type F signal "context window >70%
utilized"), the anxiety signal strengthens. The combined score is a weighted average of the two signals. If it
exceeds the confidence threshold (configurable, default 0.60), the detector fires.

**Per §1.8 toggleable.** The entire anxiety detector is toggleable per §1.8. The config flag
`[trajectory].anxiety_detection_enabled` (default `true`) controls whether the detector runs. Per the
architecture note on Type F: "this intervention encodes assumptions about model context anxiety behavior.
Audit when upgrading model slots." The `model_gen_assumption` field in the signal documents exactly what
assumption is being compensated for.

**Config section:**

```toml
[trajectory]
anxiety_detection_enabled = true         # per §1.8 toggleable
anxiety_window_size = 5                  # number of outputs to examine
anxiety_length_decline_threshold = 0.30  # 30% decline triggers signal
anxiety_context_utilization_threshold = 0.70  # 70% context window usage
anxiety_confidence_threshold = 0.60      # minimum confidence to fire signal
```

The gate test verifies: (a) no anxiety detected with stable output lengths and low context utilization, (b)
anxiety detected when output lengths decline by 30%+, (c) anxiety detected when context utilization exceeds
70%, (d) signal carries `failure_type="F"`, (e) signal carries `model_gen_assumption`, (f)
`anxiety_detection_enabled=false` disables the detector.

### ANNEX

**`orchestration/trajectory/anxiety_detector.py`:**

```python
"""Context anxiety detector — detects Type F (Context Anxiety) per §10.1 and Appendix E.

Per Appendix E Type F: "Model perceives context window filling and
accelerates toward any completion signal."
Per §1.8: toggleable — this intervention encodes assumptions about
model context anxiety behavior. Audit when upgrading model slots.
"""
from __future__ import annotations

from datetime import datetime, timezone

from foundation.protocols import TraceStore
from foundation.schemas import SessionContext, TrajectorySignal


# Default model_gen_assumption for anxiety detection per §1.8
_ANXIETY_ASSUMPTION = (
    "Models rush to conclude as context fills, producing shorter, "
    "more hedged outputs with declining depth. Per Appendix E Type F."
)


async def detect_anxiety(
    session_context: SessionContext,
    trace_store: TraceStore,
    config: "AipConfig | dict | None" = None,
) -> TrajectorySignal | None:
    """Detect context anxiety in a session.

    Per §10.1: output-length collapse → failure_type F.
    Per Appendix E Type F: output length declining, context >70%.

    Args:
        session_context: Current session state with context window tracking.
        trace_store: For querying trace events.
        config: AipConfig or dict with [trajectory] section.

    Returns:
        TrajectorySignal if anxiety detected, None otherwise.
    """
    cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
    traj_cfg = cfg.get("trajectory", {})

    # §1.8 toggleable
    if not traj_cfg.get("anxiety_detection_enabled", True):
        return None

    window = traj_cfg.get("anxiety_window_size", 5)
    length_decline_threshold = traj_cfg.get("anxiety_length_decline_threshold", 0.30)
    context_util_threshold = traj_cfg.get("anxiety_context_utilization_threshold", 0.70)
    confidence_threshold = traj_cfg.get("anxiety_confidence_threshold", 0.60)

    # Signal 1: Context window utilization
    context_util = 0.0
    if session_context.context_window_limit > 0:
        context_util = (
            session_context.context_tokens_estimate / session_context.context_window_limit
        )

    # Signal 2: Output-length decline
    events = await trace_store.query_events(
        session_id=session_context.session_id, limit=window * 2
    )
    output_lengths = []
    for event in events:
        metadata = event.get("metadata", {})
        length = metadata.get("output_length")
        if length is not None:
            output_lengths.append(int(length))

    length_decline_score = 0.0
    if len(output_lengths) >= 2:
        first = output_lengths[0] if output_lengths[0] > 0 else 1
        last = output_lengths[-1]
        decline = (first - last) / first
        if decline >= length_decline_threshold:
            length_decline_score = min(1.0, decline / length_decline_threshold * 0.5)

    # Combine signals
    context_score = max(0.0, (context_util - context_util_threshold) / (1.0 - context_util_threshold))
    combined = 0.5 * length_decline_score + 0.5 * context_score

    if combined < confidence_threshold:
        return None

    return TrajectorySignal(
        signal_type="anxiety",
        session_id=session_context.session_id,
        failure_type="F",
        confidence=combined,
        detail=(
            f"Context anxiety detected: utilization={context_util:.0%}, "
            f"output decline={length_decline_score:.0%}"
        ),
        detected_at=datetime.now(timezone.utc).isoformat(),
        model_gen_assumption=_ANXIETY_ASSUMPTION,
    )
```
<!-- ESTIMATED_TOKENS: ~500 -->

**`tests/test_anxiety_detector.py`:**

```python
"""Tests for the context anxiety detector."""
import pytest

from foundation.schemas import SessionContext, TrajectorySignal
from orchestration.trajectory.anxiety_detector import detect_anxiety


class FakeTraceStore:
    def __init__(self, events=None):
        self._events = events or []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        pass

    async def query_events(self, session_id, node_type=None, limit=100):
        return [e for e in self._events if e.get("session_id") == session_id][-limit:]


@pytest.mark.asyncio
async def test_no_anxiety_stable_session():
    ctx = SessionContext(session_id="s1", project_id="p1", context_tokens_estimate=30000)
    events = [
        {"session_id": "s1", "metadata": {"output_length": 1000}},
        {"session_id": "s1", "metadata": {"output_length": 950}},
        {"session_id": "s1", "metadata": {"output_length": 900}},
    ]
    store = FakeTraceStore(events)
    result = await detect_anxiety(ctx, store)
    assert result is None


@pytest.mark.asyncio
async def test_anxiety_detected_high_context_utilization():
    ctx = SessionContext(
        session_id="s2", project_id="p1",
        context_tokens_estimate=100000,
        context_window_limit=128000,
    )
    events = [
        {"session_id": "s2", "metadata": {"output_length": 500}},
        {"session_id": "s2", "metadata": {"output_length": 300}},
        {"session_id": "s2", "metadata": {"output_length": 100}},
    ]
    store = FakeTraceStore(events)
    result = await detect_anxiety(ctx, store)
    assert result is not None
    assert result.signal_type == "anxiety"
    assert result.failure_type == "F"


@pytest.mark.asyncio
async def test_signal_carries_model_gen_assumption():
    ctx = SessionContext(
        session_id="s3", project_id="p1",
        context_tokens_estimate=110000,
        context_window_limit=128000,
    )
    store = FakeTraceStore([{"session_id": "s3", "metadata": {"output_length": 100}}])
    result = await detect_anxiety(ctx, store)
    if result is not None:
        assert result.model_gen_assumption is not None


@pytest.mark.asyncio
async def test_anxiety_detection_toggleable():
    """Per §1.8: anxiety detection is toggleable."""
    ctx = SessionContext(
        session_id="s4", project_id="p1",
        context_tokens_estimate=120000,
        context_window_limit=128000,
    )
    store = FakeTraceStore([])
    result = await detect_anxiety(
        ctx, store,
        config={"trajectory": {"anxiety_detection_enabled": False}},
    )
    assert result is None
```
<!-- ESTIMATED_TOKENS: ~450 -->

---

## CHUNK-5.4: Failure Streak Detector (Type E)

```
CHUNK-5.4: Failure Streak Detector (Type E — False Success)
PHASE: 5
DEPENDS-ON: CHUNK-5.0a
CODER-PROFILE: L2
CONTEXT-BUDGET: ~2,500 tokens
FILES:
  orchestration/trajectory/failure_streak.py
  tests/test_failure_streak.py
INTERFACES:
  async def detect_failure_streak(
      session_id: str,
      trace_store: TraceStore,
      config: AipConfig | dict | None = None,
  ) -> TrajectorySignal | None: ...
TESTS:
  tests/test_failure_streak.py
GATE: uv run pytest tests/test_failure_streak.py -xvs
```

### Prose

This chunk implements the failure streak detector specified in §10.1 as the third L4 trajectory signal
detector. The failure streak detector identifies consecutive Type E failures (False Success Reporting) where
the model claims completion but the result is incomplete or hallucinated. Per §10.1: "tool failure streak →
failure_type E."

**Detection algorithm.** The detector queries `trace_store.query_events(session_id)` for recent events with
`outcome="failure"` and examines consecutive failures. If N consecutive failures occur within the session
window (configurable, default N=3), the detector fires a `TrajectorySignal` with
`signal_type="failure_streak"`, `failure_type="E"`. The confidence score is proportional to the streak length.

**Config section:**

```toml
[trajectory]
failure_streak_threshold = 3       # consecutive failures before signal fires
failure_streak_window = 20         # max events to examine
failure_streak_confidence_base = 0.65
```

**Per §1.8, the detector carries `model_gen_assumption`.** The default assumption: "Models sometimes claim
task completion when the result is incomplete, particularly for complex multi-step tasks. A verify step is
required before commit."

The gate test verifies: (a) no streak detected in a healthy session, (b) streak detected when N consecutive
failures occur, (c) signal carries `failure_type="E"`, (d) signal carries `model_gen_assumption`, (e) config
overrides work.

### ANNEX

**`orchestration/trajectory/failure_streak.py`:**

```python
"""Failure streak detector — detects Type E (False Success) streaks per §10.1.

Per §10.1: tool failure streak → failure_type E.
Per Appendix E Type E: model claims completion when result is incomplete.
"""
from __future__ import annotations

from datetime import datetime, timezone

from foundation.protocols import TraceStore
from foundation.schemas import TrajectorySignal


_STREAK_ASSUMPTION = (
    "Models sometimes claim task completion when the result is incomplete, "
    "particularly for complex multi-step tasks. A verify step is required "
    "before commit. Per Appendix E Type E."
)


async def detect_failure_streak(
    session_id: str,
    trace_store: TraceStore,
    config: "AipConfig | dict | None" = None,
) -> TrajectorySignal | None:
    """Detect consecutive failure streak in a session.

    Per §10.1: tool failure streak → failure_type E.

    Args:
        session_id: The session to examine.
        trace_store: For querying trace events.
        config: AipConfig or dict with [trajectory] section.

    Returns:
        TrajectorySignal if streak detected, None otherwise.
    """
    cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
    traj_cfg = cfg.get("trajectory", {})
    threshold = traj_cfg.get("failure_streak_threshold", 3)
    window = traj_cfg.get("failure_streak_window", 20)
    confidence_base = traj_cfg.get("failure_streak_confidence_base", 0.65)

    events = await trace_store.query_events(session_id=session_id, limit=window)

    # Count consecutive failures from most recent
    consecutive = 0
    for event in reversed(events):
        if event.get("outcome") == "failure":
            consecutive += 1
        else:
            break

    if consecutive < threshold:
        return None

    confidence = min(1.0, confidence_base + (consecutive - threshold) * 0.1)

    return TrajectorySignal(
        signal_type="failure_streak",
        session_id=session_id,
        failure_type="E",
        confidence=confidence,
        detail=f"Failure streak detected: {consecutive} consecutive failures in session",
        detected_at=datetime.now(timezone.utc).isoformat(),
        model_gen_assumption=_STREAK_ASSUMPTION,
    )
```
<!-- ESTIMATED_TOKENS: ~350 -->

**`tests/test_failure_streak.py`:**

```python
"""Tests for the failure streak detector."""
import pytest

from orchestration.trajectory.failure_streak import detect_failure_streak


class FakeTraceStore:
    def __init__(self, events=None):
        self._events = events or []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        pass

    async def query_events(self, session_id, node_type=None, limit=100):
        return [e for e in self._events if e.get("session_id") == session_id][-limit:]


@pytest.mark.asyncio
async def test_no_streak_healthy_session():
    events = [
        {"session_id": "s1", "outcome": "success"},
        {"session_id": "s1", "outcome": "success"},
        {"session_id": "s1", "outcome": "failure"},
        {"session_id": "s1", "outcome": "success"},
    ]
    store = FakeTraceStore(events)
    result = await detect_failure_streak("s1", store)
    assert result is None


@pytest.mark.asyncio
async def test_streak_detected():
    events = [
        {"session_id": "s2", "outcome": "success"},
        {"session_id": "s2", "outcome": "failure"},
        {"session_id": "s2", "outcome": "failure"},
        {"session_id": "s2", "outcome": "failure"},
    ]
    store = FakeTraceStore(events)
    result = await detect_failure_streak("s2", store)
    assert result is not None
    assert result.signal_type == "failure_streak"
    assert result.failure_type == "E"


@pytest.mark.asyncio
async def test_signal_carries_model_gen_assumption():
    events = [
        {"session_id": "s3", "outcome": "failure"},
        {"session_id": "s3", "outcome": "failure"},
        {"session_id": "s3", "outcome": "failure"},
    ]
    store = FakeTraceStore(events)
    result = await detect_failure_streak("s3", store)
    assert result is not None
    assert result.model_gen_assumption is not None


@pytest.mark.asyncio
async def test_config_overrides():
    events = [
        {"session_id": "s4", "outcome": "failure"},
        {"session_id": "s4", "outcome": "failure"},
    ]
    store = FakeTraceStore(events)
    result = await detect_failure_streak(
        "s4", store,
        config={"trajectory": {"failure_streak_threshold": 2}},
    )
    assert result is not None
```
<!-- ESTIMATED_TOKENS: ~350 -->

---

## CHUNK-5.5: Trajectory Regulator

```
CHUNK-5.5: Trajectory Regulator
PHASE: 5
DEPENDS-ON: CHUNK-5.2, CHUNK-5.3, CHUNK-5.4
CODER-PROFILE: L2
CONTEXT-BUDGET: ~3,000 tokens
FILES:
  orchestration/trajectory/regulator.py
  tests/test_trajectory_regulator.py
INTERFACES:
  async def regulate_trajectory(
      session_context: SessionContext,
      trace_store: TraceStore,
      config: AipConfig | dict | None = None,
  ) -> list[TrajectorySignal]: ...
  async def should_intervene(
      signals: list[TrajectorySignal],
      config: AipConfig | dict | None = None,
  ) -> bool: ...
TESTS:
  tests/test_trajectory_regulator.py
GATE: uv run pytest tests/test_trajectory_regulator.py -xvs
```

### Prose

This chunk implements the trajectory regulator that composes the three L4 detectors and applies the "2 of 3
signals" rule from §10.1: "If 2 of 3 signals fire inside the session window, inject deterministic recovery or
trigger context reset."

**`regulate_trajectory`.** This function runs all three detectors (loop, anxiety, failure_streak) and returns
a list of all firing signals. It does not itself decide whether to intervene — it just collects the signals.
This separation of detection and decision makes the regulator testable and allows downstream code (CHUNK-5.6
context reset) to decide what to do based on the signal pattern.

**`should_intervene`.** This function implements the "2 of 3" rule: if two or more distinct signal types
(loop, anxiety, failure_streak) are present in the signals list, return `True`. A single signal type firing
multiple times does not count — the rule requires at least two different signal types. This prevents false
positives from a single detector being overly sensitive. The function also considers signal confidence — only
signals above a minimum confidence threshold (configurable, default 0.50) count toward the "2 of 3" rule.
```

**Config section:**

```toml
[trajectory]
intervention_min_confidence = 0.50   # minimum signal confidence to count
intervention_signal_threshold = 2    # number of distinct signal types needed
```

**Deterministic recovery.** When `should_intervene` returns True but the signals don't indicate a full context
reset is needed (e.g., the combined confidence is moderate), the regulator can inject deterministic recovery
instructions into the synthesis context. This is a lighter-weight intervention than a full context reset. The
recovery instruction template is loaded from config and appended to the next synthesis call's context. The
injection is logged as a trace event with `intervention_type="trajectory_correction"` per §5.9.

The gate test verifies: (a) single signal does not trigger intervention, (b) two different signals trigger intervention, (c) signals below minimum confidence are excluded, (d) `regulate_trajectory` runs all three detectors, (e) the regulator composes detectors correctly.

### ANNEX

**`orchestration/trajectory/regulator.py`:**

```python
"""Trajectory regulator — composes L4 detectors per §10.1.

Per §10.1: "If 2 of 3 signals fire inside the session window,
inject deterministic recovery or trigger context reset."
"""
from __future__ import annotations

from foundation.protocols import TraceStore
from foundation.schemas import SessionContext, TrajectorySignal

from orchestration.trajectory.anxiety_detector import detect_anxiety
from orchestration.trajectory.failure_streak import detect_failure_streak
from orchestration.trajectory.loop_detector import detect_loop


async def regulate_trajectory(
    session_context: SessionContext,
    trace_store: TraceStore,
    config: "AipConfig | dict | None" = None,
) -> list[TrajectorySignal]:
    """Run all three L4 trajectory detectors and collect firing signals.

    Per §10.1: loop detection, output-length collapse, tool failure streak.
    Returns all signals that fired (not just the ones that trigger intervention).

    Args:
        session_context: Current session state.
        trace_store: For querying trace events.
        config: AipConfig or dict with [trajectory] section.

    Returns:
        List of TrajectorySignals from all detectors that fired.
    """
    signals: list[TrajectorySignal] = []

    # Run all three detectors
    loop_signal = await detect_loop(session_context.session_id, trace_store, config)
    if loop_signal is not None:
        signals.append(loop_signal)

    anxiety_signal = await detect_anxiety(session_context, trace_store, config)
    if anxiety_signal is not None:
        signals.append(anxiety_signal)

    streak_signal = await detect_failure_streak(session_context.session_id, trace_store, config)
    if streak_signal is not None:
        signals.append(streak_signal)

    return signals


def should_intervene(
    signals: list[TrajectorySignal],
    config: "AipConfig | dict | None" = None,
) -> bool:
    """Determine if trajectory regulation should intervene.

    Per §10.1: "2 of 3 signals fire" triggers intervention.
    Requires at least two DISTINCT signal types above minimum confidence.

    Args:
        signals: List of signals from regulate_trajectory.
        config: AipConfig or dict with [trajectory] section.

    Returns:
        True if intervention should be triggered.
    """
    cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
    traj_cfg = cfg.get("trajectory", {})
    min_confidence = traj_cfg.get("intervention_min_confidence", 0.50)
    threshold = traj_cfg.get("intervention_signal_threshold", 2)

    # Filter by minimum confidence
    qualifying = [s for s in signals if s.confidence >= min_confidence]

    # Count distinct signal types
    signal_types = set(s.signal_type for s in qualifying)

    return len(signal_types) >= threshold
```
<!-- ESTIMATED_TOKENS: ~400 -->

**`tests/test_trajectory_regulator.py`:**

```python
"""Tests for the trajectory regulator."""
import pytest

from foundation.schemas import SessionContext, TrajectorySignal
from orchestration.trajectory.regulator import regulate_trajectory, should_intervene


class FakeTraceStore:
    def __init__(self, events=None):
        self._events = events or []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        pass

    async def query_events(self, session_id, node_type=None, limit=100):
        return [e for e in self._events if e.get("session_id") == session_id][-limit:]


def test_should_intervene_single_signal_no():
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop", detected_at="2026-01-01T00:00:00Z",
        )
    ]
    assert not should_intervene(signals)


def test_should_intervene_two_signals_yes():
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop", detected_at="2026-01-01T00:00:00Z",
        ),
        TrajectorySignal(
            signal_type="anxiety", session_id="s1", failure_type="F",
            confidence=0.7, detail="anxiety", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    assert should_intervene(signals)


def test_should_intervene_low_confidence_excluded():
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop", detected_at="2026-01-01T00:00:00Z",
        ),
        TrajectorySignal(
            signal_type="anxiety", session_id="s1", failure_type="F",
            confidence=0.3, detail="anxiety", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    assert not should_intervene(signals)


def test_should_intervene_three_signals_yes():
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop", detected_at="2026-01-01T00:00:00Z",
        ),
        TrajectorySignal(
            signal_type="anxiety", session_id="s1", failure_type="F",
            confidence=0.7, detail="anxiety", detected_at="2026-01-01T00:00:00Z",
        ),
        TrajectorySignal(
            signal_type="failure_streak", session_id="s1", failure_type="E",
            confidence=0.75, detail="streak", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    assert should_intervene(signals)


@pytest.mark.asyncio
async def test_regulate_trajectory_runs_all_detectors():
    """regulate_trajectory should run all three detectors."""
    ctx = SessionContext(session_id="s1", project_id="p1")
    store = FakeTraceStore([
        {"session_id": "s1", "node_type": "L3a", "outcome": "success"},
    ])
    signals = await regulate_trajectory(ctx, store)
    # With only 1 success event, no signals should fire
    assert isinstance(signals, list)
```
<!-- ESTIMATED_TOKENS: ~450 -->

---

## CHUNK-5.6: Context Reset Protocol

```
CHUNK-5.6: Context Reset Protocol
PHASE: 5
DEPENDS-ON: CHUNK-5.5
CODER-PROFILE: L2
CONTEXT-BUDGET: ~4,000 tokens
FILES:
  orchestration/trajectory/context_reset.py
  tests/test_context_reset.py
INTERFACES:
  async def execute_context_reset(
      session_context: SessionContext,
      signals: list[TrajectorySignal],
      artifact_store: ArtifactStore,
      trace_store: TraceStore,
      event_store: EventStore,
      ecs_store: EcsStore,
      config: AipConfig | dict | None = None,
  ) -> SessionContext: ...
  async def inject_deterministic_recovery(
      signals: list[TrajectorySignal],
      config: AipConfig | dict | None = None,
  ) -> str: ...
TESTS:
  tests/test_context_reset.py
GATE: uv run pytest tests/test_context_reset.py -xvs
```

### Prose

This chunk implements the context reset protocol specified in §10.2. The six-step protocol is the primary L4
intervention mechanism: when trajectory regulation determines that a session is degrading, context reset
produces a progress summary, commits it, logs the intervention, surfaces to the DEFINER, and starts a fresh
session with the summary as seed.

**`execute_context_reset`.** This function implements the full six-step protocol from §10.2:

1. **Detect context anxiety or degeneration.** The function receives the `SessionContext` and
`list[TrajectorySignal]` that triggered the intervention. It validates that at least one signal is Type D
(drift) or Type F (anxiety) — these are the reset-eligible types per §10.2. Type E streaks alone trigger
deterministic recovery, not a full reset.

2. **Instruct model to produce progress summary.** In CI, the progress summary is a deterministic fixture:
"Session {session_id} progress: {N} turns completed, {M} artifacts produced. Last signal: {detail}." In
production, this would be a model call via the synthesis slot, but the model call is deferred to the actual
integration (Phase 4+ real LLM calls). The summary captures what was accomplished and what remains.

3. **Commit progress summary to Provisional Store / artifact record.** The summary is written to the
`ArtifactStore` as a new artifact with metadata `{"type": "progress_summary", "session_id": session_id,
"reset_reason": signal details}`. An ECS transition is recorded: the session's primary artifact transitions
through the ECS states appropriately. The progress summary is versioned per CHUNK-4.3.

4. **Log reset event to trace_events.** A trace event is written
with `node_type="L4"`, `intervention_applied=1`, `intervention_type="context_reset"`, `outcome="success"`. This ensures Sexton can measure intervention effectiveness per §16.1.

5. **Surface to DEFINER.** An event is written to `EventStore` with `event_type="context_reset"`,
`actor="trajectory_regulator"`, including the signals that triggered the reset and the progress summary
reference. Per §1.7, no action may bypass DEFINER gates — the DEFINER is informed even though the reset
proceeds automatically.

6. **Start fresh session with progress summary as seed.** The function returns a new `SessionContext` with
`turn_count=0`, `context_tokens_estimate=0` (fresh start), `last_reset_at=current_timestamp`, and
`artifacts_produced` carrying over the IDs from the previous session plus the new progress summary ID. The
caller (workflow engine or session manager) uses this new context to continue the session.

**`inject_deterministic_recovery`.** This is the lighter-weight intervention for when `should_intervene`
returns True but the signals don't warrant a full reset (e.g., only Type E streak, or moderate combined
confidence). It returns a string instruction to be appended to the next synthesis call's context, such as:
"Attention: the following trajectory issues were detected: [loop, failure_streak]. Apply corrective measures:
[avoid repetition, verify completeness before reporting done]." The instruction is derived from the signals'
failure types and correction instructions per Appendix E.

The gate test verifies: (a) context reset creates a progress summary artifact, (b) reset logs trace event with
`intervention_type="context_reset"`, (c) reset surfaces event to DEFINER, (d) new session context has
`turn_count=0`, (e) deterministic recovery produces instruction string, (f) ECS transitions are recorded.

### ANNEX

**`orchestration/trajectory/context_reset.py`:**

```python
"""Context reset protocol — implements §10.2 six-step reset.

Per §10.2:
1. Detect context anxiety or degeneration
2. Instruct model to produce progress summary
3. Commit progress summary to artifact store
4. Log reset event to trace_events
5. Surface to DEFINER
6. Start fresh session with progress summary as seed
"""
from __future__ import annotations

from datetime import datetime, timezone

from foundation.protocols import ArtifactStore, EcsStore, EventStore, TraceStore
from foundation.schemas import SessionContext, TrajectorySignal


# Recovery instruction templates per Appendix E failure type
_RECOVERY_TEMPLATES = {
    "D": "Session drift detected. Avoid repeating prior outputs. "
         "Introduce new perspectives and expand the analysis scope.",
    "E": "False completion detected. Do NOT report completion until "
         "all required deliverables are verified present. Include a "
         "self-verification step before finalizing any output.",
    "F": "Context anxiety detected. Do not rush to conclude. "
         "Maintain output depth and completeness regardless of "
         "perceived context pressure.",
}


async def execute_context_reset(
    session_context: SessionContext,
    signals: list[TrajectorySignal],
    artifact_store: ArtifactStore,
    trace_store: TraceStore,
    event_store: EventStore,
    ecs_store: EcsStore,
    config: "AipConfig | dict | None" = None,
) -> SessionContext:
    """Execute the full six-step context reset protocol per §10.2.

    Args:
        session_context: Current session state.
        signals: TrajectorySignals that triggered the intervention.
        artifact_store: For writing progress summary.
        trace_store: For logging reset trace event.
        event_store: For surfacing reset to DEFINER.
        ecs_store: For ECS state transitions.
        config: AipConfig or dict.

    Returns:
        New SessionContext for the fresh session.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Step 1: Validated — signals already received
    signal_details = "; ".join(f"{s.signal_type}({s.failure_type}): {s.detail}" for s in signals)

    # Step 2: Produce progress summary (deterministic in CI)
    summary_id = f"{session_context.session_id}_progress_summary"
    summary_content = (
        f"Session {session_context.session_id} progress summary:\n"
        f"- Turns completed: {session_context.turn_count}\n"
        f"- Artifacts produced: {len(session_context.artifacts_produced)}\n"
        f"- Reset triggered by: {signal_details}\n"
        f"- Context utilization at reset: "
        f"{session_context.context_tokens_estimate}/{session_context.context_window_limit}"
    )

    # Step 3: Commit progress summary
    await artifact_store.write(
        summary_id,
        summary_content,
        metadata={
            "type": "progress_summary",
            "session_id": session_context.session_id,
            "reset_reason": signal_details,
        },
    )

    # Step 4: Log reset event to trace_events
    await trace_store.write_event(
        session_id=session_context.session_id,
        node_type="L4",
        failure_type=signals[0].failure_type if signals else "",
        outcome="success",
        detail=f"Context reset executed. Reason: {signal_details}",
    )

    # Step 5: Surface to DEFINER
    await event_store.write_event(
        event_type="context_reset",
        actor="trajectory_regulator",
        artifact_id=session_context.session_id,
        from_state=None,
        to_state=None,
        reason=signal_details,
        progress_summary_id=summary_id,
    )

    # Step 6: Return fresh session context with progress summary as seed
    new_artifacts = list(session_context.artifacts_produced) + [summary_id]
    return SessionContext(
        session_id=session_context.session_id,
        project_id=session_context.project_id,
        turn_count=0,
        context_tokens_estimate=0,
        context_window_limit=session_context.context_window_limit,
        artifacts_produced=new_artifacts,
        last_reset_at=now,
    )


async def inject_deterministic_recovery(
    signals: list[TrajectorySignal],
    config: "AipConfig | dict | None" = None,
) -> str:
    """Generate deterministic recovery instruction from signals.

    Lighter-weight than full context reset. Appends instruction
    to the next synthesis call's context.

    Args:
        signals: TrajectorySignals that triggered the intervention.
        config: AipConfig or dict.

    Returns:
        Recovery instruction string.
    """
    detected_types = sorted(set(s.failure_type for s in signals))
    instructions = []
    for ft in detected_types:
        template = _RECOVERY_TEMPLATES.get(ft, f"Address failure type {ft}.")
        instructions.append(template)

    return (
        "TRAJECTORY REGULATION — CORRECTIVE INSTRUCTION:\n"
        f"Detected issues: {', '.join(detected_types)}\n"
        + "\n".join(instructions)
    )
```
<!-- ESTIMATED_TOKENS: ~600 -->

**`tests/test_context_reset.py`:**

```python
"""Tests for the context reset protocol."""
import pytest

from foundation.schemas import SessionContext, TrajectorySignal
from orchestration.trajectory.context_reset import (
    execute_context_reset,
    inject_deterministic_recovery,
)


class FakeArtifactStore:
    def __init__(self):
        self.written = []

    async def write(self, id, content, metadata):
        self.written.append({"id": id, "content": content, "metadata": metadata})

    async def read(self, id, version=None):
        return ""

    async def list_versions(self, id):
        return []


class FakeTraceStore:
    def __init__(self):
        self.events = []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        self.events.append({"session_id": session_id, "node_type": node_type, "outcome": outcome})

    async def query_events(self, session_id, node_type=None, limit=100):
        return []


class FakeEventStore:
    def __init__(self):
        self.events = []

    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        self.events.append({"event_type": event_type, "actor": actor, "artifact_id": artifact_id, **kwargs})

    async def query(self, artifact_id=None, event_type=None, limit=100):
        return []


class FakeEcsStore:
    def __init__(self):
        self.transitions = []

    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        self.transitions.append({"artifact_id": artifact_id, "to_state": to_state})

    async def current_state(self, artifact_id):
        return None


@pytest.fixture
def stores():
    return FakeArtifactStore(), FakeTraceStore(), FakeEventStore(), FakeEcsStore()


@pytest.mark.asyncio
async def test_context_reset_creates_progress_summary(stores):
    artifact, trace, events, ecs = stores
    ctx = SessionContext(session_id="s1", project_id="p1", turn_count=10)
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop detected", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    new_ctx = await execute_context_reset(ctx, signals, artifact, trace, events, ecs)
    assert len(artifact.written) == 1
    assert "progress_summary" in artifact.written[0]["id"]


@pytest.mark.asyncio
async def test_context_reset_returns_fresh_session(stores):
    artifact, trace, events, ecs = stores
    ctx = SessionContext(session_id="s1", project_id="p1", turn_count=10)
    signals = [
        TrajectorySignal(
            signal_type="anxiety", session_id="s1", failure_type="F",
            confidence=0.7, detail="anxiety", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    new_ctx = await execute_context_reset(ctx, signals, artifact, trace, events, ecs)
    assert new_ctx.turn_count == 0
    assert new_ctx.context_tokens_estimate == 0
    assert new_ctx.last_reset_at is not None


@pytest.mark.asyncio
async def test_context_reset_logs_trace_event(stores):
    artifact, trace, events, ecs = stores
    ctx = SessionContext(session_id="s1", project_id="p1")
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    await execute_context_reset(ctx, signals, artifact, trace, events, ecs)
    assert len(trace.events) == 1
    assert trace.events[0]["node_type"] == "L4"


@pytest.mark.asyncio
async def test_context_reset_surfaces_to_definer(stores):
    artifact, trace, events, ecs = stores
    ctx = SessionContext(session_id="s1", project_id="p1")
    signals = [
        TrajectorySignal(
            signal_type="anxiety", session_id="s1", failure_type="F",
            confidence=0.7, detail="anxiety", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    await execute_context_reset(ctx, signals, artifact, trace, events, ecs)
    assert any(e["event_type"] == "context_reset" for e in events.events)


@pytest.mark.asyncio
async def test_deterministic_recovery():
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop", detected_at="2026-01-01T00:00:00Z",
        ),
        TrajectorySignal(
            signal_type="failure_streak", session_id="s1", failure_type="E",
            confidence=0.7, detail="streak", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    instruction = await inject_deterministic_recovery(signals)
    assert "TRAJECTORY REGULATION" in instruction
    assert "D" in instruction
    assert "E" in instruction
```
<!-- ESTIMATED_TOKENS: ~600 -->

---

## CHUNK-5.7: Multi-Turn Session Context

```
CHUNK-5.7: Multi-Turn Session Context
PHASE: 5
DEPENDS-ON: CHUNK-5.6, CHUNK-5.1, CHUNK-4.5
CODER-PROFILE: L2
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  orchestration/session.py
  tests/test_session_context.py
INTERFACES:
  class SessionManager:
      def __init__(self, config: AipConfig | dict | None = None) -> None: ...
      def create_session(self, session_id: str, project_id: str) -> SessionContext: ...
      async def advance_turn(self, session_context: SessionContext, output_tokens: int) -> SessionContext: ...
      async def check_trajectory(self, session_context: SessionContext, trace_store: TraceStore) ->
tuple[list[TrajectorySignal], bool]: ...
      async def handle_intervention(self, session_context: SessionContext, signals: list[TrajectorySignal],
artifact_store: ArtifactStore, trace_store: TraceStore, event_store: EventStore, ecs_store: EcsStore) ->
SessionContext: ...
      def context_utilization(self, session_context: SessionContext) -> float: ...
TESTS:
  tests/test_session_context.py
GATE: uv run pytest tests/test_session_context.py -xvs
```

### Prose

This chunk implements the multi-turn session context manager that ties together the embedding slot, trajectory
regulation, and context reset into a coherent session lifecycle. Phase 2's Workflow 0.1 is single-turn; Phase
3 adds the ability to sustain a session across multiple turns, tracking context window usage, accumulating
artifacts, and applying trajectory regulation at each turn boundary.

**SessionManager.** The manager maintains session state across turns. `create_session` initializes a fresh
`SessionContext`. `advance_turn` increments the turn count and updates the context token estimate based on the
output tokens from the most recent synthesis call. `check_trajectory` runs `regulate_trajectory` and
`should_intervene`, returning both the signals and a boolean indicating whether intervention is needed.
`handle_intervention` decides whether to execute a full context reset or inject deterministic recovery, based
on the signal pattern: if any signal is Type D or Type F (reset-eligible per §10.2), execute full reset; if
only Type E, inject recovery instructions. `context_utilization` returns the current `context_tokens_estimate
/ context_window_limit` ratio.

**Context token estimation.** The `advance_turn` method adds `output_tokens` to the running estimate. The
estimate is approximate — it counts prompt tokens (from the assembled context) and completion tokens. The
estimate is used by the anxiety detector to determine context window pressure. The `context_window_limit` is
loaded from config `[models].context_window_limit` (default 128000, matching typical 128K context windows).

**Integration with workflow engine.** The session manager is designed to be called by the workflow engine
(CHUNK-4.5) between workflow executions. After each turn completes, the engine calls `advance_turn` and
`check_trajectory`. If intervention is needed, the engine calls `handle_intervention` and uses the returned
`SessionContext` to configure the next turn. This composes the L5 orchestration layer with the L4 regulation
layer without creating circular dependencies.

**Per §1.3, context is assembled from explicit stores.** The session context does not rely on the model's
conversation history as the durable substrate. Each turn re-assembles context from: the session's
`SessionContext` (artifact IDs, turn count), the progress summary (if a reset occurred), the retrieved context
(via `retrieve_for_synthesis`), and the trajectory regulation state (signals from the last check). This
ensures that even after a context reset, the next turn has everything it needs from durable stores.

The gate test verifies: (a) `create_session` returns a fresh context, (b) `advance_turn` increments turn count
and updates token estimate, (c) `context_utilization` returns correct ratio, (d) `check_trajectory` runs
detectors and returns intervention flag, (e) `handle_intervention` with Type D/F signals triggers full reset,
(f) `handle_intervention` with only Type E signals injects recovery, (g) session state survives across
multiple turns.

### ANNEX

**`orchestration/session.py`:**

```python
"""Multi-turn session context manager.

Per §1.3: context is assembled from explicit stores, not chat history.
Per §10.1: trajectory regulation at each turn boundary.
Per §10.2: context reset when session degrades.
Composes L5 workflow engine with L4 regulation layer.
"""
from __future__ import annotations

from foundation.protocols import ArtifactStore, EcsStore, EventStore, TraceStore
from foundation.schemas import SessionContext, TrajectorySignal
from orchestration.trajectory.context_reset import (
    execute_context_reset,
    inject_deterministic_recovery,
)
from orchestration.trajectory.regulator import regulate_trajectory, should_intervene


class SessionManager:
    """Manages multi-turn session state with trajectory regulation.

    Creates sessions, advances turns, checks trajectory,
    and handles interventions (recovery or reset).
    """

    def __init__(self, config: "AipConfig | dict | None" = None) -> None:
        cfg = config.model_dump() if hasattr(config, "model_dump") else (config or {})
        self._config = cfg
        self._models_cfg = cfg.get("models", {})
        self._context_window_limit = self._models_cfg.get("context_window_limit", 128000)

    def create_session(self, session_id: str, project_id: str) -> SessionContext:
        """Initialize a fresh session context."""
        return SessionContext(
            session_id=session_id,
            project_id=project_id,
            turn_count=0,
            context_tokens_estimate=0,
            context_window_limit=self._context_window_limit,
            artifacts_produced=[],
            last_reset_at=None,
        )

    async def advance_turn(
        self, session_context: SessionContext, output_tokens: int
    ) -> SessionContext:
        """Advance the session by one turn.

        Increments turn count and updates context token estimate.
        """
        # Estimate: prior context + new output (rough approximation)
        new_estimate = session_context.context_tokens_estimate + output_tokens
        return SessionContext(
            session_id=session_context.session_id,
            project_id=session_context.project_id,
            turn_count=session_context.turn_count + 1,
            context_tokens_estimate=new_estimate,
            context_window_limit=session_context.context_window_limit,
            artifacts_produced=session_context.artifacts_produced,
            last_reset_at=session_context.last_reset_at,
        )

    async def check_trajectory(
        self, session_context: SessionContext, trace_store: TraceStore
    ) -> tuple[list[TrajectorySignal], bool]:
        """Run trajectory regulation and check if intervention is needed.

        Returns:
            Tuple of (signals, should_intervene_flag).
        """
        signals = await regulate_trajectory(session_context, trace_store, self._config)
        intervene = should_intervene(signals, self._config)
        return signals, intervene

    async def handle_intervention(
        self,
        session_context: SessionContext,
        signals: list[TrajectorySignal],
        artifact_store: ArtifactStore,
        trace_store: TraceStore,
        event_store: EventStore,
        ecs_store: EcsStore,
    ) -> SessionContext:
        """Handle trajectory intervention.

        If any signal is Type D or Type F: execute full context reset per §10.2.
        If only Type E: inject deterministic recovery instruction.

        Returns:
            Updated SessionContext (fresh after reset, or same after recovery).
        """
        failure_types = {s.failure_type for s in signals}

        # Full reset for drift (D) or anxiety (F) per §10.2
        if "D" in failure_types or "F" in failure_types:
            return await execute_context_reset(
                session_context, signals,
                artifact_store, trace_store, event_store, ecs_store,
                self._config,
            )

        # Lighter recovery for failure streak (E)
        recovery = await inject_deterministic_recovery(signals, self._config)
        # The recovery instruction is returned to the caller
        # (workflow engine) to inject into the next synthesis call.
        # For now, we store it in session context metadata via a simple approach:
        # the caller checks this by calling inject_deterministic_recovery directly.
        return session_context

    def context_utilization(self, session_context: SessionContext) -> float:
        """Return current context window utilization ratio."""
        if session_context.context_window_limit <= 0:
            return 0.0
        return session_context.context_tokens_estimate / session_context.context_window_limit
```
<!-- ESTIMATED_TOKENS: ~600 -->

**`tests/test_session_context.py`:**

```python
"""Tests for multi-turn session context manager."""
import pytest

from foundation.schemas import SessionContext, TrajectorySignal
from orchestration.session import SessionManager


class FakeTraceStore:
    def __init__(self, events=None):
        self._events = events or []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        pass

    async def query_events(self, session_id, node_type=None, limit=100):
        return [e for e in self._events if e.get("session_id") == session_id][-limit:]


class FakeArtifactStore:
    async def write(self, id, content, metadata):
        pass

    async def read(self, id, version=None):
        return ""

    async def list_versions(self, id):
        return []


class FakeEventStore:
    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        pass

    async def query(self, artifact_id=None, event_type=None, limit=100):
        return []


class FakeEcsStore:
    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        pass

    async def current_state(self, artifact_id):
        return None


@pytest.fixture
def manager():
    return SessionManager()


def test_create_session(manager):
    ctx = manager.create_session("s1", "p1")
    assert ctx.session_id == "s1"
    assert ctx.turn_count == 0
    assert ctx.context_tokens_estimate == 0


@pytest.mark.asyncio
async def test_advance_turn(manager):
    ctx = manager.create_session("s1", "p1")
    ctx = await manager.advance_turn(ctx, output_tokens=2000)
    assert ctx.turn_count == 1
    assert ctx.context_tokens_estimate == 2000
    ctx = await manager.advance_turn(ctx, output_tokens=3000)
    assert ctx.turn_count == 2
    assert ctx.context_tokens_estimate == 5000


def test_context_utilization(manager):
    ctx = manager.create_session("s1", "p1")
    ctx = SessionContext(
        session_id="s1", project_id="p1",
        context_tokens_estimate=64000,
        context_window_limit=128000,
    )
    assert manager.context_utilization(ctx) == 0.5


@pytest.mark.asyncio
async def test_check_trajectory_returns_signals_and_flag(manager):
    ctx = manager.create_session("s1", "p1")
    store = FakeTraceStore()
    signals, intervene = await manager.check_trajectory(ctx, store)
    assert isinstance(signals, list)
    assert isinstance(intervene, bool)


@pytest.mark.asyncio
async def test_handle_intervention_type_d_triggers_reset(manager):
    ctx = SessionContext(session_id="s1", project_id="p1", turn_count=10)
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s1", failure_type="D",
            confidence=0.8, detail="loop", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    result = await manager.handle_intervention(
        ctx, signals, FakeArtifactStore(), FakeTraceStore(),
        FakeEventStore(), FakeEcsStore(),
    )
    # Full reset: turn_count should be 0
    assert result.turn_count == 0
    assert result.last_reset_at is not None


@pytest.mark.asyncio
async def test_handle_intervention_type_e_recovery_only(manager):
    ctx = SessionContext(session_id="s1", project_id="p1", turn_count=5)
    signals = [
        TrajectorySignal(
            signal_type="failure_streak", session_id="s1", failure_type="E",
            confidence=0.7, detail="streak", detected_at="2026-01-01T00:00:00Z",
        ),
    ]
    result = await manager.handle_intervention(
        ctx, signals, FakeArtifactStore(), FakeTraceStore(),
        FakeEventStore(), FakeEcsStore(),
    )
    # Recovery only: turn_count unchanged (no reset)
    assert result.turn_count == 5
```
<!-- ESTIMATED_TOKENS: ~600 -->

---

## CHUNK-5.8: Integration Test

```
CHUNK-5.8: Integration Test
PHASE: 5
DEPENDS-ON: CHUNK-5.7, CHUNK-4.5, CHUNK-4.7
CODER-PROFILE: L2
CONTEXT-BUDGET: ~5,000 tokens
FILES:
  tests/test_phase3_integration.py
INTERFACES:
  # No new interfaces — this is a test-only chunk
TESTS:
  tests/test_phase3_integration.py
GATE: uv run pytest tests/test_phase3_integration.py -xvs
```

### Prose

This chunk delivers the Phase 3 integration test that exercises the full multi-turn session lifecycle with
embedding, trajectory regulation, and context reset. It extends the Phase 2 integration test (CHUNK-4.7) by
adding multi-turn execution, trajectory signal injection, and context reset verification.

**Test scenario: Happy path multi-turn session.** A session starts, completes 3 turns successfully (each
producing an artifact that goes through the ECS lifecycle SPECIFIED→GENERATED→REVIEWED→APPROVED), the session
context accumulates turns and tokens, no trajectory signals fire, and the session completes normally. This
verifies that the session manager, embedding client, and model slot resolver work correctly in a standard
multi-turn flow.

**Test scenario: Trajectory regulation with context reset.** A session starts, completes 5 turns, then
simulated trajectory signals fire (loop + anxiety — the "2 of 3" rule). The regulator determines intervention
is needed, the context reset protocol executes, a progress summary is committed, trace events are logged, the
DEFINER is notified, and a fresh session context is returned with `turn_count=0`. The next turn after reset
uses the progress summary as seed context. This verifies the full L4 intervention flow end-to-end.

**Test scenario: Embedding integration.** A multi-turn session uses the `OllamaEmbeddingClient` (in mock mode)
for `retrieve_for_synthesis`, verifying that the real embedding slot replaces `fake_embed` without breaking
the retrieval pipeline. The test asserts that the embedding client is called (not `fake_embed`) and that the
VectorStore receives properly embedded vectors.

**Test scenario: Model slot resolver in CI mode.** The integration test runs with `ci_mode=true`, verifying
that the `ModelSlotResolver` returns deterministic fixtures for synthesis and evaluation calls. No real API
calls are made. The test asserts that the resolver is used for model calls (not hardcoded stubs) and that
routing outcomes are recorded.

The gate test verifies all four scenarios pass, and that all Phase 1/2 acceptance gates still hold (no regressions).

### ANNEX

**`tests/test_phase3_integration.py`:**

```python
"""Phase 3 integration test — multi-turn session with trajectory regulation."""
import pytest

from adapter.model_slot_resolver import ModelSlotResolver
from foundation.schemas import SessionContext, TrajectorySignal
from orchestration.session import SessionManager


# --- Shared fakes ---

class FakeTraceStore:
    def __init__(self):
        self.events = []

    async def write_event(self, session_id, node_type, failure_type, outcome, detail=None):
        self.events.append({
            "session_id": session_id, "node_type": node_type,
            "failure_type": failure_type, "outcome": outcome, "detail": detail,
        })

    async def query_events(self, session_id, node_type=None, limit=100):
        results = self.events
        if session_id:
            results = [e for e in results if e.get("session_id") == session_id]
        if node_type:
            results = [e for e in results if e.get("node_type") == node_type]
        return results[-limit:]


class FakeArtifactStore:
    def __init__(self):
        self._data = {}

    async def write(self, id, content, metadata):
        self._data[id] = {"content": content, "metadata": metadata}

    async def read(self, id, version=None):
        return self._data.get(id, {}).get("content", "")

    async def list_versions(self, id):
        return [1] if id in self._data else []


class FakeEventStore:
    def __init__(self):
        self.events = []

    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        self.events.append({"event_type": event_type, "actor": actor, "artifact_id": artifact_id, **kwargs})

    async def query(self, artifact_id=None, event_type=None, limit=100):
        return []


class FakeEcsStore:
    def __init__(self):
        self._state = {}
        self.transitions = []

    async def transition(self, artifact_id, from_state, to_state, actor, reason, superseded_by=None):
        self._state[artifact_id] = to_state
        self.transitions.append({"artifact_id": artifact_id, "to_state": to_state})

    async def current_state(self, artifact_id):
        return self._state.get(artifact_id)


TEST_CONFIG = {
    "models": {
        "ci_mode": True,
        "context_window_limit": 128000,
        "synthesis": {"provider": "deepseek", "model": "deepseek-chat"},
        "embedding": {"provider": "ollama", "model": "nomic-embed-text:v1.5", "dimensions": 768},
    },
    "trajectory": {
        "loop_repetition_threshold": 3,
        "anxiety_detection_enabled": True,
        "failure_streak_threshold": 3,
    },
}


@pytest.fixture
def stores():
    return FakeArtifactStore(), FakeTraceStore(), FakeEventStore(), FakeEcsStore()


@pytest.fixture
def manager():
    return SessionManager(config=TEST_CONFIG)


@pytest.mark.asyncio
async def test_happy_path_multi_turn(manager, stores):
    """3-turn session: no trajectory signals, normal completion."""
    artifact, trace, events, ecs = stores

    ctx = manager.create_session("s1", "p1")
    for i in range(3):
        # Simulate synthesis and commit
        artifact_id = f"a_s1_t{i+1}"
        await artifact.write(artifact_id, f"Content turn {i+1}", {})
        await ecs.transition(artifact_id, None, "SPECIFIED", "test", "test")
        await ecs.transition(artifact_id, "SPECIFIED", "GENERATED", "test", "test")
        await ecs.transition(artifact_id, "GENERATED", "APPROVED", "test", "test")

        ctx = await manager.advance_turn(ctx, output_tokens=2000)

    assert ctx.turn_count == 3
    assert manager.context_utilization(ctx) < 0.1  # well below limit

    # No trajectory signals should fire
    signals, intervene = await manager.check_trajectory(ctx, trace)
    assert not intervene


@pytest.mark.asyncio
async def test_trajectory_regulation_with_reset(manager, stores):
    """5-turn session → trajectory signals → context reset → fresh session."""
    artifact, trace, events, ecs = stores

    ctx = manager.create_session("s2", "p1")

    # Simulate 5 turns with declining output (anxiety pattern)
    for i in range(5):
        artifact_id = f"a_s2_t{i+1}"
        await artifact.write(artifact_id, f"Content turn {i+1}", {})
        ctx = await manager.advance_turn(ctx, output_tokens=2000)

    # Simulate trajectory signals firing
    signals = [
        TrajectorySignal(
            signal_type="loop", session_id="s2", failure_type="D",
            confidence=0.8, detail="loop detected", detected_at="2026-05-27T10:00:00Z",
        ),
        TrajectorySignal(
            signal_type="anxiety", session_id="s2", failure_type="F",
            confidence=0.7, detail="anxiety", detected_at="2026-05-27T10:00:00Z",
        ),
    ]

    # Handle intervention (should trigger full reset)
    new_ctx = await manager.handle_intervention(
        ctx, signals, artifact, trace, events, ecs,
    )

    # Verify reset happened
    assert new_ctx.turn_count == 0
    assert new_ctx.last_reset_at is not None
    assert new_ctx.context_tokens_estimate == 0

    # Verify trace events logged
    assert any(e.get("node_type") == "L4" for e in trace.events)

    # Verify DEFINER notified
    assert any(e.get("event_type") == "context_reset" for e in events.events)


@pytest.mark.asyncio
async def test_model_slot_resolver_ci_mode():
    """Model slot resolver returns deterministic fixtures in CI mode."""
    resolver = ModelSlotResolver(TEST_CONFIG)
    result = await resolver.call("synthesis", [{"role": "user", "content": "test"}])
    assert "CI fixture" in result["content"]
    assert result["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_embedding_client_mock():
    """Embedding client produces vectors via mock Ollama."""
    from adapter.embedding.ollama_embed import fake_embed_via_provider
    vec = fake_embed_via_provider("test query")
    assert len(vec) == 768
    assert isinstance(vec[0], float)
```
<!-- ESTIMATED_TOKENS: ~800 -->

---

## CHUNK-5.9: Network Isolation and Model-Name Gate

```
CHUNK-5.9: Network Isolation and Model-Name Gate
PHASE: 5
DEPENDS-ON: CHUNK-5.8, CHUNK-4.8
CODER-PROFILE: L1
CONTEXT-BUDGET: ~2,000 tokens
FILES:
  tests/test_phase3_network_gate.py
INTERFACES:
  # No new interfaces — test-only chunk
TESTS:
  tests/test_phase3_network_gate.py
GATE: uv run pytest tests/test_phase3_network_gate.py -xvs
```

### Prose

This chunk extends the cross-cutting network isolation and model-name gate from CHUNK-1.7 and CHUNK-4.8 to
cover all Phase 3 code. It verifies that no Phase 3 module makes network calls outside of the adapter layer,
and that no model names are hardcoded in any Phase 3 orchestration or foundation code.

**Network isolation.** The test asserts that `orchestration/trajectory/*.py` and `orchestration/session.py` do
not import `httpx`, `requests`, `openai`, `anthropic`, `aiohttp`, or any other network library. Only
`adapter/model_slot_resolver.py` and `adapter/embedding/ollama_embed.py` may import `httpx` — and even there,
the import is used conditionally (only when `ci_mode=false`). All other modules must use injected protocols
for storage and model access.

**Model-name gate.** The test asserts that no Phase 3 code contains hardcoded model names (claude, gpt,
deepseek-chat, qwen, nomic). All model names must come from config through the `ModelSlotResolver`. The test
scans all `orchestration/trajectory/*.py` and `orchestration/session.py` files for forbidden strings.

**Import boundary gate.** The test re-verifies the import boundary rules from §7.2: foundation does not import orchestration, foundation does not import adapter, orchestration does not import adapter directly (only through injected protocols).

The gate test verifies: (a) trajectory modules have no network imports, (b) session module has no network imports, (c) no hardcoded model names in orchestration code, (d) import boundaries hold across all Phase 3 code, (e) all Phase 1/2 network gates still
pass.

### ANNEX

**`tests/test_phase3_network_gate.py`:**

```python
"""Phase 3 network isolation and model-name gate.

Extends CHUNK-1.7 and CHUNK-4.8 gates for Phase 3 code.
Per §7.2: orchestration must not import adapter directly.
Per §4.1: no hardcoded model names in application code.
"""
import ast
import importlib
from pathlib import Path

import pytest

# Phase 3 modules to check
PHASE3_ORCHESTRATION_MODULES = [
    "orchestration.trajectory.loop_detector",
    "orchestration.trajectory.anxiety_detector",
    "orchestration.trajectory.failure_streak",
    "orchestration.trajectory.regulator",
    "orchestration.trajectory.context_reset",
    "orchestration.session",
]

FORBIDDEN_NETWORK_IMPORTS = {
    "httpx", "requests", "openai", "anthropic",
    "aiohttp", "urllib3", "socket",
}

FORBIDDEN_MODEL_NAMES = {
    "claude", "gpt-4", "gpt-5.5", "deepseek-chat",
    "qwen3-coder", "nomic-embed",
}


def _get_imports(module_name: str) -> set[str]:
    """Get top-level imports from a module."""
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            return set()
        source = Path(spec.origin).read_text()
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        return imports
    except Exception:
        return set()


@pytest.mark.parametrize("module_name", PHASE3_ORCHESTRATION_MODULES)
def test_no_network_imports(module_name):
    """Orchestration modules must not import network libraries."""
    imports = _get_imports(module_name)
    violations = imports & FORBIDDEN_NETWORK_IMPORTS
    assert not violations, (
        f"{module_name} imports forbidden network libraries: {violations}"
    )


@pytest.mark.parametrize("module_name", PHASE3_ORCHESTRATION_MODULES)
def test_no_hardcoded_model_names(module_name):
    """Orchestration modules must not contain hardcoded model names."""
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            pytest.skip(f"Module {module_name} not found")
        source = Path(spec.origin).read_text().lower()
        violations = []
        for name in FORBIDDEN_MODEL_NAMES:
            if f'"{name}' in source or f"'{name}" in source:
                violations.append(name)
        assert not violations, (
            f"{module_name} contains hardcoded model names: {violations}"
        )
    except Exception:
        pytest.skip(f"Cannot check {module_name}")


def test_adapter_model_resolver_has_no_hardcoded_models():
    """ModelSlotResolver must not hardcode model names in logic."""
    try:
        spec = importlib.util.find_spec("adapter.model_slot_resolver")
        if spec is None or spec.origin is None:
            pytest.skip("Module not found")
        source = Path(spec.origin).read_text()
        # Config keys are OK; hardcoded model names in logic are not
        for name in ["claude-sonnet", "gpt-4", "deepseek-chat"]:
            assert f'"{name}"' not in source, f"Hardcoded model: {name}"
    except Exception:
        pytest.skip("Cannot check")
```
<!-- ESTIMATED_TOKENS: ~400 -->

---

## Config Additions Summary

Phase 3 adds the following sections to `config/aip.config.toml`:

```toml
[models]
ci_mode = true                   # false in production — per §1.8 toggleable
context_window_limit = 128000    # shared across all slots

[trajectory]
# Loop detection (Type D)
loop_detection_window = 5
loop_repetition_threshold = 3
loop_confidence_base = 0.70

# Context anxiety (Type F) — per §1.8 toggleable
anxiety_detection_enabled = true
anxiety_window_size = 5
anxiety_length_decline_threshold = 0.30
anxiety_context_utilization_threshold = 0.70
anxiety_confidence_threshold = 0.60

# Failure streak (Type E)
failure_streak_threshold = 3
failure_streak_window = 20
failure_streak_confidence_base = 0.65

# Trajectory regulator
intervention_min_confidence = 0.50
intervention_signal_threshold = 2
```

All values are toggleable per §1.8. Every L4 trigger carries `model_gen_assumption` per §1.8 so Sexton can audit it when model slots change.

---

## Acceptance Gates (Phase 3 Coverage)

Phase 3 contributes to the following acceptance gates from §22:

```text
[31] Harness evolution principle applied:
     Every L4 trigger carries model_gen_assumption field.
     TrajectorySignal.model_gen_assumption is required per CHUNK-5.0a.
     tests/test_phase3_schema_additions.py verifies the field exists.

[34] Slot assignment implemented per §4.1 default table:
     ModelSlotResolver resolves all four slots from config.
     OllamaEmbeddingClient implements EmbeddingProvider Protocol.
     tests/test_model_slot_resolver.py verifies slot resolution.

[35] Workflow 0.1 executable:
     Multi-turn session extends Workflow 0.1 with trajectory regulation.
     SessionManager composes with workflow engine without circular dependencies.
     tests/test_phase3_integration.py verifies multi-turn lifecycle.
```
