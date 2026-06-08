# ADR-005: AIP Project Namespace — The Hall Model

**Date:** 2026-06-04
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

---

## Context

The AIP project has grown to include multiple distinct software products:
- AIP Brain — the core sovereign knowledge engine
- AIP Loom — the long-form writing and document continuity module
- CodeForge — the autonomous coding pipeline
- FG Translate — the translation technology product

During corpus tagging, Beast consistently routed AIP architecture turns to
`aip_methodology` rather than `aip_brain`, because spec development conversations
read as methodology discussions rather than implementation discussions. The
`aip_methodology` domain was too broad — it captured both "what AI Poiesis is"
and "how AIP Brain is built."

## Decision

**The Hall Model:** AIP as a unified intellectual project uses a hall-and-rooms
metaphor for domain organization.

```
aip              — THE HALL
                   The whole AIP project as intellectual endeavor.
                   The 'why' and 'how': AI Poiesis methodology and philosophy,
                   the seed prompt, DEFINER sovereignty principle, multi-model
                   orchestration theory, field notes, aipoiesis.io blog,
                   the AI Poiesis book, capability tiers.

aip_brain        — THE ENGINE ROOM
                   AIP software implementation specifics only.
                   The 'what/build': actual code, bug fixes, specific component
                   behavior, deployment, database schema decisions, API routes,
                   test results. Turns about building the thing, not what it is.

aip_loom         — THE WRITING ROOM
                   Long-form writing module and THREAD document continuity.
                   Distillate format, spoken-register synthesis, manuscript support.

codeforge        — THE FORGE ROOM
                   Autonomous coding pipeline system.
                   CodeForge spec, Sexton orchestrator, ReviewerEnsemble, WorkUnit.
                   Will be refactored into aip_brain eventually.

fg_translate     — THE TRANSLATION ROOM
                   Translation technology products specifically.
                   Komal app, FG Translate Telegram bot, romanized Urdu technology.
                   NOT general Urdu/Punjabi communication.
```

**The renamed domain:** `aip_methodology` → `aip` in registry v1.1.
All connector references updated (e.g., `aip_methodology->theology_research`
→ `aip->theology_research`).

**The clarified boundary:** `aip_brain` is implementation-only. Spec discussions,
architecture philosophy, methodology — these go to `aip`. Code, bugs, routes,
tests — these go to `aip_brain`.

## Why This Matters

Beast's first tagging pass assigned only 8 turns to `aip_brain` vs 455 to
`aip_methodology`. This suggested Beast was correctly sensing that most AIP
conversation is about methodology and philosophy, not implementation details.
The hall model makes this explicit and correct rather than accidental.

For retrieval quality: a query about "DEFINER sovereignty" should retrieve `aip`
turns. A query about "corpus_turns table schema" should retrieve `aip_brain` turns.
These are different retrieval contexts and deserve different domain routing.

## Consequences

- Registry v1.0 `aip_methodology` turns need --retag to become `aip`
- `aip_brain` will likely have fewer turns than `aip` — this is correct
- The hall model is a useful conceptual framework for AIP onboarding and
  the AI Poiesis book (explaining what each component does)
- Future AIP extensions follow the same pattern: new rooms in the hall

## Related

- ADR-002: Beast Domain Registry
- docs/beast_domain_registry_v1.md (v1.1 implements this decision)
- ROADMAP.md Phase 1.3 (Registry v1.1)
