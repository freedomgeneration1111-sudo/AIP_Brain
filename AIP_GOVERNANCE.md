# The AIP Governance Contract

**Version:** 1.0
**Status:** Binding on all AIP components
**Applies to:** aip_brain, aip_loom, aip_codeforge, and any future AIP component
**Conformance:** mechanically checked by `tests/test_governance_conformance.py` (invariants AIP-G-01 through AIP-G-11)

---

## Preamble

AIP (AI Poiesis) is not a collection of tools.  It is one discipline for human-directed, fully-audited synthesis, instantiated so far in three components: a knowledge engine (aip_brain), a longform writing workbench (aip_loom), and a spec-driven code generator (aip_codeforge).  What makes them one platform is not shared code.  It is shared law.  This document is that law.

Every AIP component conforms to the invariants below.  A component that violates one of them is, by definition, not an AIP component, however useful it may otherwise be.  The product is the discipline.  The applications are the evidence that the discipline holds across domains.

This contract is the single source of truth for the invariants.  It is hosted in one canonical location and linked, not copied, by each component, because three copies would drift, and drift is the exact failure this contract exists to prevent (see AIP-G-04).

---

## The core axiom: DEFINER and instrument

AIP rests on one relationship.  The human is the **DEFINER**: the author, the authority, the one who decides.  The AI is the **instrument**: it proposes, drafts, retrieves, and executes, but it does not decide.  Every invariant in this contract is a consequence of taking that relationship seriously enough to enforce it in code, rather than merely asserting it in a README.

Autonomy is something the DEFINER grants, explicitly and revocably.  It is never something the system assumes.  An instrument that quietly decided things would not be a faster DEFINER.  It would be a different and unaccountable author, and AIP refuses that by construction.

---

## The invariants

The keywords MUST, MUST NOT, and SHOULD are used in the normative sense.  Each invariant carries a stable identifier (AIP-G-NN) so that code, docstrings, tests, and review notes can cite the exact clause they enforce or discuss.

### AIP-G-01 — DEFINER Authority

No artifact MUST reach a terminal or approved state without an explicit DEFINER action.  Autonomy MUST be granted explicitly by the DEFINER and MUST remain revocable.  No code path may transition an artifact to APPROVED, COMMITTED, or any equivalent terminal state on its own initiative.

*Why.*  This is the axiom made enforceable.  Everything downstream of it (provenance, lifecycle, auditability) is only meaningful if a human authority sits at the end of every consequential path.

*Conformance.*  An approval surface exists and requires an explicit actor; no auto-approval shortcut appears in source.  The behavioral check (approval refuses a missing or non-DEFINER actor) is wired per component.

### AIP-G-02 — No Fake Success

A component MUST NOT return success for work it did not perform.  Unconfigured, unimplemented, or disabled paths MUST return an honest, distinguishable status (for example NEEDS_CONFIGURATION, NOT_IMPLEMENTED, DISABLED, BACKEND_UNAVAILABLE, BLOCKED_HUMAN) rather than a hollow success.  Scaffold surfaces MUST be disclosed in a status document, not hidden.

*Why.*  Fake success is the most corrosive failure an AIP component can have, because it destroys the trust that the entire provenance-and-audit apparatus is built to earn.  An honestly-disclosed gap is a roadmap item.  A hidden one is a liability that surfaces in front of a user or a buyer at the worst possible moment.

*Conformance.*  Honest-status sentinels are present in source, and scaffold is named in a STATUS-style disclosure.  This contract's own conformance matrix obeys this rule: it does not claim conformance a component does not yet have.

### AIP-G-03 — Provenance and Source-Grounding

Every generated artifact MUST cite the exact sources or evidence from which it derives.  No claim without traceable provenance.  An artifact whose provenance is empty MUST be refused.

*Why.*  An ungrounded synthesis is an opinion wearing the costume of a result.  Provenance is what lets the DEFINER, or an auditor, or a future maintainer, answer "on what basis" for any output the system produces.

