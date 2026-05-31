"""Ingestion pipeline — parse, persist, chunk, index.

Orchestrates the full ingestion flow:
1. Parse source file into ImportedConversation(s)
2. Store raw conversation as an artifact (provenance in metadata)
3. Chunk conversation content
4. Index chunks into LexicalStore (FTS5)
5. Index chunks into VectorStore (when EmbeddingProvider available)
6. Record ingestion event in EventStore
7. Return IngestionResult

Uses existing AIP primitives — no parallel storage system.
Imported conversations enter as APPROVED artifacts (they are
already human-authored content) and are immediately indexed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from aip.foundation.protocols import (
    ArtifactStore,
    EmbeddingProvider,
    EventStore,
    LexicalStore,
    VectorStore,
)
from aip.foundation.schemas.ingestion import (
    ImportedConversation,
    IngestionResult,
    SourceFormat,
)

from .chunker import chunk_conversation
from .parsers import detect_format
from .parsers.chatgpt import parse_chatgpt_export
from .parsers.markdown import parse_markdown_transcript
from .parsers.plaintext import parse_plaintext_transcript

logger = logging.getLogger(__name__)


async def ingest_file(
    path: str,
    artifact_store: ArtifactStore,
    lexical_store: LexicalStore,
    vector_store: VectorStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    event_store: EventStore | None = None,
    source_format: SourceFormat | None = None,
    domain: str = "imported",
) -> list[IngestionResult]:
    """Ingest a conversation file from disk.

    Auto-detects the format when ``source_format`` is not specified.
    A single file may yield multiple conversations (e.g. ChatGPT
    exports contain an array). Returns one IngestionResult per
    conversation.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, encoding="utf-8") as f:
        content = f.read()

    if source_format is None:
        source_format = detect_format(path, content)

    conversations = _parse_content(content, source_format, source_file=path)

    results: list[IngestionResult] = []
    for conv in conversations:
        conv.metadata["domain"] = domain
        result = await ingest_conversation(
            conversation=conv,
            artifact_store=artifact_store,
            lexical_store=lexical_store,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
            event_store=event_store,
        )
        results.append(result)

    return results


