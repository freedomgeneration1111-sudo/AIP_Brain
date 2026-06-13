"""Model slot management routes.

Exposes ModelSlotResolver information to API consumers (CLI, GUI, MCP).
The GUI uses GET /models/slots to populate its model dropdown instead of
reading enabled_models.json directly.

Added: PATCH /models/slots/{slot_name}/model — runtime model override.
This allows the GUI to change which model a slot uses without restarting
the server. The override is stored in-process (in-memory runtime overrides
on ModelSlotResolver, NOT in os.environ) so it persists until the next
server restart. API keys are never written to the process environment.

Added: GET /models/text-generation-slots — returns only text-generation
slots (excludes embedding). Used by the Model Council panel to populate
the slot selector. Never exposes secrets.

When the "embedding" slot is patched, the container's embedding_provider
is also recreated so that the new embedding model takes effect immediately
for all downstream consumers (vector store, Beast, knowledge store, etc.).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from aip.adapter.api.dependencies import AipContainer, get_container, require_definer
from aip.logging import get_logger

log = get_logger(__name__)

router = APIRouter()

# Slots that should NOT be used for text generation comparison
_EXCLUDED_TEXT_GEN_SLOTS = {"embedding"}


class ModelOverrideRequest(BaseModel):
    """Request body for PATCH /models/slots/{slot_name}/model."""

    model: str
    api_key: str | None = None


@router.get("/models/api_key_status")
async def api_key_status(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Check whether the backend has a valid API key configured.

    The GUI calls this on startup to determine if it should show the
    API key prompt. This checks the actual resolved configuration
    (env vars + TOML config), not just a local env var.

    Returns per-slot has_key status and a global has_any_key flag.
    """
    model_provider = container.model_provider
    if model_provider is None:
        return {"has_any_key": False, "slots": {}}

    result: dict[str, Any] = {"has_any_key": False, "slots": {}}
    try:
        slot_names = model_provider.list_slots()
    except Exception as exc:
        log.warning("model_list_slots_failed", error=str(exc))
        slot_names = []

    for slot_name in slot_names:
        try:
            resolved = model_provider._resolve_slot_config(slot_name)
            api_key = resolved.get("api_key")
            has_key = api_key is not None and len(str(api_key).strip()) > 0
            result["slots"][slot_name] = {
                "has_key": has_key,
                "provider": resolved.get("provider", "unknown"),
            }
            if has_key:
                result["has_any_key"] = True
        except Exception as exc:
            log.debug("model_slot_config_resolve_failed", slot=slot_name, error=str(exc))
            result["slots"][slot_name] = {"has_key": False, "provider": "unknown"}

    return result


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
    except Exception as exc:
        log.warning("model_list_slots_failed", error=str(exc))
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
        except Exception as exc:
            log.debug("model_slot_config_resolve_failed", slot=slot_name, error=str(exc))
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


