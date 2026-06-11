"""CLI command for conversation ingestion.

Provides ``aip ingest`` for importing conversation files into AIP
stores. Supports ChatGPT export JSON, markdown transcripts, and
plain text transcripts.
"""

from __future__ import annotations

import asyncio
import os
import sys

import click

from aip.foundation.schemas.ingestion import SourceFormat

_VALID_FORMATS = ("chatgpt_json", "markdown", "plaintext", "auto")


@click.group("ingest")
def ingest() -> None:
    """Import conversations into the AIP knowledge substrate.

    Supported formats:
      chatgpt_json  ChatGPT conversations.json export
      markdown      Markdown-formatted transcript (**Role**: content)
      plaintext     Plain text transcript (Role: content)
      auto          Auto-detect from file extension and content (default)

    Imported conversations are stored as artifacts with full provenance
    and indexed into both lexical (FTS5) and vector search.
    """
    pass


@ingest.command("file")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--format",
    "source_format",
    type=click.Choice(_VALID_FORMATS, case_sensitive=False),
    default="auto",
    help="Source format (default: auto-detect).",
)
@click.option("--domain", default="imported", help="Domain tag for indexed content (default: imported).")
@click.option("--project", default=None, help="Project name — resolves domain automatically from project.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
@click.option(
    "--embed/--no-embed",
    default=None,
    help="Enable real embedding using [models.embedding] config during ingest (default: auto from config; --no-embed forces metadata-only vectors)",
)
def ingest_file_cmd(
    path: str, source_format: str, domain: str, project: str | None, db_path: str | None, embed: bool | None
) -> None:
    """Import a conversation file into AIP.

    PATH is the file to import (ChatGPT JSON, markdown, or plain text).
    """
    _run_ingest_file(path, source_format, domain, project, db_path, embed=embed)


@ingest.command("directory")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--format",
    "source_format",
    type=click.Choice(_VALID_FORMATS, case_sensitive=False),
    default="auto",
    help="Source format for all files (default: auto-detect each).",
)
@click.option("--domain", default="imported", help="Domain tag for indexed content (default: imported).")
@click.option("--project", default=None, help="Project name — resolves domain automatically from project.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
@click.option("--recursive/--no-recursive", default=False, help="Recurse into subdirectories.")
@click.option(
    "--embed/--no-embed",
    default=None,
    help="Enable real embedding using [models.embedding] config during ingest (default: auto from config; --no-embed forces metadata-only vectors)",
)
def ingest_directory_cmd(
    directory: str,
    source_format: str,
    domain: str,
    project: str | None,
    db_path: str | None,
    recursive: bool,
    embed: bool | None,
) -> None:
    """Import all conversation files in a directory.

    DIRECTORY is the folder containing files to import.
    """
    _run_ingest_directory(directory, source_format, domain, project, db_path, recursive, embed=embed)


def _resolve_domain(domain: str, project: str | None, db_path: str) -> str:
    """Resolve domain from --project or --domain.

    If --project is given, look up the project's domain from the store.
    If both are given, --project takes precedence (with a warning if they differ).
    Falls back to the --domain value.
    """
    if project is None:
        return domain

    # Try to resolve project domain from the store
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT domain FROM projects WHERE name = ? OR project_id = ?", (project, project))
        row = cursor.fetchone()
        conn.close()
        if row and row["domain"]:
            resolved = row["domain"]
            if domain != "imported" and domain != resolved:
                click.echo(
                    f"Warning: --domain '{domain}' differs from project domain '{resolved}'. Using project domain."
                )
            return resolved
    except Exception:
        pass

    # Fallback: use project name as domain if not explicitly set
    if domain == "imported":
        return project
    return domain


def _run_ingest_file(
    path: str, source_format: str, domain: str, project: str | None, db_path: str | None, embed: bool | None = None
) -> None:
    """Synchronous entry point that runs async ingestion."""
    try:
        results = asyncio.run(_ingest_file_async(path, source_format, domain, project, db_path, embed=embed))
        for result in results:
            _print_result(result)
        total_chunks = sum(r.chunk_count for r in results)
        total_turns = sum(r.turn_count for r in results)
        click.echo(f"\nImported {len(results)} conversation(s): {total_turns} turns, {total_chunks} chunks indexed.")
        effective_domain = domain if project is None else _resolve_domain_sync(domain, project, db_path)
        click.echo(f"Indexed into domain: {effective_domain}")
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"Error during ingestion: {exc}", err=True)
        sys.exit(1)


