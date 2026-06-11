# CHUNK_1_5_TRIAGE.md

**Frozen at:** commit `efeb887c799aa5cefa1add1f011b7b8cf99bd83b`
**Date:** 2026-06-10
**Hardening Cycle:** Chunk 1.5 — Critical Triage and Next-Chunk Handoff

This document is the output of the Chunk 1.5 triage pass. It verifies whether the two flagged
governance risks (DDR-008, CDR-010) are real, reachable, and urgent, and produces a clean
handoff brief for Chunk 2.

---

## PRE-EXECUTION TRIAGE MAP

### 1. MCP Dispatch / Approval Path

#### Relevant Files

| File | Role |
|---|---|
| `src/aip/adapter/mcp/server.py` | AipMcpServer — tool dispatch with autonomy gate enforcement |
| `src/aip/adapter/mcp/tools/artifacts.py` | `aip_artifact_approve()` — real ECS transition + canonical write |
| `src/aip/adapter/mcp/tools/search.py` | `aip_search()` — real hybrid search via Protocols |
| `src/aip/adapter/autonomy/autonomy_gate.py` | AutonomyGateImpl — SQLite-backed DEFINER sovereignty enforcement |
| `src/aip/adapter/api/dependencies.py` | AipContainer — DI container (autonomy_gate: AutonomyGate \| None) |
| `src/aip/adapter/api/app.py` | Application wiring — NO MCP references |

#### Whether `aip_artifact_approve` Is Reachable

**NOT reachable at runtime.** The MCP server is not wired into `app.py`, `start.sh`, or any startup
script. `AipMcpServer.start()` only sets `self._running = True` with no transport binding. The
server is only exercisable via direct `call_tool()` invocation from tests or embedded use.

#### Whether It Mutates Artifact State or Only Returns a Fake Response

**It performs REAL mutations.** The current implementation (post commit `0d63e58`) does:
1. `container.ecs_store.transition(artifact_id, from_state="REVIEWED", to_state="APPROVED", ...)` — real ECS state transition
2. `container.canonical_store.write_canonical(artifact_id, {...}, approved_by="definer")` — real canonical write

This is NOT the hardcoded `{"approved": True, "canonical": True}` that the Chunk 1 docs describe.
The Chunk 1 docs are **stale** — they reflect the pre-`0d63e58` state.

#### Whether Anything Consumes the Response as Real Approval

**No production code consumes MCP tool responses.** The only imports from `aip.adapter.mcp` are in
test files: `test_mcp_server.py`, `test_adapter_promotion_integration.py`,
`test_surfaces_round_trip_integration.py`, `test_multi_surface_isolation.py`.

#### Latent Vulnerability: `autonomy_gate=None` Escape Hatch

In `server.py` line 213: `if level in ("write", "admin") and self.container.autonomy_gate:` —
if `container.autonomy_gate` is `None`, the condition short-circuits to `False` and **gate
enforcement is completely skipped**. The tool dispatches directly without any gate check.

In production wiring (`app.py:185`), `autonomy_gate` IS set. But if the MCP server is ever wired
separately without explicit gate provision, this escape hatch becomes a governance bypass. This
should be hardened: the gate check should fail-closed (reject when gate is None for admin tools)
rather than fail-open.

---

### 2. `_AlwaysApproveDialogNode` Path

#### Relevant Files

| File | Role |
|---|---|
| `src/aip/orchestration/workflow/workflow_01.py` | Workflow 0.1 runner — contains replacement `_ReviewGateNode` |
| `src/aip/orchestration/nodes/definer_gate.py` | DEFINER gate implementation (AUTO_APPROVE_STUB / MANUAL modes) |
| `src/aip/orchestration/nodes/commit.py` | Commit node — requires explicit DefinerDecision |
| `examples/run_workflow_01.py` | Example script — manual invocation only |
| `tests/test_workflow_engine.py` | Workflow engine tests |

#### Whether `_AlwaysApproveDialogNode` Exists

