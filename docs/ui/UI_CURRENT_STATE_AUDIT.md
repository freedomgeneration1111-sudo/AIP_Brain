# UI Current State Audit

**Date:** 2026-06-11  
**Cycle:** UI Cycle 1 — Operator Console Current-State Audit  
**Auditor:** Senior AIP_Brain UI/Architecture Engineer  
**Reference:** AIP_Brain UI Operator Console Architecture v0.1, AIP_Brain Full Dogfood UI Development Cycle v0.1

---

## 1. Existing GUI Capabilities

### 1.1 Active GUI Files

| File | Purpose | Lines | Status |
|------|---------|-------|--------|
| `gui/main.py` | Original NiceGUI chat frontend (OpenRouter integration pass) | ~1015 | **PRESERVED LEGACY** — retained until Ask Workbench proven; archived copy in gui/archive/ |
| `gui/shell.py` | New shell with tab-based layout, AIP design tokens, unified panels | ~1760+ | **FROZEN** — no new features; reference only. Default entry point is now gui.app |
| `gui/api_client.py` | HTTP + WebSocket client for backend communication | ~1060+ | Active, API-first, no orchestration imports |
| `gui/config.py` | Backend URL, GUI port, role-to-slot mapping | ~31 | Active |
| `gui/components.py` | Shared components stub | ~5 | **Empty** — no reusable components extracted |
| `gui/__init__.py` | Package marker with docstring | ~8 | Active |
| `gui/app.py` | New NiceGUI entry point, guarded ui.run(), registers page routes | — | **Active** |
| `gui/theme.py` | Design tokens extracted from shell.py | — | **Active** |
| `gui/state.py` | Per-session GuiState, replaces _state singleton | — | **Active** |
| `gui/components/` | Reusable UI components (layout, chat, pills, buttons, modals) | — | **Active** |
| `gui/pages/` | Page modules (dashboard, ask, corpus, retrieval_lab, wiki, artifacts, maintenance, settings) | — | **Active** |
| `gui/panels/` | Persistent panels (right_rail) | — | **Active** |

### 1.2 Current Route/Page Structure

The old shell had **one route** registered: `@ui.page("/")` in `shell.py` (now frozen — no longer the default entry point). The new Operator Console has **8 routes** registered via `@ui.page()` decorators in page modules under `gui/pages/`:

- **CHAT** tab — Unified chat with normal/augmented modes, WebSocket chat, direct OpenRouter fallback, Beast scan, auto-save, gate handling, model slot selection
- **STATUS** tab — Health display, actors, slots, wiki/corpus stats, knowledge graph
- **REVIEW** tab — Pending review queue with approve/reject/approve-all, article expansion
- **CORPUS** tab — Corpus stats, domain distribution, top turns by importance
- **WIKI** tab — Two-pane domain navigator + article reader (GENERATED/APPROVED states)
- **GRAPH** tab — Knowledge graph stats display
- **COHORT** tab — Multi-model ask/comparison (asks same question to multiple models, shows side-by-side results)

The old `main.py` had a header-based navigation with buttons to `/models`, `/vector`, `/graph`, `/wiki`, `/sources`, `/review` — these subordinate routes have been **removed** and are now handled as shell tabs.

**New route structure (UI Cycle 2 — app.py entry point):**

- `/` → Dashboard (default landing, "Can I trust AIP right now?")
- `/ask` → Ask Workbench (preserved chat functionality)
- `/corpus` → Corpus Workbench (**BUILT — UI Cycle 10**)
- `/retrieval` → Retrieval Lab v1 (UI Cycle 11)
- `/wiki` → Wiki/CODEX Home (placeholder)
- `/artifacts` → Artifact Workbench (**BUILT — UI Cycle 9**)
- `/maintenance` → Maintenance Center (v1 — actor status, maintenance jobs, logs, problems)
- `/settings` → Settings (placeholder)

The shell.py tab-based layout is **frozen** (no new features); `app.py` is the default entry point. All start scripts reference `gui.app`.

### 1.3 Chat/WebSocket Flow

The chat flow is fully implemented and functional:

1. API key check on load (blocking dialog if missing)
2. Backend health check with 4s timeout
3. Model slot loading from `/api/v1/models/slots`
4. Session creation via `POST /api/v1/sessions`
5. WebSocket chat via `ws://<backend>/api/v1/chat/<session_id>`
6. Message types: `message`, `response`, `gate`, `error`, `pong`
7. Gate handling: approve/reject buttons for DEFINER gates
8. Auto-save toggle with session update
9. Ingestion status refresh after auto-save
10. Direct OpenRouter fallback when backend unreachable
11. Beast scan (corpus scan) after chat responses in shell.py

### 1.4 Backend Health Flow

- `check_backend_health()` with 4s timeout
- Backend reachable flag stored in `GuiState.backend_reachable`
- Lazy retry on each send if backend was previously unreachable
- Status message displayed to user when backend is down

### 1.5 Model/API Key Flow

- OpenRouter API key stored in-memory on `AipApiClient._openrouter_api_key`
- Environment variable `AIP_OPENAI_API_KEY` as fallback
- Key never written to `os.environ` (security improvement in current code)
- Model slots fetched from backend, persisted locally to `config/selected_models.json` and `config/slot_models.json`
- Universal model dropdown built from selected models + backend slots + config defaults
- Per-slot model assignment with runtime update via `PATCH /api/v1/models/slots/{slot}/model`
- Model library browsing from `GET /api/v1/models/library` and direct OpenRouter catalog

### 1.6 Source Display

- Sources shown inline in chat as system messages (title + score, max 5 shown)
- No dedicated source detail panel
- No retrieval trace display
- No source-to-artifact/wiki linking

### 1.7 Gate Handling

- Gate prompts received via WebSocket `gate` message type
- Approve/Reject buttons rendered inline in chat
- Gate response sent via new WebSocket connection
- Pending gate tracked in `GuiState.pending_gate`
- No gate queue view; no gate history

### 1.8 Actor Status

- Actor status (Beast/Vigil/Sexton) displayed in STATUS tab and sidebar
- Manual trigger buttons for each actor
- Actor initialized/scheduled status shown
- No actor run history, no actor logs, no next-run display

### 1.9 Review/Artifact Pages

- REVIEW tab shows pending reviews + GENERATED knowledge items
- Approve/Reject/Approve-All actions
- Article expansion (preview/full toggle)
- **Artifact Workbench v1 — Built UI Cycle 9:**
  - Artifact list with tab filtering (All, Generated, Needs Revision, Approved, Exported, Rejected, Overrides)
  - Artifact detail panel (content preview, metadata, state badge, sources, review history, crosslinks)
  - Review action panel (Approve, Reject, Needs Revision, Export, Force Export)
  - Force-export with sovereign override dialog (requires confirmation + reason + audit trail)
  - Dashboard summary endpoint with counts by state
  - Full lifecycle tracking (GENERATED → REVIEWED → APPROVED, plus NEEDS_REVISION verdict and EXPORTED event)
  - Artifact export from APPROVED state only
  - Crosslink panel integration on artifact detail
  - Honest empty/unavailable states
  - No auto-approve, no auto-export, no silent state changes

### 1.10 Wiki/CODEX Pages

- WIKI tab shows two-pane domain navigator + article reader
- Articles filtered by domain with search param
- State indicators (GENERATED/APPROVED)
- **Article creation** — POST /api/v1/wiki/articles (DEFINER action, state=GENERATED) — Implemented UI Cycle 7
- **Article editing** — PATCH /api/v1/wiki/articles/{id} (DEFINER action, no state change) — Implemented UI Cycle 7
- **Single article view** — GET /api/v1/wiki/articles/{id} with full WikiArticle schema — Implemented UI Cycle 7
- **Backlinks** — GET /api/v1/wiki/backlinks/{id} — Implemented UI Cycle 7
- **Contradiction detection** — GET /api/v1/wiki/contradictions — Implemented UI Cycle 7
- **Stale article detection** — GET /api/v1/wiki/stale — Implemented UI Cycle 7
- **Enhanced stats** — GET /api/v1/wiki/stats with stale_count, contradiction_count — Implemented UI Cycle 7
- **Storage boundary hardened** — Cycle 7.1: wiki create/edit route through `container.artifact_store` + `container.ecs_store` when available; `sqlite_compat` fallback isolated and documented
- **storage_backend indicator** — Cycle 7.1: every wiki response includes `storage_backend` (artifact_store / sqlite_compat / unavailable); GUI shows badge in article view
- **Article IDs crosslink-safe** — Cycle 7.1: `wiki:{domain}:{title_slug}:{timestamp}` format documented as stable target for Cycle 8 Crosslinks
- No crosslinks to artifacts/sources/turns (deferred to Crosslink System)
- No article revision history browsing (version counter exists)

