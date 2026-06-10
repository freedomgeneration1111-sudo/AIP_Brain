# Architecture

AIP 0.1 follows a strict three-layer architecture with dependency inversion via Protocol classes.

---

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        ADAPTER LAYER                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ REST API │ │   CLI    │ │   MCP    │ │   Chat   │           │
│  │ FastAPI  │ │  Click   │ │  Server  │ │ Surface  │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │  Auth    │ │  Rate    │ │  Vector  │ │  Vigil   │           │
│  │Middleware│ │ Limiter  │ │  Stores  │ │  Store   │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Entity   │ │ Knowledge│ │ Plugin   │ │ DB Conc. │           │
│  │  Store   │ │  Store   │ │  Loader  │ │ Manager  │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
├──────────────────────────────────────────────────────────────────┤
│                     ORCHESTRATION LAYER                          │
│  ┌───────────────┐ ┌──────────────┐ ┌────────────────┐         │
│  │   Canonical   │ │   Sexton     │ │     Beast      │         │
│  │   Pipeline    │ │   Actor      │ │     Actor      │         │
│  └───────────────┘ └──────────────┘ └────────────────┘         │
│  ┌───────────────┐ ┌──────────────┐ ┌────────────────┐         │
│  │    Vigil      │ │   Budget     │ │   Workflow     │         │
│  │    Actor      │ │   Manager    │ │    Engine      │         │
│  └───────────────┘ └──────────────┘ └────────────────┘         │
│  ┌───────────────┐ ┌──────────────┐ ┌────────────────┐         │
│  │  L4 Traj.     │ │  ACE         │ │    Router      │         │
│  │  Monitor      │ │  Playbook    │ │                │         │
│  └───────────────┘ └──────────────┘ └────────────────┘         │
│  ┌────────────────────────────────────────────────────┐         │
│  │  Nodes: Synthesis, Faithfulness, DomainCoherence,  │         │
│  │  AdversarialEval, DefinerGate, Commit              │         │
│  └────────────────────────────────────────────────────┘         │
├──────────────────────────────────────────────────────────────────┤
│                       FOUNDATION LAYER                           │
│  ┌───────────────┐ ┌──────────────┐ ┌────────────────┐         │
│  │  Protocols    │ │   Schemas    │ │   ECS Graph    │         │
│  │  (16+ Protos) │ │  (30+ DCs)   │ │  State Machine │         │
│  └───────────────┘ └──────────────┘ └────────────────┘         │
│  ┌───────────────┐ ┌──────────────┐                             │
│  │  Validation   │ │   Config     │                             │
│  │  Rules        │ │   Loader     │                             │
│  └───────────────┘ └──────────────┘                             │
└──────────────────────────────────────────────────────────────────┘
```

---

## Dependency Rules

| Layer | May Import From | Must Not Import From |
|-------|----------------|---------------------|
| Foundation | stdlib, pydantic | Orchestration, Adapter |
| Orchestration | Foundation | Adapter |
| Adapter | Foundation, Orchestration (composition root only) | Nothing below |

All cross-layer dependencies are expressed via **Protocol classes** (structural typing), not concrete imports. This enables:
- Swap SQLite ↔ PostgreSQL without changing orchestration logic
- Swap Ollama ↔ OpenAI without changing pipeline code
- Test orchestration with in-memory stubs

Layer discipline detail:
- foundation: imports no AIP upper layers (stdlib/pydantic only)
- orchestration: may import foundation/protocols, not adapter (known violations documented in AIP-G-06/07)
- adapter composition root (app.py, dependencies.py): may wire orchestration implementations
- adapter routes/GUI: should use container/protocol interfaces, not concrete orchestration internals where avoidable

---

## Key Subsystems

### ECS State Machine (§9.3)

The Entity Control State machine governs every artifact's lifecycle:

```
SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED
                ↓           ↓
              FAILED     REJECTED
