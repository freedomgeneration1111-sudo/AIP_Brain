# ADR Hygiene Trigger Line
# docs/adr_hygiene_prompt.md
#
# Drop this at the TOP of any Grok Build prompt whenever the build
# session involves an architectural decision, schema change, or
# non-trivial design choice. Agents don't maintain doc hygiene
# automatically — this makes it explicit.
#
# Last updated: 2026-06-04

---

## The Standard Trigger Line

Paste this at the top of your Grok Build prompt:

```
HYGIENE: Before writing any code, read ROADMAP.md and docs/decisions/
to understand current architecture. After completing the build task,
if this session introduced or confirmed any architectural decision,
draft an ADR in docs/decisions/ using the next ADR number (check
existing files to find it). Update ROADMAP.md status if any planned
item is now complete. Update STATUS.md if test counts or known
issues changed. Commit hygiene changes separately from code changes.
```

---

## Variants by Task Type

**Pure code task (no new decisions):**
```
HYGIENE: Read ROADMAP.md and docs/decisions/ before starting.
After build, update STATUS.md if test counts changed.
```

**Design/architecture session (decisions likely):**
```
HYGIENE: Read ROADMAP.md and all ADRs in docs/decisions/ before
starting. After build, write a new ADR for any decision made.
Find next ADR number, use template in docs/decisions/ADR-000-template.md.
Update ROADMAP.md. Commit hygiene separately. Do not auto-proceed
past the ADR draft — show it to user for review first.
```

**Documentation-only task:**
```
HYGIENE: Read ROADMAP.md, STATUS.md, and docs/decisions/ before
editing. Do not change source code. Commit doc changes only.
```

---

## The ADR Template Reference

All ADRs follow the structure at `docs/decisions/ADR-000-template.md`.

Core sections:
- **Date / Status / DEFINER** header
- **Context** — why this decision was needed
- **Decision** — what was decided (the bulk of the doc)
- **Alternatives Considered** — what was rejected and why
- **Consequences** — what this decision implies going forward
- **Related** — links to code files and other ADRs

Status values: PROPOSED | ACCEPTED | SUPERSEDED | DEPRECATED

---

## When to Write an ADR

Write an ADR when you make a decision that:
- Cannot be undone cheaply (schema changes, data format choices)
- Reflects a design principle AIP will live by
- Will confuse a future developer (or future-you) without explanation
- Was explicitly debated and alternatives rejected

Do NOT write ADRs for:
- Bug fixes
- Refactors that don't change behavior
- Variable naming
- Routine feature additions that implement an already-specified design

---

## Doc Update Checklist (end of any session)

- [ ] ROADMAP.md — mark completed items ✅, add new planned items 🔲
- [ ] STATUS.md — update corpus stats, test counts, known issues
- [ ] docs/decisions/ — new ADR if a decision was made
- [ ] docs/entity_aliases.md — add aliases if new entities appeared
- [ ] CONTRIBUTING.md — update if build conventions changed
- [ ] Commit message — reference ADR numbers if applicable
