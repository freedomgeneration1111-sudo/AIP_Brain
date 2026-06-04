# ADR-004: Multi-Corpus Architecture

**Date:** 2026-06-04
**Status:** PROPOSED — not yet implemented
**DEFINER:** B. Moses Jorgensen

---

## Context

Moses needs to build specialized research corpora (Branham sermons, academic
citations for NBCM/EZ water) that are distinct from his personal conversation
corpus. These external corpora should be queryable through AIP but must not
dilute or pollute the personal corpus with external content.

## Problem

The current AIP corpus is a unified personal knowledge base of Moses's own
conversation turns. External research corpora (1200 Branham sermons, thousands
of academic papers) are qualitatively different:
- They are authored by others, not Moses
- They are reference material, not working memory
- They should be queryable but not retrieved alongside personal turns by default
- Some should be shareable (e.g., NBCM citations for collaborators) without
  exposing personal corpus content

## Decision (Proposed)

**Two-tier corpus architecture:**

**Tier 1 — Personal corpus** (current state)
`db/state.db` — Moses's conversation turns, Beast-tagged, always private.
Default corpus for all queries. Never receives external content.

**Tier 2 — Named research corpora** (to be built)
Separate SQLite databases per research project:
```
corpora/branham/state.db       — Branham sermons, books, critic analysis
corpora/nbcm_citations/state.db — Academic papers for NBCM + EZ water
corpora/[future]/state.db
```

**Corpus registry in config/aip.config.toml:**
```toml
[corpora.personal]
db_path = "db/state.db"
description = "Personal conversation corpus"
default = true

[corpora.branham]
db_path = "corpora/branham/state.db"
description = "Branham sermons, books, and critical analysis"
default = false

[corpora.nbcm_citations]
db_path = "corpora/nbcm_citations/state.db"
description = "Scientific citations for NBCM and EZ water research"
default = false
```

**CLI corpus selection:**
```bash
# Ingest into named corpus
uv run aip corpus ingest branham_sermons/ --corpus branham --source-model text

# Tag a named corpus
uv run aip corpus tag --corpus branham --limit 5000

# Query a named corpus in augmented chat
# (corpus selector in UI or --corpus flag in CLI)
```

**Query-time corpus selection:**
- Default: personal corpus only
- Explicit: `--corpus branham` or `--corpus all`
- UI: corpus selector dropdown in augmented chat

**Each corpus is independent:**
- Own CorpusTurnStore
- Own Beast tagging (may use corpus-specific domain registry extension)
- Own wiki and graph (Phase 2)
- Own embedding index
- Personal corpus Beast does not read external corpora

## Planned Research Corpora

**branham** — William Branham primary sources and criticism
- Source: branham.org sermons (1200+), books (text format)
- Source: Critic sites (seekgod.ca, contendingforthefaith.com, sitestrip)
- Parser needed: plain text sermon parser, web crawl parser
- Domain registry extension: branham_doctrine, branham_biography,
  branham_criticism, message_movement
- Use case: Systematic analysis of Branham's actual doctrinal positions
  and documented contradictions across his full ministry

**nbcm_citations** — Scientific literature for NBCM and EZ water
- Source: arXiv papers (null surfaces, soft charges, holographic encoding,
  boundary conditions in GR, decoherence, quantum gravity)
- Source: Pollack EZ water papers and citations
- Source: Related consciousness and quantum cognition literature
- Parser needed: arXiv/PDF parser, BibTeX parser
- Domain registry extension: quantum_gravity, soft_charges, ez_water_science,
  holography, decoherence, consciousness_science
- Use case: Citation support for NBCM paper, answer "what does the literature
  say about X" with real citations

## Export Package Model

When a research corpus reaches maturity, it can be exported as a standalone
shareable package:
- Relevant turns + wiki articles + graph nodes + embeddings
- Versioned snapshot (not live-synced)
- Recipient can query without access to personal corpus
- Format: compressed archive with portable SQLite + metadata

## Consequences

- Requires new parsers (plain text, PDF, web crawl, BibTeX)
- Corpus registry adds config complexity but keeps each corpus isolated
- Beast runs separate tagging cycles per corpus
- Query time increases slightly when searching multiple corpora
- Implementation order: corpus registry config first, then parsers,
  then corpus-specific Beast tagging, then query-time selection

## Alternatives Considered

**Single unified corpus with source_model filtering** — rejected. External
research content (Branham sermons) would appear in personal corpus retrieval
by default. A theology question might retrieve a Branham sermon turn when it
should retrieve Moses's own theological analysis.

**Separate AIP instances** — rejected. Too much operational overhead. Managing
five separate AIP installations is impractical for a solo operator.

**Domain-only separation in unified corpus** — insufficient. Domain tags filter
retrieval but don't prevent cross-contamination of corpus statistics, Beast
summaries, and importance scoring. A Branham sermon domain summary would
influence the theology_research domain overview even though they're different
knowledge types.

## Related

- ADR-001: Turn-Level Corpus Ingestion
- ADR-002: Beast Domain Registry
- ROADMAP.md Phase 1.5, Phase 2.3
- src/aip/adapter/corpus_turn_store.py (will need corpus_id parameter)
- config/aip.config.toml ([corpora] section to be added)
