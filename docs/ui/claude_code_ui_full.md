# AIP GUI — MAJOR UI OVERHAUL
# Claude Code Session
# Reference: ADR-003, ADR-006, ADR-007, ADR-008, ADR-009, ROADMAP Phase 4
# Date: 2026-06-05
# DEFINER: B. Moses Jorgensen

---

## HYGIENE — READ FIRST

Before writing a single line of code:

```bash
# Confirm what exists
find gui/ -name "*.py" | sort
find src/aip/adapter/api/routes/ -name "*.py" | sort
cat ROADMAP.md | grep -A5 "PHASE 4"
cat docs/decisions/ADR-008-semantic-session-context.md
cat docs/decisions/ADR-003-beast-context-advisory.md
```

Report your findings. Do not write any code until I say "proceed."

---

## ARCHITECTURAL MANDATE — FIXED SHELL

The current NiceGUI implementation navigates between pages and loses
chat state. This is the primary UX failure. The entire GUI must be
restructured as a **fixed shell with swappable content areas**.

```
┌─────────────────────────────────────────────────────────────┐
│  TOP NAV (fixed, always visible)                            │
│  AIP  [CHAT] [AUGMENTED] [COHORT] [REVIEW ●3] [WIKI]       │
│       [CORPUS] [GRAPH] [STATUS]      [🔍] [⚙] [history]   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  CONTENT AREA (swaps on nav click)                          │
│                                                              │
│  Chat persists in memory even when other tabs are active.   │
│  Navigating to WIKI does not destroy the chat state.        │
│  Return to CHAT and the conversation is exactly where       │
│  you left it.                                               │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  STATUS BAR (fixed, dark, monospaced)                        │
│  corpus: 2,766t  beast: idle  vigil: idle  sexton: next 47s │
└──────────────────────────────────────────────────────────────┘
```

Implementation in NiceGUI: use `ui.tab_panels()` with `ui.tab_panel()`
containers at the top level. Each panel is rendered once and hidden/shown
via the tab system. Chat state lives in Python app state (message list,
current session turns), not re-initialized on tab switch.

---

## DESIGN SYSTEM

Apply this consistently across all components. Do not deviate.

### Colors

```python
# Ground — near-black
GROUND     = "#0E0E0F"   # page background
SURFACE    = "#1A1A1D"   # cards, panels
SURFACE_HI = "#252529"   # hover states, selected

# Structural accent — slate-teal
ACCENT     = "#3D5566"   # borders, separators, inactive elements
ACCENT_HI  = "#4E6B80"   # active borders, focused inputs

# Activation — amber
AMBER      = "#B8935A"   # Beast status, active tab indicator,
                          # important actions, source chips
AMBER_HI   = "#D4A96A"   # amber hover

# Text
TEXT_PRI   = "#E8E8EA"   # primary text
TEXT_SEC   = "#8A8A90"   # secondary, metadata, timestamps
TEXT_DIM   = "#52525A"   # disabled, placeholder

# Domain color chips (used in Thread Log and corpus results)
# Map Beast's domain registry to colors — teal family for research
# domains, amber for aip/methodology, muted blue for theology, etc.
# Derive from ACCENT with hue variations. Do not use pure red/green.

# Status
OK     = "#4A7C59"   # confirmed, approved
WARN   = "#7C6A2E"   # pending, draft, generated-not-approved
ERROR  = "#7C3A3A"   # failed, rejected
```

### Typography

```css
/* Wordmark / headers */
font-family: Georgia, 'Times New Roman', serif;

/* Body / UI elements */
font-family: 'Inter', system-ui, -apple-system, sans-serif;

/* Code / monospace / status bar / UUIDs / timestamps */
font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
font-size: 11px;
```

### Component Standards

- Cards: `background: SURFACE`, `border: 1px solid ACCENT`, `border-radius: 4px`
- Active tab: amber bottom border `2px solid AMBER`, text `TEXT_PRI`
- Inactive tab: text `TEXT_SEC`, no border
- Inputs: `background: SURFACE_HI`, `border: 1px solid ACCENT`, focus `ACCENT_HI`
- Buttons (primary): `background: ACCENT`, `color: TEXT_PRI`, hover `ACCENT_HI`
- Buttons (action): `background: AMBER`, `color: GROUND`, hover `AMBER_HI`
- Status bar: `background: GROUND`, `border-top: 1px solid ACCENT`, monospace

---

## LAYOUT — CHAT / AUGMENTED TABS

