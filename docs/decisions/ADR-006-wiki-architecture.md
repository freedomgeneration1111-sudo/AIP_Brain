# ADR-006: Beast Wiki Architecture

**Date:** 2026-06-04
**Status:** PROPOSED — not yet implemented
**DEFINER:** B. Moses Jorgensen

---

## Context

AIP's corpus is a turn-level retrieval system. It finds specific past turns
that match a query. It does not compile what it knows into organized, persistent
knowledge. When Moses asks "what is the DEFINER gate?" in augmented mode, AIP
returns relevant turns — but does not have a canonical, human-browsable article
explaining the concept from first principles.

Three converging influences validated the wiki layer as necessary architecture:

1. **Karpathy LLM Wiki (April 2026)** — Proposed a three-layer architecture:
   raw sources (immutable) → compiled wiki (LLM-maintained) → schema (rules
   for compilation). Key principle: "Stop re-deriving, start compiling."
   Retrieval from raw sources is inefficient when the same synthesis is needed
   repeatedly. The compiled layer is faster, more consistent, and human-readable.

2. **MemPalace (2026)** — Demonstrated spatial hierarchy for memory organization
   (wings/halls/rooms/closets) with cross-referencing tunnels. 30x AAAK
   compression. 96-100% retrieval accuracy on LongMemEval. Spatial hierarchy
   maps naturally to AIP's domain registry.

3. **Seed corpus requirement** — New users need AIP's own self-knowledge
   pre-loaded so the system is useful from first session. Wiki articles
   are the human-readable component of that seed.

## Decision

Implement a Beast Wiki layer (Phase 2A) as the compilation layer between
the raw turn corpus (Phase 1) and the knowledge graph (Phase 2B).

**Core principles:**

1. **Beast-generated, DEFINER-approved — never auto-canonical.**
   Beast drafts wiki articles from corpus analysis. All articles enter
   GENERATED state. Moses reviews and approves. No article becomes
   canonical without DEFINER action. This preserves the DEFINER gate
   that governs the entire AIP artifact lifecycle.

2. **Domain-first scope.**
   Begin with one article per Beast domain (26+ domains = 26 articles).
   Expand to concept-level as domains mature. Some domains are dense:
   NBCM alone may reach 100+ concept articles. Brick kiln / bonded labor
   may reach 50+. Theological domains may reach 200+.

3. **Dual purpose — browsing and injection.**
   Articles serve two functions simultaneously:
   - Human browsing: reconnect with thinking months later without
     re-reading full conversation histories
   - LLM context injection: approved articles prepended to augmented
     chat synthesis prompt as orientation context, alongside retrieved
     turns (higher-density signal than raw turns)

4. **Publication pipeline.**
   Approved wiki articles are the structural spine of manuscripts.
   "Architecture of Mercy" draws from theological wiki articles.
   NBCM paper draws from physics/chemistry articles. Bonded labor
   document draws from policy/FG_ministry articles. The wiki is not
   a dead end — it feeds document production.

5. **Event-driven generation trigger.**
   Beast wiki pass fires when cumulative new tokens processed in a domain
   exceed approximately 1M tokens since last wiki generation for that domain.
   Not timer-driven. Not user-triggered (though DEFINER can force trigger).
   This prevents premature article generation before sufficient corpus exists.

**Storage:**

```
docs/wiki/
  aip/
    aip-overview.md
    definer-gate.md
    beast-actor.md
    corpus-architecture.md
    [...]
  nbcm/
    nbcm-overview.md
    null-boundary-hypothesis.md
    [...]
  [domain]/
    [concept].md
```

Each article includes:
- `domain:` tag
- `beast_version:` (which Beast generation pass produced it)
- `approved_by:` DEFINER
- `approved_date:`
- `source_turn_ids:` list of corpus turns used in synthesis
- `status:` GENERATED | APPROVED | SUPERSEDED

**Database record:**

Wiki articles stored in `wiki_articles` SQLite table with ECS lifecycle
columns mirroring the corpus_turns table. DEFINER gate via review queue.
Approved articles indexed in FTS5 for keyword retrieval alongside turns.

**Injection into augmented chat:**

When a domain wiki article exists in APPROVED state, the context advisory
prepends it to the synthesis prompt before retrieved turns:

```
[DOMAIN WIKI: aip — approved 2026-06-04]
[article content, ~800 tokens]

[RETRIEVED TURNS]
Turn 1: ...
Turn 2: ...
```

This is denser context than domain summaries (Beast's current mechanism)
because wiki articles are compiled, edited, and structurally organized —
not just LLM-generated summaries of recent activity.

## Alternatives Considered

**No wiki layer — pure retrieval forever** — rejected. As corpus grows past
10,000 turns per domain, retrieval becomes noisier. A compiled layer
that accumulates and cross-references knowledge is more reliable at scale.
Karpathy's analysis confirms this: retrieval is not a substitute for compilation.

**Auto-approved wiki articles (no DEFINER gate)** — rejected. Beast makes
errors. Domain misattribution, factual synthesis errors, outdated content.
An auto-approved wiki would inject unchecked content into augmented chat context.
This is the exact class of error AIP's DEFINER gate was designed to prevent.
The gate adds friction but ensures canonical content is correct.

**External wiki tool (Obsidian, Notion)** — rejected. Breaks AIP's local-first
sovereignty principle. External tools introduce vendor dependency, sync problems,
and break the corpus unity that makes AIP's retrieval coherent. All knowledge
stays in the AIP artifact store.

**Full LLM Wiki / MemPalace adoption** — evaluated, not adopted wholesale.
MemPalace's hierarchy (wings/halls/rooms) is useful as a mental model but
its implementation is vector-only and doesn't match AIP's hybrid FTS5+vector
architecture. Karpathy's LLM Wiki is a blog post, not a library. AIP's wiki
layer incorporates the principles without the implementations.

## Consequences

- Phase 2A is a significant build: new SQLite table, new Beast actor method,
  new review queue UI panel, new context injection path in augmented chat
- Wiki article quality is bottlenecked by Beast's synthesis quality, which
  depends on domain summary quality, which depends on corpus density
- Early domains (aip, nbcm, theology) should reach wiki-worthy density
  before Phase 2A ships; thin domains may wait
- DEFINER review burden increases as wiki grows — Moses must maintain
  article approval cadence or the queue backs up
- Articles must be versioned: as corpus evolves, older articles become stale
  and require re-generation and re-approval (Beast handles detection,
  DEFINER handles approval)

## Related

- ADR-001: Turn-Level Corpus Ingestion (the raw layer)
- ADR-002: Beast Domain Registry (the domain taxonomy)
- ADR-003: Beast Context Advisory (current augmented chat mechanism)
- ADR-007: Knowledge Graph Architecture (the next compiled layer)
- ROADMAP.md: Phase 2A
- Research basis: June 2026 parallel research report on GraphRAG/LightRAG/LLM Wiki
