"""Model slot management routes.

Exposes ModelSlotResolver information to API consumers (CLI, GUI, MCP).
The GUI uses GET /models/slots to populate its model dropdown instead of
reading enabled_models.json directly.

Added: PATCH /models/slots/{slot_name}/model — runtime model override.
This allows the GUI to change which model a slot uses without restarting
the server. The override is stored in-process (env var) so it persists
until the next server restart.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()


class ModelOverrideRequest(BaseModel):
    """Request body for PATCH /models/slots/{slot_name}/model."""
    model: str
    api_key: str | None = None


@router.get("/models/slots")
async def list_model_slots(container: AipContainer = Depends(get_container)):
    """List all configured model slots with resolved provider and model info.

    Returns slot name, provider, model, and whether the slot has a real
    provider configured (vs. CI fixture mode). This is the primary endpoint
    the GUI uses to populate the model/role dropdowns.
    """
    model_provider = container.model_provider
    if model_provider is None:
        return {"slots": [], "ci_mode": True, "error": "model_provider_not_configured"}

    slots_info = []
    try:
        slot_names = model_provider.list_slots()
    except Exception:
        slot_names = []

    ci_mode = getattr(model_provider, "_ci_mode", True)

    for slot_name in slot_names:
        try:
            resolved = model_provider._resolve_slot_config(slot_name)
            slots_info.append(
                {
                    "slot_name": slot_name,
                    "provider": resolved.get("provider", "unknown"),
                    "model": resolved.get("model", f"<{slot_name}>"),
                    "base_url": resolved.get("base_url"),
                    "has_fallback": resolved.get("fallback_provider") is not None,
                    "fallback_provider": resolved.get("fallback_provider"),
                    "fallback_model": resolved.get("fallback_model"),
                }
            )
        except Exception:
            slots_info.append(
                {
                    "slot_name": slot_name,
                    "provider": "unknown",
                    "model": f"<{slot_name}>",
                    "base_url": None,
                    "has_fallback": False,
                    "fallback_provider": None,
                    "fallback_model": None,
                }
            )

    return {
        "slots": slots_info,
        "ci_mode": ci_mode,
    }


@router.get("/models/slots/{slot_name}")
async def get_model_slot(slot_name: str, container: AipContainer = Depends(get_container)):
    """Get detailed info for a single model slot."""
    model_provider = container.model_provider
    if model_provider is None:
        return {"error": "model_provider_not_configured"}

    try:
        resolved = model_provider._resolve_slot_config(slot_name)
        return {
            "slot_name": slot_name,
            "provider": resolved.get("provider", "unknown"),
            "model": resolved.get("model", f"<{slot_name}>"),
            "base_url": resolved.get("base_url"),
            "has_fallback": resolved.get("fallback_provider") is not None,
            "fallback_provider": resolved.get("fallback_provider"),
            "fallback_model": resolved.get("fallback_model"),
        }
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/models/slots/{slot_name}/model")
async def update_slot_model(
    slot_name: str,
    body: ModelOverrideRequest,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Update the model for a slot at runtime.

    Sets the AIP_<SLOT>_MODEL environment variable, which has the highest
    priority in ModelSlotResolver._resolve_slot_config(). Also optionally
    updates the API key via AIP_<SLOT>_API_KEY.

    This change persists in-process until the server restarts.
    """
    model_provider = container.model_provider
    if model_provider is None:
        return {"ok": False, "error": "model_provider_not_configured"}

    # Verify the slot exists
    try:
        slot_names = model_provider.list_slots()
    except Exception:
        slot_names = []

    if slot_name not in slot_names:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown slot: {slot_name}")

    # Set environment variables — these have highest priority in the resolver
    env_model_key = f"AIP_{slot_name.upper()}_MODEL"
    os.environ[env_model_key] = body.model

    # Optionally update API key
    if body.api_key:
        env_api_key = f"AIP_{slot_name.upper()}_API_KEY"
        os.environ[env_api_key] = body.api_key

    # Verify by resolving
    resolved = model_provider._resolve_slot_config(slot_name)
    return {
        "ok": True,
        "slot_name": slot_name,
        "model": resolved.get("model"),
        "provider": resolved.get("provider"),
        "base_url": resolved.get("base_url"),
    }
