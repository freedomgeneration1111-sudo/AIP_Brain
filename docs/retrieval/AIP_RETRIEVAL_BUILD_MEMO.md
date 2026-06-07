# AIP Retrieval Architecture Build Memo

**Document Classification:** Authoritative Build Plan  
**DEFINER:** B. Moses Jorgensen  
**Date:** 2026-06-07  
**Branch:** moses-aip-brain  
**Predecessor Documents:** AIP_BRAIN_RETRIEVAL_ARCHITECTURE_MEMO.md, RETRIEVAL_REVIEW_SYNTHESIS.md  
**Companion Document:** AIP_PROJECT_STATUS.md (current project state, phase tracker, file inventory)  
**Status:** APPROVED FOR BUILD

---

## 0. Architectural North Star

> AIP retrieval is not a feature. It is **attention**.

AIP's corpus will eventually contain Claude conversations, ChatGPT conversations, other model conversations, emails, journals, research papers, web research, school documents, publication drafts, invention notes, approved wiki articles, and CodeForge/Loom artifacts. The retrieval system must answer fundamentally different kinds of memory questions: What did I say? What did a model suggest? What did I approve? What evidence supports this? What person, project, or technology is connected? What is the latest state? What is the historical development? What procedure or spec should guide action? What source should be trusted?

That requires multiple retrieval modes under one discipline:

| Mode | Mechanism | Answers |
|------|-----------|---------|
| FTS5 | Full-text search | Exact lexical recall |
| Vector | Embedding similarity | Semantic similarity |
| Graph / PPR | Entity graph + PageRank | Associative relationship recall |
| Wiki | Approved articles | Approved synthesis / community memory |
| Procedural | Approved specs/methods | How-to and procedural memory |
| Trace | Retrieval provenance | Why this result appeared |
| Budget | Token allocation | What fits into model context |

The mistake would be to bolt these on as separate, inconsistent paths. The right move is a unified retrieval substrate where every retriever returns the same shape and fusion does not care whether the source came from FTS5, vector, graph, wiki, or later procedural memory.

---

## 1. Build the Retrieval Substrate Before Adding Magic

The original GraphRAG memo correctly identified the missing entity-turn mapping and the fact that graph_store, wiki articles, and entity information were not being used in ask-time retrieval. It also described the right basic mechanism: entity extraction, seed graph nodes, Personalized PageRank, entity-turn lookup, and RRF merge with FTS5/vector candidates. However, the ensemble synthesis sharpens the sequencing: do not add `_graph_search_sources()` as a fifth special path inside `_search_sources()`. That would multiply existing inconsistency. Instead, create one retriever protocol and make graph retrieval the first conforming implementation.

### Target Architecture

```
AskPipeline
  └── RetrievalOrchestrator
      ├── FTSRetriever
      ├── VectorRetriever
      ├── GraphRetriever
      ├── WikiRetriever
      ├── ProceduralRetriever (later)
      └── Fusion + Budget + Trace
```

### RetrievalHit Shape

Every retriever must return the same shape so that fusion does not care about the source. The RetrievalHit dataclass is the contract that prevents retrieval from becoming a cancer of inconsistent paths.

| Field | Type | Purpose |
|-------|------|---------|
| id | str | Unique hit identifier |
| source_type | str | corpus_turn / wiki_article / artifact / paper / email / procedure |
| source_id | str | Reference to source record |
| title | str \| None | Display title |
| text | str | Retrieved passage text |
| rank | int | Rank within retriever |
| score | float | Raw relevance score |
| confidence | float | Source confidence |
| recency_ts | datetime \| None | Timestamp for freshness scoring |
| importance | float \| None | Sexton importance weight |
| domain | str \| None | Project domain classification |
| entities | list[str] | Entities mentioned in hit |
| retrieval_channel | str | fts / vector / graph / wiki / procedural |
| evidence_status | str | raw / model_output / approved / rejected / superseded |
| debug | dict | Retriever-specific trace data |

---

