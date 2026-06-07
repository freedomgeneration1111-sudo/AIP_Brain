"""Beast scan API route — corpus scan for bare mode Beast pane.

Per AIP_UNIFIED_CHAT_SPEC §Beast Pane:
- Fires AFTER a BARE response arrives (non-blocking)
- Uses FTS5 search via corpus_turn_store.search()
- Gets domain neighbors via graph_store.get_neighbors()
- Looks up wiki coverage for detected domain
- If scan fails, returns error — never fake results (AIP-G-02)
"""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Query

router = APIRouter()
logger = logging.getLogger(__name__)

_STATE_DB = "db/state.db"


@router.get("/beast/scan")
async def beast_scan(
    query: str = Query(..., description="User message to scan against corpus"),
    limit: int = Query(5, ge=1, le=20, description="Max turns to return"),
) -> dict[str, Any]:
    """Corpus scan for Beast pane in BARE mode.

    Per AIP-G-02: if any subsystem fails, report the failure — never
    fake results. If the entire scan fails, return {error: ...} so
    the pane can show "corpus unavailable".

    Returns:
        domain: best-guess domain from top FTS5 hit
        confidence: rough confidence (0-1) based on hit count
        top_turns: [{turn_id, snippet, importance, domain}]
        neighbors: [{source, target, relation}]
        wiki_coverage: {domain, word_count, status} or null
    """
    result: dict[str, Any] = {
        "domain": None,
        "confidence": 0.0,
        "top_turns": [],
        "neighbors": [],
        "wiki_coverage": None,
    }

    # Step 1: FTS5 search via corpus_turn_store (if available through container)
    # We access the corpus_turn_store through the app state container
    try:
        from aip.adapter.api.dependencies import get_container

        container = get_container()
    except Exception as exc:
        logger.warning("beast_scan: container unavailable: %s", exc)
        return {"error": f"container unavailable: {exc}"}

    if container is None:
        return {"error": "container not initialized"}

    corpus_turns = getattr(container, "corpus_turn_store", None)
    if corpus_turns is None:
        return {"error": "corpus_turn_store not configured"}

    # FTS5 search
    try:
        hits = await corpus_turns.search(query=query, limit=limit)
    except Exception as exc:
        logger.warning("beast_scan: FTS5 search failed: %s", exc)
        return {"error": f"search failed: {exc}"}

    if not hits:
        result["confidence"] = 0.0
        return result

    # Extract top turns and detect primary domain
    domain_counts: dict[str, int] = {}
    for hit in hits[:limit]:
        # CorpusTurn object — access attributes
        turn_id = getattr(hit, "turn_id", "")
        user_text = getattr(hit, "user_text", "")
        importance = getattr(hit, "importance", 0.0)
        domain = getattr(hit, "primary_domain", "")

        snippet = (user_text or "")[:200]
        result["top_turns"].append(
            {
                "turn_id": turn_id,
                "snippet": snippet,
                "importance": importance,
                "domain": domain,
            }
        )
        if domain:
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

    # Determine primary domain (most frequent)
    if domain_counts:
        best_domain = max(domain_counts, key=domain_counts.get)  # type: ignore[arg-type]
        result["domain"] = best_domain
        # Confidence: fraction of top turns in this domain
        result["confidence"] = round(
            domain_counts[best_domain] / max(len(result["top_turns"]), 1), 2
        )

    # Step 2: Domain neighbors from graph_store
    graph_store = getattr(container, "graph_store", None)
    if graph_store is not None and result["domain"]:
        try:
            # Convert domain to node_id format (lowercase, underscored)
            node_id = result["domain"].lower().replace(" ", "_")
            neighbors = graph_store.get_neighbors(node_id)
            for n in neighbors[:10]:
                result["neighbors"].append(
                    {
                        "source": node_id,
                        "target": getattr(n, "id", ""),
                        "relation": getattr(n, "entity_type", "CONNECTS"),
                    }
                )
        except Exception as exc:
            logger.warning("beast_scan: graph neighbors failed: %s", exc)

    # Step 3: Wiki coverage lookup
    if result["domain"]:
        try:
            conn = await aiosqlite.connect(_STATE_DB)
            conn.row_factory = aiosqlite.Row
            try:
                cursor = await conn.execute(
                    """
                    SELECT a.id, a.metadata_json, e.current_state
                    FROM artifacts a
                    LEFT JOIN ecs_state e ON a.id = e.artifact_id
                    INNER JOIN (
                        SELECT id, MAX(version) as max_ver
                        FROM artifacts
                        GROUP BY id
                    ) latest ON a.id = latest.id AND a.version = latest.max_ver
                    WHERE a.id LIKE 'beast:wiki:%'
                    AND (a.metadata_json LIKE ? OR a.id LIKE ?)
                    """,
                    (
                        f'%"domain": "{result["domain"]}"%',
                        f"beast:wiki:{result['domain']}:%",
                    ),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    meta_str = row["metadata_json"] or "{}"
                    try:
                        import json

                        meta = json.loads(meta_str)
                    except Exception:
                        meta = {}
                    # Only count if domain matches exactly
                    if meta.get("domain") == result["domain"]:
                        result["wiki_coverage"] = {
                            "domain": result["domain"],
                            "word_count": meta.get("word_count", 0),
                            "status": row["current_state"] or "UNKNOWN",
                        }
                        break
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("beast_scan: wiki lookup failed: %s", exc)

    return result