def _resolve_domain_sync(domain: str, project: str | None, db_path: str | None) -> str:
    """Synchronous domain resolution for output messages."""
    from aip.cli._db_path import get_default_db_path

    resolved_db = db_path or get_default_db_path()
    if project is None:
        return domain
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{resolved_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT domain FROM projects WHERE name = ? OR project_id = ?", (project, project))
        row = cursor.fetchone()
        conn.close()
        if row and row["domain"]:
            return row["domain"]
    except Exception:
        pass
    return domain if domain != "imported" else (project or domain)


def _run_ingest_directory(
    directory: str,
    source_format: str,
    domain: str,
    project: str | None,
    db_path: str | None,
    recursive: bool,
    embed: bool | None = None,
) -> None:
    """Import all files in a directory."""
    supported_extensions = {".json", ".md", ".markdown", ".txt", ".text"}
    files: list[str] = []

    if recursive:
        for root, _dirs, filenames in os.walk(directory):
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in supported_extensions:
                    files.append(os.path.join(root, fname))
    else:
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            if os.path.isfile(fpath) and os.path.splitext(fname)[1].lower() in supported_extensions:
                files.append(fpath)

    if not files:
        click.echo("No supported files found in directory.")
        return

    click.echo(f"Found {len(files)} file(s) to import.")

    all_results = []
    for fpath in sorted(files):
        click.echo(f"\nImporting: {fpath}")
        try:
            results = asyncio.run(_ingest_file_async(fpath, source_format, domain, project, db_path, embed=embed))
            for result in results:
                _print_result(result)
            all_results.extend(results)
        except Exception as exc:
            click.echo(f"  Error: {exc}", err=True)

    total_chunks = sum(r.chunk_count for r in all_results)
    total_turns = sum(r.turn_count for r in all_results)
    click.echo(
        f"\nImported {len(all_results)} conversation(s) "
        f"from {len(files)} file(s): {total_turns} turns, {total_chunks} chunks."
    )
    effective_domain = domain if project is None else _resolve_domain_sync(domain, project, db_path)
    click.echo(f"Indexed into domain: {effective_domain}")


def _print_result(result) -> None:
    """Print a single ingestion result summary."""
    status_parts = []
    if result.lexical_indexed:
        status_parts.append("lexical")
    if result.vector_indexed:
        status_parts.append("vector")
    status = "+".join(status_parts) if status_parts else "none"

    click.echo(f"  {result.conversation_id}: {result.turn_count} turns, {result.chunk_count} chunks [{status}]")
    if result.errors:
        for err in result.errors:
            click.echo(f"    Warning: {err}")


async def _ingest_file_async(
    path: str, source_format: str, domain: str, project: str | None, db_path: str | None, embed: bool | None = None
):
    """Async ingestion implementation."""
    from aip.cli._db_path import ensure_db_dir, get_default_db_path
    from aip.orchestration.ingestion import pipeline as _pipeline

    if db_path is None:
        db_path = get_default_db_path()

    ensure_db_dir(db_path)

    effective_domain = _resolve_domain(domain, project, db_path)

    stores = await _pipeline.create_ingestion_stores(db_path)

    fmt: SourceFormat | None = None if source_format == "auto" else source_format

    # embedding_provider now comes from stores (which uses centralized _create from models.embedding or legacy)
    # --no-embed from CLI can force None (metadata-only, no real embed calls)
    embedding_provider = getattr(stores, "embedding_provider", None)
    if embed is False:
        embedding_provider = None
    # if embed is True, keep whatever stores provided (even if None, it tried)

    try:
        results = await _pipeline.ingest_file(
            path=path,
            artifact_store=stores.artifact_store,
            lexical_store=stores.lexical_store,
            vector_store=stores.vector_store,
            embedding_provider=embedding_provider,
            event_store=stores.event_store,
            source_format=fmt,
            domain=effective_domain,
        )
        return results
    finally:
        await stores.close()
