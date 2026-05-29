"""aip session subcommand group.

Session management loads ACE Playbook on start.
Currently partially implemented — list/resume require SessionManager API wiring.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click


def _get_db_path() -> str:
    """Resolve database path from config or default."""
    config_path = Path("config/aip.config.toml")
    if config_path.exists():
        for line in config_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("db_path"):
                _, _, val = stripped.partition("=")
                return val.strip().strip('"').strip("'")
    return "db/state.db"


@click.group("session")
def session() -> None:
    """Session management (loads ACE Playbook on start)."""
    pass


@session.command("start")
@click.option("--project-id", required=True, help="Project ID to start session for")
@click.option("--domain", required=True, help="Domain for the session")
def start_session(project_id: str, domain: str) -> None:
    """Start a new session for a project.

    TODO: Wire through SessionManager API once available.
    Currently records session start in the events store.
    """
    import datetime
    import uuid

    session_id = str(uuid.uuid4())[:8]

    db_path = _get_db_path()
    if Path(db_path).exists():
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO events (event_type, actor, artifact_id, detail, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (
                    "session_start",
                    "cli",
                    session_id,
                    f"Session started for project {project_id} in domain {domain}",
                    f'{{"project_id": "{project_id}", "domain": "{domain}"}}',
                ),
            )
            conn.commit()
            conn.close()
            click.echo(f"Session {session_id} started for project {project_id} (domain: {domain})")
            click.echo("  Note: Session management is not yet fully wired. ACE Playbook loading not available in CLI.")
        except Exception as exc:
            click.echo(f"Session {session_id} recorded (event store write failed: {exc})")
    else:
        click.echo(f"Session {session_id} started for project {project_id} (domain: {domain})")
        click.echo("  No database found — session not persisted. Run `aip init` first.")


@session.command("resume")
@click.argument("session_id")
def resume_session(session_id: str) -> None:
    """Resume an existing session.

    TODO: Not yet implemented — requires SessionManager API wiring.
    """
    click.echo(f"Session resume for '{session_id}' is not yet implemented.")
    click.echo("  The SessionManager API is not wired into the CLI layer yet.")
    click.echo("  Use the API server (`uvicorn aip.adapter.api.app:create_app --factory`) for session management.")


@session.command("list")
def list_sessions() -> None:
    """List sessions from the event store.

    Queries recent session_start events from the events table.
    """
    db_path = _get_db_path()
    if not Path(db_path).exists():
        click.echo("No database found — run `aip init` first.")
        return

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT artifact_id, detail, created_at FROM events "
            "WHERE event_type = 'session_start' ORDER BY created_at DESC LIMIT 20",
        ).fetchall()
        conn.close()

        if not rows:
            click.echo("No sessions found.")
            return

        click.echo(f"Recent sessions ({len(rows)}):")
        for row in rows:
            click.echo(f"  {row['artifact_id']}  {row['detail']}  started: {row['created_at']}")
    except Exception as exc:
        click.echo(f"Error querying sessions: {exc}")
