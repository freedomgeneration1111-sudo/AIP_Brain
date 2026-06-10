"""CLI commands for exporting artifacts to markdown.

Provides ``aip export`` with subcommands:
- artifact: Export a single artifact to markdown
- project: Export all approved artifacts for a project

Chunk 7 — Review/export gate integrity:
    - --force is an explicit emergency/debug path, not a casual override.
    - When --force is used, a loud warning is printed and confirmation is
      required (unless --yes is given for CI/scripts).
    - --reason is strongly recommended with --force; the reason is recorded
      in the audit trail.
    - Every force-export writes a ``force_export`` audit event.
    - Normal export only exports APPROVED artifacts.
"""

from __future__ import annotations

import asyncio
import sys

import click


@click.group("export")
def export() -> None:
    """Export artifacts to markdown.

    Exported files include metadata frontmatter and source/provenance footer.
    Normal export only exports APPROVED artifacts.
    Use --force for emergency/debug export of non-APPROVED artifacts
    (audit event will be recorded).
    """
    pass


@export.command("artifact")
@click.argument("artifact_id")
@click.option("--format", "fmt", type=click.Choice(["markdown"]), default="markdown", help="Export format (default: markdown).")
@click.option("--out", required=True, help="Output file path.")
@click.option("--force", is_flag=True, default=False, help="EMERGENCY/DEBUG: Force export of non-APPROVED artifacts. Audit event will be recorded.")
@click.option("--reason", default="", help="Reason for force-export (recorded in audit trail). Strongly recommended with --force.")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt (for CI/scripts). Only meaningful with --force.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def export_artifact(artifact_id: str, fmt: str, out: str, force: bool, reason: str, yes: bool, db_path: str | None) -> None:
    """Export an artifact to a markdown file.

    Includes metadata frontmatter and source/provenance footer.
    Normal export: only APPROVED artifacts.
    Force export (emergency/debug): non-APPROVED artifacts with audit trail.
    """
    # Force-export gate: warn and confirm
    if force:
        _print_force_warning(artifact_id, reason)
        if not yes:
            if not click.confirm("\n  Proceed with force-export?", default=False):
                click.echo("Aborted.")
                sys.exit(0)
        if not reason:
            click.echo(
                "  WARNING: No --reason provided. The audit event will record "
                "'(no explicit reason provided)'. Consider providing --reason.",
                err=True,
            )

    try:
        result = asyncio.run(_export_artifact_async(artifact_id, out, fmt, force, reason, db_path))
        _print_export_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@export.command("project")
@click.argument("project_name")
@click.option("--format", "fmt", type=click.Choice(["markdown"]), default="markdown", help="Export format (default: markdown).")
@click.option("--out", required=True, help="Output file path.")
@click.option("--include-unreviewed", is_flag=True, default=False, help="Include GENERATED/REVIEWED artifacts (sovereign override with audit trail). Default: APPROVED only.")
@click.option("--reason", default="", help="Reason for including unreviewed artifacts (recorded in audit trail). Recommended with --include-unreviewed.")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt (for CI/scripts). Only meaningful with --include-unreviewed.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def export_project(project_name: str, fmt: str, out: str, include_unreviewed: bool, reason: str, yes: bool, db_path: str | None) -> None:
    """Export approved artifacts for a project to a markdown bundle.

    Includes an artifact index and provenance metadata.
    Default: APPROVED artifacts only.
    With --include-unreviewed: also includes GENERATED/REVIEWED artifacts
    (each recorded as a sovereign override with audit event).
    REJECTED artifacts are always excluded.
    """
    # Include-unreviewed gate: warn and confirm
    if include_unreviewed:
        _print_include_unreviewed_warning(project_name, reason)
        if not yes:
            if not click.confirm("\n  Proceed with including unreviewed artifacts?", default=False):
                click.echo("Aborted.")
                sys.exit(0)
        if not reason:
            click.echo(
                "  WARNING: No --reason provided. Audit events will record "
                "'(no explicit reason provided)'. Consider providing --reason.",
                err=True,
            )

    try:
        result = asyncio.run(_export_project_async(project_name, out, fmt, include_unreviewed, reason, db_path))
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


async def _export_artifact_async(artifact_id: str, out: str, fmt: str, force: bool, force_reason: str, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, export_artifact

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await export_artifact(artifact_id, out, stores, format=fmt, force=force, force_reason=force_reason)
    finally:
        await stores.close()


async def _export_project_async(project_name: str, out: str, fmt: str, include_unreviewed: bool, force_reason: str, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, export_project

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await export_project(project_name, out, stores, format=fmt, include_unreviewed=include_unreviewed, force_reason=force_reason)
    finally:
        await stores.close()


# ---------------------------------------------------------------------------
# Warning helpers
# ---------------------------------------------------------------------------


def _print_force_warning(artifact_id: str, reason: str) -> None:
    """Print a loud warning when --force is used."""
    click.echo("", err=True)
    click.echo("  ============================================================", err=True)
    click.echo("  SOVEREIGN OVERRIDE: FORCE-EXPORT", err=True)
    click.echo("  ============================================================", err=True)
    click.echo(f"  Artifact '{artifact_id}' is NOT in APPROVED state.", err=True)
    click.echo("  Force-export bypasses the DEFINER review gate.", err=True)
    click.echo("  This action will be recorded in the audit trail.", err=True)
    if reason:
        click.echo(f"  Reason: {reason}", err=True)
    else:
        click.echo("  Reason: (not provided — use --reason for a clear audit trail)", err=True)
    click.echo("  ============================================================", err=True)
    click.echo("", err=True)


def _print_include_unreviewed_warning(project_name: str, reason: str) -> None:
    """Print a loud warning when --include-unreviewed is used."""
    click.echo("", err=True)
    click.echo("  ============================================================", err=True)
    click.echo("  SOVEREIGN OVERRIDE: INCLUDING UNREVIEWED ARTIFACTS", err=True)
    click.echo("  ============================================================", err=True)
    click.echo(f"  Project '{project_name}': exporting non-APPROVED artifacts.", err=True)
    click.echo("  Each unreviewed artifact will be recorded as a sovereign", err=True)
    click.echo("  override in the audit trail.", err=True)
    if reason:
        click.echo(f"  Reason: {reason}", err=True)
    else:
        click.echo("  Reason: (not provided — use --reason for a clear audit trail)", err=True)
    click.echo("  ============================================================", err=True)
    click.echo("", err=True)


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
        if result.get("sovereign_override_count"):
            click.echo(
                f"  Sovereign overrides: {result['sovereign_override_count']} artifact(s) "
                "exported from non-APPROVED state (audit recorded)",
            )
        click.echo(f"  Output:   {result['out_path']}")
        click.echo(f"  Size:     {result.get('bytes_written', 0)} bytes")
    else:
        # Artifact export
        click.echo(f"Artifact exported.")
        click.echo(f"  ID:       {result['artifact_id']}")
        click.echo(f"  State:    {result.get('lifecycle_state', '')}")
        if result.get("force_bypass"):
            click.echo(f"  ** SOVEREIGN OVERRIDE: exported from {result.get('force_bypass_state', '')} state **")
            click.echo(f"  Audit:    Recorded (force_export event)")
            click.echo(f"  Reason:   {result.get('force_reason', '')}")
        click.echo(f"  Output:   {result['out_path']}")
        click.echo(f"  Size:     {result.get('bytes_written', 0)} bytes")