## 2. Step Zero: Golden Set and Retrieval Trace

The ensemble synthesis is right: the golden set is the arbiter. Reviews, intuitions, and model confidence do not prove retrieval improved. The Komal, GEF RF heating, frost alert, and AIP features queries are the validation mechanism. Before coding big changes, create golden test files that define queries, required clusters, forbidden dominators, and success thresholds.

### Golden Test Directory Structure

```
tests/retrieval_goldens/
├── komal.yaml
├── gef_rf_heating.yaml
├── frost_alert_device.yaml
├── aip_features.yaml
├── aip_retrieval_architecture.yaml
└── fgs_school_registration.yaml
```

### Golden Test Schema

```yaml
query: "Who is Komal and what does she do?"
must_include_clusters:
  - principal_role
  - freedom_generation_school
  - urdu_translation_collaboration
  - curriculum_development
  - brick_kiln_liberation
  - loan_or_support_applications
must_not_dominate:
  - unrelated_aip_implementation
  - generic_pakistan_mentions
  - duplicate_low_signal_turns
success:
  recall_at_25: 0.75
  recall_at_40: 0.85
  noise_top_10_max: 0.25
  answer_quality_manual_gate: pass
```

### Retrieval Trace Instrumentation

Add retrieval trace instrumentation now. Without it, you will not know whether GraphRAG helped or merely changed the flavor of failure. The trace must capture:

| Trace Field | Description |
|-------------|-------------|
| query | Raw user query string |
| normalized query | Lowered, stripped, de-duplicated query |
| detected entities | Entities seeded into graph retriever |
| retriever list | Enabled/disabled per retriever |
| hits per retriever | Count and top scores per channel |
| fusion ranks | Post-RRF ranking of merged hits |
| final selected context | What actually enters the prompt |
| context token budget usage | Tokens used vs. budget per section |
| wiki injected | Yes/no and which articles |
| direct mentions count | Entity-turn direct hits |
| graph expanded entities | PPR-expanded entity list |
| excluded due to budget | Hits cut by budget constraint |
| fallbacks triggered | Which fallback paths activated |

---

## 3. Retriever Protocol and Budgeted Context Manager

### Retriever Protocol

The Retriever protocol is the single most important architectural move. Every retriever must implement the same interface, accept the same query shape, obey the same budget constraints, and return the same hit shape. The protocol enforces AIP-G-02 as a per-retriever contract: failure degrades that retriever only, the ask pipeline falls back to remaining retrievers, and no graph, vector, or wiki failure blocks the answer.

```python
class Retriever(Protocol):
    name: str

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
    ) -> RetrievalList:
        ...
```

The RetrievalQuery carries the raw and normalized query text, optional project domain filter, optional source type filter, optional user intent hint, and a max_candidates cap. The RetrievalList carries the retriever name, a list of RetrievalHit objects, any errors encountered, and a degraded flag indicating partial failure.

### Context Budget

| Context Section | Allocation | Notes |
|----------------|------------|-------|
| User question / instruction | Reserved | Fixed allocation per query |
| System / developer prompt | Reserved | Governance and mode instructions |
| Approved wiki background | 10-15% | Budgeted; never unbounded injection |
| Retrieved evidence | 55-65% | Primary context; curated, not raw top-N |
| Graph / debug summary | 0-5% | Tiny; only when debug mode active |
| Recent chat turns | 10-15% | Conversation continuity |
| Answer reserve | Reserved | Model output generation budget |

Retrieval quality has two steps: candidate recall and context selection. GraphRAG should improve candidate recall. The budgeter decides what the model actually sees. Do not just raise max_sources to 30 and shove 30 chunks into the prompt.

---

## 4. Fix the Lexical Substrate: One Source of Lexical Truth

Consolidate lexical retrieval under the protocol, not let corpus_turns and legacy fts_documents behave like unrelated worlds. The target shape is a single search_index table with a source_type discriminator column.