```

- Transitions are validated by `foundation/ecs_graph.py`
- Every transition is recorded via `EcsStore.transition()` with actor and reason
- Invalid transitions raise `InvalidTransitionError`
- The graph makes it **structurally impossible** to skip states (e.g., SPECIFIED → APPROVED)

### Canonical Pipeline

The 10-step pipeline for promoting artifacts from REVIEWED → APPROVED:

1. Verify REVIEWED state
2. Read artifact content
3. Run faithfulness evaluation (L3a Stage 2)
4. Run domain coherence evaluation (L3a Stage 3)
5. Check AutonomyGate for admin escalation
6. Verify approved_by = "definer"
7. Write to CanonicalStore
8. Transition ECS state to APPROVED
9. Re-index (Vector + Lexical) if configured
10. Record Vigil health check + write Event

### Budget System (§6)

Three-level token budget enforcement:
- **Session**: per-session token limit (default: 500,000)
- **Project**: per-project token limit (default: 5,000,000)
- **Daily**: per-day token limit (default: 10,000,000)

Features:
- `budget_hard_stop = true` blocks calls that would exceed limits
- `budget_warning_threshold = 0.80` emits events at 80% consumption
- BudgetManager composes BudgetStore (persistence) + BudgetConfig (limits)

### L4 Trajectory Regulation (§10.1)

Three detectors prevent runaway orchestration:
- **Loop Detector**: Detects repeated similar outputs
- **Anxiety Detector**: Detects escalating model uncertainty
- **Failure Streak**: Detects consecutive failures

Each carries a `model_gen_assumption` per §1.8.

### Sexton Actor (§16.1)

Background maintenance engine with five operations (ADR-011 refactor):
- **Tagging**: Batch LLM-based domain tagging of untagged corpus turns
- **Embedding**: Background embedding pass for unembedded turns (~50/cycle)
- **Wiki generation**: Domain article generation as GENERATED artifacts
- **Graph extraction**: Entity extraction and relationship inference from corpus
- **Classification**: Failure classification using Appendix E taxonomy (A–F)

> **Note (DEBT-006):** The new full-maintenance Sexton (`actors/sexton.py`, 1,341 lines) is built
> but **not yet wired** into `app.py`. The old failure-classifier-only Sexton is currently active.
> See TECH_DEBT.md#DEBT-006 for remediation steps.

### Beast Actor (§3)

Active synthesis support actor:
- Context advisory for augmented chat (domain overview + retrieval injection)
- On-demand wiki draft generation
- Health checks (every 60s)
- Domain summary generation (event-driven, not timer-driven)

### Vigil Actor

Quality evaluation actor with two monitoring paths:
- **Synthesis quality**: Citation rate, grounding rate, LLM faithfulness scoring
- **Retrieval quality**: Periodic precision@5 sampling with degradation alerting (Sprint 6.4)
- Triggers re-evaluation on model slot changes
- Records health checks to VigilQualityStore (persistent, with retention/rollup)

---

## Protocol Classes (Interfaces)

AIP uses Python's `Protocol` (PEP 544) for structural subtyping:

| Protocol | Purpose |
|----------|---------|
| `VectorStore` | Vector similarity search (sqlite-vss / pgvector) |
| `LexicalStore` | Full-text search (FTS5) |
| `CanonicalStore` | DEFINER-approved artifact storage |
| `ArtifactStore` | Draft artifact content |
| `EcsStore` | ECS state transitions |
| `EventStore` | Lifecycle event recording |
| `BudgetStore` | Token budget tracking |
| `AutonomyGate` | Sovereignty enforcement |
| `ModelProvider` | Model API abstraction |
| `EmbeddingProvider` | Text-to-vector embedding |
| `VigilStore` | Vigil health data |
| `AuthStore` | Authentication storage |
| `KnowledgeStore` | Compiled knowledge layer |
| `PluginProvider` | Plugin model extension |

---

## Data Flow

### Synthesis Pipeline

```
DEFINER Input (Chat/CLI)
       ↓
    Synthesis Node (ModelProvider.call)
       ↓
    Faithfulness Evaluation
       ↓
    Domain Coherence Evaluation
       ↓
    DEFINER Gate (AutonomyGate.escalate)
       ↓
    Commit Node (CanonicalStore.write, EcsStore.transition)
       ↓
    Re-index (VectorStore.upsert, LexicalStore.index_document)
       ↓
    Vigil Health Recording
```

### Retrieval Pipeline

The hybrid retrieval pipeline uses multi-channel dispatch with Reciprocal Rank Fusion (RRF):

```
User Query
       ↓
    RetrievalOrchestrator.retrieve()
       ↓
    ┌─────────────┬─────────────┬──────────────┐
    │ FTS5 Channel│Vector Channel│Corpus Channel│   (parallel dispatch)
    └──────┬──────┴──────┬──────┴──────┬───────┘
           ↓             ↓             ↓
    ┌──────────────────────────────────────────┐
    │         Weighted RRF Fusion (k=60)        │
    │   channel_weights: vector=0.6, fts=0.4   │
    └──────────────────┬───────────────────────┘
                       ↓
              Quality Gate (min_score, min_hits)
                       ↓
              Coverage-Aware Gating
              (falls back to FTS5-only if
               vector coverage < 10%)
                       ↓
              Ranked Results
```

- **RRF fusion**: Each channel returns ranked results; RRF computes `1/(k + rank)` per result,
  weighted by channel weight, then sums across channels.
- **Channel weights**: Configurable via `[retrieval.channel_weights]` in `aip.config.toml`.
- **Coverage-aware gating**: When vector coverage is below threshold, the vector channel is
  disabled and FTS5-only results are returned (prevents degraded hybrid quality).
- **Evaluation**: `aip eval retrieval --mode (hybrid|fts-only|all)` measures P@5, R@10, MRR
  against golden queries. `scripts/retrieval_weight_tuning.py` does grid search over weights.
