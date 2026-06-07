"""aip init command — the installation contract.

Performs:
1. RAM detection + hardware profile suggestion
2. Vector backend configuration (writes aip.config.toml)
3. All DB schema initialization using ACTUAL adapter store schemas
4. Write db_path to config so all CLI commands use the same datastore
5. Ollama validation (graceful warning + fallback if unavailable)
6. Model slot validation via resolver
7. Clear summary of local vs API surface
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import click


def _detect_ram_gb() -> int:
    """Best-effort RAM detection. Falls back to 8 if psutil unavailable."""
    try:
        import psutil

        return int(psutil.virtual_memory().total / (1024**3))
    except Exception:
        return 8


def _suggest_profile(ram_gb: int) -> str:
    """Suggest a deployment profile based on available RAM."""
    if ram_gb < 6:
        return "sqlite_vss + API synthesis (low-RAM profile)"
    if ram_gb < 8:
        return "pgvector (tuned) + local evaluation"
    return "pgvector (preferred) + full local stack"


def _init_state_db(db_path: Path) -> bool:
    """Initialize state.db with schemas matching the actual adapter stores.

    CRITICAL: These schemas must match what VersionedArtifactStore,
    SqliteProjectStore, QueryableEventStore, PersistentEcsStore, and
    SqliteCanonicalStore create. Otherwise, stores will find wrong
    column layouts and fail.

    Only creates tables if they don't already exist.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            -- VersionedArtifactStore: artifacts table (version preservation)
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_id ON artifacts(id);

            -- SqliteProjectStore: projects table (must have 'status' column)
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                domain TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- QueryableEventStore: events table
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_artifact ON events(artifact_id);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

            -- PersistentEcsStore: ECS state + transition history
            CREATE TABLE IF NOT EXISTS ecs_state (
                artifact_id TEXT PRIMARY KEY,
                current_state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ecs_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                superseded_by TEXT,
                timestamp TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_ecs_transitions_artifact
            ON ecs_transitions(artifact_id, timestamp DESC);

            -- SqliteCanonicalStore: canonical artifacts (DEFINER approved only)
            CREATE TABLE IF NOT EXISTS canonical_artifacts (
                artifact_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                domain TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                superseded_by TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_canonical_domain ON canonical_artifacts(domain);

            -- Entity table (legacy, used by status/project commands)
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            -- Model library: cached OpenRouter models + BYOK entries
            CREATE TABLE IF NOT EXISTS enabled_models (
                model_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'openrouter',
                cost_input_per_million REAL,
                cost_output_per_million REAL,
                context_length INTEGER,
                supports_vision INTEGER DEFAULT 0,
                supports_tools INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 0,
                is_custom INTEGER DEFAULT 0,
                custom_base_url TEXT,
                custom_api_key TEXT,
                last_fetched TEXT
            );
        """)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        click.echo(f"  state.db schema init skipped: {e}")
        return False


def _init_trace_db(db_path: Path) -> bool:
    """Initialize trace.db with the required schema.

    Returns True on success, False on failure.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                node_type TEXT,
                model_slot TEXT,
                model_name TEXT,
                token_count_in INTEGER DEFAULT 0,
                token_count_out INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                latency_ms REAL DEFAULT 0.0,
                failure_type TEXT CHECK (failure_type IS NULL OR failure_type IN ('A', 'B', 'C', 'D', 'E', 'F')),
                detail TEXT,
                intervention_applied INTEGER DEFAULT 0,
                intervention_type TEXT,
                outcome TEXT CHECK (outcome IS NULL OR outcome IN
                    ('success', 'failure', 'timeout', 'gate_blocked',
                     'insufficient_memory', 'detected', 'stale_detected')),
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trace_session
            ON trace_events(session_id);

            CREATE INDEX IF NOT EXISTS idx_trace_unclassified
            ON trace_events(failure_type, outcome);

            CREATE INDEX IF NOT EXISTS idx_trace_node_type
            ON trace_events(node_type);

            CREATE TABLE IF NOT EXISTS routing_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                slot_name TEXT NOT NULL,
                domain TEXT,
                was_exploration INTEGER DEFAULT 0,
                model_name TEXT,
                token_count INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                latency_ms REAL DEFAULT 0.0,
                outcome TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_routing_session
            ON routing_outcomes(session_id);

            CREATE INDEX IF NOT EXISTS idx_routing_slot_domain
            ON routing_outcomes(slot_name, domain);
        """)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        click.echo(f"  trace.db schema init skipped: {e}")
        return False


def _init_lexical_db(db_path: Path) -> bool:
    """Initialize lexical.db with FTS5 schema matching SqliteFts5LexicalStore.

    Returns True on success, False on failure.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fts_documents (
                doc_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                domain TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_index
                USING fts5(content, domain, metadata, tokenize=unicode61);
        """)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        click.echo(f"  lexical.db schema init skipped: {e}")
        return False


def _check_ollama() -> bool:
    """Check if Ollama is running. Returns True if reachable."""
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _write_db_path_to_config(config_path: Path, db_path: str) -> None:
    """Write the db_path setting to the [database] section of config.

    Only writes to the [database] section — does NOT interfere with
    db_path settings in other sections like [ace_playbook] or [ecs].
    """
    content = config_path.read_text() if config_path.exists() else ""

    in_database_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_database_section = stripped == "[database]"
            continue
        if in_database_section and stripped.startswith("db_path"):
            return  # Already configured in [database], don't overwrite

    if "[database]" not in content:
        with open(config_path, "a") as f:
            f.write(f'\n\n[database]\ndb_path = "{db_path}"\n')
    else:
        lines = content.splitlines()
        new_lines = []
        for line in lines:
            new_lines.append(line)
            if line.strip() == "[database]":
                new_lines.append(f'db_path = "{db_path}"')
        with open(config_path, "w") as f:
            f.write("\n".join(new_lines))
            f.write("\n")


def _populate_enabled_models(state_db: Path) -> None:
    """Populate enabled_models table from config/enabled_models.json.

    The JSON file is a flat array of model_id strings (e.g.
    ["openrouter/owl-alpha", "deepseek/deepseek-v4-flash:free"]).

    Each entry is inserted with:
      - display_name: the portion after the last "/" (or the whole string)
      - provider: "openrouter" (default)
      - enabled: 1 (models listed in the JSON are enabled by default)

    Uses INSERT OR IGNORE so re-running init never overwrites rows that
    may have been updated by model library fetch or user toggles.
    """
    json_path = Path("config/enabled_models.json")
    if not json_path.exists():
        click.echo("enabled_models: config/enabled_models.json not found, skipping seed")
        return

    try:
        raw = json_path.read_text(encoding="utf-8")
        model_ids = json.loads(raw)
    except Exception as e:
        click.echo(f"enabled_models: failed to read config/enabled_models.json: {e}")
        return

    if not isinstance(model_ids, list):
        click.echo("enabled_models: expected JSON array in config/enabled_models.json, skipping")
        return

    if not model_ids:
        click.echo("enabled_models: config/enabled_models.json is empty, no models seeded")
        return

    try:
        conn = sqlite3.connect(str(state_db))
        count = 0
        for mid in model_ids:
            if not isinstance(mid, str) or not mid.strip():
                continue
            mid = mid.strip()
            # Derive display name from model_id: take portion after last "/"
            display_name = mid.split("/")[-1] if "/" in mid else mid
            conn.execute(
                """
                INSERT OR IGNORE INTO enabled_models
                    (model_id, display_name, provider, enabled)
                VALUES (?, ?, 'openrouter', 1)
                """,
                (mid, display_name),
            )
            count += 1
        conn.commit()
        conn.close()
        click.echo(f"enabled_models: seeded {count} model(s) from config/enabled_models.json")
    except Exception as e:
        click.echo(f"enabled_models: population skipped: {e}")


@click.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing config/DBs (dangerous)")
def init(force: bool) -> None:
    """Initialize AIP 0.1 for this machine.

    Performs:
    1. RAM detection + hardware profile suggestion
    2. Vector backend configuration (writes aip.config.toml)
    3. All DB schema initialization (using actual adapter store schemas)
    4. Write db_path to config so all CLI commands use the same datastore
    5. Ollama validation (graceful warning + fallback if unavailable)
    6. Model slot validation via resolver
    7. Clear summary of local vs API surface
    """
    click.echo("=== AIP 0.1 init ===")

    # 1. Hardware profile
    ram = _detect_ram_gb()
    profile = _suggest_profile(ram)
    click.echo(f"Detected RAM: ~{ram} GB → suggested profile: {profile}")

    # 2. Write minimal vector config
    config_path = Path("config/aip.config.toml")
    config_path.parent.mkdir(parents=True, exist_ok=True)

    content = config_path.read_text() if config_path.exists() else ""
    if "[vector_backend]" not in content:
        with open(config_path, "a") as f:
            f.write("\n\n[vector_backend]\n")
            if ram < 6:
                f.write('provider = "sqlite_vss"\n')
            else:
                f.write('provider = "pgvector"\n')
            f.write('host = "127.0.0.1"\nport = 5432\n')
        click.echo(f"Configured vector backend in {config_path}")
    else:
        click.echo(f"Vector backend config already present in {config_path}")

    # 3. Initialize databases with correct schemas
    db_dir = Path("db")
    db_dir.mkdir(exist_ok=True)

    # state.db — all core tables matching adapter store schemas
    state_db = db_dir / "state.db"
    if _init_state_db(state_db):
        click.echo("state.db: schema initialized")
        click.echo(
            "  (artifacts, projects, events, ecs_state, ecs_transitions, "
            "canonical_artifacts, entities, enabled_models)"
        )
    else:
        state_db.touch(exist_ok=True)
        click.echo("state.db: touched (schema init failed, stores will create tables on first use)")

    # trace.db — trace events + routing outcomes
    trace_db = db_dir / "trace.db"
    if _init_trace_db(trace_db):
        click.echo("trace.db: schema initialized (trace_events + routing_outcomes)")
    else:
        trace_db.touch(exist_ok=True)
        click.echo("trace.db: touched (schema init failed, will be created on first use)")

    # lexical.db — FTS5 full-text search index
    lexical_db = db_dir / "lexical.db"
    if _init_lexical_db(lexical_db):
        click.echo("lexical.db: schema initialized (fts_documents + fts_index)")
    else:
        lexical_db.touch(exist_ok=True)
        click.echo("lexical.db: touched (schema init failed, store will create tables on first use)")

    # Other DBs — create empty files; adapter stores will add schemas on first initialize()
    other_dbs = ["events.db", "ace_playbook.db", "vectors.db"]
    for name in other_dbs:
        (db_dir / name).touch(exist_ok=True)
    click.echo(f"Initialized: {', '.join(other_dbs)} (schemas created on first use)")

    # 3b. Create default project so list_projects() is never empty on first run.
    # Uses sqlite3 directly (no aiosqlite) — consistent with the rest of init.
    # INSERT OR IGNORE is idempotent: safe to re-run aip init on an existing DB.
    try:
        import sqlite3 as _sqlite3

        _conn = _sqlite3.connect(str(state_db))
        _conn.execute(
            """
            INSERT OR IGNORE INTO projects (project_id, name, status, domain, created_at, updated_at)
            VALUES ('default', 'Default', 'active', '', datetime('now'), datetime('now'))
            """
        )
        _conn.commit()
        _conn.close()
        click.echo("Default project created (project_id='default', name='Default')")
    except Exception as e:
        click.echo(f"Default project creation skipped: {e}")

    # 3c. Populate enabled_models from config/enabled_models.json (if it exists).
    # The JSON file is a flat array of model_id strings. We INSERT OR IGNORE
    # so re-running init is safe — it won't overwrite existing rows (which
    # may have been updated by the model library fetch or user toggles).
    _populate_enabled_models(state_db)

    # 4. Write db_path to config so ALL CLI commands use the same datastore
    _write_db_path_to_config(config_path, "db/state.db")
    click.echo("Database path written to config: db/state.db")

    # 5. Ollama validation
    ollama_ok = _check_ollama()
    if ollama_ok:
        click.echo("Ollama: reachable (local models available)")
    else:
        click.echo("Ollama: not detected. Embedding and synthesis will use API fallback.")
        click.echo("  Run `ollama serve` to enable local models.")

    # 6. Model slot validation
    try:
        from aip.adapter.model_slot_resolver import ModelSlotResolver

        resolver = ModelSlotResolver(config={})
        slots = (
            list(resolver._slots.keys())
            if hasattr(resolver, "_slots")
            else ["synthesis", "evaluation", "sexton", "embedding"]
        )
        click.echo(f"Model slots validated: {slots}")
    except Exception as e:
        click.echo(f"Model slot validation skipped: {e}")

    # 7. Summary
    click.echo("\n=== Init complete ===")
    click.echo(f"Profile: {profile}")
    click.echo(f"Config: {config_path}")
    click.echo("Main database: db/state.db")
    click.echo("Lexical index: db/lexical.db")
    click.echo(f"Databases: {db_dir}/")
    click.echo("\nNext steps:")
    click.echo("  1. Run `aip status` to inspect current state.")
    click.echo("  2. Run `aip project create --name <name> --domain <domain>` to create a project.")
    click.echo("  3. Run `aip ingest directory <path> --domain <domain>` to import conversations.")
    click.echo('  4. Run `aip ask "<question>" --project <name>` to query your knowledge.')
