# AIP 0.1 Production Packaging (Phase 7 9.6)

## Profiles

- **laptop** (default): sqlite_vss + Ollama + local models. Zero external dependencies.
- **production**: pgvector + API models + authentication (9.0b).

## Quick Start (Laptop)

```bash
cd deploy
AIP_PROFILE=laptop docker compose --profile laptop up --build
```

Laptop mode allows auth-disabled operation on localhost only. The API binds
to 127.0.0.1 by default, which is safe for local development without auth.

## Quick Start (Production)

```bash
cd deploy

# REQUIRED: Set a strong database password before starting
export POSTGRES_PASSWORD=$(openssl rand -hex 32)

AIP_PROFILE=production docker compose --profile production up --build
```

Production mode requires:
- `auth_enabled=true` in the production config file
- `POSTGRES_PASSWORD` environment variable set (no default fallback)
- Real model/embedding providers (not `fake`, `mock`, or `ci`)
- No CI fixture evaluation modes

## Config Validation

The application validates configuration at startup. If unsafe settings are
detected, the application **will not start** and will print a clear error
with the offending setting and a remediation hint.

You can also validate config without starting the server:

```bash
# Validate the default config file
aip validate

# Validate a specific config file
aip validate --config-path deploy/configs/aip.config.production.toml
```

### Blocked Unsafe Configurations

| Configuration | Blocked? | Error Code |
|---|---|---|
| Production + auth disabled | **Blocked** | `PROD_AUTH_DISABLED` |
| Production + missing POSTGRES_PASSWORD | **Blocked** | `PROD_MISSING_DB_PASSWORD` |
| Production + weak/default password | **Blocked** | `PROD_WEAK_DB_PASSWORD` |
| Production + fake/mock embedding provider | **Blocked** | `PROD_FIXTURE_PROVIDER` |
| Production + fake/mock model provider | **Blocked** | `PROD_FIXTURE_MODEL_PROVIDER` |
| Public bind (0.0.0.0) + auth disabled | **Blocked** | `PUBLIC_NO_AUTH` |
| Public bind + weak database password | **Blocked** | `PUBLIC_WEAK_SECRET` |
| Laptop + localhost + auth disabled | **Allowed** | — |
| Production + auth enabled + strong secrets | **Allowed** | — |

### Unsafe Override (Local Dev Only)

To intentionally run a public API without auth for local development testing:

```bash
export AIP_UNSAFE_ALLOW_PUBLIC_NO_AUTH=true
```

This override **does not** bypass production auth requirements. It only
allows public-bind + auth-disabled in laptop/dev mode. The override name is
intentionally long and ugly to discourage use.

## Required Environment Variables

### Production
| Variable | Required | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | **Yes** | Strong, unique database password. No default fallback. |
| `AIP_PROFILE` | No (defaults to `laptop`) | Set to `production` for production deployments. |
| `DATABASE_URL` | No (auto-constructed) | Full PostgreSQL connection string. |

### Laptop
No required environment variables. Uses SQLite by default.

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
- Config validation is enforced programmatically, not just documented.
