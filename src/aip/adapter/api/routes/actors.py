"""Actor status and management routes.

Exposes the status of all three orchestration actors (Beast, Vigil, Sexton)
to API consumers (GUI, CLI, monitoring). The GUI uses GET /actors/status
to display actor health and last-cycle information in the sidebar.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()


@router.get("/actors/status")
async def get_actors_status(container: AipContainer = Depends(get_container)):
    """Get status of all orchestration actors.

    Returns health, last-cycle info, and configuration for Beast, Vigil, and Sexton.
    This is the primary endpoint the GUI uses to populate the actor status display.
    """
    actors = {}

    # --- Beast ---
    beast_status: dict = {"initialized": container.beast is not None, "health": None, "last_cycle": None}
    if container.beast is not None:
        try:
            health = await container.beast.run_health_check()
            beast_status["health"] = health
            beast_status["last_cycle_time"] = container.beast._last_cycle_time
            beast_status["interval_seconds"] = container.beast._config.health_check_interval_seconds
        except Exception as exc:
            beast_status["health_error"] = str(exc)
    actors["beast"] = beast_status

    # --- Vigil ---
    vigil_status: dict = {"initialized": container.vigil is not None, "health": None}
    if container.vigil is not None:
        try:
            health = await container.vigil.check_canonical_health()
            vigil_status["health"] = health
            vigil_status["interval_seconds"] = container.vigil.config.canonical_health_check_interval_seconds
            vigil_status["stale_threshold_days"] = container.vigil.config.stale_threshold_days
        except Exception as exc:
            vigil_status["health_error"] = str(exc)
    actors["vigil"] = vigil_status

    # --- Sexton ---
    sexton_status: dict = {"initialized": container.sexton is not None, "unclassified_count": 0}
    if container.sexton is not None:
        try:
            unclassified = await container.sexton.count_unclassified()
            sexton_status["unclassified_count"] = unclassified
            sexton_status["interval_seconds"] = container.sexton._config.classification_interval_seconds
        except Exception as exc:
            sexton_status["error"] = str(exc)
    actors["sexton"] = sexton_status

    return {"actors": actors}


@router.get("/actors/{actor_name}")
async def get_actor_detail(actor_name: str, container: AipContainer = Depends(get_container)):
    """Get detailed status for a single actor."""
    if actor_name == "beast":
        if container.beast is None:
            return {"actor": "beast", "initialized": False}
        try:
            health = await container.beast.run_health_check()
            return {
                "actor": "beast",
                "initialized": True,
                "health": health,
                "last_cycle_time": container.beast._last_cycle_time,
                "config": {
                    "health_check_interval_seconds": container.beast._config.health_check_interval_seconds,
                    "corpus_reindex_interval_seconds": container.beast._config.corpus_reindex_interval_seconds,
                    "entity_maintenance_interval_seconds": container.beast._config.entity_maintenance_interval_seconds,
                    "max_reindex_batch_size": container.beast._config.max_reindex_batch_size,
                },
            }
        except Exception as exc:
            return {"actor": "beast", "initialized": True, "error": str(exc)}

    elif actor_name == "vigil":
        if container.vigil is None:
            return {"actor": "vigil", "initialized": False}
        try:
            health = await container.vigil.check_canonical_health()
            stale = await container.vigil.detect_stale_canonicals()
            inconsistencies = await container.vigil.detect_entity_inconsistencies()
            return {
                "actor": "vigil",
                "initialized": True,
                "health": health,
                "stale_canonicals_count": len(stale),
                "entity_inconsistencies_count": len(inconsistencies),
                "config": {
                    "canonical_health_check_interval_seconds": container.vigil.config.canonical_health_check_interval_seconds,
                    "stale_threshold_days": container.vigil.config.stale_threshold_days,
                    "re_evaluate_on_slot_change": container.vigil.config.re_evaluate_on_slot_change,
                    "max_re_evaluate_batch_size": container.vigil.config.max_re_evaluate_batch_size,
                    "entity_consistency_check": container.vigil.config.entity_consistency_check,
                },
            }
        except Exception as exc:
            return {"actor": "vigil", "initialized": True, "error": str(exc)}

    elif actor_name == "sexton":
        if container.sexton is None:
            return {"actor": "sexton", "initialized": False}
        try:
            unclassified = await container.sexton.count_unclassified()
            return {
                "actor": "sexton",
                "initialized": True,
                "unclassified_count": unclassified,
                "config": {
                    "classification_batch_size": container.sexton._config.classification_batch_size,
                    "classification_interval_seconds": container.sexton._config.classification_interval_seconds,
                    "audit_on_slot_change": container.sexton._config.audit_on_slot_change,
                    "max_unclassified_before_alert": container.sexton._config.max_unclassified_before_alert,
                },
            }
        except Exception as exc:
            return {"actor": "sexton", "initialized": True, "error": str(exc)}

    else:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Unknown actor: {actor_name}")


@router.post("/actors/{actor_name}/trigger")
async def trigger_actor_cycle(actor_name: str, container: AipContainer = Depends(get_container)):
    """Manually trigger an actor cycle (for debugging/admin)."""
    if actor_name == "beast":
        if container.beast is None:
            return {"error": "Beast not initialized"}
        try:
            summary = await container.beast.run_cycle()
            return {"actor": "beast", "triggered": True, "result": summary}
        except Exception as exc:
            return {"actor": "beast", "triggered": False, "error": str(exc)}

    elif actor_name == "vigil":
        if container.vigil is None:
            return {"error": "Vigil not initialized"}
        try:
            await container.vigil.run()
            health = await container.vigil.check_canonical_health()
            return {"actor": "vigil", "triggered": True, "health": health}
        except Exception as exc:
            return {"actor": "vigil", "triggered": False, "error": str(exc)}

    elif actor_name == "sexton":
        if container.sexton is None:
            return {"error": "Sexton not initialized"}
        try:
            await container.sexton.run_classification_cycle()
            unclassified = await container.sexton.count_unclassified()
            return {"actor": "sexton", "triggered": True, "remaining_unclassified": unclassified}
        except Exception as exc:
            return {"actor": "sexton", "triggered": False, "error": str(exc)}

    else:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Unknown actor: {actor_name}")
