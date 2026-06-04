# ADR-006: Beast Wiki Architecture

**Date:** 2026-06-04
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

## Context

AIP's Beast actor generates domain summaries injected into augmented
chat as domain overviews. These summaries are short (200-400 words),
serve only as LLM orientation context, and are not human-browsable
as a knowledge artifact.

The DEFINER needs a richer knowledge base that serves two purposes:
(1) human browsing to reconnect with current thinking on a domain
and spark new research directions, and (2) LLM orientation context
in augmented chat.

Research confirmation (June 2026): Andrej Karpathy published the
"LLM Wiki" pattern in April 2026 (5,000+ GitHub stars in two weeks).
It names exactly what AIP's Beast wiki is doing. AIP's critical
advantage over all existing LLM wiki implementations: the DEFINER
gate prevents AI-generated content from entering the canonical
knowledge base without explicit approval.

## Decision

Beast generates wiki articles at concept level, triggered by corpus
change events, reviewed and approved by DEFINER before canonical.

**Scope:** Domain-level first (28 articles), expanding to concept-level.
NBCM alone may reach 100 articles. Brick kiln project may reach 50+.
A fully developed domain wiki is the spine of a manuscript.

**Trigger:** Event-driven. Beast wiki pass when cumulative new tokens
processed in a domain since last wiki generation exceeds ~1M tokens.
Not timer-driven — time passing without new content means the existing
wiki remains accurate.

**Article structure (dual-purpose):**
- Overview (3-5 sentences) — injected into augmented chat
- Key Concepts — human reading
- Cross-Domain Connections — bridge relationships
- Current State — where the work stands, decisions made
- Evolution — how thinking has changed over time
- Key Turns — 3-5 highest-importance corpus turns with citations
- Open Questions — what remains unresolved

**UI:** Built-in markdown editor. DEFINER injects <comment> tags
at convenience. Reviewed/unreviewed indicator. Publication export.

**Publication pipeline:** Approved wiki articles are the spine of
manuscripts. Architecture of Mercy, NBCM paper, bonded labor
intervention — compilable from approved wiki articles.

## Critical Risk

Beast hallucinating about the DEFINER's own thinking is the
highest-risk failure mode. A subtly wrong NBCM summary, approved
without careful reading, corrupts every subsequent augmented chat
session in that domain.

Mitigation: high-importance domain articles require explicit
careful DEFINER review. Vigil runs wiki health checks to detect
contradictions between wiki content and corpus turn content.

## Alternatives Considered

**Dynamic generation on every query** — rejected. No accumulation.
**Domain-level only** — insufficient for mature domains.
**Auto-approve Beast wiki articles** — rejected. Highest risk failure mode.

## Related
- ADR-003: Beast Context Advisory
- ADR-005: AIP Hall Model
- ROADMAP.md Phase 2A