### 1.11 Corpus/Vector/Graph Pages

- **Corpus Workbench v1 — Built UI Cycle 10:**
  - Corpus summary cards (Documents, Chunks, Embeddings, Problems, Backfill State)
  - Document table with search, chunk counts, embedding status, problem indicators
  - Document detail panel with metadata, chunk/embedding summary, errors, sample turns
  - Ingest action (explicit DEFINER action, requires path, reports honestly)
  - Run Embedding Backfill action (explicit DEFINER action, reports not_wired if no provider)
  - Retry Failed Embeds action (explicit DEFINER action, clears failure counters)
  - Problems panel (failed jobs, unembedded chunks, stale docs, duplicate hashes)
  - 7 new backend endpoints: /corpus/documents, /corpus/documents/{path}, /corpus/problems, /corpus/unembedded, /corpus/backfill, /corpus/retry-failed, /corpus/duplicates, /corpus/stale
  - 3 new store methods: list_documents, get_document_detail, get_corpus_problems
  - 12 API client methods for corpus operations
  - 15 TypedDict classes for corpus response types
  - Honest unavailable/not_wired states — never fake healthy
  - No fake corpus counts, no fake embedding status
  - No silent document deletion or overwrite
  - No secrets exposed
- CORPUS tab shows stats (turns, tagged, untagged, embedded) — superseded by Corpus Workbench
- Domain distribution table — now part of Corpus Workbench summary
- Top turns by importance — available via /corpus/stats
- GRAPH tab shows node/edge counts
- No document-level detail view
- No chunk inspection
- No embedding status per document
- No failed ingest job display
- No ingest action from UI
- No backfill trigger from UI (exists in api_client but no UI surface)
- No graph visualization in UI (exists as standalone `/graph-viz` HTML page)

### 1.12 Design System

- `shell.py` defines AIP design tokens (colors, typography, spacing, borders, radii)
- Status pill helper (`status_pill()`)
- Button style helpers (`btn_primary()`, `btn_secondary()`, `btn_ghost()`)
- AIP Corpus Mark SVG
- Design reference docs exist in `docs/ui/` (HTML mockups, style system)

---

## 2. Existing Backend API Capabilities

### 2.1 Fully Available Endpoints (87 total)

The backend has a rich API surface. Key endpoints relevant to the Operator Console:

| Category | Endpoints | Console Section |
|----------|-----------|-----------------|
| Health & Status | `GET /health`, `GET /health/datastore`, `GET /health/dogfood` | Dashboard |
| Actors | `GET /actors/status`, `GET /actors/{name}`, `POST /actors/{name}/trigger` | Maintenance |
| Corpus | `GET /corpus/stats`, `GET /corpus/embedding-progress`, `GET /corpus/status`, `GET /corpus/audit`, `GET /corpus/backfill-queue`, `POST /corpus/ingest` | Corpus Workbench |
| Artifacts | `GET /artifacts`, `GET /artifacts/{id}`, `GET /artifacts/{id}/versions`, `GET /artifacts/{id}/evaluation` | Artifact Workbench |
| Reviews | `GET /reviews`, `POST /reviews/{id}/approve`, `POST /reviews/approve-all`, `POST /reviews/{id}/reject` | Artifact Workbench |
| Wiki | `GET /wiki/articles`, `GET /wiki/articles/{id}`, `POST /wiki/articles`, `PATCH /wiki/articles/{id}`, `GET /wiki/backlinks/{id}`, `GET /wiki/stale`, `GET /wiki/contradictions`, `GET /wiki/stats` | Wiki/CODEX |
| Beast Scan | `GET /beast/scan` | Ask Workbench |
| Models | `GET /models/slots`, `GET /models/slots/{name}`, `PATCH /models/slots/{name}/model`, `GET /models/api_key_status`, `GET /models/library`, `POST /models/library/fetch`, `PATCH /models/library/{id}` | Settings |
| Ask | `POST /ask`, `POST /ask/retrieve` | Ask Workbench |
| Chat (WS) | `WS /chat/{session_id}` | Ask Workbench |
| Sessions | Full CRUD + context | Ask Workbench |
| Knowledge | `GET /knowledge`, `GET /knowledge/{id}`, `GET /knowledge/search` | Wiki/CODEX |
| Graph | `GET /graph/data`, `GET /graph/neighbors/{id}`, `GET /graph/stats` | Wiki/CODEX |
| ECS | `GET /ecs/graph`, `GET /ecs/artifacts`, `GET /ecs/artifacts/{id}` | Artifact Workbench |
| Sources | `GET /sources`, `GET /sources/stats` | Corpus Workbench |
| Memory | `GET /memory/trace/{session_id}`, `GET /memory/events/{project_id}`, `GET /memory/search`, `GET /memory/entities`, `GET /memory/canonical` | Retrieval Lab |
| Retrieval Dashboard | `GET /retrieval/dashboard`, `GET /retrieval/traces`, `GET /retrieval/channels`, `GET /retrieval/stats`, `GET /retrieval/quality`, `GET /retrieval/budget-tune`, `POST /retrieval/test`, `GET /retrieval/health` | Retrieval Lab |
| Vigil Quality | 17 endpoints for alerts, retention, rollups, mute rules, SSE/WS streams | Maintenance |
| Admin | Config, Sexton classifications/audit/playbook, Beast status, router weights, budget, autonomy log, embedding backfill | Settings/Maintenance |
| Ingest | `POST /ingest/conversation`, `POST /ingest/file` | Corpus Workbench |
| Projects | CRUD + work units | Ask Workbench |
| Collaborators | CRUD (DEFINER-gated) | Settings |
| Plugins | List, enable, disable, health | Settings |
| Performance | Metrics, slow ops, memory | Maintenance |

### 2.2 Dogfood Status Endpoint

`GET /api/v1/health/dogfood` returns:
- Mode: `FULL` / `DIAGNOSTIC` / `BARE`
- Component readiness
- Actor status
- Embedding provider status
- Retrieval channel availability breakdown
- Review gates
- DB paths
- Sexton dependencies

This is the authoritative source for the dashboard's "Can I trust AIP right now?" question.

### 2.3 Retrieval Channel Health

Available via:
- `GET /health` includes `retrieval_channel_health` with per-channel status (fts, vector, graph, wiki, procedural, corpus)
- `GET /retrieval/channels` — per-channel health and performance metrics
- `GET /retrieval/dashboard` — enhanced summary with latency, quality gates, retry rate

---

## 3. Missing Backend APIs

The following API concepts are needed by the Operator Console but **do not exist** in the current backend:

### 3.1 Dashboard

| Needed | Status | Notes |
|--------|--------|-------|
| `GET /api/v1/status/summary` | **✅ BUILT (UI Cycle 3)** | Consolidated status for dashboard cards. Aggregates `/health`, `/health/dogfood`, `/actors/status`, `/corpus/status`, `/retrieval/channels`, `/reviews`, `/wiki/stats` into a single secret-safe response. GUI dashboard and right rail now consume this endpoint. |

### 3.2 Beast Counsel Panel

| Needed | Status | Notes |
|--------|--------|-------|
| `GET /api/v1/turns/{turn_id}/beast-commentary` | **MISSING** | No turn-level Beast commentary storage or retrieval |
| `POST /api/v1/turns/{turn_id}/beast-commentary/run` | **MISSING** | No on-demand Beast commentary generation |
| BeastCommentary schema | **MISSING** | No persistent storage for structured Beast commentary |
| Beast modes (continuity, critique, strategy, etc.) | **MISSING** | `/beast/scan` exists but only does FTS5 → domain → graph → wiki scan, not structured counsel modes |

### 3.3 Model Council / Multi-Model Comparison

