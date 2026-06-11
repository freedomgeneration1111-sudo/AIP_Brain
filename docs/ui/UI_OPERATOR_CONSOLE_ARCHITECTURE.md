# AIP_Brain UI Operator Console Architecture

AIP_Brain UI Operator Console Architecture
Full Dogfood Mode — Architecture Reference v0.1
1. Purpose
This document defines the architecture for the AIP_Brain Full Dogfood Mode UI.
The UI is not merely a chat screen. It is the operator console for a sovereign knowledge engine. It must
expose the full system loop:
1. Corpus ingestion
2. Retrieval and trace inspection
3. Ask / synthesis
4. Beast commentary and second-perspective counsel
5. Wiki / CODEX navigation
6. Artifact generation
7. DEFINER review and approval
8. Export
9. Maintenance actors
10. Health, degradation, and audit visibility
The UI's primary obligation is operational legibility. The DEFINER must be able to see what AIP knows,
what it retrieved, what failed, what is degraded, what is awaiting approval, and what maintenance actions
are needed.

2. Architectural Principles
2.1 API-first adapter boundary
The GUI must communicate with the backend through REST/WebSocket API endpoints. It must not import
orchestration modules directly.
The UI belongs to the adapter layer. It may call adapter API routes. It may not reach into orchestration
internals, stores, actor implementations, or model factories directly.

2.2 DEFINER sovereignty
The UI must preserve the DEFINER's authority.
The system may suggest:
• wiki links
• artifact actions
• source associations
• Beast commentary
• contradiction resolutions
• review recommendations
• maintenance actions
But the system must not silently approve, export, mutate canonical wiki pages, or bypass artifact gates
without explicit DEFINER approval.

2.3 Full dogfood mode must be visible
The UI must clearly show whether the system is in:
• FULL DOGFOOD
• MINIMAL DOGFOOD
• DIAGNOSTIC
• DEGRADED
• DIRECT MODEL ONLY
Full dogfood mode means:
• backend reachable
• corpus available
• retrieval channels active or visibly degraded
• Beast active
• Vigil active
• Sexton active
• artifact lifecycle active
• CODEX/wiki available
• maintenance state visible
• model slots configured
• storage paths known
• admin/DEFINER controls protected
If any required subsystem is missing, the UI must not pretend full dogfood mode is active.

2.4 No silent degradation
If the system answers without vector retrieval, graph retrieval, CODEX retrieval, or source grounding, the UI
must say so.
Examples:
• "Vector retrieval unavailable."
• "Graph retrieval returned no results."
• "Answer generated from lexical context only."
• "Direct model fallback active: no corpus, no retrieval, no artifact lifecycle."

2.5 Knowledge objects must be crosslinked
AIP_Brain should expose several first-class knowledge objects:
• Source documents
• Chunks
• Retrieval traces
• Conversation turns
• Beast commentaries
• Wiki/CODEX articles
• Artifacts
• Review events
• Actor events
• Maintenance jobs
• Model comparison reports
These should be navigable and crosslinked.

3. Core UI Areas
The UI should have the following primary navigation structure:

Dashboard
Ask
Corpus
Retrieval Lab
Wiki / CODEX
Artifacts
Maintenance
Settings
The current chat-centric UI should evolve into this operator-console structure.

4. Global Layout
The UI should use three persistent regions:

┌──────────────────────────────────────────────────────────────────────────────┐
│ Top Bar: AIP_Brain Alpha | Dogfood Mode | Backend Status | User / DEFINER
│
├───────────────┬──────────────────────────────────────────────┬───────────────┤
│ Left Nav
│ Main Work Area
│ Right Rail
│
│
│
│
│ Dashboard
│ Page-specific workspace
│ Actors
│
│ Ask
│
│ Retrieval
│
│ Corpus
│
│ Gates
│
│ Wiki
│
│ Warnings
│
│ Artifacts
│
│
│
│ Maintenance
│
│
│
│ Settings
│
│
│
└───────────────┴──────────────────────────────────────────────┴───────────────┘

5. Persistent Right Rail
The right rail should appear on most pages.
It should show:

FULL DOGFOOD MODE
Status: Healthy / Degraded / Broken
Actors
- Beast: active / idle / failed
- Vigil: active / idle / failed
- Sexton: active / idle / failed
Retrieval
- Lexical
- Vector
- Graph
- CODEX
- Procedural
Gates
- pending artifact reviews
- pending Beast suggestions
- pending wiki updates
- pending exports
Warnings
- unembedded chunks
- stale docs
- failed actor runs
- degraded retrieval channels
- config/security warnings
The right rail is the compact "truth surface" of AIP_Brain.

