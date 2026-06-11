"""CLI commands for artifact lifecycle management.

Provides ``aip artifact`` with subcommands:
- create: Create an artifact from content (standalone, not from ask)
- ledger: Inspect full lifecycle history for an artifact

Sprint 11 — Artifact Lifecycle and Review Sprint:
    The artifact lifecycle makes AIP useful for producing real work
    without bypassing sovereignty gates. Every artifact carries
    metadata, sources, state, and review history.
"""

from __future__ import annotations

import asyncio
import sys

import click


@click.group("artifact")
def artifact() -> None:
    """Manage artifact lifecycle — create, inspect, trace.

    Every artifact follows the GENERATED → REVIEWED → APPROVED
    lifecycle with full provenance tracking.
    """
    pass


@artifact.command("create")
@click.option("--title", default="", help="Artifact title (default: first 80 chars of content).")
@click.option("--description", default="", help="Artifact description.")
@click.option("--tag", multiple=True, help="Tags for categorization (can specify multiple).")
@click.option("--type", "artifact_type", default="manual_document", help="Artifact type (default: manual_document).")
@click.option("--project", default="", help="Project name to associate with the artifact.")
@click.option("--source", multiple=True, help="Source IDs referenced by this artifact (can specify multiple).")
@click.option("--prompt", default="", help="Originating prompt that led to this content.")
@click.option("--content", default=None, help="Artifact content (use --content or pipe via stdin).")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def artifact_create(
    title: str,
    description: str,
    tag: tuple[str, ...],
    artifact_type: str,
    project: str,
    source: tuple[str, ...],
    prompt: str,
    content: str | None,
    db_path: str | None,
) -> None:
    """Create a new artifact from content.

    The artifact enters the lifecycle at GENERATED state,
    ready for review. It carries full metadata and source links.

    Content can be provided via --content flag or piped via stdin.

    Examples:

        aip artifact create --title "ADR-012" --content "We decided to..."

        echo "Document content" | aip artifact create --title "My Doc"

        aip artifact create --content "Answer text" --project myproject --source chunk:conv1:0
    """
    # Read content from stdin if not provided via --content
    if content is None:
        if not sys.stdin.isatty():
            content = sys.stdin.read()
        else:
            click.echo("Error: provide --content or pipe content via stdin.", err=True)
            sys.exit(1)

    if not content.strip():
        click.echo("Error: content is empty.", err=True)
        sys.exit(1)

    try:
        result = asyncio.run(
            _artifact_create_async(
                content=content,
                title=title,
                description=description,
                tags=list(tag),
                artifact_type=artifact_type,
                project_name=project,
                source_ids=list(source),
                prompt=prompt,
                db_path=db_path,
            )
        )
        _print_create_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@artifact.command("ledger")
@click.argument("artifact_id")
@click.option("--limit", default=50, type=int, help="Maximum number of ledger entries to show (default: 50).")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def artifact_ledger(artifact_id: str, limit: int, db_path: str | None) -> None:
    """Inspect the full lifecycle history for an artifact.

    Shows every event in the artifact's lifecycle: creation,
    transitions, review verdicts, reviewer notes, exports,
    and force-export exceptions. This is the DEFINER's primary
    tool for understanding how an artifact reached its current state.

    Example:

        aip artifact ledger ask:abc123
    """
    try:
        result = asyncio.run(_artifact_ledger_async(artifact_id, limit, db_path))
        _print_ledger_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Async implementations
# ---------------------------------------------------------------------------


def _get_db_path(db_path: str | None) -> str:
    from aip.cli._db_path import ensure_db_dir, get_default_db_path

    if db_path is None:
        db_path = get_default_db_path()
    ensure_db_dir(db_path)
    return db_path


async def _artifact_create_async(
    content: str,
    title: str,
    description: str,
    tags: list[str],
    artifact_type: str,
    project_name: str,
    source_ids: list[str],
    prompt: str,
    db_path: str | None,
):
    from aip.orchestration.artifact_lifecycle import artifact_create, create_artifact_lifecycle_stores

    stores = await create_artifact_lifecycle_stores(_get_db_path(db_path))
    try:
        return await artifact_create(
            content=content,
            stores=stores,
            title=title,
            description=description,
            tags=tags,
            artifact_type=artifact_type,
            project_name=project_name,
            source_ids=source_ids if source_ids else None,
            prompt=prompt,
        )
    finally:
        await stores.close()


async def _artifact_ledger_async(artifact_id: str, limit: int, db_path: str | None):
    from aip.orchestration.artifact_lifecycle import artifact_ledger, create_artifact_lifecycle_stores

    stores = await create_artifact_lifecycle_stores(_get_db_path(db_path))
    try:
        return await artifact_ledger(artifact_id, stores, limit=limit)
    finally:
        await stores.close()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_create_result(result: dict) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error ({err['code']}): {err['message']}", err=True)
        sys.exit(1)

    click.echo("Artifact created.")
    click.echo(f"  ID:       {result['artifact_id']}")
    click.echo(f"  State:    {result['lifecycle_state']}")
    click.echo(f"  Type:     {result['artifact_type']}")
    click.echo(f"  Title:    {result['title']}")
    click.echo(f"  Sources:  {result['source_count']}")
    click.echo()
    click.echo("  Review with: aip review list --project <project>")
    click.echo(f"  Inspect with: aip review show {result['artifact_id']}")


def _print_ledger_result(result: dict) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error ({err['code']}): {err['message']}", err=True)
        sys.exit(1)

    click.echo("=" * 70)
    click.echo("Artifact Ledger")
    click.echo("=" * 70)
    click.echo(f"  ID:              {result['artifact_id']}")
    click.echo(f"  Current State:   {result['current_state']}")
    click.echo(f"  Title:           {result['title']}")
    click.echo(f"  Type:            {result['artifact_type']}")
    click.echo(f"  Project:         {result['project']}")
    click.echo(f"  Sources:         {result['source_count']}")
    click.echo(f"  Created:         {result['created_at']}")
    click.echo(f"  Transitions:     {result['transition_count']}")
    click.echo(f"  Total Events:    {result['event_count']}")
    click.echo()

    # Ledger entries
    ledger = result.get("ledger", [])
    if not ledger:
        click.echo("  No lifecycle events recorded.")
        return

    click.echo("--- Lifecycle Timeline ---")
    for entry in ledger:
        timestamp = entry.get("timestamp", "")
        event_type = entry.get("event_type", "")
        actor = entry.get("actor", "")
        detail = entry.get("detail", "")
        from_state = entry.get("from_state")
        to_state = entry.get("to_state")

        # Format transition info
        state_info = ""
        if from_state and to_state and from_state != to_state:
            state_info = f" [{from_state} → {to_state}]"

        click.echo(f"  {timestamp[:19]}  {event_type}{state_info}")
        click.echo(f"    by {actor}")
        if detail:
            click.echo(f"    {detail[:120]}")
        click.echo()
