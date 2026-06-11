# AIP_Brain Full Dogfood UI Development Cycle

Consecutive Agent Prompt Series v0.1

## Purpose

This document provides a sequence of prompts for coding/review agents to carry AIP_Brain from the current architecture/hardening work into a complete full-dogfood UI alpha.

Each prompt must require the agent to:
1. Review current architecture before modifying code.
2. Identify relevant technical debt.
3. Preserve AIP_Brain's layer discipline.
4. Preserve DEFINER sovereignty.
5. Avoid silent degradation.
6. Add tests or verification commands.
7. Update documentation where behavior changes.
8. Produce a clear completion report.

Agents should not treat these as isolated feature prompts. Each prompt builds on the previous one.

## Global Agent Instructions

Use this header at the top of every agent prompt:

> You are acting as a senior AIP_Brain engineer.
> Before coding:
> 1. Read the current architecture documents, README, STATUS, DOGFOOD_READY, relevant ADRs, and the UI Operator Console Architecture reference.
> 2. Inspect the current implementation before assuming file paths or existing endpoints.
> 3. Identify any architecture drift, layer violations, duplicated logic, silent failure patterns, blocking sync calls in async paths, or stale documentation related to your task.
> 4. Preserve the foundation / orchestration / adapter layer discipline.
> 5. The GUI must remain API-first and must not import orchestration internals.
> 6. Preserve DEFINER sovereignty. Do not add silent mutation, silent export, or silent approval paths.
> 7. Any degraded mode must be visible to the operator.
> 8. Add or update tests where practical.
> 9. Run the relevant test/lint/typecheck commands.
> 10. End with a concise worklog: files changed, behavior added, tests run, unresolved debt.

---

## Prompt 1 — UI Architecture Reconciliation and Current-State Audit

TASK: Audit the current AIP_Brain UI/API architecture against the Full Dogfood UI Operator Console Architecture reference.

Goal:
Produce a current-state report and implementation plan before feature work begins.

Steps:
1. Read README.md, STATUS.md, DOGFOOD_READY.md, docs/ARCHITECTURE.md, docs/API_REFERENCE.md, docs/CONFIGURATION.md, and the UI Operator Console Architecture reference.
2. Inspect gui/main.py, gui/api_client.py, src/aip/adapter/api/app.py, src/aip/adapter/api/dependencies.py, and all current API route modules.
3. Map existing UI capabilities:
   - chat
   - backend health
   - model slots
   - API key handling
   - actor status
   - source display
   - review page
   - vector/graph/wiki/source pages
   - gate handling
   - ingestion status
4. Identify missing capabilities required for full dogfood mode:
   - dashboard
   - Beast commentary
   - wiki home
   - crosslink navigation
   - corpus workbench
   - retrieval lab
   - artifact workbench
   - maintenance center
   - safe settings
5. Identify technical debt that must be resolved or avoided:
   - GUI module-level state
   - duplicated dialogs
   - direct OpenRouter fallback ambiguity
   - route/API gaps
   - admin/security exposure
   - layer violations
   - stale docs
6. Produce a staged implementation plan with dependencies and risks.

Deliverable:
Create docs/ui/UI_CURRENT_STATE_AUDIT.md with:
- existing capabilities
- missing capabilities
- required API endpoints
- tech debt inventory
- recommended build order
- risks and non-goals

Do not implement features in this prompt unless necessary to complete the audit. Run formatting or docs validation if available.

---

## Prompt 2 — UI Shell and Navigation Skeleton

TASK: Create the Full Dogfood Operator Console shell.

Goal:
Refactor or extend the existing NiceGUI UI so it has a stable operator-console layout without breaking existing chat.

Required pages:
- Dashboard
- Ask
- Corpus
- Retrieval Lab
- Wiki
- Artifacts
- Maintenance
- Settings
- Models, if already separate

