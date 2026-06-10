# AI_FINGERPRINT_REGISTER.md

**Frozen at:** commit `efeb887c799aa5cefa1add1f011b7b8cf99bd83b`
**Date:** 2026-06-10
**Hardening Cycle:** Chunk 1 — Continuity Audit and Baseline Freeze

This register catalogs AI-generated fingerprints found in the AIP_Brain source code.
AI fingerprints are comments, patterns, or code structures that reveal AI-assisted
generation rather than expressing durable design intent. They are identified for
potential removal in later chunks per the hardening rules.

---

## Fingerprint Categories

| Category | Pattern | Why It's a Fingerprint |
|---|---|---|
| Sprint-number comment | `# Sprint 5.XX: ...` | Narrates implementation timeline, not design. A human developer writes "why", not "when Sprint added this". |
| Step-number scaffold | `# Step N: ...` | Narrates sequential implementation steps. Durable comments describe what and why, not implementation order. |
| CRITICAL: history narrative | `CRITICAL: These schemas must match...` | Uses urgency language to narrate implementation history rather than expressing a design constraint. |
| Placeholder surface | `"""List work units (placeholder)."""` | Function that exists as a stub without real behavior. |
| Placeholder status string | `"placeholder"` | Runtime value that indicates a feature is not yet implemented. |

---

## Fingerprint Inventory

### Category 1: Sprint-Number Comments (300+ instances)

These are the most pervasive AI fingerprints in the codebase. They appear as inline comments
that reference Sprint numbers (Sprint 5.21 through Sprint 6.3), documenting when a feature
was added rather than why it exists or what constraint it satisfies.

#### Worst Offenders (by density)

**`src/aip/adapter/alerting.py`** — ~200 Sprint references across 9,133 lines

This file is the single largest source of Sprint-number fingerprints. The file implements
alert management (notification, A/B experiments, rollback, bandit, WebSocket delivery, etc.)
and was built incrementally across Sprint 5.25 through Sprint 5.63. Nearly every section,
method, and configuration parameter is annotated with its originating Sprint number.

Sample patterns:
```
# Sprint 5.26: Webhook retry configuration
# Sprint 5.29: Config-driven alert routing
# Sprint 5.30: Escalation configuration
# Sprint 5.31: Alert digest/aggregation configuration
# Sprint 5.32: Per-alert-type digest overrides
# Sprint 5.33: Delivery status auto-pruning max age
# Sprint 5.38: Alert throttling & circuit breaker
# Sprint 5.39: Circuit breaker auto-tuning
# Sprint 5.45: A/B experiment configuration
# Sprint 5.46: Experiment expiry/cleanup configuration
# Sprint 5.47: Rollback + live config reversion
# Sprint 5.48: Multi-armed bandit support
# Sprint 5.49: Contextual bandit support
# Sprint 5.50: Bandit decision logging
# Sprint 5.63: Connection pool for high-concurrency WS delivery
```

These Sprint references appear in:
- Module docstrings (lines 3-50): multi-Sprint changelog as the module description
- Class docstrings (e.g., `AlertConfig`): each configuration field annotated with its Sprint
- Inline comments: every method annotated with the Sprint that introduced it
- Schema version comments: `# Sprint 5.45: Schema migration v7 -> v8`

**`src/aip/adapter/alert_history_store.py`** — ~80 Sprint references across 3,159 lines

Same pattern as alerting.py. The module docstring (lines 3-15) is a multi-Sprint changelog.
Every schema migration, method, and configuration parameter is annotated with its Sprint.

**`src/aip/adapter/read_pool.py`** — ~20 Sprint references

Key patterns:
```
# Sprint 5.23 -> Sprint 5.24 auto-apply
# Sprint 5.24: How many consecutive observations required for auto-apply
# Sprint 5.24: Auto-apply mode
# Sprint 5.25: Auto-rollback configuration
# Sprint 5.27: Configurable exhaustion threshold (driven by AutoTuningPolicy)
# Sprint 6.3: busy_timeout even on read connections
```

**Other files with Sprint references:**
- `src/aip/adapter/auth/session_store.py` (1 reference: "Sprint 5.21")
- `src/aip/adapter/auto_tuning_policy.py` (3 references)
- `src/aip/adapter/graph_store.py` (2 references: "Sprint 6.3")

#### Classification for Removal

| Sprint Comment Type | Count | Should Remove? | Rationale |
|---|---|---|---|
| Module docstring Sprint changelog | ~10 | YES | Replace with a concise description of what the module does and its design constraints |
| Config field Sprint annotation | ~50 | YES | Replace with a comment explaining the field's purpose and valid range |
| Method docstring Sprint annotation | ~100 | PARTIAL | Keep if it explains the method's purpose; remove if it only says "Sprint X added this" |
| Inline Sprint comment | ~140 | PARTIAL | Keep if it explains a design constraint; remove if it only narrates implementation history |
| Schema migration Sprint comment | ~20 | NO | These document migration history and should be preserved, but reformat to remove Sprint number |

#### Recommended Replacement Pattern

**Before (fingerprint):**
```python
# Sprint 5.26: Webhook retry configuration
webhook_max_retries = 3
webhook_retry_base_delay_seconds = 1.0  # Sprint 5.26: exponential backoff base
```

**After (durable):**
```python
# Webhook delivery retry with exponential backoff
webhook_max_retries = 3
webhook_retry_base_delay_seconds = 1.0
```