async def ingest_conversation(
    conversation: ImportedConversation,
    artifact_store: ArtifactStore,
    lexical_store: LexicalStore,
    vector_store: VectorStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    event_store: EventStore | None = None,
) -> IngestionResult:
    """Ingest a single parsed conversation into AIP stores.

    1. Persist raw conversation as an artifact with provenance metadata
    2. Chunk the conversation content
    3. Index chunks into LexicalStore (FTS5)
    4. Index chunks into VectorStore (if embedding provider available)
    5. Record ingestion event
    """
    domain = conversation.metadata.get("domain", "imported")
    artifact_id = f"conv:{conversation.conversation_id}"
    vector_indexed = False
    lexical_indexed = False
    errors: list[str] = []

    # Step 1: Persist raw conversation as artifact with provenance metadata
    artifact_metadata = {
        "artifact_type": "conversation",
        "source_format": conversation.source_format,
        "source_file": conversation.source_file,
        "imported_at": conversation.imported_at or datetime.now(timezone.utc).isoformat(),
        "turn_count": len(conversation.turns),
        "title": conversation.title,
        "domain": domain,
        "conversation_id": conversation.conversation_id,
        **conversation.metadata,
    }

    # Store the full conversation as JSON
    conversation_json = json.dumps(
        {
            "conversation_id": conversation.conversation_id,
            "title": conversation.title,
            "turns": [
                {"role": t.role, "content": t.content, "timestamp": t.timestamp}
                for t in conversation.turns
            ],
            "source_format": conversation.source_format,
            "source_file": conversation.source_file,
            "imported_at": conversation.imported_at,
            "metadata": conversation.metadata,
        },
        indent=2,
        ensure_ascii=False,
    )

    try:
        await artifact_store.write(artifact_id, conversation_json, artifact_metadata)
        logger.info("Stored conversation artifact '%s' (%d turns)", artifact_id, len(conversation.turns))
    except Exception as exc:
        errors.append(f"Artifact store write failed: {exc}")
        logger.warning("Failed to write artifact '%s': %s", artifact_id, exc)

    # Step 2: Chunk the conversation
    chunks = chunk_conversation(conversation)
    logger.info("Chunked conversation '%s' into %d chunks", conversation.conversation_id, len(chunks))

    # Step 3: Index chunks into LexicalStore (FTS5)
    for chunk_id, chunk_text in chunks:
        try:
            await lexical_store.index_document(
                doc_id=chunk_id,
                content=chunk_text,
                domain=domain,
                metadata={
                    "type": "conversation_chunk",
                    "conversation_id": conversation.conversation_id,
                    "source_format": conversation.source_format,
                    "domain": domain,
                },
            )
        except Exception as exc:
            errors.append(f"Lexical index failed for chunk {chunk_id}: {exc}")
            logger.debug("Lexical index failed for %s: %s", chunk_id, exc)

    # If at least one chunk indexed, mark as lexical-indexed
    if chunks and not any("Lexical index failed" in e for e in errors):
        lexical_indexed = True
    elif chunks:
        # Some chunks may have succeeded even if some failed
        lexical_indexed = len(errors) < len(chunks)

    # Step 4: Index chunks into VectorStore (when available)
    if vector_store is not None and embedding_provider is not None and chunks:
        indexed_count = 0
        for chunk_id, chunk_text in chunks:
            try:
                embedding = await embedding_provider.embed(chunk_text[:2000])
                if embedding and len(embedding) > 0:
                    await vector_store.upsert(
                        id=chunk_id,
                        embedding=embedding,
                        content=chunk_text[:2000],
                        metadata={
                            "type": "conversation_chunk",
                            "conversation_id": conversation.conversation_id,
                            "source_format": conversation.source_format,
                            "domain": domain,
                        },
                        domain=domain,
                    )
                    indexed_count += 1
            except Exception as exc:
                errors.append(f"Vector index failed for chunk {chunk_id}: {exc}")
                logger.debug("Vector index failed for %s: %s", chunk_id, exc)

        vector_indexed = indexed_count > 0
        if vector_indexed:
            logger.info(
                "Indexed %d/%d chunks into vector store for '%s'",
                indexed_count, len(chunks), conversation.conversation_id,
            )
        else:
            logger.info(
                "No vector chunks for '%s' (embedding may be unavailable)",
                conversation.conversation_id,
            )

    # Step 5: Record ingestion event
    if event_store is not None:
        try:
            await event_store.write_event(
                event_type="conversation_ingested",
                actor="ingestion_pipeline",
                artifact_id=artifact_id,
                from_state=None,
                to_state="APPROVED",
                metadata={
                    "conversation_id": conversation.conversation_id,
                    "source_format": conversation.source_format,
                    "source_file": conversation.source_file,
                    "turn_count": len(conversation.turns),
                    "chunk_count": len(chunks),
                    "vector_indexed": vector_indexed,
                    "lexical_indexed": lexical_indexed,
                    "domain": domain,
                },
            )
        except Exception as exc:
            errors.append(f"Event store write failed: {exc}")
            logger.debug("Event store write failed: %s", exc)

    return IngestionResult(
        conversation_id=conversation.conversation_id,
        artifact_id=artifact_id,
        turn_count=len(conversation.turns),
        chunk_count=len(chunks),
        vector_indexed=vector_indexed,
        lexical_indexed=lexical_indexed,
        source_format=conversation.source_format,
        source_file=conversation.source_file,
        errors=errors,
    )


def _parse_content(content: str, source_format: SourceFormat, source_file: str) -> list[ImportedConversation]:
    """Parse content string into ImportedConversation(s)."""
    if source_format == "chatgpt_json":
        return parse_chatgpt_export(content, source_file=source_file)
    elif source_format == "markdown":
        return [parse_markdown_transcript(content, source_file=source_file)]
    elif source_format == "plaintext":
        return [parse_plaintext_transcript(content, source_file=source_file)]
    else:
        raise ValueError(f"Unknown source format: {source_format}")


class IngestionStores:
    """Container for the stores needed by the ingestion pipeline.

    Created by ``create_ingestion_stores()`` which encapsulates
    adapter-layer imports so that callers (like the CLI) do not
    need to import concrete adapter implementations directly.
    """

    def __init__(self, artifact_store, lexical_store, vector_store, event_store) -> None:
        self.artifact_store = artifact_store
        self.lexical_store = lexical_store
        self.vector_store = vector_store
        self.event_store = event_store

    async def close(self) -> None:
        for store in (self.artifact_store, self.lexical_store, self.event_store):
            if hasattr(store, "close"):
                await store.close()


async def create_ingestion_stores(db_path: str) -> IngestionStores:
    """Factory: create and initialize all stores needed for ingestion.

    Encapsulates adapter-layer imports so that CLI and other
    orchestration callers do not import concrete adapters directly.
    """
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore
    from aip.adapter.event_store_queryable import QueryableEventStore
    from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore
    from aip.adapter.vector._in_memory import InMemoryVectorStore

    artifact_store = VersionedArtifactStore(db_path)
    await artifact_store.initialize()

    lexical_db = os.path.join(os.path.dirname(db_path), "lexical.db")
    lexical_store = SqliteFts5LexicalStore(lexical_db)
    await lexical_store.initialize()

    vector_store = InMemoryVectorStore()

    event_store = QueryableEventStore(db_path)
    await event_store.initialize()

    return IngestionStores(artifact_store, lexical_store, vector_store, event_store)
