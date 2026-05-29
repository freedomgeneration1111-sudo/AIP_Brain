"""Beast Actor — cadence-based corpus and entity maintenance.

Beast — cadence / corpus / entity maintenance.
Deterministic (no LLM in main paths). Uses injected Protocols only.

Real health checks with connectivity probes, stale-vector detection via
list_stale_vectors() + re-embedding via EmbeddingProvider, properly
injected EntityStore, optional ProjectStore,
and cadence run_cycle() for periodic scheduling.

Usage via AipContainer:
    Beast is wired during application lifespan (app.py). To use it:

        container = request.app.state.container
        if container.beast:
            health = await container.beast.run_health_check()

    For periodic execution, call run_cycle() from a scheduler or background
    task. It runs health check + corpus maintenance + entity maintenance in
    sequence and returns a summary dict.

    Alternatively, individual methods can be called independently:
        await beast.run_health_check()
        await beast.run_corpus_maintenance()
        await beast.run_entity_maintenance()
"""

from __future__ import annotations

import time

from aip.foundation.protocols import (
    CanonicalStore,
    EmbeddingProvider,
    EntityStore,
    EventStore,
    ProjectStore,
    VectorStore,
)
from aip.foundation.schemas import BeastCadenceConfig
from aip.logging import get_logger

log = get_logger(__name__)