The CHAT and AUGMENTED tabs share the same three-column layout.
AUGMENTED is the same as CHAT but with context assembly active and
the context disclosure panel visible after each response.

```
┌──────────────┬────────────────────────────────┬──────────────┐
│ LEFT PANEL   │ CHAT CENTER                    │ RIGHT PANEL  │
│ (280px)      │ (flex, main content)           │ (260px)      │
│              │                                │              │
│ Actor Cards  │ Mode breadcrumb                │ Slots card   │
│              │ "AUGMENTED · nbcm · 8 turns"   │              │
│ Beast        │                                │ Wiki queue   │
│ [amber dot]  │ Message history                │ (pending)    │
│ Tagging t23  │ ─────────                      │              │
│ aip_method.  │ USER: ...                      │ Corpus stats │
│ [logs] [↗]  │ BEAST: [context disclosure]    │ 2,766t       │
│              │ ASSISTANT: ...                 │ 26 domains   │
│ Vigil        │ [source chips]                 │              │
│ [grey dot]   │ ─────────                      │              │
│ Last: 2h ago │ [Thread Log — always visible   │              │
│ [logs] [↗]  │  below messages]               │              │
│              │                                │              │
│ Sexton       │ ─────────────────────────────  │              │
│ [grey dot]   │ [INPUT BAR]                    │              │
│ Next: 47s    │ [____________________] [SEND]  │              │
│ [logs] [↗]  │ [model selector]  [mode: AUG]  │              │
│              │                                │              │
└──────────────┴────────────────────────────────┴──────────────┘
```

### Left Panel — Actor Cards

Each actor card:
```
┌─ BEAST ──────────────────────────── ● ─┐
│ Tagging turn 23/2766 · nbcm domain     │
│ google/gemma-4-26b-a4b-it              │
│                              [logs] [↗] │
└────────────────────────────────────────┘
```

- `●` dot: amber = active, grey = idle, red = error
- Status line: current activity in present tense ("Tagging turn 23",
  "Idle · last run 2h ago", "Summarizing nbcm domain")
- Model name: small, `TEXT_SEC`
- `[logs]`: opens modal with last 50 log lines for this actor
- `[↗]`: opens full actor detail modal (config, history, controls)
- Cards are always visible; they update reactively via polling

### Chat Center — Context Disclosure

After each AUGMENTED response, a collapsible panel appears below the
response, collapsed by default:

```
▶ Context used  [nbcm]  [8 turns]  [wiki]
```

Expanded:
```
▼ Context used  [nbcm]  [8 turns]  [wiki]
  Domain searched: nbcm
  Wiki overview: "The NBCM framework posits that boundary conditions..."
  Retrieved turns (8):
    · 2026-04-12 [claude] "what is the relationship between EZ water..."
    · 2026-03-08 [deepseek] "the exclusion zone as a thermodynamic..."
    · [6 more...]
```

Retrieved turn rows are clickable → opens full turn in a slide-out drawer.

### Chat Center — Source Chips

After each response, inline chips showing sources:
```
[nbcm 0.81] [theology_research 0.68] [aip 0.72]
```

Format: `[domain score]`. Domain name in ACCENT color, score in TEXT_SEC.
Clicking a chip filters the Thread Log to that domain.

### Chat Center — Thread Log

Sits below the message history, always visible, scrollable independently:

```
THREAD LOG  [ALL] [nbcm] [theology] [aip] [policy]  [🔍 search...]
─────────────────────────────────────────────────────────────────
▶  14:23  [nbcm]      what is the proper time of a photon...
▶  14:18  [theology]  the new covenant's relationship to...
▶  13:45  [nbcm]      can EZ water exhibit quantum coherence...
▶  13:02  [aip]       how does Beast decide domain assignment...
```

- Each row: timestamp, domain tag chip, truncated user message
- Click any row → opens full turn Q+A in a right-side reference drawer
- Domain filter buttons reduce rows by tag
- Search box runs FTS5 against message content
- The reference drawer does not close the thread log or chat
- Thread Log height: ~200px collapsed, expandable. Collapses on chat focus.

### Right Panel

Three cards stacked vertically:

**Slots Card** (always visible):
```
┌─ SLOTS ──────────────────────────────┐
│ synthesis  claude-maverick      ● ok  │
│ beast      gemma-4-26b-a4b     ● ok  │
│ vigil      [not configured]    ○      │
│ sexton     gemma-4-26b-a4b     ● ok  │
│ embed      [Phase 1.4]         ○      │
└──────────────────────────────────────┘
```

