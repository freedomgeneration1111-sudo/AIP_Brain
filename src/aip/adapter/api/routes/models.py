"""Model slot management routes.

Exposes ModelSlotResolver information to API consumers (CLI, GUI, MCP).
The GUI uses GET /models/slots to populate its model dropdown instead of
reading enabled_models.json directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()


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
            # Slot exists but couldn't be resolved — include minimal info
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
