"""Ingestion routes — API-driven conversation ingestion.

POST /api/v1/ingest/conversation  — ingest a live chat conversation
POST /api/v1/ingest/file          — ingest a conversation file from disk

These endpoints allow the GUI and other surfaces to trigger the
ingestion pipeline (parse → chunk → FTS5 index → vector upsert)
without going through the CLI.

Phase 3 Auto-Save: the auto-save hook in the chat WebSocket handler
calls the ingestion pipeline directly (not via HTTP) for efficiency.
This route exists for:
  1. Manual ingestion triggers from the GUI
  2. Re-ingestion after configuration changes
  3. External integrations (MCP, scripts)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.foundation.schemas.ingestion import (
    ConversationTurn,
    ImportedConversation,
    IngestionResult,
)

router = APIRouter()


@router.post("/ingest/conversation")
async def ingest_conversation_endpoint(
    payload: dict,
    container: AipContainer = Depends(get_container),
):
    """Ingest a conversation from the API.

    Accepts a conversation payload with:
      - conversation_id: unique ID for this conversation
      - title: optional title
      - turns: list of {role, content, timestamp} dicts
      - domain: optional domain (default: "chat")
      - source_format: "plaintext" (default) or other SourceFormat

    Returns an IngestionResult summary.
    """
    # Validate turns before checking stores (fail fast on bad input)
    raw_turns = payload.get("turns", [])
    if not raw_turns:
        raise HTTPException(status_code=400, detail="No turns provided in payload")

    # Validate required stores
    if container.artifact_store is None:
        raise HTTPException(status_code=503, detail="Artifact store not wired")
    if container.lexical_store is None:
        raise HTTPException(status_code=503, detail="Lexical store not wired")

    # Parse the payload into an ImportedConversation
    conversation_id = payload.get("conversation_id", f"live-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    title = payload.get("title", f"Live Chat {conversation_id}")
    domain = payload.get("domain", "chat")
    source_format = payload.get("source_format", "plaintext")

    turns = [
        ConversationTurn(
            role=t.get("role", "user"),
            content=t.get("content", ""),
            timestamp=t.get("timestamp", ""),
        )
        for t in raw_turns
        if t.get("content")  # skip empty turns
    ]

    if not turns:
        raise HTTPException(status_code=400, detail="No non-empty turns in payload")

    conversation = ImportedConversation(
        conversation_id=conversation_id,
        title=title,
        turns=turns,
        source_format=source_format,
        source_file="api:live-chat",
        imported_at=datetime.now(timezone.utc).isoformat(),
        metadata={"domain": domain, "source": "api", "auto_save": payload.get("auto_save", False)},
    )

    # Run the ingestion pipeline
    try:
        from aip.orchestration.ingestion.pipeline import ingest_conversation

        result: IngestionResult = await ingest_conversation(
            conversation=conversation,
            artifact_store=container.artifact_store,
            lexical_store=container.lexical_store,
            vector_store=container.vector_store,
            embedding_provider=container.embedding_provider,
            event_store=container.event_store,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")

    return {
        "conversation_id": result.conversation_id,
        "artifact_id": result.artifact_id,
        "turn_count": result.turn_count,
        "chunk_count": result.chunk_count,
        "vector_indexed": result.vector_indexed,
        "lexical_indexed": result.lexical_indexed,
        "errors": result.errors,
    }


@router.post("/ingest/file")
async def ingest_file_endpoint(
    payload: dict,
    container: AipContainer = Depends(get_container),
):
    """Ingest a conversation file from disk.

    Accepts:
      - path: file path to ingest
      - domain: optional domain (default: "imported")
      - source_format: optional SourceFormat override

    Returns a list of IngestionResult summaries.
    """
    path = payload.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="No file path provided")

    # Validate required stores
    if container.artifact_store is None:
        raise HTTPException(status_code=503, detail="Artifact store not wired")
    if container.lexical_store is None:
        raise HTTPException(status_code=503, detail="Lexical store not wired")

    domain = payload.get("domain", "imported")
    source_format = payload.get("source_format")

    try:
        from aip.orchestration.ingestion.pipeline import ingest_file

        results = await ingest_file(
            path=path,
            artifact_store=container.artifact_store,
            lexical_store=container.lexical_store,
            vector_store=container.vector_store,
            embedding_provider=container.embedding_provider,
            event_store=container.event_store,
            domain=domain,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")

    return {
        "results": [
            {
                "conversation_id": r.conversation_id,
                "artifact_id": r.artifact_id,
                "turn_count": r.turn_count,
                "chunk_count": r.chunk_count,
                "vector_indexed": r.vector_indexed,
                "lexical_indexed": r.lexical_indexed,
                "errors": r.errors,
            }
            for r in results
        ],
    }


async def auto_save_chat_turn(
    session_id: str,
    user_message: str,
    assistant_response: str,
    container: AipContainer,
    domain: str = "chat",
    turn_index: int = 0,
    model_used: str = "",
    augmented: bool = False,
    source_turn_ids: list[str] | None = None,
) -> IngestionResult | None:
    """Auto-save a completed chat turn through the ingestion pipeline.

    This is the core auto-save hook called from the chat WebSocket handler
    after a successful model response. It:
    1. Builds an ImportedConversation from the turn pair
    2. Calls ingest_conversation() to chunk, index, and embed
    3. Writes a CorpusTurn to corpus_turns (so Sexton can tag/embed it)
    4. Updates the session's ingestion status
    5. Returns the IngestionResult (or None on failure)

    When augmented=True, the turn is marked as an augmented chat response
    in metadata_json so Vigil can evaluate citation quality. source_turn_ids
    lists the corpus turn IDs that were retrieved as context.
    """
    from aip.adapter.api.routes.sessions import update_ingestion_status

    # Update status to ingesting
    update_ingestion_status(session_id, "ingesting", container=container)

    _legacy_result: IngestionResult | None = None

    try:
        # --- Legacy ingestion path (artifacts + lexical + vector) ---
        # Only runs if both artifact_store and lexical_store are available.
        if container.artifact_store is not None and container.lexical_store is not None:
            from aip.orchestration.ingestion.pipeline import ingest_conversation

            conversation = ImportedConversation(
                conversation_id=session_id,
                title=f"Chat Session {session_id}",
                turns=[
                    ConversationTurn(
                        role="user",
                        content=user_message,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ),
                    ConversationTurn(
                        role="assistant",
                        content=assistant_response,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ),
                ],
                source_format="plaintext",
                source_file=f"api:auto-save:{session_id}",
                imported_at=datetime.now(timezone.utc).isoformat(),
                metadata={
                    "domain": domain,
                    "source": "auto-save",
                    "session_id": session_id,
                },
            )

            _legacy_result = await ingest_conversation(
                conversation=conversation,
                artifact_store=container.artifact_store,
                lexical_store=container.lexical_store,
                vector_store=container.vector_store,
                embedding_provider=container.embedding_provider,
                event_store=container.event_store,
            )

        # --- Corpus turns write (so Sexton can tag/embed/wiki/graph it) ---
        # This fixes the two-pipeline problem: previously, auto-save only wrote
        # to the artifacts table via ingest_conversation(), so Sexton's
        # get_untagged_turns() never saw live chat turns.
        if container.corpus_turn_store is not None:
            try:
                import json as _json
                from aip.foundation.schemas.corpus_turn import CorpusTurn, make_turn_id

                now = datetime.now(timezone.utc)
                turn_id = make_turn_id(session_id, turn_index)

                # Build metadata_json for Vigil: augmented flag + source_turn_ids
                metadata: dict = {}
                if augmented:
                    metadata["augmented"] = True
                    if source_turn_ids:
                        metadata["source_turn_ids"] = source_turn_ids
                    # source_model="aip_chat" identifies augmented turns for Vigil queries

                corpus_turn = CorpusTurn(
                    turn_id=turn_id,
                    conversation_id=session_id,
                    conversation_name=f"Chat Session {session_id[:8]}",
                    turn_index=turn_index,
                    source_model="aip_chat" if augmented else (model_used or "chat"),
                    source_account="auto-save",
                    export_date=now.strftime("%Y-%m-%d"),
                    user_text=user_message,
                    assistant_text=assistant_response,
                    turn_timestamp=now.isoformat(),
                    metadata_json=_json.dumps(metadata) if metadata else "{}",
                )

                await container.corpus_turn_store.write_turn(corpus_turn)
            except Exception as corpus_exc:
                # Non-critical — Sexton tagging is best-effort
                import logging
                logging.getLogger(__name__).warning(
                    "auto_save_corpus_turn_failed",
                    session_id=session_id,
                    turn_index=turn_index,
                    error=str(corpus_exc),
                )

        # Update session with ingestion results
        from aip.adapter.api.routes.sessions import get_session_meta

        if _legacy_result is not None:
            meta = get_session_meta(session_id)
            current_chunks = (meta or {}).get("chunks_indexed", 0)
            new_total = current_chunks + _legacy_result.chunk_count
            update_ingestion_status(session_id, "idle", chunks_indexed=new_total, container=container)
        else:
            update_ingestion_status(session_id, "idle", container=container)

        return _legacy_result

    except Exception as exc:
        # Log but don't propagate — auto-save is non-critical
        import logging
        logging.getLogger(__name__).warning(
            "auto_save_failed",
            session_id=session_id,
            error=str(exc),
        )
        update_ingestion_status(session_id, "error", container=container)
        return None
