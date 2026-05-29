"""CLI commands for exporting artifacts to markdown.

Provides ``aip export`` with subcommands:
- artifact: Export a single artifact to markdown
- project: Export all approved artifacts for a project
"""

from __future__ import annotations

import asyncio
import sys

import click


@click.group("export")
def export() -> None:
    """Export artifacts to markdown.

    Exported files include metadata frontmatter and source/provenance footer.
    Rejected artifacts are not exported by default.
    """
    pass


@export.command("artifact")
@click.argument("artifact_id")
@click.option("--format", "fmt", type=click.Choice(["markdown"]), default="markdown", help="Export format (default: markdown).")
@click.option("--out", required=True, help="Output file path.")
@click.option("--force", is_flag=True, default=False, help="Force export of rejected or unreviewed artifacts.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def export_artifact(artifact_id: str, fmt: str, out: str, force: bool, db_path: str | None) -> None:
    """Export an artifact to a markdown file.

    Includes metadata frontmatter and source/provenance footer.
    Refuses to export REJECTED artifacts by default.
    Warns for unreviewed (GENERATED/REVIEWED) artifacts unless --force.
    """
    try:
        result = asyncio.run(_export_artifact_async(artifact_id, out, fmt, force, db_path))
        _print_export_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@export.command("project")
@click.argument("project_name")
@click.option("--format", "fmt", type=click.Choice(["markdown"]), default="markdown", help="Export format (default: markdown).")
@click.option("--out", required=True, help="Output file path.")
@click.option("--include-unreviewed", is_flag=True, default=False, help="Include GENERATED/REVIEWED artifacts (default: APPROVED only).")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def export_project(project_name: str, fmt: str, out: str, include_unreviewed: bool, db_path: str | None) -> None:
    """Export approved artifacts for a project to a markdown bundle.

    Includes an artifact index and provenance metadata.
    Excludes REJECTED artifacts by default.
    Excludes unreviewed artifacts unless --include-unreviewed.
    """
    try:
        result = asyncio.run(_export_project_async(project_name, out, fmt, include_unreviewed, db_path))
        _print_export_result(result)
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


async def _export_artifact_async(artifact_id: str, out: str, fmt: str, force: bool, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, export_artifact

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await export_artifact(artifact_id, out, stores, format=fmt, force=force)
    finally:
        await stores.close()


async def _export_project_async(project_name: str, out: str, fmt: str, include_unreviewed: bool, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, export_project

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await export_project(project_name, out, stores, format=fmt, include_unreviewed=include_unreviewed)
    finally:
        await stores.close()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_export_result(result: dict) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error ({err['code']}): {err['message']}", err=True)
        sys.exit(1)

    if result.get("artifacts_exported") is not None:
        # Project export
        click.echo(f"Project export complete.")
        click.echo(f"  Project:  {result['project']}")
        click.echo(f"  Exported: {result['artifacts_exported']} artifacts")
        click.echo(f"  Output:   {result['out_path']}")
        click.echo(f"  Size:     {result.get('bytes_written', 0)} bytes")
    else:
        # Artifact export
        click.echo(f"Artifact exported.")
        click.echo(f"  ID:       {result['artifact_id']}")
        click.echo(f"  State:    {result.get('lifecycle_state', '')}")
        click.echo(f"  Output:   {result['out_path']}")
        click.echo(f"  Size:     {result.get('bytes_written', 0)} bytes")