| Needed | Status | Notes |
|--------|--------|-------|
| `POST /api/v1/beast/compare-models` | **✅ BUILT (UI Cycle 6)** | Structured multi-model comparison endpoint. Produces advisory Model Council report across all configured text-generation slots (embedding slot excluded). Returns `completed`, `partial`, `insufficient_models`, `unavailable`, or `error`. Reports are ADVISORY ONLY (`advisory_only: true`, `requires_DEFINER_approval: true`). One model failure yields `partial`/degraded report, not total failure. If fewer than 2 text-gen slots are configured, returns `insufficient_models`. Optional `save_as_artifact` persists report as GENERATED artifact. |

### 3.4 Wiki/CODEX Home

| Needed | Status | Notes |
|--------|--------|-------|
| `GET /api/v1/wiki/articles` (enhanced) | **✅ BUILT (UI Cycle 7)** | Added `search` param, stable WikiArticle schema |
| `GET /api/v1/wiki/articles/{id}` | **✅ BUILT (UI Cycle 7)** | Single article with full schema |
| `POST /api/v1/wiki/articles` | **✅ BUILT (UI Cycle 7)** | Create article (DEFINER action, state=GENERATED) |
| `PATCH /api/v1/wiki/articles/{id}` | **✅ BUILT (UI Cycle 7)** | Update article (DEFINER action, no state change) |
| `GET /api/v1/wiki/backlinks/{id}` | **✅ BUILT (UI Cycle 7)** | Backlink retrieval |
| `GET /api/v1/wiki/contradictions` | **✅ BUILT (UI Cycle 7)** | Contradiction detection |
| `GET /api/v1/wiki/stale` | **✅ BUILT (UI Cycle 7)** | Stale article detection |
| WikiArticle schema (full) | **✅ COMPLETE (UI Cycle 7)** | Dedicated article model with tags, aliases, linked articles, open questions, version. CREATE always sets state=GENERATED; EDIT does not change ECS state. |

### 3.5 Crosslink System

| Needed | Status | Notes |
|--------|--------|-------|
| `GET /api/v1/links` | **✅ BUILT (UI Cycle 8)** | List knowledge links with filters |
| `POST /api/v1/links` | **✅ BUILT (UI Cycle 8)** | Create knowledge link (status=suggested, no auto-approve) |
| `PATCH /api/v1/links/{link_id}` | **✅ BUILT (UI Cycle 8)** | Update link status/relation/metadata |
| `DELETE /api/v1/links/{link_id}` | **✅ BUILT (UI Cycle 8)** | Delete link (no linked object mutation) |
| `GET /api/v1/links/backlinks/{target_type}/{target_id}` | **✅ BUILT (UI Cycle 8)** | Get backlinks for an object |
| `GET /api/v1/links/forward/{source_type}/{source_id}` | **✅ BUILT (UI Cycle 8)** | Get forward links from an object |
| KnowledgeLink schema | **✅ BUILT (UI Cycle 8)** | 10 object types, 12 relation types, storage_backend indicator |
| KnowledgeLinkStore | **✅ BUILT (UI Cycle 8)** | aiosqlite adapter with dedicated knowledge_links table in state.db |
| gui/components/link_panel.py | **✅ BUILT (UI Cycle 8)** | Reusable Link Panel with status badges, approve/reject/delete |
| gui/components/link_editor.py | **✅ BUILT (UI Cycle 8)** | Manual link creation dialog with dropdowns |
| gui/api_client.py (6 new methods) | **✅ BUILT (UI Cycle 8)** | list/create/update/delete + backlinks/forward |
| gui/status_types.py (6 TypedDicts) | **✅ BUILT (UI Cycle 8)** | KnowledgeLink + response types |
| Link panel in wiki sidebar | **✅ BUILT (UI Cycle 8)** | Integrated into wiki article view |
| Answer card "Link Wiki" wired | **✅ BUILT (UI Cycle 8)** | No longer "not yet implemented" |

### 3.6 Artifact Workbench

| Needed | Status | Notes |
|--------|--------|-------|
| `GET /api/v1/artifacts` | **✅ BUILT (UI Cycle 9)** | Full list with filtering by state, type, source, search query |
| `GET /api/v1/artifacts/{id}` | **✅ BUILT (UI Cycle 9)** | Full detail with content, metadata, state, sources, review history, export eligibility |
| `GET /api/v1/artifacts/{id}/sources` | **✅ BUILT (UI Cycle 9)** | Source provenance with batch reads |
| `GET /api/v1/artifacts/{id}/reviews` | **✅ BUILT (UI Cycle 9)** | Full ledger (ECS transitions + review verdicts + notes + exports + force-exports) |
| `POST /api/v1/artifacts/{id}/approve` | **✅ BUILT (UI Cycle 9)** | Explicit DEFINER action, GENERATED → REVIEWED → APPROVED, canonical write |
| `POST /api/v1/artifacts/{id}/reject` | **✅ BUILT (UI Cycle 9)** | Explicit DEFINER action, preserves artifact |
| `POST /api/v1/artifacts/{id}/needs-revision` | **✅ BUILT (UI Cycle 9)** | Stores NEEDS_REVISION verdict as event (no ECS transition) |
| `POST /api/v1/artifacts/{id}/export` | **✅ BUILT (UI Cycle 9)** | Only APPROVED artifacts, records export event |
| `POST /api/v1/artifacts/{id}/force-export` | **✅ BUILT (UI Cycle 9)** | Sovereign override, requires reason + confirmation, audit trail |
| `GET /api/v1/artifacts/dashboard` | **✅ BUILT (UI Cycle 9)** | Review queue summary with counts, NEEDS_REVISION count, force-export count |
| Artifact evaluation scores | **HONEST UNAVAILABLE** | Returns status=unavailable instead of fake scores |

### 3.7 Corpus Workbench

| Needed | Status | Notes |
|--------|--------|-------|
| `GET /api/v1/corpus/documents` | **✅ BUILT (UI Cycle 10)** | List documents with chunk counts, embedding status, search |
| `GET /api/v1/corpus/documents/{source_path}` | **✅ BUILT (UI Cycle 10)** | Document detail with metadata, chunks, errors, sample turns |
| `GET /api/v1/corpus/problems` | **✅ BUILT (UI Cycle 10)** | Failed jobs, unembedded count, duplicates, stale docs |
| `GET /api/v1/corpus/unembedded` | **✅ BUILT (UI Cycle 10)** | List of unembedded turns |
| `POST /api/v1/corpus/backfill` | **✅ BUILT (UI Cycle 10)** | Explicit DEFINER backfill trigger, wraps existing admin path |
| `POST /api/v1/corpus/retry-failed` | **✅ BUILT (UI Cycle 10)** | Explicit DEFINER action, clears failure counters |
| `GET /api/v1/corpus/duplicates` | **✅ BUILT (UI Cycle 10)** | Duplicate content hashes |
| `GET /api/v1/corpus/stale` | **✅ BUILT (UI Cycle 10)** | Stale documents (30+ days) |

### 3.8 Retrieval Lab

| Needed | Status | Notes |
|--------|--------|-------|
| `POST /api/v1/retrieval/test` | **✅ BUILT (UI Cycle 11)** | Standalone retrieval test without answer synthesis. Per-channel results, health, latency, fusion/ranking, selected context. No mutation. |
| `GET /api/v1/retrieval/health` | **✅ BUILT (UI Cycle 11)** | Per-channel retrieval health and availability snapshot. |

### 3.9 Maintenance Center

| Needed | Status | Notes |
|--------|--------|-------|
| `GET /api/v1/maintenance/status` | **BUILT** | UI Cycle 12: Aggregated maintenance overview |
| `GET /api/v1/actors/{name}/runs` | **BUILT** | UI Cycle 12: Actor run history from event store |
| `GET /api/v1/maintenance/logs` | **BUILT** | UI Cycle 12: Recent maintenance event logs |
| `POST /api/v1/maintenance/backfill-embeddings` | **BUILT** | UI Cycle 12: Delegates to existing corpus backfill path |
| `POST /api/v1/maintenance/rebuild-graph` | **BUILT** | UI Cycle 12: Returns `scheduled_only` (graph rebuild runs via Sexton cycle) |
| `POST /api/v1/maintenance/rebuild-codex` | **BUILT** | UI Cycle 12: Returns `scheduled_only` (wiki rebuild runs via Sexton cycle) |
| `POST /api/v1/maintenance/run-retrieval-eval` | **BUILT** | UI Cycle 12: Returns `not_wired` (CLI-only tool) |
| `POST /api/v1/maintenance/check-stale-docs` | **BUILT** | UI Cycle 12: Delegates to existing corpus stale logic |
| `POST /api/v1/maintenance/check-contradictions` | **BUILT** | UI Cycle 12: Returns `not_wired` (not yet available) |
| Actor next-run display | **PARTIAL** | Actor interval is shown but next scheduled run time is not exposed |

