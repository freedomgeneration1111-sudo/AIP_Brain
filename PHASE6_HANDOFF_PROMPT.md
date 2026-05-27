# PHASE 6 HANDOFF PROMPT — Fresh Session Resume Instructions

**You are resuming the AIP (AI Poiesis v0.1) project at `/home/moses/aip`.**

**This is the authoritative single source of truth for this session (overrides all prior context, summaries, or memory):**

1. **Primary SSOT:** `specs/AIP_0_1_Phase1_BuildSpec_Rev1.3.docx` (and its .md export) remains the root architectural authority for terminology, section references (§1.8, §4.1, §7.2, §9.1, Appendix E, etc.), process rules, and model_gen_assumption requirements.
2. **Phase 5 SSOT (Completed Baseline):** `specs/AIP_0_1_Phase5_BuildSpec_Rev1.0.md` is now the authoritative record of what was delivered in Architectural Phase 5 (CHUNK-7.0a through 7.7). All future Phase 6 work must treat the delivered Phase 5 actor layer (Sexton, Beast, Adaptive Router, ACE Playbook, BudgetManager, etc.) as the new stable foundation.
3. **Import Notes (authoritative policy):** `specs/PHASE2_IMPORT_NOTES.md` (must be re-read in full at session start and extended with a new Phase 6 record).

**Authoritative current state (as of this handoff):**
- Architectural Phase 5 (CHUNK-7.0a through CHUNK-7.7) is **complete and green**.
- Final commit: 2a6aef2 (CHUNK-7.7 final gate + Phase 5 completion declaration).
- WORKLOG.md contains the full detailed history of Phase 5 (all pre-chunk CCs, implementations, gates, and the explicit "Phase 5 complete" declaration).
- Tree is clean and fully pushed to GitHub (HEAD 2a6aef2).
- Permanent +2 offset policy remains in force: **All Phase 6 work must be numbered exclusively as the CHUNK-8.x series** (8.0a, 8.0b, 8.1, ...). Never use bare "Phase 6" or original spec chunk numbers without the +2 remapping.
- Phase 5 delivered a complete autonomous actor layer. Phase 6 is expected to build the human-facing and integration surfaces on top of it.

---

## Mandatory First Actions (Execute in Strict Order — No Production Code Until Complete)

1. **Read the full post-Phase-5 completion section at the end of WORKLOG.md** (the CHUNK-7.7 completion record + the statement that "Architectural Phase 5 is now complete."). Treat this as the new authoritative baseline. Cross-check that the tree at the current HEAD matches the documented state.

2. **Read `specs/PHASE2_IMPORT_NOTES.md` in full**, with special attention to:
   - The permanent +2 offset policy and CHUNK remapping history (Phase 6 architectural work → CHUNK-8.x).
   - All Process Rules (especially Rule 10 / Repo overlap reconciliation).
   - The Phase 5 import/completion record.
   - Warnings about existing partials and the need to extend rather than replace.

3. **Verify whether a Phase 6 spec exists** in `specs/`. If a Phase 6 BuildSpec has been imported (or is present in `/home/moses/Downloads/`), follow the exact prior import pattern, commit it, and append a "Phase 6 Import Record" section to `specs/PHASE2_IMPORT_NOTES.md`. If no spec yet exists, note this clearly in the first pre-8.0a CC.

4. **Perform a full 6-step Continuity Check for the first Phase 6 chunk (CHUNK-8.0a)** before writing **any** production code. This CC must explicitly cover:
   - Re-read of the target CHUNK-8.0a prose + complete ANNEX (once a Phase 6 spec is available).
   - Re-read of all DEPENDS-ON items (the entire delivered Phase 5 actor layer + all Phase 4/3/2/1/0 foundations).
   - Cross-check against the post-Phase-5 Clean Bill and "Phase 5 complete" declaration.
   - Full **Rule #10 / Repo overlap reconciliation** against the now-substantial Phase 5 delivered code (especially the new `orchestration/actors/`, `orchestration/router.py`, `orchestration/ace_playbook.py`, `orchestration/sexton/`, budget system, etc.).
   - Architecture Rev 5.2 cross-references (especially any sections describing the UI/MCP/CLI layer and its relationship to the actor layer in §3 / §16 / §22).
   - State + governance verification (run the full battery: `test_layering.py`, `test_phase2_no_network.py` and all descendants, all schema tests, hardcode violation scans, file audits, git blame on target files).
   - Append the entire CC record (structured exactly like the pre-7.x CCs) to WORKLOG.md **before any src/ or tests/ edits**.

