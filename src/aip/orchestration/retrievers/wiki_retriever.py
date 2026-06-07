"""WikiRetriever — inject APPROVED beast_wiki articles as context.

Fetches approved wiki articles matching the query's detected domains and
injects them into the retrieval result pool. Wiki articles provide
high-quality background context without changing the evidence path —
they are classified as EvidenceStatus.APPROVED and RetrievalChannel.WIKI.

Design decisions:
- Only APPROVED wiki articles are included (ECS state = APPROVED)
- Articles are matched by domain (from query's detected entities or
  explicit domain filter) and by keyword overlap with the query
- Phase 5.4: Semantic matching via embedding similarity when
  EmbeddingProvider is available
- Wiki hits respect RetrievalBudget.wiki_allocation and max_wiki_articles
- Graceful degradation: if artifact store is unavailable, return []

The wiki route reads from artifacts + ecs_state tables. WikiRetriever
mirrors that access pattern but through the artifact store protocol
for cleaner layering.

Phase 5.3: Basic implementation with keyword + domain matching.
Phase 5.4: Semantic matching via embedding similarity, embedding_provider
    parameter for computing query-article similarity.

Layer: orchestration. Imports from foundation (schemas, protocols).
"""

from __future__ import annotations

import logging
import math
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
# WikiRetriever
# ---------------------------------------------------------------------------


