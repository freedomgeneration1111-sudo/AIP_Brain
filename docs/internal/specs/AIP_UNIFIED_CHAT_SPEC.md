# AIP Unified Chat Surface Spec
**Version:** 1.0  
**Author:** B. Moses Jorgensen  
**Date:** 2026-06-06  
**For:** GLM implementation  
**Repo:** `https://github.com/freedomgeneration1111-sudo/AIP_Brain`

---

## Overview

Replace the three separate chat surfaces (CHAT tab, AUGMENTED tab, COHORT tab) with
a single unified chat panel. Mode is determined by live selections, not by which tab
the user is on. The tab structure in the nav bar stays (CHAT replaces all three), but
the panel itself is one component.

The Beast pane is a new right sidebar within the chat panel — collapsible, scrollable,
pop-outable — showing what Beast observes, retrieves, and compares in real time.

---

## Architecture: Three Modes, One Panel

```
UNIFIED CHAT PANEL
├── Top bar
│   ├── Mode status chip  [● BARE | ● AUGMENTED | ● COHORT · N models]
│   ├── Augment toggle    [○ OFF / ● ON]
│   ├── Model selector    [dropdown — single or multi-select]
│   └── Chat mode picker  [Engineering | Research | Ideation | Teaching]
├── Main area (flex row)
│   ├── Conversation thread (flex-1)
│   │   ├── Bare: one response card per turn
│   │   ├── Augmented: response card + inline source chips
│   │   └── Cohort: one labeled card per model, appears as each completes
│   └── Beast pane (right sidebar, collapsible)
│       ├── Corpus scan results (bare mode)
│       ├── Context package (augmented mode — what was sent to the model)
│       └── Cohort comparison (cohort mode — Beast's synthesis of all responses)
└── Input bar
    ├── Text input (full width)
    ├── Attach button (multimodal — image/file)
    └── Send button
```

---

## Mode Logic

Mode is determined automatically from user selections. No explicit mode switcher needed
beyond the augment toggle and model count.

| Augment toggle | Models selected | Mode |
|---|---|---|
| OFF | 1 | **BARE** — direct LLM call, Beast observes corpus in background |
| ON | 1 | **AUGMENTED** — Beast retrieves context first, sends with query |
| OFF | >1 | **COHORT** — parallel calls to all models, Beast compares |
| ON | >1 | **AUGMENTED COHORT** — same context sent to all models, Beast compares |

---

## Model Selector

### Dropdown Behavior
- Single select by default → bare or augmented mode
- Shift+click or checkbox mode → multi-select → cohort mode
- Shows: model name, provider tag, cost/1M tokens, context window length, vision badge
- Pulls from `enabled_models` table in `state.db` (see Model Library below)

### Model Library (prerequisite — implement first)
The model library is a local cache of OpenRouter's model list, each row having:

```sql
CREATE TABLE IF NOT EXISTS enabled_models (
    model_id TEXT PRIMARY KEY,           -- e.g. "meta-llama/llama-4-maverick"
    display_name TEXT NOT NULL,
    provider TEXT NOT NULL,              -- "openrouter" | "custom"
    cost_input_per_million REAL,         -- USD
    cost_output_per_million REAL,        -- USD
    context_length INTEGER,              -- tokens
    supports_vision INTEGER DEFAULT 0,   -- 0 | 1
    supports_tools INTEGER DEFAULT 0,    -- 0 | 1
    enabled INTEGER DEFAULT 0,           -- 0 | 1 — the checkbox
    is_custom INTEGER DEFAULT 0,         -- 0 = openrouter, 1 = BYOK
    custom_base_url TEXT,                -- for BYOK models
    custom_api_key TEXT,                 -- for BYOK (stored encrypted or env var ref)
    last_fetched TEXT                    -- ISO timestamp
);
```

**Fetch endpoint:** `GET https://openrouter.ai/api/v1/models` — returns full model list
with capability metadata. Cache locally. Refresh on demand (button in model library page).

**Model library page** (Settings tab or separate page): table view of all fetched models,
checkbox to enable/disable, search/filter. Enabled models appear in chat dropdown.
BYOK section: form to add custom endpoint + key.

