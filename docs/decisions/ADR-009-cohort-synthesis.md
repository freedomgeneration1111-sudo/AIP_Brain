# ADR-009: Cohort Synthesis Architecture

**Date:** 2026-06-05
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

---

## Context

AI Poiesis methodology involves running the same prompt through multiple
frontier models simultaneously and synthesizing the results. The DEFINER
has been doing this manually for over a year: copy prompt, paste to each
interface (Claude, DeepSeek, GPT, Grok, Gemini), read each response,
mentally synthesize. This works but the friction prevents systematic use
and the results are not ingested into the corpus.

The value is not in any single model's answer. The value is in the
intersection and divergence of multiple models' answers, filtered through
the DEFINER's accumulated knowledge, delivered as a synthesis that
identifies where models converge (highest confidence), where they diverge
(productive tension), and what the corpus suggests they're all missing.

OpenRouter makes this achievable via a single API endpoint across all
models.

## Decision

Cohort Synthesis is a first-class AIP feature accessible via a dedicated
**COHORT** tab in the GUI.

### User Flow

1. DEFINER types prompt in COHORT tab
2. Selects up to 5 models from OpenRouter model picker (with cost/token
   indicators per model)
3. Reviews estimated cost: "~$0.04 across 5 models" shown before sending
4. Clicks "Ask Cohort"
5. AIP fans the prompt out **simultaneously** to all selected models via
   OpenRouter async calls
6. Progress shown per model as responses arrive (streaming or polling)
7. Each response **immediately ingested** as a corpus_turn tagged with:
   - `source_model`: the specific model name
   - `turn_type`: "cohort_response"
   - `cohort_id`: UUID shared across all responses in this cohort call
   - `primary_domain`: detected by Beast (same real-time tagging loop)
   - `importance_score`: set initially at 0.7, Beast re-scores later
8. After all responses received (or 60-second timeout for stragglers),
   synthesis model runs cohort synthesis call
9. Cohort synthesis response displayed and also ingested as corpus_turn
   with `turn_type: "cohort_synthesis"`

### Synthesis Prompt Structure

```
[DEFINER PROFILE]
[DOMAIN WIKI OVERVIEW for detected domain]
[GRAPH CONTEXT: top 3 neighbor domains]
[TOP 5 RELEVANT CORPUS TURNS by FTS5+vector]

You are synthesizing responses from N frontier AI models to the DEFINER's
question. The DEFINER has extensive domain expertise.

QUESTION: {definer_prompt}

MODEL RESPONSES:
=== {model_1_name} ===
{model_1_response}

=== {model_2_name} ===
{model_2_response}

[...up to 5]

Synthesize. Identify and address:
1. Where all models converge — highest confidence claims
2. Where models diverge — what each position implies
3. What the DEFINER's corpus context suggests models are missing or
   have wrong
4. Integrated synthesis incorporating all of the above

Cite models inline as [{model_name}]. Cite corpus turns as [corpus:{turn_id}].
Do not simply summarize each model in turn. Synthesize across them.
```

### Why Up to 5 Models

The DEFINER's established practice uses 5-6 models. Five is the pragmatic
ceiling: context window cost for synthesis grows with N models, rate limits
on OpenRouter, and synthesis quality has a practical ceiling — beyond 5
models, marginal information gain from additional models decreases while
synthesis complexity increases.

The DEFINER selects which 5 based on the question type:
- Technical/scientific: Claude + DeepSeek R1 + GPT-OSS + Gemini + Nemotron
- Theological: Claude + DeepSeek + GLM + Gemini + GPT-OSS
- Policy/social: Claude + GPT-4o + Gemini + Grok + DeepSeek

### Corpus Enrichment

Every cohort call enriches the corpus with N+1 turns (N model responses
+ 1 synthesis). Over time, the DEFINER's corpus contains the full record
of multi-model deliberation on every significant question. This is the
long-term value: future context assembly can retrieve prior cohort
syntheses as high-quality corpus turns, creating compounding returns.

---

## Implementation Notes

**Fan-out**: Use `asyncio.gather()` or equivalent for true parallel calls.
Do not call models sequentially — 60 seconds for 5 sequential calls vs
~15 seconds for parallel is unacceptable UX.

**Timeout handling**: If any model doesn't respond within 60 seconds,
mark it as timed out, proceed with responses received, note the timeout
in synthesis context.

**Streaming**: Per-model streaming improves perceived latency. Each model
response appears in its own panel as it streams. Synthesis begins after
all panels complete (or timeout).

**Cost display**: Before sending, compute estimated cost from OpenRouter's
per-model pricing. Show as "~$X.XX across N models." This is a courtesy
to the DEFINER, not a hard gate.

**Model picker**: Dropdown with OpenRouter's available models. Show
context window size, cost per 1M tokens, and known strengths. Persist
DEFINER's last-used model selection as default.

**Backend route**: `POST /api/v1/cohort/synthesize`
Request: `{prompt, model_ids: [...], synthesis_model_id}`
Returns: `{cohort_id, responses: [{model, content, turn_id}], synthesis: {content, turn_id}}`

---

## Alternatives Considered

**Sequential calls** — rejected. Destroys the time advantage of
parallelism. 5 sequential 10-second calls = 50 seconds. 5 parallel = 15.

**Auto-select models** — rejected. DEFINER sovereignty requires explicit
model selection. Auto-selection would hide which models were consulted
and undermine the DEFINER's ability to evaluate synthesis quality.

**No synthesis step** — rejected. Displaying 5 raw responses without
synthesis puts the synthesis burden back on the DEFINER mentally, which
is exactly what we're automating.

**Separate UI from main chat** — accepted. COHORT is a distinct tab.
The interaction pattern is fundamentally different (you're asking an
ensemble, not a single model), and the display (N response panels +
synthesis) doesn't fit the linear chat layout.

---

## Consequences

- Cohort synthesis is the most expensive feature in AIP (N model calls
  + 1 synthesis call per cohort query). Cost awareness UI is mandatory.
- Fan-out requires async handling in the FastAPI backend. OpenRouter
  supports async calls; implementation is straightforward.
- The cohort_id field in corpus_turns allows future queries like "show
  me all cohort sessions where DeepSeek diverged from Claude." This is
  a planned Phase 4 feature in the corpus browser.
- Synthesis quality depends on the synthesis model's ability to handle
  large context windows. DeepSeek R1 or Claude at a high context window
  is recommended for the synthesis slot.

## Related

- ADR-008: Semantic Session Context (cohort turns enter same real-time
  tagging loop; cohort_id is additional metadata)
- ADR-010: Browser Extension Ingest (deferred; extension could capture
  cohort responses from external interfaces in the future)
- ROADMAP.md: Phase 3 (Cohort Synthesis feature)
- src/aip/adapter/api/routes/cohort.py (new route file)
- gui/main.py: COHORT tab