### 3.9 Settings

| Needed | Status | Notes |
|--------|--------|-------|
| `GET /api/v1/settings/health` | **MISSING** | No consolidated settings health endpoint. Individual config is available via `GET /admin/config` but not structured as a health check. |
| Secret display (source, not value) | **PARTIAL** | `/models/api_key_status` shows has_key per slot, but no general secret source indicator (env/file/missing/override) |

---

## 4. Existing UI Debt to Remove During Migration

### 4.1 Module-Level Singleton State

**Both `main.py` and `shell.py` use `_state: GuiState | None = None` as a module-level singleton.**

- `get_state()` creates one `GuiState` instance lazily and shares it across all page loads
- This breaks multi-tab scenarios and creates shared mutable state
- `_selected_models`, `_role_model_assignments`, `_enabled_slots`, `_role_models` are all module-level globals
- File persistence (`config/selected_models.json`, `config/slot_models.json`) happens at module level

**Migration action:** Replace with per-session state management or NiceGUI's `app.storage.user` / `app.storage.general`.

### 4.2 Duplicate Code Between main.py and shell.py

- `GuiState` class duplicated in both files (nearly identical)
- `get_state()`, `_load_selected_models_from_disk()`, `_save_selected_models_to_disk()` duplicated
- `build_model_options()` duplicated
- `check_backend_health()` duplicated (slightly different signatures)
- `refresh_budget_status()` duplicated
- API key prompt dialog duplicated
- `add_message()`, `add_system_message()` in main.py (not extracted to components)

**Migration action:** Delete `main.py` or clearly mark as deprecated. Consolidate all shared logic into `gui/components.py` or a new `gui/state.py`.

### 4.3 Direct OpenRouter Fallback Ambiguity

- When backend is unreachable, the GUI silently falls back to `chat_direct_openrouter()`
- The message says `(direct OpenRouter — backend not connected)` but this is insufficient per the architecture:
  - Must be labeled **`DIRECT MODEL ONLY — NOT DOGFOOD`**
  - Must clearly state: "No retrieval. No corpus. No actors. No artifact lifecycle."
- In `main.py`, the backend timeout message says "using direct OpenRouter" without dogfood warning
- In `shell.py`, a more prominent warning exists but doesn't use the required label

**Migration action:** Implement the required `DIRECT MODEL ONLY — NOT DOGFOOD` banner with full explanation.

### 4.4 Chat-Centric Layout Assumptions

- The entire GUI is organized around a chat page with auxiliary tabs
- The Operator Console architecture requires a **Dashboard-first** layout with persistent top bar, left nav, main work area, and right rail
- Current tab structure doesn't support the required three-column layout
- No persistent right rail with dogfood mode, actor status, retrieval health, gates, warnings

**Migration action:** Restructure to the three-region layout specified in the architecture.

### 4.5 No Fake Healthy Placeholders (Verification)

- The STATUS tab loads real data from backend APIs
- When data is unavailable, it shows "Loading..." or empty sections
- The REVIEW tab shows "No items pending review" when empty
- **However:** When backend is unreachable, actor status defaults to empty `{}` with no explicit "unavailable" message
- The health check says "Backend: TIMEOUT" or "Backend: UNREACHABLE" but the actor sidebar still shows initialized=False without explaining that data is unavailable (not that actors are uninitialized)

**Migration action:** Distinguish between "actor is uninitialized" and "actor status is unavailable because backend is unreachable".

### 4.6 Silently Swallowed Exceptions

- Multiple `except Exception: pass` patterns in both files (persistence functions, refresh functions)
- `refresh_ingestion_status()` silently catches all exceptions
- `refresh_budget_status()` silently catches all exceptions
- `_load_selected_models_from_disk()` and `_save_selected_models_to_disk()` silently catch all exceptions

**Migration action:** At minimum, log these exceptions. Consider making persistence failures visible.

### 4.7 Sprint/Step/Chunk Comments

- DEBT-009 tracks 200+ Sprint/Step/Chunk scaffold comments in 20+ files
- `main.py` has "STEP 1", "STEP 2", "STEP 3" comments (lines 727, 739, 752)
- These should be cleaned up during migration

**Migration action:** Remove procedural comments and replace with architectural documentation.

### 4.8 No Components Module

- `gui/components.py` is empty (5 lines, just a docstring)
- All UI rendering is inline in `main.py` and `shell.py`
- No reusable card, table, status pill, or panel components

**Migration action:** Extract reusable components during migration.

### UI Cycle 2 Progress Note

UI Cycle 2 addressed items 4.1, 4.2, 4.3, 4.4, 4.6, 4.8 partially:

- **4.1:** Module-level singleton replaced with per-session state in `gui/state.py`
- **4.2:** Duplicate code consolidated — chat logic now in `gui/pages/ask.py`, shared components in `gui/components/`
- **4.3:** Direct model fallback now labeled "DIRECT MODEL ONLY — NOT DOGFOOD"
- **4.4:** Three-region layout implemented (top bar, left nav, main workspace, right rail)
- **4.6:** Persistence functions in `gui/state.py` log errors instead of `except Exception: pass`
- **4.8:** `gui/components/` module now contains reusable components

**Remaining:** 4.5 (fake healthy) and 4.7 (Sprint/Step/Chunk comments in old files) are deferred.

### UI Cycle 3 Progress Note

UI Cycle 3 wired the Operator Console Dashboard to the consolidated `GET /api/v1/status/summary` endpoint:

- **3.1:** Dashboard now has 9 real cards consuming the status summary endpoint instead of making multiple individual API calls
- **Right rail** updated to consume status summary data for all sections (dogfood mode, actors, retrieval health, gates, warnings)
- **state.refresh_status_summary()** added as the single-call refresh path
- **gui/status_types.py** added as TypedDict schema documentation
- **gui/components/layout.py** right rail now delegates to gui.panels.right_rail
- **Honest state visibility**: All cards show UNAVAILABLE, NOT CONFIGURED, DEGRADED, or EMPTY — never fake healthy
- **No secret exposure**: Model slot API keys show "configured"/"missing" only
- **Import boundary preserved**: gui/ never imports from aip.*

### UI Cycle 4 Progress Note

UI Cycle 4 upgrades the Ask page to the Ask Workbench with answer inspection, source detail, retrieval trace, and save-as-artifact capabilities:

- **Answer card** (`gui/components/answer_card.py`): New component replacing plain `add_message` for assistant responses. Includes a status strip showing retrieval health (retrieval healthy, degraded, lexical only, no sources, direct model only, trace unavailable) and an action bar with Show Sources, Show Trace, Save Artifact, Link Wiki (disabled — not yet implemented tooltip), and Model Council (disabled — not yet implemented tooltip).
- **Source panel** (`gui/components/source_panel.py`): New component — right drawer showing source title/path, snippet, score, and channel for each retrieval source. Opened via "Show Sources" action on the answer card.
- **Trace panel** (`gui/components/trace_panel.py`): New component — right drawer showing retrieval trace details (channels attempted/used, latency, verdict, degradation, warnings). Opened via "Show Trace" action on the answer card. Shows "Trace unavailable" honestly when no trace exists.
- **Ask page upgraded** (`gui/pages/ask.py`): Uses answer_card instead of plain add_message for assistant responses. Wires source_panel, trace_panel, and save-artifact actions. Preserves all existing chat/WebSocket/gate behavior. Direct model fallback still shows "DIRECT MODEL ONLY — NOT DOGFOOD" banner.
- **Save-as-artifact endpoint** (`POST /api/v1/turns/save-artifact`): Creates artifacts in GENERATED state only — never auto-approves. Requires DEFINER review before promotion to APPROVED.
- **Retrieval trace by session** (`GET /api/v1/retrieval/traces/session/{session_id}`): New endpoint for the trace panel. Returns the most recent trace or `available: false` honestly.
- **API client methods** (`gui/api_client.py`): Added `get_retrieval_trace_by_session()` and `save_turn_as_artifact()`.
- **Status types** (`gui/status_types.py`): Added SourceEntry, RetrievalTraceEntry, ChatResponseMetadata, SaveArtifactResponse, SessionTraceResponse TypedDicts.
- **Link Wiki and Model Council**: Shown as disabled buttons with "not yet implemented" tooltips. No backend endpoints exist for these features — UI is honest about this.
- **No fake traces or source lists**: All data comes from real backend responses. When data is unavailable, the UI shows "unavailable" honestly.
- **GUI import boundary tests**: Still 14/14 — gui/ never imports from aip.orchestration.
- **Layer/import boundary tests**: Still 17/17 — all adapter/foundation/orchestration boundaries enforced.

