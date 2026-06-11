"""Admin Console routes.

Writes (config) go through AutonomyGate (admin).
Reads from actors (Sexton, Beast, Router, Budget, etc.).

Embedding backfill: POST /admin/embeddings/backfill generates vectors for
lexical documents that don't yet have vector embeddings.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aip.adapter.api.dependencies import AipContainer, get_container, require_definer
from aip.foundation.schemas import coerce_autonomy_level
from aip.logging import get_logger

log = get_logger(__name__)

# Backward-compatible alias for existing route code that uses logger
logger = log

router = APIRouter()


@router.get("/admin/config")
async def get_admin_config(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Return the runtime configuration.

    DEFINER-only: the config dict may contain sensitive values
    (SMTP credentials, API key indicators, internal hostnames)
    that must not be exposed to unauthenticated callers.
    """
    return container.config or {"status": "unconfigured"}


@router.patch("/admin/config")
async def patch_admin_config(
    payload: dict, container: AipContainer = Depends(get_container), _auth=Depends(require_definer)
):
    """Apply runtime configuration changes.

    Safe keys (intervals, thresholds) are applied immediately.
    Unsafe keys (db_path, auth settings) require a restart.
    """
    if not container.autonomy_gate:
        raise HTTPException(503, "AutonomyGate not wired")
    esc = await container.autonomy_gate.escalate(
        action_type="modify_config",
        resource_id="admin_config",
        requested_level=coerce_autonomy_level("admin"),
        requested_by="api",
    )
    if not esc.granted:
        raise HTTPException(403, f"Autonomy gate blocked: {esc.reason}")

    # Define safe keys that can be hot-reloaded
    safe_keys = {
        "budget",
        "beast",
        "vigil",
        "sexton",
        "performance",
        "rate_limit",
        "surface",
    }
    set(payload.keys()) - safe_keys

    applied = {}
    not_applied = {}

    for key, value in payload.items():
        if key in safe_keys:
            # Apply to the in-memory config
            container.config[key] = value
            applied[key] = value
        else:
            not_applied[key] = "requires restart"

    return {
        "updated": True,
        "applied": applied,
        "not_applied": not_applied,
        "note": (
            "Safe keys (budget, beast, vigil, sexton, performance, rate_limit, surface) "
            "are applied immediately. Other keys require a process restart."
        )
        if not_applied
        else None,
    }


