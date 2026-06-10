"""Corpus API route — corpus_turns statistics, embedding progress, and audit.

Provides aggregate statistics about the project-agnostic corpus of
ingested conversation turns stored in CorpusTurnStore (corpus_turns
table in state.db).  Distinct from /sources which covers entity store
and knowledge store content.

Sprint 6.1: Added /corpus/embedding-progress endpoint for real-time
embedding pipeline visibility.

Sprint 9: Added /corpus/audit, /corpus/status, /corpus/backfill-queue,
and /corpus/ingest endpoints for corpus reliability and document ingestion.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/corpus/stats")
async def get_corpus_stats(container: AipContainer = Depends(get_container)):
    """Get aggregate statistics about the corpus of ingested turns.

    Returns:
      - total_turns: total number of turns in corpus_turns table
      - tagged: turns with primary_domain IS NOT NULL AND != "" (domain-assigned)
      - untagged: turns without a primary_domain
      - embedded: turns with embedded == 1
      - domains: list of {name, count} for each primary_domain
      - top_turns: top 10 turns by importance score
    """
    result: dict[str, Any] = {
        "total_turns": 0,
        "tagged": 0,
        "untagged": 0,
        "embedded": 0,
        "domains": [],
        "top_turns": [],
    }

    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return result

    try:
        result["total_turns"] = await cts.total_turns()
    except Exception as exc:
        logger.warning("CorpusTurnStore total_turns failed: %s", exc)

    try:
        result["tagged"] = await cts.count_tagged()
    except Exception as exc:
        logger.warning("CorpusTurnStore count_tagged failed: %s", exc)

    result["untagged"] = result["total_turns"] - result["tagged"]

    try:
        result["embedded"] = result["total_turns"] - await cts.count_unembedded()
    except Exception as exc:
        logger.warning("CorpusTurnStore embedded count failed: %s", exc)

    try:
        domain_counts = await cts.count_by_domain()
        result["domains"] = [
            {"name": name, "count": count}
            for name, count in domain_counts.items()
        ]
    except Exception as exc:
        logger.warning("CorpusTurnStore domain counts failed: %s", exc)

    try:
        result["top_turns"] = await cts.top_turns_by_importance(limit=10)
    except Exception as exc:
        logger.warning("CorpusTurnStore top_turns_by_importance failed: %s", exc)

    return result


@router.get("/corpus/embedding-progress")
async def get_embedding_progress(container: AipContainer = Depends(get_container)):
    """Get real-time embedding pipeline progress.

    Returns embedding coverage statistics and current Sexton embedding
    pass state. This endpoint provides visibility into the embedding
    pipeline's progress toward full corpus coverage.

    Returns:
      - total: total number of turns in the corpus
      - embedded: turns with embedded == 1
      - unembedded: turns not yet embedded
      - needs_reembed: turns flagged for re-embedding (model changed)
      - percentage: embedded/total as a percentage
      - last_embed_at: ISO timestamp of most recent embed operation
      - embedding_models: dict of model_name -> count of turns embedded with that model
      - sexton_pass: current Sexton embedding pass status (running, last batch stats)
    """
    result: dict[str, Any] = {
        "total": 0,
        "embedded": 0,
        "unembedded": 0,
        "needs_reembed": 0,
        "percentage": 0.0,
        "last_embed_at": None,
        "embedding_models": {},
        "sexton_pass": None,
    }

    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return result

    # Get progress from CorpusTurnStore
    try:
        progress = await cts.get_embedding_progress()
        result.update(progress)
    except Exception as exc:
        logger.warning("CorpusTurnStore get_embedding_progress failed: %s", exc)
        # Fallback: compute from basic methods
        try:
            total = await cts.total_turns()
            unembedded = await cts.count_unembedded()
            result["total"] = total
            result["embedded"] = total - unembedded
            result["unembedded"] = unembedded
            result["percentage"] = round((total - unembedded) / total * 100, 2) if total > 0 else 0.0
        except Exception as exc2:
            logger.warning("CorpusTurnStore fallback progress failed: %s", exc2)

    # Get Sexton in-progress state
    sexton = getattr(container, "sexton_actor", None)
    if sexton is not None:
        try:
            pass_state = getattr(sexton, "_embedding_pass_state", None)
            if pass_state:
                result["sexton_pass"] = dict(pass_state)
        except Exception as exc:
            logger.warning("Sexton embedding pass state read failed: %s", exc)

    return result


@router.get("/corpus/status")
async def get_corpus_status(container: AipContainer = Depends(get_container)):
    """Quick corpus status summary (Sprint 9).

    Returns key metrics: total turns, embedding coverage, tagging coverage,
    documents, conversations, embed failures, and needs_reembed count.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"total_turns": 0, "embedded": 0, "tagged": 0}

    try:
        return await cts.get_corpus_status()
    except Exception as exc:
        logger.warning("CorpusTurnStore get_corpus_status failed: %s", exc)
        return {"error": str(exc)}