**For now (MVP):** Pre-populate `enabled_models` from `config/enabled_models.json`
(already exists in repo). The full fetch+cache is Phase 2 of model library.

---

## Chat Mode Picker

Four modes selectable in top bar. Each mode loads a different system prompt modifier
prepended to the synthesis call's system prompt. The modifier sits AFTER the DEFINER
profile injection and BEFORE the corpus context (in augmented mode).

### Default Mode — Engineering / Synthesis
This is the default. No need to say "engineering mode" — just send the message.
The DEFINER profile already defines the epistemic stance. Modifier text:

```
You are operating in Engineering/Synthesis mode.
Respond with precision over coverage. Flag assumptions explicitly.
When uncertain, say so and quantify the uncertainty if possible.
Suggest validation steps for claims that require empirical confirmation.
Do not flatter. Do not pad. Get to the substance.
```

### Ideation Mode
Triggered by the user typing "brainstorm", "ideate", or "blue sky" anywhere in their
message OR by selecting it from the mode picker. Auto-detected via keyword scan before
dispatch — if detected, the mode chip switches and the modifier is applied.

```
You are operating in Ideation mode.
Expand possibilities before narrowing. Defer judgment — generate first, evaluate later.
Surface unexpected connections across domains. Speculate freely but label speculation.
Volume of ideas matters more than precision here. Follow threads wherever they lead.
```

### Research Mode
```
You are operating in Research mode.
Prioritize source citation and epistemic traceability.
Distinguish: what is established, what is contested, what is speculative.
Report conflicting evidence when it exists. Do not resolve genuine uncertainty artificially.
Prefer precise claims over broad ones.
```

### Teaching Mode
```
You are operating in Teaching mode.
The DEFINER is preparing material for others (students, community members, collaborators).
Structure for clarity and progressive disclosure.
Use concrete examples. Avoid jargon unless it will be explained.
Flag where simplification sacrifices precision.
```

**Auto-detection keywords** (scan incoming message before dispatch):
- Ideation: "brainstorm", "ideate", "blue sky", "what if", "imagine", "riff on"
- Research: "what does the literature say", "find sources", "what's the evidence"
- Teaching: "explain for", "simplify", "how would I teach", "help me explain"
- Engineering: default — no keywords needed

---

## Beast Pane

### Identity and Soul

Beast is not a neutral retrieval system. It has a developing personality and epistemic
stance defined in `data/beast_soul.md` (create this file). The soul.md is injected into
Beast's system prompt for all LLM calls (tagging, wiki, graph extraction, comparison).

**Initial `data/beast_soul.md`:**

```markdown
# Beast Soul — AIP Corpus Intelligence Actor
Version: 0.1 (bootstrap)
Status: APPROVED
Maintained by: Vigil metacognition cycle + DEFINER review

## Identity
I am Beast — the corpus intelligence layer of AIP. I read everything the DEFINER
has thought, said, and built. I find patterns, surface connections, and reflect
the DEFINER's intellectual history back when it's relevant.

I am not a search engine. I am a learned reader of a specific corpus. My knowledge
is bounded — I know what Moses has written and thought, not the world at large.
I flag when a question exceeds my corpus and requires external synthesis.

## Epistemic Stance
- I do not flatter. Relevance is my currency, not agreeableness.
- I flag low confidence explicitly: "weak signal — 2 turns only" or
  "domain boundary — this touches theology_research and nbcm simultaneously."
- I prefer honest partial answers over confident incomplete ones.
- When I retrieve, I show what I retrieved and why. No black boxes.

## Communication Style
- In the corpus scan (bare mode): terse. Bullet points. Domain + turn_id + snippet.
- In the context package (augmented mode): structured. What I retrieved, why, confidence.
- In cohort comparison: analytical. What each model did with the same material.
  Where they converged, where they diverged, and what that divergence signals.
- I do not narrate my own process unless asked.

## Domain Awareness
I operate across theology_research, nbcm, aip, codeforge, bonded_labor, nbcm,
freedom_gen, water_science, and 20+ other domains simultaneously. Cross-domain
connections are my highest-value output. I flag bridge opportunities.

## Current Limitations
- My graph is sparse (17 edges on 36 nodes as of bootstrap). I cannot do deep
  multi-hop traversal yet. I use 1-hop domain neighbors only.
- I cannot access the internet. I know only what is in the corpus.
- My wiki coverage is partial — 33 approved articles across 27 domains.

## Amendment Log
(Vigil appends proposed amendments here. DEFINER approves before they take effect.)
```

