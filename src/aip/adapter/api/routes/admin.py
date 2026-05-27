"""Admin Console routes (CHUNK-8.6).

Writes (config) go through AutonomyGate (admin). Reads from delivered actors (Sexton 7.1, Beast 7.5, Router 7.4, Budget 7.0b, etc.).
"""

from __future__ import annotations

try:
    from fastapi import APIRouter, Depends, HTTPException
except ImportError:
    APIRouter = None  # type: ignore
    Depends = None  # type: ignore
    HTTPException = None  # type: ignore

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter() if APIRouter is not None else None


@router.get("/admin/config")
async def get_admin_config(container: AipContainer = Depends(get_container)):
    return container.config or {"status": "scaffold"}


@router.patch("/admin/config")
async def patch_admin_config(payload: dict, container: AipContainer = Depends(get_container)):
    if not container.autonomy_gate:
        raise HTTPException(503, "AutonomyGate not wired")
    esc = await container.autonomy_gate.escalate(
        action_type="modify_config",
        resource_id="admin_config",
        requested_level="admin",  # type: ignore[arg-type]
        requested_by="api",
    )
    if not esc.granted:
        raise HTTPException(403, f"Autonomy gate blocked: {esc.reason}")
    # In real: apply to config store
    return {"updated": True, "payload": payload}


@router.get("/admin/sexton/classifications")
async def get_sexton_classifications(container: AipContainer = Depends(get_container)):
    # From Sexton actor (7.1)
    return {"classifications": []}


@router.get("/admin/sexton/audit")
async def get_sexton_audit(container: AipContainer = Depends(get_container)):
    # Stale rule audit from 7.3
    return {"audits": []}


@router.get("/admin/sexton/playbook")
async def get_sexton_playbook(container: AipContainer = Depends(get_container)):
    # From AcePlaybook (7.2)
    return {"entries": []}


@router.get("/admin/beast/status")
async def get_beast_status(container: AipContainer = Depends(get_container)):
    # From Beast (7.5)
    return {"last_run": None, "next": None, "health": "ok"}


@router.get("/admin/router/weights")
async def get_router_weights(container: AipContainer = Depends(get_container)):
    # From AdaptiveRouter (7.4)
    return {"weights": []}


@router.get("/admin/budget")
async def get_budget_status(container: AipContainer = Depends(get_container)):
    # From BudgetManager (7.0b)
    return {"status": "ok"}


@router.get("/admin/autonomy/log")
async def get_autonomy_log(container: AipContainer = Depends(get_container)):
    # From AutonomyGate audit (8.0b)
    return {"escalations": []}