**Wiki Queue Card** (visible when pending > 0):
```
┌─ WIKI QUEUE ──────── 3 pending ──────┐
│ nbcm-overview.md        [review →]   │
│ theology-overview.md    [review →]   │
│ aip-overview.md         [review →]   │
│                    [open review tab] │
└──────────────────────────────────────┘
```

`[review →]` clicks jump directly to that article in the REVIEW tab.

**Corpus Card**:
```
┌─ CORPUS ─────────────────────────────┐
│ 2,766 turns  ·  26 domains           │
│ 42 tagged    ·  2,724 untagged       │
│ 0 embedded   ·  vectors pending 1.4  │
└──────────────────────────────────────┘
```

---

## LAYOUT — REVIEW TAB

Two-section layout:

**Section 1 — Wiki Articles** (top)

For each article in GENERATED state:

```
┌─ [DOMAIN: nbcm] ──────────────── nbcm-overview.md ── 1,247 words ─┐
│ The NBCM framework posits that physical law emerges from boundary   │
│ conditions on null hypersurfaces rather than from field equations.  │
│ The exclusion zone (EZ) water research by Pollack connects...       │
│                         [APPROVE] [REJECT] [EXPAND ↓]              │
└────────────────────────────────────────────────────────────────────┘
```

- Shows first ~200 chars of article
- APPROVE: calls `POST /api/v1/wiki/{id}/approve`, updates status to APPROVED
- REJECT: calls `POST /api/v1/wiki/{id}/reject`, prompts one-line reason
- EXPAND: shows full article inline with annotation interface (see below)

**Wiki Annotation (expanded view)**

Article renders paragraph by paragraph. Each paragraph:
- Hover → grey highlight
- Click → paragraph highlighted amber, small annotation input appears below it:
  ```
  [______ Add annotation for Beast... ______] [Save]
  ```
- Annotations stored as `beast_wiki_annotation` linked to `(article_id, paragraph_index)`
- Annotations surface to Beast during next wiki regeneration for that domain
- Existing annotations shown inline as amber margin notes

**Global comment box** at bottom of expanded article:
```
[Overall notes for Beast (will inform next regeneration): ___________]
[Save note]
```

**Section 2 — Domain Proposals** (below wiki articles)

Beast domain proposals that need DEFINER approval/rejection.
Same APPROVE/REJECT pattern. Less common; shown collapsed by default.

---

## LAYOUT — CORPUS TAB

Three sections:

**Section 1 — Stats bar**
```
Total: 2,766  ·  Tagged: 42  ·  Untagged: 2,724  ·  Bridges: 5
```

**Section 2 — Domain Distribution Table** (sortable)
```
Domain               | Turns | Avg Importance | Top Source
─────────────────────────────────────────────────────────
aip                  | 487   | 0.72           | claude
theology_research    | 312   | 0.68           | claude
nbcm                 | 198   | 0.81           | claude
[...]
untagged             | 2,724 | —              | —
```

Click a domain row → filters the Turn Browser below to that domain.

**Section 3 — Turn Browser** (main section)

Search/filter controls:
```
[🔍 search turns...] [domain: all ▼] [source: all ▼] [date: all ▼]
[min importance: 0.0 ──●── 1.0]
```

Results list — each turn as a card:

```
┌── 2026-03-08  [nbcm]  [deepseek]  importance: 0.81  ────────────┐
│ USER: What is the mechanism by which exclusion zone water...      │
│                                                                    │
│ ASSISTANT: The exclusion zone forms through a phase transition    │
│ driven by infrared radiation absorption at the water-surface...   │
│                                               [VIEW FULL ↗]      │
└────────────────────────────────────────────────────────────────────┘
```

User message and assistant message rendered as `ui.markdown()`.
VIEW FULL opens the complete turn (including thinking_text if present)
in a modal. Importance score shown. Domain chip clickable → filters.

Backend route: `GET /api/v1/corpus/turns` with query params:
`domain`, `source_model`, `date_from`, `date_to`, `min_importance`,
`search`, `page`, `page_size`. Add if not present.

---

## LAYOUT — WIKI TAB

Two-pane layout. Left pane fixed width (~260px), right pane flex.

**Left Pane — Domain Navigator**
```
DOMAINS                        [search...]
─────────────────────────────────────────
  aip              2 articles  ✅ approved
  nbcm             1 article   ✅ approved
  theology_resear  3 articles  ⏳ 1 pending
  bonded_labor     —           (no articles yet)
  [...]
```

Clicking a domain shows its articles in the right pane.
Click an article title to open it.