Requirements:
1. Preserve existing chat functionality.
2. Introduce a consistent top bar, left navigation, main work area, and right rail.
3. Add placeholder pages for missing areas.
4. The right rail should show placeholder sections for:
   - dogfood mode
   - actor status
   - retrieval health
   - pending gates
   - warnings
5. Do not wire fake data as if real. If data is unavailable, show "unavailable" or "not wired".
6. Avoid direct orchestration imports from GUI.
7. Refactor duplicated navigation code if needed.
8. Preserve backend health behavior but label direct model fallback as degraded/non-dogfood.

Tests / Verification:
- App imports without starting a second server.
- Existing Ask/chat page still renders.
- All new routes render.
- No orchestration imports are introduced into gui/.

Deliverable:
- Updated GUI shell
- Route skeletons
- Worklog

---

## Prompt 3 — Status Summary API and Dashboard

TASK: Implement the Full Dogfood Dashboard and status summary API.

Goal:
The dashboard must answer: "Can I trust AIP right now?"

Backend:
1. Add or consolidate a status summary endpoint.
2. The endpoint should report:
   - dogfood mode
   - backend health
   - actor status summary
   - retrieval health summary
   - corpus summary
   - artifact review queue summary
   - wiki/CODEX summary if available
   - current warnings
3. Do not expose secrets.
4. Admin/DEFINER-sensitive status should be protected according to existing auth rules.

Frontend:
1. Implement dashboard cards:
   - Dogfood Mode
   - Corpus Health
   - Retrieval Health
   - Review Queue
   - Actor Health
   - Warnings
   - Recent Activity, if available
2. If a subsystem is unavailable, show unavailable/degraded, not fake healthy.
3. The right rail should consume the same or related status data.

Tech debt:
- If status information is currently scattered, create a small adapter-layer status aggregation service.
- Do not import orchestration directly from GUI.

Tests / Verification:
- Status endpoint returns stable schema.
- Dashboard renders when all data is available.
- Dashboard renders honest degraded state when data is missing.
- No secrets are returned.

Deliverable:
- Dashboard UI
- Status endpoint/schema
- Tests
- Docs update if endpoint is added
- Worklog

---

## Prompt 4 — Ask Workbench Upgrade

TASK: Upgrade the chat page into the Ask Workbench.

Goal:
Every answer should become inspectable and linkable.

Requirements:
1. Preserve existing chat/WebSocket behavior.
2. Add visible retrieval status per assistant answer:
   - healthy
   - degraded
   - no sources
   - direct model only
3. Add buttons or panels:
   - Show Sources
   - Show Retrieval Trace
   - Save as Artifact
   - Link Wiki
   - Run Model Council
4. Show linked knowledge:
   - wiki articles
   - artifacts
   - source documents
   - retrieval trace
5. If retrieval trace data is not yet available from backend, define the API contract and show "trace unavailable" honestly.
6. Direct model fallback must be visually marked as not full dogfood.

Backend:
- Add or adapt endpoints needed to fetch turn-level sources and retrieval trace.
- Do not create fake traces.

Tech debt:
- Refactor source display into reusable component.
- Refactor message rendering if it is currently monolithic.

Tests / Verification:
- Existing chat works.
- Assistant answer displays retrieval status.
- Source panel opens.
- Trace panel handles available and unavailable trace states.
- Direct model fallback warning is visible.

Deliverable:
- Ask Workbench UI
- Turn-level source/trace integration
- Worklog

---

## Prompt 5 — Beast Counsel Panel v1

TASK: Add the Beast Counsel side panel to the Ask Workbench.

Goal:
Beast should comment on each turn as an advisory second perspective.

Backend:
1. Define BeastCommentary schema:
   - id
   - turn_id
   - mode
   - summary
   - critique
   - continuity_notes
   - risk_notes
   - suggested_actions
   - suggested_wiki_links
   - suggested_artifacts
   - model_comparison
   - created_at
2. Add endpoints:
   - GET /api/v1/turns/{turn_id}/beast-commentary
   - POST /api/v1/turns/{turn_id}/beast-commentary/run
