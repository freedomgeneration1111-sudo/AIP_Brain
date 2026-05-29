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
| Adapter | Foundation, Orchestration | Nothing below |

All cross-layer dependencies are expressed via **Protocol classes** (structural typing), not concrete imports. This enables:
- Swap SQLite ↔ PostgreSQL without changing orchestration logic
- Swap Ollama ↔ OpenAI without changing pipeline code
- Test orchestration with in-memory stubs

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

Failure classification engine:
- Reads trace events with unclassified failures
- Classifies using Appendix E taxonomy (A–F)
- Auto-derives ACE Playbook entries from classifications
- Audits `model_gen_assumption` fields on model slot changes

### Beast Actor (§3)

Maintenance cadence actor:
- Corpus reindexing (hourly)
- Entity maintenance (every 30 min)
- Health checks (every 60 sec)

### Vigil Actor

Read-only health monitoring:
- Canonical corpus staleness detection
- Entity consistency checking
- Triggers re-evaluation on model slot changes
- Records health checks to VigilStore

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
