"""API plugin management routes (CHUNK-10.2).

Adapter-layer. Mounted under the main app.
Enable/disable require DEFINER-level auth (per §1.7).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import get_container, require_definer

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("")
async def list_plugins(container=Depends(get_container)):
    pm = getattr(container, "plugin_manager", None)
    if pm is None:
        return {"plugins": []}
    return {"plugins": pm.list_plugins()}


@router.post("/enable")
async def enable_plugin(slot_name: str, config_path: str, container=Depends(get_container), _=Depends(require_definer)):
    pm = getattr(container, "plugin_manager", None)
    loader = getattr(container, "plugin_loader", None)
    if pm is None or loader is None:
        raise HTTPException(503, "Plugin infrastructure not available")
    provider = loader.load_plugin(config_path)
    if provider:
        pm.register_plugin(provider)
        return {"status": "enabled", "slot": slot_name}
    raise HTTPException(400, "Failed to load plugin")


@router.post("/disable")
async def disable_plugin(slot_name: str, provider_name: str, container=Depends(get_container), _=Depends(require_definer)):
    pm = getattr(container, "plugin_manager", None)
    if pm is None:
        raise HTTPException(503, "PluginManager not available")
    pm.unregister_plugin(slot_name, provider_name)
    return {"status": "disabled"}


@router.get("/health")
async def plugin_health(container=Depends(get_container)):
    pm = getattr(container, "plugin_manager", None)
    if pm is None:
        return {"health": {}}
    import asyncio
    health = asyncio.get_event_loop().run_until_complete(pm.health_check_all())
    return {"health": health}