**Right Pane — Article Reader**

Article title as header (Georgia serif, large).
Article content rendered as `ui.markdown()`.
Article metadata bar: `generated: 2026-06-04 · approved: 2026-06-04 · 1,247 words`

Below the article: same annotation interface as in REVIEW tab.
(Paragraph-click annotation + global comment box.)

`[Open in Review]` button for articles in GENERATED state.
`[Force Regenerate]` button for APPROVED articles (triggers Beast re-draft,
status returns to GENERATED, enters review queue again). Requires confirmation.

---

## LAYOUT — GRAPH TAB

Embedded iframe containing the existing Cytoscape graph visualization
at `/graph-viz`. The iframe takes the full content area.

**postMessage bridge** between iframe and NiceGUI shell:

When the DEFINER clicks a node in the Cytoscape graph:
1. Cytoscape sends `postMessage({type: "node_selected", node_id: "..."})` to parent
2. NiceGUI catches this via `ui.run_javascript()` window event listener
3. NiceGUI calls `GET /api/v1/graph/node/{node_id}` for node details
4. A right-side detail panel slides in:

```
┌─ NODE DETAIL ─────────────────────────────────────┐
│ Entity: NBCM                                       │
│ Type: concept  ·  Domain: nbcm                     │
│ Connections: 8 edges                               │
│                                                    │
│ TOP RELATED TURNS (by importance)                  │
│ · 2026-04-12 [claude] "what is the mechanism..."  │
│ · 2026-03-08 [deepseek] "the exclusion zone..."   │
│ · 2026-01-15 [claude] "NBCM and EZ water..."      │
│                           [VIEW TURN] [VIEW TURN] │
│                                                    │
│ NEIGHBOR NODES                                     │
│ [EZ water] [theology_research] [photon proper time]│
└────────────────────────────────────────────────────┘
```

`[VIEW TURN]` buttons open the full turn in a modal (same modal as Corpus tab).
Neighbor nodes are clickable → sends a new `node_selected` message back to
the iframe to navigate the graph.

Add these backend routes if not present:
- `GET /api/v1/graph/node/{node_id}` — entity detail + top turns + neighbors
- The Cytoscape page needs the postMessage emit added to its click handler

If the Cytoscape page is static HTML at `src/aip/adapter/api/routes/graph_viz.py`
or similar, add the postMessage call there:
```javascript
cy.on('tap', 'node', function(evt) {
  window.parent.postMessage({type: 'node_selected', node_id: evt.target.id()}, '*');
});
```

---

## LAYOUT — COHORT TAB

(Phase 3 feature — build UI scaffold now, wire backend later if time allows)

```
┌─ COHORT SYNTHESIS ─────────────────────────────────────────────────┐
│                                                                     │
│  [___ Enter your question for the cohort... _____________________]  │
│                                                                     │
│  SELECT MODELS (up to 5):                                           │
│  [ ] Claude Sonnet 4.6    $1.50/1M    ☑ claude                    │
│  [ ] DeepSeek R1          $0.55/1M    ☑ deepseek                  │
│  [ ] GPT-OSS-20B          $0.80/1M    □                            │
│  [ ] Gemini 2.5 Flash     $0.10/1M    □                            │
│  [ ] Grok 3               $0.90/1M    □                            │
│                                                                     │
│  Synthesis model: [claude-sonnet-4-6 ▼]                            │
│  Estimated cost: ~$0.03   [ASK COHORT]                             │
│                                                                     │
│  ─────────────────────────────────────────────────────             │
│  [Previous cohort results appear below, most recent first]          │
└─────────────────────────────────────────────────────────────────────┘
```

For Phase 3: wire `POST /api/v1/cohort/synthesize` per ADR-009.
For this session: build the tab scaffold and the UI layout. Stub the
backend call with a placeholder that returns mock data so the layout
is testable.

---

## LAYOUT — STATUS TAB

Replaces the currently empty "ECS State Graph" page.

```
SYSTEM STATUS                              [Refresh]
──────────────────────────────────────────────────────
CORPUS
  Total turns:          2,766
  Tagged:               42 (1.5%)
  Untagged:             2,724
  Bridge-tagged:        5
  Embedded:             0 (Phase 1.4 pending)

WIKI
  APPROVED articles:    0
  GENERATED (pending):  26
  Domains with wiki:    3

KNOWLEDGE GRAPH
  Entities:             (Phase 2.2 pending)
  Edges:                (Phase 2.2 pending)

ACTOR SLOTS
  synthesis    claude-maverick       ● READY
  beast        gemma-4-26b-a4b      ● READY
  vigil        [not configured]     ○ UNCONFIGURED
  sexton       gemma-4-26b-a4b      ● READY    next: 47s
  embed        [not configured]     ○ PHASE 1.4

RECENT ACTOR ACTIVITY
  Beast:    14:23  Tagging corpus (turn 42/2766, nbcm)
  Sexton:   14:01  Maintenance cycle complete — 0 failures resolved
  Vigil:    never  Not configured
──────────────────────────────────────────────────────
```