### UI Cycle 5 Progress Note

UI Cycle 5 adds the Beast Counsel Panel v1 to the Ask Workbench, providing an advisory second perspective on each assistant turn:

- **Beast Counsel panel** (`gui/components/beast_panel.py`): New component — right drawer showing Beast commentary for a selected turn. Supports five modes: Continuity, Critique, Strategy, Librarian, Risk. Handles all honest states: no commentary yet (with Run button), commentary available, not wired (no model provider), unavailable (no artifact store), error.
- **Beast Commentary backend** (`src/aip/adapter/api/routes/beast_commentary.py`): New route module with `GET /api/v1/turns/{turn_id}/beast-commentary` and `POST /api/v1/turns/{turn_id}/beast-commentary/run`. Commentary generated via Beast model slot (ModelSlotResolver "beast"). Persisted as GENERATED artifacts via VersionedArtifactStore with ECS state management — never auto-approved.
- **Answer card updated** (`gui/components/answer_card.py`): Added "Beast Counsel" button to the action bar between "Save Artifact" and "Link Wiki". Active when on_beast_counsel callback is provided.
- **Ask page wired** (`gui/pages/ask.py`): BeastPanel instance created alongside SourcePanel and TracePanel. _handle_beast_counsel callback wired to both WebSocket and direct OpenRouter answer cards.
- **API client methods** (`gui/api_client.py`): Added get_beast_commentary() and run_beast_commentary().
- **Status types** (`gui/status_types.py`): Added BeastCommentarySuggestedAction and BeastCommentaryResponse TypedDicts.
- **BeastCommentaryRequest/Response Pydantic models**: Stable schema with all required fields: id, turn_id, session_id, mode, summary, critique, continuity_notes, risk_notes, suggested_actions, suggested_wiki_links, suggested_artifacts, model_comparison, retrieval_notes, source_notes, created_at, status, persistence, error.
- **Suggested actions are always advisory-only**: Every suggested action includes advisory_only=True and requires_DEFINER_approval=True. Beast never auto-approves, auto-exports, mutates wiki, changes config, or changes model slots.
- **No fake commentary**: If model provider is not configured, returns honest "not_wired" status. If artifact store is not available, returns honest "unavailable" status. If generation fails, returns honest "error" status.
- **GUI import boundary tests**: Still 16/16 (14 existing + 2 new for beast_panel and all GUI modules).
- **Layer/import boundary tests**: Still 17/17 — adapter routes never import from orchestration.
- **Cycle 5 tests**: 34/34 pass — schema stability, GET honest states, POST sovereignty, no secret exposure, panel import, panel states, answer card wiring, GUI import boundary, backend import boundary, API client methods, TypedDict types, route registration.

### UI Cycle 6 Progress Note

UI Cycle 6 implements the Model Council feature, providing an advisory multi-model comparison tool for the Ask Workbench:

- **Model Council endpoint** (`POST /api/v1/beast/compare-models`): New route in `src/aip/adapter/api/routes/model_council.py`. Runs the same prompt across all configured text-generation model slots (embedding slot excluded) and produces a structured advisory comparison report. Returns `ModelCouncilResponse` with status: `completed`, `partial`, `insufficient_models`, `unavailable`, `error`.
- **Advisory-only design**: All Model Council reports include `advisory_only: true` and `requires_DEFINER_approval: true`. The council never auto-approves, auto-exports, or mutates any system state. It is a comparison and advisory tool only.
- **Graceful degradation**: If fewer than 2 text-generation slots are configured, the endpoint returns `insufficient_models` with an honest message. If one model fails during comparison, the remaining models produce a `partial`/degraded report rather than a total failure.
- **Optional artifact persistence**: When `save_as_artifact=true`, the report is persisted as a GENERATED artifact via VersionedArtifactStore — requiring DEFINER review before approval (no auto-approve).
- **Answer card updated** (`gui/components/answer_card.py`): The "Model Council" button is now enabled (was previously disabled with "not yet implemented" tooltip). Clicking it triggers the Model Council comparison for the associated turn's prompt.
- **Model Council panel** (`gui/components/model_council_panel.py`): Right drawer showing side-by-side model responses, convergence/disagreement highlights, and advisory recommendations. Handles all honest states: completed, partial, insufficient_models, unavailable, error.
- **Ask page wired** (`gui/pages/ask.py`): ModelCouncilPanel instance created alongside BeastPanel, SourcePanel, and TracePanel. `_handle_model_council` callback wired to answer card.
- **API client methods** (`gui/api_client.py`): Added `run_model_council()` method calling `POST /api/v1/beast/compare-models`. Added `list_text_generation_slots()` calling `GET /api/v1/models/text-generation-slots`.
- **Status types** (`gui/status_types.py`): Added `ModelCouncilResponse`, `PerModelResult`, and `TextGenerationSlotEntry` TypedDicts.
- **GUI import boundary tests**: Still passing — no aip.orchestration imports.
- **Layer/import boundary tests**: Still passing — adapter routes never import from orchestration.

### UI Cycle 6.1 Progress Note

UI Cycle 6.1 adds explicit model slot selection to the Model Council panel:

- **Text-generation slots endpoint** (`GET /api/v1/models/text-generation-slots`): New route in `src/aip/adapter/api/routes/models.py`. Returns only text-generation model slots (excludes embedding), with `sufficient_for_council` flag indicating whether at least 2 slots are available. Never exposes API keys or secrets. Each slot entry includes `has_real_model` flag to indicate unconfigured sentinel placeholders.
- **Model slot selector**: Model Council panel now shows a slot selector with checkboxes for each available text-generation slot. The DEFINER can choose which slots participate in the council comparison. At least 2 slots must be selected to run the council. Embedding is never shown. Unconfigured slots are marked with "(unconfigured)" label.
- **Backend `selected_model_slots` honored**: The `POST /api/v1/beast/compare-models` endpoint properly honors the `selected_model_slots` field — only the specified slots are called. Embedding is excluded even if explicitly requested. Invalid/unconfigured slot names are filtered out honestly.
- **Insufficient models state**: If fewer than 2 text-generation slots are available, the panel shows an honest "INSUFFICIENT MODELS" message with the list of available slots. No faking of available models.
- **Selected slots in report header**: The report now shows which slots were used in the comparison header.
- **Backend defaults preserved**: If no slot list can be loaded from the backend (e.g. backend unreachable), the panel falls back to running the council with backend default selection.
- **Advisory labeling**: Reports are labeled as "ADVISORY ONLY — requires DEFINER review before canonical use" in both the header and footer of the panel.
- **Cycle 6.1 tests**: 38/38 pass — selected_model_slots honored, embedding excluded even if requested, invalid slot handled honestly, text-generation-slots endpoint, frontend selector methods, panel toggle slot, minimum slot requirement, API client methods, TypedDict types, route registration, GUI import boundary, backend import boundary, existing Cycle 6 tests still pass, Beast Counsel tests still pass.
- **Sanitation**: No fake council/comparison, no auto-approve, no wiki mutation, no secret exposure, no bare except-pass (fixed), no TODO/FIXME/placeholder, no orchestration imports from GUI.

### UI Cycle 10 Progress Note

UI Cycle 10 builds the Corpus Workbench v1, enabling the DEFINER to ingest, inspect, repair, and backfill the corpus from the Operator Console:

