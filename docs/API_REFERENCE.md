# AIP 0.1 API Reference

Comprehensive reference for all REST and WebSocket endpoints exposed by AIP 0.1 as of **Sprint 6.4 (Alpha Test Release)**.

**Base URL**: `http://localhost:8000/api/v1`

---

## Table of Contents

- [Authentication](#authentication)
- [Health](#health)
- [Projects](#projects)
- [Sessions](#sessions)
- [Chat (WebSocket)](#chat-websocket)
- [Artifacts](#artifacts)
- [Reviews](#reviews)
- [Admin](#admin)
- [Memory](#memory)
- [Models](#models)
- [Actors](#actors)
- [Ingest](#ingest)
- [Ask](#ask)
- [Knowledge](#knowledge)
- [ECS](#ecs)
- [Sources](#sources)
- [Collaborators](#collaborators)
- [Plugins](#plugins)
- [Performance](#performance)
- [Graph](#graph)
- [Corpus](#corpus)
- [Vigil Quality](#vigil-quality)
- [Retrieval Dashboard](#retrieval-dashboard)
- [Rate Limiting](#rate-limiting)
- [Error Responses](#error-responses)

---

## Authentication

When `[auth] auth_enabled = true`:
- Include `Authorization: Bearer <session_token>` header
- Or include `X-API-Key: <api_key>` header for non-interactive access

When `[auth] auth_enabled = false` (default for laptop profile):
- No authentication required

Certain endpoints require **DEFINER** authorization (via `require_definer` dependency). These are noted with **Auth: Definer** in the endpoint documentation.

---

## Health

### `GET /api/v1/health`

Public health check. No authentication required.

Computes real uptime, checks component availability, probes database write connectivity, and reports system status as `ok`, `degraded`, or `unhealthy`.

**Response**:
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "ci_mode": false,
  "critical_components": true,
  "optional_components": {
    "lexical_store": true,
    "vector_store": true,
    "embedding_provider": true,
    "project_store": true,
    "budget_store": true,
    "budget_manager": true,
    "vigil_store": true,
    "model_provider": true,
    "knowledge_store": true,
    "session_store": true,
    "ecs_store": true,
    "review_queue_store": true,
    "trace_store": true
  },
  "optional_available": 13,
  "optional_total": 13,
  "vector_backend": "configured",
  "model_slots": ["synthesis", "evaluation", "sexton", "embedding"],
  "actors": {
    "beast": { "initialized": true },
    "vigil": { "initialized": true },
    "sexton": { "initialized": true }
  },
  "budget_status": "active",
  "db_writable": true,
  "retrieval_channel_health": {
    "fts": {
      "registered": true,
      "state": "active"
    },
    "vector": {
      "registered": true,
      "state": "active",
      "backend_type": "sqlite_vss",
      "vss_available": true,
      "vector_count": 50,
      "embedding_provider_configured": true
    },
    "corpus": {
      "registered": true,
      "state": "active"
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"ok"` — all components available; `"degraded"` — some optional components missing; `"unhealthy"` — critical components missing |
| `uptime_seconds` | int | Seconds since application start |
| `ci_mode` | bool | Whether the model provider is running in CI/stub mode |
| `critical_components` | bool | True if all 5 required components (entity, canonical, event, autonomy gate, artifact stores) are initialized |
| `optional_components` | object | Per-component availability map |
| `optional_available` | int | Count of available optional components |
| `optional_total` | int | Total number of optional components |
| `vector_backend` | string | `"configured"` or `"placeholder"` |
| `model_slots` | array | Configured model slot names |
| `actors` | object | Beast/Vigil/Sexton initialization status |
| `budget_status` | string | `"active"`, `"unconfigured"`, or `"error"` |
| `db_writable` | bool | Whether a lightweight write to the event store succeeded |
| `retrieval_channel_health` | object | **(Chunk 5)** Per-channel retrieval health. Keys are channel names (`fts`, `vector`, `corpus`). Each value contains `registered` (bool), `state` (string: active, unavailable, not_configured, degraded, failed, empty, disabled), and for the vector channel additionally: `backend_type`, `vss_available`, `vector_count`, `embedding_provider_configured` |

---

### `GET /api/v1/health/dogfood`

Detailed health check for dogfood operators. No authentication required. Returns extended diagnostic information beyond the basic `/health` endpoint.

**Response** (excerpt showing Chunk 5 additions):
```json
{
  "sexton": "active",
  "embedding_backfill_state": "not_configured",
  "channel_states": {
    "fts": "available",
    "vector": "not_configured",
    "corpus": "available"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `channel_states` | object | **(Chunk 5)** Per-channel state summary. Values: `"available"`, `"unavailable"`, `"not_configured"`, `"degraded"`. Distinguishes between channels that are present and healthy vs. channels that are absent or misconfigured. |

---

## Projects

### `GET /api/v1/projects`

List all projects.

**Auth**: Optional | **Autonomy**: read

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | null | Filter by project status |

**Response**: `{"projects": [...]}`

---

### `POST /api/v1/projects`

Create a new project.

**Auth**: Required | **Autonomy**: write | **Gate**: Yes

**Request Body**:
```json
{
  "name": "My Project",
  "description": "Project description",
  "domain": "science"
}
```

**Response**: `{"id": "proj-001", "name": "My Project", "domain": "science"}`

---

### `GET /api/v1/projects/{project_id}`

Get project details.

**Auth**: Optional | **Autonomy**: read

**Response**: `{"id": "proj-001", "work_units": [...]}`

---

### `GET /api/v1/projects/{project_id}/work_units`

List work units within a project.

**Auth**: Optional | **Autonomy**: read

**Response**: `{"project_id": "proj-001", "work_units": [...]}`

---

## Sessions

Session lifecycle with ACE Playbook loading, auto-save ingestion tracking, and trajectory regulation support.

### `POST /api/v1/sessions`

Create a new chat session. Returns a `session_id` used for WebSocket communication.

**Auth**: Optional

**Request Body**:
```json
{
  "project_id": "proj-001",
  "domain": "science",
  "role": "beast",
  "model_slot": "synthesis",
  "mode": "normal",
  "auto_save": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | null | Project context for this session |
| `domain` | string | null | Domain context |
| `role` | string | null | Active actor role (e.g. `"beast"`, `"vigil"`, `"embedding"`) |
| `model_slot` | string | `"synthesis"` | Model slot for chat dispatch |
| `mode` | string | `"normal"` | `"normal"` (direct model dispatch) or `"augmented"` (retrieval + context injection) |
| `auto_save` | bool | `true` | Auto-ingest chat turns after each exchange |

**Response**:
```json
{
  "id": "sess-a1b2c3d4e5f6",
  "project_id": "proj-001",
  "domain": "science",
  "role": "beast",
  "model_slot": "synthesis",
  "mode": "normal",
  "auto_save": true,
  "ingestion_status": "idle",
  "ace_playbook_loaded": true
}
```

---

### `GET /api/v1/sessions`

List all active sessions.

**Response**: `{"sessions": [...]}`

---

### `GET /api/v1/sessions/{session_id}`

Get session details including role, model slot, and turn count.

**Response**: Session metadata object.

---

### `GET /api/v1/sessions/{session_id}/context`

Get session context including turn count and context window estimate.

**Response**:
```json
{
  "session_id": "sess-a1b2c3d4e5f6",
  "turn_count": 5,
  "context_window_estimate": 2400,
  "role": "beast",
  "model_slot": "synthesis"
}
```

---

### `PATCH /api/v1/sessions/{session_id}`

Update session metadata. Accepts a partial dict of fields to update. Immutable fields (`id`, `session_id`, `created_at`) are ignored.

**Request Body**:
```json
{
  "auto_save": false,
  "mode": "augmented",
  "role": "vigil"
}
```

**Response**: Updated session metadata object.

---

### `DELETE /api/v1/sessions/{session_id}`

Delete a session by ID. Removes from both in-memory and persistent SessionStore.

**Response**: `{"deleted": true, "session_id": "sess-..."}`

---

## Chat (WebSocket)

### `WebSocket /api/v1/chat/{session_id}`

WebSocket chat endpoint with model slot routing, DEFINER gate handling, and optional retrieval-augmented generation.

The GUI connects to this endpoint after creating a session via `POST /api/v1/sessions`. Each message is routed through the configured model slot (from session metadata), allowing the backend's `ModelSlotResolver` to dispatch to the appropriate provider.

> **Important**: This is a **WebSocket** endpoint, not a POST endpoint. Connect via `ws://` or `wss://` protocol.

**Message Flow (Normal Mode)**:
```
message → ModelSlotResolver.call() → response → [auto-save ingestion]
```

**Message Flow (Augmented Mode)**:
```
message → retrieve sources → assemble context → ModelSlotResolver.call() → response + sources → [auto-save ingestion]
```

**Incoming Messages**:

#### `type: "message"` — Chat message

```json
{
  "type": "message",
  "content": "Synthesize a summary of quantum entanglement",
  "model_slot": "synthesis"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Must be `"message"` |
| `content` | string | Yes | The user's message text |
| `model_slot` | string | No | Override the session's default model slot for this message only |

**Response (success)**:
```json
{
  "type": "response",
  "content": "Quantum entanglement is...",
  "model_slot": "synthesis",
  "model": "gpt-4",
  "artifacts": [],
  "tokens_used": 450,
  "latency_ms": 1200,
  "cost_usd": 0.009,
  "auto_save": true,
  "sources": [],
  "mode": "normal"
}
```

In **augmented mode**, the `sources` array is populated with retrieval results:
```json
{
  "type": "response",
  "content": "Based on the sources...",
  "sources": [
    {
      "source_id": "src-001",
      "source_type": "lexical",
      "title": "Quantum Mechanics Notes",
      "score": 0.92,
      "content_snippet": "Entanglement occurs when...",
      "domain": "physics"
    }
  ],
  "mode": "augmented",
  "review_available": true
}
```

**Error responses**:
```json
{
  "type": "error",
  "content": "Model call failed: ...",
  "model_slot": "synthesis"
}
```

```json
{
  "type": "error",
  "content": "Budget limit reached...",
  "error_type": "budget_exhausted",
  "model_slot": "synthesis"
}
```

#### `type: "gate_response"` — Review gate decision

```json
{
  "type": "gate_response",
  "approved": true,
  "queue_item_id": 42
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Must be `"gate_response"` |
| `approved` | bool | Yes | Whether the DEFINER approves or rejects |
| `queue_item_id` | int | Yes | The review queue item ID |

#### `type: "ping"` — Keepalive

```json
{"type": "ping"}
```

Response: `{"type": "pong"}`

**Server-initiated messages**:

| Message Type | Description |
|-------------|-------------|
| `trajectory_warning` | Sent when trajectory degradation is detected (loop, context bloat, etc.) |
| `error` | Generic error with `content` and optional `model_slot` |

---

## Artifacts

Read-only artifact browsing. No AutonomyGate required.

### `GET /api/v1/artifacts`

List artifacts with pagination and filtering.

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_id` | string | null | Filter by project |
| `ecs_state` | string | null | Filter by ECS state |
| `domain` | string | null | Filter by domain |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Items per page (clamped by `surface.artifact_page_size`) |

**Response**:
```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

---

### `GET /api/v1/artifacts/{artifact_id}`

Get artifact content and metadata.

**Response**: `{"id": "art-001", "ecs_state": "GENERATED", "versions": 1}`

---

### `GET /api/v1/artifacts/{artifact_id}/versions`

List all versions of an artifact.

**Response**: `{"artifact_id": "art-001", "versions": [...]}`

---

### `GET /api/v1/artifacts/{artifact_id}/evaluation`

Get evaluation results (faithfulness, domain coherence, etc.) for an artifact.

**Response**:
```json
{
  "artifact_id": "art-001",
  "faithfulness": 0.92,
  "domain_coherence": 0.85
}
```

---

## Reviews

Review queue for artifacts awaiting DEFINER approval. Approve/reject execute real ECS state transitions and canonical writes.

### `GET /api/v1/reviews`

List pending artifacts for review (ECS state = GENERATED or REVIEWED).

**Auth**: Required | **Autonomy**: read

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `domain` | string | null | Filter by domain |
| `project_id` | string | null | Filter by project |
| `ecs_state` | string | null | Filter by ECS state |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Items per page (clamped by `surface.review_page_size`) |

**Response**:
```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

---

### `POST /api/v1/reviews/{artifact_id}/approve`

Approve an artifact for canonical promotion. Requires DEFINER authorization, admin-level AutonomyGate escalation, and executes:
1. ECS transition: `REVIEWED → APPROVED`
2. Canonical write: artifact content written to CanonicalStore
3. Event recording: `review_approved` event logged

**Auth**: Definer | **Autonomy**: admin | **Gate**: Yes

**Response**:
```json
{
  "artifact_id": "art-001",
  "new_state": "APPROVED",
  "canonical_written": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `artifact_id` | string | The approved artifact's ID |
| `new_state` | string | Always `"APPROVED"` on success |
| `canonical_written` | bool | Whether the canonical store write succeeded (non-fatal if `false` — ECS state is already transitioned) |

**Error responses**:
- `403` — AutonomyGate blocked the escalation
- `500` — ECS transition failed
- `503` — AutonomyGate not wired

---

### `POST /api/v1/reviews/{artifact_id}/reject`

Reject an artifact. Requires DEFINER authorization, write-level AutonomyGate escalation, and executes:
1. ECS transition: `REVIEWED → FAILED`
2. Event recording: `review_rejected` event logged
3. No canonical write is performed

**Auth**: Definer | **Autonomy**: write | **Gate**: Yes

**Response**:
```json
{
  "artifact_id": "art-001",
  "new_state": "FAILED"
}
```

---

## Admin

Administrative endpoints for configuration, actor inspection, budget monitoring, and audit logging.

### `GET /api/v1/admin/config`

Get the current runtime configuration.

**Response**: Full config dict, or `{"status": "unconfigured"}`.

---

### `PATCH /api/v1/admin/config`

Apply runtime configuration changes with hot-reload support.

**Auth**: Definer | **Autonomy**: admin | **Gate**: Yes

Safe keys are applied immediately (hot-reloaded). Unsafe keys require a process restart.

| Key Category | Hot-Reloadable | Examples |
|-------------|---------------|----------|
| `budget` | Yes | Token limits, cost caps |
| `beast` | Yes | Cadence intervals |
| `vigil` | Yes | Thresholds, check intervals |
| `sexton` | Yes | Classification parameters |
| `performance` | Yes | Profiling toggles |
| `rate_limit` | Yes | Rate limit thresholds |
| `surface` | Yes | Page sizes, CORS origins |
| `retrieval.channel_weights` | Yes | Vector, FTS, corpus weights |
| `db_path` | **No** | Requires restart |
| `auth` | **No** | Requires restart |
| `models.*` | **No** | Requires restart (use slot API) |

**Request Body**:
```json
{
  "budget": {"hard_stop": true},
  "beast": {"health_check_interval_seconds": 600}
}
```

**Response**:
```json
{
  "updated": true,
  "applied": {
    "budget": {"hard_stop": true},
    "beast": {"health_check_interval_seconds": 600}
  },
  "not_applied": {},
  "note": null
}
```

When unsafe keys are present:
```json
{
  "updated": true,
  "applied": {"budget": {"hard_stop": true}},
  "not_applied": {
    "db_path": "requires restart"
  },
  "note": "Safe keys (budget, beast, vigil, sexton, performance, rate_limit, surface) are applied immediately. Other keys require a process restart."
}
```

---

### `GET /api/v1/admin/sexton/classifications`

Get Sexton failure classifications.

**Response**: `{"classifications": [{"failure_type": "...", "trace_event_id": "...", "confidence": 0.9}]}`

---

### `GET /api/v1/admin/sexton/audit`

Get Sexton stale rule audit results.

**Response**: `{"audits": [...]}`

---

### `GET /api/v1/admin/sexton/playbook`

Get ACE Playbook entries derived from Sexton classifications.

**Response**: `{"entries": [...]}`

---

### `GET /api/v1/admin/beast/status`

Get Beast actor health status and last cycle info.

**Response**: `{"last_run": null, "next": null, "health": "ok"}`

---

### `GET /api/v1/admin/router/weights`

Get AdaptiveRouter routing weights.

**Response**: `{"weights": [...]}`

---

### `GET /api/v1/admin/budget`

Get budget status for a given scope and scope ID.

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `scope` | string | `"session"` | Budget scope: `"session"`, `"project"`, or `"daily"` |
| `scope_id` | string | `"default"` | Scope identifier (session ID, project ID, or ISO date for daily) |

**Response**:
```json
{
  "scope": "session",
  "scope_id": "sess-abc123",
  "tokens_used": 5000,
  "tokens_limit": 50000,
  "cost_usd": 0.10,
  "cost_limit": 5.00
}
```

When budget manager is not configured: `{"status": "unconfigured", "budget_manager": false}`

---

### `GET /api/v1/admin/autonomy/log`

Get AutonomyGate escalation audit log.

**Response**: `{"escalations": [...]}`

---

## Memory

Read-only memory inspection across trace, event, entity, canonical, and search stores. No AutonomyGate required.

### `GET /api/v1/memory/search`

Hybrid search across lexical (FTS5) and vector stores.

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | required | Search query |
| `domain` | string | null | Filter by domain |
| `limit` | int | 10 | Max results |

**Response**:
```json
{
  "results": [
    {"id": "...", "content": "...", "score": 0.92, "source": "lexical"},
    {"id": "...", "content": "...", "score": 0.88, "source": "vector"}
  ]
}
```

---

### `GET /api/v1/memory/trace/{session_id}`

Get trace events for a specific session.

**Response**: `{"session_id": "sess-...", "events": [...]}`

---

### `GET /api/v1/memory/events/{project_id}`

Get event timeline for a project (from EventStore).

**Response**: `{"project_id": "proj-...", "timeline": [...]}`

---

### `GET /api/v1/memory/entities`

List all entities from the EntityStore.

**Response**: `{"entities": [...]}`

---

### `GET /api/v1/memory/canonical`

List all canonical artifacts from the CanonicalStore.

**Response**: `{"canonicals": [...]}`

---

## Models

Model slot configuration and resolution.

### `GET /api/v1/models/slots`

List all configured model slots with resolved provider and model info. This is the primary endpoint the GUI uses to populate model/role dropdowns.

**Response**:
```json
{
  "slots": [
    {
      "slot_name": "synthesis",
      "provider": "openai",
      "model": "gpt-4",
      "base_url": "https://api.openai.com/v1",
      "has_fallback": true,
      "fallback_provider": "anthropic",
      "fallback_model": "claude-3-sonnet"
    }
  ],
  "ci_mode": false
}
```

---

### `GET /api/v1/models/slots/{slot_name}`

Get detailed info for a single model slot.

**Response**: Single slot object (same shape as items in the list above).

**Error**: `404` if slot name is not found.

---

## Actors

Orchestration actor status and manual trigger endpoints.

### `GET /api/v1/actors/status`

Get status of all three orchestration actors (Beast, Vigil, Sexton).

**Response**:
```json
{
  "actors": {
    "beast": {
      "initialized": true,
      "health": "ok",
      "last_cycle_time": "2025-01-15T10:30:00Z",
      "interval_seconds": 300
    },
    "vigil": {
      "initialized": true,
      "health": "ok",
      "interval_seconds": 3600,
      "stale_threshold_days": 30
    },
    "sexton": {
      "initialized": true,
      "unclassified_count": 5,
      "interval_seconds": 300
    }
  }
}
```

---

### `GET /api/v1/actors/{actor_name}`

Get detailed status for a single actor (`beast`, `vigil`, or `sexton`).

**Response**: Actor-specific detail including configuration and operational state.

**Error**: `404` for unknown actor names.

---

### `POST /api/v1/actors/{actor_name}/trigger`

Manually trigger an actor cycle for debugging/administration. Actor name must be `beast`, `vigil`, or `sexton`.

**Response** (Beast example):
```json
{
  "actor": "beast",
  "triggered": true,
  "result": {"health_overall": "ok"}
}
```

**Error**: `404` for unknown actor names; `{"error": "Beast not initialized"}` if the actor is not wired.

---

## Ingest

API-driven conversation and file ingestion. Triggers the full pipeline: parse → chunk → FTS5 index → vector upsert.

### `POST /api/v1/ingest/conversation`

Ingest a live chat conversation through the ingestion pipeline.

**Request Body**:
```json
{
  "conversation_id": "conv-001",
  "title": "Quantum Physics Discussion",
  "turns": [
    {"role": "user", "content": "What is entanglement?", "timestamp": "2025-01-15T10:00:00Z"},
    {"role": "assistant", "content": "Entanglement is...", "timestamp": "2025-01-15T10:00:05Z"}
  ],
  "domain": "physics",
  "source_format": "plaintext",
  "auto_save": false
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `conversation_id` | string | auto-generated | Unique ID for this conversation |
| `title` | string | auto-generated | Conversation title |
| `turns` | array | required | List of `{role, content, timestamp}` dicts |
| `domain` | string | `"chat"` | Domain for indexing |
| `source_format` | string | `"plaintext"` | Source format identifier |
| `auto_save` | bool | `false` | Whether this was triggered by auto-save |

**Response**:
```json
{
  "conversation_id": "conv-001",
  "artifact_id": "art-conv-001",
  "turn_count": 2,
  "chunk_count": 4,
  "vector_indexed": true,
  "lexical_indexed": true,
  "errors": []
}
```

**Error responses**: `400` (empty turns), `503` (required stores not wired), `500` (pipeline failure).

---

### `POST /api/v1/ingest/file`

Ingest a conversation file from disk.

**Request Body**:
```json
{
  "path": "/data/conversations/chat.md",
  "domain": "imported",
  "source_format": "markdown"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | File path to ingest |
| `domain` | string | No | Domain (default: `"imported"`) |
| `source_format` | string | No | Format override |

**Response**:
```json
{
  "results": [
    {
      "conversation_id": "...",
      "artifact_id": "...",
      "turn_count": 10,
      "chunk_count": 20,
      "vector_indexed": true,
      "lexical_indexed": true,
      "errors": []
    }
  ]
}
```

**Error responses**: `400` (no path), `404` (file not found), `503` (stores not wired), `500` (pipeline failure).

---

## Ask

Source-grounded knowledge queries via the ask pipeline.

### `POST /api/v1/ask`

Execute a source-grounded ask query against the AIP knowledge substrate. Retrieves relevant sources, assembles context, dispatches to the configured model, and returns an answer with provenance.

**Request Body**:
```json
{
  "question": "What are the key principles of quantum entanglement?",
  "project_name": "Physics Research",
  "source": "all",
  "max_sources": 10,
  "save_artifact": false,
  "model_slot": "synthesis"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | string | required | The query text |
| `project_name` | string | required | Project to search within |
| `source` | string | `"all"` | `"ingested"`, `"artifacts"`, or `"all"` |
| `max_sources` | int | 10 | Max sources to retrieve |
| `save_artifact` | bool | false | Save answer as draft artifact |
| `model_slot` | string | `"synthesis"` | Model slot to use |

**Response**:
```json
{
  "status": "ok",
  "answer": "Quantum entanglement is...",
  "sources": [
    {
      "source_id": "src-001",
      "source_type": "lexical",
      "title": "Quantum Notes",
      "score": 0.95,
      "content_snippet": "Entanglement occurs when...",
      "domain": "physics",
      "metadata": {}
    }
  ],
  "model_slot": "synthesis",
  "model_provider": "openai",
  "artifact_id": null,
  "session_id": null,
  "project_id": "proj-001",
  "project_name": "Physics Research",
  "prompt": "...",
  "errors": []
}
```

**Error responses**: `400` (missing question/project), `503` (required stores not wired), `500` (pipeline failure).

---

### `POST /api/v1/ask/retrieve`

Retrieve sources for a query without generating an answer. Lightweight endpoint for the search panel.

**Request Body**:
```json
{
  "question": "quantum entanglement",
  "project_name": "Physics Research",
  "domain": "physics",
  "source": "all",
  "max_sources": 20
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `question` | string | required | The query text |
| `project_name` | string | null | Project domain to filter by |
| `domain` | string | null | Domain to filter by (alternative to project_name) |
| `source` | string | `"all"` | Source filter |
| `max_sources` | int | 20 | Max sources to retrieve |

**Response**:
```json
{
  "question": "quantum entanglement",
  "domain": "physics",
  "sources": [
    {
      "source_id": "src-001",
      "source_type": "lexical",
      "title": "...",
      "score": 0.95,
      "content_snippet": "...",
      "domain": "physics",
      "metadata": {}
    }
  ],
  "total": 5
}
```

---

## Knowledge

Compiled knowledge browsing and search. Knowledge items track provenance to source canonicals and follow a compilation state machine: `SPECIFIED → COMPILED → REVIEWED → APPROVED`.

### `GET /api/v1/knowledge`

List compiled knowledge items, optionally filtered by domain and state.

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `domain` | string | null | Filter by domain |
| `state` | string | null | Filter by compilation state |

**Response**:
```json
{
  "items": [
    {
      "knowledge_id": "kno-001",
      "content": "...",
      "source_canonical_ids": ["can-001", "can-002"],
      "domain": "physics",
      "state": "APPROVED",
      "metadata": {},
      "created_at": "2025-01-15T10:00:00Z",
      "updated_at": "2025-01-15T11:00:00Z"
    }
  ],
  "total": 1
}
```

**Error**: `503` if KnowledgeStore is not available.

---

### `GET /api/v1/knowledge/search`

Search compiled knowledge by query and domain. Performs hybrid search: lexical (FTS5) + vector semantic search.

> **Note**: This route is registered before `/knowledge/{knowledge_id}` to avoid the path parameter capturing `"search"` as a knowledge_id.

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | required | Search query |
| `domain` | string | null | Filter by domain |
| `limit` | int | 10 | Max results |

**Response**:
```json
{
  "results": [...],
  "total": 5,
  "query": "quantum entanglement"
}
```

**Error**: `400` (empty query), `503` (store not available).

---

### `GET /api/v1/knowledge/{knowledge_id}`

Get a specific compiled knowledge item by ID, including provenance chain.

**Response**:
```json
{
  "knowledge_id": "kno-001",
  "content": "...",
  "source_canonical_ids": ["can-001"],
  "domain": "physics",
  "state": "APPROVED",
  "provenance": [
    {"canonical_id": "can-001", "compiled_at": "2025-01-15T10:00:00Z"}
  ]
}
```

**Error**: `404` if not found, `503` if store not available.

---

## ECS

Entity Component System state graph and artifact lifecycle visualization.

### `GET /api/v1/ecs/graph`

Return the ECS state graph definition and artifact distribution.

**Response**:
```json
{
  "transitions": {
    "SPECIFIED": ["GENERATED"],
    "GENERATED": ["REVIEWED", "REJECTED"],
    "REVIEWED": ["APPROVED", "FAILED"],
    "APPROVED": ["SUPERSEDED"],
    "REJECTED": ["GENERATED"],
    "FAILED": ["SPECIFIED"]
  },
  "all_states": ["APPROVED", "FAILED", "GENERATED", "REJECTED", "REVIEWED", "SPECIFIED", "SUPERSEDED"],
  "distribution": {
    "SPECIFIED": 0,
    "GENERATED": 5,
    "REVIEWED": 3,
    "APPROVED": 10,
    "REJECTED": 1,
    "FAILED": 0,
    "SUPERSEDED": 2
  }
}
```

---

### `GET /api/v1/ecs/artifacts`

List artifacts tracked by the ECS store, optionally filtered by state.

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `state` | string | null | ECS state filter (must be a valid state name) |

**With state filter**:
```json
{
  "state": "REVIEWED",
  "artifact_ids": ["art-001", "art-002"],
  "count": 2
}
```

**Without state filter** (summary):
```json
{
  "summary": {
    "GENERATED": {"count": 5, "sample_ids": ["art-001", "art-002"]},
    "REVIEWED": {"count": 3, "sample_ids": ["art-010"]}
  },
  "total_artifacts": 21
}
```

**Error**: `400` (unknown state), `503` (ECS store not available).

---

### `GET /api/v1/ecs/artifacts/{artifact_id}`

Get the ECS state and transition history for a specific artifact.

**Response**:
```json
{
  "artifact_id": "art-001",
  "current_state": "REVIEWED",
  "history": [
    {
      "from_state": "SPECIFIED",
      "to_state": "GENERATED",
      "actor": "pipeline",
      "reason": "synthesis_complete",
      "timestamp": "2025-01-15T10:00:00Z"
    }
  ]
}
```

---

## Sources

Browse indexed source inventory and aggregate statistics.

### `GET /api/v1/sources`

List indexed sources with metadata. Gathers content from EntityStore and KnowledgeStore.

**Query Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `domain` | string | null | Filter by domain |
| `source_type` | string | null | Filter by source type (`"conversation"`, `"artifact"`, `"compiled_knowledge"`) |

**Response**:
```json
{
  "sources": [
    {
      "source_id": "src-001",
      "source_type": "conversation",
      "domain": "physics",
      "title": "Quantum Chat",
      "metadata": {}
    }
  ],
  "total": 1,
  "vector_stats": {"total_vectors": 150, "domain": null},
  "lexical_stats": {"available": true, "domain": null}
}
```

---

### `GET /api/v1/sources/stats`

Get aggregate statistics about indexed content across all stores.

**Response**:
```json
{
  "vector_store": {"available": true, "total_vectors": 150, "health": "ok"},
  "entity_store": {"available": true, "total_entities": 42},
  "knowledge_store": {"available": true, "total_items": 12},
  "lexical_store": {"available": true}
}
```

---

## Collaborators

Collaborator management with DEFINER-gated create/update/revoke. Collaborators can create drafts and submit reviews but **cannot approve** (per spec §1.7).

> **Security**: Password/secret material is NEVER accepted via query parameters. All secrets must be transmitted in the request body to prevent leakage into logs, browser history, and proxies.

### `GET /api/v1/collaborators`

List all collaborators.

**Auth**: Optional

**Response**: `{"collaborators": [...]}`

---

### `POST /api/v1/collaborators`

Create a new collaborator. Password must be in the request body, not query params. The password is never reflected in the response.

**Auth**: Definer

**Request Body**:
```json
{
  "identity": "user@example.com",
  "role": "collaborator",
  "password": "s3cure-p4ss"
}
```

**Response**: Collaborator object (password field excluded).

**Error**: `400` (creation failed), `503` (CollaboratorManager not available).

---

### `PUT /api/v1/collaborators/{identity}`

Update a collaborator's role. Role must be in the request body.

**Auth**: Definer

**Request Body**:
```json
{
  "new_role": "reviewer",
  "requested_by": "definer"
}
```

**Response**: Updated collaborator object.

**Error**: `400` (update failed), `503` (CollaboratorManager not available).

---

### `DELETE /api/v1/collaborators/{identity}`

Revoke a collaborator's access. Request body is optional for delete.

**Auth**: Definer

**Request Body** (optional):
```json
{
  "requested_by": "definer"
}
```

**Response**: `{"status": "revoked"}`

**Error**: `400` (revoke failed), `503` (CollaboratorManager not available).

---

## Plugins

Plugin management with DEFINER-gated enable/disable operations.

### `GET /api/v1/plugins`

List loaded plugins and their status.

**Response**: `{"plugins": [...]}`

---

### `POST /api/v1/plugins/enable`

Enable and register a plugin. Loads from a config path and registers with the PluginManager.

**Auth**: Definer

**Query Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `slot_name` | string | Yes | The model slot to bind this plugin to |
| `config_path` | string | Yes | Path to the plugin configuration file |

**Response**: `{"status": "enabled", "slot": "synthesis"}`

**Error**: `400` (failed to load), `503` (plugin infrastructure not available).

---

### `POST /api/v1/plugins/disable`

Disable and unregister a plugin.

**Auth**: Definer

**Query Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `slot_name` | string | Yes | The model slot |
| `provider_name` | string | Yes | The provider name to unregister |

**Response**: `{"status": "disabled"}`

**Error**: `503` (PluginManager not available).

---

### `GET /api/v1/plugins/health`

Check health of all loaded plugins' model providers.

**Response**: `{"health": {...}}`

---

## Performance

Performance profiling endpoints. All endpoints require DEFINER authorization. Return structured error responses when the profiler is not configured or disabled.

### `GET /api/v1/performance/metrics`

Get system performance metrics.

**Auth**: Definer

**Response** (profiler available):
```json
{
  "ok": true,
  "data": {
    "cpu_percent": 45.2,
    "memory_mb": 512,
    "uptime_seconds": 3600
  }
}
```

**Response** (profiler not configured):
```json
{
  "ok": false,
  "error": {
    "code": "BACKEND_UNAVAILABLE",
    "message": "PerformanceProfiler is not configured or not initialized...",
    "details": {}
  }
}
```

**Response** (profiling disabled):
```json
{
  "ok": false,
  "error": {
    "code": "DISABLED",
    "message": "Performance profiling is disabled in the current configuration...",
    "details": {}
  }
}
```

---

### `GET /api/v1/performance/slow`

Get slow operation metrics.

**Auth**: Definer

**Response**: `{"ok": true, "data": {"slow": [...]}}` (or same error structure as above).

---

### `GET /api/v1/performance/memory`

Get memory usage metrics.

**Auth**: Definer

**Response**: `{"ok": true, "data": {...}}` (or same error structure as above).

---

## Graph

Knowledge graph visualization and exploration endpoints.

### `GET /api/v1/graph/data`

Get all graph nodes and edges for Cytoscape.js visualization.

**Response**:
```json
{
  "nodes": [
    {"id": "aip", "label": "AIP", "type": "domain"}
  ],
  "edges": [
    {"source": "aip", "target": "theology_research", "type": "bridge"}
  ]
}
```

---

### `GET /api/v1/graph/neighbors/{node_id}`

Get neighbors of a specific graph node.

**Response**:
```json
{
  "node_id": "aip",
  "neighbors": [
    {"id": "theology_research", "type": "domain", "edge_type": "bridge"}
  ]
}
```

---

### `GET /api/v1/graph/stats`

Get graph statistics (node count, edge count).

**Response**:
```json
{
  "node_count": 36,
  "edge_count": 17
}
```

---

## Corpus

Corpus management and statistics endpoints.

### `GET /api/v1/corpus/stats`

Get corpus statistics including turn counts, tagging progress, and embedding coverage.

**Response**:
```json
{
  "total_turns": 2766,
  "tagged_turns": 2766,
  "embedded_turns": 50,
  "embedding_coverage": 0.018,
  "domain_distribution": {
    "aip": 450,
    "theology_research": 380
  }
}
```

---

### `GET /api/v1/corpus/embedding-progress`

Get embedding pass progress for monitoring background embedding operations.

**Response**:
```json
{
  "total_turns": 2766,
  "embedded_turns": 50,
  "remaining": 2716,
  "progress_percent": 1.8,
  "last_batch_size": 50,
  "estimated_completion_hours": 17
}
```

---

## Vigil Quality

Vigil quality monitoring and history endpoints (Sprint 6.4+).

### `GET /api/v1/vigil/quality`

Get latest Vigil quality metrics including citation rate, grounding rate, and retrieval quality.

**Response**:
```json
{
  "citation_rate": 0.85,
  "grounding_rate": 0.78,
  "last_check": "2026-06-10T10:00:00Z",
  "retrieval_quality": {
    "precision_at_5": 0.42,
    "last_sample": "2026-06-10T04:00:00Z",
    "sample_size": 5
  }
}
```

---

### `GET /api/v1/vigil/quality/alerts`

Get retrieval quality degradation alerts.

**Response**:
```json
{
  "alerts": [
    {
      "alert_type": "retrieval_quality_degradation",
      "metric": "precision_at_5",
      "value": 0.25,
      "threshold": 0.3,
      "timestamp": "2026-06-10T04:00:00Z"
    }
  ]
}
```

---

## Retrieval Dashboard

Retrieval evaluation and benchmarking endpoints.

### `GET /api/v1/retrieval/dashboard`

Get retrieval dashboard data including current channel weights, evaluation history, and coverage stats.

**Response**:
```json
{
  "channel_weights": {"vector": 0.6, "fts": 0.4, "corpus": 0.4},
  "vector_coverage": 0.018,
  "coverage_gate_active": true,
  "last_eval": null,
  "baseline_available": false
}
```

---

### `POST /api/v1/admin/embeddings/backfill`

Trigger an embedding backfill for documents that lack vector entries.

**Auth**: Definer

**Request Body**:
```json
{
  "domain": null,
  "batch_size": 50,
  "limit": null,
  "dry_run": false
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `domain` | string | null | Filter to specific domain |
| `batch_size` | int | 50 | Embedding batch size |
| `limit` | int | null | Max documents to process (null = all) |
| `dry_run` | bool | false | Preview without generating embeddings |

**Response**:
```json
{
  "processed": 150,
  "embedded": 148,
  "errors": 2,
  "dry_run": false
}
```

---

## Rate Limiting

All endpoints are rate-limited when `[rate_limit] enabled = true` (default). Response headers include:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Remaining` | Remaining requests in the current window |
| `X-Request-ID` | Correlation ID (echoed from request or auto-generated) |

When rate limit is exceeded, returns `429 Too Many Requests`.

Read-only (GET) requests are not rate-limited when `model_budget_protection = true`.

---

## Error Responses

### Standard HTTP Error Codes

| Code | Meaning | When |
|------|---------|------|
| `400` | Bad Request | Invalid input, missing required fields |
| `403` | Forbidden | AutonomyGate blocked the action |
| `404` | Not Found | Resource does not exist |
| `429` | Too Many Requests | Rate limit exceeded |
| `500` | Internal Server Error | Unhandled exception (structured JSON with `detail`, `error_type`, `path`) |
| `503` | Service Unavailable | Required backend store not wired or not initialized |

### Unhandled Exception Format

```json
{
  "detail": "Internal server error",
  "error_type": "ValueError",
  "path": "/api/v1/sessions"
}
```

### AutonomyGate Rejection Format

```json
{
  "detail": "Autonomy gate blocked: insufficient autonomy level for action 'approve_artifact'"
}
```

---

## Endpoint Summary

| Method | Path | Auth | Gate | Description |
|--------|------|------|------|-------------|
| `GET` | `/api/v1/health` | None | No | System health check |
| `GET` | `/api/v1/projects` | Optional | No | List projects |
| `POST` | `/api/v1/projects` | Required | Yes | Create project |
| `GET` | `/api/v1/projects/{id}` | Optional | No | Get project |
| `GET` | `/api/v1/projects/{id}/work_units` | Optional | No | List work units |
| `POST` | `/api/v1/sessions` | Optional | No | Create session |
| `GET` | `/api/v1/sessions` | Optional | No | List sessions |
| `GET` | `/api/v1/sessions/{id}` | Optional | No | Get session |
| `GET` | `/api/v1/sessions/{id}/context` | Optional | No | Session context |
| `PATCH` | `/api/v1/sessions/{id}` | Optional | No | Update session |
| `DELETE` | `/api/v1/sessions/{id}` | Optional | No | Delete session |
| `WS` | `/api/v1/chat/{session_id}` | — | No | WebSocket chat |
| `GET` | `/api/v1/artifacts` | Optional | No | List artifacts |
| `GET` | `/api/v1/artifacts/{id}` | Optional | No | Get artifact |
| `GET` | `/api/v1/artifacts/{id}/versions` | Optional | No | Artifact versions |
| `GET` | `/api/v1/artifacts/{id}/evaluation` | Optional | No | Evaluation results |
| `GET` | `/api/v1/reviews` | Required | No | List review queue |
| `POST` | `/api/v1/reviews/{id}/approve` | Definer | Yes | Approve artifact |
| `POST` | `/api/v1/reviews/{id}/reject` | Definer | Yes | Reject artifact |
| `GET` | `/api/v1/admin/config` | Optional | No | Get configuration |
| `PATCH` | `/api/v1/admin/config` | Definer | Yes | Hot-reload config |
| `GET` | `/api/v1/admin/sexton/classifications` | Optional | No | Sexton classifications |
| `GET` | `/api/v1/admin/sexton/audit` | Optional | No | Sexton stale rule audit |
| `GET` | `/api/v1/admin/sexton/playbook` | Optional | No | ACE playbook entries |
| `GET` | `/api/v1/admin/beast/status` | Optional | No | Beast health status |
| `GET` | `/api/v1/admin/router/weights` | Optional | No | Router weights |
| `GET` | `/api/v1/admin/budget` | Optional | No | Budget status |
| `GET` | `/api/v1/admin/autonomy/log` | Optional | No | Autonomy audit log |
| `GET` | `/api/v1/memory/search` | Optional | No | Hybrid memory search |
| `GET` | `/api/v1/memory/trace/{session_id}` | Optional | No | Session trace |
| `GET` | `/api/v1/memory/events/{project_id}` | Optional | No | Project event timeline |
| `GET` | `/api/v1/memory/entities` | Optional | No | List entities |
| `GET` | `/api/v1/memory/canonical` | Optional | No | List canonicals |
| `GET` | `/api/v1/models/slots` | Optional | No | List model slots |
| `GET` | `/api/v1/models/slots/{name}` | Optional | No | Get model slot |
| `GET` | `/api/v1/actors/status` | Optional | No | All actors status |
| `GET` | `/api/v1/actors/{name}` | Optional | No | Single actor detail |
| `POST` | `/api/v1/actors/{name}/trigger` | Optional | No | Trigger actor cycle |
| `POST` | `/api/v1/ingest/conversation` | Optional | No | Ingest conversation |
| `POST` | `/api/v1/ingest/file` | Optional | No | Ingest file |
| `POST` | `/api/v1/ask` | Optional | No | Source-grounded ask |
| `POST` | `/api/v1/ask/retrieve` | Optional | No | Retrieve sources only |
| `GET` | `/api/v1/knowledge` | Optional | No | List knowledge items |
| `GET` | `/api/v1/knowledge/search` | Optional | No | Search knowledge |
| `GET` | `/api/v1/knowledge/{id}` | Optional | No | Get knowledge item |
| `GET` | `/api/v1/ecs/graph` | Optional | No | ECS state graph |
| `GET` | `/api/v1/ecs/artifacts` | Optional | No | ECS artifact list |
| `GET` | `/api/v1/ecs/artifacts/{id}` | Optional | No | ECS artifact history |
| `GET` | `/api/v1/sources` | Optional | No | List sources |
| `GET` | `/api/v1/sources/stats` | Optional | No | Source statistics |
| `GET` | `/api/v1/collaborators` | Optional | No | List collaborators |
| `POST` | `/api/v1/collaborators` | Definer | No | Create collaborator |
| `PUT` | `/api/v1/collaborators/{identity}` | Definer | No | Update collaborator role |
| `DELETE` | `/api/v1/collaborators/{identity}` | Definer | No | Revoke collaborator |
| `GET` | `/api/v1/plugins` | Optional | No | List plugins |
| `POST` | `/api/v1/plugins/enable` | Definer | No | Enable plugin |
| `POST` | `/api/v1/plugins/disable` | Definer | No | Disable plugin |
| `GET` | `/api/v1/plugins/health` | Optional | No | Plugin health check |
| `GET` | `/api/v1/performance/metrics` | Definer | No | System metrics |
| `GET` | `/api/v1/performance/slow` | Definer | No | Slow operations |
| `GET` | `/api/v1/performance/memory` | Definer | No | Memory usage |