**No.** It was removed in commit `f66e00b` (2026-05-29, "fix: replace Workflow 0.1 scaffold with
real pause/resume review workflow"). The only surviving reference is a historical comment on
line 182: `# Review Gate Node (replaces _AlwaysApproveDialogNode)`.

#### Whether It Is Test-Only, Dev-Only, or Reachable in Runtime

**Not reachable at runtime.** The class does not exist. Its replacement (`_ReviewGateNode`) exists
in `workflow_01.py`, but `workflow_01` itself is not reachable at runtime:
- Not wired in `app.py` (no API route triggers it)
- Not wired via CLI (no command references it)
- Config sets `default_template = "workflow_01"` in all three TOML files, but **no Python code
  reads `default_template`** — it is a dead config value
- Only reachable via `examples/run_workflow_01.py` (manual) or tests

#### Whether the Replacement Can Bypass DEFINER Approval

**Partially.** The replacement `_ReviewGateNode` defaults to `AUTO_APPROVE_STUB` mode when no
`gate_mode` is specified (lines 262 and 518 of `workflow_01.py`). In this mode:

- Validation failure → returns "revise" (blocked)
- Eval failure → returns "reject" (blocked)
- CI fixture eval in production → returns "revise" (blocked)
- Validation + eval both pass with real data → **auto-approves** with `approved_by="stub:auto_approve"` and a logged WARNING

This means: **if the workflow were invoked in production with a real model_resolver producing
passing eval results, artifacts would be auto-approved without a human DEFINER's review.** This
is a governance gap, but it is gated behind the workflow being unreachable at runtime.

#### Whether It Is Documented or Guarded

- The replacement IS documented in the `workflow_01.py` module docstring and inline comments.
- The `AUTO_APPROVE_STUB` behavior IS documented in `definer_gate.py` docstrings.
- The default mode choice is NOT config-driven — there is no way to set `gate_mode` from TOML.
- The stale `implementation_status.md` still says "DANGEROUS: `_AlwaysApproveDialogNode` always
  approves" — this needs updating.
- The Chunk 1 CODE_DEBT_REGISTER still lists CDR-010 as CRITICAL — this needs updating.

---

### 3. Top 10 Items to Drive Chunk 2

After reviewing all four Chunk 1 hardening docs, these are the items that should drive Chunk 2
execution, ordered by priority:

| # | Item ID | Description | Chunk 2 Action | Rationale |
|---|---|---|---|---|
| 1 | DDR-008 | MCP `aip_artifact_approve` — docs say "hardcoded True" but code does real mutations | Update hardening docs to reflect current state; add fail-closed gate check | Stale docs are a truth gap; the `autonomy_gate=None` escape hatch must be hardened before MCP is wired |
| 2 | CDR-010 | `_AlwaysApproveDialogNode` — no longer exists, replaced by `_ReviewGateNode` | Update CODE_DEBT_REGISTER to RESOLVED; update implementation_status.md; address AUTO_APPROVE_STUB default | Docs are stale; AUTO_APPROVE_STUB default should be config-driven or changed to MANUAL for production |
| 3 | DEBT-006 | New Sexton not wired — 2,716 turns unembedded, no auto-tagging/graph/wiki | Wire `actors/sexton.py` into `app.py` replacing old Sexton | Highest-priority active debt per all three registers |
| 4 | CDR-003 | 21 `except Exception: pass` — silent degradation | Replace with logging or explicit state reporting per AIP-G-02 | Direct violation of No Silent Degradation rule |
| 5 | CDR-013 | health.py embedding status always "healthy" — fake success | Implement real embedding health check | AIP-G-02 violation, directly observable in production |
| 6 | DDR-001/002 | README says vector embeddings + knowledge graph "Not built" | Update README to reflect working status | High-impact doc truth gap (first doc anyone reads) |
| 7 | CDR-011 | ECS state lost on process restart (guardrailed store) | Verify PersistentEcsStore is primary; document or fix | If wrong store is used, state is lost on restart |
| 8 | CDR-012 | Old Sexton event write signature mismatch | Fix event write call signatures | Latent runtime crash when EventStore is fully functional |
| 9 | DDR-010 | Split TECH_DEBT.md (root vs docs/) | Merge or delete root TECH_DEBT.md | Confusing structure, violates documentation truth |
| 10 | CDR-009 | CLI AutonomyGate TODOs — CLI writes bypass gate | Wire CLI write paths through AutonomyGate | Governance gap (CLI surface is unguarded) |

#### Duplicates Identified

| Duplicate | Items | Resolution |
|---|---|---|
| DEBT-003 and DDR-008 | Both describe MCP scaffold/fake success | Merge: DDR-008 is the doc discrepancy, DEBT-003 is the code debt. They are the same issue. Update both to reflect that the code is no longer scaffold but a real mutating path that is unreachable. |
| DEBT-007 and CDR-007 | Both describe admin.py blocking sqlite3 | Same issue. DEBT-007 (from TECH_DEBT.md) and CDR-007 (from grep scan) are identical. Keep DEBT-007 as canonical, note CDR-007 as duplicate. |
| CDR-014 and CURRENT_STATE_BASELINE section 8 | Both describe adaptive router scaffold | Same issue. STATUS.md already discloses this honestly. |

#### Misprioritized Items

| Item | Current Priority | Recommended Priority | Reason |
|---|---|---|---|
| CDR-010 (`_AlwaysApproveDialogNode`) | CRITICAL | **RESOLVED** | The class no longer exists. The residual risk (AUTO_APPROVE_STUB default) is real but is a separate, lower-priority item. |
| DDR-008 (MCP fake success) | CRITICAL | **Non-live debt** (HIGH if MCP is ever wired) | Not reachable at runtime. The stale docs are HIGH priority to fix, but the code risk is non-live. |

#### Items to Defer from Chunk 2

| Item ID | Description | Reason for Deferral |
|---|---|---|
| CDR-001 | 300+ Sprint-number comments | High volume cosmetic cleanup. Do incrementally when touching files, not as a dedicated pass. |
| CDR-002 | 22 Step-number comments | Low volume cosmetic cleanup. Can be combined with Sprint cleanup. |
| CDR-004 | ~170 broad `except Exception:` | Important but very high volume. Requires careful per-instance analysis. Spread across multiple chunks. |
| CDR-005 | `return []` hiding failures | Medium volume, needs per-instance judgment. Not Chunk 2 priority. |
| DDR-004 | Build spec architecture revision 5.2 | Historical docs. Low impact. |
| DDR-005 | Dual step numbering in DOGFOOD_READY.md | Cosmetic. Low impact. |
| DDR-009 | Ingest command confusion | Cosmetic. Low impact. |
| DDR-011 | ADR count in README | Low impact. |
| DEBT-001 | Graph node alias cleanup | Minimal blast radius. Deferred per TECH_DEBT.md. |
| DEBT-002 | PPR expansion in chat | Latency constraint. Deferred per TECH_DEBT.md. |

---

## DDR-008 Verdict

| Field | Value |
|---|---|
| **Classification** | **Non-live debt** (HIGH if MCP is ever wired) |
| **Reachable path** | No. MCP server is not wired in `app.py` or any startup script. No transport binding. Only exercisable via direct `call_tool()` invocation in tests. |
| **Doc accuracy** | STALE. The Chunk 1 docs (DDR-008, DEBT-003, CURRENT_STATE_BASELINE section 8) describe the pre-`0d63e58` state where `aip_artifact_approve` returned hardcoded `{"approved": True, "canonical": True}`. The current code performs real ECS transitions and canonical writes. This actually makes the risk MORE serious if the MCP server is ever wired, but it is not wired today. |
| **Latent vulnerability** | The `autonomy_gate=None` escape hatch in `server.py:213` silently bypasses gate enforcement. When the MCP server IS eventually wired, this must be fail-closed. |
| **Recommended action** | (1) Update all hardening docs to reflect current state: MCP dispatch is real (not scaffold), but unreachable. (2) Harden `server.py` line 213 to fail-closed when `autonomy_gate is None` for write/admin tools (reject rather than allow). (3) Add a test proving admin tools are rejected when gate is None. (4) Update DDR-008 severity from CRITICAL to HIGH (non-live). |

---

## CDR-010 Verdict

| Field | Value |
|---|---|
| **Classification** | **Non-live debt — partially resolved** |
| **Reachable path** | No. `_AlwaysApproveDialogNode` was removed in commit `f66e00b`. Its replacement (`_ReviewGateNode`) exists in `workflow_01.py`, but `workflow_01` is not wired into `app.py`, CLI, or any runtime path. Only exercisable via `examples/run_workflow_01.py` (manual) or tests. |
| **Doc accuracy** | STALE. `implementation_status.md:97` still says "DANGEROUS: `_AlwaysApproveDialogNode` always approves". `CODE_DEBT_REGISTER` still lists CDR-010 as CRITICAL. Both need updating. |
| **Residual risk** | `_ReviewGateNode` defaults to `AUTO_APPROVE_STUB` mode, which auto-approves when validation + eval both pass with real (non-fixture) data. This could bypass DEFINER review if the workflow is ever wired into production without changing the default to `MANUAL`. The risk is real but gated behind the workflow being unreachable. |
| **Recommended action** | (1) Update CODE_DEBT_REGISTER: mark CDR-010 as RESOLVED (replaced by `_ReviewGateNode`). (2) Create new debt item for AUTO_APPROVE_STUB default risk. (3) Update `implementation_status.md:97`. (4) Change `_ReviewGateNode` default to `MANUAL` or make it config-driven from TOML `[workflow]` section. (5) Either consume `default_template` config or remove the dead config value. |

---

## Chunk 2 Execution Order

### Phase 1: Doc Truth Reconciliation (must come first — sets accurate baseline)

1. **Update DDR-008** in `DOC_DISCREPANCY_REGISTER.md`: change description from "hardcoded True" to
   "real mutations, unreachable at runtime, autonomy_gate=None escape hatch"
2. **Update DEBT-003** in `docs/TECH_DEBT.md`: same update — MCP dispatch is no longer scaffold
3. **Update CURRENT_STATE_BASELINE.md** section 8: reflect real MCP dispatch state
4. **Mark CDR-010 as RESOLVED** in `CODE_DEBT_REGISTER.md`; create new item for AUTO_APPROVE_STUB default risk
5. **Update `implementation_status.md:97`**: remove "DANGEROUS: `_AlwaysApproveDialogNode`" reference
6. **Update README.md**: fix DDR-001 (vector embeddings), DDR-002 (knowledge graph), DDR-011 (ADR count)
7. **Merge or delete root `TECH_DEBT.md`** (DDR-010)

### Phase 2: Governance Hardening (code fixes)

8. **Harden `server.py:213`**: change `autonomy_gate=None` behavior from fail-open to fail-closed
9. **Add test**: admin tools rejected when `autonomy_gate is None`
10. **Change `_ReviewGateNode` default** from `AUTO_APPROVE_STUB` to `MANUAL` or make it config-driven
11. **Remove or consume dead config** `default_template` in TOML files

### Phase 3: Critical Debt Resolution

12. **Wire new Sexton** (DEBT-006) into `app.py`
13. **Fix old Sexton event write signatures** (CDR-012)
14. **Fix health.py embedding status** (CDR-013)

### Phase 4: Silent Degradation Cleanup

15. **Replace top `except Exception: pass`** instances (CDR-003) — focus on app.py, dependencies.py, and adapter stores first
16. **Wire CLI AutonomyGate** (CDR-009) for admin-level writes

---

## Chunk 2 Must-Fix Docs List

| Doc File | What to Fix |
|---|---|
| `docs/hardening/DOC_DISCREPANCY_REGISTER.md` | DDR-008: update from "hardcoded True" to real mutations; DDR-010: merge split; general cleanup |
| `docs/hardening/CODE_DEBT_REGISTER.md` | CDR-010: mark RESOLVED; create new AUTO_APPROVE_STUB item; DEBT-003: update description |
| `docs/hardening/CURRENT_STATE_BASELINE.md` | Section 8: update MCP scaffolding status |
| `docs/hardening/AI_FINGERPRINT_REGISTER.md` | No changes needed (accurate as-is) |
| `docs/TECH_DEBT.md` | DEBT-003: update from scaffold to real dispatch; DEBT-006: mark as in-progress after wiring |
| `docs/implementation_status.md` | Line 97: remove DANGEROUS reference; update MCP status |
| `README.md` | DDR-001: vector embeddings working; DDR-002: knowledge graph working; DDR-011: ADR count |
| `STATUS.md` | DDR-007: add failing test names; DDR-006: clarify scaffolding percentage scope |
| Root `TECH_DEBT.md` | Merge into `docs/TECH_DEBT.md` or delete (DDR-010) |

---

## Chunk 2 Out-of-Scope List

| Item | Reason |
|---|---|
| Sprint-number comment removal (CDR-001) | Incremental only — do when touching files, not dedicated pass |
| Step-number comment cleanup (CDR-002) | Low volume, cosmetic |
| Broad `except Exception:` narrowing (CDR-004) | Too high volume for one chunk; spread across chunks |
| `return []` failure hiding (CDR-005) | Needs per-instance judgment; defer |
| Build spec architecture revision updates (DDR-004) | Historical docs; low priority |
| DOGFOOD_READY.md dual step numbering (DDR-005) | Cosmetic |
| Ingest command confusion (DDR-009) | Cosmetic |
| Graph node alias cleanup (DEBT-001) | Minimal blast radius; deferred |
| PPR expansion in chat (DEBT-002) | Latency constraint; deferred |
| Layer violation refactoring (CDR-006) | Architectural change requiring design; defer to dedicated chunk |
| Adaptive router implementation (CDR-014) | Honestly disclosed scaffold; not governance-critical |
| InMemoryBudgetStore fix (CDR-015) | Medium priority; production uses SQLite store |
| Vigil re-evaluation wiring (CDR-016) | Medium priority; not governance-critical |
| CLI session TODOs (CDR-008) | Medium priority; requires SessionManager API |
| admin.py blocking sqlite3 (CDR-007 / DEBT-007) | Accepted for now; convert when admin routes are next modified |

---

## Emergency Code Changes Made

**No emergency code changes were made.** Neither DDR-008 nor CDR-010 represents a runtime-critical
bypass today:

- **DDR-008**: MCP server is not reachable at runtime. The `aip_artifact_approve` function performs
  real mutations but cannot be invoked by any external client. The `autonomy_gate=None` escape
  hatch is a latent vulnerability that should be hardened in Chunk 2, but it is not an emergency
  because the MCP server is not wired.

- **CDR-010**: `_AlwaysApproveDialogNode` no longer exists. Its replacement defaults to
  `AUTO_APPROVE_STUB` which could auto-approve in production, but `workflow_01` is not reachable
  at runtime. The AUTO_APPROVE_STUB default should be changed in Chunk 2, but it is not an
  emergency.

**No runtime-critical bypass was confirmed.**