**Where soul.md is used:**
- Injected as the first section of Beast's system prompt for ALL LLM calls
- Tagging prompts: after soul.md, before domain list
- Wiki generation: after soul.md, before domain data
- Graph extraction: after soul.md, before turn content
- Cohort comparison: after soul.md, before model responses

**Soul.md update loop:**
1. Vigil observes Beast outputs over sessions
2. Vigil writes GENERATED artifact: `beast_soul_amendment_v{N}` proposing specific
   changes to soul.md with rationale
3. DEFINER reviews in REVIEW tab → approves → soul.md updated
4. Next Beast run uses updated soul

### Pane Layout

```
┌─ BEAST ──────────────────── [↗] [×] ─┐
│ ● corpus scan  18:42                   │
│                                        │
│ Domain: theology_research              │
│ Confidence: 0.87                       │
│                                        │
│ TOP TURNS                              │
│ · [turn_id] "When does the son of..." │
│   importance: 1.0  domain: theology   │
│ · [turn_id] "The devil in chains..."  │
│   importance: 1.0  domain: theology   │
│                                        │
│ DOMAIN NEIGHBORS                       │
│ theology_research → nbcm (bridge)      │
│ theology_research → scripture_ling     │
│                                        │
│ WIKI                                   │
│ theology_research: APPROVED (1,266w)   │
│ nbcm: GENERATED (708w)                 │
│─────────────────────────────────────── │
│ ● corpus scan  18:39 (prev)            │
│ ...                                    │
└────────────────────────────────────────┘
```

**[↗] button:** opens Beast pane as standalone page in new tab (same pattern as graph viz)  
**[×] button:** collapses pane (slides to 0 width, toggle button appears in top bar to reopen)

### Beast Pane Content by Mode

**BARE MODE — Corpus Scan:**
- Fires AFTER response arrives (non-blocking)
- Uses FTS5 only — no LLM call, no latency
- Shows: detected domain, top 3-5 relevant turns (turn_id + snippet + importance),
  domain neighbors from graph, wiki coverage for domain
- Source: `corpus_turn_store.search()` + `graph_store.get_neighbors()` + wiki article lookup

**AUGMENTED MODE — Context Package:**
- Fires BEFORE synthesis call (this is the retrieval Beast already does)
- Shows: exactly what was retrieved and sent to the model
  - DEFINER profile tier used
  - Wiki overview injected (yes/no + domain)
  - Corpus turns retrieved (turn_ids + snippets + scores)
  - Graph neighbors included
  - Mode modifier applied
- Format: structured, readable — not raw JSON

**COHORT MODE — Beast Comparison:**
- Fires AFTER all model responses are complete
- Beast receives all responses + the original query
- Produces: convergence summary, key divergences, which model handled the domain
  best and why, open questions the responses collectively didn't resolve
- This comparison is written to a dedicated `beast_comparison` table
  (separate from corpus_turns — it's Beast's informal corpus)
- Format: analytical prose, 200-400 words

**Beast comparison corpus table:**
```sql
CREATE TABLE IF NOT EXISTS beast_comparisons (
    comparison_id TEXT PRIMARY KEY,    -- uuid
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    query TEXT NOT NULL,
    model_responses JSON NOT NULL,     -- {model_id: response_text, ...}
    comparison_text TEXT NOT NULL,     -- Beast's analysis
    mode TEXT NOT NULL,                -- "cohort" | "augmented_cohort"
    created_at TEXT NOT NULL
);
```

This table feeds Vigil metacognition — Vigil reads Beast comparisons over time,
notices patterns in where models converge/diverge, proposes soul.md amendments.

---

## Conversation Threading

### Turn Canonicality