@router.get("/corpus/audit")
async def get_corpus_audit(container: AipContainer = Depends(get_container)):
    """Comprehensive corpus audit (Sprint 9).

    Returns detailed integrity check results: duplicate hashes, missing
    content hashes, embed failures, source model distribution, domain
    distribution, source path distribution, and computed health status.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"total_turns": 0, "healthy": False, "issues": ["CorpusTurnStore not available"]}

    try:
        return await cts.get_corpus_audit()
    except Exception as exc:
        logger.warning("CorpusTurnStore get_corpus_audit failed: %s", exc)
        return {"error": str(exc), "healthy": False}


@router.get("/corpus/backfill-queue")
async def get_backfill_queue(
    limit: int = 100,
    container: AipContainer = Depends(get_container),
):
    """Get the embedding backfill queue (Sprint 9).

    Returns turns that need embedding: failures first, then needs_reembed,
    then first-time unembedded. Ordered by priority.
    """
    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return {"turns": [], "count": 0}

    try:
        turns = await cts.get_backfill_queue(limit=limit)
        return {
            "count": len(turns),
            "turns": [
                {
                    "turn_id": t.turn_id,
                    "source_path": t.source_path,
                    "source_model": t.source_model,
                    "embed_fail_count": t.embed_fail_count,
                    "last_embed_error": t.last_embed_error[:200] if t.last_embed_error else "",
                    "needs_reembed": t.needs_reembed,
                    "embedded": t.embedded,
                    "importance": t.importance,
                }
                for t in turns
            ],
        }
    except Exception as exc:
        logger.warning("CorpusTurnStore get_backfill_queue failed: %s", exc)
        return {"error": str(exc), "turns": [], "count": 0}


@router.post("/corpus/ingest")
async def ingest_to_corpus(
    payload: dict,
    container: AipContainer = Depends(get_container),
):
    """Ingest a file or directory into the corpus (Sprint 9).

    Accepts:
      - path: file or directory path to ingest
      - source_model: optional (auto-detected if not specified)
      - source_account: optional (defaults to "api_ingest")
      - recursive: optional (for directory ingestion)

    Returns CorpusIngestResult with counts of ingested, skipped, updated, failed turns.
    """
    # Chunk 6: Use container-mediated access instead of direct orchestration import
    CorpusIngestConfig = getattr(container, "_corpus_ingest_config_class", None)
    ingest_directory_to_corpus = getattr(container, "_ingest_directory_to_corpus_fn", None)
    ingest_file_to_corpus = getattr(container, "_ingest_file_to_corpus_fn", None)

    if CorpusIngestConfig is None or ingest_file_to_corpus is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Corpus ingestion pipeline not wired")

    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="CorpusTurnStore not wired")

    import os
    path = payload.get("path")
    if not path:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="No path provided")

    if not os.path.exists(path):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")

    config = CorpusIngestConfig(
        source_model=payload.get("source_model", ""),
        source_account=payload.get("source_account", "api_ingest"),
        export_date=payload.get("export_date", ""),
        db_path=getattr(container, "_db_path", ""),
        recursive=payload.get("recursive", False),
    )

    try:
        if os.path.isdir(path):
            results = await ingest_directory_to_corpus(path, cts, config)
            return {
                "type": "directory",
                "files_processed": len([r for r in results if r.source_type != "directory"]),
                "total_ingested": sum(r.turns_ingested for r in results),
                "total_skipped": sum(r.turns_skipped for r in results),
                "total_updated": sum(r.turns_updated for r in results),
                "total_failed": sum(r.turns_failed for r in results),
            }
        else:
            result = await ingest_file_to_corpus(path, cts, config)
            return {
                "type": "file",
                "source_path": result.source_path,
                "source_type": result.source_type,
                "turns_ingested": result.turns_ingested,
                "turns_skipped": result.turns_skipped,
                "turns_updated": result.turns_updated,
                "turns_failed": result.turns_failed,
                "warnings": result.warnings,
                "errors": result.errors,
            }
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")