- **Corpus Workbench page** (`gui/pages/corpus.py`): Replaced placeholder with full workbench. Summary cards (Documents, Chunks, Embeddings, Problems, Backfill State), document table with search, document detail panel, actions bar (Ingest, Backfill, Retry Failed), and problems panel (failed jobs, unembedded, stale docs, duplicates).
- **5 new frontend components**: `corpus_summary.py`, `document_table.py`, `document_detail.py`, `corpus_actions.py`, `corpus_problems.py` in `gui/components/`.
- **8 new backend endpoints**: `GET /corpus/documents`, `GET /corpus/documents/{source_path}`, `GET /corpus/problems`, `GET /corpus/unembedded`, `POST /corpus/backfill`, `POST /corpus/retry-failed`, `GET /corpus/duplicates`, `GET /corpus/stale`. All return honest unavailable/not_wired states when stores are not wired.
- **3 new store methods** on `CorpusTurnStore`: `list_documents()`, `count_documents()`, `get_document_detail()`, `get_corpus_problems()` — document-level queries on the corpus_turns table.
- **Ingest action**: Explicit DEFINER action (requires `require_definer` auth). Must provide path. Reports honestly: ingested, skipped, updated, failed counts. Never silently overwrites.
- **Backfill action**: Explicit DEFINER action. Uses existing Sexton/admin backfill path. Returns `not_wired` if embedding provider not configured. Returns `already_running` if backfill in progress. Returns `accepted` when started.
- **Retry failed**: Explicit DEFINER action. Clears embed failure counters for failed turns so they will be retried.
- **12 API client methods** in `gui/api_client.py`: `get_corpus_status`, `get_corpus_embedding_progress`, `list_corpus_documents`, `get_corpus_document_detail`, `get_corpus_problems`, `get_corpus_unembedded`, `trigger_corpus_backfill`, `retry_failed_embeds`, `ingest_to_corpus`, `get_corpus_duplicates`, `get_corpus_stale`.
- **15 TypedDict classes** in `gui/status_types.py`: CorpusStatusResponse, CorpusEmbeddingProgressResponse, CorpusDocumentItem, CorpusDocumentListResponse, CorpusDocumentDetailResponse, CorpusFailedJob, CorpusStaleDoc, CorpusDuplicateHash, CorpusProblemsResponse, CorpusUnembeddedResponse, CorpusBackfillResponse, CorpusRetryFailedResponse, CorpusIngestResponse.
- **Embedding coverage computed honestly**: Zero coverage returns 0.0%, never fake healthy.
- **No fake corpus counts**: All numbers come from real backend queries. Unavailable stores return honest zeros.
- **No silent mutation**: All actions are explicit DEFINER actions with confirmation dialogs.
- **No secrets exposed**: No API keys, passwords, or tokens in any corpus response.
- **Crosslink integration deferred**: Link panel not yet integrated into document detail (deferred to integration pass).
- **Cycle 10 tests**: 30/30 pass — schema stability, honest unavailable states, empty corpus, document detail 404, ingest explicit, backfill honest, retry honest, no secret exposure, import boundary, existing tests still pass.
- **Existing tests still pass**: 14 GUI import boundary, 106 crosslink/artifact/import-boundary tests all green.

### UI Cycle 11 Progress Note

UI Cycle 11 builds the Retrieval Lab v1, enabling the DEFINER to test retrieval independently from answer synthesis:

- **Retrieval Lab page** (`gui/pages/retrieval_lab.py`): Replaced placeholder with full lab. Query panel with text input and channel toggle checkboxes, health cards showing per-channel state/availability, per-channel results display with latency and hit counts, and ranked context view showing fusion output.
- **4 new frontend components**: `retrieval_query_panel.py`, `retrieval_channel_results.py`, `retrieval_health_cards.py`, `retrieval_ranked_context.py` in `gui/components/`.
- **2 new backend endpoints**: `POST /api/v1/retrieval/test` (standalone retrieval test without synthesis), `GET /api/v1/retrieval/health` (per-channel health snapshot).
- **8 new TypedDicts** in `gui/status_types.py`: RetrievalTestRequest, RetrievalTestResponse, RetrievalChannelResult, RetrievalHealthResponse, RetrievalChannelHealth, RetrievalEmbeddingCoverage, RetrievalHealthSummary, RetrievalScores.
- **3 new API client methods** in `gui/api_client.py`: `run_retrieval_test`, `get_retrieval_health`, `get_retrieval_test_result`.
- **No-synthesis verification**: `POST /retrieval/test` never dispatches to any model for answer synthesis — retrieval only, no mutation of artifacts, wiki, corpus, or any system state.
- **No-secret exposure verification**: No API keys, passwords, or tokens in any retrieval test or health response.
- **Honest empty/unavailable states**: Unavailable channels report honestly; empty result sets are surfaced as empty, not faked.
- **Cycle 11 tests**: 26/26 pass — schema stability, honest unavailable states, no-synthesis verification, no-secret exposure, channel toggle handling, health snapshot, fusion output, import boundary, existing tests still pass.
- **Sanitation scan**: 0 blockers, all hits legitimate.

---

## 5. Components/Pages to Reuse

| Component | Source | Reuse Strategy |
|-----------|--------|----------------|
| `AipApiClient` | `gui/api_client.py` | **Keep as-is** — well-structured API-first client. Add missing endpoint wrappers as backend APIs are built. |
| `GuiState` (concept) | `gui/shell.py` | **Refactor** — keep the state class but eliminate module-level singleton. Move to dedicated `gui/state.py`. Add dogfood mode, retrieval health, gate queue, warning list fields. |
| Design tokens (colors, typography, spacing) | `gui/shell.py` | **Extract** to `gui/theme.py` or `gui/design_tokens.py`. Tokens are already well-defined and match the AIP design reference. |
| `status_pill()` helper | `gui/shell.py` | **Extract** to `gui/components.py`. Already follows design system. |
| Button style helpers | `gui/shell.py` | **Extract** to `gui/components.py`. |
| AIP Corpus Mark SVG | `gui/shell.py` | **Extract** to `gui/components.py`. |
| Chat/WebSocket flow | `gui/shell.py` | **Preserve** — the core chat flow is functional and well-tested. Wrap in an Ask Workbench page instead of the current CHAT tab. |
| Gate approve/reject | `gui/shell.py` | **Preserve** — keep gate handling in Ask Workbench. |
| Model slot selection | `gui/shell.py` | **Preserve** — move to Settings page and top bar. |
| API key management | `gui/api_client.py` + `gui/shell.py` | **Preserve** — keep in-memory key storage. Move prompt to first-run experience. |
| Review approve/reject cards | `gui/shell.py` | **Preserve** — extract to Artifact Workbench with additional lifecycle states. |
| Wiki domain navigator | `gui/shell.py` | **Preserve** — extend with article creation, editing, backlinks, crosslinks. |
| Corpus stats display | `gui/shell.py` | **Preserve** — extend into full Corpus Workbench with ingest, backfill, failed jobs. |
| Backend health check | Both files | **Preserve** — extend to include dogfood mode status from `/health/dogfood`. |
| Auto-save toggle + ingestion refresh | `gui/shell.py` | **Preserve** — keep in Ask Workbench. |
| Budget status polling | `gui/shell.py` | **Preserve** — add to right rail. |
| Cohort multi-model ask | `gui/shell.py` | **Preserve concept** — evolve into Model Council. |
| Beast scan | `gui/api_client.py` + `gui/shell.py` | **Preserve API call** — evolve into Beast Counsel Panel with structured modes. |

---

## 6. Components/Pages to Replace

| Component | Current | Replacement | Reason |
|-----------|---------|-------------|--------|
| Page layout | Tab-based single page | Three-region layout (top bar + left nav + main + right rail) | Architecture requires operator console, not chat-centric tabs |
| `_state` singleton | Module-level global | Per-session state via NiceGUI storage or scoped instances | Multi-tab safety, testability |
| `main.py` | ~1015 lines, separate entry point | **Delete** — shell.py is the active frontend | Avoids dual-maintenance, duplicate code |
| Navigation | Header buttons (`/models`, `/vector`, etc.) in main.py; tabs in shell.py | Left nav with Dashboard/Ask/Corpus/Retrieval/Wiki/Artifacts/Maintenance/Settings | Architecture requires structured navigation |
| Direct OpenRouter fallback label | "(direct OpenRouter — backend not connected)" | `DIRECT MODEL ONLY — NOT DOGFOOD` banner with full explanation | Architecture requires explicit degraded mode labeling |
| Actor status display | Sidebar with initialized flag | Right rail with dogfood mode, per-channel retrieval health, gate queue, warnings | Architecture requires persistent truth surface |
| Chat mode toggle | "Chat" / "Augmented" buttons in header | Ask Workbench with mode integrated into the workbench UI | Chat is a sub-feature of Ask, not the primary layout |
| Module-level model persistence | `config/selected_models.json`, `config/slot_models.json` | Backend-first: use `/models/slots` and `/models/library` as source of truth, local cache only | Eliminates state divergence between GUI and backend |
| `config.py` DEFAULT_ROLE_SLOTS / KNOWN_SLOTS | Hardcoded in GUI | Fetch from backend configuration | Backend is authoritative for slot definitions |
| Inline message rendering | `add_message()` / `add_system_message()` in main.py | Reusable `ChatMessage` component in `gui/components.py` | DRY, consistent styling, testability |