| Column | Type | Purpose |
|--------|------|---------|
| source_type | TEXT | corpus_turn / artifact / wiki / paper / email / journal / code / procedure |
| source_id | TEXT | Reference to source record |
| title | TEXT | Display title |
| body | TEXT | Full content |
| domain | TEXT | Project domain |
| created_at | TEXT | Creation timestamp |
| updated_at | TEXT | Last update timestamp |
| importance | REAL | Sexton importance weight |
| evidence_status | TEXT | raw / model_output / approved / rejected / superseded |
| searchable_text | TEXT | FTS5-optimized content |

You do not necessarily need to migrate everything immediately. But architecturally, FTS retrieval should return one standard RetrievalHit. This matters because later file upload, web import, emails, journals, and papers all need to enter retrieval without creating six new search paths.

---

## 5. Entity-Turn Index: The Hippocampal Index

The missing structure is the entity-to-turn index. Create a dedicated entity_turn_index table:

```sql
CREATE TABLE IF NOT EXISTS entity_turn_index (
    entity_id   TEXT NOT NULL,
    turn_id     TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    source      TEXT NOT NULL DEFAULT 'unknown',
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, turn_id)
);
CREATE INDEX idx_eti_entity ON entity_turn_index(entity_id);
CREATE INDEX idx_eti_turn   ON entity_turn_index(turn_id);
CREATE INDEX idx_eti_source ON entity_turn_index(source);
```

### Confidence Calibration

| Source | Confidence Formula | Notes |
|--------|-------------------|-------|
| llm_entity_extraction | entity_confidence × turn_importance | Highest confidence; LLM-verified |
| edge_backfill | edge_confidence × turn_importance | Derived from existing edge evidence |
| mention_scan | 0.55-0.75 × turn_importance | Varies by match quality |
| manual | 1.0 | DEFINER-verified |
| importer_metadata | 0.7 × turn_importance | Imported from external source |

### Staleness Pruning

Add a pruning maintenance task that deletes entity_turn_index rows where the referenced turn_id no longer exists in corpus_turns. Run as a periodic maintenance task or on startup in laptop mode.

---

## 6. Mention Scan, Hub Leash, and Type Filter

Do not ship mention scan alone. These are one change, not three.

### Allowed Entity Types

PERSON, PROJECT, ORGANIZATION, TECHNOLOGY, CONCEPT, DOMAIN, METHOD, DOCUMENT. Avoid or downweight generic nouns, common words, overly broad categories, short lowercase aliases, and ambiguous abbreviations.

Rules: aliases shorter than 3 characters ignored unless uppercase acronym; acronyms case-sensitive; word-boundary matching for ordinary words; longest-match-first when overlapping; exact alias match > token match > fuzzy match; store match kind in debug/source metadata.

### Critical Distinction

> entity_turn_index = this entity was mentioned in this turn  
> graph_edges = this relationship was extracted or approved  
> Mention scan improves recall. It does not prove relationships.

### Hub Leash Formula

```
effective_edge_weight = edge_weight / log(degree(entity) + 1)
```

Additional controls: cap turns per expanded entity, cap generic domains, prefer entities with direct path length ≤ 2, exclude low-confidence mention-only generic entities from PPR expansion.

---

## 7. Fix the 0.7 Importance Coverage Bias

Sexton's graph extraction only covers importance ≥ 0.7. Komal and other everyday-but-crucial entities may have many mentions but weak graph connectivity.

### Coverage Fix A: Mention Scan

Makes entity_turn_index complete-ish across all turns, ensuring direct entity-to-turn recall works regardless of graph connectivity.

### Coverage Fix B: Edge Densification

Lower graph extraction threshold toward 0.5 gradually. Staged backfill priorities:

| Priority | Target | Rationale |
|----------|--------|-----------|
| 1 | Turns containing known high-value entities | Maximize impact on known important areas |
| 2 | Turns in golden-set domains | Improve test coverage directly |
| 3 | Turns with multiple entity mentions | High connectivity potential |
| 4 | All remaining 0.5 turns | Slow background densification |

