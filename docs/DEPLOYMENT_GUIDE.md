# Deployment Guide

AIP 0.1 supports two deployment profiles: **laptop** (local-first, no external services) and **production** (PostgreSQL + pgvector, authentication enabled).

---

## Laptop Profile

The laptop profile is designed for single-developer use on local hardware (4–6 GB RAM). It uses SQLite for all storage and Ollama for local model inference.

### Requirements

- Python 3.11+ (3.12 recommended)
- 4+ GB RAM
- 10 GB disk space (for models and data)
- Ollama installed and running

### Quick Start with Docker

```bash
# Start all services
docker compose -f deploy/docker-compose.laptop.yml up --build

# Check health
curl http://localhost:8000/api/v1/health
```

### Quick Start without Docker

```bash
# Install dependencies
uv sync

# Start Ollama
ollama serve &
ollama pull llama3.2

# Start AIP
uv run uvicorn aip.adapter.api.app:create_app \
  --host 0.0.0.0 --port 8000 --factory
```

### Configuration

Copy and customize the laptop config:

```bash
cp deploy/configs/aip.config.laptop.toml config/aip.config.toml
```

Key laptop settings:
```toml
[deployment]
profile_name = "laptop"
vector_backend = "sqlite_vss"
model_provider = "ollama"
auth_enabled = false
workers = 1
memory_limit_mb = 4096

[embedding]
provider = "openai_compatible"  # Set to "ollama" for local, "fake" for CI

[performance]
sqlite_wal_mode = true
sqlite_busy_timeout_ms = 5000
```

### Data Storage

All data is stored in `./db/`:
- `state.db` — Main application state (ECS, events, budget, auth)
- `ecs.db` — ECS transition audit
- `lexical.db` — FTS5 full-text search index
- `ace_playbook.db` — ACE playbook entries
- `vigil.db` — Vigil health checks

### Backups

```bash
# Create backup
deploy/backup.sh

# Restore from backup
deploy/restore.sh /path/to/backup
```

---

## Production Profile

The production profile uses PostgreSQL with pgvector for vector storage, supports API-based model providers, and enables authentication.

### Requirements

- Docker and Docker Compose
- PostgreSQL 16+ with pgvector extension
- 8+ GB RAM
- API keys for model providers (OpenAI, Anthropic, etc.)

### Deploy with Docker Compose

```bash
# Set required environment variables
export POSTGRES_PASSWORD=$(openssl rand -hex 32)
export AIP_PROFILE=production

# Start all services
docker compose -f deploy/docker-compose.production.yml up --build -d

# Verify
curl http://localhost:8000/api/v1/health
```

### Configuration

```bash
cp deploy/configs/aip.config.production.toml config/aip.config.toml
```

Key production settings:
```toml
[deployment]
profile_name = "production"
vector_backend = "pgvector"
model_provider = "api"
auth_enabled = true
workers = 2
memory_limit_mb = 8192

[auth]
auth_enabled = true
api_key_enabled = true

[performance]
sqlite_wal_mode = true
sqlite_busy_timeout_ms = 5000
max_memory_mb = 8192
```

### Database Setup

PostgreSQL with pgvector is included in the production Docker Compose. The database is initialized automatically on first start.

Connection string format:
```
postgresql://aip:${POSTGRES_PASSWORD}@postgres:5432/aip
```

### Authentication Setup

1. **Set the DEFINER password** on first startup:
   ```bash
   # The definer identity is configured in auth.definer_identity
   # Default: "definer"
   ```

2. **Create API keys** for non-interactive access (CLI, MCP):
   ```bash
   curl -X POST http://localhost:8000/api/v1/admin/keys \
     -H "Authorization: Bearer <definer-token>" \
     -d '{"key_name": "cli-access", "role": "readonly"}'
   ```

3. **Add collaborators** (if enabled):
   ```bash
   curl -X POST http://localhost:8000/api/v1/collaborators \
     -H "Authorization: Bearer <definer-token>" \
     -d '{"identity": "alice", "role": "collaborator"}'
   ```

---

## Health Monitoring

### HTTP Health Check

```bash
curl http://localhost:8000/api/v1/health
```

### Script-Based Health Check

```bash
deploy/health-check.sh
```

This script is used by the Docker `HEALTHCHECK` directive and orchestrators.

### Vigil Health

The Vigil actor periodically checks canonical corpus health:
- Staleness detection (artifacts not re-evaluated within `stale_threshold_days`)
- Entity consistency checks
- Model slot change triggers re-evaluation

Access via API:
```bash
curl http://localhost:8000/api/v1/admin/status
```

---

## Scaling Considerations

### Horizontal Scaling

AIP is designed for single-instance deployment. For team use:
- Run one AIP instance per project
- Use PostgreSQL for shared state
- Put behind a reverse proxy (nginx, caddy) for TLS

### Resource Limits

| Resource | Laptop | Production |
|----------|--------|------------|
| RAM | 4 GB | 8 GB |
| Workers | 1 | 2-4 |
| Vector Backend | sqlite_vss | pgvector |
| Max Concurrent | 5 sessions | 20 sessions |

---

## Troubleshooting

### Container won't start
```bash
docker compose logs aip
```

### Database connection errors
- Verify PostgreSQL is running: `docker compose exec postgres pg_isready`
- Check connection string in config

### Ollama not responding
- Ensure Ollama container is running: `docker compose ps ollama`
- Check model is pulled: `docker compose exec ollama ollama list`

### Port conflicts
```bash
AIP_PORT=8080 docker compose up
```
