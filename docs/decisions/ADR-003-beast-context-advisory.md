# ADR-003: Beast Context Advisory for Augmented Chat

**Date:** 2026-06-04
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

---

## Context

Augmented chat mode needed to retrieve relevant corpus content and present it
to the synthesis model in a way that actually improves response quality.
The original implementation dumped raw FTS5 chunks into a generic system prompt.

## Problem

Raw chunk retrieval produced poor augmented chat quality because:
1. Chunks were fixed-size text windows with no semantic coherence
2. The synthesis model received fragments with no domain context or orientation
3. Beast had no participation in the real-time chat path despite being the
   corpus intelligence actor
4. The system prompt was generic ("you are AIP, cite sources like [source: X]")
   with no domain-specific grounding

## Decision

**A two-tier context preparation architecture:**

**Tier 1 — BeastContextPreparer (real-time, stateless)**
Runs on every augmented chat turn. Fast — no LLM calls.
- Retrieves relevant CorpusTurns via FTS5 + vector search (hybrid)
- Reads latest beast_domain_summary artifact for the relevant domain
- Assembles a ContextAdvisory dataclass:
  - domain_overview (from Beast's pre-generated summary, if exists)
  - retrieved_chunks (top N turns by relevance score)
  - source_citations (human-readable citation list)
  - advisory_text (deterministic string assembly, NOT a model call)
- Injects into synthesis model system prompt as structured context block

**Tier 2 — Beast background domain summaries (async, event-driven)**
Runs in Beast's background cycle. May call Beast's LLM slot.
- Triggers when corpus_modified event is newer than last summary generation
- Samples chunks from domain, calls Beast LLM, saves as GENERATED artifact
- DEFINER reviews and approves → becomes canonical domain overview
- Does NOT run on every chat turn — runs once per corpus change per domain

**The context block format injected into synthesis model:**
```
=== AIP CORPUS CONTEXT ===
Query: {query}
Domain: {domain}

DOMAIN OVERVIEW:
{beast_domain_summary if exists}

RELEVANT PASSAGES ({N} retrieved):
[1] {turn content}...
[2] {turn content}...

Citations: [1] turn_id: first 60 chars
=== END CONTEXT ===
```

## Key Design Constraints

- advisory_text is always deterministic string assembly — never a model call
  in the real-time path. LLM cost is paid once in Beast's background cycle,
  not on every chat turn.
- BeastContextPreparer is stateless — reads only, never writes to any store
- Beast's background summaries go through ECS GENERATED → DEFINER review →
  APPROVED before influencing chat responses (GENERATED state is also readable
  for freshness, but APPROVED is canonical)
- The synthesis model's response quality depends entirely on what Beast has
  prepared. Poor domain summaries → poor augmented responses.

## Alternatives Considered

**Beast in the real-time chat path as an LLM call** — rejected. Adding an LLM
call to every chat turn would add 10-30 seconds of latency. Users cannot wait
that long. The two-tier architecture preserves intelligence without sacrificing
responsiveness.

**Pure keyword retrieval (FTS5 only)** — insufficient. FTS5 finds turns
containing the query words, not turns semantically related to the query intent.
Hybrid FTS5+vector scoring (Phase 1.4) is the upgrade path.

**No domain orientation** — rejected. Without domain context, the synthesis
model treats all retrieved turns as equally relevant. Domain overview helps
the model understand what universe it's in before reading specific passages.

## Consequences

- Domain overview quality depends on Beast's LLM slot model quality
- If no domain summary exists for a query domain, the context advisory gracefully
  omits the overview (no error, just less context)
- Beast summaries are DEFINER-gated — reviewing them is a regular maintenance task
- Embedding pipeline (Phase 1.4) will significantly improve retrieval quality
  by adding vector similarity alongside keyword matching
- Context advisory response payload is returned to GUI for citation display
  (GUI doesn't yet display citations — Phase 4.1)

## Related

- ADR-001: Turn-Level Corpus Ingestion
- ADR-002: Beast Domain Registry
- src/aip/orchestration/context_advisory.py
- src/aip/orchestration/actors/beast.py (domain summary generation)
- src/aip/adapter/api/routes/chat.py (augmented mode context injection)
