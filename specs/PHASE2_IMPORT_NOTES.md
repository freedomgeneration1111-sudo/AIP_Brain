# AIP 0.1 — Phase 2 & Phase 3 BuildSpec Import Notes

**Date:** May 2026  
**Status:** Authoritative — all future sessions MUST read this before touching Phase 2/3 spec code  
**DEFINER:** Moses Jorgensen  

---

## 1. Purpose

This document resolves six glitches identified when the Phase 2 BuildSpec (Rev 1.1) was imported into the live code repo. It establishes the canonical chunk-number mapping, terminology rules, and process rules that govern all future work against these specs. Any session that touches code described in the Phase 2 or Phase 3 BuildSpecs MUST follow the rules in this document.

---

## 2. Glitch Summary

| # | Severity | Issue | Resolution |
|---|----------|-------|------------|
| 1 | SEVERE | CHUNK numbering collision — spec uses 2.0a–2.8 but repo's git history already has 2.1–2.13 for earlier YAML engine work; 3.1–3.12 for L4/Sexton/budget work | Remap spec chunks: Phase 2 spec → 4.0a–4.8; Phase 3 spec → 5.0a–5.9 |
| 2 | SEVERE | Phase boundary assumption mismatch — spec assumes "only Phase 1 exists" but repo already built substantial 2.x and 3.x code | Add Repo State Reconciliation section to both specs; all Continuity Checks must verify against actual code, not spec assumptions |
| 3 | HIGH | "Phase 2" terminology collision — architectural phase vs. repo chunk series | Define strict terminology: "Architectural Phase 2" = logical scope; "repo 2.x" = historical commit series |
| 4 | MEDIUM | Missing process rules — Phase 2 spec inherits Phase 1 process rules implicitly but never restates them | Add §Process Rules section to both specs (verbatim from Phase 1 Rev 1.3) |
| 5 | MEDIUM | Current code vs. spec expectations — most Phase 2 types and methods don't exist yet | Document gap audit below; spec is the target, code must be built to match |
| 6 | POSITIVE | S2 fix (append-only Protocol amendments) is sound and aligns with practice | No change needed |

---

## 3. Chunk Number Remapping (Authoritative)

The spec documents have been updated to use remapped chunk numbers. **The original 2.x and 3.x chunk numbers in the spec documents are NO LONGER VALID.** All references must use the remapped numbers.

### Phase 2 BuildSpec (Architectural Phase: ECS Lifecycle, Review Loop & YAML Engine)

| Original Spec Chunk | Remapped Chunk | Deliverable |
|---------------------|---------------|-------------|
| CHUNK-2.0a | **CHUNK-4.0a** | Schema additions (ReviewVerdict, ReviewContext, EcsTransition, Event) + Protocol amendments |
| CHUNK-2.0b | **CHUNK-4.0b** | ECS state graph (VALID_TRANSITIONS) + GuardrailedEcsStore |
| CHUNK-2.1 | **CHUNK-4.1** | Review node (quality gate + DEFINER review) |
| CHUNK-2.2 | **CHUNK-4.2** | Re-synthesis loop (REJECTED→GENERATED with failure context) |
| CHUNK-2.3 | **CHUNK-4.3** | ArtifactStore versioning |
| CHUNK-2.4 | **CHUNK-4.4** | EventStore query API |
| CHUNK-2.5 | **CHUNK-4.5** | YAML workflow engine |
| CHUNK-2.6 | **CHUNK-4.6** | Workflow 0.1 YAML definition |
| CHUNK-2.7 | **CHUNK-4.7** | Integration test |
| CHUNK-2.8 | **CHUNK-4.8** | Network isolation and model-name gate |

### Phase 3 BuildSpec (Architectural Phase: Embedding Slot, L4 Trajectory & Multi-Turn)

