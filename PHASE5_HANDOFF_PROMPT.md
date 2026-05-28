# PHASE 5 HANDOFF PROMPT — Fresh Session Resume Instructions

**You are resuming the AIP (AI Poiesis v0.1) project at `/home/moses/aip`.**

**This is the authoritative single source of truth for this session (overrides all prior context, summaries, or memory):**

1. **Primary SSOT:** `specs/AIP_0_1_Phase1_BuildSpec_Rev1.3.docx` (and its .md export) remains the root architectural authority for terminology, section references (§1.8, §4.1, §7.2, §9.1, Appendix E, etc.), process rules, and model_gen_assumption requirements.
2. **Phase 5 SSOT:** `specs/AIP_0_1_Phase5_BuildSpec_Rev1.0.md` (imported copy of `/home/moses/Downloads/AIP_0_1_Phase5_BuildSpec.md`, 86KB, 1331 lines, Spec Rev 1.0). This is the binding document for all CHUNK-7.x work. Architecture Rev 5.2 (in Downloads) is the supporting reference.
3. **Import Notes (authoritative policy):** `specs/PHASE2_IMPORT_NOTES.md` (must be re-read in full at session start and extended with a new Phase 5 record).

**Authoritative current state (as of this handoff):**
- Architectural Phase 4 (CHUNK-6.0a through CHUNK-6.6) is **complete and green**.
- Final commit: f2bb46c (uv.lock maintenance) after 8cbcb0c (CHUNK-6.6 gate green).
- WORKLOG.md ends with explicit "**Phase 4 complete.**" after the full pre-6.6 CC + 6.6 completion record.
- Tree is clean and fully pushed to GitHub.
- Permanent +2 offset policy remains in force: **All Phase 5 work must be numbered exclusively as the CHUNK-7.x series** (7.0a, 7.0b, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7). Never use bare "Phase 5" or original spec chunk numbers.
- The only documented partial from prior phases that carries forward is the 5.8 test fidelity gap (non-blocking per every CC since the post-Phase-3 Clean Bill).

---

## Mandatory First Actions (Execute in Strict Order — No Production Code Until Complete)

1. **Read the full post-Phase-4 completion section at the end of WORKLOG.md** (the CHUNK-6.6 completion record + the statement "Phase 4 complete."). Treat the Clean Bill of Health, all reconciliations, and the "Phase 4 complete" declaration as the new authoritative baseline. Cross-check that the tree at the current HEAD matches the documented state.

2. **Read `specs/PHASE2_IMPORT_NOTES.md` in full**, with special attention to:
   - The permanent +2 offset policy and CHUNK remapping history.
   - All Process Rules (especially Rule 10 / Repo overlap reconciliation).
   - The Phase 4 import record (§9).
   - Any warnings about existing partial implementations from repo 2.x / 3.x.

3. **Verify the Phase 5 spec is in place** at `specs/AIP_0_1_Phase5_BuildSpec_Rev1.0.md`. If it is not, copy it from `/home/moses/Downloads/AIP_0_1_Phase5_BuildSpec.md` following the exact prior import pattern, commit the import, and append a new "Phase 5 Import Record" section to `specs/PHASE2_IMPORT_NOTES.md`.

4. **Perform a full 6-step Continuity Check for the first Phase 5 chunk (CHUNK-7.0a: Schema Additions + Protocol Amendments + Config Extensions)** before writing **any** production code. This CC must explicitly cover:
   - Re-read of the CHUNK-7.0a prose + complete ANNEX in the Phase 5 SSOT.
   - Re-read of all DEPENDS-ON items (Phase 4 6.0a/6.6, Phase 3 5.0a/5.0b/5.7/5.8/5.9, Phase 2 4.0a/4.5, Phase 1 1.0a/1.0b, Phase 0 relevant tables).
   - Cross-check against the just-completed post-Phase-4 Clean Bill and "Phase 4 complete" declaration.
   - Full **Rule #10 / Repo overlap reconciliation** against all historical code — with special scrutiny on the pre-existing partials in `src/aip/orchestration/sexton/`, `src/aip/orchestration/budget.py`, `src/aip/orchestration/session.py`, and any other files that touch budget, actors, or routing. Document every overlap with "extend existing rather than replace" decisions.
   - Architecture Rev 5.2 cross-references (especially §1.8, §4.3, §6, §7.1, §8.1, §16.1, Appendix D, Appendix E, and the three-layer actor model in §3).
   - State + governance verification (run the full battery: `test_layering.py`, `test_phase2_no_network.py`, all schema tests including the new Phase 5 schema test once created, phase3_network_gate / test_phase4_gate equivalents, hardcode violation scans, file audits, git blame on target files).
   - Append the entire CC record (structured exactly like prior pre-6.x CCs) to WORKLOG.md **before any src/ or tests/ edits**.

