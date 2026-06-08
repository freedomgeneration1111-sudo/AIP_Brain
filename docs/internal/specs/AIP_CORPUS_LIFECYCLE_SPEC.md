# AIP Corpus Lifecycle Spec
**Version:** 1.0  
**Author:** B. Moses Jorgensen  
**Date:** 2026-06-06  
**For:** GLM implementation  
**Repo:** `https://github.com/freedomgeneration1111-sudo/AIP_Brain`

---

## Purpose

This spec defines the complete lifecycle of a corpus turn from ingestion through tagging, wiki generation, and graph extraction — across all five entry surfaces. It identifies the current wiring gaps and specifies the exact fixes required for each gap.

---

## Current State Summary (verified by code review)

### Two Separate Ingestion Pipelines — They Do Not Meet

**Pipeline A — Legacy artifact/lexical pipeline** (`src/aip/orchestration/ingestion/pipeline.py`):
- Called by: `auto_save_chat_turn` (chat WebSocket), CLI `aip ingest`, `watch_import.sh`
- Writes to: `artifacts` table, `lexical.db` (FTS5), `vectors.db` (optional)
- Does NOT write to: `corpus_turns` table
- Result: turns are searchable via FTS5 artifact search but are NOT in the Sexton/Beast tagging pipeline

**Pipeline B — Corpus turn pipeline** (`src/aip/adapter/corpus_turn_store.py`, `src/aip/cli/corpus.py`):
- Called by: `aip corpus ingest` CLI only
- Writes to: `corpus_turns` table + `corpus_turns_fts` in `state.db`
- Result: turns ARE in the Sexton tagging pipeline (Sexton reads `corpus_turns` for `get_untagged_turns()`)
- NOT called by: chat auto-save, API ingest endpoints, watch_import.sh

**Gap:** Chat turns, API-ingested turns, and watch_import turns never reach `corpus_turns`. They go into `artifacts` only. Sexton only tags what's in `corpus_turns`. Therefore new chat turns are never tagged, never get wiki coverage, never appear in graph extraction.

### Sexton Startup Run — Fires But Hits 429 Rate Limit

Sexton startup run is wired (commits `9acac7d`). On startup it runs wiki generation immediately and hits OpenRouter 429 (too many requests) on the 4th domain. Wiki gen for 3 domains succeeds on startup. Graph extraction runs but fails JSON parse on all turns (Sexton sends extraction prompt, model returns non-JSON). This is a prompt engineering issue, not a wiring issue.

### Vigil Startup Run — Crashes

Vigil `run_cycle()` crashes with `Logger._log() got an unexpected keyword argument 'status'` because `vigil.py` uses stdlib `logging.getLogger` which doesn't accept kwargs. Fixed in `vigil.py` (structlog `get_logger`). Confirmed by test output: `vigil_eval_complete status=quality_evaluation_pending` now logs correctly.

### Sexton and Vigil "Never" in RECENT ACTOR ACTIVITY

The RECENT ACTOR ACTIVITY section reads `container.sexton` (the old failure-classifier Sexton) for last_cycle_time, not `container.sexton_actor` (the new full-maintenance Sexton). The sexton_actor runs every 300s but its timestamps never appear in the STATUS panel. This is a display-only bug in `src/aip/adapter/api/routes/actors.py`.

### Tagged: 0 in STATUS Panel

`corpus_turn_store.total_turns()` used the shared `self._conn` and closed it in `finally`, causing a "no active connection" race on the first request after startup (concurrent sexton startup run). Fixed in `corpus_turn_store.py` (dedicated per-call connection in `total_turns()`). Status shows 2,766 tagged correctly when the race is won; shows 0 when it's lost.

---

## The Five Entry Surfaces

### Surface A — Bare Chat Prompt (CHAT tab, no augmentation)

**Current flow:**
1. User sends message → WebSocket handler → `ModelSlotResolver.call(slot="synthesis")` → response sent
2. `auto_save_chat_turn()` fires as background task
3. `ingest_conversation()` writes to `artifacts` + `lexical.db`
4. **MISSING:** turn never written to `corpus_turns`