| Original Spec Chunk | Remapped Chunk | Deliverable |
|---------------------|---------------|-------------|
| CHUNK-3.0a | **CHUNK-5.0a** | Schema additions (TrajectorySignal, SessionContext, ModelSlotConfig) + Protocol amendments |
| CHUNK-3.0b | **CHUNK-5.0b** | Model slot resolver |
| CHUNK-3.1 | **CHUNK-5.1** | Embedding slot client (OllamaEmbeddingClient) |
| CHUNK-3.2 | **CHUNK-5.2** | Loop detector (Type D) |
| CHUNK-3.3 | **CHUNK-5.3** | Context anxiety detector (Type F) |
| CHUNK-3.4 | **CHUNK-5.4** | Failure streak detector (Type E) |
| CHUNK-3.5 | **CHUNK-5.5** | Trajectory regulator ("2 of 3" rule) |
| CHUNK-3.6 | **CHUNK-5.6** | Context reset protocol |
| CHUNK-3.7 | **CHUNK-5.7** | Multi-turn session context (SessionManager) |
| CHUNK-3.8 | **CHUNK-5.8** | Integration test |
| CHUNK-3.9 | **CHUNK-5.9** | Network isolation and model-name gate |

### Why 4.x and 5.x?

The code repo's git history already owns the 2.x and 3.x chunk series:

- **Repo 2.x** (CHUNK-2.1 through CHUNK-2.13): Earlier YAML engine mechanics work that was narrower than what the Architectural Phase 2 spec describes. These commits are historical fact — they cannot be renumbered.
- **Repo 3.x** (CHUNK-3.1 through CHUNK-3.12): L4/Sexton/ACE/budget foundation work that was already built before the Phase 2 spec was imported. Also historical fact.

Jumping to 4.x and 5.x avoids any collision with these existing series. Future phases (Phase 4, Phase 5, Phase 6 in the architectural sense) will use 6.x, 7.x, 8.x respectively, maintaining the +2 offset.

---

## 4. Terminology Rules

To prevent the "Phase 2" collision from recurring, all sessions must use these terms precisely:

| Term | Meaning | Example |
|------|---------|---------|
| **Architectural Phase N** | The logical scope of work as defined in the Phase Scope Definition document | "Architectural Phase 2 covers ECS lifecycle, review loop, and YAML engine" |
| **CHUNK-N.x** | A specific build unit within a BuildSpec, where N is the BUILD SERIES number (may differ from architectural phase) | "CHUNK-4.5 implements the YAML workflow engine" |
| **Repo N.x** | The historical chunk series in git history | "Repo 2.x was narrower YAML engine mechanics" |
| **BuildSpec Phase N** | The spec document for Architectural Phase N (may use different chunk series numbers) | "BuildSpec Phase 2 uses chunk series 4.x" |

**FORBIDDEN:** Using "Phase 2" without qualification to refer to either the architectural phase, the repo chunk series, or the spec document. Always specify which you mean.

---

## 5. Repo State Reconciliation

### What the Architectural Phase 2 Spec expects to exist (but may not)

The Phase 2 BuildSpec was written assuming only Phase 1 code exists. The following Phase 2 types and methods are specified but do NOT yet exist in the codebase as properly typed, tested implementations:

| Type/Method | Spec Chunk | Status |
|-------------|-----------|--------|
| `ReviewVerdict` dataclass | CHUNK-4.0a | Not implemented |
| `ReviewContext` dataclass | CHUNK-4.0a | Not implemented |
| `EcsTransition` dataclass | CHUNK-4.0a | Not implemented |
| `Event` dataclass (with required timestamp) | CHUNK-4.0a | Not implemented |
| `FailureTypeCode` type alias | CHUNK-4.0a | Not implemented |
| `EventStore.query()` method | CHUNK-4.0a | Not implemented |
| `ArtifactStore.list_versions()` method | CHUNK-4.0a | Not implemented |
| `ArtifactStore.read(id, version=)` method | CHUNK-4.0a | Not implemented |
| `EcsStore.current_state()` method | CHUNK-4.0a | Not implemented |
| `VALID_TRANSITIONS` dict | CHUNK-4.0b | Not implemented |
| `InvalidTransitionError` | CHUNK-4.0b | Not implemented |
| `GuardrailedEcsStore` | CHUNK-4.0b | Not implemented |
| `orchestration/review.py` | CHUNK-4.1 | Not implemented |
| `orchestration/re_synthesize.py` | CHUNK-4.2 | Not implemented |
| `VersionedArtifactStore` | CHUNK-4.3 | Not implemented |
| `QueryableEventStore` | CHUNK-4.4 | Not implemented |
| `orchestration/engine.py` (full YAML engine) | CHUNK-4.5 | Partial — repo 2.x has mechanics but not full spec compliance |
| `workflows/synthesis_session_v1.yaml` | CHUNK-4.6 | Not implemented |
| Full lifecycle integration test | CHUNK-4.7 | Not implemented |
| Phase 2 network isolation gate | CHUNK-4.8 | Not implemented |