6. Dashboard
6.1 Purpose
The dashboard answers one question:
Can I trust AIP right now?

6.2 Required cards
The dashboard should show:
1. Dogfood Mode
2. Backend/API Health
3. Corpus Health
4. Retrieval Health
5. Actor Health
6. Artifact Review Queue
7. CODEX/Wiki Health
8. Current Warnings
9. Recent Activity

6.3 Dashboard wireframe
AIP_Brain Alpha — Full Dogfood Mode
┌────────────────────┬────────────────────┬────────────────────┬────────────────────┐
│ Dogfood Mode
│ Corpus Health
│ Retrieval Health
│ Review
│ 128 docs
│ 5/5 channels active │ 7
│ 4 unembedded
│ vector OK
│
Queue
│ FULL
│
pending
│ all actors active
│ 2 needs
│
revision
└────────────────────┴────────────────────┴────────────────────┴────────────────────┘
┌──────────────────────────────────────────────┬──────────────────────────────────────┐
│ Recent Activity
│ Current
│
Warnings
│ - Asked: Sprint 13 UI
│ - 4 chunks need
│
embedding
│ - Beast added commentary
STATUS
│ - 1 stale doc conflicts with
│
│ - Artifact approved
once
│ - Vector fallback occurred
│
└──────────────────────────────────────────────┴──────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────┐
│ Actor
│
Status
│ Beast: active | last run 12m ago | next 18m
[Run] [View
│
Log]
│ Vigil: active | last run 5m ago
| 2 warnings
[Run] [View
| 4 repairs
[Run] [View
│
Log]
│ Sexton: active| last run 2m ago
│
Log]
└─────────────────────────────────────────────────────────────────────────────────────┘

7. Ask Workbench
7.1 Purpose
The Ask Workbench is the primary reasoning chamber.
It is not a simple chat page. It is where the DEFINER asks, AIP answers, retrieval is inspected, Beast
comments, and knowledge links are created.

7.2 Three-voice chamber
The Ask Workbench should contain:
1. DEFINER / User
2. Main synthesis answer
3. Beast Commentary side panel

7.3 Ask Workbench wireframe
┌────────────────────────────────────────────────────────────┬───────────────┐
│ Ask Thread
│ Beast
│
│
│ Counsel
│ User: Sprint 13 needs Beast commentary and wiki navigation...
says:
│ Beast
│
│
│ Good
│ AIP: Agreed. This turns the workbench into a three-voice chamber...
addition.│
│
│ Missing:
│
│
│ article
│ [Sources] [Trace] [Save Artifact] [Link Wiki] [Run Model Council]
graph │
│
│
├────────────────────────────────────────────────────────────┴───────────────┤
│ Linked
│
Knowledge
│ Wiki: Sprint 13, Ask Workbench, Beast Counsel Layer, CODEX
Wiki
│
│ Artifacts: Sprint 13 Design Brief, Alpha Console
│
Spec
│ Sources: Architecture docs, code reviews, conversation
turns
│
└────────────────────────────────────────────────────────────────────────────┘

7.4 Ask Workbench requirements
Every assistant answer should expose:
• retrieval health
• source list
• retrieval trace
• save-as-artifact action
• suggested wiki links
• Beast commentary
• model comparison option
• degraded mode warnings

7.5 Direct model fallback warning
If the backend is unreachable and the UI falls back to direct model chat, the page must clearly show:

DIRECT MODEL MODE — NOT DOGFOOD
No retrieval. No corpus. No actors. No artifact lifecycle.

8. Beast Counsel Panel
8.1 Purpose
The Beast Counsel panel is a persistent side window that comments on each turn.
Beast is not the main answer. Beast is the second perspective, continuity keeper, critic, strategist, librarian,
and model-council summarizer.

8.2 Beast modes
The panel should support these modes:

Continuity
Critique
Strategy
Multi-model
Librarian
Risk

8.3 Beast commentary content
A Beast commentary may contain:
• short assessment
• continuity note
• critique
• risk note
• suggested next action
• suggested wiki links
• suggested artifact action
• model comparison summary
• contradiction warning