Settings: `sexton.graph_extraction_min_importance = 0.7` (default), dogfood mode = 0.5, targeted backfill = entity/domain-filtered.

---

## 8. GraphRetriever: Direct Mentions First, PPR Second

Two-zone graph retrieval strategy:

- **Zone A:** Direct mention recall — every high-confidence turn directly mentioning seed entities should have a path into candidates before PPR expansion.
- **Zone B:** PPR expansion — run Personalized PageRank to retrieve related entities, then retrieve turns connected to expanded entities.

### Graph Scoring Formula

```
graph_score =
    0.40 * direct_seed_mention
  + 0.25 * ppr_entity_score
  + 0.15 * entity_turn_confidence
  + 0.10 * turn_importance
  + 0.05 * lexical_overlap
  + 0.05 * freshness_or_temporal_fit
```

Do not rely on raw PageRank only.

---

## 9. PageRank Tuning: Temporal Depth Without Recentism

Do not use one global temporal decay for everything. Use query intent:

| Query Mode | Temporal Factor | Strategy |
|------------|----------------|----------|
| Identity / History | 1.0 or very weak decay | Preserve durable associations |
| Current Status | Moderate freshness boost | Recent evidence preferred, historical kept |
| Procedure / Next Action | Prefer latest approved | Approved artifacts prioritized |

```
edge_weight = base_confidence * hub_penalty * optional_temporal_factor
```

Also track visible freshness metadata: oldest evidence, newest evidence, evidence time span, recent evidence count, historical evidence count.

---

## 10. Hub Control: Prevent Graph Retrieval From Becoming Noise

| Control | Mechanism | Purpose |
|---------|-----------|---------|
| Hub leash | weight / log(degree + 1) | Reduces hub influence logarithmically |
| Turns per entity cap | Max N turns per expanded entity | Prevents candidate flooding |
| Generic domain cap | Max M hits from generic domains | Prevents domain-level dominance |
| Path length preference | Prefer path length ≤ 2 | Keeps expansion relevant |
| Mention-only exclusion | Exclude low-confidence generic from PPR | Prevents noise expansion |

---

## 11. Entity Seed Selector

Replace substring matching with a real candidate selector:

```
EntitySeedSelector
├── exact canonical match
├── exact alias match
├── acronym match
├── phrase longest-match-first
├── FTS5 over canonical_name + aliases + description/domain
├── token overlap scoring
├── entity type filter
└── confidence threshold
```

Output: EntitySeed(entity_id, canonical_name, entity_type, confidence, match_reason, matched_text). Make this visible in trace.

---

## 12. RRF Fusion: The Stable Center

```
rrf_score = Σ 1 / (k + rank)  for each retriever, k = 60
```

Then apply AIP-specific modifiers:

```
final_score = rrf_score * importance_factor * evidence_status_factor * freshness_factor * diversity_factor
```

### Evidence Status Scoring Policy

| Evidence Status | Scoring Treatment | Rationale |
|----------------|-------------------|-----------|
| approved artifact / approved wiki | Boost | Verified knowledge |
| raw user turn | Neutral | Original source, unverified |
| model output | Neutral or slight downweight | Derived, not ground truth |
| rejected / superseded | Exclude unless query asks history | Outdated or discredited |
| low-confidence mention scan only | Downweight | Weak signal, high noise risk |

---

## 13. Wiki Retrieval: Background, Not Evidence

WikiRetriever returns approved wiki articles as background hits. Do not blindly prepend one primary wiki. Budget it. Select up to 1-3 wiki articles based on top domains from seed entities, top graph entities, top FTS/vector hits, and user-selected project domain.

Prompt labeling:

```
## Approved Background Synthesis
The following material is approved background. It may guide interpretation,
but claims in the final answer should still be grounded in retrieved evidence
when possible.

## Retrieved Evidence
The following are source-level records from the corpus.
```

---

## 14. Procedural Memory Retriever

