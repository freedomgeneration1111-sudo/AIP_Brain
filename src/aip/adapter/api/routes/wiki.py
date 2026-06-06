"""Wiki API routes — browse approved beast_wiki articles.

Unlike /knowledge which queries compiled_knowledge (empty until Phase 4
compilation pipeline runs), this endpoint reads directly from the
artifacts + ecs_state tables where beast_wiki articles are stored after
Beast generates them.

Returns articles with IDs matching beast:wiki:* or beast:proposal:*
that have an ecs_state entry (any state), so the WIKI tab can show
both approved and pending articles grouped by domain.
"""

from __future__ import annotations

import json
import logging

import aiosqlite

from fastapi import APIRouter, Query

router = APIRouter()
logger = logging.getLogger(__name__)

_STATE_DB = "db/state.db"


@router.get("/wiki/articles")
async def list_wiki_articles(
    state: str | None = None,
    domain: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
) -> dict:
    """List wiki articles from artifacts + ecs_state.

    Returns beast:wiki:* and beast:proposal:* artifacts with their
    ECS state, domain (extracted from metadata), content, and timestamps.

    Optional filters:
      - state: filter by ECS state (e.g. "APPROVED", "GENERATED")
      - domain: filter by domain name

    Each article item includes:
      - id: artifact ID (e.g. "beast:wiki:agi_philosophy:20260605T062614")
      - domain: extracted from artifact metadata or ID
      - state: current ECS state
      - content: article text content
      - artifact_type: "wiki" or "proposal"
      - version: artifact version number
      - created_at: creation timestamp
      - updated_at: last ECS state update timestamp
      - word_count: number of words in content
    """
    items: list[dict] = []

    # Build WHERE clause
    conditions = ["(a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%')"]
    params: list[str] = []

    if state:
        conditions.append("e.current_state = ?")
        params.append(state)

    if domain:
        conditions.append("(a.metadata_json LIKE ? OR a.id LIKE ?)")
        params.append(f'%"domain": "{domain}"%')
        params.append(f"beast:wiki:{domain}:%")

    where_clause = " AND ".join(conditions)

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            # Get latest version of each artifact with its ECS state
            cursor = await conn.execute(
                f"""
                SELECT a.id, a.version, a.content, a.metadata_json, a.created_at,
                       COALESCE(e.current_state, 'UNKNOWN') as current_state,
                       COALESCE(e.updated_at, a.created_at) as updated_at
                FROM artifacts a
                LEFT JOIN ecs_state e ON a.id = e.artifact_id
                INNER JOIN (
                    SELECT id, MAX(version) as max_ver
                    FROM artifacts
                    GROUP BY id
                ) latest ON a.id = latest.id AND a.version = latest.max_ver
                WHERE {where_clause}
                ORDER BY a.created_at DESC
                """,
                params,
            )
            rows = await cursor.fetchall()

            for row in rows:
                metadata = {}
                raw_meta = row["metadata_json"]
                if raw_meta:
                    try:
                        metadata = json.loads(raw_meta)
                    except (json.JSONDecodeError, TypeError):
                        pass

                artifact_id = row["id"]
                content_text = row["content"] or ""

                # Extract domain from metadata, or from artifact ID
                # ID format: beast:wiki:domain_name:timestamp
                article_domain = metadata.get("domain", "")
                if not article_domain and ":" in artifact_id:
                    parts = artifact_id.split(":")
                    if len(parts) >= 3:
                        article_domain = parts[2].replace("_", " ").title()

                items.append({
                    "id": artifact_id,
                    "knowledge_id": artifact_id,  # backward compat with wiki panel
                    "domain": article_domain,
                    "state": row["current_state"],
                    "content": content_text,
                    "artifact_type": "wiki" if artifact_id.startswith("beast:wiki:") else "proposal",
                    "version": row["version"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "approved_at": row["updated_at"] if row["current_state"] == "APPROVED" else None,
                    "word_count": len(content_text.split()) if content_text else 0,
                    "metadata": metadata,
                })
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("Failed to list wiki articles: %s", exc)
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    # Apply pagination
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return {"items": page_items, "total": total, "page": page, "page_size": page_size}


@router.get("/wiki/stats")
async def wiki_stats() -> dict:
    """Quick wiki statistics — article counts by state and domain.

    Returns total articles, approved count, generated count,
    and a list of domains with article counts.
    """
    stats: dict = {
        "total": 0,
        "approved": 0,
        "generated": 0,
        "domains": [],
    }

    try:
        conn = await aiosqlite.connect(_STATE_DB)
        conn.row_factory = aiosqlite.Row
        try:
            # Count by state
            cursor = await conn.execute(
                """
                SELECT e.current_state, COUNT(*) as c
                FROM artifacts a
                INNER JOIN ecs_state e ON a.id = e.artifact_id
                WHERE a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%'
                GROUP BY e.current_state
                """,
            )
            for row in await cursor.fetchall():
                st = row["current_state"]
                cnt = row["c"]
                stats["total"] += cnt
                if st == "APPROVED":
                    stats["approved"] = cnt
                elif st == "GENERATED":
                    stats["generated"] = cnt

            # Domain breakdown
            cursor = await conn.execute(
                """
                SELECT
                    COALESCE(
                        json_extract(a.metadata_json, '$.domain'),
                        SUBSTR(a.id, 12, INSTR(SUBSTR(a.id, 12), ':') - 1)
                    ) as domain,
                    e.current_state,
                    COUNT(*) as c
                FROM artifacts a
                INNER JOIN ecs_state e ON a.id = e.artifact_id
                WHERE a.id LIKE 'beast:wiki:%' OR a.id LIKE 'beast:proposal:%'
                GROUP BY domain, e.current_state
                ORDER BY domain
                """,
            )
            domain_map: dict[str, dict] = {}
            for row in await cursor.fetchall():
                d = row["domain"] or "(unclassified)"
                if d not in domain_map:
                    domain_map[d] = {"name": d, "total": 0, "approved": 0, "generated": 0}
                domain_map[d]["total"] += row["c"]
                if row["current_state"] == "APPROVED":
                    domain_map[d]["approved"] += row["c"]
                elif row["current_state"] == "GENERATED":
                    domain_map[d]["generated"] += row["c"]

            stats["domains"] = list(domain_map.values())
        finally:
            await conn.close()
    except Exception as exc:
        logger.warning("Wiki stats query failed: %s", exc)

    return stats