8.4 Example Beast commentary
BEAST COMMENTARY — Turn 14
Assessment:
The answer correctly frames Sprint 13 as operator-console work, but underweights
article navigation and wiki home design.
Continuity:
The project has already committed to full dogfood mode, so the UI must expose
all live subsystems.
Risk:
If Beast commentary is only decorative, it will become noise. It needs
structured modes and persistent linkage to the turn.
Suggested next move:
Define crosslink schema between turns, Beast comments, wiki articles, artifacts,
sources, and retrieval traces.

8.5 Beast commentary schema
BeastCommentary
id
turn_id
mode
summary
critique
continuity_notes
risk_notes
suggested_actions
suggested_wiki_links
suggested_artifacts
model_comparison
created_at

8.6 Beast authority boundary
In first implementation, Beast must be advisory only.
Beast may suggest:
• create wiki article
• link article
• create artifact
• update artifact
• run model council
• flag contradiction
Beast may not silently:
• mutate canonical wiki
• approve artifact
• export artifact
• change model slots
• change config
• delete corpus data

9. Multi-Model Comparison Reports
9.1 Purpose
The Ask Workbench should allow the DEFINER to run or inspect multi-model review/comparison outputs.

9.2 Report structure
Multi-Model Comparison Report
Model Positions:
- Claude: architecture boundary focus
- Grok: runtime failure and retrieval realism
- GLM: docs reconciliation and release framing
- GPT: synthesis and product architecture
Convergence:
All models agree alpha is close but requires honesty, visibility, and full
wiring.
Disagreement:
Claude prioritizes layer discipline.
Grok prioritizes runtime embarrassment and retrieval fallback.
GLM prioritizes documentation consistency.
Beast Conclusion:
The UI must expose full-system truth to the DEFINER.

9.3 Save targets
A multi-model report may be saved as:
• artifact
• wiki note
• decision record
• review note
• source-linked synthesis

10. Corpus Workbench
10.1 Purpose
The Corpus Workbench lets the operator ingest, inspect, backfill, and repair the knowledge base.

10.2 Required capabilities
• ingest file
• ingest folder
• show document list
• show source status
• show chunks
• show embedding status
• show unembedded chunks
• show failed ingest jobs
• retry failed jobs
• deduplicate documents
• show stale documents
• show document provenance
• open related wiki articles
• open related artifacts

10.3 Wireframe
Corpus
[Ingest File] [Ingest Folder] [Backfill Embeddings] [Run Corpus Audit]
┌────────────────────┬────────────────────┬────────────────────┬────────────────────┐
│ Documents
│ Chunks
│ Embeddings
│
│ 9,842 chunks
│ 9,701 embedded
│ 4 failed
│ 141 unembedded
│ 98.6% complete
│ 2
│
Problems
│ 128 total
docs
│
│ 7 stale
│
duplicates
└────────────────────┴────────────────────┴────────────────────┴────────────────────┘
Documents
┌───────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│ Name
│ Type
│ Status
│ Embedded
│ Last Updated │
│ README.md
│ docs
│ current
│ yes
│ today
│
│ STATUS.md
│ docs
│ stale?
│ yes
│ yesterday
│
│ audit.md
│ review
│ current
│ no
│ today
│
└───────────────┴──────────────┴──────────────┴──────────────┴──────────────┘

11. Retrieval Lab
11.1 Purpose
The Retrieval Lab allows the DEFINER to test retrieval without synthesis.

11.2 Required capabilities
• run retrieval test query
• enable/disable channels
• show lexical results
• show vector results
• show graph results
• show wiki/CODEX results
• show procedural results
• show fusion/ranking
• show degraded channels
• show latency per channel
• show final context selection

11.3 Wireframe
Retrieval Lab
Query: [ What is full dogfood mode?
] [Test Retrieval]
Channels:
[x] Lexical
[x] Vector
[x] Graph
[x] Wiki/CODEX
[x] Procedural
┌────────────────────┬────────────────────┬────────────────────┬────────────────────┐
│ Lexical
│ 12 hits
│ Wiki/
│ 8 hits
│ 3 paths
│ 2 topic
│ 88 ms
│ 31 ms
│ 19
│
│ 42 ms
ms
│ Graph
│
CODEX
pages
│ Vector
│
└────────────────────┴────────────────────┴────────────────────┴────────────────────┘
Ranked Context
1. docs/DOGFOOD_READY.md — score 0.91
2. docs/ARCHITECTURE.md — score 0.84
3. CODEX topic: Full Dogfood Mode — score 0.80