3. If Beast actor integration already exists, use it.
4. If not, create a clean adapter/orchestration boundary and mark commentary generation as unavailable rather than faking it.
5. Store commentary persistently if a suitable store exists. If not, define the persistence TODO clearly and avoid losing the API contract.

Frontend:
1. Add persistent Beast Counsel side panel.
2. Show Beast modes:
   - Continuity
   - Critique
   - Strategy
   - Multi-model
   - Librarian
   - Risk
3. Allow "Run Beast Commentary" for selected turn.
4. Display commentary linked to the turn.
5. Beast suggestions must be advisory only.

Sovereignty:
- Beast may suggest actions.
- Beast may not silently mutate wiki, artifacts, config, or approval state.

Tests / Verification:
- Commentary endpoint returns stable schema.
- Panel handles no commentary yet.
- Panel displays commentary when available.
- No silent mutation occurs.

Deliverable:
- Beast Counsel Panel v1
- API/schema
- Tests
- Worklog

---

## Prompt 6 — Model Council / Multi-Model Comparison Reports

TASK: Implement Model Council comparison reports for the Ask Workbench.

Goal:
Allow the DEFINER to compare multiple model outputs and receive a Beast synthesis.

Backend:
1. Add endpoint:
   - POST /api/v1/beast/compare-models
2. Input:
   - prompt/question
   - selected model slots or model IDs
   - optional source context
   - optional existing assistant answer
3. Output:
   - per-model position
   - unique contributions
   - convergence
   - disagreement
   - risks
   - Beast conclusion
   - recommended decision
4. Respect configured model slots.
5. Do not expose raw API keys.
6. Record token/cost/latency if available.

Frontend:
1. Add "Run Model Council" action in Ask Workbench.
2. Show report as a structured comparison table.
3. Allow saving the report as:
   - artifact
   - wiki note
   - decision record, if supported
4. If saving is not yet wired, show planned action honestly.

Tech debt:
- Avoid one-off hardcoded model calls.
- Use model slot resolver or existing model abstraction.

Tests / Verification:
- Endpoint validates input.
- Handles one model failure without losing whole report.
- UI displays partial/degraded report honestly.
- No secret leakage.

Deliverable:
- Model Council v1
- Comparison report UI
- Worklog

---

## Prompt 7 — Wiki / CODEX Home

TASK: Build the primary Wiki / CODEX home.

Goal:
The wiki becomes a first-class navigable knowledge home.

Backend:
1. Inspect existing wiki/CODEX/graph/source capabilities.
2. Add or adapt endpoints for:
   - list articles
   - get article
   - create article
   - update article
   - backlinks
   - related artifacts
   - related sources
   - contradictions
   - stale articles/docs
3. Define WikiArticle schema if not already present:
   - id
   - title
   - summary
   - body
   - status
   - tags
   - aliases
   - linked_articles
   - backlinks
   - source_documents
   - related_artifacts
   - related_turns
   - related_beast_commentaries
   - open_questions
   - contradictions
   - revision_history

Frontend:
1. Add Wiki as a primary nav item.
2. Implement article tree/list.
3. Implement selected article view.
4. Implement backlinks panel.
5. Implement related artifacts/sources panel.
6. Implement create/edit article flow.
7. Show stale/conflict markers.

Sovereignty:
- Auto-generated article suggestions require DEFINER approval before becoming canonical.

Tests / Verification:
- Wiki home renders empty state.
- Article list renders.
- Article detail renders.
- Backlinks render if available.
- Create/edit flow validates input.

Deliverable:
- Wiki/CODEX Home v1
- API/schema additions if needed
- Tests
- Docs update
- Worklog

---

## Prompt 8 — Crosslink System v1

TASK: Implement the crosslink system between knowledge objects.

Goal:
Make sources, turns, Beast commentaries, wiki articles, artifacts, retrieval traces, and review events navigable.

