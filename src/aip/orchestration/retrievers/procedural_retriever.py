"""ProceduralRetriever — retrieves how-to/procedure artifacts.

Implements the Retriever protocol. Retrieves APPROVED procedural
artifacts (how-to guides, step-by-step instructions, reference
procedures) that answer "how do I..." style questions.

These are distinct from wiki articles (which provide background/
context) and corpus evidence (which provides raw source material).
Procedural knowledge is action-oriented: it tells you what steps
to take, not just what something is.

The retriever reads from the artifacts table (matching
beast:procedure:* or ace_playbook entries) and checks ECS state
for APPROVED status. Only approved procedures are returned.

Phase 5.5 deliverable: Procedural Retriever.

Layer: orchestration. Imports from foundation (schemas, protocols).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any

from aip.foundation.schemas.retrieval_trace import (
    EvidenceStatus,
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Procedural query detection
# ---------------------------------------------------------------------------


# Patterns that suggest a procedural/how-to question
_PROCEDURAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)how (do|do I|does|can|should|to|might|will)\s", re.IGNORECASE),
    re.compile(r"(?i)steps (to|for|on)\s", re.IGNORECASE),
    re.compile(r"(?i)what (is|are) the (steps|procedure|process)\s", re.IGNORECASE),
    re.compile(r"(?i)guide(me| to| for)?\s", re.IGNORECASE),
    re.compile(r"(?i)instructions? (for|to|on)\s", re.IGNORECASE),
    re.compile(r"(?i)(configure|setup|install|deploy|run|create|build|fix)\s", re.IGNORECASE),
    re.compile(r"(?i)walkthrough\s", re.IGNORECASE),
    re.compile(r"(?i)how.?to\b", re.IGNORECASE),
]


def is_procedural_query(query: str) -> bool:
    """Check if a query looks like a procedural/how-to question.

    This is a lightweight heuristic — not a classifier. It checks
    for common patterns that indicate the user wants step-by-step
    instructions rather than factual information.

    Returns True if any procedural pattern matches.
    """
    for pattern in _PROCEDURAL_PATTERNS:
        if pattern.search(query):
            return True
    return False


# ---------------------------------------------------------------------------
# ProceduralRetriever
# ---------------------------------------------------------------------------


class ProceduralRetriever:
    """Retrieves APPROVED procedural/how-to artifacts.

    Implements the Retriever protocol from foundation.protocols.retrieval.

    Procedural artifacts are stored as beast:procedure:* entries in
    the artifacts table with ECS APPROVED status. The ACE playbook
    entries (ace_playbook table) also provide procedural intervention
    rules that answer "what to do when X fails" type questions.

    Scoring:
    - Intent match: procedural queries get a score boost
    - Keyword overlap: query terms vs. procedure content
    - Domain match: procedure domain matches query domain
    - Recency: newer procedures rank slightly higher

    Graceful degradation (AIP-G-02):
    - If db_path is None, returns []
    - If SQLite query fails, returns []
    - If no procedures found, returns [] (not an error)
    - Never raises — errors are recorded in the trace
    """

    def __init__(
        self,
        db_path: str | None = None,
        max_procedures: int = 3,
        intent_weight: float = 0.4,
        keyword_weight: float = 0.35,
        domain_weight: float = 0.25,
    ) -> None:
        """Initialize ProceduralRetriever.

        Args:
            db_path: Path to the SQLite database with artifacts + ecs_state.
            max_procedures: Maximum procedure articles to return.
            intent_weight: Weight for intent match (procedural query detection).
            keyword_weight: Weight for keyword overlap score.
            domain_weight: Weight for domain match score.
        """
        self._db_path = db_path
        self._max_procedures = max_procedures
        self._intent_weight = intent_weight
        self._keyword_weight = keyword_weight
        self._domain_weight = domain_weight

    @property
    def name(self) -> str:
        return "ProceduralRetriever"

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
    ) -> list[RetrievalHit]:
        """Fetch approved procedural artifacts matching the query.

        Strategy:
        1. Detect if query is procedural (how-to intent)
        2. Query artifacts + ecs_state for APPROVED procedure articles
        3. Also query ACE playbook for active entries matching domain
        4. Score procedures by intent + keyword + domain match
        5. Return as RetrievalHit with PROCEDURAL channel + APPROVED status
        """
        if not self._db_path:
            return []

        started = time.monotonic()
        errors: list[str] = []
        procedural_hits: list[RetrievalHit] = []

        try:
            procedural_hits = self._fetch_procedures(query, trace, budget)
        except Exception as exc:
            msg = f"ProceduralRetriever query failed: {exc}"
            logger.warning(msg)
            errors.append(msg)

        # Record trace
        elapsed_ms = (time.monotonic() - started) * 1000.0
        degraded = bool(errors) and len(procedural_hits) > 0
        error_msg = "; ".join(errors) if errors else None

        retriever_trace = RetrieverTrace(
            retriever_name=self.name,
            enabled=True,
            degraded=degraded,
            error=error_msg,
            started_at=datetime.now(),
            finished_at=datetime.now(),
            elapsed_ms=round(elapsed_ms, 2),
            hit_count=len(procedural_hits),
            top_score=procedural_hits[0].score if procedural_hits else 0.0,
            top_hit_ids=[h.id for h in procedural_hits],
            debug={
                "channel": "procedural",
                "max_procedures": self._max_procedures,
                "query_is_procedural": is_procedural_query(query.raw_query),
                "domains_checked": trace.detected_entities[:10] if trace.detected_entities else [],
                "score_weights": {
                    "intent": self._intent_weight,
                    "keyword": self._keyword_weight,
                    "domain": self._domain_weight,
                },
            },
        )
        trace.retriever_traces.append(retriever_trace)

        # Update trace procedural fields
        if procedural_hits:
            trace.procedural_injected = True
            trace.procedural_articles = [h.id for h in procedural_hits]

        return procedural_hits

    def _fetch_procedures(
        self,
        query: RetrievalQuery,
        trace: RetrievalTrace,
        budget: RetrievalBudget,
    ) -> list[RetrievalHit]:
        """Fetch approved procedural articles from SQLite.

        Queries the artifacts table for APPROVED procedures matching
        beast:procedure:* pattern. Also queries the ACE playbook table
        for active entries matching the domain.

        Returns list of RetrievalHit with PROCEDURAL channel.
        """
        import sqlite3

        max_procedures = min(
            self._max_procedures,
            budget.max_procedures,
        )

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row

        try:
            # Determine candidate domains from trace entities + query
            candidate_domains: set[str] = set()
            if trace.detected_entities:
                try:
                    placeholders = ",".join("?" for _ in trace.detected_entities)
                    domain_rows = conn.execute(
                        f"""
                        SELECT DISTINCT domain FROM graph_nodes
                        WHERE id IN ({placeholders}) AND domain IS NOT NULL
                        """,
                        trace.detected_entities,
                    ).fetchall()
                    for dr in domain_rows:
                        if dr[0]:
                            candidate_domains.add(dr[0])
                except Exception:
                    pass  # graceful: no graph_nodes table

            if query.domain_filter:
                candidate_domains.add(query.domain_filter)

            # --- Fetch APPROVED procedure articles ---
            rows = conn.execute(
                """
                SELECT a.id, a.content, a.metadata_json, a.created_at,
                       COALESCE(e.current_state, 'UNKNOWN') as state
                FROM artifacts a
                INNER JOIN ecs_state e ON a.id = e.artifact_id
                INNER JOIN (
                    SELECT id, MAX(version) as max_ver
                    FROM artifacts
                    GROUP BY id
                ) latest ON a.id = latest.id AND a.version = latest.max_ver
                WHERE (a.id LIKE 'beast:procedure:%' OR a.id LIKE 'beast:howto:%')
                  AND e.current_state = 'APPROVED'
                ORDER BY a.created_at DESC
                """,
            ).fetchall()

            # --- Also fetch active ACE playbook entries ---
            ace_rows = []
            try:
                ace_rows = conn.execute(
                    """
                    SELECT entry_id, domain, intervention, condition,
                           confidence, created_at
                    FROM ace_playbook
                    WHERE deprecated_at IS NULL
                    ORDER BY confidence DESC, created_at DESC
                    """,
                ).fetchall()
            except Exception:
                pass  # graceful: ace_playbook table may not exist

            if not rows and not ace_rows:
                return []

            # --- Score procedure articles ---
            query_tokens = set(query.raw_query.lower().split())
            is_procedural = is_procedural_query(query.raw_query)
            scored_articles: list[tuple[float, dict]] = []

            for row in rows:
                artifact_id = row["id"]
                content = row["content"] or ""
                metadata_json = row["metadata_json"] or "{}"

                import json
                try:
                    metadata = json.loads(metadata_json)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                article_domain = metadata.get("domain", "")
                if not article_domain and ":" in artifact_id:
                    parts = artifact_id.split(":")
                    if len(parts) >= 3:
                        article_domain = parts[2].replace("_", " ").title()

                # Score computation
                intent_score = 1.0 if is_procedural else 0.3

                # Keyword overlap
                content_lower = content.lower()
                overlap_count = sum(
                    1 for t in query_tokens if len(t) >= 3 and t in content_lower
                )
                keyword_score = min(1.0, overlap_count * 0.15) if overlap_count > 0 else 0.0

                # Domain match
                domain_score = 0.0
                if candidate_domains and article_domain.lower() in {
                    d.lower() for d in candidate_domains
                }:
                    domain_score = 1.0

                # Combined score
                total_score = (
                    self._intent_weight * intent_score
                    + self._keyword_weight * keyword_score
                    + self._domain_weight * domain_score
                )

                # Only include procedures with some relevance
                if total_score > 0.0 or not candidate_domains:
                    if not candidate_domains:
                        total_score = max(total_score, 0.1)

                    scored_articles.append((total_score, {
                        "id": artifact_id,
                        "content": content,
                        "domain": article_domain,
                        "metadata": metadata,
                        "type": "procedure_article",
                        "intent_score": intent_score,
                        "keyword_score": keyword_score,
                        "domain_score": domain_score,
                    }))

            # --- Score ACE playbook entries ---
            for row in ace_rows:
                entry_id = row["entry_id"]
                ace_domain = row["domain"] or ""
                intervention = row["intervention"] or ""
                condition = row["condition"] or ""
                confidence = float(row["confidence"] or 0.0)

                # Intent match
                intent_score = 1.0 if is_procedural else 0.2

                # Keyword overlap with intervention + condition
                ace_text = f"{intervention} {condition}".lower()
                overlap_count = sum(
                    1 for t in query_tokens if len(t) >= 3 and t in ace_text
                )
                keyword_score = min(1.0, overlap_count * 0.15) if overlap_count > 0 else 0.0

                # Domain match
                domain_score = 0.0
                if candidate_domains and ace_domain.lower() in {
                    d.lower() for d in candidate_domains
                }:
                    domain_score = 1.0

                # Combined score with confidence bonus
                total_score = (
                    self._intent_weight * intent_score
                    + self._keyword_weight * keyword_score
                    + self._domain_weight * domain_score
                ) * (0.5 + 0.5 * confidence)  # confidence-scaled

                if total_score > 0.0:
                    scored_articles.append((total_score, {
                        "id": entry_id,
                        "content": f"**Condition:** {condition}\n\n**Intervention:** {intervention}",
                        "domain": ace_domain,
                        "metadata": {"confidence": confidence, "condition": condition},
                        "type": "ace_playbook_entry",
                        "intent_score": intent_score,
                        "keyword_score": keyword_score,
                        "domain_score": domain_score,
                    }))

            # Sort by score descending, take top max_procedures
            scored_articles.sort(key=lambda x: x[0], reverse=True)
            top_articles = scored_articles[:max_procedures]

            # Convert to RetrievalHit
            hits: list[RetrievalHit] = []
            for rank, (score, article) in enumerate(top_articles, start=1):
                content = article["content"]
                max_chars = int(budget.total_tokens * budget.procedural_allocation)
                truncated = content[:max_chars]

                hit = RetrievalHit(
                    id=article["id"],
                    source_type=article["type"],
                    source_id=article["id"],
                    title=f"Procedure: {article['domain']}",
                    text=truncated,
                    snippet=truncated[:200],
                    rank=rank,
                    score=round(score, 4),
                    confidence=1.0,  # APPROVED procedure = high confidence
                    importance=0.8,  # Procedures get high importance
                    domain=article["domain"],
                    entities=article["metadata"].get("tags", []),
                    retrieval_channel=RetrievalChannel.PROCEDURAL,
                    evidence_status=EvidenceStatus.APPROVED,
                    debug={
                        "source": "procedural_retriever",
                        "ecs_state": "APPROVED",
                        "article_type": article["type"],
                        "word_count": len(content.split()),
                        "char_count": len(truncated),
                        "intent_score": round(article["intent_score"], 4),
                        "keyword_score": round(article["keyword_score"], 4),
                        "domain_score": round(article["domain_score"], 4),
                        "query_is_procedural": is_procedural,
                    },
                )
                hits.append(hit)

            return hits

        finally:
            conn.close()


__all__ = ["ProceduralRetriever", "is_procedural_query"]