### Category 2: Step-Number Scaffold Comments (22 instances)

| File | Line(s) | Pattern | Durable Design? | Recommendation |
|---|---|---|---|---|
| `cli/eval.py` | 783, 790, 804, 813, 820 | `# Step 1-5:` budget tuning | No — narrates implementation | Replace with descriptive labels |
| `beast_scan.py` | 51, 109, 127 | `# Step 1-3:` scan pipeline | Partially — documents search pipeline | Replace with descriptive labels |
| `definer_gate.py` | 263, 275, 287, 305 | `# Step 1-4:` approval gate | Yes — documents the approval protocol | Replace "Step N:" with section headers |
| `context_reset.py` | 57, 60, 71, 91, 102, 113 | `# Step 1-6:` context reset protocol | Yes — documents the 6-step protocol from spec | Replace "Step N:" with spec section references |
| `entity_extractor.py` | 316 | `# Step 1:` noun-phrase extraction | No — narrates single step | Remove or rephrase |
| `vigil.py` | 272, 285, 319, 334, 391, 455, 512 | `# --- Step 1-7 ---` vigil cycle | Partially — documents vigil evaluation cycle | Replace with section headers |
| `vigil.py` | 592, 604, 608, 642, 665, 673, 704 | `# Step 1-7:` retrieval quality | Partially — documents retrieval sampling | Replace with section headers |
| `l4/reset.py` | 118 | `# Step 4:` log reset event | No — narrates single step | Remove or rephrase |

#### Recommended Replacement Pattern

**Before (fingerprint):**
```python
# Step 1: Check structural validation
# Step 2: Check adversarial evaluation
# Step 3: Both validation and evaluation passed — check CI fixture status
# Step 4: Approve with appropriate marker
```

**After (durable):**
```python
# Structural validation must pass before evaluation is considered
# ...
# Adversarial evaluation must pass for production approval
# ...
# CI fixture results are blocked from auto-approval in production
# ...
# Approval is recorded with the appropriate source marker
```

### Category 3: CRITICAL: History Narrative

| File | Line | Pattern | Recommendation |
|---|---|---|---|
| `cli/init.py` | 44 | `CRITICAL: These schemas must match what VersionedArtifactStore,` | Remove "CRITICAL:" prefix. Rewrite as a durable design constraint: "Schema definitions here must stay in sync with VersionedArtifactStore and other store implementations." |

### Category 4: Placeholder Surfaces

| File | Line | Pattern | Current Behavior | Recommendation |
|---|---|---|---|---|
| `api/routes/projects.py` | 101 | `"""List work units for a project (placeholder)."""` | Stub endpoint | Either implement or return NOT_IMPLEMENTED status per AIP-G-02 |
| `api/routes/health.py` | 549 | `"placeholder" if container.vector_store is None else "configured"` | Status string | Replace "placeholder" with "not_configured" or "disabled" for honest status |

### Category 5: AI Scaffold in workflow_01.py

| File | Line(s) | Pattern | Description |
|---|---|---|---|
| `workflow/workflow_01.py` | 394-412 | CI fixture synthesis placeholder | `content="CI fixture synthesis (placeholder)"` with guard: "Refusing to commit with placeholder" — this is actually a GOOD pattern (honest about placeholder, blocks production use), but the naming could be clearer |
| `workflow/workflow_01.py` | 608 | `# Synthesis Node (replaces the bare AgentNode with placeholder wiring)` | Comment narrates replacement history rather than explaining current design |

---

## Removal Priority

| Priority | Category | Estimated Effort | Impact |
|---|---|---|---|
| **1** (Do first) | Placeholder surfaces (DDR-008 MCP, health.py) | Small | Direct AIP-G-02 fix |
| **2** | CRITICAL: history narrative (1 instance) | Trivial | Remove urgency language |
| **3** | Step-number scaffold (22 instances) | Low | Improves code readability |
| **4** | Sprint-number comments — config fields (~50) | Medium | Reduces noise in configuration |
| **5** | Sprint-number comments — method docstrings (~100) | High | Large volume but mostly cosmetic |
| **6** | Sprint-number comments — inline (~140) | High | Large volume; some contain useful info |
| **7** | Sprint-number comments — module docstrings (~10) | Medium | Needs rewrite, not just removal |
| **8** | Sprint-number comments — schema migration (~20) | Low | Preserve history, just reformat |

---

## Rules for Future Chunks

1. **When touching a file with Sprint comments**, remove Sprint-number references from the
   lines you are modifying. Do not do a separate Sprint-comment-removal pass unless the
   chunk specifically calls for it.

2. **Replace with durable comments** that explain WHY a feature exists, not WHEN it was added.

3. **Preserve schema migration history** by reformatting: `# Migration v7 -> v8: added A/B experiment tables`
   instead of `# Sprint 5.45: Schema migration v7 -> v8`.

4. **Do NOT remove comments that explain durable design constraints.** Examples of KEEP:
   - `# GUI writes must run inside the NiceGUI client context because background tasks do not inherit the active UI slot.`
   - `# Use policy-driven exhaustion threshold instead of hardcoded value`
   - `# CI fixture results are blocked from auto-approval in production mode`

5. **Do NOT add new Sprint-number comments or Step-number scaffolding.** Use descriptive
   section headers instead.
