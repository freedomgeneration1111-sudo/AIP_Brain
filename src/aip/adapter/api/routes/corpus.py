"""Corpus API route — corpus_turns statistics and embedding progress.

Provides aggregate statistics about the project-agnostic corpus of
ingested conversation turns stored in CorpusTurnStore (corpus_turns
table in state.db).  Distinct from /sources which covers entity store
and knowledge store content.

Sprint 6.1: Added /corpus/embedding-progress endpoint for real-time
embedding pipeline visibility.
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