Backend:
1. Define KnowledgeLink schema:
   - id
   - source_type
   - source_id
   - target_type
   - target_id
   - relation_type
   - confidence
   - created_by
   - approved_by_definer
   - created_at
2. Supported object types:
   - source_document
   - chunk
   - conversation_turn
   - retrieval_trace
   - beast_commentary
   - wiki_article
   - artifact
   - review_event
   - actor_event
   - model_comparison_report
3. Supported relation types:
   - supports
   - contradicts
   - summarizes
   - extends
   - mentions
   - depends_on
   - implements
   - supersedes
   - related_to
   - generated_from
   - reviewed_by
   - approved_by
4. Add endpoints:
   - GET /api/v1/links
   - POST /api/v1/links
   - PATCH /api/v1/links/{id}
   - DELETE /api/v1/links/{id}
5. Add backlink lookup.

Frontend:
1. Add link panels to:
   - Ask Workbench
   - Beast Counsel
   - Wiki Article view
   - Artifact view
   - Source document view
2. Add actions:
   - Accept suggested link
   - Reject suggested link
   - Edit relation type
   - Create manual link
3. Do not auto-approve system-suggested links unless explicitly permitted.

Tests / Verification:
- Create link.
- List links by source.
- List backlinks.
- Approve/reject link suggestion.
- UI navigates from article to artifact and back.

Deliverable:
- KnowledgeLink v1
- Crosslink UI panels
- Tests
- Worklog

---

## Prompt 9 — Artifact Workbench

TASK: Build the Artifact Workbench for the ECS artifact lifecycle.

Goal:
The DEFINER can review, approve, revise, link, and export artifacts from the GUI.

Backend:
1. Inspect existing artifact/review/export APIs.
2. Add or adapt endpoints for:
   - list artifacts by state
   - get artifact
   - get artifact sources
   - get artifact review history
   - approve
   - reject
   - needs revision
   - export
   - force export, if allowed
3. Force export must be visibly exceptional and audited.

Frontend:
1. Add artifact list with tabs:
   - Generated
   - Needs Review
   - Needs Revision
   - Approved
   - Exported
   - Overrides
2. Add artifact preview.
3. Add review panel.
4. Add source panel.
5. Add linked wiki/articles panel.
6. Add review actions.
7. Add export action.
8. Make force export visually dangerous and require explicit confirmation.

Sovereignty:
- Normal export should require approved state.
- Any bypass must create audit event.

Tests / Verification:
- List artifacts.
- Review artifact.
- Approve artifact.
- Reject artifact.
- Mark needs revision.
- Export approved artifact.
- Force export, if present, logs audit event.

Deliverable:
- Artifact Workbench v1
- Tests
- Docs update
- Worklog

---

## Prompt 10 — Corpus Workbench

TASK: Build the Corpus Workbench.

Goal:
The DEFINER can ingest, inspect, repair, and backfill the corpus from the GUI.

Backend:
1. Inspect existing ingestion/corpus/vector/source APIs.
2. Add or adapt endpoints for:
   - corpus status
   - document list
   - document detail
   - ingest file/folder
   - failed ingest jobs
   - retry failed ingest
   - unembedded chunks
   - embedding backfill
   - duplicate docs
   - stale docs
3. Return honest degraded/unavailable states.

Frontend:
1. Add corpus summary cards:
   - documents
   - chunks
   - embeddings
   - problems
2. Add document table.
3. Add document detail panel.
4. Add ingest action.
5. Add backfill action.
6. Add failed jobs view.
7. Add links to related wiki articles and artifacts.

Tech debt:
- If DB paths or stores are split, show actual storage locations in settings/status rather than implying unified state.

Tests / Verification:
- Corpus page renders empty and populated states.
- Ingest action works or clearly reports unavailable.
- Backfill action works or clearly reports unavailable.
- Failed jobs visible.

Deliverable:
- Corpus Workbench v1
- API additions if needed
- Tests
- Worklog

