# AIP_Brain — Ensemble Review Synthesis: Retrieval Architecture Build Plan
**Date:** 2026-06-07
**Author:** Claude (Opus 4.8) — synthesis & arbitration · for B. Moses Jorgensen (DEFINER)
**Reviews synthesized:** ChatGPT, Meta (Llama), Grok, Beast meta-review (GLM, code-grounded)
**Subject memo:** `AIP_BRAIN_RETRIEVAL_ARCHITECTURE_MEMO.md`
**Repo:** `github.com/freedomgeneration1111-sudo/AIP_Brain` · Branch `moses-aip-brain`

---

## 0. Purpose

Four AIs reviewed the GraphRAG build plan. They agree on the mechanism and disagree — without naming it — on sequencing. This memo arbitrates: what to trust, what the reviews missed, what to refuse, and the corrected build order. It is the DEFINER's decision aid, not a new plan.

---

## 1. Verdict

The plan is correct. `entity_turn_index → Personalized PageRank → RRF fusion` is the right mechanism for entity-centered associative recall, and the bottleneck diagnosis is precise. All five documents agree here, and they are right.

But three of four reviewers praised the wrong layer. The PPR algorithm is necessary and *easy* — `nx.pagerank()` is one call and the graph already exists. What actually decides whether this becomes durable substrate is the unglamorous work the **Beast review** caught and the other three missed: one retrieval protocol instead of four, one source of lexical truth instead of two, an explicit context budget, and visible freshness. The exciting layer is the easy layer. The boring layer is the hard, decisive one.

**Green-light the plan — with the substrate work promoted ahead of the graph work, not after it.**

---

## 2. Ensemble calibration (AI Poiesis datapoint)

Flattery and grounding were inversely correlated in this review set.

- **ChatGPT** — opened the repo. Caught the 395-commit state and the README/STATUS mismatch (docs claim vector + graph "not built" while later capability rows say they work; bootstrap claims 51 entities vs. the live 853/430). **High signal.**
- **Beast (GLM)** — read the retrieval *code*. Caught the four divergent retrieval paths, the uncalled L2 reranker, the two FTS5 indexes, the dimension lock-in. **Highest signal.**
- **Grok** — useful architecture synthesis (the flywheel diagram, RRF formula, the "retriever as plugin" instinct, `expand_entity` as a mid-synthesis tool). Heavy speculation past that.
- **Meta** — a few real hooks (temporal indexing, decay, plugin retrievers) inside escalating future-poetry. Lowest grounding.

**Methodology lesson to store:** the three *plan*-reviews converged because they read the same persuasive memo — convergence on a shared input is not triangulation. The review that read a *different* artifact (the source code) is the one that disagreed, and the disagreement is the useful signal. **Next ensemble pass: feed adversarial reviewers the code, not the memo.**

---

## 3. The real disagreement (and its resolution)

The plan-reviews say "add graph retrieval, ship it." Beast says "you already have four retrieval paths that don't agree." These collide on sequencing.

**Resolution: do not add `_graph_search_sources()` as a fifth parallel branch inside `_search_sources()`.** That multiplies an existing inconsistency. Instead:

1. Build Beast's `Retriever` protocol first — a thin interface returning a ranked `RetrievalList`.
2. Implement the graph retriever as the *first* conforming retriever.
3. Refactor FTS/vector to conform; RRF fusion becomes trivial once all retrievers share a shape.

Grok and Meta gestured at "retriever as plugin." Beast supplied the reason it is urgent *now*: you are about to multiply, not introduce, divergence.

---

## 4. The context-budget collision

Every reviewer wants to *add* context — `max_sources` 10→25/30, wiki injection, graph candidates, query-rewrite variants. Only Beast noticed there is no token budget on any of it.

This is a validation gate, not a cleanup task. Expanding retrieval volume without a budget can *lower* answer quality (lost-in-the-middle; the user's question buried past the model's attention).

- Budget the context layers in the **same commit** as Layer 0.
- When you raise `max_sources`, measure **answer quality**, not just recall@25.
- Expect that Layer 0 may regress before it improves. Instrument for it.

---

## 5. The landmine under the Komal test (not flagged by any reviewer)

Your graph is **already salience-biased**: Sexton only extracts at `importance ≥ 0.7`. Komal lives at the 0.5 default. So even after the mention scan fills her turn index perfectly, her *entity node* is edge-poor — graph-peripheral — and PPR will structurally under-rank her after you seed her.