The real robotics bridge is approved procedural memory: Forge specs, CodeForge pipelines, approved methodology artifacts, how-to documents. This should become a later retriever (ProceduralRetriever) for questions like: How should we build this? What process did we approve? What checklist applies? What steps did we decide? What invariant should guide this coding task?

---

## 15. Query Rewriting: Defer Until Substrate Holds

Defer until golden tests prove the non-LLM retrieval path works. Do not use Beast for pre-retrieval expansion. When ready, use a fast small model to return structured JSON with entities, query variants, likely domains, and query mode.

---

## 16. Context Construction: Evidence Diversity Over Raw Top-N

The final prompt should be a curated context pack:

| Priority | Section | Content |
|----------|---------|---------|
| 1 | Direct evidence | Seed entity direct mentions |
| 2 | Related evidence | Graph-expanded associative hits |
| 3 | Approved background | Wiki articles, budgeted |
| 4 | Procedural guidance | If query asks how-to |
| 5 | Fresh / current evidence | If query asks current state |
| 6 | Historical / origin evidence | If query asks development |

Diversity rules: max duplicate turns from same conversation, max same-domain dominance unless domain-specific, ensure at least N direct seed mentions, ensure at least M graph-expanded hits, include wiki only under budget, include date span summary.

---

## 17. Retrieval Answer Contract

| Answer Type | Retrieval Needs |
|-------------|----------------|
| Identity profile | Direct mentions, relationships, role, history, current state |
| Current status | Current state, development, artifacts, uncertainty |
| Feature catalog | Broad recall, deduplication, grouping, status classification |
| Historical development | Origin, milestones, decisions, evolution |
| Technical troubleshooting | Error context, similar issues, approved procedures |
| Publication synthesis | Drafts, reviews, approved content, source material |
| Decision audit | Decision records, rationale, alternatives, outcomes |
| Procedure recommendation | Approved methods, checklists, constraints |

---

## 18. Build Order

### Phase 0 — Measurement and Trace
- Retrieval golden tests
- Retrieval trace instrumentation
- Before/after CLI or debug endpoint
- Current baseline measurements

**Deliverable:** AIP can show why the current retrieval fails.

### Phase 1 — Protocol Substrate
- Retriever protocol
- RetrievalHit / RetrievalList / RetrievalTrace
- ContextBudget
- RRF fusion service
- FTSRetriever wrapped into protocol
- VectorRetriever wrapped into protocol

**Deliverable:** Existing retrieval behaves the same or better through a unified path.

### Phase 2 — Entity-Turn Index and Coverage Repair
- entity_turn_index schema
- GraphStore methods
- Backfill from evidence_turn_ids_json
- Write during Sexton extraction
- Staleness prune
- Mention scan with type filter + alias rules
- Hub leash
- Targeted 0.5 importance edge densification

**Deliverable:** AIP can map entities to turns comprehensively without trusting every mention equally.

### Phase 3 — GraphRetriever
- EntitySeedSelector
- networkx graph builder with cache
- Direct-mention zone
- PPR expansion zone
- Hub leash and confidence/importance scoring
- Graph retrieval trace
- Conforming RetrievalList output
- RRF fusion with FTS/vector

**Deliverable:** Komal / GEF / frost / AIP feature golden queries improve in recall without unacceptable noise.

### Phase 4 — Wiki/Background Retriever
- WikiRetriever
- Domain selection from seeds + hits
- Budgeted multi-wiki injection
- Labeled background vs. evidence

**Deliverable:** AIP answers with approved synthesis as background without confusing it for evidence.

### Phase 5 — Context Packer and Answer Quality
- Context diversity rules
- Source caps
- Temporal span handling
- Direct vs. associative evidence balance
- Evidence status weighting
- Answer mode templates

**Deliverable:** More retrieved material improves answers instead of burying the question.

### Phase 6 — Later Intelligence Layers
Only after golden tests pass:
- Query rewriting with fast model
- Procedural memory retriever
- Community/domain retrieval
- Decay/consolidation
- Adaptive retriever weighting
- Model-selected retrieval plans