---

## Prompt 11 — Retrieval Lab

TASK: Build the Retrieval Lab.

Goal:
The DEFINER can test retrieval quality independently of answer synthesis.

Backend:
1. Add or adapt endpoint:
   - POST /api/v1/retrieval/test
2. Input:
   - query
   - selected channels
   - optional project/corpus scope
   - optional limit/context budget
3. Output:
   - channel results
   - channel health
   - latency
   - scores
   - fusion result
   - final selected context
   - degraded channels
   - warnings
4. Add endpoint:
   - GET /api/v1/retrieval/health

Frontend:
1. Query input.
2. Channel toggles.
3. Channel result cards.
4. Ranked context list.
5. Trace details.
6. Degraded channel warnings.
7. Links to source docs/wiki/articles.

Tech debt:
- Do not hide vector fallback or brute-force degradation.
- Do not report retrieval as healthy if only lexical is active.

Tests / Verification:
- Retrieval test works with all channels.
- Retrieval test works with partial channels.
- Degraded vector state is visible.
- Empty retrieval is visible and not treated as success.

Deliverable:
- Retrieval Lab v1
- Tests
- Worklog

---

## Prompt 12 — Maintenance Center

TASK: Build the Maintenance Center.

Goal:
The DEFINER can inspect and run Beast, Vigil, Sexton, and maintenance jobs.

Backend:
1. Add or adapt endpoints:
   - GET /api/v1/actors/status
   - POST /api/v1/actors/{actor}/run
   - GET /api/v1/actors/{actor}/runs
   - POST /api/v1/maintenance/backfill-embeddings
   - POST /api/v1/maintenance/rebuild-graph
   - POST /api/v1/maintenance/rebuild-codex
   - POST /api/v1/maintenance/run-retrieval-eval
2. Each actor status should show:
   - initialized
   - scheduled
   - running
   - last run
   - next run
   - last result
   - last error
3. Do not confuse initialized with actively scheduled.

Frontend:
1. Actor table.
2. Run buttons.
3. Maintenance job controls.
4. Recent maintenance log.
5. Failed runs panel.
6. Links to warnings and related corpus/wiki/artifact objects.

Tests / Verification:
- Actor status endpoint works.
- Run actor endpoint works or returns clear unavailable state.
- UI displays failed and healthy states.
- No orphan/background task confusion.

Deliverable:
- Maintenance Center v1
- Tests
- Worklog

---

## Prompt 13 — Settings and Model Slots

TASK: Build safe Settings and Model Slot management UI.

Goal:
The DEFINER can inspect configuration health without leaking secrets.

Backend:
1. Add or adapt endpoints:
   - GET /api/v1/settings/health
   - GET /api/v1/models/slots
   - PATCH /api/v1/models/slots/{slot}
2. Settings health should show:
   - dogfood mode
   - storage paths
   - model slot status
   - embedding provider status
   - retrieval weights
   - actor cadence
   - auth/admin status
   - backup/export paths
   - degraded fallback policy
3. Do not return secret values.
4. Show secret source:
   - env
   - file
   - missing
   - runtime override

Frontend:
1. Dogfood mode panel.
2. Model slots table.
3. API key configured/missing indicators.
4. Storage paths panel.
5. Retrieval config panel.
6. Actor cadence panel.
7. Security/auth panel.
8. Degraded fallback policy panel.

Tech debt:
- Do not write API keys to process environment at runtime.
- Do not show secrets in the UI.

Tests / Verification:
- Settings page renders.
- Model slot status visible.
- Secret values not exposed.
- Updating allowed slot works, if supported.

Deliverable:
- Settings v1
- Tests
- Docs update
- Worklog

---

## Prompt 14 — Integration Pass and UI Consistency

TASK: Perform a full UI integration pass.

Goal:
Make the operator console coherent and navigable.

Steps:
1. Review all UI pages:
   - Dashboard
   - Ask
   - Corpus
   - Retrieval Lab
   - Wiki
   - Artifacts
   - Maintenance
   - Settings