---

## 7. Proposed New UI Module/File Structure

```
gui/
├── __init__.py              # Package marker (keep)
├── app.py                   # NiceGUI app entry point, startup/shutdown, route registration
├── theme.py                 # Design tokens (extracted from shell.py)
├── state.py                 # GuiState class (per-session), state factory, session management
├── api_client.py            # AipApiClient (keep, extend with new endpoints)
├── config.py                # GUI-specific config (backend URL, port) — keep minimal
│
├── components/
│   ├── __init__.py
│   ├── layout.py            # Top bar, left nav, right rail, main work area
│   ├── cards.py             # Stat cards, status cards, dashboard cards
│   ├── tables.py            # Data tables (documents, artifacts, actors, etc.)
│   ├── pills.py             # Status pills, badges, mode indicators
│   ├── buttons.py           # Button style helpers (primary, secondary, ghost, danger)
│   ├── chat.py              # Chat message bubbles, system messages, thinking indicator
│   ├── modals.py            # Dialogs (API key prompt, confirmation, etc.)
│   ├── panels.py            # Collapsible panels, expansion items
│   └── crosslinks.py        # Link panels (accept/reject suggested links)
│
├── pages/
│   ├── __init__.py
│   ├── dashboard.py         # Dashboard page — "Can I trust AIP right now?"
│   ├── ask.py               # Ask Workbench — three-voice chamber (DEFINER + AIP + Beast)
│   ├── corpus.py            # Corpus Workbench — ingest, inspect, backfill, repair
│   ├── retrieval_lab.py     # Retrieval Lab — test retrieval without synthesis
│   ├── wiki.py              # Wiki/CODEX Home — article tree, detail, backlinks
│   ├── artifacts.py         # Artifact Workbench — ECS lifecycle management
│   ├── maintenance.py       # Maintenance Center — actors, jobs, logs
│   └── settings.py          # Settings — model slots, API keys, config health
│
├── panels/
│   ├── __init__.py
│   ├── right_rail.py        # Persistent right rail (dogfood mode, actors, retrieval, gates, warnings)
│   ├── beast_counsel.py     # Beast Counsel side panel
│   └── model_council.py     # Model Council comparison panel
│
└── (archive)/
    ├── main.py              # Archived original chat frontend
    └── shell.py             # Archived shell (reference for migration)
```

### Key Design Decisions

1. **`app.py`** replaces both `main.py` and `shell.py` as the single entry point
2. **`pages/`** directory contains one module per Operator Console page, each registering its own `@ui.page()` route
3. **`components/`** directory extracts all reusable UI primitives
4. **`panels/`** directory contains persistent panels (right rail, Beast counsel)
5. **`theme.py`** centralizes all design tokens
6. **`state.py`** eliminates module-level singletons
7. **Archive** preserves old code for reference without creating dual-maintenance

---

## 8. Recommended Build Order

Following the Development Cycle sequence, adapted based on current state:

| Order | Build Step | Depends On | Backend APIs Ready? | Estimated Effort |
|-------|-----------|------------|---------------------|------------------|
| 1 | **UI Shell and Route Skeleton** | This audit | N/A (no new APIs needed) | Medium |
| 2 | **Dashboard + Status Summary** | Shell | `/health`, `/health/dogfood`, `/actors/status`, `/corpus/status`, `/retrieval/channels`, `/wiki/stats` — all exist; may need aggregation endpoint | Medium |
| 3 | **Ask Workbench Upgrade** | Shell | Chat WS exists; need turn-level sources/trace endpoints (partially exist in retrieval dashboard) | Medium-High |
| 4 | **Beast Counsel Panel v1** | Ask Workbench | **MISSING** — need `GET/POST /turns/{id}/beast-commentary` | High |
| 5 | **Model Council** | Ask Workbench | **MISSING** — need `POST /beast/compare-models` | Medium |
| 6 | **Wiki/CODEX Home** | Shell | **✅ BUILT (UI Cycle 7)** — CRUD for articles, backlinks, contradictions, stale detection all implemented | ~~High~~ Done |
| 7 | **Crosslink System v1** | Wiki + Artifacts + Ask | **✅ BUILT (UI Cycle 8)** — full crosslink API, link panel, link editor, wiki sidebar integration | ~~High~~ Done |
| 8 | **Artifact Workbench** | Shell + Crosslinks | **PARTIAL** — need needs-revision, export, force-export | Medium-High |
| 9 | **Corpus Workbench** | Shell | **✅ BUILT (UI Cycle 10)** — full document-level API, problems, backfill, ingest actions | ~~Medium~~ Done |
| 10 | **Retrieval Lab** | Shell | **✅ BUILT (UI Cycle 11)** — `POST /retrieval/test` with per-channel detail, `GET /retrieval/health`, query panel, channel toggles, health cards, ranked context | ~~Medium~~ Done |
| 11 | **Maintenance Center** | Shell | **MOSTLY READY** — actor status/trigger exists; need run history, rebuild endpoints | Medium |
| 12 | **Settings** | Shell | **MOSTLY READY** — model slots, API key status, admin config exist | Low-Medium |
| 13 | **Integration Pass** | All pages | All | Medium |
| 14 | **Full Dogfood E2E Test** | All pages | All | Medium |
| 15 | **Documentation and Alpha Release** | All | All | Low-Medium |

### Critical Path

The **Beast Counsel Panel** and **Wiki/CODEX Home** are on the critical path because they require significant new backend API surface. The Dashboard, Ask Workbench upgrade, and Corpus/Maintenance/Settings pages can proceed with existing APIs.

---

## 9. Risk List

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Beast commentary backend doesn't exist** — no storage, no schema, no generation pipeline | High | Define API contract first; implement panel with "unavailable" state; build backend in parallel |
| ~~Wiki article CRUD doesn't exist~~ — **RESOLVED (UI Cycle 7)** — full CRUD, backlinks, contradictions, stale detection all implemented | ~~High~~ Resolved | Define WikiArticle schema; implement panel with read-only view first; build CRUD endpoints |
| ~~Crosslink system is entirely absent~~ — **RESOLVED (UI Cycle 8)** — full crosslink API (GET/POST/PATCH/DELETE /links, backlinks, forward links), KnowledgeLinkStore, link panel, link editor all implemented | ~~High~~ Resolved | 56 tests in tests/test_crosslink_system_cycle8.py; all existing tests still pass |
| **Module-level singleton state breaks multi-tab** — concurrent tabs share `_state` | Medium | Refactor to per-session state early (Step 1) |
| **Dual main.py / shell.py creates confusion** — which is the active frontend? | Medium | Archive main.py immediately in Step 1 |
| **Direct OpenRouter fallback lacks required labeling** — must show `DIRECT MODEL ONLY — NOT DOGFOOD` | Medium | Fix in Ask Workbench upgrade (Step 3) |
| **Embedding coverage is ~1.8%** — most corpus is unembedded; Sexton needs sustained uptime | Medium | Show honest degraded state in Dashboard; don't pretend vector retrieval is healthy |
| **DEBT-003: MCP fail-open risk** — `autonomy_gate=None` | Low (for UI) | UI should not expose MCP dispatch directly; ensure AutonomyGate is respected in review/approval flows |
| **DEBT-007: Blocking sqlite3 in async admin path** | Low (for UI) | UI calls admin endpoints asynchronously; backend async issue is server-side |
| **DEBT-009: Sprint/Step/Chunk comments** | Low | Clean up during migration |
| **No existing import boundary tests for GUI** — test suite covers `aip.*` but not `gui.*` | Medium | Add GUI import boundary test in Step 1 |
| **NiceGUI version compatibility** — library is evolving rapidly | Low | Pin version in pyproject.toml |

---

## 10. Non-Goals

This UI cycle does **not** include:

