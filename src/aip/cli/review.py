"""CLI commands for reviewing generated artifacts.

Provides ``aip review`` with subcommands:
- list: Show artifacts pending review for a project
- show: Display artifact content and lifecycle state
- sources: Display source/provenance links
- approve: Approve artifact through existing ECS lifecycle
- reject: Reject artifact with reviewer note
- needs-revision: Mark artifact as needing revision
"""

from __future__ import annotations

import asyncio
import sys

import click


@click.group("review")
def review() -> None:
    """Review generated artifacts — list, inspect, approve, reject.

    All review actions are performed as DEFINER (the human operator).
    No auto-approve path exists.
    """
    pass


@review.command("list")
@click.option("--project", required=True, help="Project name to list artifacts for.")
@click.option("--state", multiple=True, help="Filter by lifecycle state (default: GENERATED).")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def review_list(project: str, state: tuple[str, ...], db_path: str | None) -> None:
    """List artifacts pending review for a project.

    Shows artifact ID, title, lifecycle status, source count, and model info.
    """
    try:
        result = asyncio.run(_review_list_async(project, state, db_path))
        _print_list_result(result, project)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@review.command("show")
@click.argument("artifact_id")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def review_show(artifact_id: str, db_path: str | None) -> None:
    """Show artifact content and lifecycle details.

    Displays artifact ID, title, project, lifecycle state, content,
    originating prompt, model info, source count, and export eligibility.
    """
    try:
        result = asyncio.run(_review_show_async(artifact_id, db_path))
        _print_show_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@review.command("sources")
@click.argument("artifact_id")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def review_sources(artifact_id: str, db_path: str | None) -> None:
    """Display source/provenance links for an artifact.

    Shows where the generated answer's content came from:
    source ID, type, title, snippet, and tracing metadata.
    """
    try:
        result = asyncio.run(_review_sources_async(artifact_id, db_path))
        _print_sources_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@review.command("approve")
@click.argument("artifact_id")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def review_approve(artifact_id: str, db_path: str | None) -> None:
    """Approve a generated artifact through the existing ECS lifecycle.

    Transitions: GENERATED → REVIEWED → APPROVED.
    Writes to CanonicalStore with DEFINER sovereignty.
    Records event in EventStore for provenance.
    No auto-approve — this IS the DEFINER gate.
    """
    try:
        result = asyncio.run(_review_approve_async(artifact_id, db_path))
        _print_action_result(result, "Approved")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@review.command("reject")
