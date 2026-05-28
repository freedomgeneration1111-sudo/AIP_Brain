# AIP 0.1 Production Packaging (Phase 7 9.6)

## Profiles

- **laptop** (default): sqlite_vss + Ollama + local models. Zero external dependencies.
- **production**: pgvector + API models + authentication (9.0b).

## Quick Start (Laptop)

```bash
cd deploy
AIP_PROFILE=laptop docker compose --profile laptop up --build
```

## Quick Start (Production)

```bash
cd deploy
AIP_PROFILE=production docker compose --profile production up --build
```

## Scripts

- `backup.sh` — creates timestamped tarball of db/ + config/
- `restore.sh <backup.tar.gz>` — restores from backup
- `health-check.sh` — simple health orchestration helper

## Health Checks

The Docker healthcheck calls `/api/v1/health` (Phase 6 endpoint, still present).

## Notes

- All Phase 6 surfaces (CLI, API, Chat, Review, MCP, Admin/Memory) + Phase 7 additions (Vigil 9.1, Canonical Pipeline 9.2, Workflows 9.3, Web UI 9.4) are included.
- Auth (9.0b) and Rate Limiting (9.0c) are enforced in both profiles.
- The 9.5 acceptance test + 9.7 final gates verify the packaged system.