### Summary

| Phase | Name | Deliverables |
|-------|------|-------------|
| 0 | Measurement and Trace | Golden tests, trace instrumentation, baselines |
| 1 | Protocol Substrate | Retriever protocol, RetrievalHit, ContextBudget, RRF, FTS+Vector wrapped |
| 2 | Entity-Turn Index + Coverage | entity_turn_index, mention scan, hub leash, edge densification |
| 3 | GraphRetriever | EntitySeedSelector, PPR, direct mentions, hub control, RRF integration |
| 4 | Wiki/Background Retriever | WikiRetriever, domain selection, budgeted injection |
| 5 | Context Packer + Quality | Diversity, source caps, evidence status, answer modes |
| 6 | Later Intelligence | Query rewriting, procedural retriever, consolidation, adaptation |

---

## 19. What to Explicitly Refuse Right Now

| Item to Refuse | Rationale |
|---------------|-----------|
| Robot-specific schema | No robot exists to validate against |
| Hardware interface columns | Premature; no hardware target |
| Vector clocks | Over-engineering for current scale |
| Complex autonomy/action schemas | Speculative; procedural memory covers this |
| Agent execution expansion | Needs proven substrate first |
| Unbounded query rewriting | Costly and unvalidated |
| Auto wiki promotion from retrieval | Bypasses DEFINER sovereignty |
| Large ingestion flood | Retrieval must prove itself first |

The right future bridge is approved procedural memory, governed action plans, retrievable specifications, Vigil review, and DEFINER approval.

---

## 20. Final Cohesive Architecture

```
User query
  ↓
RetrievalQuery normalization
  ↓
Intent / query mode detection
  ↓
EntitySeedSelector
  ↓
Parallel retrievers
  ├── FTSRetriever
  ├── VectorRetriever
  ├── GraphRetriever
  ├── WikiRetriever
  └── ProceduralRetriever (later)
  ↓
RetrievalLists
  ↓
RRF fusion
  ↓
AIP-specific scoring
  ├── importance
  ├── confidence
  ├── evidence status
  ├── temporal relevance
  ├── hub penalty
  └── diversity
  ↓
ContextBudget + ContextPacker
  ↓
Prompt sections
  ├── approved background
  ├── retrieved evidence
  ├── procedural guidance
  └── trace summary (debug)
  ↓
Model synthesis
  ↓
Answer with provenance
  ↓
Optional artifact / wiki proposal
  ↓
DEFINER approval before canon
```

---

## 21. Why This Fits What You Actually Need

You are not building retrieval for a demo corpus. You are building retrieval for your life's intellectual archive. The system must handle cross-model memory, cross-year memory, cross-domain memory, approved versus speculative knowledge, current status versus historical origin, people, projects, and technologies, procedures, specs, and publication plans, research papers and external evidence. Pure FTS cannot do that. Pure vectors cannot do that. Pure graph cannot do that. AIP needs **federated retrieval under governance**. That is the correct architecture.

---

## 22. Verdict

> **Green-light GraphRAG, but build the retrieval substrate first. Do not chase the magic first. Build the plumbing.**

The correct build sequence is: goldens, trace, retriever protocol, budget, entity-turn index, mention scan with guardrails, edge densification, direct mention path, PPR path, RRF fusion, wiki background, and context packer. Then dogfood hard.

If this works, AIP stops being a system that "has a corpus" and becomes a system that **remembers**.

---

## 23. Invariants Preserved

| Invariant | Rule | Build Memo Compliance |
|-----------|------|----------------------|
| AIP-G-02 | Graph/retriever failure falls back silently to FTS5 | Protocol makes this a per-retriever contract |
| AIP-G-09 | All retrieval is local SQLite + networkx; no outbound calls | No new external dependencies |
| No auto-approve | Wiki injection is read-only; retrieval is display-only | Nothing writes corpus from retrieval path |
| DEFINER sovereignty | Threshold changes and wiki proposals are DEFINER-gated | No autonomous promotion to canon |