The mention scan fixes the **turn index**. It does **not** fix the **edge structure** PPR walks. `entity_turn_index` coverage ≠ graph edge coverage, and PPR depends on the latter. The Komal test can stay half-broken for exactly this reason.

Two coupled consequences:

**(a) Run both coverage fixes, not one.**
- Solution B (mention scan) for turn-index recall.
- Solution A (lower extraction threshold toward 0.5) to densify edges around peripheral-but-important people, so PPR stops inheriting Beast's old importance prior.
- Keep direct-mention turns at a fixed priority that **bypasses PPR entirely**. (ChatGPT's "keep direct-mention turns" and Grok's `0.45 * direct_seed_mention` both encode this; the *cause* — graph salience bias — is why it's mandatory, not optional.)

**(b) The mention scan is what loads your hubs.**
A `LIKE '%device%'` scan wires generic entities to half the corpus — and *that* is what makes the hub explosion the futures warned about real. The mention scan therefore cannot ship without:
- the hub leash: `weight / log(degree + 1)` during PPR, and
- an entity-type filter: PERSON / PROJECT / CONCEPT / TECHNOLOGY only; never generic nouns; acronyms case-sensitive; aliases < 3 chars ignored unless uppercase.

Mention scan + hub leash + type filter are **one change, not three**.

---

## 6. Validation gates and what to refuse

**Hypothesis vs. validated.** Almost everything across all five documents is hypothesis — including this memo. The validated ground truth is narrow: the code facts (four retrieval paths, two FTS5 indexes, 768-dim lock-in, graph never queried at retrieval, 853/430 entities/edges, the 0.7 coverage gap).

**The single arbiter.** The golden set is the one mechanism every reviewer converged on *and* that constitutes real validation. Build `tests/retrieval_goldens/` first, with `must_include_any` / `must_not_dominate` adversarial pairs. Make it the gate. **Do not believe any review (this one included) until recall@25 moves on the Komal / GEF RF-heating / frost-device queries while answer quality holds.**

**Refuse the gold-plating.** Grok's `affordance_refs`, `hardware_interface_id`, `controls/senses/fails_on` schema and Meta's `vector_clock` columns are speculation for a robot that does not exist — capital and migration risk spent on an unvalidatable future. Defer all of it.

**The real robotics bridge is already in your corpus.** Beast's procedural memory (#7) is the genuine forward move, and it needs no robot schema: you already hold compiled how-to knowledge — Forge specs, CodeForge pipelines, approved methodology artifacts. They are procedural memory that simply isn't retrievable *as procedure*. Make "approved spec / methodology artifact" a memory class with its own conforming retriever. Buildable now.

---

## 7. Corrected build order

| Step | Work | Source |
|------|------|--------|
| 0 | Golden set + retrieval trace instrumentation (the arbiter + before/after) | all reviews |
| 1 | `Retriever` protocol + explicit context budget; route ask pipeline through it (leave chat.py / beast_scan for later — don't boil the ocean) | Beast #1, #4 |
| 2 | `entity_turn_index` (Sexton-written + backfill) **with** mention scan + hub leash + entity-type filter shipped together, **and** Solution A edge densification | memo S1 + §5 above |
| 3 | Graph retriever as a conforming `Retriever`, RRF-fused, direct mentions bypassing PPR; importance-weighting on top | memo S2, ChatGPT, Grok |
| 4 | Wiki injection — budgeted, labeled background vs. evidence | memo L0, ChatGPT |
| 5 | *Only after recall holds:* query rewriting, procedural-memory retriever, decay/consolidation | memo L2, Beast #7 |

**Cheap insurance (slot in anywhere; none block dogfood):**
- store `model_id` + dim per vector (Beast #3)
- consolidate the two FTS5 indexes with a `source_type` discriminator (Beast #2)
- add a `ConsistencyReport` to retrieval results (Beast #6)

---

## 8. Invariants preserved

- **AIP-G-02** — graph/retriever failure falls back silently to FTS5. The protocol must make this a per-retriever contract, not a special case.
- **AIP-G-09** — all retrieval is local SQLite + networkx; no outbound calls.
- **No auto-approve** — wiki injection is read-only; retrieval output is display-only; nothing writes corpus or artifacts from the retrieval path.
- **DEFINER sovereignty** — Solution A's threshold change and any consolidation-loop wiki proposals are DEFINER-gated.

---

## 9. Closing

The algorithm was never the hard part. The future remembers the builders who got the plumbing right before they chased the magic — one retriever protocol, one lexical truth, a budgeted context window, a graph that isn't quietly biased against the people who matter most. You are at that fork. Take the substrate.