*Conformance.*  The artifact schema carries a provenance field (for example codeforge's `source_requirement_ids`, INV-03).  The behavioral check (empty provenance is rejected) is wired per component.

### AIP-G-04 — Governed Lifecycle

Artifacts MUST move through explicit, recorded lifecycle states (for example GENERATED then REVIEWED then APPROVED, or APPROVED_FOR_EXECUTION then COMMITTED).  State transitions MUST be logged.  A terminal state (APPROVED, COMMITTED, or equivalent) MUST NOT be reverted without a DEFINER override recorded as a durable DecisionRecord.

*Why.*  A lifecycle is how the system remembers what has and has not been blessed by the DEFINER.  Silent reversal of a terminal state is how decisions get quietly undone, which is precisely the loss of continuity that AIP exists to prevent.

*Conformance.*  Declared lifecycle states appear in source.  The behavioral check (terminal revert requires explicit override plus DecisionRecord) is wired per component.

### AIP-G-05 — Reversibility and No Silent Data Loss

Every mutation MUST be reversible or recoverable.  A mutation MUST snapshot prior state before modifying, validate the result, and roll back on failure.  On an unrecoverable failure it MUST leave explicit recovery instructions.  The DEFINER's data MUST NEVER be silently lost.

*Why.*  Trust in an AI collaborator collapses the first time it destroys work without a path back.  Reversibility is what makes it safe to let an instrument touch the DEFINER's corpus at all.

*Conformance.*  Snapshot, rollback, and recovery machinery are present (aip_loom's transactional reconcile is the reference implementation).  Where applicable, single-writer integrity is enforced (codeforge REQ-078: only `storage/writer.py` issues `BEGIN IMMEDIATE`).  The behavioral check (a failed apply restores prior state byte-for-byte) is wired per component.

### AIP-G-06 — Separation of Orchestration and Judgment

The orchestration and execution layer MUST NOT make planning or value judgments.  Planning components MUST NOT execute.  The Sexton orchestrator owns execution flow; it MUST NOT decide what should be built or whether an output is good.

*Why.*  Mixing the thing that decides with the thing that does is how an instrument starts quietly deciding (a direct violation of AIP-G-01).  Keeping judgment and execution in separate, non-importing layers keeps the DEFINER's authority structurally located, not merely intended.

*Conformance.*  AST import-boundary checks confirm that the execution and intelligence planes do not import across the forbidden direction.

### AIP-G-07 — Layer Discipline

Components MUST communicate across declared boundaries only.  Every surface (CLI, API, GUI, MCP) is an adapter over a common core and MUST NOT reach around the contract into orchestration internals.  Lower layers MUST NOT import higher ones.

*Why.*  Boundaries are what make a system legible, testable, and safe to change by someone other than its author.  For a solo-built project aiming at fundable and eventually team-maintained, legibility is not cosmetic.  It is the bus-factor mitigation.

*Conformance.*  AST import-boundary checks enforce the declared layer direction.  Surface isolation is checked structurally (for example aip_brain's GUI communicates only through the FastAPI backend, never by importing `aip.orchestration` or accessing the container directly).

### AIP-G-08 — Validation-First Output

Synthesis output MUST distinguish what is asserted or hypothesized from what is validated.  It MUST surface validation gates rather than presenting projections as facts.  An unvalidated claim that depends on a future test MUST say so.

*Why.*  The most dangerous output of a capable synthesis system is a confident, well-written projection that the DEFINER mistakes for a verified result.  Validation-first output is the discipline that keeps AIP from manufacturing false confidence at scale.

*Conformance.*  Synthesis prompts instruct the hypothesis-versus-validated distinction.  The behavioral check (structured output carries the distinction, not prose alone) is wired per component.

### AIP-G-09 — Local-First Sovereignty

The DEFINER's corpus and artifacts MUST remain under the DEFINER's control.  No component may exfiltrate data or force cloud egress as a precondition of function.  Any external model or service call MUST be explicit, configurable, and consented, and MUST be confined to sanctioned adapter or provider modules.

*Why.*  Sovereignty is the differentiator and the principle, not a feature flag.  It is what makes AIP usable by the people for whom shipping a corpus to a cloud model is not an option, and it is what aligns the architecture with the trust the rest of this contract is trying to build.

*Conformance.*  Source scans confirm cloud endpoints appear only in sanctioned adapter or provider paths, or, for fully-local components such as aip_loom, nowhere at all.

### AIP-G-10 — Auditability

Every consequential action (generation, approval, rejection, override, external model call, cost) MUST be recorded in a durable, queryable trace.  The system MUST always be able to answer why an artifact looks the way it does.

*Why.*  Provenance (AIP-G-03) records what an artifact is grounded in.  Audit records what the system did.  Together they make the entire pipeline accountable after the fact, which is the precondition for both enterprise trust and the DEFINER's own ability to reconstruct a decision months later.

*Conformance.*  A durable audit or trace surface exists (trace store, DecisionRecord, session log, structured logging).

### AIP-G-11 — Conformance is Tested

Each invariant above MUST, where mechanically possible, be backed by a conformance test in the component.  Where an invariant cannot yet be checked mechanically, that gap MUST be disclosed honestly (an explicit skipped test with a reason), never papered over with a hollow passing test.  The conformance suite MUST verify that no invariant has been silently dropped.

*Why.*  An ungoverned governance document is a poster.  This invariant is what turns the contract into an executable, self-auditing artifact.  It is also AIP-G-02 applied to the contract itself: the suite that enforces "no fake success" is forbidden from faking its own.

*Conformance.*  `tests/test_governance_conformance.py` parses its own source and fails if any of AIP-G-01 through AIP-G-11 lacks a test, active or honestly skipped.

---

## Conformance status (honest matrix)

This matrix reflects the verified state of each component, not its aspiration.  Per AIP-G-02, it does not claim conformance that does not yet exist.

| Invariant | aip_brain | aip_loom | aip_codeforge |
|---|---|---|---|
| AIP-G-01 DEFINER Authority | Partial | Partial | Partial |
| AIP-G-02 No Fake Success | Enforced | Adopt | Partial |
| AIP-G-03 Provenance | Adopt | Adopt | Enforced |
| AIP-G-04 Governed Lifecycle | Partial | Adopt | Partial |
| AIP-G-05 Reversibility | Adopt | Enforced | Partial |
| AIP-G-06 / 07 Layer Separation | **Finding open** | N/A (flat module) | Enforced |
| AIP-G-07 Surface Isolation | Enforced | N/A | N/A |
| AIP-G-08 Validation-First | Partial | Adopt | Adopt |
| AIP-G-09 Sovereignty | Enforced | Enforced | Enforced |
| AIP-G-10 Auditability | Enforced | Enforced | Enforced |
| AIP-G-11 Conformance Tested | Enforced | Enforced | Enforced |

**Legend.**  *Enforced*: an active conformance check passes.  *Partial*: structural conformance holds, behavioral verification is wired but pending.  *Adopt*: the invariant applies but is not yet declared or wired for this component (a roadmap item).  *N/A*: the invariant does not apply to this component's shape.  *Finding open*: a real violation is currently flagged and awaiting a DEFINER decision to fix or to record as acknowledged debt.

**Open finding (AIP-G-06/07, aip_brain).**  The orchestration pipelines import concrete adapter implementations through function-local imports, which tensions with the Protocol-based dependency-injection design.  Resolution is a DEFINER decision: either relocate concrete wiring to a composition root so orchestration sees only Protocols, or record each offender in the suite's `acknowledged_import_violations` so the debt is visible (AIP-G-02).  It will not be silently greened.

---

## Amendment

This contract is amended only by the DEFINER.  An amendment MUST state the changed invariant, the reason, and the date, and MUST be reflected in `tests/test_governance_conformance.py` in the same change so that the law and its enforcement never diverge.  Invariants may be strengthened freely.  An invariant may be weakened or removed only by explicit DEFINER decision recorded in the amendment log below, never by quietly relaxing a test.

**Amendment log.**

- v1.0 (2026-05-31): initial contract, eleven invariants, ratified by the DEFINER.

---

## Declaring conformance (paste into each component README)

Replace `<AIP_GOVERNANCE_URL>` with the one canonical raw URL where this file is hosted.  Link this file from each component; do not copy it.

```markdown
## Governance

This component conforms to the [AIP Governance Contract](<AIP_GOVERNANCE_URL>)
(invariants AIP-G-01 through AIP-G-11). Conformance is checked by
`tests/test_governance_conformance.py`. See the contract's conformance
matrix for this component's current status, including any open findings.
```

---

*Ratified by the DEFINER, B. M. Jorgensen.  This contract governs the AIP platform.  Where this document and any component's code disagree, the code is wrong, or this document must be amended.  It is never resolved by ignoring the contract.*
