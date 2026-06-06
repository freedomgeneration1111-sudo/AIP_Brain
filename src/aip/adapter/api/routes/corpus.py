"""Corpus API route — corpus_turns statistics.

Provides aggregate statistics about the project-agnostic corpus of
ingested conversation turns stored in CorpusTurnStore (corpus_turns
table in state.db).  Distinct from /sources which covers entity store
and knowledge store content.

Phase 4: Knowledge Exploration Features.
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
    """
    result: dict[str, Any] = {
        "total_turns": 0,
        "tagged": 0,
        "untagged": 0,
        "embedded": 0,
        "domains": [],
    }

    cts = getattr(container, "corpus_turn_store", None)
    if cts is None:
        return result

    try:
        result["total_turns"] = await cts.total_turns()
    except Exception as exc:
        logger.warning("CorpusTurnStore total_turns failed: %s", exc)

    try:
        # Tagged = has a non-empty primary_domain (actually domain-assigned turns)
        conn = await cts._get_conn()
        try:
            cursor = await conn.execute(
                'SELECT COUNT(*) as c FROM corpus_turns WHERE primary_domain IS NOT NULL AND primary_domain != ""'
            )
            row = await cursor.fetchone()
            result["tagged"] = int(row["c"]) if row else 0
        finally:
            await conn.close()
            cts._conn = None
    except Exception as exc:
        logger.warning("CorpusTurnStore tagged count failed: %s", exc)

    result["untagged"] = result["total_turns"] - result["tagged"]

    try:
        result["embedded"] = result["total_turns"] - await cts.count_unembedded()
    except Exception as exc:
        logger.warning("CorpusTurnStore embedded count failed: %s", exc)

    try:
        domain_counts = await cts.count_by_domain()
        result["domains"] = [
            {"name": name or "(unclassified)", "count": count}
            for name, count in domain_counts.items()
        ]
    except Exception as exc:
        logger.warning("CorpusTurnStore domain counts failed: %s", exc)

    return result
