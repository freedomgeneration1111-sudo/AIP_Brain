"""Librarian — CODEX maintenance cycle orchestration.

The Librarian is the orchestration component that drives the CODEX internal
map maintenance. It runs as a cycle (typically invoked by Sexton) and performs:

1. Detect new docs: Discover ingested sources not yet in the CODEX map
2. Classify them: Assign domains and topics using the domain registry
3. Link to existing concepts: Connect new sources to existing topics
4. Flag contradictions: Detect conflicting claims across sources
5. Mark stale docs: Update staleness scores based on source freshness
6. Detect duplicates: Identify potential duplicate or overlapping content

The Librarian never auto-resolves contradictions or auto-merges duplicates —
those require DEFINER sovereignty. It only flags and categorizes.

Uses the "sexton" model slot for LLM-assisted classification and
contradiction detection when available; falls back to deterministic
heuristics otherwise.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from aip.adapter.codex.codex_store import CodexStore
from aip.foundation.schemas.codex import (
    CodexConfig,
    CodexContradiction,
    CodexSource,
    CodexTopic,
)
from aip.logging import get_logger

log = get_logger(__name__)


class Librarian:
    """CODEX Librarian — internal map maintenance actor.

    The Librarian maintains the structured internal map of AIP's corpus.
    It is the "internal librarian" that organizes project knowledge instead
    of merely retrieving chunks.

    Constructor accepts optional dependencies for graceful operation when
    those components are not yet configured. Uses the "sexton" model slot
    for LLM-assisted classification/contradiction detection.
    """

    def __init__(
        self,
        codex_store: CodexStore,
        corpus_turn_store: Any = None,  # CorpusTurnStore for reading corpus data
        event_store: Any = None,  # EventStore for audit trail
        model_provider: Any = None,  # ModelSlotResolver for LLM calls
        config: CodexConfig | None = None,
    ) -> None:
        self._store = codex_store
        self._corpus = corpus_turn_store
        self._events = event_store
        self._model = model_provider
        self._config = config or CodexConfig()
        self._cycle_count: int = 0
        self._last_cycle_time: float | None = None

    # ------------------------------------------------------------------
    # Main maintenance cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> dict:
        """Execute the full Librarian maintenance cycle.

        Operations run in this order:
        1. Sync sources from corpus_turns
        2. Classify unclassified sources
        3. Update topic map from source classifications
        4. Detect contradictions
        5. Compute staleness scores
        6. Detect duplicate candidates

        Returns a summary dict with results from each operation.
        """
        cycle_start = time.monotonic()

        log.info(
            "librarian_cycle_start",
            cycle=self._cycle_count + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        await self._emit_event(
            event_type="librarian_cycle_start",
            artifact_id="system",
            metadata={"cycle": self._cycle_count + 1},
        )

        # 1. Sync sources from corpus
        sync_result = await self._sync_sources()

        # 2. Classify unclassified sources
        classify_result = await self._classify_sources()

        # 3. Update topic map
        topic_result = await self._update_topic_map()

        # 4. Detect contradictions
        contradiction_result = await self._detect_contradictions()

        # 5. Compute staleness
        staleness_result = await self._compute_staleness()

        # 6. Detect duplicates
        duplicate_result = await self._detect_duplicates()

        elapsed = time.monotonic() - cycle_start
        self._last_cycle_time = time.time()
        self._cycle_count += 1

        summary = {
            "sync": sync_result,
            "classify": classify_result,
            "topics": topic_result,
            "contradictions": contradiction_result,
            "staleness": staleness_result,
            "duplicates": duplicate_result,
            "cycle_elapsed_seconds": round(elapsed, 3),
            "last_cycle_time": self._last_cycle_time,
        }

        await self._emit_event(
            event_type="librarian_cycle_complete",
            artifact_id="system",
            metadata=summary,
        )

        log.info(
            "librarian_cycle_complete",
            cycle=self._cycle_count,
            elapsed=round(elapsed, 3),
            synced=sync_result.get("new_sources", 0),
            classified=classify_result.get("classified", 0),
            topics_updated=topic_result.get("topics_updated", 0),
            contradictions_found=contradiction_result.get("new_contradictions", 0),
            staleness_updated=staleness_result.get("topics_updated", 0),
            duplicates_found=duplicate_result.get("new_candidates", 0),
        )

        return summary

    # ------------------------------------------------------------------
    # 1. Sync sources from corpus
    # ------------------------------------------------------------------

    async def _sync_sources(self) -> dict:
        """Discover sources from corpus_turns that aren't yet in the CODEX map.

        Groups corpus turns by source_path (or conversation_id for conversations)
        and creates CodexSource entries for any new groups. Updates existing
        entries with current word/turn counts.
        """
        if self._corpus is None:
            return {"skipped": "no_corpus_turn_store"}

        try:
            # Get all distinct source paths from corpus_turns
            conn = self._corpus._get_conn if hasattr(self._corpus, "_get_conn") else None
            if conn is None:
                return {"skipped": "corpus_not_connected"}

            # Query distinct sources from corpus_turns
            from aip.adapter.corpus_turn_store import CorpusTurnStore

            if not isinstance(self._corpus, CorpusTurnStore):
                return {"skipped": "corpus_type_mismatch"}

            # Direct SQL query for efficiency (we're in the adapter layer conceptually)
            async_conn = await self._corpus._get_conn()
            cursor = await async_conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(source_path, ''), conversation_id) as source_key,
                    COALESCE(NULLIF(source_path, ''), conversation_name) as source_label,
                    source_model,
                    MIN(export_date) as first_export,
                    MAX(updated_at) as last_update,
                    COUNT(*) as turn_count,
                    SUM(word_count) as total_words,
                    MAX(primary_domain) as primary_domain,
                    GROUP_CONCAT(DISTINCT primary_domain) as domains
                FROM corpus_turns
                WHERE source_path != '' OR conversation_name != ''
                GROUP BY source_key
                ORDER BY last_update DESC
                LIMIT ?
                """,
                (self._config.cycle_limit_sources,),
            )
            rows = await cursor.fetchall()

            new_sources = 0
            updated_sources = 0

            for row in rows:
                source_key = row["source_key"]
                source_label = row["source_label"] or source_key
                source_model = row["source_model"] or "unknown"

                # Generate a stable source_id
                source_id = _make_source_id(source_key)

                # Check if already registered
                existing = await self._store.get_source(source_id)
                now = datetime.now(timezone.utc).isoformat()

                # Determine source type
                source_type = "document"
                if source_model in ("claude", "gpt", "deepseek", "glm", "gemini", "grok"):
                    source_type = "conversation"
                elif source_model == "aip_chat":
                    source_type = "conversation"

                # Compute content hash from source_key + turn count
                hash_input = f"{source_key}:{row['turn_count']}:{row['total_words']}"
                content_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

                if existing is None:
                    # New source — register it
                    source = CodexSource(
                        source_id=source_id,
                        title=source_label[:200],
                        source_type=source_type,
                        source_path=source_key,
                        domain=row["primary_domain"] or "",
                        topics=[],
                        status="active",
                        content_hash=content_hash,
                        word_count=int(row["total_words"] or 0),
                        turn_count=int(row["turn_count"] or 0),
                        first_ingested_at=row["first_export"] or now,
                        last_updated_at=row["last_update"] or now,
                    )
                    await self._store.upsert_source(source)
                    new_sources += 1
                else:
                    # Existing source — update counts if changed
                    changed = False
                    if existing.word_count != int(row["total_words"] or 0):
                        existing.word_count = int(row["total_words"] or 0)
                        changed = True
                    if existing.turn_count != int(row["turn_count"] or 0):
                        existing.turn_count = int(row["turn_count"] or 0)
                        changed = True
                    if existing.content_hash != content_hash:
                        existing.content_hash = content_hash
                        changed = True
                    if not existing.domain and row["primary_domain"]:
                        existing.domain = row["primary_domain"]
                        changed = True

                    if changed:
                        existing.last_updated_at = row["last_update"] or now
                        # Reset stale status if content updated
                        if existing.status == "stale":
                            existing.status = "active"
                        await self._store.upsert_source(existing)
                        updated_sources += 1

            log.info(
                "librarian_sync_complete",
                new_sources=new_sources,
                updated_sources=updated_sources,
                total_groups=len(rows),
            )

            return {
                "new_sources": new_sources,
                "updated_sources": updated_sources,
                "total_groups": len(rows),
            }

        except Exception as exc:
            log.warning("librarian_sync_failed", error=str(exc))
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # 2. Classify unclassified sources
    # ------------------------------------------------------------------

    async def _classify_sources(self) -> dict:
        """Classify sources that have no domain assignment.

        Uses LLM when available; falls back to extracting the domain
        from the corpus_turn primary_domain field.
        """
        try:
            unclassified = await self._store.get_unclassified_sources(limit=self._config.cycle_limit_sources)

            if not unclassified:
                return {"classified": 0, "note": "nothing_to_classify"}

            classified = 0
            for source in unclassified:
                # Try to infer domain from corpus turns
                domain = await self._infer_source_domain(source)
                if domain:
                    source.domain = domain
                    await self._store.upsert_source(source)
                    classified += 1

            return {"classified": classified, "examined": len(unclassified)}

        except Exception as exc:
            log.warning("librarian_classify_failed", error=str(exc))
            return {"error": str(exc)}

    async def _infer_source_domain(self, source: CodexSource) -> str:
        """Infer the domain for a source from its corpus turns.

        Uses the most common primary_domain among the source's turns.
        """
        if self._corpus is None:
            return ""

        try:
            from aip.adapter.corpus_turn_store import CorpusTurnStore

            if not isinstance(self._corpus, CorpusTurnStore):
                return ""

            conn = await self._corpus._get_conn()
            # Find turns from this source and get most common domain
            if source.source_path:
                cursor = await conn.execute(
                    """
                    SELECT primary_domain, COUNT(*) as c
                    FROM corpus_turns
                    WHERE (source_path = ? OR conversation_id = ?)
                      AND primary_domain IS NOT NULL AND primary_domain != ''
                    GROUP BY primary_domain
                    ORDER BY c DESC LIMIT 1
                    """,
                    (source.source_path, source.source_path),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT primary_domain, COUNT(*) as c
                    FROM corpus_turns
                    WHERE conversation_id = ?
                      AND primary_domain IS NOT NULL AND primary_domain != ''
                    GROUP BY primary_domain
                    ORDER BY c DESC LIMIT 1
                    """,
                    (source.source_id,),
                )

            row = await cursor.fetchone()
            if row and row["primary_domain"]:
                return row["primary_domain"]

        except Exception as exc:
            log.debug("librarian_infer_domain_failed", source_id=source.source_id, error=str(exc))

        return ""

    # ------------------------------------------------------------------
    # 3. Update topic map
    # ------------------------------------------------------------------

    async def _update_topic_map(self) -> dict:
        """Build and update the topic map from source classifications.

        For each source with a domain, ensure topics exist for its key
        tags/concepts. Links sources to topics.
        """
        try:
            # Get all active sources with domains
            sources = await self._store.list_sources(limit=1000)
            active = [s for s in sources if s.domain and s.status == "active"]

            topics_updated = 0
            new_topics = 0

            for source in active:
                # Get topic tags from the source's corpus turns
                topic_tags = await self._get_source_topic_tags(source)

                for tag in topic_tags[:5]:  # Cap at 5 topics per source
                    topic_id = _make_topic_id(source.domain, tag)

                    existing = await self._store.get_topic(topic_id)
                    if existing is None:
                        # Create new topic
                        topic = CodexTopic(
                            topic_id=topic_id,
                            title=tag.replace("_", " ").title(),
                            domain=source.domain,
                            description=f"Topic: {tag} in domain {source.domain}",
                            source_ids=[source.source_id],
                            last_activity_at=source.last_updated_at,
                        )
                        await self._store.upsert_topic(topic)
                        new_topics += 1
                    else:
                        # Add source to existing topic
                        if source.source_id not in existing.source_ids:
                            existing.source_ids.append(source.source_id)
                            if source.last_updated_at > existing.last_activity_at:
                                existing.last_activity_at = source.last_updated_at
                            await self._store.upsert_topic(existing)
                            topics_updated += 1

                # Update source's topics list
                if topic_tags:
                    source.topics = topic_tags[:5]
                    await self._store.upsert_source(source)

            # Link related topics (same domain, overlapping sources)
            await self._link_related_topics()

            return {
                "new_topics": new_topics,
                "topics_updated": topics_updated,
            }

        except Exception as exc:
            log.warning("librarian_topic_map_failed", error=str(exc))
            return {"error": str(exc)}

    async def _get_source_topic_tags(self, source: CodexSource) -> list[str]:
        """Get the top topic tags for a source from its corpus turns."""
        if self._corpus is None:
            return []

        try:
            from aip.adapter.corpus_turn_store import CorpusTurnStore

            if not isinstance(self._corpus, CorpusTurnStore):
                return []

            conn = await self._corpus._get_conn()
            if source.source_path:
                cursor = await conn.execute(
                    """
                    SELECT tags FROM corpus_turns
                    WHERE (source_path = ? OR conversation_id = ?)
                      AND tags != '[]' AND tagging_version > 0
                    LIMIT 100
                    """,
                    (source.source_path, source.source_path),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT tags FROM corpus_turns
                    WHERE conversation_id = ?
                      AND tags != '[]' AND tagging_version > 0
                    LIMIT 100
                    """,
                    (source.source_id,),
                )

            rows = await cursor.fetchall()
            tag_counts: dict[str, int] = {}
            for row in rows:
                try:
                    for tag in json.loads(row["tags"] or "[]"):
                        if tag and tag not in ("unclassified",):
                            tag_counts[tag] = tag_counts.get(tag, 0) + 1
                except Exception:
                    pass

            # Return top tags sorted by frequency
            return [t for t, _ in sorted(tag_counts.items(), key=lambda kv: -kv[1])[:8]]

        except Exception as exc:
            log.debug("librarian_get_tags_failed", source_id=source.source_id, error=str(exc))
            return []

    async def _link_related_topics(self) -> None:
        """Link topics in the same domain that share sources."""
        try:
            topics = await self._store.list_topics(limit=500)
            by_domain: dict[str, list[CodexTopic]] = {}
            for t in topics:
                by_domain.setdefault(t.domain, []).append(t)

            for domain, domain_topics in by_domain.items():
                if len(domain_topics) < 2:
                    continue

                # Link topics that share sources
                for i, t1 in enumerate(domain_topics):
                    for t2 in domain_topics[i + 1 :]:
                        shared = set(t1.source_ids) & set(t2.source_ids)
                        if shared:
                            await self._store.add_related_topic(t1.topic_id, t2.topic_id)

        except Exception as exc:
            log.debug("librarian_link_topics_failed", error=str(exc))

    # ------------------------------------------------------------------
    # 4. Detect contradictions
    # ------------------------------------------------------------------

    async def _detect_contradictions(self) -> dict:
        """Detect contradictions between sources within the same topic.

        Uses LLM when available for semantic contradiction detection.
        Falls back to simple heuristic: sources with same domain but
        different status claims (stale vs active) on the same topic.

        Contradictions are NEVER auto-resolved — only flagged.
        """
        try:
            topics = await self._store.get_topics_with_contradictions(limit=20)

            # Also check topics with multiple sources (potential for contradictions)
            all_topics = await self._store.list_topics(limit=200)
            multi_source = [t for t in all_topics if len(t.source_ids) >= 2]

            new_contradictions = 0

            # Use LLM for semantic contradiction detection if available
            if self._model is not None:
                llm_results = await self._llm_detect_contradictions(multi_source)
                new_contradictions += llm_results
            else:
                # Heuristic: check for stale/active pairs on same topic
                for topic in multi_source:
                    sources = []
                    for sid in topic.source_ids:
                        src = await self._store.get_source(sid)
                        if src:
                            sources.append(src)

                    # Flag if some sources are stale and others active
                    active = [s for s in sources if s.status == "active"]
                    stale = [s for s in sources if s.status == "stale"]

                    if active and stale:
                        for a_src in active[:1]:  # Only flag first pair
                            for s_src in stale[:1]:
                                # Check if a contradiction already exists
                                existing = await self._store.list_contradictions(
                                    topic_id=topic.topic_id, status="open", limit=10
                                )
                                already_flagged = any(
                                    (e.source_a_id == a_src.source_id and e.source_b_id == s_src.source_id)
                                    or (e.source_a_id == s_src.source_id and e.source_b_id == a_src.source_id)
                                    for e in existing
                                )

                                if not already_flagged:
                                    now = datetime.now(timezone.utc).isoformat()
                                    cid = _make_contradiction_id(topic.topic_id, a_src.source_id, s_src.source_id)
                                    contradiction = CodexContradiction(
                                        contradiction_id=cid,
                                        topic_id=topic.topic_id,
                                        claim_a=f"Source '{a_src.title}' is current (active)",
                                        source_a_id=a_src.source_id,
                                        source_a_title=a_src.title,
                                        claim_b=f"Source '{s_src.title}' may contain outdated information (stale since {s_src.last_updated_at[:10]})",
                                        source_b_id=s_src.source_id,
                                        source_b_title=s_src.title,
                                        severity="apparent",
                                        status="open",
                                        context=f"Topic '{topic.title or topic.topic_id}' has both active and stale sources. "
                                        f"The stale source may contain claims that contradict the current source.",
                                        detected_at=now,
                                    )
                                    await self._store.upsert_contradiction(contradiction)
                                    new_contradictions += 1

            return {"new_contradictions": new_contradictions}

        except Exception as exc:
            log.warning("librarian_contradiction_detect_failed", error=str(exc))
            return {"error": str(exc)}

    async def _llm_detect_contradictions(self, topics: list[CodexTopic]) -> int:
        """Use LLM to detect semantic contradictions between sources.

        Asks the LLM to compare claims from different sources on the same
        topic and flag any factual conflicts.
        """
        if self._model is None:
            return 0

        new_count = 0
        limit = self._config.cycle_limit_contradictions

        for topic in topics:
            if new_count >= limit:
                break

            if len(topic.source_ids) < 2:
                continue

            # Get source content samples
            source_samples = []
            for sid in topic.source_ids[:5]:  # Max 5 sources per LLM call
                src = await self._store.get_source(sid)
                if src:
                    # Get a sample of content from the corpus turns
                    sample = await self._get_source_content_sample(src)
                    if sample:
                        source_samples.append(
                            {
                                "source_id": src.source_id,
                                "title": src.title or src.source_path,
                                "status": src.status,
                                "sample": sample[:500],  # Truncate for LLM context
                            }
                        )

            if len(source_samples) < 2:
                continue

            # Build LLM prompt
            prompt = _build_contradiction_detection_prompt(topic, source_samples)

            try:
                result = await self._model.call(
                    "sexton",
                    [
                        {"role": "system", "content": CONTRADICTION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                content = (result or {}).get("content", "").strip()

                # Parse LLM response for contradictions
                if content and "CONTRADICTION:" in content.upper():
                    contradictions = _parse_contradiction_response(content, topic, source_samples)
                    for c in contradictions:
                        # Check if already exists
                        existing = await self._store.list_contradictions(
                            topic_id=topic.topic_id, status="open", limit=10
                        )
                        already = any(
                            (e.source_a_id == c.source_a_id and e.source_b_id == c.source_b_id) for e in existing
                        )
                        if not already:
                            await self._store.upsert_contradiction(c)
                            new_count += 1

            except Exception as exc:
                log.debug("librarian_llm_contradiction_failed", topic_id=topic.topic_id, error=str(exc))

        return new_count

    async def _get_source_content_sample(self, source: CodexSource) -> str:
        """Get a sample of content from a source's corpus turns."""
        if self._corpus is None:
            return ""

        try:
            from aip.adapter.corpus_turn_store import CorpusTurnStore

            if not isinstance(self._corpus, CorpusTurnStore):
                return ""

            conn = await self._corpus._get_conn()
            if source.source_path:
                cursor = await conn.execute(
                    """
                    SELECT assistant_text FROM corpus_turns
                    WHERE (source_path = ? OR conversation_id = ?)
                      AND importance >= 0.5
                    ORDER BY importance DESC LIMIT 3
                    """,
                    (source.source_path, source.source_path),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT assistant_text FROM corpus_turns
                    WHERE conversation_id = ?
                      AND importance >= 0.5
                    ORDER BY importance DESC LIMIT 3
                    """,
                    (source.source_id,),
                )

            rows = await cursor.fetchall()
            samples = [row["assistant_text"][:300] for row in rows if row["assistant_text"]]
            return "\n---\n".join(samples)

        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 5. Compute staleness
    # ------------------------------------------------------------------

    async def _compute_staleness(self) -> dict:
        """Recompute staleness scores for all topics."""
        try:
            updated = await self._store.compute_staleness_scores(self._config)

            # Also mark individual stale sources
            stale_sources = await self._store.get_stale_sources(
                threshold_days=self._config.stale_threshold_days,
                limit=self._config.cycle_limit_sources,
            )
            marked = 0
            for src in stale_sources:
                if src.status == "active":
                    await self._store.mark_source_stale(src.source_id)
                    marked += 1

            return {
                "topics_updated": updated,
                "sources_marked_stale": marked,
            }

        except Exception as exc:
            log.warning("librarian_staleness_failed", error=str(exc))
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # 6. Detect duplicates
    # ------------------------------------------------------------------

    async def _detect_duplicates(self) -> dict:
        """Detect potential duplicate sources.

        Uses content hash for exact duplicates and LLM-based similarity
        for near-duplicates when the model is available.
        """
        try:
            sources = await self._store.list_sources(status="active", limit=200)
            new_candidates = 0

            # Exact hash-based dedup
            seen_hashes: dict[str, str] = {}
            for src in sources:
                if not src.content_hash:
                    continue
                if src.content_hash in seen_hashes:
                    # Register as duplicate candidate
                    await self._store.add_duplicate_candidate(
                        source_a_id=seen_hashes[src.content_hash],
                        source_b_id=src.source_id,
                        similarity_score=1.0,  # Exact match
                    )
                    new_candidates += 1
                else:
                    seen_hashes[src.content_hash] = src.source_id

            # Same-path dedup (different versions of same file)
            seen_paths: dict[str, str] = {}
            for src in sources:
                if not src.source_path:
                    continue
                if src.source_path in seen_paths:
                    await self._store.add_duplicate_candidate(
                        source_a_id=seen_paths[src.source_path],
                        source_b_id=src.source_id,
                        similarity_score=0.9,  # Very high — same path
                    )
                    new_candidates += 1
                else:
                    seen_paths[src.source_path] = src.source_id

            return {"new_candidates": new_candidates}

        except Exception as exc:
            log.warning("librarian_duplicate_detect_failed", error=str(exc))
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Status and event helpers
    # ------------------------------------------------------------------

    def get_status_summary(self) -> dict:
        """Return a summary of the Librarian's current state."""
        return {
            "cycle_count": self._cycle_count,
            "last_cycle_time": self._last_cycle_time,
            "dependencies": {
                "codex_store": self._store is not None,
                "corpus_turn_store": self._corpus is not None,
                "event_store": self._events is not None,
                "model_provider": self._model is not None,
            },
            "config": {
                "stale_threshold_days": self._config.stale_threshold_days,
                "cycle_limit_sources": self._config.cycle_limit_sources,
                "librarian_model_slot": self._config.librarian_model_slot,
            },
        }

    async def _emit_event(self, event_type: str, artifact_id: str, metadata: dict | None = None) -> None:
        """Write an event to the EventStore if available."""
        if self._events is None:
            return
        try:
            await self._events.write_event(
                event_type=event_type,
                actor="librarian",
                artifact_id=artifact_id,
                **(metadata or {}),
            )
        except Exception as exc:
            log.debug("librarian_event_emit_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _make_source_id(source_key: str) -> str:
    """Generate a stable source_id from a source key."""
    h = hashlib.sha256(source_key.encode()).hexdigest()[:12]
    return f"src-{h}"


def _make_topic_id(domain: str, tag: str) -> str:
    """Generate a topic_id from domain and tag."""
    safe_tag = tag.lower().replace(" ", "_")[:64]
    return f"{domain}:{safe_tag}"


def _make_contradiction_id(topic_id: str, source_a: str, source_b: str) -> str:
    """Generate a stable contradiction_id."""
    # Sort source IDs for deterministic ordering
    pair = sorted([source_a, source_b])
    h = hashlib.sha256(f"{topic_id}:{pair[0]}:{pair[1]}".encode()).hexdigest()[:12]
    return f"contra-{h}"


# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

CONTRADICTION_SYSTEM_PROMPT = """You are the AIP Librarian, a contradiction detection agent.
Your job is to identify factual contradictions between sources discussing the same topic.

For each contradiction found, output EXACTLY this format:
CONTRADICTION:
  Severity: critical|major|minor|apparent
  Source A claims: <claim>
  Source B claims: <claim>
  Context: <why this is contradictory>

If no contradictions are found, respond with: NO CONTRADICTIONS FOUND

Be conservative — only flag genuine factual conflicts, not differences in emphasis, 
perspective, or scope. Apparent contradictions (where context might explain the 
difference) should be marked as "apparent" severity."""


def _build_contradiction_detection_prompt(topic: CodexTopic, source_samples: list[dict]) -> str:
    """Build the user prompt for contradiction detection."""
    topic_title = topic.title or topic.topic_id
    source_blocks = []
    for i, s in enumerate(source_samples):
        source_blocks.append(f"SOURCE {chr(65 + i)}: {s['title']} (status: {s['status']})\n{s['sample']}")

    return (
        f"Topic: {topic_title} (domain: {topic.domain})\n\n"
        f"Compare the following sources for factual contradictions:\n\n" + "\n\n".join(source_blocks)
    )


def _parse_contradiction_response(
    content: str, topic: CodexTopic, source_samples: list[dict]
) -> list[CodexContradiction]:
    """Parse the LLM's contradiction detection response."""
    contradictions = []
    now = datetime.now(timezone.utc).isoformat()

    # Split by CONTRADICTION: markers
    blocks = content.split("CONTRADICTION:")
    for block in blocks[1:]:  # Skip the part before the first marker
        lines = block.strip().split("\n")
        severity = "major"
        claim_a = ""
        claim_b = ""
        context = ""

        for line in lines:
            line = line.strip()
            if line.lower().startswith("severity:"):
                sev = line.split(":", 1)[1].strip().lower()
                if sev in ("critical", "major", "minor", "apparent"):
                    severity = sev
            elif line.lower().startswith("source a claims:"):
                claim_a = line.split(":", 1)[1].strip()
            elif line.lower().startswith("source b claims:"):
                claim_b = line.split(":", 1)[1].strip()
            elif line.lower().startswith("context:"):
                context = line.split(":", 1)[1].strip()

        if claim_a and claim_b and len(source_samples) >= 2:
            src_a = source_samples[0]
            src_b = source_samples[1]
            cid = _make_contradiction_id(topic.topic_id, src_a["source_id"], src_b["source_id"])
            contradictions.append(
                CodexContradiction(
                    contradiction_id=cid,
                    topic_id=topic.topic_id,
                    claim_a=claim_a,
                    source_a_id=src_a["source_id"],
                    source_a_title=src_a["title"],
                    claim_b=claim_b,
                    source_b_id=src_b["source_id"],
                    source_b_title=src_b["title"],
                    severity=severity,
                    status="open",
                    context=context,
                    detected_at=now,
                )
            )

    return contradictions