Backend route: `GET /api/v1/system/status` — add if not present.
Returns the above data as JSON. Page polls on load, re-polls on Refresh click.

---

## IMPLEMENTATION ORDER

Work in this exact sequence. **Stop and report after each tier.**
Do not proceed to next tier without confirmation.

**TIER 0 — Shell restructure** (do this before any individual page work)

Convert current multi-page navigation to fixed shell with tab_panels.
Verify that switching tabs does not destroy chat state.
This is the hardest change architecturally and must be done first.

```bash
# Commit message after tier 0:
git commit -m "refactor: fixed shell architecture — tab_panels, persistent chat state"
```

**TIER 1 — Apply design system**

Apply colors, typography, card styles globally.
No content changes — visual pass only.

```bash
git commit -m "style: apply Linear-influenced design system (ground #0E0E0F, teal #3D5566, amber #B8935A)"
```

**TIER 2 — Chat + Augmented: context disclosure and source chips**

Add context disclosure panel after augmented responses.
Add source domain chips.
Verify markdown rendering (ui.markdown not ui.label).

```bash
git commit -m "ui: chat — context disclosure panel, source chips, markdown render"
```

**TIER 3 — Thread Log**

Add Thread Log component below chat messages.
Domain filter buttons, search, click-to-reference-drawer.

```bash
git commit -m "ui: thread log — infinite turn history, domain filter, search, reference drawer"
```

**TIER 4 — Left panel actor cards + right panel cards**

Implement actor card layout (Beast/Vigil/Sexton with live status).
Right panel: Slots, Wiki Queue, Corpus stats cards.

```bash
git commit -m "ui: left/right panels — actor cards with live status, slots card, wiki queue, corpus stats"
```

**TIER 5 — Review tab**

Wire beast_wiki artifacts to review queue.
Add paragraph annotation interface.
APPROVE/REJECT calls to backend routes (add routes if missing).

```bash
git commit -m "ui: review tab — beast_wiki wired, paragraph annotation, approve/reject"
```

**TIER 6 — Corpus tab**

Domain distribution table, turn browser with search/filter, markdown rendering.
Add `GET /api/v1/corpus/turns` route if missing.

```bash
git commit -m "ui: corpus tab — domain distribution, turn browser, search/filter"
```

**TIER 7 — Wiki tab**

Two-pane domain navigator + article reader with annotation.

```bash
git commit -m "ui: wiki tab — two-pane navigator, markdown reader, annotation"
```

**TIER 8 — Graph tab**

Iframe embed + postMessage bridge + node detail panel.
Add postMessage emit to Cytoscape click handler.
Add `GET /api/v1/graph/node/{id}` route if missing.

```bash
git commit -m "ui: graph tab — iframe embed, postMessage bridge, node detail panel"
```

**TIER 9 — Status tab + Cohort tab scaffold**

Status tab replaces ECS graph page.
Cohort tab: UI scaffold only, stub backend.

```bash
git commit -m "ui: status tab replaces ECS graph; cohort tab scaffold"
```

**Push all:**
```bash
git push origin main
```

---

## DO NOT

- Change database schema
- Change Beast actor logic
- Change ingest pipeline
- Change config/aip.config.toml (live config)
- Auto-approve any wiki articles
- Add non-UI Python dependencies without flagging first
- Force push

---

## VERIFICATION (after all tiers)

1. Switch from CHAT to WIKI and back — confirm chat state preserved
2. Send augmented message — confirm context disclosure panel appears
3. Send message — confirm domain chips appear below response
4. Thread Log — confirm past turns visible, domain filter works
5. Actor cards — confirm Beast shows live status, not static dot
6. Review tab — confirm 26 wiki articles visible with approve button
7. Corpus tab — confirm domain table and turn browser load
8. Wiki tab — confirm article renders, annotation works
9. Graph tab — confirm Cytoscape loads, node click triggers detail panel
10. Status tab — confirm corpus stats and slot health visible

Report `git log --oneline -12` when complete.