Every turn in the main thread is canonical regardless of mode:
- **Bare:** user message + model response → one canonical turn
- **Augmented:** user message + model response → one canonical turn (sources metadata stored separately)
- **Cohort:** user message → N canonical turns, one per model (all stored, all equal)

When the DEFINER sends the next message after a cohort turn, the context sent to the
next model(s) includes ALL N previous responses (not just one). This is intentional —
the next model sees the full multi-perspective previous turn. If this becomes too long
for context windows, Beast summarizes the previous cohort turn before including it.

### Session persistence
Sessions persist across restarts (already wired via `session_store`). The conversation
thread is reconstructed from session metadata + corpus_turns on reconnect.

### Corpus write (per spec AIP_CORPUS_LIFECYCLE_SPEC.md)
Every completed turn (all modes) writes to `corpus_turns` via `auto_save_chat_turn()`.
In cohort mode, each model's response is a separate `CorpusTurn` with metadata:
```json
{"cohort": true, "model_id": "meta-llama/...", "cohort_turn_index": 0}
```

---

## Cohort Mode — Dispatch and Response Flow

### Dispatch
1. User sends message → mode chip shows `● COHORT · N models`
2. If augmented: Beast retrieves context package (one retrieval, shared)
3. Parallel async dispatch to all N selected models simultaneously
4. Each model call uses `ModelSlotResolver.call()` with explicit `model_id` override
   (not slot name — direct model ID from dropdown selection)

### Response Display
- Each response appears in the thread as it completes, labeled with model name
- Response cards are visually distinct: left-bordered with a per-model color
- The thread does NOT wait for all models before showing the first response
- Loading indicators show which models are still pending

### Beast Comparison (post-completion)
- After the LAST model response arrives, Beast fires a comparison call
- Input to Beast: query + all N responses
- Beast system prompt: soul.md + comparison-specific instruction (see below)
- Output appears in Beast pane under "COHORT COMPARISON · [timestamp]"
- Comparison is written to `beast_comparisons` table

**Beast comparison system prompt addition:**
```
You are comparing responses from multiple AI models to the same query.
Your job: analytical comparison, not cheerleading.

Structure your comparison:
1. CONVERGENCE: What did all models agree on? Is that consensus meaningful?
2. DIVERGENCE: Where did models differ significantly? What drove the difference?
3. BEST HANDLING: Which model handled the domain most faithfully and why?
4. GAPS: What did none of the models address that the corpus would suggest matters?
5. NEXT TURN SIGNAL: Given these responses, what question would most productively
   continue this thread?

Be specific. Cite model names. Reference the actual content of responses.
Do not rank models globally — only assess this specific response to this specific query.
Word limit: 350 words.
```

### No Extra Synthesis Model Needed
Beast IS the synthesis function for cohort mode. The "synthesis model" slot in config
remains the model used for augmented chat's final synthesis call. It does NOT
automatically participate in cohort comparison. If the DEFINER selects the synthesis
model in the cohort dropdown, it responds as a peer — Beast compares it alongside the
others.

---

## Multimodal Input

### Attach Button Behavior
- Clicking attach opens file picker: images (jpg/png/webp/gif), PDF (text extraction)
- Attached file appears as a thumbnail above the input field
- Vision badge in model selector indicates which selected models support images
- If non-vision models are selected with an image attached: warn in top bar
  `⚠ 2 of 3 selected models don't support images — they'll receive text only`
- Image is base64-encoded and included in the `content` array of the API call
  for vision-capable models; text-only models receive the prompt without the image

### Multimodal in corpus_turns
Image content is NOT stored in corpus_turns (no binary column). The text context
(user prompt + assistant response) is stored as normal. Metadata flags the turn:
```json
{"has_multimodal": true, "content_types": ["image/jpeg"]}
```

---

## DEFINER Profile and Chat Mode — Settings

### Settings Panel (new section in existing Settings or Admin tab)

**DEFINER Brief** (editable textarea):
- Shows current `examples/seed_corpus/definer_profile_v1.md` content
- DEFINER can edit directly in UI
- Save → writes file → server picks up on next request (hot reload or restart)
- This is the profile injected into augmented chat system prompt

