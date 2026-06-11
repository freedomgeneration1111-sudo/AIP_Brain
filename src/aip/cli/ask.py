"""CLI command for source-grounded ask queries.

Provides ``aip ask`` for querying the AIP knowledge substrate using
ingested conversations and project artifacts as context. The ask
pipeline searches existing stores, assembles context, dispatches to
the configured model, and generates source-grounded answers.
"""

from __future__ import annotations

import asyncio
import sys

import click

from aip.foundation.schemas.ask import AskSource

_VALID_SOURCES = ("ingested", "artifacts", "all")


@click.command("ask")
@click.argument("question")
@click.option("--project", required=True, help="Project name to search within.")
@click.option(
    "--source",
    type=click.Choice(_VALID_SOURCES, case_sensitive=False),
    default="all",
    help="Source type filter: ingested conversations, project artifacts, or all (default: all).",
)
@click.option("--max-sources", default=10, type=int, help="Maximum number of sources to use (default: 10).")
@click.option("--save-artifact", is_flag=True, default=False, help="Save the answer as a draft artifact.")
@click.option("--model-slot", default="synthesis", help="Model slot to use for generation (default: synthesis).")
@click.option("--show-context", is_flag=True, default=False, help="Display retrieved context before generation.")
@click.option("--session", default=None, help="Session ID to use (default: auto-generated).")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def ask_cmd(
    question: str,
    project: str,
    source: str,
    max_sources: int,
    save_artifact: bool,
    model_slot: str,
    show_context: bool,
    session: str | None,
    db_path: str | None,
) -> None:
    """Ask AIP a source-grounded question about a project.

    QUESTION is the natural-language query to ask.

    AIP searches the project's ingested conversations and existing artifacts,
    retrieves relevant sources, and generates an answer grounded in those
    sources. Every answer includes provenance back to the retrieved sources.

    Examples:

        aip ask "What is the current state of CodeForge?" --project codeforge

        aip ask "What have we decided about storage?" --project aip_loom --source all --save-artifact

        aip ask "Draft the next architecture section" --project aip_loom --show-context --save-artifact
    """
    _run_ask(question, project, source, max_sources, save_artifact, model_slot, show_context, session, db_path)


def _run_ask(
    question: str,
    project: str,
    source: str,
    max_sources: int,
    save_artifact: bool,
    model_slot: str,
    show_context: bool,
    session: str | None,
    db_path: str | None,
) -> None:
    """Synchronous entry point that runs the async ask pipeline."""
    try:
        result = asyncio.run(
            _ask_async(
                question, project, source, max_sources, save_artifact, model_slot, show_context, session, db_path
            )
        )
        _print_result(result, show_context, project)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


async def _ask_async(
    question: str,
    project: str,
    source: str,
    max_sources: int,
    save_artifact: bool,
    model_slot: str,
    show_context: bool,
    session: str | None,
    db_path: str | None,
):
    """Async ask pipeline implementation."""
    from aip.cli._db_path import ensure_db_dir, get_default_db_path
    from aip.orchestration.ask_pipeline import ask, create_ask_stores, format_context_display

    if db_path is None:
        db_path = get_default_db_path()

    ensure_db_dir(db_path)

    stores = await create_ask_stores(db_path)

    try:
        result = await ask(
            question=question,
            project_name=project,
            stores=stores,
            source=source,
            max_sources=max_sources,
            save_artifact=save_artifact,
            model_slot=model_slot,
            session_id=session,
        )
        return result
    finally:
        await stores.close()


def _print_result(result, show_context: bool, project_name: str = "") -> None:
    """Print the ask result in a user-friendly format."""
    # Status header
    if result.status == "OK":
        click.echo("=" * 60)
        click.echo("AIP Answer")
        click.echo("=" * 60)
        click.echo()
        click.echo(result.answer)
    else:
        click.echo("=" * 60)
        status_labels = {
            "NO_PROJECT": "NO PROJECT",
            "NO_PROJECT_MEMORY": "NO PROJECT MEMORY",
            "NEEDS_CONFIGURATION": "NEEDS CONFIGURATION",
            "MODEL_FAILURE": "MODEL FAILURE",
            "ARTIFACT_SAVE_FAILURE": "ARTIFACT SAVE FAILURE",
        }
        label = status_labels.get(result.status, result.status)
        click.echo(f"AIP: {label}")
        click.echo("=" * 60)
        click.echo()
        click.echo(result.answer)

        # Provide helpful suggestions for common error states
        if result.status == "NO_PROJECT":
            click.echo()
            click.echo(f"Create it with: aip project create --name {project_name} --domain {project_name}")
            click.echo(f"Then ingest: aip ingest directory <path> --project {project_name}")
        elif result.status == "NO_PROJECT_MEMORY":
            domain = getattr(result, "project_id", project_name)
            click.echo()
            click.echo(f"Ingest conversations with: aip ingest directory <path> --project {project_name}")
            click.echo(f"  (or: aip ingest directory <path> --domain {domain})")

    # Source list
    if result.sources:
        click.echo()
        click.echo("--- Sources Used ---")
        for i, src in enumerate(result.sources, 1):
            click.echo(f"  {i}. {src.title} (score={src.score:.4f}, type={src.source_type})")

    if show_context and result.sources:
        from aip.orchestration.ask_pipeline import format_context_display

        click.echo()
        click.echo(format_context_display(result.sources, max_sources=len(result.sources)))

    # Model info
    if result.model_slot or result.model_provider:
        click.echo()
        click.echo("--- Model ---")
        if result.model_slot:
            click.echo(f"  Slot:     {result.model_slot}")
        if result.model_provider:
            click.echo(f"  Provider: {result.model_provider}")

    # Artifact info
    if result.artifact_id:
        click.echo()
        click.echo(f"Artifact saved: {result.artifact_id}")
        click.echo("  Status: GENERATED (pending DEFINER review)")
        click.echo("  Review with: aip review list --project " + project_name)
        click.echo("  Inspect with: aip review show " + result.artifact_id)

    # Session info
    if result.session_id:
        click.echo()
        click.echo(f"Session: {result.session_id}")

    # Errors
    if result.errors:
        click.echo()
        click.echo("--- Warnings/Errors ---")
        for err in result.errors:
            click.echo(f"  - {err}")

    # Sprint 10: Retrieval warnings — visible degradation diagnostics
    if result.retrieval_warnings:
        click.echo()
        click.echo("--- Retrieval Warnings ---")
        click.echo("  Answer generated with degraded retrieval:")
        for warning in result.retrieval_warnings:
            click.echo(f"  - {warning}")

    # Exit code
    if result.status not in ("OK",):
        sys.exit(1)
