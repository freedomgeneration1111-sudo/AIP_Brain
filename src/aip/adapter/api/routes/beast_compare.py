"""Beast comparison API route — cohort response comparison via Beast actor.

POST /api/v1/beast/compare accepts a query and a list of model responses,
calls Beast._run_cohort_comparison() to generate a synthesis comparison,
and returns the comparison text.

Per AIP_UNIFIED_CHAT_SPEC §Beast Comparison:
  - Soul.md prepended to comparison system prompt
  - Per AIP-G-01: comparison is GENERATED, not auto-approved
  - Non-blocking: client triggers this after cohort cards are rendered
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from aip.adapter.api.dependencies import get_container

router = APIRouter()
logger = logging.getLogger(__name__)


class CompareRequest(BaseModel):
    """Request body for POST /api/v1/beast/compare."""

    query: str
    responses: list[dict[str, str]]
    session_id: str = ""
    turn_index: int = 0


class CompareResponse(BaseModel):
    """Response for POST /api/v1/beast/compare."""

    comparison_text: str
    comparison_id: str = ""


@router.post("/beast/compare")
async def beast_compare(request: Request, payload: CompareRequest) -> CompareResponse:
    """Generate a Beast comparison across multiple model responses.

    Calls Beast._run_cohort_comparison() which:
      - Prepends soul.md to the comparison system prompt
      - Formats all model responses for the Beast LLM
      - Returns comparison_text

    Per AIP-G-02: if Beast is unavailable, return an error — never fake.
    """
    if not payload.query:
        raise HTTPException(status_code=400, detail="query is required")
    if not payload.responses:
        raise HTTPException(status_code=400, detail="responses must be non-empty")

    container = get_container(request)
    beast = getattr(container, "beast", None)
    if beast is None:
        raise HTTPException(
            status_code=503,
            detail="Beast actor not available — comparison requires Beast to be initialized.",
        )

    comparison_id = str(uuid.uuid4())

    try:
        comparison_text = await beast._run_cohort_comparison(
            query=payload.query,
            responses=payload.responses,
        )
    except Exception as exc:
        logger.error("beast_compare_failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Beast comparison failed: {exc}",
        ) from exc

    # Write to beast_comparisons table (if it exists — Commit 4 wires this)
    try:
        conn = await aiosqlite.connect("db/state.db")
        try:
            # Check if beast_comparisons table exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='beast_comparisons'"
            )
            row = await cursor.fetchone()
            if row:
                now = datetime.now(timezone.utc).isoformat()
                await conn.execute(
                    """
                    INSERT INTO beast_comparisons
                        (comparison_id, session_id, turn_index, query,
                         model_responses, comparison_text, mode, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comparison_id,
                        payload.session_id,
                        payload.turn_index,
                        payload.query,
                        json.dumps(payload.responses),
                        comparison_text,
                        "cohort",
                        now,
                    ),
                )
                await conn.commit()
        finally:
            await conn.close()
    except Exception as exc:
        # Log but don't fail — the table may not exist yet (Commit 4)
        logger.debug("beast_comparisons_write_skipped: %s", exc)

    return CompareResponse(
        comparison_text=comparison_text,
        comparison_id=comparison_id,
    )


@router.get("/beast/comparison/{session_id}")
async def get_comparisons_for_session(session_id: str) -> dict:
    """Return all Beast comparisons for a given session.

    Returns a list of comparison dicts ordered by created_at DESC.
    """
    try:
        conn = await aiosqlite.connect("db/state.db")
        conn.row_factory = aiosqlite.Row
        try:
            # Check if beast_comparisons table exists
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='beast_comparisons'"
            )
            row = await cursor.fetchone()
            if not row:
                return {"comparisons": [], "total": 0}

            cursor = await conn.execute(
                """
                SELECT comparison_id, session_id, turn_index, query,
                       comparison_text, mode, created_at
                FROM beast_comparisons
                WHERE session_id = ?
                ORDER BY created_at DESC
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
            comparisons = []
            for row in rows:
                comparisons.append(
                    {
                        "comparison_id": row["comparison_id"],
                        "session_id": row["session_id"],
                        "turn_index": row["turn_index"],
                        "query": row["query"],
                        "comparison_text": row["comparison_text"],
                        "mode": row["mode"],
                        "created_at": row["created_at"],
                    }
                )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("get_comparisons_failed: %s", exc)
        return {"comparisons": [], "total": 0, "error": str(exc)}

    return {"comparisons": comparisons, "total": len(comparisons)}