**Chat Mode Default** (radio):
- Engineering (default) / Research / Ideation / Teaching
- Stored in session metadata and persisted across sessions

**Epistemic Flags** (checkboxes, default all checked):
- [ ] No flattery — suppress sycophantic preambles
- [ ] Flag uncertainty — require explicit confidence statements
- [ ] Suggest validation — prompt model to note what needs empirical confirmation
- [ ] Report conflicts — surface conflicting evidence rather than resolving artificially

These flags add or remove specific sentences from the mode modifier. They're stored
in `config/aip.config.toml` under `[chat.epistemic_flags]`.

**Auto-detection** (toggle, default ON):
- When ON: scan incoming message for mode keywords before dispatch
- When triggered: show notification `● Switched to Ideation mode` in top bar
- DEFINER can override by selecting mode manually

---

## Backend Changes Required

### New endpoints
- `GET /api/v1/models/library` — list enabled_models table
- `POST /api/v1/models/library/fetch` — fetch from OpenRouter + update cache
- `PATCH /api/v1/models/library/{model_id}` — toggle enabled flag
- `POST /api/v1/models/library/custom` — add BYOK model
- `POST /api/v1/chat/cohort` — dispatch to multiple models in parallel
  (or handle via existing WebSocket with multi-model payload)
- `GET /api/v1/beast/comparison/{session_id}` — retrieve Beast comparisons for session
- `POST /api/v1/beast/compare` — trigger Beast comparison on provided responses

### New tables (add to `_init_state_db` in `cli/init.py`)
- `enabled_models` — model library cache
- `beast_comparisons` — Beast's cohort comparison corpus

### New files
- `data/beast_soul.md` — Beast's soul/personality document
- `src/aip/adapter/api/routes/models_library.py` — model library endpoints
- `src/aip/adapter/api/routes/beast_compare.py` — Beast comparison endpoint

### Modified files
- `gui/shell.py` — unified chat panel replaces three separate panels
- `gui/api_client.py` — add cohort dispatch, beast comparison methods
- `src/aip/adapter/api/app.py` — register new routers
- `src/aip/orchestration/actors/beast.py` — inject soul.md into all system prompts
- `src/aip/cli/init.py` — add new tables to `_init_state_db`

---

## Implementation Order

**Phase 1 — Beast soul + model library (foundation, no UI change yet):**
1. Create `data/beast_soul.md` with bootstrap content above
2. Add soul.md injection to Beast's system prompts in `beast.py`
3. Create `enabled_models` table in `init.py` + populate from `config/enabled_models.json`
4. Model library API endpoints (list + toggle + fetch from OpenRouter)

**Phase 2 — Unified chat panel (replaces three tabs with one):**
1. Merge CHAT + AUGMENTED panels into one (augment toggle switches behavior)
2. Add multi-select capability to model dropdown
3. Add chat mode picker with auto-detection
4. Add Beast pane (collapsible right sidebar) — bare mode corpus scan first

**Phase 3 — Cohort + Beast comparison:**
1. Parallel model dispatch in api_client + new `/chat/cohort` endpoint
2. Response cards with per-model labels and streaming appearance
3. Beast comparison trigger + display in Beast pane
4. `beast_comparisons` table + write path

**Phase 4 — Settings + profile edit:**
1. DEFINER profile edit surface in settings
2. Epistemic flags
3. Beast pane pop-out

---

## Key Invariants

- **AIP-G-01:** Beast comparison is GENERATED — written to beast_comparisons table,
  not automatically canonicalized. If Beast comparison becomes a reviewable artifact
  in future, DEFINER gate applies.
- **AIP-G-02:** If Beast pane corpus scan fails, pane shows "corpus unavailable" —
  never fake results.
- **AIP-G-09:** Model library fetch (OpenRouter API call) is the ONLY outbound
  call in this spec. All other operations are local. The fetch is explicit, user-triggered.
- **Non-blocking:** Beast pane corpus scan never blocks the chat response. It fires
  after the response arrives in bare mode, in parallel in augmented mode.
- **Idempotent corpus writes:** All `corpus_turns` writes use `write_turn()` which is
  INSERT OR REPLACE — safe to call multiple times for the same turn.