Only after the pre-8.0a CC is fully documented in WORKLOG.md and pushed may you proceed to implementation.

---

## Standing Rules for This Entire Phase 6 Session (Non-Negotiable)

- **Full 6-step Continuity Check before every new chunk** (8.0b, 8.1, etc.). No exceptions. Document append-only in WORKLOG.
- **Append-only / amend-by-addition discipline** on foundation files and existing Phase 5 modules.
- **WORKLOG.md is the sole living document.** Every CC and every chunk completion produces a detailed append-only record.
- **Push after every completed work unit** with gate results in the commit message. Tree must be left clean.
- **Strict determinism and scope fidelity:** Only implement what the current chunk's prose + ANNEX explicitly describe.
- **Linearized DAG order:** Follow the order defined in the Phase 6 spec (once available).
- **All Phase 6 work uses CHUNK-8.x numbering** exclusively. Use qualified terminology at all times ("Architectural Phase 6", "CHUNK-8.1", "post-Phase-5 baseline").
- **Rule #10 on every chunk:** Before touching any file, audit historical code (including the now-complete Phase 5 delivered surface) for overlaps. Prefer "extend existing rather than replace".
- **Import boundaries (§7.2) remain strictly in force.** Phase 6 (MCP, CLI, any UI) may import the orchestration actor layer and foundation Protocols, but must still respect the layering rules.
- **No hardcoded model names (§4.1).** All model references continue to go through named slots.
- **Continuous execution mode:** After the initial pre-8.0a CC is complete and documented, code continuously through the entire linearized Phase 6 order without waiting for further "go" signals. Only stop and ask for user input on the four stop conditions listed in the Phase 5 handoff.

**Never offer lists of options or ask "what next?"** Report progress at natural milestones using the standard short status + "Continuing to next chunk per linearized order."

---

## Known High-Risk Areas for Phase 6 (Mandatory Awareness)

**1. Integration with the new Phase 5 actor layer (highest risk):**
- MCP servers will need to expose and invoke Sexton, Beast, the Adaptive Router, ACE Playbook loading, and budget enforcement.
- CLI commands must surface the new autonomous capabilities without breaking existing deterministic behavior.
- Any future UI must consume the actor layer through clean, Protocol-based interfaces.

**2. Maintaining all previous governance invariants:**
- Every new Phase 6 module must pass the full suite of layering, network-isolation, and model-name gates (the Phase 5 final gate in 7.7 must be extended).
- Determinism in CI must remain absolute.

**3. Spec availability:**
- As of this handoff, no Phase 6 BuildSpec has been imported into `specs/`. The first action of any Phase 6 session will likely be importing and remapping a Phase 6 spec (following the exact pattern used for Phase 5).

**4. Environment / determinism notes:**
- All new MCP/CLI surfaces must be fully testable in CI mode with zero network calls.

---

## Execution Directive for This Session

After completing the four mandatory first actions (especially the full pre-8.0a CC documented in WORKLOG and pushed), **proceed continuously and autonomously through the entire Phase 6 linearized order**.

For each chunk:
- Perform the mandatory pre-chunk 6-step CC (with heavy emphasis on Rule #10 overlaps against the complete Phase 5 delivered codebase).
- Implement exactly the prose + ANNEX for that chunk only.
- Create/update tests per the ANNEX.
- Run the exact gate command specified in the spec.
- Capture full output.
- Append detailed completion record to WORKLOG.md.
- Commit + push.
- Immediately begin the next pre-chunk CC.

Report status after each push with: "CHUNK-8.X complete (gate green + pushed at <hash>). Continuing to next per linearized order."

Only pause and surface a precise question to the user when one of the "stop conditions" listed in the Continuous execution mode section is met.

**The spec (once imported) is the law. Continuity with the now-complete Phase 5 delivered actor layer is mandatory. Do not deviate.**

This handoff prompt + the three documents listed at the top (Phase 1 SSOT, Phase 5 SSOT, PHASE2_IMPORT_NOTES) are now your complete operating instructions for the entire Phase 6 effort.

Begin.