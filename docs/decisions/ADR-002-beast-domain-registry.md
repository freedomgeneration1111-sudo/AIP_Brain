# ADR-002: Beast Domain Registry and Turn Tagging

**Date:** 2026-06-04
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

---

## Context

2,691 corpus turns needed domain classification, importance scoring, and
cross-domain bridge tagging to make retrieval meaningful. Without domain
assignment, augmented chat would search the entire corpus for every query,
returning unrelated content alongside relevant content.

## Problem

How should domains be defined, assigned to turns, and evolved over time?
The corpus spans theology, physics, software development, commercial ventures,
personal life, and ministry — many turns touch multiple domains simultaneously.

## Decision

**Domain taxonomy is maintained in a human-readable registry file**
(`docs/beast_domain_registry_v1.md`) that Beast reads at the start of each
tagging cycle. Beast never hardcodes domains in code.

**Beast assigns domains via LLM batch tagging** — 8 turns per LLM call, with
the full registry as context. Beast assigns:
- `primary_domain` — single best domain for retrieval routing
- `domains` — all relevant domains (multi-domain allowed)
- `tags` — freeform topic tags (3-8 per turn)
- `importance` — 0.0-1.0 score (thinking_text presence adds 0.1 bonus)
- `bridges` — approved cross-domain connector tags

**Beast may PROPOSE new domains and connectors but never unilaterally creates them.**
Proposals go into GENERATED state artifacts for DEFINER review. DEFINER approves
or rejects. Only approved domains appear in the registry.

**Tagging is idempotent and iterative** — tagging_version increments on each
re-evaluation. The corpus is designed to be retagged as the registry evolves.

**Special pseudo-domains:**
- `unclassified` — Beast confidence < 0.4, needs DEFINER help
- `quarantine` — no retrieval value (short greetings, empty turns)

## The Hall Model (v1.1)

The AIP project uses a hall-and-rooms metaphor for domain organization:
- `aip` = the hall (methodology, philosophy, the whole project)
- `aip_brain` = the engine room (software implementation)
- `aip_loom` = the writing room (document continuity)
- `codeforge` = the forge room (autonomous coding pipeline)
- `fg_translate` = the translation room (translation technology)

This distinction emerged during dogfood: Beast was routing too many turns to
`aip_methodology` because spec conversations read as methodology rather than
product. The hall model clarifies: `aip` for the why/how, `aip_brain` for
the what/build.

## Registry Versioning

| Version | Date       | Changes |
|---------|------------|---------|
| v1.0    | 2026-06-03 | Seed registry, 26 domains, 13 connectors |
| v1.1    | 2026-06-04 | Hall model, ancient_archaeology, agi_philosophy, tightened fg_translate |

## Alternatives Considered

**Hardcoded domain list in code** — rejected. Domain taxonomy must evolve as the
corpus grows and new projects emerge. A file-based registry allows DEFINER
to update without code changes or Grok Build prompts.

**Automatic domain discovery only** — rejected. Pure unsupervised clustering
would produce domains that don't match Moses's actual mental model of his work.
The seed registry establishes the conceptual framework; Beast discovers gaps.

**One domain per turn** — rejected. Many turns genuinely bridge domains
(NBCM→theology_research, gef_tech→bonded_labor). Forcing single assignment
would lose the cross-domain connections that are intellectually most valuable.

## Consequences

- Beast tagging a full 2,691-turn corpus takes 4-6 hours on free-tier Nemotron
- Parse failures (~304 of 2,691 first pass) are handled gracefully; affected
  turns get tagging_version=0 and are re-queued on next --retag run
- Registry v1.1 changes require a --retag pass to apply retroactively
- Beast proposals accumulate as GENERATED artifacts; DEFINER reviews periodically
- The registry file is the source of truth; code always reads from it

## Related

- ADR-001: Turn-Level Corpus Ingestion
- docs/beast_domain_registry_v1.md
- src/aip/orchestration/actors/domain_registry.py
- src/aip/orchestration/actors/beast.py (_run_turn_tagging)
- src/aip/cli/corpus.py (aip corpus tag)