class Beast:
    """Beast maintenance actor.

    Performs real corpus maintenance (stale vector detection + re-embedding),
    entity consistency checks, and honest health reporting across all
    critical subsystems.

    Constructor accepts optional stores (entity_store, canonical_store,
    project_store) for graceful operation when those components are not
    yet configured.
    """

    def __init__(
        self,
        config: BeastCadenceConfig,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        project_store: ProjectStore | None = None,
        event_store: EventStore | None = None,
        entity_store: EntityStore | None = None,
        canonical_store: CanonicalStore | None = None,
    ) -> None:
        self._config = config
        self._vector = vector_store
        self._embed = embedding_provider
        self._projects = project_store
        self._events = event_store
        self._entity_store = entity_store
        self._canonical_store = canonical_store
        self._last_cycle_time: float | None = None

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def run_health_check(self) -> dict:
        """Comprehensive health report with real connectivity checks.

        Probes each critical subsystem and returns honest status.
        Never fabricates a healthy status when a check actually fails.

        Returns:
            dict with keys: vector_backend, embedding_provider, entity_store,
            canonical_store, databases, project_store, overall.
            Each sub-dict contains at minimum ``connected`` (bool).
        """
        checks: dict[str, dict] = {}
        all_healthy = True

        # --- Vector store ---
        try:
            v_health = await self._vector.health_check()
            checks["vector_backend"] = v_health
            if not v_health.get("connected", False):
                all_healthy = False
        except Exception as exc:
            checks["vector_backend"] = {"connected": False, "error": str(exc)}
            all_healthy = False

        # --- Embedding provider (Ollama or mock) ---
        embed_status = await self._check_embedding_provider()
        checks["embedding_provider"] = embed_status
        if not embed_status.get("connected", False):
            all_healthy = False

        # --- Entity store ---
        entity_status = await self._check_entity_store()
        checks["entity_store"] = entity_status
        if not entity_status.get("connected", False):
            # Entity store is optional for basic operation, degraded but not fatal
            pass

        # --- Canonical store ---
        canonical_status = await self._check_canonical_store()
        checks["canonical_store"] = canonical_status

        # --- Project store ---
        project_status = await self._check_project_store()
        checks["project_store"] = project_status

        # --- Database aggregates ---
        db_ok = entity_status.get("connected", False) and canonical_status.get("connected", False)
        checks["databases"] = {
            "events": "ok" if self._events is not None else "not_configured",
            "state": "ok" if entity_status.get("connected") else "degraded",
            "trace": "ok" if canonical_status.get("connected") else "degraded",
            "connected": db_ok,
        }

        overall = "ok" if all_healthy else "degraded"

        # Log health event if event store is wired
        await self._emit_event(
            event_type="beast_health_check",
            artifact_id="system",
            metadata={"overall": overall, "checks": {k: v.get("connected", False) for k, v in checks.items()}},
        )

        checks["overall"] = overall
        return checks

    async def _check_embedding_provider(self) -> dict:
        """Probe the embedding provider with a tiny embed call."""
        try:
            start = time.monotonic()
            await self._embed.embed("health-check-ping")
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"connected": True, "latency_ms": latency_ms}
        except Exception as exc:
            log.warning("health_check_failed", component="embedding_provider", error=str(exc))
            return {"connected": False, "error": str(exc)}

    async def _check_entity_store(self) -> dict:
        """Probe entity store via list_entities."""
        if self._entity_store is None:
            return {"connected": False, "status": "not_configured"}
        try:
            start = time.monotonic()
            await self._entity_store.list_entities()
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"connected": True, "latency_ms": latency_ms}
        except Exception as exc:
            log.warning("health_check_failed", component="entity_store", error=str(exc))
            return {"connected": False, "error": str(exc)}

    async def _check_canonical_store(self) -> dict:
        """Probe canonical store via list_canonical with no filters."""
        if self._canonical_store is None:
            return {"connected": False, "status": "not_configured"}
        try:
            start = time.monotonic()
            await self._canonical_store.list_canonical()
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"connected": True, "latency_ms": latency_ms}
        except Exception as exc:
            log.warning("health_check_failed", component="canonical_store", error=str(exc))
            return {"connected": False, "error": str(exc)}

    async def _check_project_store(self) -> dict:
        """Probe project store via list_projects."""
        if self._projects is None:
            return {"connected": False, "status": "not_configured"}
        try:
            start = time.monotonic()
            await self._projects.list_projects()
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"connected": True, "latency_ms": latency_ms}
        except Exception as exc:
            log.warning("health_check_failed", component="project_store", error=str(exc))
            return {"connected": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Corpus Maintenance
    # ------------------------------------------------------------------

    async def run_corpus_maintenance(self) -> dict:
        """Detect and re-embed stale vectors across all projects.

        Uses ``list_stale_vectors()`` on the VectorStore to identify vectors
        whose embeddings may be outdated (age beyond threshold or after model
        slot changes), then re-embeds their content and upserts fresh vectors.

        If project_store is not configured, performs a global stale-vector
        scan without per-project filtering.

        Returns:
            dict with: projects_checked, stale_vectors_found,
            vectors_reembedded, vectors_failed, errors.
        """
        if self._projects is None:
            # No project store — do a single global stale-vector scan
            return await self._corpus_maintenance_global()

        projects = await self._projects.list_projects()
        stale_found = 0
        reembedded = 0
        failed = 0
        errors = 0

        threshold_days = self._config.corpus_reindex_interval_seconds // 86400
        if threshold_days < 1:
            threshold_days = 1
        batch_limit = self._config.max_reindex_batch_size

        for proj in projects:
            pid = proj.get("project_id") or proj.get("id")
            if not pid:
                continue
            try:
                # Step 1: Detect stale vectors via the VectorStore protocol
                stale_vectors = await self._vector.list_stale_vectors(
                    threshold_days=threshold_days,
                    domain=pid,
                    limit=batch_limit,
                )
                stale_found += len(stale_vectors)

                # Step 2: Re-embed and upsert each stale vector
                reembedded_count, failed_count = await self._reembed_stale_vectors(stale_vectors, pid)
                reembedded += reembedded_count
                failed += failed_count

            except Exception as exc:
                log.error("corpus_maintenance_error", project=pid, error=str(exc))
                errors += 1

        result = {
            "projects_checked": len(projects),
            "stale_vectors_found": stale_found,
            "vectors_reembedded": reembedded,
            "vectors_failed": failed,
            "errors": errors,
        }

        # Emit maintenance event
        await self._emit_event(
            event_type="beast_corpus_maintenance",
            artifact_id="system",
            metadata=result,
        )

        return result

    async def _corpus_maintenance_global(self) -> dict:
        """Run corpus maintenance without project-level partitioning.

        Used when project_store is not configured. Performs a single
        list_stale_vectors() call without domain filtering.
        """
        threshold_days = self._config.corpus_reindex_interval_seconds // 86400
        if threshold_days < 1:
            threshold_days = 1
        batch_limit = self._config.max_reindex_batch_size

        try:
            stale_vectors = await self._vector.list_stale_vectors(
                threshold_days=threshold_days,
                domain=None,
                limit=batch_limit,
            )
            reembedded, failed = await self._reembed_stale_vectors(stale_vectors)

            result = {
                "projects_checked": 0,
                "stale_vectors_found": len(stale_vectors),
                "vectors_reembedded": reembedded,
                "vectors_failed": failed,
                "errors": 0,
                "mode": "global_no_project_store",
            }
        except Exception as exc:
            log.error("corpus_maintenance_failed", mode="global", error=str(exc))
            result = {
                "projects_checked": 0,
                "stale_vectors_found": 0,
                "vectors_reembedded": 0,
                "vectors_failed": 0,
                "errors": 1,
                "mode": "global_no_project_store",
            }

        await self._emit_event(
            event_type="beast_corpus_maintenance",
            artifact_id="system",
            metadata=result,
        )

        return result

    async def _reembed_stale_vectors(self, stale_vectors: list[dict], domain: str | None = None) -> tuple[int, int]:
        """Re-embed and upsert stale vectors. Returns (reembedded, failed)."""
        reembedded = 0
        failed = 0

        for vec_record in stale_vectors:
            vec_id = vec_record.get("id")
            content = vec_record.get("metadata", {}).get("content", "")
            vec_domain = vec_record.get("domain") or domain
            metadata = vec_record.get("metadata", {})

            if not vec_id or not content:
                failed += 1
                continue

            try:
                new_embedding = await self._embed.embed(content)
                await self._vector.upsert(
                    id=vec_id,
                    embedding=new_embedding,
                    content=content,
                    metadata=metadata,
                    domain=vec_domain,
                )
                reembedded += 1
            except Exception as exc:
                log.warning(
                    "reembed_failed",
                    vector_id=vec_id,
                    error=str(exc),
                )
                failed += 1

        return reembedded, failed

    # ------------------------------------------------------------------
    # Entity Maintenance
    # ------------------------------------------------------------------

    async def run_entity_maintenance(self) -> dict:
        """Validate entity consistency and cross-reference with canonicals.

        Checks for stale entity references (entities modified after their
        referencing canonical was promoted). Reports stale entities and
        emits events for downstream consumers (L4 regulation, Vigil).

        Returns:
            dict with: entities_checked, stale_entities, consistency_errors.
        """
        if self._entity_store is None:
            return {
                "entities_checked": 0,
                "stale_entities": [],
                "consistency_errors": 0,
                "skipped_reason": "entity_store_not_configured",
            }

        batch_size = self._config.max_reindex_batch_size
        entities_checked = 0
        stale_entities: list[dict] = []
        consistency_errors = 0

        try:
            all_entities = await self._entity_store.list_entities()

            for i in range(0, len(all_entities), batch_size):
                batch = all_entities[i : i + batch_size]
                for entity in batch:
                    entities_checked += 1
                    entity_id = entity.get("entity_id") or entity.get("id")
                    if not entity_id:
                        continue
                    try:
                        data = await self._entity_store.get_entity(entity_id)
                        if data and data.get("updated_since_canonical"):
                            stale_entities.append(
                                {
                                    "entity_id": entity_id,
                                    "entity_type": data.get("entity_type"),
                                    "name": data.get("name"),
                                    "reason": "updated_since_canonical",
                                },
                            )
                    except Exception as exc:
                        log.warning(
                            "entity_check_failed",
                            entity_id=entity_id,
                            error=str(exc),
                        )
                        consistency_errors += 1

        except Exception as exc:
            log.error("entity_maintenance_failed", error=str(exc))
            consistency_errors += 1

        result = {
            "entities_checked": entities_checked,
            "stale_entities": stale_entities,
            "consistency_errors": consistency_errors,
        }

        # Emit stale entity events for downstream consumers
        if stale_entities:
            await self._emit_event(
                event_type="beast_entity_stale_detected",
                artifact_id="system",
                metadata={
                    "stale_count": len(stale_entities),
                    "stale_entity_ids": [e.get("entity_id") for e in stale_entities],
                },
            )

        return result

    # ------------------------------------------------------------------
    # Cadence / Scheduling
    # ------------------------------------------------------------------

    async def run_cycle(self) -> dict:
        """Execute a full Beast maintenance cycle.

        Runs health check, corpus maintenance, and entity maintenance
        in sequence. Intended to be called periodically by a scheduler
        or background task.

        Returns a summary dict with results from each phase and timing.

        Example usage with asyncio:
            import asyncio
            beast = container.beast
            while True:
                summary = await beast.run_cycle()
                await asyncio.sleep(beast._config.health_check_interval_seconds)
        """
        cycle_start = time.monotonic()

        health = await self.run_health_check()
        corpus = await self.run_corpus_maintenance()
        entity = await self.run_entity_maintenance()

        elapsed = time.monotonic() - cycle_start
        self._last_cycle_time = time.time()

        summary = {
            "health_overall": health.get("overall", "unknown"),
            "corpus": corpus,
            "entity": entity,
            "cycle_elapsed_seconds": round(elapsed, 3),
            "last_cycle_time": self._last_cycle_time,
        }

        await self._emit_event(
            event_type="beast_cycle_complete",
            artifact_id="system",
            metadata=summary,
        )

        return summary

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _emit_event(
        self,
        event_type: str,
        artifact_id: str,
        metadata: dict | None = None,
    ) -> None:
        """Write an event to the EventStore if wired.

        Silently no-ops if no event store is configured. Never raises.
        """
        if self._events is None:
            return
        try:
            await self._events.write_event(
                event_type=event_type,
                actor="beast",
                artifact_id=artifact_id,
                from_state=None,
                to_state=None,
                **(metadata or {}),
            )
        except Exception as exc:
            log.warning("event_emit_failed", event_type=event_type, error=str(exc))
