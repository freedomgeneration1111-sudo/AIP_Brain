"""Admin Console routes.

Writes (config) go through AutonomyGate (admin).
Reads from actors (Sexton, Beast, Router, Budget, etc.).

Embedding backfill: POST /admin/embeddings/backfill generates vectors for
lexical documents that don't yet have vector embeddings.
"""

from __future__ import annotations

import logging

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
async def get_admin_config(container: AipContainer = Depends(get_container)):
    return container.config or {"status": "unconfigured"}


@router.patch("/admin/config")
async def patch_admin_config(payload: dict, container: AipContainer = Depends(get_container), _auth=Depends(require_definer)):
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
        "budget", "beast", "vigil", "sexton", "performance",
        "rate_limit", "surface",
    }
    unsafe_keys = set(payload.keys()) - safe_keys

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
        "note": "Safe keys (budget, beast, vigil, sexton, performance, rate_limit, surface) are applied immediately. Other keys require a process restart." if not_applied else None,
    }


@router.get("/admin/sexton/classifications")
async def get_sexton_classifications(container: AipContainer = Depends(get_container)):
    # From Sexton actor (7.1)
    if container.sexton:
        try:
            classifications = await container.sexton.classify_failures()
            return {
                "classifications": [
                    {"failure_type": fc.failure_type, "trace_event_id": fc.trace_event_id, "confidence": fc.confidence}
                    for fc in classifications
                ],
            }
        except Exception:
            logger.warning("Sexton classification failed", exc_info=True)
    return {"classifications": []}


@router.get("/admin/sexton/audit")
async def get_sexton_audit(container: AipContainer = Depends(get_container)):
    # Stale rule audit from 7.3
    if container.sexton:
        try:
            classified = await container.sexton.classify_failures()
            rules = container.sexton.derive_ace_rules(
                [fc.__dict__ if hasattr(fc, "__dict__") else dict(fc) for fc in classified],
            )
            stale = container.sexton.audit_model_gen_assumption(rules)
            return {"audits": stale}
        except Exception:
            logger.warning("Sexton audit failed", exc_info=True)
    return {"audits": []}


@router.get("/admin/sexton/playbook")
async def get_sexton_playbook(container: AipContainer = Depends(get_container)):
    # From AcePlaybook (7.2)
    if container.ace_playbook:
        try:
            entries = container.ace_playbook.list_entries()
            return {"entries": entries}
        except Exception:
            logger.warning("ACE playbook list failed", exc_info=True)
    return {"entries": []}


@router.get("/admin/beast/status")
async def get_beast_status(container: AipContainer = Depends(get_container)):
    # From Beast (7.5)
    if container.beast:
        try:
            health = await container.beast.run_health_check()
            return {"last_run": None, "next": None, "health": health}
        except Exception:
            logger.warning("Beast health check failed", exc_info=True)
    return {"last_run": None, "next": None, "health": "ok"}


@router.get("/admin/router/weights")
async def get_router_weights(container: AipContainer = Depends(get_container)):
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
async def get_autonomy_log(container: AipContainer = Depends(get_container)):
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
):
    """Generate vector embeddings for lexical documents that don't have them yet.

    This endpoint is used to backfill vector embeddings for data that was
    ingested before an embedding provider was configured (e.g. CLI ingestion
    with embedding_provider=None). It reads documents from the lexical store,
    checks which ones don't have vector embeddings, and generates embeddings
    using the currently active embedding provider.

    Parameters:
      - domain: Only backfill documents in this domain (default: all domains)
      - limit: Maximum number of documents to process (default: 500)
      - batch_size: Number of documents to embed before committing (default: 20)
      - dry_run: If True, only report what would be done without making changes

    Returns a summary with counts of documents scanned, embedded, and failed.
    """
    # Validate prerequisites
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

    # Scan lexical store for documents
    import json
    import sqlite3

    lexical_store = container.lexical_store
    vector_store = container.vector_store
    embedding_provider = container.embedding_provider

    # Query the lexical store's database directly for documents
    # This is a pragmatic approach — the LexicalStore protocol doesn't have
    # a "list all" method, so we access the underlying FTS5 database.
    db_path = getattr(lexical_store, "_db_path", None)
    if not db_path:
        raise HTTPException(503, "Cannot access lexical store database path.")

    try:
        scanned = 0
        embedded = 0
        failed = 0
        skipped = 0

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        try:
            # Get all document IDs and content from the lexical store
            if body.domain:
                cursor = conn.execute(
                    "SELECT doc_id, content, domain, metadata FROM fts_documents "
                    "WHERE domain = ? ORDER BY rowid ASC LIMIT ?",
                    (body.domain, body.limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT doc_id, content, domain, metadata FROM fts_documents "
                    "ORDER BY rowid ASC LIMIT ?",
                    (body.limit,),
                )
            rows = cursor.fetchall()

            for row in rows:
                doc_id = row["doc_id"]
                content = row["content"]
                domain = row["domain"]
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}

                scanned += 1

                # Check if this document already has a vector embedding
                try:
                    existing = await vector_store.get_by_id(doc_id)
                    if existing is not None:
                        skipped += 1
                        continue
                except Exception:
                    pass  # If get_by_id fails, proceed with embedding

                if body.dry_run:
                    embedded += 1
                    continue

                # Generate embedding
                try:
                    # Truncate content to avoid API limits (most models cap at ~8K tokens)
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

        return result

    except Exception as exc:
        log.error("backfill_failed", error=str(exc), exc_info=True)
        raise HTTPException(500, f"Backfill failed: {exc}")
