# AIP BRAIN RETRIEVAL ARCHITECTURE MEMO

**From:** Tech Time Traveler, AGI Era (approx. 2041)
**To:** AIP Brain Architecture Team
**Subject:** Retrieval System Design Guidance — What We Learned the Hard Way
**Date:** 2026-06-07
**Classification:** Architectural Guidance

---

## Preamble

I have reviewed your retrieval architecture in its current form — the five-store design (CorpusTurnStore, LexicalStore, VectorStore, KnowledgeStore, GraphStore), the ask_pipeline's three-source fusion, the chat route's five-layer context assembly, and the Beast/Sexton maintenance loops. What you have built is genuinely ahead of its time: a sovereign, locally-first knowledge engine with graceful degradation as a first-class principle. That last point alone puts you ahead of 90% of the "AI-native" systems that will emerge between now and 2030.

But I have also seen how systems like this evolve — and where they break when you try to scale them toward autonomous agency and embodied robotics. The purpose of this memo is not to praise or demolish your architecture, but to point at the specific stress fractures that will open under the weight of what you're building toward, and to suggest interventions that are cheap now and impossibly expensive later.

I will organize this around seven theses. Each is grounded in a specific feature of your current codebase.

---

## Thesis 1: The Missing Retriever Abstraction Will Kill You

**Current state:** Every caller (ask_pipeline, chat.py, beast_scan, knowledge_store) independently selects and combines stores. There is no shared `Retriever` interface. The ask_pipeline has its own hybrid fusion logic (0.4×lexical + 0.6×vector for overlapping IDs). The chat route has a different cascade (CorpusTurnStore → fallback to LexicalStore+VectorStore). Beast scan uses only FTS5 with no vector retrieval at all. The L2 retrieval module in `orchestration/retrieval.py` implements four-factor reranking (semantic 0.60, recency 0.15, authority 0.15, frequency 0.10) but nobody calls it.

**What happens next:** Within 18 months you will have seven different retrieval code paths, each with subtly different scoring, filtering, and fallback behavior. A bug fix in one won't propagate to the others. You'll discover that Beast's scan returns stale results because it doesn't go through the same freshness filter as chat. You'll find that ask_pipeline and chat.py return different top results for the same query because one applies importance boosts and the other doesn't. The divergence will be invisible until it manifests as inconsistent behavior that users notice but can't reproduce.

**The fix — a unified Retriever protocol:**

```python
@runtime_checkable
class Retriever(Protocol):
    """Single retrieval interface for all callers."""
    async def retrieve(
        self,
        query: str,
        *,
        domains: list[str] | None = None,
        max_results: int = 10,
        min_confidence: float = 0.3,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        context: RetrievalContext | None = None,
    ) -> RetrievalResult:
        """Retrieve relevant chunks from the knowledge base.
        
        mode controls the retrieval strategy:
          HYBRID  — fuse lexical + vector + corpus (default)
          LEXICAL — FTS5 only (fast, no embedding cost)
          SEMANTIC — vector only (when lexical is unavailable)
          DEEP    — hybrid + graph expansion + wiki (for augmented chat)
        
        context carries session-level signals (conversation history,
        detected domain, epistemic flags) that influence ranking.
        """
        ...
```

The key insight is not the interface itself but the **single implementation** behind it. All scoring, filtering, fusion, and reranking logic lives in one place. Beast scan, ask pipeline, and chat route all call `retriever.retrieve(query, mode=...)` with different mode flags but the same scoring engine. This costs you maybe two days of refactoring now. It costs you two months when you have to unify seven divergent code paths under production pressure.

**Why this matters for AGI:** Autonomous agents need retrieval to be *predictable* — not in the sense of returning the same result every time, but in the sense that the retrieval system's behavior is a function of the query and configuration, not of which code path happened to be invoked. When an agent is planning a multi-step action sequence and retrieval returns inconsistent context on step 3 versus step 7, the plan falls apart. I have seen this happen in production robotic systems where the planning agent retrieved different safety constraints depending on which API endpoint happened to handle the sub-query. The robot tried to pick up a 40kg object with a 20kg-rated arm because the retrieval path that returned the weight limit went through LexicalStore instead of CorpusTurnStore and the FTS5 index hadn't been synced yet.