@router.get("/models/text-generation-slots")
async def list_text_generation_slots(container: AipContainer = Depends(get_container)):
    """List text-generation model slots only (excludes embedding).

    Returns only slots suitable for text generation, filtering out
    embedding and other non-text-generation slots. Used by the Model
    Council panel to populate the slot selector. Never exposes secrets.

    Each slot entry includes: slot_name, provider, model (display only,
    never the actual model ID with secrets), and whether the slot has
    a real model configured (vs. sentinel placeholder like <slot_name>).
    """
    model_provider = container.model_provider
    if model_provider is None:
        return {
            "slots": [],
            "ci_mode": True,
            "sufficient_for_council": False,
            "error": "model_provider_not_configured",
        }

    all_slots_info = []
    try:
        slot_names = model_provider.list_slots()
    except Exception as exc:
        log.warning("model_list_slots_failed", error=str(exc))
        slot_names = []

    ci_mode = getattr(model_provider, "_ci_mode", True)

    for slot_name in slot_names:
        # Skip embedding and other excluded slots
        if slot_name in _EXCLUDED_TEXT_GEN_SLOTS:
            continue

        try:
            resolved = model_provider._resolve_slot_config(slot_name)
            model_id = resolved.get("model", f"<{slot_name}>")
            all_slots_info.append(
                {
                    "slot_name": slot_name,
                    "provider": resolved.get("provider", "unknown"),
                    "model": model_id,
                    "has_real_model": not (model_id.startswith("<") and model_id.endswith(">")),
                }
            )
        except Exception as exc:
            log.debug("model_slot_config_resolve_failed", slot=slot_name, error=str(exc))
            all_slots_info.append(
                {
                    "slot_name": slot_name,
                    "provider": "unknown",
                    "model": f"<{slot_name}>",
                    "has_real_model": False,
                }
            )

    return {
        "slots": all_slots_info,
        "ci_mode": ci_mode,
        "sufficient_for_council": len(all_slots_info) >= 2,
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
    _auth=Depends(require_definer),
) -> dict[str, Any]:
    """Update the model for a slot at runtime.

    Uses the ModelSlotResolver's in-memory runtime overrides, which have
    the highest priority in ``_resolve_slot_config()``. API keys are stored
    in process memory only — they are NEVER written to ``os.environ``.

    When the "embedding" slot is updated, the container's embedding_provider
    is also recreated so the new model takes effect immediately for vector
    store, Beast, knowledge store, and ingestion — without a restart.

    This change persists in-process until the server restarts.
    """
    model_provider = container.model_provider
    if model_provider is None:
        return {"ok": False, "error": "model_provider_not_configured"}

    # Verify the slot exists
    try:
        slot_names = model_provider.list_slots()
    except Exception as exc:
        log.warning("model_list_slots_failed", error=str(exc))
        slot_names = []

    if slot_name not in slot_names:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Unknown slot: {slot_name}")

    # Set in-memory runtime overrides — highest priority in the resolver.
    # API keys are kept in process memory, never written to os.environ.
    model_provider.set_runtime_override(slot_name, "model", body.model)

    # Optionally update API key (in-memory only)
    if body.api_key:
        model_provider.set_runtime_override(slot_name, "api_key", body.api_key)

    # Verify by resolving
    resolved = model_provider._resolve_slot_config(slot_name)

    # If this is the embedding slot, recreate the embedding provider at runtime
    embedding_updated = False
    if slot_name == "embedding":
        embedding_updated = _recreate_embedding_provider(container)

    return {
        "ok": True,
        "slot_name": slot_name,
        "model": resolved.get("model"),
        "provider": resolved.get("provider"),
        "base_url": resolved.get("base_url"),
        "embedding_provider_updated": embedding_updated,
    }


def _recreate_embedding_provider(container: AipContainer) -> bool:
    """Recreate the container's embedding_provider from the current slot config.

    Called when the embedding slot model is changed at runtime. This ensures
    the new embedding model takes effect immediately without a restart.

    Returns True if the embedding provider was successfully recreated.
    """
    from aip.adapter.api.app import _create_embedding_provider

    try:
        # Create a new provider from the current config.
        # set_embedding_provider will handle closing the previous one (if any)
        # and updating references on dependents.
        new_provider = _create_embedding_provider(container.config)
        container.set_embedding_provider(new_provider)

        model_name = ""
        if hasattr(new_provider, "model"):
            model_name = new_provider.model
        elif hasattr(new_provider, "__class__"):
            model_name = new_provider.__class__.__name__

        log.info(
            "embedding_provider_recreated",
            new_provider=model_name,
            vector_store_updated=container.vector_store is not None,
            beast_updated=container.beast is not None,
            knowledge_store_updated=container.knowledge_store is not None,
        )
        return True

    except Exception as exc:
        log.error("embedding_provider_recreation_failed", error=str(exc), exc_info=True)
        return False