@click.argument("artifact_id")
@click.option("--note", default="", help="Reason for rejection.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def review_reject(artifact_id: str, note: str, db_path: str | None) -> None:
    """Reject a generated artifact. Preserves artifact and source links.

    Transition: GENERATED → REJECTED or REVIEWED → REJECTED.
    The artifact is NOT deleted. It can be re-generated later.
    """
    try:
        result = asyncio.run(_review_reject_async(artifact_id, note, db_path))
        _print_action_result(result, "Rejected")
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@review.command("needs-revision")
@click.argument("artifact_id")
@click.option("--note", default="", help="Revision instruction for the artifact.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def review_needs_revision(artifact_id: str, note: str, db_path: str | None) -> None:
    """Mark artifact as needing revision. Preserves artifact and source links.

    The artifact stays in its current ECS state. The revision instruction
    is stored as a review event for later reference.
    """
    try:
        result = asyncio.run(_review_needs_revision_async(artifact_id, note, db_path))
        _print_action_result(result, "Needs Revision")
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


async def _review_list_async(project: str, state: tuple[str, ...], db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_list

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        states = list(state) if state else None
        return await review_list(project, stores, states=states)
    finally:
        await stores.close()


async def _review_show_async(artifact_id: str, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_show

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await review_show(artifact_id, stores)
    finally:
        await stores.close()


async def _review_sources_async(artifact_id: str, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_sources

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await review_sources(artifact_id, stores)
    finally:
        await stores.close()


async def _review_approve_async(artifact_id: str, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_approve

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await review_approve(artifact_id, stores)
    finally:
        await stores.close()


async def _review_reject_async(artifact_id: str, note: str, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_reject

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await review_reject(artifact_id, stores, note=note)
    finally:
        await stores.close()


async def _review_needs_revision_async(artifact_id: str, note: str, db_path: str | None):
    from aip.orchestration.review_export_pipeline import create_review_export_stores, review_needs_revision

    stores = await create_review_export_stores(_get_db_path(db_path))
    try:
        return await review_needs_revision(artifact_id, stores, instruction=note)
    finally:
        await stores.close()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_list_result(result: dict, project_name: str = "") -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error: {err['message']}", err=True)
        if "NOT_FOUND" in err.get("code", ""):
            click.echo(f"Create it with: aip project create --name {project_name} --domain {project_name}")
        sys.exit(1)

    artifacts = result.get("artifacts", [])
    project = result.get("project", project_name)

    if not artifacts:
        click.echo(f"No artifacts found for project '{project}'.")
        click.echo(f"Generate one with: aip ask \"<question>\" --project {project} --save-artifact")
        return

    click.echo(f"Artifacts for project '{project}':")
    click.echo("=" * 80)
    for art in artifacts:
        click.echo(f"  ID:       {art['artifact_id']}")
        click.echo(f"  Title:    {art['title']}")
        click.echo(f"  State:    {art['lifecycle_state']}")
        click.echo(f"  Created:  {art['created_at']}")
        click.echo(f"  Sources:  {art['source_count']}")
        if art.get("session_id"):
            click.echo(f"  Session:  {art['session_id']}")
        if art.get("model_slot"):
            click.echo(f"  Model:    {art['model_slot']}")
        click.echo("")


def _print_show_result(result: dict) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error: {err['message']}", err=True)
        sys.exit(1)

    click.echo("=" * 60)
    click.echo("Artifact Details")
    click.echo("=" * 60)
    click.echo(f"  ID:             {result['artifact_id']}")
    click.echo(f"  Title:          {result['title']}")
    click.echo(f"  Project:        {result['project']}")
    click.echo(f"  Lifecycle:      {result['lifecycle_state']}")
    click.echo(f"  Type:           {result.get('artifact_type', '')}")
    click.echo(f"  Sources:        {result['source_count']}")
    click.echo(f"  Generated:      {result.get('generated_at', '')}")
    if result.get("prompt"):
        click.echo(f"  Prompt:         {result['prompt'][:120]}")
    if result.get("model_slot"):
        click.echo(f"  Model Slot:     {result['model_slot']}")
    if result.get("model_name"):
        click.echo(f"  Model Provider: {result['model_name']}")
    if result.get("session_id"):
        click.echo(f"  Session:        {result['session_id']}")

    # Export eligibility
    if result.get("export_blocked"):
        click.echo("  Export:         BLOCKED (rejected)")
    elif result.get("export_warn"):
        click.echo("  Export:         WARNING (unreviewed — use --force)")
    else:
        click.echo("  Export:         Eligible")

    # Review notes
    notes = result.get("review_notes", [])
    if notes:
        click.echo()
        click.echo("--- Review Notes ---")
        for note in notes:
            click.echo(f"  [{note.get('verdict', '')}] {note.get('detail', '')}")
            click.echo(f"    by {note.get('actor', '')} at {note.get('timestamp', '')}")

    # Content
    click.echo()
    click.echo("--- Content ---")
    click.echo(result["content"])


def _print_sources_result(result: dict) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error: {err['message']}", err=True)
        sys.exit(1)

    click.echo("=" * 60)
    click.echo(f"Source Links for Artifact: {result['artifact_id']}")
    click.echo(f"Total sources: {result['source_count']}")
    click.echo("=" * 60)

    sources = result.get("sources", [])
    if not sources:
        click.echo("No source links recorded.")
        return

    for i, src in enumerate(sources, 1):
        click.echo(f"\nSource {i}:")
        click.echo(f"  ID:       {src['source_id']}")
        click.echo(f"  Type:     {src['source_type']}")
        click.echo(f"  Title:    {src['title']}")
        if src.get("snippet"):
            click.echo(f"  Snippet:  {src['snippet'][:150]}")
        meta = src.get("metadata", {})
        if meta:
            for k, v in meta.items():
                if v:
                    click.echo(f"  {k}: {v}")


def _print_action_result(result: dict, action: str) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error ({err['code']}): {err['message']}", err=True)
        sys.exit(1)

    click.echo(f"{action}: {result['artifact_id']}")
    click.echo(f"  Lifecycle state: {result['lifecycle_state']}")
    if result.get("previous_state"):
        click.echo(f"  Previous state:  {result['previous_state']}")
    if result.get("note"):
        click.echo(f"  Note:            {result['note']}")
    if result.get("instruction"):
        click.echo(f"  Instruction:     {result['instruction']}")
    if result.get("artifact_preserved"):
        click.echo("  Artifact preserved: Yes")
    if result.get("canonical_written"):
        click.echo("  Canonical store: Written (DEFINER approved)")
