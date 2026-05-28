"""aip project subcommand group.

Uses ProjectStore (SqliteProjectStore) via direct store access.
Writes go through AutonomyGate in production.
"""

from __future__ import annotations

import json
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


@click.group("project")
def project() -> None:
    """Project management (uses ProjectStore / EcsStore via injection)."""
    pass


@project.command("list")
def list_projects() -> None:
    """List all projects from the ProjectStore."""
    db_path = _get_db_path()
    if not Path(db_path).exists():
        click.echo("No database found — run `aip init` first.")
        return

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # Check if projects table exists
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
        ).fetchall()]
        if not tables:
            click.echo("Projects table not found — database may need initialization.")
            return

        rows = conn.execute(
            "SELECT project_id, name, domain, created_at, updated_at FROM projects ORDER BY created_at DESC"
        ).fetchall()
        conn.close()

        if not rows:
            click.echo("No projects found.")
            return

        click.echo(f"Projects ({len(rows)}):")
        for row in rows:
            click.echo(f"  {row['project_id']}  {row['name']}  [{row['domain']}]  created: {row['created_at']}")
    except Exception as exc:
        click.echo(f"Error querying projects: {exc}")


@project.command("create")
@click.option("--name", required=True, help="Project name")
@click.option("--domain", required=True, help="Project domain")
def create_project(name: str, domain: str) -> None:
    """Create a new project.

    In production, writes go through AutonomyGate (admin level).
    Currently creates directly in the store.
    """
    import uuid
    db_path = _get_db_path()
    if not Path(db_path).exists():
        click.echo("No database found — run `aip init` first.")
        return

    # TODO: Wire through AutonomyGate for admin-level write approval
    try:
        from aip.adapter.project.sqlite_project_store import SqliteProjectStore
        store = SqliteProjectStore(db_path)
        import asyncio
        asyncio.run(store.initialize())
        project_id = str(uuid.uuid4())[:8]
        result = asyncio.run(store.create_project(
            project_id=project_id, name=name, domain=domain
        ))
        click.echo(f"Created project: {project_id} — {name} [{domain}]")
    except ImportError:
        # Fallback: direct SQL
        try:
            conn = sqlite3.connect(db_path)
            project_id = str(uuid.uuid4())[:8]
            conn.execute(
                "INSERT OR IGNORE INTO projects (project_id, name, domain) VALUES (?, ?, ?)",
                (project_id, name, domain),
            )
            conn.commit()
            conn.close()
            click.echo(f"Created project: {project_id} — {name} [{domain}]")
        except Exception as exc:
            click.echo(f"Error creating project: {exc}")
    except Exception as exc:
        click.echo(f"Error creating project: {exc}")


@project.command("show")
@click.argument("project_id")
def show_project(project_id: str) -> None:
    """Show project details and work units."""
    db_path = _get_db_path()
    if not Path(db_path).exists():
        click.echo("No database found — run `aip init` first.")
        return

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        conn.close()

        if row:
            for key in row.keys():
                click.echo(f"  {key}: {row[key]}")
        else:
            click.echo(f"Project '{project_id}' not found.")
    except Exception as exc:
        click.echo(f"Error querying project: {exc}")
