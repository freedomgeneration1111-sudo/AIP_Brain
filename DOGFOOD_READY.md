---
## ⚠️ Read This First — Alpha Test Release (2026-06-10)

AIP v0.1 is now in **alpha test release**. The dogfood guide below describes the foundational
ingest→ask→review→export loop. This still works. However AIP has grown significantly since
that guide was written, and there are important caveats for alpha testers:

**What works well:**
- FTS5 full-text search across your ingested corpus
- Ingest, tag, ask, review, approve, export pipeline
- Augmented chat with Beast context advisory
- Knowledge graph visualization at /graph-viz
- CLI evaluation tools (`aip eval retrieval`)

**Known limitations:**
- **Embedding coverage is ~1.8%** — only 50 of 2,766 turns are embedded. Hybrid retrieval
  (vector + FTS5) will show limited benefit until a full embedding pass completes.
- **DEBT-006 (Critical):** Automatic tagging, embedding, wiki, and graph extraction are NOT
  running automatically. Use `aip corpus tag` and `aip embed` manually until this is fixed.
- **MCP dispatch is built but not runtime-wired** — no MCP operations are reachable via API/CLI today
- **No review queue web UI** — use `aip review list/approve/reject` via CLI

The current recommended first-run sequence is:

### Step 0 — Initialize
```bash
git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
cd AIP_Brain
uv sync
uv run aip init
```

### Step 1 — Ingest your Claude conversations
Export your conversations from claude.ai (Settings → Export data).
Unzip and ingest:
```bash
uv run aip corpus ingest /path/to/conversations.json \
  --source-model claude \
  --source-account claude_export_$(date +%Y_%m)
```

### Step 2 — Tag your corpus with Sexton
```bash
uv run aip corpus tag --limit 500
# For a large corpus this takes time — Sexton calls LLM for each batch of 8 turns
# Run with --retag after updating docs/beast_domain_registry_v1.md
```

### Step 3 — Check corpus status
```bash
uv run aip status
# Shows: total turns, tagged/untagged, domain distribution
```

### Step 4 — Start the server and use augmented chat
```bash
./scripts/start.sh
# Opens backend on http://127.0.0.1:8000
# Opens GUI on http://127.0.0.1:8080
# Switch to AUGMENTED tab for Beast-context-enhanced responses
```

Note: `uv run aip serve` does not exist. Always use `./scripts/start.sh` to
start both the FastAPI backend and the NiceGUI shell together.

### Step 5 — Review Sexton's domain proposals
Any domains Sexton couldn't classify appear in the review queue:
```bash
uv run aip review list
```

The original dogfood guide continues below for the foundational
ask→review→approve→export pipeline.

---

# DOGFOOD_READY.md — AIP First-Run Guide

This document provides the exact commands needed for a fresh user to go from
clean checkout to a working knowledge base with review and export — without
reading source code or guessing database paths.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended package manager)
- Optional: Ollama (for local model inference)

## 1. Install

```bash
git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
cd AIP_Brain
uv sync
```

## 2. Initialize

```bash
uv run aip init
```

This creates:
- `config/aip.config.toml` — configuration file with `db_path = "db/state.db"`
- `db/state.db` — main database (artifacts, projects, events, ECS state, canonicals)
- `db/lexical.db` — FTS5 full-text search index
- `db/trace.db` — trace events and routing outcomes

**All CLI commands use the same databases** initialized by `aip init`.
You do NOT need to pass `--db-path` manually.

Verify with:
```bash
uv run aip status
```

## 3. Create a Project

```bash
uv run aip project create --name aip_loom --domain aip_loom
```

This creates a project named `aip_loom` with domain `aip_loom`.
The domain is the tag used for indexing and retrieval.

List projects:
```bash
uv run aip project list
```

## 4. Ingest Conversations

```bash
# Using --domain (matches the project's domain)
uv run aip ingest directory examples/sample_threads --domain aip_loom

# Using --project (resolves the domain automatically from the project)
uv run aip ingest directory examples/sample_threads --project aip_loom

# Single file
uv run aip ingest file examples/sample_threads/aip_loom_decisions.md --project aip_loom
```

Supported formats (auto-detected):
- ChatGPT JSON export (`conversations.json`)
- Markdown transcripts (`**Role**: content`)
- Plain text (`Role: content`)

The output will show the domain the content was indexed into.

## 5. Ask a Question

### Without a Model Configured

```bash
uv run aip ask "What have we decided about artifact storage?" \
  --project aip_loom \
  --source all \
  --show-context
```

If no model provider is configured, this returns `NEEDS_CONFIGURATION`
but **still shows the retrieved sources**. This proves that ingestion
and retrieval are working correctly.

### With a Model Configured

Set environment variables for your model provider:

```bash
# Option A: OpenAI-compatible API
export AIP_SYNTHESIS_BASE_URL="https://api.openai.com/v1"
export AIP_SYNTHESIS_MODEL="gpt-4o"
export AIP_SYNTHESIS_API_KEY="sk-..."

# Option B: Local Ollama
# Make sure Ollama is running: ollama serve
export AIP_SYNTHESIS_BASE_URL="http://127.0.0.1:11434/v1"
export AIP_SYNTHESIS_MODEL="llama3"
```

Then ask with `--save-artifact` to save the generated answer:

```bash
uv run aip ask "What have we decided about artifact storage?" \
  --project aip_loom \
  --source all \
  --show-context \
  --save-artifact
```