2. Ensure consistent badges:
   - FULL DOGFOOD
   - DEGRADED
   - DIRECT MODEL ONLY
   - ACTIVE
   - FAILED
   - STALE
   - NEEDS REVIEW
   - APPROVED
3. Ensure crosslinks work:
   - turn to Beast commentary
   - turn to artifact
   - turn to wiki article
   - artifact to wiki article
   - wiki article to source
   - source to artifact
4. Ensure empty states are clear.
5. Ensure degraded states are clear.
6. Ensure no fake healthy placeholders remain.
7. Refactor duplicated UI components.
8. Improve operator-readable error messages.

Tests / Verification:
- Manual route walkthrough.
- Unit/component tests where practical.
- Backend API schema compatibility.
- Lint/typecheck/test commands.

Deliverable:
- Integrated UI pass
- Component cleanup
- Worklog

---

## Prompt 15 — Full Dogfood End-to-End Test

TASK: Implement and run a full dogfood UI E2E test.

Goal:
Verify the complete loop can be completed from the GUI.

Scenario:
1. Start app.
2. Confirm dashboard shows full dogfood mode or clear degraded state.
3. Ingest a test document.
4. Confirm corpus status updates.
5. Confirm embeddings/backfill status.
6. Ask a question about the document.
7. Inspect sources.
8. Inspect retrieval trace.
9. Run Beast commentary on the turn.
10. Accept or reject Beast link suggestions.
11. Create or link a wiki article.
12. Save answer as artifact.
13. Review artifact.
14. Approve artifact.
15. Export artifact.
16. Run maintenance actor.
17. Confirm dashboard/recent activity updates.
18. Restart app.
19. Confirm state persists.

Deliverable:
- E2E test or scripted smoke test
- Test corpus fixture
- Known expected outputs
- Bug list for any failures
- Worklog

Do not fake pass. If something fails, record it as a blocker or known limitation.

---

## Prompt 16 — Documentation and Alpha Release Pass

TASK: Update documentation for the completed Full Dogfood UI alpha.

Goal:
Docs must match the implemented software.

Update:
1. README.md
2. STATUS.md
3. DOGFOOD_READY.md
4. docs/ARCHITECTURE.md
5. docs/API_REFERENCE.md
6. docs/CONFIGURATION.md
7. docs/DEVELOPER_GUIDE.md
8. docs/DEPLOYMENT_GUIDE.md
9. docs/UI_OPERATOR_CONSOLE_ARCHITECTURE.md
10. docs/UI_FULL_DOGFOOD_USER_GUIDE.md
11. docs/CHANGELOG.md

Required documentation:
- how to start the UI
- what full dogfood mode means
- dashboard explanation
- Ask Workbench explanation
- Beast Counsel explanation
- Wiki/CODEX explanation
- artifact lifecycle explanation
- corpus/retrieval/maintenance/settings explanation
- known limitations
- degraded modes
- troubleshooting
- backup/recovery notes if available

Verification:
1. Run docs consistency search for stale claims:
   - fake embeddings
   - vectors not built
   - unified datastore if no longer true
   - built but not wired
   - old port assumptions
   - outdated dogfood status
2. Run tests/lints.
3. Produce release note:
   - AIP_Brain Alpha Full Dogfood UI

Deliverable:
- Updated docs
- Release notes
- Final known limitations list
- Worklog

---

## Final Completion Standard

The UI development cycle is complete when:

> A technically competent user can start AIP_Brain locally, open the GUI, see full dogfood status, ingest documents, ask source-grounded questions, inspect retrieval traces, receive Beast commentary, navigate the wiki, crosslink articles and artifacts, review/approve/export artifacts, run maintenance actors, inspect settings, and understand any degraded mode without using the CLI.

The UI is not complete merely because pages exist.
It is complete when it makes the sovereign knowledge loop visible and usable.