### What already exists from repo 2.x and 3.x work

The repo's earlier "Phase 2" work (CHUNK-2.1–2.13 in git) was a narrower slice focused on YAML engine mechanics. The repo's 3.x series built L4/Sexton/ACE/budget foundation work. These exist as code artifacts that may partially overlap with or pre-empt portions of the Architectural Phase 2 and Phase 3 specs.

**Continuity Check rule:** When building any CHUNK-4.x or CHUNK-5.x, the builder MUST:
1. Read WORKLOG for all prior work on the same files/modules
2. Check whether repo 2.x or 3.x code already implements part of the spec
3. If overlap exists, extend existing code to meet the spec (amend by addition) rather than rewriting from scratch
4. Document any reconciliation in WORKLOG

---

## 6. Process Rules (Inherited from Phase 1 Rev 1.3)

These rules are binding for all work against the Phase 2 and Phase 3 BuildSpecs:

1. **Continuity Check.** Before starting any chunk, read WORKLOG.md and verify the DEPENDS-ON chunks are merged and green. If not, block.

2. **WORKLOG append-only.** Every chunk completion appends a work record to WORKLOG.md. Never overwrite existing entries.

3. **Amend by addition.** Protocol amendments append method stubs to existing classes. Never redeclare a Protocol class. Schema amendments append new dataclasses. Never modify or reorder existing definitions.

4. **Deterministic CI.** All gate tests must pass without network, API keys, or secrets. CI mode returns deterministic fixtures.

5. **Push after each chunk.** After a chunk passes its gate test, commit and push before starting the next chunk.

6. **Import boundaries.** Foundation never imports orchestration or adapter. Adapter may import foundation but not orchestration. Orchestration may import foundation and adapter.

7. **No hardcoded model names.** Per §4.1, all model references resolve through named slots from config.

8. **Phase references.** Always use qualified terminology (Architectural Phase N, CHUNK-N.x, Repo N.x) — never bare "Phase N".

---

## 7. Gap Audit: Code vs. Spec

### Architectural Phase 2 gaps (spec target vs. code reality)