@router.get("/admin/sexton/classifications")
async def get_sexton_classifications(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    # From Sexton actor's failure classifier (ADR-011)
    if container.sexton_actor is not None:
        fc = getattr(container.sexton_actor, "_failure_classifier", None)
        if fc:
            try:
                classifications = await fc.classify_failures()
                return {
                    "classifications": [
                        {
                            "failure_type": fc.failure_type,
                            "trace_event_id": fc.trace_event_id,
                            "confidence": fc.confidence,
                        }
                        for fc in classifications
                    ],
                }
            except Exception:
                logger.warning("Sexton classification failed", exc_info=True)
    return {"classifications": []}


@router.get("/admin/sexton/audit")
async def get_sexton_audit(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    # Stale rule audit from Sexton actor's failure classifier (7.3)
    if container.sexton_actor is not None:
        fc = getattr(container.sexton_actor, "_failure_classifier", None)
        if fc:
            try:
                classified = await fc.classify_failures()
                rules = fc.derive_ace_rules(
                    [fc.__dict__ if hasattr(fc, "__dict__") else dict(fc) for fc in classified],
                )
                stale = fc.audit_model_gen_assumption(rules)
                return {"audits": stale}
            except Exception:
                logger.warning("Sexton audit failed", exc_info=True)
    return {"audits": []}


@router.get("/admin/sexton/playbook")
async def get_sexton_playbook(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    # From AcePlaybook (7.2) — Chunk 4: now uses async load_playbook()
    if container.ace_playbook:
        try:
            entries = await container.ace_playbook.load_playbook()
            return {"entries": [e.__dict__ for e in entries]}
        except Exception:
            logger.warning("ACE playbook list failed", exc_info=True)
    return {"entries": []}


@router.get("/admin/beast/status")
async def get_beast_status(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    # From Beast (7.5)
    if container.beast:
        try:
            health = await container.beast.run_health_check()
            return {"last_run": None, "next": None, "health": health}
        except Exception:
            logger.warning("Beast health check failed", exc_info=True)
    return {"last_run": None, "next": None, "health": "ok"}


@router.get("/admin/router/weights")
async def get_router_weights(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    # From AdaptiveRouter (7.4)
    if container.adaptive_router:
        try:
            weights = await container.adaptive_router.get_routing_weights()
            return {"weights": [w.__dict__ if hasattr(w, "__dict__") else w for w in weights]}
        except Exception:
            logger.warning("Router weights retrieval failed", exc_info=True)
    return {"weights": []}


@router.get("/admin/budget")
async def get_budget_status(
    scope: str = "session",
    scope_id: str = "default",
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Get budget status for a given scope and scope_id.

    Scopes: session, project, daily. The scope_id identifies the specific
    session, project, or day (ISO date string for daily).
    """
    if container.budget_manager:
        try:
            status = await container.budget_manager.get_status(scope=scope, scope_id=scope_id)
            return status
        except Exception:
            logger.warning("Budget status retrieval failed", exc_info=True)
    return {"status": "unconfigured", "budget_manager": False}


@router.get("/admin/autonomy/log")
async def get_autonomy_log(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    # From AutonomyGate audit
    return {"escalations": []}


# ------------------------------------------------------------------
# Embedding Backfill
# ------------------------------------------------------------------


class BackfillRequest(BaseModel):
    """Request body for POST /admin/embeddings/backfill."""

    domain: str | None = None
    limit: int = 500
    batch_size: int = 20
    dry_run: bool = False


@router.post("/admin/embeddings/backfill")
async def backfill_embeddings(
    body: BackfillRequest,
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Generate vector embeddings for lexical documents that don't have them yet.

    This endpoint now starts the backfill in the background (non-blocking) so
    the API remains responsive. Progress is available via GET /admin/embeddings/backfill/status .

    It uses the currently active embedding provider from the container (tied to
    the embedding slot selected in the UI).

    For large backfills (e.g. claude_komal ~14k chunks), use reasonable limit/batch
    or trigger and let it run; it can be re-triggered to resume (skips done items).
    """
    # Validate prerequisites (quick)
    if container.embedding_provider is None:
        raise HTTPException(503, "No embedding provider configured. Select an embedding model first.")
    if container.lexical_store is None:
        raise HTTPException(503, "Lexical store not wired.")
    if container.vector_store is None:
        raise HTTPException(503, "Vector store not wired.")

    # Check that the embedding provider is real (not mock/fake)
    provider_class = container.embedding_provider.__class__.__name__
    is_mock = "Mock" in provider_class or "Fake" in provider_class
    if is_mock:
        return {
            "ok": False,
            "error": (
                f"Current embedding provider is '{provider_class}' (mock/fake). "
                "Select a real embedding model via the /models page before running backfill."
            ),
            "scanned": 0,
            "embedded": 0,
            "failed": 0,
            "skipped": 0,
        }

    if container.backfill_status.get("running"):
        return {
            "ok": True,
            "scheduled": False,
            "message": "Backfill already running. Check status.",
            "status": container.backfill_status,
        }

    # Schedule background work
    container.backfill_status["running"] = True
    container.backfill_status["progress"] = {
        "scanned": 0,
        "embedded": 0,
        "skipped": 0,
        "failed": 0,
        "domain": body.domain or "all",
    }
    container.backfill_status["last_result"] = None

    asyncio.create_task(
        _run_backfill_in_background(body, container),
        name="backfill-embeddings",
    )

    return {
        "ok": True,
        "scheduled": True,
        "message": (
            "Backfill started in background. Poll GET /admin/embeddings/backfill/status for progress and result."
        ),
        "status": container.backfill_status,
    }


async def _run_backfill_in_background(body: BackfillRequest, container: AipContainer) -> None:
    """Background worker for backfill. Updates container.backfill_status with progress."""
    import json
    import sqlite3

    try:
        lexical_store = container.lexical_store
        vector_store = container.vector_store
        embedding_provider = container.embedding_provider
        provider_class = embedding_provider.__class__.__name__ if embedding_provider else "unknown"

        db_path = getattr(lexical_store, "_db_path", None)
        if not db_path:
            container.backfill_status["running"] = False
            container.backfill_status["last_result"] = {"ok": False, "error": "No lexical db_path"}
            return

        scanned = 0
        embedded = 0
        failed = 0
        skipped = 0

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        try:
            if body.domain:
                cursor = conn.execute(
                    "SELECT doc_id, content, domain, metadata FROM fts_documents "
                    "WHERE domain = ? ORDER BY rowid ASC LIMIT ?",
                    (body.domain, body.limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT doc_id, content, domain, metadata FROM fts_documents ORDER BY rowid ASC LIMIT ?",
                    (body.limit,),
                )
            rows = cursor.fetchall()

            for row in rows:
                doc_id = row["doc_id"]
                content = row["content"]
                domain = row["domain"]
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}

                scanned += 1
                container.backfill_status["progress"] = {
                    "scanned": scanned,
                    "embedded": embedded,
                    "skipped": skipped,
                    "failed": failed,
                    "domain": body.domain or "all",
                    "current_doc": doc_id,
                }

                try:
                    existing = await vector_store.get_by_id(doc_id)
                    if existing is not None:
                        skipped += 1
                        continue
                except Exception:
                    pass

                if body.dry_run:
                    embedded += 1
                    continue

                try:
                    text_to_embed = content[:2000] if len(content) > 2000 else content
                    embedding = await embedding_provider.embed(text_to_embed)

                    if embedding and len(embedding) > 0:
                        await vector_store.upsert(
                            id=doc_id,
                            embedding=embedding,
                            content=text_to_embed,
                            metadata=metadata,
                            domain=domain,
                        )
                        embedded += 1
                        if embedded % body.batch_size == 0:
                            log.info(
                                "backfill_progress",
                                embedded=embedded,
                                scanned=scanned,
                                domain=domain or "all",
                            )
                    else:
                        failed += 1
                        log.warning("backfill_empty_embedding", doc_id=doc_id)
                except Exception as exc:
                    failed += 1
                    log.warning("backfill_embed_failed", doc_id=doc_id, error=str(exc))

        finally:
            conn.close()

        result = {
            "ok": True,
            "provider": provider_class,
            "model": getattr(embedding_provider, "model", "unknown"),
            "scanned": scanned,
            "embedded": embedded,
            "failed": failed,
            "skipped": skipped,
            "dry_run": body.dry_run,
        }

        if not body.dry_run:
            log.info(
                "backfill_complete",
                scanned=scanned,
                embedded=embedded,
                failed=failed,
                skipped=skipped,
                domain=body.domain or "all",
            )

        container.backfill_status["last_result"] = result
        container.backfill_status["progress"] = result  # final

    except Exception as exc:
        log.error("backfill_failed", error=str(exc), exc_info=True)
        container.backfill_status["last_result"] = {"ok": False, "error": str(exc)}
    finally:
        container.backfill_status["running"] = False


@router.get("/admin/embeddings/backfill/status")
async def get_backfill_status(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Get status of the (background) backfill process.
    Includes running flag, current progress, and last result.
    """
    return container.backfill_status


# ------------------------------------------------------------------
# Sprint 5.26: Hot-Reload Admin Endpoint
# ------------------------------------------------------------------


@router.get("/admin/hot-reload/status")
async def get_hot_reload_status(
    container: AipContainer = Depends(get_container),
    _auth=Depends(require_definer),
):
    """Get detailed hot-reload status including pending and rejected changes.

    Sprint 5.26: Provides operators with visibility into the hot-reload
    system, including:
    - ConfigWatcher status (enabled, last reload, errors)
    - Recent successful reloads
    - Recent rejected changes with reasons
    - Current auto-tuning policy values
    - Alert manager config validation status
    """
    result: dict = {
        "config_watcher": {},
        "auto_tuning_policy": {},
        "alerting_validation": [],
    }

    # Config watcher status
    config_watcher = getattr(container, "_config_watcher", None)
    if config_watcher is not None and hasattr(config_watcher, "get_status"):
        try:
            result["config_watcher"] = config_watcher.get_status()
        except Exception:
            result["config_watcher"] = {"error": "status_retrieval_failed"}

    # Auto-tuning policy status
    try:
        from aip.adapter.auto_tuning_policy import load_policy_from_config

        config = getattr(container, "config", {})
        policy = load_policy_from_config(config)
        result["auto_tuning_policy"] = {
            "current_values": policy.to_dict(),
            "is_valid": policy.is_valid(),
            "validation_errors": policy._validation_errors,
        }
    except Exception as exc:
        result["auto_tuning_policy"] = {"error": str(exc)}

    # Alerting config validation
    alert_manager = getattr(container, "_alert_manager", None)
    if alert_manager is not None and hasattr(alert_manager, "validate_config"):
        try:
            warnings = alert_manager.validate_config()
            result["alerting_validation"] = warnings
        except Exception:
            result["alerting_validation"] = ["validation_failed"]

    return result