1. **Public SaaS deployment** — local-first alpha only
2. **Multi-user collaboration** — single DEFINER operator
3. **Mobile UI** — desktop-first design
4. **Perfect visual polish** — functional and legible over beautiful
5. **Production-grade RBAC** — basic DEFINER/collaborator auth only
6. **External plugin marketplace** — internal plugin list only
7. **Fully autonomous wiki mutation** — all mutations require DEFINER approval
8. **Demo DB** — use real corpus data
9. **MCP transport** — out of scope for this cycle
10. **Extension System** — out of scope for this cycle
11. **Full E2E automation** — scripted smoke test, not CI/CD pipeline
12. **PostgreSQL migration** — SQLite for alpha
13. **Streaming responses in UI** — use current WebSocket flow; streaming is a future enhancement

---

## 11. First Implementation Chunk Plan for UI Cycle 2

### UI Cycle 2: Shell and Route Skeleton

**Goal:** Create the Full Dogfood Operator Console shell with stable layout without breaking existing chat.

**Tasks:**

1. **Archive old code:**
   - Move `gui/main.py` → `gui/archive/main.py`
   - Mark `gui/shell.py` as frozen (no new features)

2. **Create new file structure:**
   - `gui/app.py` — entry point, NiceGUI startup, route registration
   - `gui/theme.py` — extract design tokens from shell.py
   - `gui/state.py` — per-session GuiState, eliminate `_state` singleton
   - `gui/components/` — create `__init__.py`, `layout.py`, `pills.py`, `buttons.py`, `chat.py`

3. **Implement three-region layout:**
   - Top bar: AIP_Brain title, dogfood mode badge, backend status, DEFINER identity
   - Left nav: Dashboard, Ask, Corpus, Retrieval Lab, Wiki, Artifacts, Maintenance, Settings
   - Right rail: dogfood mode, actor status (Beast/Vigil/Sexton), retrieval health (5 channels), pending gates, warnings
   - Main work area: route-specific content

4. **Register page routes:**
   - `@ui.page("/")` → Dashboard (default landing)
   - `@ui.page("/ask")` → Ask Workbench (preserved chat functionality)
   - `@ui.page("/corpus")` → Corpus Workbench (placeholder)
   - `@ui.page("/retrieval")` → Retrieval Lab (placeholder)
   - `@ui.page("/wiki")` → Wiki/CODEX Home (**Implemented — UI Cycle 7**)
   - `@ui.page("/artifacts")` → Artifact Workbench (placeholder)
   - `@ui.page("/maintenance")` → Maintenance Center (placeholder)
   - `@ui.page("/settings")` → Settings (placeholder)

5. **Preserve existing chat:**
   - Migrate WebSocket chat flow into Ask Workbench
   - Preserve gate handling
   - Preserve model slot selection
   - Preserve API key management

6. **Right rail wiring:**
   - Dogfood mode: consume `/health/dogfood`
   - Actor status: consume `/actors/status`
   - Retrieval health: consume `/retrieval/channels` or `/health` (retrieval_channel_health)
   - Pending gates: consume `/reviews`
   - Warnings: initially static; will be dynamic later

7. **Honest unavailable states:**
   - If backend is unreachable, right rail shows "UNAVAILABLE" for all sections
   - Placeholder pages show "Not yet implemented — available in future cycles"
   - No fake healthy data

8. **Direct model fallback labeling:**
   - When backend is unreachable, Ask Workbench shows: `DIRECT MODEL ONLY — NOT DOGFOOD`
   - Banner explains: "No retrieval. No corpus. No actors. No artifact lifecycle."

9. **Import boundary test:**
   - Add test that `gui/` does not import from `aip.orchestration`
   - Add test that `gui/` only imports from `gui.api_client` for backend communication

**Verification:**
- App imports without starting a second server
- All routes render (placeholder pages show honest "not implemented" messages)
- Existing chat works in Ask Workbench
- Right rail shows live data when backend is available
- Right rail shows "UNAVAILABLE" when backend is down
- No orchestration imports in gui/
- Direct model fallback shows required label

**Files changed (expected):**
- NEW: `gui/app.py`, `gui/theme.py`, `gui/state.py`
- NEW: `gui/components/__init__.py`, `gui/components/layout.py`, `gui/components/pills.py`, `gui/components/buttons.py`, `gui/components/chat.py`
- NEW: `gui/pages/__init__.py`, `gui/pages/dashboard.py`, `gui/pages/ask.py`, `gui/pages/corpus.py`, `gui/pages/retrieval.py`, `gui/pages/wiki.py`, `gui/pages/artifacts.py`, `gui/pages/maintenance.py`, `gui/pages/settings.py`
- NEW: `gui/panels/__init__.py`, `gui/panels/right_rail.py`
- MOVE: `gui/main.py` → `gui/archive/main.py`
- MODIFY: `gui/__init__.py` (update docstring)
- NEW: `tests/test_gui_import_boundary.py`

**No backend code changes required for this chunk.**

---

## Appendix A: Existing GUI Route Inventory

| Route | Handler | Status |
|-------|---------|--------|
| `/` | `shell.py:main_page()` | Active (single-page app with tabs) |
| `/graph-viz` | Backend static HTML | Standalone Cytoscape.js visualization |
| `/static/*` | Backend static files | chat.html, index.html, review.html, admin.html, projects.html |

Note: The backend also serves its own HTML pages (chat.html, index.html, review.html, admin.html, projects.html) under `/static/`. These are separate from the NiceGUI frontend and should be assessed for overlap.

## Appendix B: Backend API Coverage Map for Console Sections

| Console Section | APIs Fully Available | APIs Partially Available | APIs Missing |
|----------------|---------------------|--------------------------|-------------|
| Dashboard | `/health`, `/health/dogfood`, `/actors/status`, `/corpus/status`, `/wiki/stats`, `/retrieval/channels` | Aggregation endpoint | `/status/summary` |
| Ask Workbench | Chat WS, `/ask`, `/ask/retrieve`, `/beast/scan`, sessions CRUD | Turn-level sources/trace | `/turns/{id}/beast-commentary` (GET/POST), Beast modes |
| Beast Counsel | `/beast/scan` | — | Full Beast commentary API, Beast modes |
| Model Council | `/models/library`, `/models/slots` | Cohort tab does client-side multi-ask | `/beast/compare-models` |
| Wiki/CODEX Home | `/wiki/articles`, `/wiki/articles/{id}`, `POST /wiki/articles`, `PATCH /wiki/articles/{id}`, `/wiki/backlinks/{id}`, `/wiki/stale`, `/wiki/contradictions`, `/wiki/stats` | — | — |
| Crosslink System | `/links` (GET/POST), `/links/{id}` (PATCH/DELETE), `/links/backlinks/{type}/{id}`, `/links/forward/{type}/{id}` | — | — |
| Artifact Workbench | `/artifacts`, `/reviews` (approve/reject), `/ecs/graph` | `/artifacts/{id}/evaluation` | Needs-revision, export, force-export, source panel |
| Corpus Workbench | `/corpus/*`, `/ingest/*`, `/sources/*`, `/admin/embeddings/backfill`, `/corpus/documents`, `/corpus/documents/{path}`, `/corpus/problems`, `/corpus/unembedded`, `/corpus/backfill`, `/corpus/retry-failed`, `/corpus/duplicates`, `/corpus/stale` | — | — (Document detail, chunk inspection, failed jobs now built — UI Cycle 10) |
| Retrieval Lab | `/retrieval/dashboard`, `/retrieval/channels`, `/retrieval/traces` | `/ask/retrieve` (no per-channel detail) | `/retrieval/test` (interactive) |
| Maintenance Center | `/actors/status`, `/actors/{name}/trigger` | Backfill trigger | Actor run history, rebuild endpoints, retrieval eval |
| Settings | `/models/slots`, `/models/api_key_status`, `/admin/config` | `/admin/hot-reload/status` | `/settings/health`, secret source display |

## Appendix C: Key Architecture Constraints

1. **GUI must remain API-first** — all backend communication through `AipApiClient`
2. **GUI must not import orchestration internals** — enforced by test suite for `aip.*`, needs extension for `gui.*`
3. **DEFINER sovereignty** — no silent approve, export, mutate, bypass
4. **Degraded states visible** — every unavailable subsystem must be labeled, not hidden
5. **Direct model fallback** must be labeled `DIRECT MODEL ONLY — NOT DOGFOOD`
6. **No fake healthy placeholders** — unavailable = unavailable, not green
7. **Existing chat behavior preserved** until Ask Workbench replaces it
8. **New UI is the Operator Console**, not a patched chat page