The Phase 2 spec describes a complete ECS lifecycle with review, rejection, and re-synthesis. The repo's current codebase has:
- YAML engine mechanics from repo 2.x (narrower than spec's CHUNK-4.5)
- Some L4/Sexton/ACE/budget work from repo 3.x
- **None** of the core Phase 2 types: ReviewVerdict, ReviewContext, GuardrailedEcsStore, EventStore.query, etc.

**Build strategy:** Treat the Phase 2 spec as the authoritative target. Build all CHUNK-4.x in order. Where repo 2.x code already exists (primarily engine mechanics), extend it to meet the spec rather than replacing it.

### Architectural Phase 3 gaps (spec target vs. code reality)

The Phase 3 spec describes embedding integration, L4 trajectory regulation, and multi-turn sessions. The repo's current codebase has:
- Some L4-related code from repo 3.x ( Sexton, ACE playbook, budget work)
- **None** of the Phase 3 types: TrajectorySignal, SessionContext, ModelSlotConfig, ModelProvider, EmbeddingProvider

**Build strategy:** Same as Phase 2 — spec is authoritative. Build CHUNK-5.x in order. Extend existing repo 3.x code where it overlaps.

---

## 8. File Inventory

| File | Location | Status |
|------|----------|--------|
| Phase 1 BuildSpec Rev 1.3 | `download/AIP_0_1_Phase1_BuildSpec_Rev1.3.md` | BUILD-READY (chunk series 1.x) |
| Phase 2 BuildSpec Rev 1.2 | `download/AIP_0_1_Phase2_BuildSpec.md` | Remapped to chunk series 4.x; Process Rules + Reconciliation added |
| Phase 3 BuildSpec Rev 1.1 | `download/AIP_0_1_Phase3_BuildSpec.md` | Remapped to chunk series 5.x; Process Rules + Reconciliation added |
| This document | `download/PHASE2_IMPORT_NOTES.md` | Authoritative reference |
| Architecture Rev 5.2 | `upload/AIP_0_1_Architecture_Rev5_2.md` | Master architecture doc |
| Phase Scope Definition | `download/AIP_0_1_Phase_Scope_Definition.docx` | All phases (0–6) scope |
| Phase 4 BuildSpec Rev 1.0 | `specs/AIP_0_1_Phase4_BuildSpec_Rev1.0.md` | Architectural Phase 4 (pgvector adapter, node promotion, L3a S2/3, production hardening); remapped to CHUNK-6.x series per permanent +2 offset policy |

## 9. Phase 4 Import Record (Appended per mandatory pre-read for CHUNK-6.x)

**Date:** 2026-05 (resumption session)
**Action:** Copied AIP_0_1_Phase4_BuildSpec.md (Spec Rev 1.0) from /home/moses/Downloads/ into specs/AIP_0_1_Phase4_BuildSpec_Rev1.0.md following the exact remediation import pattern used for Phase 2 Rev 1.2 and Phase 3 Rev 1.1 (authoritative SSOT in specs/, documented here).

**Remapping:** Per permanent +2 offset policy (established in remediation): Architectural Phase 4 → CHUNK-6.x series. All future references use CHUNK-6.0a–6.6. Terminology rules from §4 remain in force (no bare "Phase 4").

**Key notes from Phase 4 spec (mandatory for all 6.x CCs):**
- Two largely independent paths: (A) pgvector adapter path (6.0a/6.0b/6.3/6.4 — touches only adapter + foundation; DEPENDS-ON only Phase 0/1) vs (B) node promotion path (6.1/6.2 — synthesis + eval pipeline; DEPENDS-ON Phase 3's 5.0b ModelSlotResolver). Converge at 6.5 integration test.
- CHUNK-6.0a is the first chunk: identical append-only/amend-by-addition pattern as 1.0a/4.0a/5.0a on foundation/schemas.py + protocols.py. No existing code deleted/rewritten.
- Explicit Continuity Check rule at end of spec (mirrors Rule #10): for every CHUNK-6.x, read WORKLOG, check repo historical 2.x/3.x/4.x/5.x for overlap on target files, extend existing rather than replace, document reconciliation.
- All new types carry §1.8 model_gen_assumption where model-based; config-driven per §2.2 / §7.2; health_check + count added to VectorStore Protocol.
- Post-Phase-3 Clean Bill of Health (from WORKLOG) is the baseline; 5.8 partial fidelity (starter vs full ANNEX) is documented as non-blocking for Phase 4 start.

**No gap audit required for 6.0a:** No prior 6.x code exists in repo (clean slate for new series). Historical overlaps only possible on shared files (schemas.py, protocols.py, config/aip.config.toml, existing nodes) — Rule #10 reconciliation applies on first touch.

**Status:** Phase 4 spec successfully imported. PHASE2_IMPORT_NOTES.md (now covering through Phase 4) updated. Ready for mandatory full CC on CHUNK-6.0a before any production code.

---

## 10. Phase 5 Import Record (Appended for CHUNK-7.x resumption)

**Date:** 2026-05 (post-Phase-4 completion)
**Action:** Copied `AIP_0_1_Phase5_BuildSpec.md` (Spec Rev 1.0, 86KB) from `/home/moses/Downloads/` into `specs/AIP_0_1_Phase5_BuildSpec_Rev1.0.md` following the exact remediation import pattern used for Phase 2/3/4.

**Remapping:** Per permanent +2 offset policy (established in remediation and reinforced in every subsequent CC): Architectural Phase 5 → **CHUNK-7.x series**. All future references use CHUNK-7.0a–7.7. Terminology rules remain in force.

**Key notes from Phase 5 spec (mandatory for all 7.x CCs):**
- Linearized order: 7.0a (schema/protocol/config) → 7.0b (budget) → 7.1 (Sexton classification) → 7.2 (ACE Playbook) → 7.3 (stale rule audit) → 7.4 (Adaptive router) → 7.5 (Beast) → 7.6 (integration) → 7.7 (final gate).
- Heavy emphasis on **Rule 10 / Repo overlap reconciliation** because substantial partial work from repo 3.x already exists in `orchestration/sexton/`, `orchestration/budget.py`, `orchestration/session.py`, etc. Extend existing rather than replace.
- CHUNK-7.0a is the first chunk: identical append-only/amend-by-addition pattern on `foundation/schemas.py` + `foundation/protocols.py` as all prior 0.0a/1.0a/4.0a/5.0a/6.0a chunks.
- New Protocols (BudgetStore) and amendments (ProjectStore.list_projects, EntityStore methods) follow the ANNEX exactly.
- All new types (AcePlaybookEntry, FailureClassification, etc.) **must** carry `model_gen_assumption` per §1.8.
- Sexton/Beast/Router are orchestration-layer only; strict Protocol injection; no direct adapter storage imports.
- Post-Phase-4 Clean Bill ("Phase 4 complete" at f2bb46c) is the new baseline. 5.8 partial remains non-blocking.
- Continuous execution directive applies: after initial pre-7.0a CC, proceed through the full linearized order without repeated "go" signals, only pausing for genuine user input needs.

**High-risk areas flagged for every CC:**
- Existing `orchestration/sexton/` and `orchestration/budget.py` partials (detailed reconciliation required on first touch of 7.0a/7.0b/7.1).
- Budget enforcement hooks into the CHUNK-4.5 YAML engine and SessionManager.
- Router wrapping of ModelSlotResolver (must be transparent).
- Actor placement vs. existing directory structure.
- All governance gates (layering, no-network, schema, hardcode scans) must stay green.

**Status:** Phase 5 spec successfully imported. PHASE2_IMPORT_NOTES.md extended. Comprehensive handoff prompt written at `PHASE5_HANDOFF_PROMPT.md`. Repo tree clean and pushed (f2bb46c). Ready for mandatory full pre-CHUNK-7.0a Continuity Check before any Phase 5 production code.

---

## 11. Phase 5 Completion Record (Post-Phase-5 Handoff Preparation)

**Date:** 2026-05 (after full completion of CHUNK-7.0a through 7.7)
**Final State:** Architectural Phase 5 is complete and green.
- Final commit: 2a6aef2 (CHUNK-7.7 final gate + Phase 5 completion declaration).
- All CHUNK-7.x work delivered with full per-chunk Continuity Checks, Rule #10 reconciliations, exact scope fidelity, and clean pushes.
- Governance suite (layering, network isolation, model-name gates, schema tests, etc.) remains green at 60+ passed.
- WORKLOG.md contains the complete detailed history of Phase 5.
- Tree clean and fully pushed.

**Remapping reminder for Phase 6:** Per the permanent +2 offset policy, Architectural Phase 6 work must use the **CHUNK-8.x** series. A future Phase 6 BuildSpec (when imported) should be placed at `specs/AIP_0_1_Phase6_BuildSpec_RevX.Y.md` and documented with a new section in this file.

**Status:** Phase 5 complete. Comprehensive Phase 6 handoff prompt created at `PHASE6_HANDOFF_PROMPT.md`. Repository is in a clean, fully pushed state ready for a new session to begin Phase 6 work.

---

## 12. Phase 6 Import Record (Appended for CHUNK-8.x resumption)

**Date:** 2026-05 (current resumption session, immediately after Phase 5 completion docs + PHASE6_HANDOFF_PROMPT.md)
**Action:** Copied `AIP_0_1_Phase6_BuildSpec.md` (Spec Rev 1.0, 100861 bytes) from `/home/moses/Downloads/` into `specs/AIP_0_1_Phase6_BuildSpec_Rev1.0.md` following the exact remediation import pattern used for Phase 2 Rev 1.2, Phase 3 Rev 1.1, Phase 4 Rev 1.0, and Phase 5 Rev 1.0 (authoritative SSOT now in specs/, documented here). No remapping edits needed inside the spec — it was authored with the permanent +2 offset (CHUNK-8.x) already applied.

**Remapping:** Per permanent +2 offset policy (established in remediation, reinforced in every CC, and explicitly called out in the Phase 6 handoff prompt): Architectural Phase 6 → **CHUNK-8.x series**. All future references (CCs, WORKLOG, commits, code comments) use CHUNK-8.0a–8.8 exclusively. Terminology rules from §4 remain in force: "Architectural Phase 6", "CHUNK-8.x", "post-Phase-5 baseline". Never bare "Phase 6".

**Key notes from Phase 6 spec (mandatory for all 8.x CCs):**
- Linearized DAG order (with parallel groups): 8.0a (schema/protocol/config L1) → 8.0b (remaining adapters: FTS5 LexicalStore, CanonicalStore, EntityStore, AutonomyGateImpl) → 8.1 (FastAPI scaffold + Project/Session/Artifact REST) → parallel surfaces 8.2 (CLI), 8.3 (Chat WS), 8.4 (Review Queue + Artifact Browser), 8.5 (MCP server), 8.6 (Admin Console + Memory Inspector) → 8.7 (full surface-to-backend integration) → 8.8 (cross-cutting gates extending 7.7).
- Groups C–D parallel pair; E–H independent paths after scaffold (8.1); all converge at 8.7.
- CHUNK-8.0a is the first chunk: identical append-only/amend-by-addition pattern on `foundation/schemas.py` + `foundation/protocols.py` + `config/aip.config.toml` as 1.0a/4.0a/5.0a/6.0a/7.0a. New types (McpToolDef, AutonomyEscalation, etc.) **must** carry `model_gen_assumption` per §1.8 where model-based.
- New/Amended Protocols: AutonomyGate (new from §6: check/escalate/audit_log), LexicalStore (new full: search/index/delete), CanonicalStore + EntityStore method additions (read/write/list). All per ANNEX exact signatures.
- Surfaces layer (CLI, REST, Chat, MCP, Review, Admin) is strictly **adapter** per §7.2 and import boundaries. Must compose Phase 5 delivered actor layer (Sexton, Beast, AdaptiveRouter, ACE Playbook, BudgetManager) + foundation Protocols via injection; never bypass AutonomyGate (§1.7, Appendix D "MCP ≠ bypass", "UI ≠ authority").
- Post-Phase-5 Clean Bill of Health (completion record §11 above, final gate at 2a6aef2 / 60+ passed) is the new baseline. 5.8 partial remains non-blocking. Phase 6 gates must extend the full Phase 5 battery (layering, no-network, model-name, schema, hardcode scans).
- Continuous execution directive (per handoff): after this session's pre-8.0a CC documented + pushed, proceed autonomously through entire linearized 8.x order. Only stop for the four explicit stop conditions (genuine user input needs, blocker, etc.).

**High-risk areas flagged for every 8.x CC (per Phase 6 handoff prompt):**
1. **Integration with delivered Phase 5 actor layer (highest risk):** MCP servers / CLI / API must expose and invoke Sexton (classification + ACE curation), Beast (cadence maintenance), Adaptive Router (budget-aware routing), ACE Playbook loading, BudgetManager enforcement. All surfaces must route privileged actions through AutonomyGate.
2. **Governance invariants:** Every new Phase 6 module (even adapter) must pass the full extended gate suite from 7.7 (test_layering.py, test_phase2_no_network.py + descendants, schema tests, hardcode scans, file audits). Determinism in CI absolute — zero network.
3. **Rule #10 on every chunk:** Before touching any file, full audit of historical code including the now-complete Phase 5 delivered surface (`orchestration/actors/`, `orchestration/router.py`, `orchestration/ace_playbook.py`, `orchestration/sexton/`, `orchestration/budget.py`, engine extensions, etc.). Prefer "extend existing rather than replace". 8.0a touches only foundation (low overlap risk), but later chunks high.
4. **Import boundaries (§7.2) strictly in force:** Adapter (Phase 6) may import foundation + orchestration Protocols, but surfaces compose actors only through clean injection + AutonomyGate. No direct storage bypass.
5. **No hardcoded model names (§4.1):** All model references through named slots. Config-driven per §1.8.
6. **Environment:** All new surfaces (esp MCP stdio, CLI, FastAPI) must be fully testable in CI mode with zero network calls.

**Status:** Phase 6 spec successfully imported. PHASE2_IMPORT_NOTES.md extended with this authoritative record. PHASE6_HANDOFF_PROMPT.md already present (docs commit 9de400b). Repository in clean state at HEAD 9de400b. Ready for mandatory full 6-step pre-CHUNK-8.0a Continuity Check (heavy Rule #10 emphasis on post-Phase-5 actor layer + cross-ref to Arch Rev 5.2 §3/§16/§22) **before any src/ or tests/ edits**.

---

## 13. Phase 7 Import Record (Appended for CHUNK-9.x resumption)

**Date:** 2026-05 (current resumption session, immediately after Phase 6 completion at ae0fb96)
**Action:** Copied `AIP_0_1_Phase7_BuildSpec.md` (Spec Rev 1.0, 100971 bytes) from `/home/moses/Downloads/` into `specs/AIP_0_1_Phase7_BuildSpec_Rev1.0.md` following the exact remediation import pattern used for all prior phases. No remapping edits needed inside the spec — it was authored anticipating the permanent +2 offset (CHUNK-9.x for Architectural Phase 7).

**Remapping:** Per permanent +2 offset policy (now in its fourth application): Architectural Phase 7 → **CHUNK-9.x series**. All future references use CHUNK-9.0a–9.8 exclusively. Terminology rules remain in force: "Architectural Phase 7", "CHUNK-9.x", "post-Phase-6 baseline". Never bare "Phase 7".

**Key notes from Phase 7 spec (mandatory for all 9.x CCs):**
- Phase 7 is the capstone: Vigil actor (last missing §3 orchestration component), real auth system, rate limiting, canonical promotion pipeline, extended workflow templates, minimal HTMX web UI scaffold, full §22 acceptance verification, and production packaging (Docker Compose for laptop-viable + production profiles).
- Linearized order begins with 9.0a (schema/protocol/config L1 for VigilConfig/AuthConfig/RateLimitConfig/CanonicalPromotionConfig + new Protocols VigilStore/AuthStore + amendments).
- Heavy Rule #10 emphasis expected: now-augmented overlaps with complete Phase 6 surfaces (8.1–8.7 API/CLI/Chat/Review/MCP/Admin/Memory + 8.0b adapters) + full Phase 5 actor layer. "Extend existing rather than replace" remains non-negotiable.
- CHUNK-9.0a follows the identical append-only/amend-by-addition pattern on foundation/schemas.py + foundation/protocols.py + config/aip.config.toml as every prior *.0a.
- New types must carry `model_gen_assumption` per §1.8 where applicable.
- Vigil (9.1) will be the orchestration-layer actor that monitors canonical corpus health, triggers re-evaluation on model slot changes, and maintains entity consistency — using the delivered 8.0b CanonicalStore/EntityStore + 8.1 container.
- Canonical promotion pipeline (9.2) will drive the full REVIEWED→APPROVED→CANONICAL lifecycle using AutonomyGate + EcsStore + CanonicalStore + indexing into LexicalStore/VectorStore.
- Full §22 acceptance test (9.5) is the final verification that the entire system (all prior phases + Phase 7 deliverables) meets the architectural acceptance criteria.
- Post-Phase-6 Clean Bill (final gate at ae0fb96 / 69+ passed core battery) is the new baseline. All Phase 6 governance invariants carry forward and must be extended by 9.8.

**High-risk areas flagged for every 9.x CC (per established pattern + Phase 7 scope):**
- Integration with now-complete Phase 6 surfaces (highest risk for 9.0b auth/rate limiting middleware, 9.4 web UI, 9.5 acceptance tests).
- Vigil actor overlaps with delivered 8.0b Canonical/Entity stores and 8.1 DI container.
- Canonical pipeline must not duplicate or bypass existing 8.0b/8.4 review/approve paths.
- Auth system must integrate cleanly with existing AutonomyGate without creating bypasses.
- Rate limiting must respect budget system and not starve Beast cadence / MCP / chat.
- All new modules must pass the full extended gate suite (layering, no-network, model-name, hardcode scans, etc.).
- Rule #10 on every chunk against the now-very-substantial delivered surface + actor codebase.

**Status:** Phase 7 spec successfully imported as Rev1.0. PHASE2_IMPORT_NOTES.md extended with this record. Repository at HEAD ae0fb96 (Phase 6 complete). Ready for mandatory full 6-step pre-CHUNK-9.0a Continuity Check (with extreme Rule #10 emphasis on complete Phase 6 surfaces + Phase 5 actor layer + all prior reconciliations) **before any src/ or tests/ edits**.

---

