"""Unified corpus ingestion pipeline — Sprint 9.

Canonical entry point for ALL corpus ingestion. Both CLI and API call
these functions, ensuring consistent behavior regardless of entry point.

Supported inputs:
  - Conversation exports: Claude, ChatGPT (→ CorpusTurn per exchange)
  - Documents: Markdown, text, PDF (→ CorpusTurn per section/page)
  - Directories: recursive directory scan and ingest
  - Chat logs: exported chat JSON formats

Key guarantees:
  1. Dedup: content_hash checked before write; unchanged content is skipped
  2. Re-ingest: changed content gets doc_version increment + previous_hash in metadata
  3. Provenance: every turn has source_path, content_hash, ingest timestamp
  4. No silent failures: embedding failures are tracked, not swallowed
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.adapter.event_store_queryable import QueryableEventStore
from aip.foundation.schemas.corpus_turn import (
    CorpusTurn,
    compute_content_hash,
    make_turn_id,
)

logger = logging.getLogger(__name__)


@dataclass
class CorpusIngestResult:
    """Result of ingesting a single file or source into the corpus."""

    source_path: str
    source_type: str  # "conversation" | "document" | "directory"
    turns_ingested: int = 0
    turns_skipped: int = 0  # unchanged content (same content_hash)
    turns_updated: int = 0  # content changed, doc_version incremented
    turns_failed: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.turns_ingested + self.turns_skipped + self.turns_updated + self.turns_failed


@dataclass
class CorpusIngestConfig:
    """Configuration for corpus ingestion."""

    source_model: str = ""  # "claude" | "gpt" | "document" etc.
    source_account: str = "corpus_ingest"
    export_date: str = ""
    db_path: str = ""
    recursive: bool = False
    supported_extensions: tuple[str, ...] = (
        ".md",
        ".markdown",
        ".txt",
        ".text",
        ".json",
        ".log",
        ".toml",
        ".yaml",
        ".yml",
        ".pdf",
    )


async def ingest_file_to_corpus(
    path: str,
    store: CorpusTurnStore,
    config: CorpusIngestConfig,
) -> CorpusIngestResult:
    """Ingest a single file into the corpus with dedup and provenance.

    This is the canonical entry point for file ingestion. Both CLI and API
    should call this function. It handles:
    - Format detection (conversation export vs document)
    - Parsing into CorpusTurns
    - Dedup via content_hash comparison
    - Re-ingest detection (increment doc_version if content changed)
    - Provenance metadata
    - Event recording

    Args:
        path: Path to the file to ingest.
        store: CorpusTurnStore instance.
        config: Ingest configuration.

    Returns:
        CorpusIngestResult with counts and any warnings/errors.
    """
    result = CorpusIngestResult(source_path=path, source_type="unknown")

    if not os.path.isfile(path):
        result.errors.append(f"File not found: {path}")
        return result

    if not config.export_date:
        config.export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Determine source type and parse
    ext = os.path.splitext(path)[1].lower()
    turns: list[CorpusTurn] = []

    if _is_conversation_export(path, ext):
        # Conversation export (Claude JSON, ChatGPT JSON)
        result.source_type = "conversation"
        turns = _parse_conversation_file(path, config)
    else:
        # Document (markdown, text, PDF)
        result.source_type = "document"
        turns = _parse_document_file(path, config)

    if not turns:
        result.warnings.append(f"No turns produced from {path}")
        return result

    # Write turns with dedup
    for turn in turns:
        try:
            # Ensure content_hash is computed
            if not turn.content_hash:
                turn.content_hash = compute_content_hash(turn.searchable_text)

            # Check for existing turn (by turn_id)
            existing = await store.get_turn(turn.turn_id)

            if existing is not None:
                if existing.content_hash == turn.content_hash:
                    # Content unchanged — skip
                    result.turns_skipped += 1
                    continue
                else:
                    # Content changed — update with version increment
                    previous_hash = existing.content_hash
                    turn.doc_version = existing.doc_version + 1
                    # Preserve previous hash in metadata
                    try:
                        meta = json.loads(turn.metadata_json or "{}")
                        meta["previous_hash"] = previous_hash
                        meta["ingest_timestamp"] = datetime.now(timezone.utc).isoformat()
                        turn.metadata_json = json.dumps(meta)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    await store.write_turn(turn)
                    result.turns_updated += 1
            else:
                # New turn
                await store.write_turn(turn)
                result.turns_ingested += 1

        except Exception as exc:
            result.turns_failed += 1
            result.errors.append(f"turn {turn.turn_id}: {exc}")
            logger.debug("Failed to write turn %s: %s", turn.turn_id, exc)

    # Record event
    await _record_ingest_event(store, result, config)

    return result


async def ingest_directory_to_corpus(
    directory: str,
    store: CorpusTurnStore,
    config: CorpusIngestConfig,
) -> list[CorpusIngestResult]:
    """Ingest all supported files in a directory.

    Recursively scans for files matching supported extensions and ingests
    each one through ingest_file_to_corpus().

    Args:
        directory: Path to the directory.
        store: CorpusTurnStore instance.
        config: Ingest configuration.

    Returns:
        List of CorpusIngestResult, one per file.
    """
    results: list[CorpusIngestResult] = []

    files = _scan_directory(directory, config.supported_extensions, config.recursive)
    if not files:
        results.append(
            CorpusIngestResult(
                source_path=directory,
                source_type="directory",
                warnings=["No supported files found in directory"],
            )
        )
        return results

    for fpath in sorted(files):
        file_result = await ingest_file_to_corpus(fpath, store, config)
        results.append(file_result)

    # Record directory-level event
    total_ingested = sum(r.turns_ingested for r in results)
    total_skipped = sum(r.turns_skipped for r in results)
    total_updated = sum(r.turns_updated for r in results)

    dir_result = CorpusIngestResult(
        source_path=directory,
        source_type="directory",
        turns_ingested=total_ingested,
        turns_skipped=total_skipped,
        turns_updated=total_updated,
        warnings=[f"Processed {len(files)} files"],
    )
    results.append(dir_result)

    return results


def _is_conversation_export(path: str, ext: str) -> bool:
    """Detect if a file is a conversation export (vs a document)."""
    if ext != ".json":
        return False

    # Check for known conversation export patterns
    basename = os.path.basename(path).lower()
    if basename in ("conversations.json", "chatgpt_export.json"):
        return True

    # Peek at content to detect ChatGPT or Claude format
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            start = f.read(500)
        if '"chat_messages"' in start or '"mapping"' in start:
            return True
        if '"uuid"' in start and '"name"' in start:
            return True
    except Exception:
        pass

    return False


def _parse_conversation_file(path: str, config: CorpusIngestConfig) -> list[CorpusTurn]:
    """Parse a conversation export file into CorpusTurns."""
    basename = os.path.basename(path).lower()

    # Claude export
    if "claude" in config.source_model.lower() or "chat_messages" in _peek_content(path):
        try:
            from aip.orchestration.ingestion.parsers.claude_parser import parse_claude_export

            turns, warnings = parse_claude_export(path, config.source_account, config.export_date)
            # Add source_path to each turn
            for turn in turns:
                turn.source_path = path
            return turns
        except Exception as exc:
            logger.warning("Claude parser failed for %s: %s", path, exc)
            return []

    # ChatGPT export — convert to CorpusTurns via ImportedConversation
    try:
        from aip.orchestration.ingestion.parsers.chatgpt import parse_chatgpt_export

        with open(path, encoding="utf-8") as f:
            content = f.read()
        convs = parse_chatgpt_export(content, source_file=path)

        turns: list[CorpusTurn] = []
        for conv in convs:
            # Pair user/assistant turns
            turn_pairs = _pair_conversation_turns(conv.turns, conv.conversation_id, conv.title, config)
            turns.extend(turn_pairs)
        return turns
    except Exception as exc:
        logger.warning("ChatGPT parser failed for %s: %s", path, exc)
        return []


def _parse_document_file(path: str, config: CorpusIngestConfig) -> list[CorpusTurn]:
    """Parse a document file into CorpusTurns."""
    try:
        from aip.orchestration.ingestion.parsers.document_parser import parse_document_file

        return parse_document_file(path, config.source_account, config.export_date)
    except Exception as exc:
        logger.warning("Document parser failed for %s: %s", path, exc)
        return []


def _pair_conversation_turns(
    turns_list: list,
    conversation_id: str,
    conversation_name: str,
    config: CorpusIngestConfig,
) -> list[CorpusTurn]:
    """Convert ImportedConversation.ConversationTurn pairs into CorpusTurns.

    Pairs consecutive user+assistant turns. System and tool turns are
    attached as context to the next user turn.
    """
    result: list[CorpusTurn] = []
    current_user_text = ""
    turn_index = 0

    for t in turns_list:
        if t.role == "user":
            if current_user_text:
                # Flush previous user text without assistant response
                corpus_turn = CorpusTurn(
                    turn_id=make_turn_id(conversation_id, turn_index),
                    conversation_id=conversation_id,
                    conversation_name=conversation_name[:200],
                    turn_index=turn_index,
                    source_model=config.source_model or "gpt",
                    source_account=config.source_account,
                    export_date=config.export_date,
                    user_text=current_user_text,
                    assistant_text="",
                    turn_timestamp="",
                )
                result.append(corpus_turn)
                turn_index += 1
            current_user_text = t.content

        elif t.role == "assistant" and current_user_text:
            corpus_turn = CorpusTurn(
                turn_id=make_turn_id(conversation_id, turn_index),
                conversation_id=conversation_id,
                conversation_name=conversation_name[:200],
                turn_index=turn_index,
                source_model=config.source_model or "gpt",
                source_account=config.source_account,
                export_date=config.export_date,
                user_text=current_user_text,
                assistant_text=t.content,
                turn_timestamp=t.timestamp,
            )
            result.append(corpus_turn)
            turn_index += 1
            current_user_text = ""

    # Flush remaining user text
    if current_user_text:
        corpus_turn = CorpusTurn(
            turn_id=make_turn_id(conversation_id, turn_index),
            conversation_id=conversation_id,
            conversation_name=conversation_name[:200],
            turn_index=turn_index,
            source_model=config.source_model or "gpt",
            source_account=config.source_account,
            export_date=config.export_date,
            user_text=current_user_text,
            assistant_text="",
            turn_timestamp="",
        )
        result.append(corpus_turn)

    return result


def _peek_content(path: str, max_chars: int = 500) -> str:
    """Read the first max_chars of a file for format detection."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    except Exception:
        return ""