Only after the pre-7.0a CC is fully documented in WORKLOG.md and pushed may you proceed to implementation.

---

## Standing Rules for This Entire Phase 5 Session (Non-Negotiable)

- **Full 6-step Continuity Check before every new chunk** (7.0b, 7.1, 7.2, etc.). No exceptions. Document append-only in WORKLOG.
- **Append-only / amend-by-addition discipline** on `foundation/schemas.py` and `foundation/protocols.py` (Phase 5 block appended after the Phase 4 block; no edits to prior phases). New Protocols (e.g. BudgetStore) may be added as new class definitions per the ANNEX.
- **WORKLOG.md is the sole living document.** Every CC and every chunk completion produces a detailed append-only record (scope fidelity statement, pre-CC reconciliations, exact gate command + full `-xvs` output, files changed, Rule #10 notes, rules followed). Never edit prior entries.
- **Push after every completed work unit** (CC or chunk completion) with gate results in the commit message. Tree must be left clean.
- **Strict determinism and scope fidelity:** Only implement what the current chunk's prose + ANNEX explicitly describe in the Phase 5 SSOT. No gold-plating, no "nice to have" extensions, no choices. When the spec says "extend existing", do exactly that.
- **Linearized DAG order:** Follow the order in the Phase 5 spec (7.0a → 7.0b → 7.1 → 7.2 → 7.3 → 7.4 → 7.5 → 7.6 → 7.7). Do not skip or reorder.
- **All Phase 5 work uses CHUNK-7.x numbering** exclusively. Use qualified terminology at all times ("Architectural Phase 5", "CHUNK-7.1", "repo 3.x partial").
- **Rule #10 on every chunk:** Before touching any file, audit historical code (git blame/log + tree + prior WORKLOG entries) for overlaps. Prefer "extend existing rather than replace". Document the reconciliation explicitly.
- **Import boundaries (§7.2) and actor rules:** Sexton, Beast, and the Adaptive Router are orchestration-layer components. They import foundation Protocols/schemas and `adapter.model_slot_resolver`. They **never** import concrete adapter storage implementations directly (use Protocol injection only). The layering gate must remain green.
- **No hardcoded model names (§4.1).** Sexton uses the `sexton` model slot. All other model references go through named slots.
- **Model-gen-assumption tagging (§1.8):** Mandatory on every new AcePlaybookEntry, FailureClassification, and any ContractRule that Sexton audits. Sexton must audit stale assumptions on slot changes.
- **Sexton classification ≠ resolution (Appendix D):** Sexton classifies and curates rules/playbook entries. It never mutates live workflow state mid-session. Playbook entries take effect at next session start.
- **Continuous execution mode (your new directive):** Once the initial pre-7.0a CC is complete and documented, **code continuously through the entire linearized Phase 5 order without waiting for further "go" signals**. Complete each chunk (implementation + exact gate + WORKLOG append + push), then immediately begin the next pre-chunk CC + implementation. Only stop and ask for user input when you encounter:
  - Genuine ambiguity in the spec that cannot be resolved by re-reading the prose + ANNEX + Architecture Rev 5.2.
  - A blocker that Rule #10 reconciliation cannot resolve without changing the spec.
  - A situation where the spec's assumptions about existing code are materially violated in a way that requires DEFINER decision.
  - Any case where proceeding would knowingly violate a Process Rule or produce a red gate that cannot be made green through faithful extension.

**Never offer lists of options or ask "what next?"** Report progress at natural milestones (after each chunk completion + push) using a short status + "Continuing to next chunk per linearized order" (or "Paused for user input: [precise reason]").

---

## Known High-Risk Areas That Will Confuse Coding or Break Operation (Mandatory Awareness)

**1. Pre-existing partial implementations from repo 3.x (highest risk of Rule #10 violations):**
- `src/aip/orchestration/sexton/` directory already exists with some files. The Phase 5 spec defines `orchestration/actors/sexton.py` (and `sexton_audit.py`). You must audit every file in the existing `sexton/` tree against the CHUNK-7.1 and 7.3 prose/ANNEX before writing anything. Extend the real delivered files; do not create parallel wrong-path implementations.
- `src/aip/orchestration/budget.py` (1970 lines) already exists. CHUNK-7.0b and 7.0a define `BudgetManager`, `BudgetStore` Protocol, and enforcement hooks. This is a major overlap point. The pre-7.0a CC and every subsequent budget-related CC must contain a detailed reconciliation of the existing budget.py against the spec's BudgetStore + BudgetConfig + engine integration requirements.
- `src/aip/orchestration/session.py` already has SessionManager (from Phase 3/5.7). Budget tracking must integrate here without breaking existing multi-turn behavior.

**2. Placement and import pattern confusion:**
- The spec uses `orchestration/actors/sexton.py`, `orchestration/actors/beast.py`, `orchestration/router.py`, `orchestration/ace_playbook.py`, `orchestration/budget.py`. The repo already has a `sexton/` subdirectory and a top-level `budget.py`. Reconcile paths vs. spec expectations via Rule #10 on first touch of each area. Prefer the spec's intended locations when extending, but never break existing imports/callers.
- Actor injection: All storage access must be via Protocol instances passed in. No direct `from aip.adapter.vector...` imports inside actors.

**3. What will break during operation if continuity is not enforced:**
- Budget enforcement hooks into the YAML workflow engine (`orchestration/engine.py` from CHUNK-4.5) and session lifecycle. Incorrect integration will either allow token overruns or break existing workflows.
- ACE Playbook loading at session start (must hook into `SessionManager` / engine startup path without changing Phase 3/4 behavior for existing callers).
- Router wrapping of `ModelSlotResolver` (7.4): Must be transparent — existing resolver callers (synthesis, evaluation nodes) must continue to work unchanged when no router is present.
- Sexton reading `trace_events` + writing classifications back (must not break the Phase 3/5.x L4 trajectory signals or the 6.2 L3a evaluation records).
- Beast re-indexing via `VectorStore` (uses `count()` and `batch_upsert` from Phase 4) + factory (6.3). Must work on both sqlite_vss and pgvector backends.
- `model_gen_assumption` propagation: Every new Phase 5 type that touches model limitations must carry it; Sexton audits must actually read and act on existing ones from Phase 1–4 ContractRules.

**4. Spec shorthand vs. reality (historical pattern):**
- Watch for path references in the Phase 5 spec that may not match delivered file locations (e.g. "orchestration/actors/..." vs existing `sexton/` dir). Always reconcile to actual files using Rule #10.
- Some Phase 5 concepts (routing_outcomes table, trace_events schema, ace_playbook.db) have partial Phase 0/3 roots — verify exact column expectations against the spec ANNEX before assuming.

**5. Governance gates that must stay green on every CC:**
- `tests/test_layering.py`
- `tests/test_phase2_no_network.py` (and its Phase 3/4/6.6 descendants)
- All schema tests (now including the new `tests/test_phase5_schema_additions.py` once created in 7.0a)
- Hardcode model name scans across `src/aip/`
- The Phase 4 final gate (`test_phase4_gate.py`) must remain green (7.7 will extend it)

**6. Environment / determinism notes:**
- Real Postgres/pgvector is usually unavailable → all new pgvector-dependent tests (Beast, etc.) must follow the PGVECTOR_AVAILABLE guard + mock/CI-mode pattern established in 6.0b–6.5.
- All model calls in new actors must go through `ModelSlotResolver` + `ci_mode` fixtures for deterministic gates.

---

## Execution Directive for This Session

After completing the four mandatory first actions (especially the full pre-7.0a CC documented in WORKLOG), **proceed continuously and autonomously through the entire Phase 5 linearized order**:

CHUNK-7.0a (schema/protocol/config) → CHUNK-7.0b (budget system) → CHUNK-7.1 (Sexton classification) → CHUNK-7.2 (ACE Playbook) → CHUNK-7.3 (stale rule audit) → CHUNK-7.4 (Adaptive router) → CHUNK-7.5 (Beast actor) → CHUNK-7.6 (integration test) → CHUNK-7.7 (final gate).

For each chunk:
- Perform the mandatory pre-chunk 6-step CC (with heavy emphasis on Rule #10 overlaps against the growing set of prior Phase 5 code + all historical repo 0.x–6.x).
- Implement **exactly** the prose + ANNEX for that chunk only.
- Create/update tests per the ANNEX.
- Run the exact gate command specified in the spec.
- Capture full output.
- Append detailed completion record to WORKLOG.md.
- Commit + push.
- Immediately begin the next pre-chunk CC.

Report status after each push with: "CHUNK-7.X complete (gate green + pushed at <hash>). Continuing to next per linearized order."

Only pause and surface a precise question to the user when one of the "stop conditions" listed in the Continuous execution mode section above is met.

**The spec is the law. Continuity with the existing delivered codebase (including all its partials and quirks) is mandatory. Do not deviate.**

This handoff prompt + the three documents listed at the top (Phase 1 SSOT, Phase 5 SSOT, PHASE2_IMPORT_NOTES) are now your complete operating instructions for the entire Phase 5 effort.

Begin.