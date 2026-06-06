"""Cohort chat API route — parallel multi-model dispatch.

POST /api/v1/chat/cohort dispatches the same query to multiple LLM models
in parallel using asyncio.gather(). If augmented=true, the retrieval pipeline
runs ONCE and the shared context is prepended to each model's messages.

Per AIP_UNIFIED_CHAT_SPEC §Cohort:
  - Server-side parallel dispatch; client gets all results at once.
  - If one model fails, include {model_id, error, response_text: null} —
    never abort the whole cohort because one model failed.
  - The synthesis slot model does NOT auto-participate; only models
    explicitly selected in the dropdown are dispatched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aip.adapter.api.dependencies import get_container

router = APIRouter()
logger = logging.getLogger(__name__)

_STATE_DB = "db/state.db"


class CohortRequest(BaseModel):
    """Request body for POST /api/v1/chat/cohort."""

    query: str
    model_ids: list[str]
    augmented: bool = False
    system_prompt_modifier: str | None = None


class CohortResponseItem(BaseModel):
    """Single model response within a cohort result."""

    model_id: str
    display_name: str = ""
    response_text: str | None = None
    error: str | None = None
    elapsed_ms: int = 0


class CohortResponse(BaseModel):
    """Response for POST /api/v1/chat/cohort."""

    responses: list[CohortResponseItem]
    session_id: str = ""
    turn_index: int = 0


async def _get_openrouter_api_key() -> str | None:
    """Resolve the OpenRouter API key from env or state.db.

    Checks AIP_OPENAI_API_KEY env var first (backward compat),
    then falls back to the first custom_api_key in enabled_models
    where provider='openrouter' and is_custom=0.
    """
    key = os.environ.get("AIP_OPENAI_API_KEY")
    if key:
        return key
    # Fallback: check if any enabled model has a custom API key stored
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        try:
            cursor = await conn.execute(
                "SELECT custom_api_key FROM enabled_models "
                "WHERE custom_api_key IS NOT NULL AND custom_api_key != '' "
                "LIMIT 1"
            )
            row = await cursor.fetchone()
            if row and row["custom_api_key"]:
                return row["custom_api_key"]
        finally:
            await conn.close()
    except Exception:
        pass
    return None


async def _call_model(
    model_id: str,
    messages: list[dict[str, str]],
    api_key: str,
) -> dict[str, Any]:
    """Call a single model via OpenRouter API.

    Returns {model_id, response_text, error, elapsed_ms, display_name}.
    Never raises — errors are captured in the 'error' field.
    """
    start = time.monotonic()
    display_name = model_id.split("/")[-1] if "/" in model_id else model_id
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:8080",
                    "X-Title": "AIP_Brain",
                },
                json={
                    "model": model_id,
                    "messages": messages,
                    "max_tokens": 4096,
                },
            )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            error_detail = resp.text[:500]
            logger.error(
                "cohort_model_error: model=%s status=%d detail=%s",
                model_id,
                resp.status_code,
                error_detail[:200],
            )
            return {
                "model_id": model_id,
                "display_name": display_name,
                "response_text": None,
                "error": f"API error ({resp.status_code}): {error_detail}",
                "elapsed_ms": elapsed_ms,
            }

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        model_used = data.get("model", model_id)

        logger.info(
            "cohort_model_success: model=%s elapsed=%dms",
            model_used,
            elapsed_ms,
        )

        return {
            "model_id": model_id,
            "display_name": display_name,
            "response_text": content,
            "error": None,
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error("cohort_model_failed: model=%s error=%s", model_id, exc)
        return {
            "model_id": model_id,
            "display_name": display_name,
            "response_text": None,
            "error": str(exc),
            "elapsed_ms": elapsed_ms,
        }


async def _get_display_names(model_ids: list[str]) -> dict[str, str]:
    """Fetch display_name for each model_id from enabled_models table."""
    names: dict[str, str] = {}
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            placeholders = ",".join("?" for _ in model_ids)
            cursor = await conn.execute(
                f"SELECT model_id, display_name FROM enabled_models "
                f"WHERE model_id IN ({placeholders})",
                model_ids,
            )
            rows = await cursor.fetchall()
            for row in rows:
                names[row["model_id"]] = row["display_name"]
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("cohort_display_names_failed: %s", exc)
    return names


@router.post("/chat/cohort")
async def cohort_dispatch(payload: CohortRequest) -> CohortResponse:
    """Dispatch a query to multiple LLM models in parallel.

    If augmented=true, runs the retrieval pipeline ONCE to get shared
    context, then prepends it to each model's messages.

    Returns all model responses at once (no streaming per-model).
    If one model fails, its entry has error + response_text=null;
    the rest succeed independently.
    """
    query = payload.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    if not payload.model_ids:
        raise HTTPException(status_code=400, detail="model_ids must be non-empty")
    if len(payload.model_ids) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 models per cohort")

    # Resolve API key
    api_key = await _get_openrouter_api_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="No OpenRouter API key configured. "
            "Set AIP_OPENAI_API_KEY or configure a model with a custom API key.",
        )

    # Build base messages
    system_msg = "You are a helpful AI assistant."
    if payload.system_prompt_modifier:
        system_msg = f"{payload.system_prompt_modifier}\n\n{system_msg}"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_msg},
    ]

    # If augmented, retrieve context ONCE and share across all models
    augmented_context: str = ""
    if payload.augmented:
        try:
            container = get_container()
            if container is not None and container.corpus_turn_store is not None:
                hits = await container.corpus_turn_store.search(query=query, limit=5)
                if hits:
                    snippets = []
                    for hit in hits[:5]:
                        text = getattr(hit, "user_text", "") or getattr(hit, "text", "") or ""
                        domain = getattr(hit, "primary_domain", "") or ""
                        if text:
                            snippet = f"[{domain}] {text[:300]}" if domain else text[:300]
                            snippets.append(snippet)
                    if snippets:
                        augmented_context = (
                            "## Retrieved Context\n\n"
                            + "\n\n".join(snippets)
                            + "\n\n---\n\nAnswer the user's question using the above context when relevant."
                        )
        except Exception as exc:
            logger.warning("cohort_augmented_retrieval_failed: %s", exc)

    # Build per-model messages (all share the same base + optional context)
    async def _dispatch_one(model_id: str) -> dict[str, Any]:
        model_messages = list(messages)
        if augmented_context:
            # Insert context as an additional system message before the user message
            model_messages.append({"role": "system", "content": augmented_context})
        model_messages.append({"role": "user", "content": query})
        result = await _call_model(model_id=model_id, messages=model_messages, api_key=api_key)
        return result

    # Parallel dispatch
    results = await asyncio.gather(
        *[_dispatch_one(mid) for mid in payload.model_ids],
        return_exceptions=False,
    )

    # Enrich with display names from DB
    display_names = await _get_display_names(payload.model_ids)
    session_id = str(uuid.uuid4())
    response_items: list[CohortResponseItem] = []
    for i, r in enumerate(results):
        mid = r["model_id"]
        display = display_names.get(mid) or r.get("display_name", mid)
        response_items.append(
            CohortResponseItem(
                model_id=mid,
                display_name=display,
                response_text=r.get("response_text"),
                error=r.get("error"),
                elapsed_ms=r.get("elapsed_ms", 0),
            )
        )

    # Write each model's response as a separate CorpusTurn
    # Per AIP_CORPUS_LIFECYCLE_SPEC §Cohort mode: metadata includes
    # {"cohort": true, "model_id": "...", "cohort_turn_index": N}
    try:
        container = get_container()
        corpus_turn_store = getattr(container, "corpus_turn_store", None)
        if corpus_turn_store is not None:
            from datetime import datetime, timezone

            from aip.foundation.schemas.corpus_turn import CorpusTurn

            now_iso = datetime.now(timezone.utc).isoformat() + "Z"
            for i, item in enumerate(response_items):
                if not item.response_text:
                    continue
                turn = CorpusTurn(
                    turn_id=f"cohort:{session_id}:{i}",
                    conversation_id=session_id,
                    conversation_name=f"cohort-{session_id[:8]}",
                    turn_index=i,
                    source_model=item.model_id,
                    source_account="definer",
                    export_date=now_iso,
                    user_text=query,
                    assistant_text=item.response_text,
                    turn_timestamp=now_iso,
                    thinking_text="",
                    domains=[],
                    primary_domain="",
                    tags=[],
                    importance=0.0,
                    bridges=[],
                    beast_confidence=0.0,
                    tagging_version=0,
                    searchable_text=f"{query} {item.response_text}",
                    word_count=len(item.response_text.split()),
                )
                # Attach cohort metadata
                turn.metadata_json = json.dumps(  # type: ignore[attr-defined]
                    {"cohort": True, "model_id": item.model_id, "cohort_turn_index": i}
                )
                await corpus_turn_store.write_turn(turn)
    except Exception as exc:
        logger.warning("cohort_corpus_write_failed: %s", exc)

    return CohortResponse(
        responses=response_items,
        session_id=session_id,
        turn_index=0,
    )