The output will include:
- The generated answer
- Source citations
- The artifact ID (e.g., `ask:abc123...`)
- Instructions to review the artifact

## 6. Model Configuration

### Environment Variables

| Variable | Purpose |
|----------|---------| 
| `AIP_SYNTHESIS_BASE_URL` | API endpoint for the synthesis slot |
| `AIP_SYNTHESIS_MODEL` | Model name for synthesis |
| `AIP_SYNTHESIS_API_KEY` | API key (if required) |
| `AIP_EVALUATION_BASE_URL` | API endpoint for the evaluation slot |
| `AIP_SEXTON_BASE_URL` | API endpoint for the sexton slot |
| `AIP_OLLAMA_BASE_URL` | Ollama endpoint (default: `http://localhost:11434`) |

### Config File

You can also configure model providers in `config/aip.config.toml`:

```toml
[synthesis]
base_url = "https://api.openai.com/v1"
model = "gpt-4o"

[evaluation]
base_url = "http://127.0.0.1:11434/v1"
model = "llama3"
```

## 7. Review and Approve

```bash
# List generated artifacts pending review
uv run aip review list --project aip_loom

# Show artifact content and details
uv run aip review show <artifact_id>

# Show source/provenance links
uv run aip review sources <artifact_id>

# Approve the artifact (GENERATED → REVIEWED → APPROVED)
uv run aip review approve <artifact_id>

# Reject the artifact (preserves it for re-generation)
uv run aip review reject <artifact_id> --note "Reason for rejection"

# Mark as needing revision (stays in current state, records instruction)
uv run aip review needs-revision <artifact_id> --note "Add more detail about X"
```

**Lifecycle states:**
- `GENERATED` → `REVIEWED` → `APPROVED` (full approval path)
- `GENERATED` → `REJECTED` (preserved, can be re-generated)
- `APPROVED` → `SUPERSEDED` (replaced by newer version)

No auto-approve path exists. All approvals require explicit DEFINER action.

## 8. Export

```bash
# Export an approved artifact to markdown
uv run aip export artifact <artifact_id> \
  --format markdown \
  --out ./exports/output.md

# Export all approved artifacts for a project
uv run aip export project aip_loom \
  --format markdown \
  --out ./exports/aip_loom.md

# Force export of unapproved artifact (not recommended)
uv run aip export artifact <artifact_id> \
  --format markdown \
  --out ./exports/draft.md \
  --force
```

> ⚠️ **Warning:** Force export bypasses the DEFINER approval gate. This should only be used for debugging or local review, never in a shared or production context.

Exported markdown includes:
- YAML frontmatter (artifact_id, project, lifecycle_state, timestamps, model info)
- Generated content
- Provenance footer (source IDs, types, and export timestamp)

**Export rules:**
- APPROVED artifacts export without `--force`
- GENERATED/REVIEWED artifacts require `--force`
- REJECTED artifacts require `--force` (not recommended)

## 9. Automated Smoke Test

```bash
bash scripts/dogfood_smoke_test.sh
```

This runs the full dogfood loop automatically on a clean datastore,
including init, project creation, ingestion, ask, review, approve, and export.

## Troubleshooting

### Project Not Found

```
Error: Project 'my_project' not found
```

**Fix:** Create the project first:
```bash
uv run aip project create --name my_project --domain my_project
```

### No Project Memory

```
AIP: NO PROJECT MEMORY
No relevant sources found in project 'aip_loom'
```

**Fix:** Ingest conversations into the project's domain:
```bash
uv run aip ingest directory <path> --project aip_loom
# or: aip ingest directory <path> --domain aip_loom
```

### No Model Provider Configured

```
AIP: NEEDS CONFIGURATION
No model provider is configured.
```

**Fix:** Set model environment variables or configure in `config/aip.config.toml`.
The retrieved sources are still shown so you can verify ingestion worked.

See [Model Configuration](#6-model-configuration) for details.

### Review List Empty

```
No artifacts found for project 'aip_loom'.
```

**Fix:** Generate an artifact first:
```bash
uv run aip ask "<question>" --project aip_loom --save-artifact
```

### Export Refused (Artifact Unapproved)

```
Error (UNREVIEWED_ARTIFACT): Artifact is in GENERATED state
```

**Fix:** Approve the artifact first:
```bash
uv run aip review approve <artifact_id>
```

Or use `--force` to export an unapproved artifact (not recommended for production).

### Database Path Mismatch

If you see data in `aip status` but not in `aip ask`/`aip review`:

1. Check that `config/aip.config.toml` has `db_path = "db/state.db"`
2. Re-run `aip init` to ensure the config is correct
3. Verify with `aip status` that it shows "Main DB: db/state.db"

All CLI commands now use the same default database path. If you previously
used `data/aip.db`, you can migrate by copying it to `db/state.db`:
```bash
cp data/aip.db db/state.db
```

### Ingest --domain vs --project

Both work:
- `--domain aip_loom` indexes content directly into the `aip_loom` domain
- `--project aip_loom` looks up the project's domain and indexes into it

If both are specified, `--project` takes precedence and resolves the domain
from the project store. If the project has domain `aip_loom` and you pass
`--domain other`, a warning is shown and the project's domain is used.

### Server Won't Start / `aip serve` Not Found

`uv run aip serve` does not exist. Use:
```bash
./scripts/start.sh
```

This starts both the FastAPI backend (port 8000) and the NiceGUI shell (port 8080).