**Required fix:**
After `ingest_conversation()` succeeds in `auto_save_chat_turn()`, also write the turn to `corpus_turn_store` via `CorpusTurnStore.upsert_turn()`. The turn enters with `primary_domain=None` and `tagging_version=0` — Sexton picks it up in the next vigil cycle and tags it.

**Files to change:**
- `src/aip/adapter/api/routes/ingest.py` — `auto_save_chat_turn()`: add `corpus_turn_store=container.corpus_turn_store` parameter; after `ingest_conversation()` call, build a `CorpusTurn` from the turn pair and call `await corpus_turn_store.upsert_turn(turn)`
- `src/aip/adapter/api/routes/chat.py` — pass `container` (already passed) so `corpus_turn_store` is accessible

**CorpusTurn fields to populate:**
```python
CorpusTurn(
    turn_id=make_turn_id(session_id, turn_index),
    conversation_id=session_id,
    conversation_name=f"Chat Session {session_id[:8]}",
    turn_index=turn_index,  # increment per session
    source_model="aip_chat",
    source_account="local",
    user_text=user_message,
    assistant_text=assistant_response,
    thinking_text=None,
    primary_domain=None,       # Sexton will tag
    tagging_version=0,         # marks as untagged
    importance=0.5,            # default mid importance
    searchable_text=f"{user_message}\n\n{assistant_response}",
)
```

---

### Surface B — Multimodal Chat Prompt

**Current flow:** Same as Surface A. Image/file content is in the message but the text content of the turn is saved. The multimodal content (image bytes) is not stored in `corpus_turns` (no binary column).

**Required fix:** Same as Surface A — write the text portions to `corpus_turns`. Add a `has_multimodal=True` flag in metadata. The image itself is not stored; the text context is what matters for tagging and retrieval.

**Additional field:**
```python
metadata={"has_multimodal": True, "content_types": ["image/jpeg"]}  # store in corpus_turns.metadata_json
```

No schema change needed — `corpus_turns` has a `metadata_json` column.

---

### Surface C — Augmented Chat Prompt (AUGMENTED tab)

**Current flow:** Same as Surface A/B plus retrieval. The auto-save fires identically. The retrieved source context is NOT saved as part of the turn — only user_message + assistant_response.

**Required fix:** Same as Surface A. Additionally, save the `source_ids` used in retrieval to `metadata_json` so Sexton can later identify which turns were used as sources for this response (useful for Vigil quality evaluation).

**Additional field:**
```python
metadata={
    "augmented": True,
    "source_turn_ids": [s["source_id"] for s in source_dicts],
    "domain_searched": query_domain,
}
```

---

### Surface D — System Ingest (watch_import.sh / API ingest endpoint)

**Current flow:**
- `watch_import.sh` calls `aip ingest file/directory` (legacy pipeline) OR `aip corpus ingest` (corpus pipeline) depending on file type
- Currently only Claude `.zip` exports go through `aip corpus ingest` (correct path)
- Other files (PDF, JSON, TXT, ZIP) go through `aip ingest` (legacy path — never reaches `corpus_turns`)

**Required fix:**
All ingested files should write to `corpus_turns` in addition to `artifacts`/`lexical.db`. Two options:

Option 1 (preferred): Modify `watch_import.sh` to route ALL files through `aip corpus ingest` with appropriate `--source-model` flag. The corpus ingest parsers (claude, chatgpt, markdown, plaintext) handle all formats.

Option 2: Add `corpus_turn_store` writes inside `ingest_conversation()` in `pipeline.py` — build `CorpusTurn` objects from conversation turns and upsert them alongside the artifact write.

**Recommended:** Option 2 — it ensures ALL entry points (API, CLI, watch) populate `corpus_turns` automatically without requiring coordination across scripts.

**Files to change:**
- `src/aip/orchestration/ingestion/pipeline.py` — `ingest_conversation()`: add optional `corpus_turn_store` parameter; if provided, build `CorpusTurn` objects from `conversation.turns` and upsert each one

