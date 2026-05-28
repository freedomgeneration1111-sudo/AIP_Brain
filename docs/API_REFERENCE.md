# API Reference

AIP 0.1 exposes a REST API via FastAPI. All endpoints are under `/api/v1/`.

**Base URL**: `http://localhost:8000/api/v1`

## Authentication

When `[auth] auth_enabled = true`:
- Include `Authorization: Bearer <session_token>` header
- Or include `X-API-Key: <api_key>` header for non-interactive access

When `[auth] auth_enabled = false` (default for laptop profile):
- No authentication required

---

## Health

### `GET /api/v1/health`

Public health check. No authentication required.

**Response**:
```json
{
  "status": "ok",
  "vector_backend": "sqlite_vss",
  "model_slots": ["synthesis", "evaluation", "sexton", "embedding"],
  "uptime_seconds": 3600
}
```

---

## Projects

### `GET /api/v1/projects`

List all projects.

**Auth**: Optional | **Autonomy**: read

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | string | null | Filter by project status |

**Response**: Array of project objects.

### `POST /api/v1/projects`

Create a new project.

**Auth**: Required | **Autonomy**: write | **Gate**: No

**Request Body**:
```json
{
  "name": "My Project",
  "description": "Project description",
  "domain": "science"
}
```

### `GET /api/v1/projects/{project_id}`

Get project details.

**Auth**: Optional | **Autonomy**: read

---

## Sessions

### `GET /api/v1/sessions`

List sessions for a project.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_id` | string | required | Filter by project |

### `POST /api/v1/sessions`

Create a new session within a project.

**Request Body**:
```json
{
  "project_id": "proj-001",
  "domain": "science"
}
```

### `GET /api/v1/sessions/{session_id}`

Get session details including turn count, context tokens, and artifacts produced.

---

## Chat

### `POST /api/v1/chat/{session_id}`

Send a message in a chat session. The system assembles context from explicit stores (not long chat history per §1.3).

**Auth**: Required | **Autonomy**: write

**Request Body**:
```json
{
  "message": "Synthesize a summary of quantum entanglement",
  "artifacts_referenced": []
}
```

**Response**:
```json
{
  "message_id": "msg-001",
  "role": "assistant",
  "content": "Quantum entanglement is...",
  "artifacts_produced": ["art-001"],
  "tokens_used": 450
}
```

---

## Artifacts

### `GET /api/v1/artifacts`

List artifacts with pagination.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_id` | string | null | Filter by project |
| `ecs_state` | string | null | Filter by ECS state |
| `domain` | string | null | Filter by domain |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Items per page |

### `GET /api/v1/artifacts/{artifact_id}`

Get artifact content and metadata.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `version` | int | null | Specific version (null = latest) |

### `GET /api/v1/artifacts/{artifact_id}/versions`

List all versions of an artifact.

---

## Review

### `GET /api/v1/review/queue`

List artifacts awaiting review (ECS state = REVIEWED).

**Auth**: Required | **Autonomy**: read

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Items per page |

### `POST /api/v1/review/{artifact_id}/promote`

Promote a REVIEWED artifact to APPROVED (canonical). Requires DEFINER approval.

**Auth**: Required | **Autonomy**: admin | **Gate**: Yes

**Request Body**:
```json
{
  "approved_by": "definer"
}
```

### `POST /api/v1/review/{artifact_id}/reject`

Reject a REVIEWED artifact (remains in REVIEWED state).

**Auth**: Required | **Autonomy**: write

**Request Body**:
```json
{
  "reason": "Faithfulness below threshold"
}
```

---

## Admin

### `GET /api/v1/admin/status`

System status including budget, vigil health, and model slot configuration.

**Auth**: Required | **Autonomy**: admin | **Gate**: Yes

### `GET /api/v1/admin/audit`

Autonomy escalation audit log.

**Auth**: Required | **Autonomy**: admin | **Gate**: Yes

### `GET /api/v1/admin/budget`

Budget status across all scopes.

**Auth**: Required | **Autonomy**: admin

---

## Memory

### `GET /api/v1/memory/search`

Search canonical and compiled knowledge.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | required | Search query |
| `domain` | string | null | Filter by domain |
| `limit` | int | 10 | Max results |

---

## Collaborators

### `GET /api/v1/collaborators`

List collaborators (requires admin).

### `POST /api/v1/collaborators`

Add a collaborator. Collaborators can create drafts and submit reviews but **cannot approve** (per §1.7).

### `DELETE /api/v1/collaborators/{identity}`

Revoke collaborator access.

---

## Plugins

### `GET /api/v1/plugins`

List loaded plugins and their status.

### `POST /api/v1/plugins/{plugin_name}/health`

Check health of a specific plugin's model provider.

---

## Performance

### `GET /api/v1/performance/metrics`

Get performance metrics (when profiling is enabled).

### `GET /api/v1/performance/health`

Detailed backend health including vector store, lexical store, and database connections.

---

## Rate Limiting

All endpoints are rate-limited when `[rate_limit] enabled = true` (default). Response headers include:
- `X-RateLimit-Remaining`: Remaining requests in the current window

When rate limit is exceeded, returns `429 Too Many Requests`.

Read-only (GET) requests are not rate-limited when `model_budget_protection = true`.
