"""Model library API routes — browse and manage enabled_models.

Provides endpoints for the unified chat surface's model selector:
  - GET  /models/library          — list all models in enabled_models
  - POST /models/library/fetch    — fetch from OpenRouter + upsert cache
  - PATCH /models/library/{model_id} — toggle enabled flag

Per AIP-G-09: the OpenRouter fetch is the ONLY outbound call, and it is
explicitly user-triggered (never on startup).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

_STATE_DB = "db/state.db"


class ToggleEnabledRequest(BaseModel):
    """Request body for PATCH /models/library/{model_id}."""

    enabled: int  # 0 or 1


@router.get("/models/library")
async def list_model_library() -> dict:
    """List all models in the enabled_models table.

    Returns a list of model dicts with all columns. Models are ordered
    by enabled (enabled first), then by display_name.
    """
    items: list[dict[str, Any]] = []
    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            cursor = await conn.execute(
                """
                SELECT model_id, display_name, provider,
                       cost_input_per_million, cost_output_per_million,
                       context_length, supports_vision, supports_tools,
                       enabled, is_custom, custom_base_url, custom_api_key,
                       last_fetched
                FROM enabled_models
                ORDER BY enabled DESC, display_name ASC
                """
            )
            rows = await cursor.fetchall()
            for row in rows:
                items.append(
                    {
                        "model_id": row["model_id"],
                        "display_name": row["display_name"],
                        "provider": row["provider"],
                        "cost_input_per_million": row["cost_input_per_million"],
                        "cost_output_per_million": row["cost_output_per_million"],
                        "context_length": row["context_length"],
                        "supports_vision": row["supports_vision"],
                        "supports_tools": row["supports_tools"],
                        "enabled": row["enabled"],
                        "is_custom": row["is_custom"],
                        "custom_base_url": row["custom_base_url"],
                        "custom_api_key": row["custom_api_key"],
                        "last_fetched": row["last_fetched"],
                    }
                )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to list model library: %s", exc)
        return {"items": [], "total": 0}

    return {"items": items, "total": len(items)}


@router.post("/models/library/fetch")
async def fetch_model_library() -> dict:
    """Fetch model list from OpenRouter and upsert into enabled_models.

    Per AIP-G-09: this is the ONLY outbound call, user-triggered only.
    Uses INSERT OR IGNORE so existing rows (with DEFINER-set enabled flags)
    are never overwritten. Returns count of new models added.

    Fetches from https://openrouter.ai/api/v1/models which returns a
    JSON object with a 'data' array of model objects.
    """
    try:
        import httpx
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="httpx not installed — cannot fetch from OpenRouter",
        ) from None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:
        logger.error("OpenRouter fetch failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"OpenRouter fetch failed: {exc}",
        ) from exc

    models_data = body.get("data", [])
    if not isinstance(models_data, list):
        raise HTTPException(
            status_code=502,
            detail="Unexpected OpenRouter response format: 'data' is not a list",
        )

    now = datetime.now(timezone.utc).isoformat()
    new_count = 0

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        try:
            for model in models_data:
                if not isinstance(model, dict):
                    continue
                model_id = model.get("id", "")
                if not model_id:
                    continue

                display_name = model.get("name") or model_id.split("/")[-1]
                # Parse pricing (OpenRouter returns strings like "0.00001")
                cost_in = _parse_float(model.get("pricing", {}).get("prompt"))
                cost_out = _parse_float(model.get("pricing", {}).get("completion"))
                context_length = model.get("context_length")
                supports_vision = 1 if model.get("modality") in (
                    "text+image", "multimodal"
                ) else 0
                supports_tools = 1 if model.get("supports_tools") else 0

                cursor = await conn.execute(
                    """
                    INSERT OR IGNORE INTO enabled_models
                        (model_id, display_name, provider,
                         cost_input_per_million, cost_output_per_million,
                         context_length, supports_vision, supports_tools,
                         enabled, is_custom, last_fetched)
                    VALUES (?, ?, 'openrouter', ?, ?, ?, ?, ?, 0, 0, ?)
                    """,
                    (
                        model_id,
                        display_name,
                        cost_in,
                        cost_out,
                        context_length,
                        supports_vision,
                        supports_tools,
                        now,
                    ),
                )
                if cursor.rowcount > 0:
                    new_count += 1

            await conn.commit()
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to upsert model library: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Database upsert failed: {exc}",
        ) from exc

    return {
        "fetched": len(models_data),
        "new_models_added": new_count,
        "last_fetched": now,
    }


@router.patch("/models/library/{model_id}")
async def toggle_model_enabled(
    model_id: str,
    body: ToggleEnabledRequest,
) -> dict:
    """Toggle the enabled flag for a model in the library.

    Body: {"enabled": 0} or {"enabled": 1}

    Returns the updated model row. Returns 404 if model_id not found.
    """
    if body.enabled not in (0, 1):
        raise HTTPException(
            status_code=400,
            detail="enabled must be 0 or 1",
        )

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            # Check model exists
            cursor = await conn.execute(
                "SELECT model_id FROM enabled_models WHERE model_id = ?",
                (model_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Model not found: {model_id}",
                )

            # Update enabled flag
            await conn.execute(
                "UPDATE enabled_models SET enabled = ? WHERE model_id = ?",
                (body.enabled, model_id),
            )
            await conn.commit()

            # Return updated row
            cursor = await conn.execute(
                """
                SELECT model_id, display_name, provider, enabled
                FROM enabled_models WHERE model_id = ?
                """,
                (model_id,),
            )
            updated = await cursor.fetchone()
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to toggle model enabled: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Database update failed: {exc}",
        ) from exc

    return {
        "model_id": updated["model_id"],
        "display_name": updated["display_name"],
        "provider": updated["provider"],
        "enabled": updated["enabled"],
    }


def _parse_float(value: Any) -> float | None:
    """Parse a value that may be a string or float, returning float or None."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
