# AIP 0.1 — Spec Import & Chunk Remapping Reference

**Last updated:** May 2026

---

## 1. Purpose

This document records the chunk-number remapping, terminology conventions, and process rules that govern
spec-to-code mapping across all architectural phases. It exists because the spec chunk numbers (e.g., "Phase 2
spec chunks 2.0a–2.8") collide with existing git history chunk series (repo 2.x, 3.x). The remapping avoids
confusion.

---

## 2. Chunk Number Remapping

### Permanent +2 Offset Policy

Architectural Phase N uses chunk series (N+2).x in all BuildSpecs and git commits:

| Architectural Phase | Chunk Series | Spec File |
|---------------------|-------------|-----------|
| Phase 1 | 1.x | `specs/AIP_0_1_Phase1_BuildSpec_Rev1.3.md` |
| Phase 2 (ECS Lifecycle, Review, YAML Engine) | 4.x | `specs/AIP_0_1_Phase2_BuildSpec_Rev1.2.md` |
| Phase 3 (Embedding, L4 Trajectory, Multi-Turn) | 5.x | `specs/AIP_0_1_Phase3_BuildSpec_Rev1.1.md` |
| Phase 4 (pgvector, Node Promotion, Production) | 6.x | `specs/AIP_0_1_Phase4_BuildSpec_Rev1.0.md` |
| Phase 5 (Sexton, ACE, Beast, Router, Budget) | 7.x | `specs/AIP_0_1_Phase5_BuildSpec_Rev1.0.md` |
| Phase 6 (Surfaces: REST, CLI, Chat, MCP, Admin) | 8.x | `specs/AIP_0_1_Phase6_BuildSpec_Rev1.0.md` |
| Phase 7 (Vigil, Auth, Rate Limiting, Pipeline, UI) | 9.x | `specs/AIP_0_1_Phase7_BuildSpec_Rev1.0.md` |
| Phase 8 (Knowledge, Plugins, Collaborators, Perf) | 10.x | `specs/AIP_0_1_Phase8_BuildSpec_Rev1.0.md` |
| Phase 9 | 11.x | `specs/AIP_0_1_Phase9_BuildSpec_Rev1.0.md` |

### Phase 2 Detail (4.x Series)

| Chunk | Deliverable |
|-------|-------------|
| 4.0a | Schema additions (ReviewVerdict, ReviewContext, EcsTransition, Event) + Protocol amendments |
| 4.0b | ECS state graph (VALID_TRANSITIONS) + GuardrailedEcsStore |
| 4.1 | Review node (quality gate) |
| 4.2 | Re-synthesis loop (REJECTED → GENERATED with failure context) |
| 4.3 | ArtifactStore versioning |
| 4.4 | EventStore query API |
| 4.5 | YAML workflow engine |
| 4.6 | Workflow 0.1 YAML definition |
| 4.7 | Integration test |
| 4.8 | Network isolation and model-name gate |

### Phase 3 Detail (5.x Series)

| Chunk | Deliverable |
|-------|-------------|
| 5.0a | Schema additions (TrajectorySignal, SessionContext, ModelSlotConfig) + Protocol amendments |
| 5.0b | Model slot resolver |
| 5.1 | Embedding slot client (OllamaEmbeddingClient) |
| 5.2 | Loop detector (Type D) |
| 5.3 | Context anxiety detector (Type F) |
| 5.4 | Failure streak detector (Type E) |
| 5.5 | Trajectory regulator ("2 of 3" rule) |
| 5.6 | Context reset protocol |
| 5.7 | Multi-turn session context (SessionManager) |
| 5.8 | Integration test |
| 5.9 | Network isolation and model-name gate |

---

## 3. Terminology

| Term | Meaning |
|------|---------|
| **Architectural Phase N** | Logical scope of work (e.g., "Phase 2 covers ECS lifecycle, review loop, YAML engine") |
| **CHUNK-N.x** | Build unit within a BuildSpec, where N is the build series number (may differ from architectural phase) |
| **Repo N.x** | Historical chunk series in git history |
| **BuildSpec Phase N** | The spec document for Architectural Phase N (may use a different chunk series) |

Always use qualified terms. Avoid bare "Phase N" without context.

---

## 4. Process Rules

1. **Amend by addition.** Protocol amendments append method stubs to existing classes. Schema amendments
append new dataclasses. Never redeclare a Protocol class or modify/reorder existing definitions.

2. **Deterministic CI.** All gate tests must pass without network, API keys, or secrets. CI mode returns deterministic fixtures.

3. **Import boundaries.** Foundation never imports orchestration or adapter. Adapter may import foundation but not orchestration. Orchestration may import foundation and adapter.

4. **No hardcoded model names.** All model references resolve through named slots from config (per spec §4.1).

5. **Extend, don't replace.** When spec work touches files that already have code from prior phases, extend the existing code rather than rewriting it.

---

## 5. Spec File Inventory

| File | Chunk Series | Status |
|------|-------------|--------|
| `specs/AIP_0_1_Phase1_BuildSpec_Rev1.3.md` | 1.x | Complete |
| `specs/AIP_0_1_Phase2_BuildSpec_Rev1.2.md` | 4.x | Complete |
| `specs/AIP_0_1_Phase3_BuildSpec_Rev1.1.md` | 5.x | Complete |
| `specs/AIP_0_1_Phase4_BuildSpec_Rev1.0.md` | 6.x | Complete |
| `specs/AIP_0_1_Phase5_BuildSpec_Rev1.0.md` | 7.x | Complete |
| `specs/AIP_0_1_Phase6_BuildSpec_Rev1.0.md` | 8.x | Complete |
| `specs/AIP_0_1_Phase7_BuildSpec_Rev1.0.md` | 9.x | Complete |
| `specs/AIP_0_1_Phase8_BuildSpec_Rev1.0.md` | 10.x | Complete |
| `specs/AIP_0_1_Phase9_BuildSpec_Rev1.0.md` | 11.x | Complete |