---

## Thesis 2: Your Two FTS5 Indexes Are a Consistency Time Bomb

**Current state:** You have `corpus_turns_fts` in `state.db` (auto-synced via triggers on the `corpus_turns` table) and `fts_index` in `lexical.db` (manually synced via `index_document()` calls). They index different data — corpus turns versus ingested artifacts and compiled knowledge — but there is overlap in the domains they cover, and no mechanism to ensure consistency between them.

**What happens next:** At some point, a user will ingest a document, see it in Beast scan (which queries `corpus_turns_fts`), then ask the ask pipeline about it and get no results (because the ask pipeline also queries `lexical.db` which wasn't updated). Or the reverse — they'll see stale content in one index that was updated in the other. The symptom will be "sometimes it knows things and sometimes it doesn't," which is the worst possible failure mode for a knowledge system because it erodes trust without giving the user any actionable information about what went wrong.

**The fix — unify the FTS5 layer:**

Consolidate to a single FTS5 index that covers all searchable content. You can keep separate tables for the source data (corpus turns, compiled knowledge, ingested artifacts) but the full-text index should be unified. This is not a performance concern — SQLite FTS5 handles millions of rows trivially. The concern is consistency: a single index that is always in sync with its source tables.

The pattern that works: one `search_index` table with a `source_type` column (`corpus_turn`, `compiled_knowledge`, `ingested_artifact`) and triggers that keep it in sync with the source tables. When CorpusTurnStore writes a turn, the trigger inserts into `search_index`. When KnowledgeStore marks something APPROVED and dual-indexes it, the same `search_index` gets the row. One index, one source of truth, one place to debug when something is missing.

**Why this matters for robotics:** An autonomous robot building a world model cannot tolerate two representations of the same room that disagree about whether a door is open. The retrieval layer is the robot's memory, and memory must be consistent. Two indexes that can drift apart is two memories that can contradict each other. In the AGI era, we call this "dissociative retrieval" and it is the number one cause of agents hallucinating constraints that don't exist while failing to recall ones that do.

---

## Thesis 3: Embedding Dimension Lock-In Is a Silent Killer

**Current state:** Your default embedding model is `nomic-embed-text` at 768 dimensions. SqliteVssVectorStore defaults to 768 dimensions. PgvectorStore auto-detects. InMemoryVectorStore doesn't care. The vector dimension is effectively hardcoded at creation time and never re-validated.

**What happens next:** In 2027, a new embedding model comes out that produces 1024-dimensional vectors with 40% better retrieval accuracy. You switch the model in the config. Half your existing vectors are 768-dim, half are 1024-dim. Cosine similarity between vectors of different dimensions is undefined. Your retrieval quality silently degrades to random because the vector store returns garbage for any query that doesn't match the dimensionality of the stored vectors. You don't notice for two weeks because the system degrades gracefully — it just falls back to FTS5, which still works, so users see slightly worse results but nothing obviously broken.

**The fix — embedding migration as a first-class operation:**

```python
class VectorStore(Protocol):
    # ... existing methods ...

    async def migration_status(self) -> MigrationStatus:
        """Check if vectors need re-embedding due to model change."""
        ...

    async def reembed_all(
        self,
        embedding_provider: EmbeddingProvider,
        batch_size: int = 100,
    ) -> MigrationResult:
        """Re-embed all stored content with current provider.
        
        This is a long-running operation. Report progress via events.
        During migration, retrieval continues on old vectors.
        Switch happens atomically when migration completes.
        """
        ...
```

You already have `list_stale_vectors()` which is half of this. What you need is the other half: a migration protocol that (a) detects when the embedding model has changed, (b) re-embeds in batches without blocking retrieval, and (c) atomically switches the active index when migration is complete. This is the database migration problem applied to vector indexes. You solved it for schema migrations. You need to solve it for embedding migrations.

**Critical detail:** Store the embedding model identifier and dimension alongside each vector. Not just in config — in the vector metadata. This lets you detect dimension mismatches at query time rather than silently producing garbage. A simple guard in `retrieve()` that checks `if stored_dim != query_dim: log.error("dimension mismatch")` would have saved us months of debugging in the systems that eventually became the first autonomous warehouse robots.

---

## Thesis 4: Your Context Window Budget Is Implicit and Unmanaged

**Current state:** The chat route assembles five context layers (DEFINER profile, wiki overview, graph connections, corpus sources, synthesis instruction) and injects them as system messages. There is no explicit budget for how many tokens each layer gets. Wiki content can be arbitrarily long. Graph connections grow with the knowledge graph. A query that hits a well-connected domain might inject 15,000 tokens of context before the user's question even appears.

**What happens next:** As your corpus grows and your knowledge graph densifies, the context window fills up with retrieved content. The model spends its attention budget on the 18th graph neighbor instead of the user's actual question. Response quality degrades in ways that look like "the model isn't paying attention to me" — because it isn't. The 128K-token context window sounds infinite until you realize that 80K tokens of retrieved context plus a multi-turn conversation leaves less room for reasoning than a 4K context GPT-3.5 had.

**The fix — token budget allocation:**

```python
@dataclass
class ContextBudget:
    """Token budget for context assembly."""
    total: int = 8000          # max tokens for ALL context layers combined
    profile: int = 500         # DEFINER profile
    wiki: int = 1500           # domain wiki overview
    graph: int = 500           # graph connections
    sources: int = 4000        # retrieved corpus turns
    instructions: int = 1500   # synthesis instructions, epistemic flags
    
    # Reserve for conversation + model response
    conversation_reserve: int = 4000
    response_reserve: int = 2000
```

Every context layer must truncate to its budget. The wiki section gets 1500 tokens — if the full wiki article is 5000 tokens, summarize it to 1500. Graph connections get 500 tokens — show the top-N most relevant edges, not the full neighborhood. Sources get 4000 tokens — this is where most of the budget should go, and it's where you need the most sophisticated truncation: prefer high-confidence, recent, domain-relevant sources over low-confidence stale ones.

**Why this matters for AGI:** Autonomous agents operate in a regime where the context window is the scarcest resource. An agent planning a multi-step task needs to carry forward relevant context from previous steps. If step 1 fills the context with 80K tokens of raw retrieval output, there's no room for steps 2-5. The agent "forgets" what it was doing. We saw this in early autonomous coding agents that would retrieve entire codebases into context and then lose track of the bug they were fixing. The fix was always the same: budget the context window like you budget RAM — every byte has a purpose, and no single consumer gets to eat the whole thing.

---

## Thesis 5: The Graph Store Is Disconnected from Retrieval Scoring

**Current state:** GraphStore is used in two places: Beast scan (which shows graph neighbors as supplementary info) and the chat route (which injects 1-hop neighbors as a separate context layer). But graph connectivity plays no role in retrieval scoring. A turn that is central in the knowledge graph (high degree, many edges) is not ranked higher than an isolated turn with the same FTS5 score. The graph is decorative, not structural.

**What happens next:** As your corpus grows past 100K turns, the graph becomes the most important signal for retrieval quality. FTS5 and vector similarity are relevance signals — they tell you whether a chunk matches the query. The graph is a *significance* signal — it tells you whether the chunk matters in the broader knowledge structure. A query about "database migrations" might return 50 chunks that are all equally relevant by FTS5 score, but the 3 that are connected to your core architectural decisions (high degree, connected to canonical artifacts, referenced by multiple domains) are the ones the model actually needs. Without graph-weighted scoring, the model gets 50 relevant chunks and has no way to know which 5 are significant.

**The fix — graph-aware retrieval scoring:**

Add a graph centrality term to the retrieval scoring formula. This doesn't require changing the graph store — it requires adding a scoring hook that consults the graph at retrieval time:

```python
# In the unified Retriever:
async def _graph_boost(self, chunk_id: str, domain: str) -> float:
    """Boost score based on graph centrality.
    
    Returns 0.0-0.2 boost based on:
    - Degree centrality in the domain subgraph
    - Whether the chunk is a canonical artifact
    - Whether it bridges multiple domains
    """
    neighbors = self.graph_store.get_neighbors(chunk_id, min_confidence=0.3)
    degree = len(neighbors)
    # Logarithmic scaling: degree 1 → 0.02, degree 10 → 0.10, degree 100 → 0.20
    return min(0.20, 0.02 * math.log2(max(degree, 1) + 1))
```

This is the cheapest high-impact change in this memo. You already have the graph. You already have the neighbor queries. You just need to wire the graph signal into the scoring formula. The L2 retrieval module already has a placeholder for this (the `authority` weight in `RerankWeights` at 0.15) but it's not connected to the actual graph.

**Why this matters for robotics:** A robot navigating a building doesn't just need to know "there's a door here" (relevance). It needs to know "this door is the main entrance, connected to the lobby, the security desk, and the elevator bank" (significance). Graph centrality is how you distinguish the front door from the broom closet. In retrieval terms, the broom closet document and the front door document both match the query "door" — but only one of them matters for planning a path through the building.

---

## Thesis 6: Eventual Consistency Is Fine for Search, but You Need to Be Explicit About It

**Current state:** Your system has several eventual consistency points: FTS5 indexes are auto-synced via triggers (strong consistency), but LexicalStore requires manual `index_document()` calls (eventual), vector embeddings are generated by Sexton's embedding pass (eventual, batch-processed), and the KnowledgeStore dual-indexes on APPROVED state (eventual relative to canonical promotion). None of these are explicitly modeled — there's no "consistency level" concept, no way for a caller to request strongly-consistent retrieval versus eventually-consistent retrieval.

**What happens next:** A user ingests a document, immediately asks a question about it, and gets no results. They assume the ingestion failed. They re-ingest. Now you have duplicates. Or: Sexton tags a batch of turns with new domains, but Beast's domain summary hasn't run yet, so the wiki overview is stale. The user sees "domain: machine_learning" in the scan results but the wiki still says "domain: uncategorized." They lose confidence in the system.

**The fix — consistency metadata on retrieval results:**

```python
@dataclass
class RetrievalResult:
    chunks: list[ScoredChunk]
    total_available: int
    consistency: ConsistencyReport

@dataclass  
class ConsistencyReport:
    """What the retrieval system knows about its own freshness."""
    index_lag_seconds: float    # how far behind the FTS5 index might be
    embedding_lag_seconds: float  # how many turns lack current embeddings
    wiki_last_updated: str | None  # timestamp of last wiki generation
    graph_last_updated: str | None  # timestamp of last graph extraction
```

This is not about making everything strongly consistent — eventual consistency is the right tradeoff for a local-first system. The point is to make it *visible*. When a retrieval result comes back with `embedding_lag_seconds: 3600`, the caller can tell the user "results may not include the latest ingested content — re-indexing in progress." When the wiki is stale, the chat route can skip injecting it rather than injecting outdated information. The consistency report turns a silent degradation into a visible, communicable system state.

**Why this matters for robotics:** An autonomous robot operating in a dynamic environment must know when its map is stale. If the retrieval system returns "no obstacles on path B" but the consistency report says "last sensor update 45 minutes ago," the robot can choose to re-scan before committing to path B. The same principle applies to knowledge retrieval: if the retrieval system's confidence is degraded because the indexes haven't caught up, the agent should know that and adjust its planning accordingly. Ignorance of staleness is worse than staleness itself.

---

## Thesis 7: You Need a Memory Hierarchy, Not Just a Retrieval Pipeline

**Current state:** Your system has a flat retrieval model: query → search indexes → return results. There is no distinction between "working memory" (the current conversation), "short-term memory" (recently accessed documents), "long-term memory" (the full corpus), and "procedural memory" (how to do things, not just what things are). The chat route comes closest with its five-layer context assembly, but the layers are static (wiki, graph, sources) rather than dynamic (what has this user been working on today?).

**What happens next:** You add multi-turn conversation support. The model needs to remember what it said three turns ago. You add project-level context. The model needs to know what decisions were made last week. You add autonomous agent mode. The agent needs to maintain a coherent goal state across hours of operation. None of these are served by the current flat retrieval model, because the retrieval system treats every query as independent — it has no concept of session continuity, user intent evolution, or task state.

**The fix — a four-tier memory architecture:**

```
TIER 0: WORKING MEMORY (conversation buffer)
  - Last N turns of the current conversation
  - Automatically maintained by the chat route
  - No retrieval needed — it's always in context
  - Capacity: ~4K tokens, ephemeral

TIER 1: EPISODIC MEMORY (session context)
  - What happened in this session: queries asked, domains explored,
    decisions made, artifacts created
  - Stored as session-scoped events in EventStore
  - Retrieved at session start, updated incrementally
  - Capacity: ~2K tokens, session-scoped

TIER 2: SEMANTIC MEMORY (the corpus)
  - This is what you already have: CorpusTurnStore, LexicalStore,
    VectorStore, KnowledgeStore, GraphStore
  - The current retrieval pipeline serves this tier
  - Capacity: unlimited, persistent

TIER 3: PROCEDURAL MEMORY (how-to knowledge)
  - Compiled procedures: "when the user asks about X, retrieve Y,
    then summarize Z, then present with format W"
  - Currently scattered across system prompts, mode modifiers,
    and hardcoded pipeline logic
  - Needs its own store: ProcedureStore with retrieval, versioning,
    and learned optimization
  - Capacity: growing, persistent
```

The key architectural insight is that these tiers have fundamentally different access patterns, consistency requirements, and retention policies. Working memory is ephemeral and fast. Episodic memory is session-scoped and sequential. Semantic memory is persistent and randomly accessed. Procedural memory is persistent, versioned, and rarely written but frequently read.

Right now you have Tier 0 (the chat buffer) and Tier 2 (the corpus). Tier 1 is partially there in the session manager. Tier 3 doesn't exist — your procedural knowledge is hardcoded in Python. When an autonomous agent needs to learn a new procedure ("when debugging database migration issues, first check the schema version, then check the migration log, then compare with the canonical schema"), it currently has no way to store and retrieve that procedure. It has to re-learn it from context every time.

**Why this matters for AGI:** The difference between a chatbot and an autonomous agent is procedural memory. A chatbot retrieves facts. An agent retrieves procedures and executes them. Your system is already closer to an agent architecture than most of what will be called "agentic" in the next five years — you have the actor model (Beast, Sexton, Vigil), the ECS lifecycle, and the autonomy gate. What you're missing is the memory tier that lets an agent accumulate and refine procedures over time. This is the difference between a robot that can follow instructions and a robot that can learn new skills.

---

## Implementation Priority

Not everything in this memo is equally urgent. Here is my recommended implementation order, ranked by impact-per-effort:

| Priority | Thesis | Effort | Impact | Rationale |
|----------|--------|--------|--------|-----------|
| **P0** | Thesis 1: Unified Retriever | 2-3 days | Critical | Stops retrieval divergence before it starts |
| **P0** | Thesis 4: Context Budget | 1 day | High | Immediate quality improvement for augmented chat |
| **P1** | Thesis 5: Graph-aware scoring | 1 day | High | Cheapest high-impact change in this memo |
| **P1** | Thesis 6: Consistency metadata | 2 days | Medium | Turns silent degradation into visible state |
| **P2** | Thesis 2: Unified FTS5 index | 3-5 days | High | Prevents consistency drift, but requires data migration |
| **P2** | Thesis 3: Embedding migration | 3-5 days | High | Essential before any model change, but not urgent yet |
| **P3** | Thesis 7: Memory hierarchy | 5-10 days | Transformative | Architectural foundation for autonomous agents |

---

## Closing Note

What you have built is genuinely unusual. Most "RAG systems" in 2026 will be a thin wrapper around a vector database with a single embedding model and no graph, no wiki, no actor model, no lifecycle management, and no graceful degradation. You have all of those. The risk is not that your system is too simple — it's that it's complex enough to develop emergent inconsistencies that are invisible until they're catastrophic.

The through-line in all seven theses is the same principle: **make the system's behavior a function of its configuration, not of its code path.** When the same query produces different results depending on which API endpoint handled it, which FTS5 index was consulted, or which embedding model was active, you have a system that is correct by accident rather than correct by design. The interventions in this memo all push toward the same goal: a retrieval system whose behavior is predictable, inspectable, and consistent regardless of how it's invoked.

In the future I come from, the systems that survived the transition from "chatbot with retrieval" to "autonomous agent with memory" were the ones that got the retrieval layer right early. The ones that didn't spent years debugging inconsistent behavior that looked like model hallucinations but was really retrieval divergence.

You're closer than you think. Don't waste the lead.

— TTL
