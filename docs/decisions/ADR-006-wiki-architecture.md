# ADR-006: Beast Wiki Architecture

**Date:** 2026-06-04
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

## Context

AIP's Beast actor generates domain summaries that are injected into
augmented chat as domain overviews. These summaries are short (200-400
words), serve only as LLM orientation context, and are not human-browsable
as a knowledge artifact. The DEFINER needs a richer knowledge base that
serves two purposes: (1) human browsing to reconnect with current thinking
on a domain and spark new research directions, and (2) LLM orientation
context in augmented chat.

Additionally, the research report (June 2026) confirmed that the Karpathy
LLM Wiki pattern (published April 2026, 5,000+ GitHub stars in two weeks)
names exactly what AIP's Beast wiki is doing. AIP has a critical advantage
over existing LLM wiki implementations: the DEFINER gate prevents AI-generated
content from entering the canonical knowledge base without explicit approval.

## Decision

Beast generates wiki articles at concept level (not just domain level),
triggered by corpus change events, reviewed and approved by DEFINER before
they become canonical. Articles serve dual purpose: human reading and LLM
context injection.

Key design choices:

**Scope:** Domain-level first (28 articles), expanding to concept-level
(potentially hundreds per mature domain — NBCM alone may reach 100 articles
covering null boundary, record formation, The Inverse, relational time,
photon timelessness, each falsifiable prediction, etc.)

**Trigger:** Event-driven. Beast wiki pass triggered when cumulative new
tokens processed in a domain since last wiki generation exceeds ~1M tokens.
Not timer-driven. Time passing without new content means the existing wiki
remains accurate.

**Article structure (dual-purpose):**
- Overview (3-5 sentences) — injected into augmented chat system prompt
- Key Concepts — human reading, extended explanation
- Cross-Domain Connections — bridge relationships to other domains
- Current State — where the work currently stands, decisions made
- Evolution — how thinking in this domain has changed over time
- Key Turns — 3-5 highest-importance corpus turns with citations
- Open Questions — what remains unresolved

**UI:** Built-in markdown editor. DEFINER injects <comment> tags at
convenience. Reviewed/unreviewed indicator. Publication export path.

**Publication pipeline:** Approved wiki articles are the spine of manuscripts.
Architecture of Mercy, NBCM paper, bonded labor intervention — all can
be compiled from approved wiki articles. The wiki IS the thinking in
structured form, not documentation of the thinking.

## The Critical Risk

Beast hallucinating about the DEFINER's own thinking is the highest-risk
failure mode. A Beast summary that subtly misrepresents a key NBCM
distinction, approved without careful reading, corrupts every subsequent
augmented chat session in that domain until manually corrected.

Mitigation: high-importance domain articles flagged for careful DEFINER
review (cannot be rubber-stamped). Vigil runs wiki health checks to detect
contradictions between wiki content and corpus turn content.

## Alternatives Considered

**Dynamic generation on every query** — rejected. Inconsistent, expensive,
no accumulation of understanding. The Karpathy pattern shows pre-compiled
wiki outperforms per-query RAG for stable knowledge.

**Domain-level only (28 articles)** — insufficient. NBCM alone warrants
100 articles. Brick kiln project warrants 50+. Concept-level is the right
long-term target; domain-level is the starting point.

**Auto-approve Beast wiki articles** — rejected. Highest risk failure mode.
Every wiki article requires DEFINER review.

## Consequences

- Wiki review queue grows as corpus grows — DEFINER maintenance overhead
- Publication pipeline emerges naturally from approved articles
- BeastContextPreparer reads Overview section (not full article) for
  augmented chat injection to manage token budget
- Concept-level wiki build will require Beast concept extraction pass
  before article generation — this is Phase 2A.2+ scope

## Related

- ADR-003: Beast Context Advisory (wiki overview feeds context advisory)
- ADR-005: AIP Hall Model (wiki articles organized by domain taxonomy)
- ROADMAP.md Phase 2A
- docs/beast_domain_registry_v1.md
---