12. Wiki / CODEX
12.1 Purpose
The wiki is the living knowledge map of AIP_Brain.
It must not be a hidden technical page. It should be a primary home.

12.2 Wiki object model
Each wiki article should have:

Title
Summary
Status
Tags
Aliases
Linked articles
Backlinks
Source documents
Related artifacts
Related conversations
Related Beast commentaries
Open questions
Contradictions
Revision history

12.3 Wiki home wireframe
CODEX Wiki
┌────────────────────┬──────────────────────────────────────┬────────────────────┐
│ Article Tree
│ Article
│ Link
│
│
│
│
│ Alpha
│ Beast Counsel Layer
│
│ Ask
│
Workbench
- Dogfood Mode
│
│
↕
- Sprint 13
│ Summary
│ Beast
│ Persistent side commentary...
│
│
│ Multi-model
│
│
│
Counsel
│ Retrieval
↕
│
│
- Vector
│
│ Actors
│ Status: Proposed / Active
│
│
│
│ Backlinks
│
│ - Beast
│
│
│ - Vigil
│
│ - Sexton
│
│ Linked Artifacts
│ - Sprint
│ - Sprint 13 Console Plan
│ - Full Dogfood
Mode │
└────────────────────┴──────────────────────────────────────┴────────────────────┘

12.4 Crosslink requirements
Wiki articles must support:
• article-to-article links
• backlinks
• source links
• artifact links
• conversation-turn links
• Beast-commentary links
• contradiction links
• supersession links

13. Artifact Workbench
13.1 Purpose
Artifacts are generated outputs that move through the ECS lifecycle.
The UI must make the lifecycle usable without CLI.

13.2 Artifact lifecycle
GENERATED → REVIEWED → APPROVED → EXPORTED
↓
NEEDS_REVISION
Force export, if allowed, must be visibly exceptional and audited.

13.3 Artifact workbench wireframe
Artifacts
Tabs: [Generated] [Needs Review] [Needs Revision] [Approved] [Exported]
[Overrides]
┌────────────┬────────────────────────────┬──────────────┬──────────────┬────────────┐
│ ID
│ Title
│ State
│ Sources
│
Actions
│ A-123
│ Sprint 13 design
│ GENERATED
│ 9
│
Review
│ A-124
│ Dogfood checklist
│ REVIEWED
│ 12
│
Approve
│ A-125
│ README update
│ APPROVED
│ 5
│
Export
│
│
│
│
│
│
└────────────┴────────────────────────────┴──────────────┴──────────────┴────────────┘
Selected Artifact
┌──────────────────────────────┬─────────────────────────────────────┐
│ Artifact Content
│ Review Panel
│
│
│ Faithfulness: pass/warn/fail
│
│
│ Coherence: pass
│
│
│ Source coverage: 9 sources
│
│
│ Notes:
│
│
│ [Approve] [Reject] [Needs Revision] │
└──────────────────────────────┴─────────────────────────────────────┘

14. Crosslink System
14.1 Purpose
The UI must let the operator navigate between all major knowledge objects.

14.2 Link model
KnowledgeLink
id
source_type
source_id
target_type
target_id
relation_type
confidence
created_by
approved_by_definer
created_at

14.3 Source and target types
source_document
chunk
conversation_turn
retrieval_trace
beast_commentary
wiki_article
artifact
review_event
actor_event
model_comparison_report

14.4 Relation types
supports
contradicts
summarizes
extends
mentions
depends_on
implements
supersedes
related_to
generated_from
reviewed_by
approved_by

14.5 DEFINER approval
Automatically suggested links should begin as unapproved suggestions.
The UI should allow:

[Accept Link]
[Reject Link]
[Edit Relation]
[Create Article]
[Link Existing Article]

15. Maintenance Center
15.1 Purpose
The Maintenance Center exposes actor and maintenance operations.

15.2 Required capabilities
• show Beast status
• show Vigil status
• show Sexton status
• run Beast
• run Vigil
• run Sexton
• run embedding backfill
• rebuild graph
• rebuild CODEX topics
• run retrieval evals
• show actor logs
• show failed runs
• show next scheduled runs

