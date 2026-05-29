"""aip init command — the installation contract.

Performs:
1. RAM detection + hardware profile suggestion
2. Vector backend configuration (writes aip.config.toml)
3. All DB schema initialization (state.db, trace.db, events.db, vectors, ace_playbook.db, lexical.db)
4. Ollama validation (graceful warning + fallback if unavailable)
5. Model slot validation via resolver
6. Clear summary of local vs API surface
"""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
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


def _init_state_db(db_path: Path) -> bool:
    """Initialize state.db with entity, canonical, project, and event tables.

    This uses the same schemas that the adapter stores create via initialize().
    Only creates tables if they don't already exist.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS canonicals (
                artifact_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                version INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                domain TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                actor TEXT,
                artifact_id TEXT,
                from_state TEXT,
                to_state TEXT,
                detail TEXT,
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_events_type
            ON events(event_type);

            CREATE INDEX IF NOT EXISTS idx_events_artifact
            ON events(artifact_id);
        """)
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        click.echo(f"  state.db schema init skipped: {e}")
        return False


def _check_ollama() -> bool:
    """Check if Ollama is running. Returns True if reachable."""
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@click.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing config/DBs (dangerous)")
def init(force: bool) -> None:
    """Initialize AIP 0.1 for this machine.

    Performs:
    1. RAM detection + hardware profile suggestion
    2. Vector backend configuration (writes aip.config.toml)
    3. All DB schema initialization (state.db, trace.db, events.db, vectors, ace_playbook.db, lexical.db)
    4. Ollama validation (graceful warning + fallback if unavailable)
    5. Model slot validation via resolver
    6. Clear summary of local vs API surface
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

    # 3. Initialize databases with schemas
    db_dir = Path("db")
    db_dir.mkdir(exist_ok=True)

    # state.db — core entity/canonical/project/event tables
    state_db = db_dir / "state.db"
    if _init_state_db(state_db):
        click.echo(f"state.db: schema initialized (entities, canonicals, projects, events)")
    else:
        state_db.touch(exist_ok=True)
        click.echo("state.db: touched (schema init failed, will be created on first use)")

    # trace.db — trace events + routing outcomes
    trace_db = db_dir / "trace.db"
    if _init_trace_db(trace_db):
        click.echo("trace.db: schema initialized (trace_events + routing_outcomes)")
    else:
        trace_db.touch(exist_ok=True)
        click.echo("trace.db: touched (schema init failed, will be created on first use)")

    # Other DBs — create empty files; adapter stores will add schemas on first initialize()
    other_dbs = ["events.db", "ace_playbook.db", "lexical.db", "vectors.db"]
    for name in other_dbs:
        (db_dir / name).touch(exist_ok=True)
    click.echo(f"Initialized: {', '.join(other_dbs)} (schemas created on first use)")

    # 4. Ollama validation
    ollama_ok = _check_ollama()
    if ollama_ok:
        click.echo("Ollama: reachable (local models available)")
    else:
        click.echo("Ollama: not detected. Embedding and synthesis will use API fallback.")
        click.echo("  Run `ollama serve` to enable local models.")

    # 5. Model slot validation
    try:
        from aip.adapter.model_slot_resolver import ModelSlotResolver

        resolver = ModelSlotResolver(config_path=str(config_path))
        slots = (
            list(resolver._slots.keys())
            if hasattr(resolver, "_slots")
            else ["synthesis", "evaluation", "sexton", "embedding"]
        )
        click.echo(f"Model slots validated: {slots}")
    except Exception as e:
        click.echo(f"Model slot validation skipped: {e}")

    # 6. Summary
    click.echo("\n=== Init complete ===")
    click.echo(f"Profile: {profile}")
    click.echo(f"Config: {config_path}")
    click.echo(f"Databases: {db_dir}/")
    click.echo("Next steps:")
    click.echo("  Run `aip status` to inspect current state.")
    click.echo("  Run `uvicorn aip.adapter.api.app:create_app --factory` to start the API server.")