---

### Surface E — User Edit of Wiki Article (NOT YET IMPLEMENTED)

**Current state:** Wiki articles are GENERATED by Sexton/Beast and shown in the WIKI tab as read-only text. There is no edit surface.

**Spec for implementation:**

**What a wiki edit means:**
A wiki article is an AIP artifact in `GENERATED` or `APPROVED` state with `artifact_type=beast_wiki`. The DEFINER edits the content, which creates a new version (ECS: existing article goes to `SUPERSEDED`, new version enters as `REVIEWED` → DEFINER approves → `APPROVED`).

**UI flow:**
1. WIKI tab shows article list with "Edit" button per article
2. Click Edit → opens markdown editor (NiceGUI `ui.textarea` or `ui.codemirror` if available)  
3. DEFINER edits → clicks "Save Draft" → new artifact version written as `REVIEWED`
4. "Approve" button transitions to `APPROVED` and supersedes previous version
5. The new approved version becomes the wiki overview injected in augmented chat

**Backend endpoints needed:**
- `GET /api/v1/wiki/articles/{domain}` — return current article content + version
- `PATCH /api/v1/wiki/articles/{domain}` — write new version as REVIEWED (DEFINER only)
- `POST /api/v1/wiki/articles/{domain}/approve` — approve the REVIEWED version (DEFINER only)

**Key invariant (AIP-G-01):** No auto-approve. DEFINER must explicitly approve. `PATCH` creates a REVIEWED artifact; `POST /approve` transitions it.

**Sexton behavior after DEFINER edit:**
When the DEFINER edits a wiki article, Sexton's `_wiki_needs_generation()` check should detect that the article is already `APPROVED` (DEFINER-authored) and skip regeneration for that domain until enough new turns accumulate (200k word threshold resets on DEFINER approval).

---

## Sexton Vigil Cycle — What It Should Do on Each Run

Current Sexton `run_cycle()` order (correct, from `actors/sexton.py`):
1. Turn tagging — max 200 untagged turns
2. Embedding pass — max 50 unembedded turns  
3. Wiki generation — max 3 domains/cycle at threshold
4. Graph extraction — from bridge-tagged turns
5. Failure classification

**Missing operations that should be added:**

**6. Tag consolidation proposals** — Sexton should detect turns where `primary_domain` doesn't match the current registry (domain was renamed or merged) and flag them for retagging. Write as GENERATED artifact: "N turns reference deprecated domain X → propose retag to Y."

**7. Domain tunnel detection** — When a turn's `bridges` field references a domain not in the registry, propose a new connector entry. Write as GENERATED artifact for DEFINER review.

**8. Quality gate on new turns** — After tagging, flag any turn where the assigned domain has confidence < 0.4 (Sexton uncertain) and add to review queue with `needs_human_review=True`.

**These are Phase 4 features** — do not implement yet. Document here for roadmap.

---

## Actor Status Panel — Display Bugs

**Bug 1 — Sexton last_cycle_time never shows:**
`src/aip/adapter/api/routes/actors.py` reads `container.sexton` (old failure classifier) for activity timestamps. `container.sexton_actor` (new full-maintenance Sexton) is not included in the actors status response.

**Fix:** In `get_actors_status()`, add `sexton_actor` to the returned dict using `container.sexton_actor`:
```python
sexton_actor_status = {"initialized": container.sexton_actor is not None}
if container.sexton_actor is not None:
    sexton_actor_status["last_cycle_time"] = container.sexton_actor._last_cycle_time
    sexton_actor_status["interval_seconds"] = 300
actors["sexton_actor"] = sexton_actor_status
```

**Bug 2 — RECENT ACTOR ACTIVITY shows "never" for SEXTON:**
`shell.py` RECENT ACTOR ACTIVITY iterates `actors` dict from `/actors/status`. It only sees `sexton` (old classifier). Needs to also show `sexton_actor`.

**Fix:** In `shell.py`, in the RECENT ACTOR ACTIVITY loop, check for both `sexton` and `sexton_actor` and display the one with the most recent timestamp.

