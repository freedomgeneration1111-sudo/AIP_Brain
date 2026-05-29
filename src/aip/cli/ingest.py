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
@click.option("--db-path", default=None, help="SQLite database path (default: data/aip.db).")
def ingest_file_cmd(path: str, source_format: str, domain: str, db_path: str | None) -> None:
    """Import a conversation file into AIP.

    PATH is the file to import (ChatGPT JSON, markdown, or plain text).
    """
    _run_ingest_file(path, source_format, domain, db_path)


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
@click.option("--db-path", default=None, help="SQLite database path (default: data/aip.db).")
@click.option("--recursive/--no-recursive", default=False, help="Recurse into subdirectories.")
def ingest_directory_cmd(
    directory: str,
    source_format: str,
    domain: str,
    db_path: str | None,
    recursive: bool,
) -> None:
    """Import all conversation files in a directory.

    DIRECTORY is the folder containing files to import.
    """
    _run_ingest_directory(directory, source_format, domain, db_path, recursive)


def _run_ingest_file(path: str, source_format: str, domain: str, db_path: str | None) -> None:
    """Synchronous entry point that runs async ingestion."""
    try:
        results = asyncio.run(_ingest_file_async(path, source_format, domain, db_path))
        for result in results:
            _print_result(result)
        total_chunks = sum(r.chunk_count for r in results)
        total_turns = sum(r.turn_count for r in results)
        click.echo(f"\nImported {len(results)} conversation(s): {total_turns} turns, {total_chunks} chunks indexed.")
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"Error during ingestion: {exc}", err=True)
        sys.exit(1)


def _run_ingest_directory(
    directory: str,
    source_format: str,
    domain: str,
    db_path: str | None,
    recursive: bool,
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
            results = asyncio.run(_ingest_file_async(fpath, source_format, domain, db_path))
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


async def _ingest_file_async(path: str, source_format: str, domain: str, db_path: str | None):
    """Async ingestion implementation."""
    from aip.orchestration.ingestion import pipeline as _pipeline

    if db_path is None:
        os.makedirs("data", exist_ok=True)
        db_path = "data/aip.db"

    stores = await _pipeline.create_ingestion_stores(db_path)

    fmt: SourceFormat | None = None if source_format == "auto" else source_format

    try:
        results = await _pipeline.ingest_file(
            path=path,
            artifact_store=stores.artifact_store,
            lexical_store=stores.lexical_store,
            vector_store=stores.vector_store,
            embedding_provider=None,  # CLI runs offline-first, no Ollama assumed
            event_store=stores.event_store,
            source_format=fmt,
            domain=domain,
        )
        return results
    finally:
        await stores.close()