15.3 Wireframe
Maintenance
Actors
┌─────────┬──────────┬─────────────┬─────────────┬──────────────┬────────────┐
│ Actor
│ Status
│ Last Run
│ Next Run
│ Last Result
│ Actions
│ Beast
│ Active
│ 12m ago
│ 18m
│ OK
│ Run / Logs │
│ Vigil
│ Active
│ 5m ago
│ 25m
│ 2 warnings
│ Run / Logs │
│ Sexton
│ Active
│ 2m ago
│ 28m
│ 4 repairs
│ Run / Logs │
└─────────┴──────────┴─────────────┴─────────────┴──────────────┴────────────┘
Maintenance Jobs
[ ] Backfill embeddings
[ ] Rebuild graph
[ ] Recompute topic map
[ ] Run retrieval eval
[ ] Check stale docs
[ ] Check contradictions

16. Settings
16.1 Purpose
Settings must expose system configuration without leaking secrets.

16.2 Required panels
• dogfood mode
• model slots
• API key status
• storage paths
• retrieval weights
• actor cadence
• auth/admin protection
• backup/export paths
• degraded fallback policy

16.3 Secret display rule
Show secret status, not secret values.
Example:

OpenRouter API key: configured via environment
SMTP password: missing

17. Required Backend Endpoint Concepts
Exact endpoint names may vary, but the UI requires these concepts:

GET /api/v1/status/summary
GET /api/v1/dogfood/status
GET /api/v1/actors/status
POST /api/v1/actors/{actor}/run
GET /api/v1/actors/{actor}/runs
GET /api/v1/corpus/status
GET /api/v1/corpus/documents
POST /api/v1/corpus/ingest
POST /api/v1/corpus/backfill
GET /api/v1/corpus/problems
POST /api/v1/retrieval/test
GET /api/v1/retrieval/health
GET /api/v1/retrieval/recent-traces
GET /api/v1/wiki/articles
GET /api/v1/wiki/articles/{id}
POST /api/v1/wiki/articles
PATCH /api/v1/wiki/articles/{id}
GET /api/v1/wiki/backlinks/{id}
GET /api/v1/wiki/contradictions
GET /api/v1/wiki/stale
GET /api/v1/artifacts
GET /api/v1/artifacts/{id}
POST /api/v1/artifacts/{id}/approve
POST /api/v1/artifacts/{id}/reject
POST /api/v1/artifacts/{id}/needs-revision
POST /api/v1/artifacts/{id}/export
GET /api/v1/turns/{turn_id}/beast-commentary
POST /api/v1/turns/{turn_id}/beast-commentary/run
POST /api/v1/beast/compare-models
POST /api/v1/beast/suggest-links
GET /api/v1/links
POST /api/v1/links
PATCH /api/v1/links/{id}
DELETE /api/v1/links/{id}
GET  /api/v1/settings/health
GET  /api/v1/models/slots
PATCH /api/v1/models/slots/{slot}

18. Development Priorities
The UI development cycle should proceed in this order:
1. Architecture and tech-debt review
2. UI shell and route skeleton
3. Dashboard / status summary
4. Ask Workbench upgrade
5. Beast Counsel panel
6. Wiki/CODEX home
7. Crosslink system
8. Artifact workbench
9. Corpus workbench
10. Retrieval Lab
11. Maintenance Center
12. Settings and model slots
13. Integration pass
14. Full dogfood E2E test
15. Documentation and alpha release pass

19. Alpha Completion Criteria
The UI cycle is complete when:
1. The DEFINER can see whether full dogfood mode is active.
2. The DEFINER can ask questions with source-grounded retrieval.
3. Each answer exposes sources and retrieval trace.
4. Beast can comment on turns and provide second-perspective counsel.
5. Beast can summarize multi-model comparison reports.
6. Wiki/CODEX has a primary home.
7. Articles are navigable and crosslinked.
8. Artifacts are crosslinked with sources, turns, wiki articles, and Beast commentaries.
9. The DEFINER can review, approve, reject, revise, and export artifacts.
10. Corpus ingestion, embedding status, and failed jobs are visible.
11. Retrieval can be tested independently of synthesis.
12. Beast, Vigil, and Sexton status and maintenance actions are visible.
13. Settings expose configuration health without leaking secrets.
14. Degraded modes are visible and honest.
15. The complete loop can be run from the GUI without CLI intervention.

20. Non-Goals for This Cycle
This cycle does not require:
• public SaaS deployment
• multi-user collaboration
• mobile UI
• perfect visual polish
• production-grade role-based access control
• external plugin marketplace
• fully autonomous wiki mutation without DEFINER approval
The goal is complete local-first alpha software for serious dogfooding.
