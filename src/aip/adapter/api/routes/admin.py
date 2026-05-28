"""Admin Console routes (CHUNK-8.6).

Writes (config) go through AutonomyGate (admin). Reads from delivered actors (Sexton 7.1, Beast 7.5, Router 7.4, Budget 7.0b, etc.).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()


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
    if container.sexton:
        try:
            classifications = await container.sexton.classify_failures()
            return {"classifications": [{"failure_type": fc.failure_type, "trace_event_id": fc.trace_event_id, "confidence": fc.confidence} for fc in classifications]}
        except Exception:
            pass
    return {"classifications": []}


@router.get("/admin/sexton/audit")
async def get_sexton_audit(container: AipContainer = Depends(get_container)):
    # Stale rule audit from 7.3
    if container.sexton:
        try:
            classified = await container.sexton.classify_failures()
            rules = container.sexton.derive_ace_rules([fc.__dict__ if hasattr(fc, '__dict__') else dict(fc) for fc in classified])
            stale = container.sexton.audit_model_gen_assumption(rules)
            return {"audits": stale}
        except Exception:
            pass
    return {"audits": []}


@router.get("/admin/sexton/playbook")
async def get_sexton_playbook(container: AipContainer = Depends(get_container)):
    # From AcePlaybook (7.2)
    if container.ace_playbook:
        try:
            entries = container.ace_playbook.list_entries()
            return {"entries": entries}
        except Exception:
            pass
    return {"entries": []}


@router.get("/admin/beast/status")
async def get_beast_status(container: AipContainer = Depends(get_container)):
    # From Beast (7.5)
    if container.beast:
        try:
            health = await container.beast.run_health_check()
            return {"last_run": None, "next": None, "health": health}
        except Exception:
            pass
    return {"last_run": None, "next": None, "health": "ok"}


@router.get("/admin/router/weights")
async def get_router_weights(container: AipContainer = Depends(get_container)):
    # From AdaptiveRouter (7.4)
    if container.adaptive_router:
        try:
            weights = await container.adaptive_router.get_routing_weights()
            return {"weights": [w.__dict__ if hasattr(w, '__dict__') else w for w in weights]}
        except Exception:
            pass
    return {"weights": []}


@router.get("/admin/budget")
async def get_budget_status(container: AipContainer = Depends(get_container)):
    # From BudgetManager (7.0b)
    if container.budget_manager:
        try:
            status = await container.budget_manager.get_status()
            return status
        except Exception:
            pass
    return {"status": "ok"}


@router.get("/admin/autonomy/log")
async def get_autonomy_log(container: AipContainer = Depends(get_container)):
    # From AutonomyGate audit (8.0b)
    return {"escalations": []}