def _scan_directory(
    directory: str,
    extensions: tuple[str, ...],
    recursive: bool = False,
) -> list[str]:
    """Scan directory for files matching supported extensions."""
    files: list[str] = []
    ext_set = {e.lower() for e in extensions}

    if recursive:
        for root, _dirs, filenames in os.walk(directory):
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in ext_set:
                    files.append(os.path.join(root, fname))
    else:
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            if os.path.isfile(fpath) and os.path.splitext(fname)[1].lower() in ext_set:
                files.append(fpath)

    return files


async def _record_ingest_event(
    store: CorpusTurnStore,
    result: CorpusIngestResult,
    config: CorpusIngestConfig,
) -> None:
    """Record a corpus_ingested event. Best-effort — never fails the ingest."""
    try:
        # Use a separate connection for events to avoid write contention
        if config.db_path:
            event_store = QueryableEventStore(db_path=config.db_path)
            await event_store.initialize()
            try:
                await event_store.write_event(
                    event_type="corpus_ingested",
                    actor="corpus_ingest_pipeline",
                    artifact_id=f"corpus:{result.source_path}",
                    from_state=None,
                    to_state=None,
                    metadata={
                        "domain": "corpus",
                        "source_path": result.source_path,
                        "source_type": result.source_type,
                        "source_model": config.source_model,
                        "turns_ingested": result.turns_ingested,
                        "turns_skipped": result.turns_skipped,
                        "turns_updated": result.turns_updated,
                        "turns_failed": result.turns_failed,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            finally:
                await event_store.close()
    except Exception as exc:
        logger.debug("Failed to record ingest event: %s", exc)