---

## Vigil — What It Should Actually Do

Current `run_cycle()` is a stub returning `quality_evaluation_pending`. This is correct for now — the infrastructure is wired (structlog fixed, startup run fires). Full implementation is Phase 3.3.

**What it will do when implemented:**
1. Read all `ask_response` artifacts created since `_last_eval_time`
2. For each response: check that every claim is supported by a cited source turn
3. Flag responses with unsupported claims as GENERATED artifact: "Response {id} made {N} claims without source support"
4. Write evaluation summary as GENERATED artifact for DEFINER review
5. If systematic drift detected (>20% of responses in a domain are unfaithful), propose DEFINER profile amendment

**Not implementing yet.** Stub is correct. Do not touch Vigil beyond the logger fix.

---

## Implementation Priority Order for GLM

**Commit 1 — Actor status display (actors.py + shell.py):**
Add `sexton_actor` to `/actors/status` response. Update RECENT ACTOR ACTIVITY in `shell.py` to show `sexton_actor` timestamps. This makes the running Sexton visible.

**Commit 2 — Chat turn → corpus_turns (ingest.py):**
`auto_save_chat_turn()` in `ingest.py`: after `ingest_conversation()` succeeds, build `CorpusTurn` and call `corpus_turn_store.upsert_turn()`. This fixes Surfaces A, B, C. New chat turns will appear in corpus and get tagged by Sexton on next vigil cycle.

**Commit 3 — Pipeline ingest → corpus_turns (pipeline.py):**
Add optional `corpus_turn_store` parameter to `ingest_conversation()`. When provided, upsert each turn. This fixes Surface D (API ingest, watch_import). Wire the parameter through from `auto_save_chat_turn()` and from the API ingest endpoints.

**Commit 4 — Graph extraction JSON parse fix (sexton.py):**
The graph extraction prompt returns non-JSON from `google/gemma-4-31b-it:free`. Fix the prompt to request strict JSON-only output, or add a JSON extraction wrapper that finds the first `{...}` block in the response. The parse failures are logged as warnings and don't crash Sexton, but graph nodes are not being extracted from bridge-tagged turns.

**Commit 5 — Wiki edit surface (shell.py + new API endpoints):**
Add edit button to WIKI tab, markdown editor, PATCH + approve endpoints. This is Surface E.

---

## Files Requiring Changes (by commit)

| Commit | File | Change |
|--------|------|--------|
| 1 | `src/aip/adapter/api/routes/actors.py` | Add sexton_actor to response |
| 1 | `gui/shell.py` | Show sexton_actor in RECENT ACTOR ACTIVITY |
| 2 | `src/aip/adapter/api/routes/ingest.py` | `auto_save_chat_turn()` → upsert CorpusTurn |
| 2 | `src/aip/adapter/corpus_turn_store.py` | Verify `upsert_turn()` signature |
| 3 | `src/aip/orchestration/ingestion/pipeline.py` | Add corpus_turn_store param |
| 3 | `src/aip/adapter/api/routes/ingest.py` | Pass corpus_turn_store to pipeline |
| 4 | `src/aip/orchestration/actors/sexton.py` | Fix graph extraction JSON prompt |
| 5 | `src/aip/adapter/api/routes/wiki.py` | Add PATCH + approve endpoints |
| 5 | `gui/shell.py` | Wiki edit UI |

---

## Key Invariants to Preserve

- **AIP-G-01:** No auto-approve anywhere. Wiki edits create REVIEWED artifacts. DEFINER approves.
- **AIP-G-02:** No fake success. If `corpus_turn_store` is None, skip silently with log warning — do not raise.
- **AIP-G-09:** No cloud egress from `corpus_turn_store` writes — this is a local SQLite operation only.
- **Idempotency:** `upsert_turn()` uses `INSERT OR IGNORE` on `turn_id` — safe to call multiple times.
- **Non-blocking:** All corpus_turn writes from chat handler must be in background tasks. Never block the WebSocket response.