class WikiRetriever:
    """Retrieves APPROVED beast_wiki articles for domain-level context.

    Implements the Retriever protocol. Wiki articles are injected as
    RetrievalChannel.WIKI hits with EvidenceStatus.APPROVED, which gives
    them a scoring boost in importance weighting.

    The retriever reads from the artifacts table (matching beast:wiki:*)
    and checks ECS state for APPROVED status. Only approved articles
    are returned — GENERATED or REJECTED articles are excluded.

    Phase 5.4: Semantic matching using embedding similarity. When an
    EmbeddingProvider is available, the query and wiki article summaries
    are embedded and ranked by cosine similarity. This significantly
    improves recall for queries that don't share keywords with wiki
    articles but are semantically related.

    Graceful degradation (AIP-G-02):
    - If db_path is None, returns []
    - If SQLite query fails, returns []
    - If embedding_provider is None or fails, falls back to keyword matching
    - Never raises — errors are recorded in the trace
    """

    def __init__(
        self,
        db_path: str | None = None,
        max_articles: int = 3,
        embedding_provider: Any = None,
        semantic_weight: float = 0.5,
        keyword_weight: float = 0.3,
        domain_weight: float = 0.2,
    ) -> None:
        """Initialize WikiRetriever.

        Args:
            db_path: Path to the SQLite database with artifacts + ecs_state.
            max_articles: Maximum wiki articles to return.
            embedding_provider: Optional EmbeddingProvider for semantic matching.
                When available, embedding similarity is computed between the
                query and wiki article content, improving recall for semantically
                related but lexically different queries.
            semantic_weight: Weight for embedding similarity score component.
            keyword_weight: Weight for keyword overlap score component.
            domain_weight: Weight for domain match score component.
        """
        self._db_path = db_path
        self._max_articles = max_articles
        self._embedding_provider = embedding_provider
        self._semantic_weight = semantic_weight
        self._keyword_weight = keyword_weight
        self._domain_weight = domain_weight

    @property
    def name(self) -> str:
        return "WikiRetriever"

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
    ) -> list[RetrievalHit]:
        """Fetch approved wiki articles matching the query's domains.

        Strategy:
        1. Determine candidate domains from:
           - trace.detected_entities (entity domains from graph)
           - query.domain_filter (explicit)
           - Keyword matching against wiki article content
        2. Query artifacts + ecs_state for APPROVED wiki articles
        3. Score articles using:
           - Domain match (Phase 5.3+)
           - Keyword overlap (Phase 5.3+)
           - Embedding similarity (Phase 5.4, when available)
        4. Return as RetrievalHit with WIKI channel + APPROVED status
        """
        if not self._db_path:
            return []

        started = time.monotonic()
        errors: list[str] = []
        wiki_hits: list[RetrievalHit] = []
        semantic_used = False

        try:
            wiki_hits, semantic_used = await self._fetch_approved_wiki_articles(
                query, trace, budget
            )
        except Exception as exc:
            msg = f"WikiRetriever query failed: {exc}"
            logger.warning(msg)
            errors.append(msg)

        # Record trace
        elapsed_ms = (time.monotonic() - started) * 1000.0
        degraded = bool(errors) and len(wiki_hits) > 0
        error_msg = "; ".join(errors) if errors else None

        retriever_trace = RetrieverTrace(
            retriever_name=self.name,
            enabled=True,
            degraded=degraded,
            error=error_msg,
            started_at=datetime.now(),
            finished_at=datetime.now(),
            elapsed_ms=round(elapsed_ms, 2),
            hit_count=len(wiki_hits),
            top_score=wiki_hits[0].score if wiki_hits else 0.0,
            top_hit_ids=[h.id for h in wiki_hits],
            debug={
                "channel": "wiki",
                "max_articles": self._max_articles,
                "domains_checked": trace.detected_entities[:10] if trace.detected_entities else [],
                "semantic_matching_used": semantic_used,
                "embedding_provider_available": self._embedding_provider is not None,
                "score_weights": {
                    "semantic": self._semantic_weight,
                    "keyword": self._keyword_weight,
                    "domain": self._domain_weight,
                },
            },
        )
        trace.retriever_traces.append(retriever_trace)

        # Update trace wiki fields
        if wiki_hits:
            trace.wiki_injected = True
            trace.wiki_articles = [h.id for h in wiki_hits]

        return wiki_hits

    async def _fetch_approved_wiki_articles(
        self,
        query: RetrievalQuery,
        trace: RetrievalTrace,
        budget: RetrievalBudget,
    ) -> tuple[list[RetrievalHit], bool]:
        """Fetch approved wiki articles from SQLite.

        Queries the artifacts + ecs_state tables for APPROVED wiki articles.
        Matches by domain (from detected entities or query), keyword overlap,
        and optionally embedding similarity with the query text.

        Returns:
            Tuple of (wiki_hits, semantic_used).
        """
        import sqlite3

        max_articles = min(
            self._max_articles,
            budget.max_wiki_articles,
        )

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        semantic_used = False

        try:
            # Get APPROVED wiki articles
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
                WHERE a.id LIKE 'beast:wiki:%'
                  AND e.current_state = 'APPROVED'
                ORDER BY a.created_at DESC
                """,
            ).fetchall()

            if not rows:
                return [], False

            # Determine candidate domains from trace entities + query
            candidate_domains: set[str] = set()
            if trace.detected_entities:
                # Map entity IDs to their domains via graph_nodes
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
                    pass  # graceful: no graph_nodes table or no domains

            # Also add domain from query filter
            if query.domain_filter:
                candidate_domains.add(query.domain_filter)

            # Score each wiki article by relevance to the query
            query_tokens = set(query.raw_query.lower().split())
            scored_articles: list[tuple[float, dict]] = []

            # Pre-compute query embedding if available (Phase 5.4)
            query_embedding: list[float] | None = None
            if self._embedding_provider is not None:
                try:
                    query_embedding = await self._embedding_provider.embed(query.raw_query)
                    if query_embedding and len(query_embedding) > 0:
                        semantic_used = True
                except Exception as exc:
                    logger.debug("Wiki embedding failed (falling back to keyword): %s", exc)
                    query_embedding = None

            for row in rows:
                artifact_id = row["id"]
                content = row["content"] or ""
                metadata_json = row["metadata_json"] or "{}"

                import json
                try:
                    metadata = json.loads(metadata_json)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                # Extract domain from metadata or ID
                article_domain = metadata.get("domain", "")
                if not article_domain and ":" in artifact_id:
                    parts = artifact_id.split(":")
                    if len(parts) >= 3:
                        article_domain = parts[2].replace("_", " ").title()

                # --- Score computation ---
                domain_score = 0.0
                keyword_score = 0.0
                semantic_score = 0.0

                # Domain match: strong signal
                if candidate_domains and article_domain.lower() in {d.lower() for d in candidate_domains}:
                    domain_score = 1.0

                # Keyword overlap with query
                content_lower = content.lower()
                title_lower = article_domain.lower()
                overlap_count = sum(1 for t in query_tokens if len(t) >= 3 and t in content_lower)
                if overlap_count > 0:
                    keyword_score = min(1.0, overlap_count * 0.15)

                # Title/domain name match with query tokens
                title_overlap = sum(1 for t in query_tokens if len(t) >= 3 and t in title_lower)
                if title_overlap > 0:
                    keyword_score = min(1.0, keyword_score + title_overlap * 0.2)

                # Semantic similarity (Phase 5.4)
                if query_embedding is not None:
                    # Use article content (truncated to first 500 chars for speed)
                    # as the text to embed for similarity comparison
                    article_text = content[:500]
                    try:
                        article_embedding = await self._embedding_provider.embed(article_text)
                        if article_embedding and len(article_embedding) > 0:
                            semantic_score = _cosine_similarity(query_embedding, article_embedding)
                    except Exception:
                        semantic_score = 0.0

                # Combine scores with configurable weights
                total_score = (
                    self._semantic_weight * semantic_score
                    + self._keyword_weight * keyword_score
                    + self._domain_weight * domain_score
                )

                # Only include articles with some relevance
                if total_score > 0.0 or not candidate_domains:
                    # If no candidate domains, include all approved wikis with low score
                    if not candidate_domains:
                        total_score = max(total_score, 0.1)

                    scored_articles.append((total_score, {
                        "id": artifact_id,
                        "content": content,
                        "domain": article_domain,
                        "metadata": metadata,
                        "created_at": row["created_at"],
                        "domain_score": domain_score,
                        "keyword_score": keyword_score,
                        "semantic_score": semantic_score,
                    }))

            # Sort by score descending, take top max_articles
            scored_articles.sort(key=lambda x: x[0], reverse=True)
            top_articles = scored_articles[:max_articles]

            # Convert to RetrievalHit
            hits: list[RetrievalHit] = []
            for rank, (score, article) in enumerate(top_articles, start=1):
                content = article["content"]
                # Truncate content for context (wiki articles can be long)
                max_chars = int(budget.total_tokens * budget.wiki_allocation)
                truncated = content[:max_chars]

                hit = RetrievalHit(
                    id=article["id"],
                    source_type="wiki_article",
                    source_id=article["id"],
                    title=f"Wiki: {article['domain']}",
                    text=truncated,
                    snippet=truncated[:200],
                    rank=rank,
                    score=round(score, 4),
                    confidence=1.0,  # APPROVED wiki = high confidence
                    importance=1.0,  # Approved wiki gets max importance
                    domain=article["domain"],
                    entities=article["metadata"].get("tags", []),
                    retrieval_channel=RetrievalChannel.WIKI,
                    evidence_status=EvidenceStatus.APPROVED,
                    debug={
                        "source": "wiki_retriever",
                        "ecs_state": "APPROVED",
                        "word_count": len(content.split()),
                        "char_count": len(truncated),
                        "domain_score": round(article["domain_score"], 4),
                        "keyword_score": round(article["keyword_score"], 4),
                        "semantic_score": round(article["semantic_score"], 4),
                        "semantic_matching_used": semantic_used,
                    },
                )
                hits.append(hit)

            return hits, semantic_used

        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns a value in [-1, 1]. For embedding similarity, values
    are typically in [0, 1] since embeddings are usually in the
    positive quadrant.
    """
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)


__all__ = ["WikiRetriever"]
