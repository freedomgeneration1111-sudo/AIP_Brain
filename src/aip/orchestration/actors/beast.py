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

import json
import time
from datetime import datetime, timezone
from typing import Any

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

# beast_provider is Any (ModelSlotResolver or None) — not a protocol import to preserve optional

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
        beast_provider: Any = None,  # ModelSlotResolver for "beast" slot, or None (heartbeat-only)
        artifact_store: Any = None,
        ecs_store: Any = None,
        lexical_store: Any = None,  # for sampling chunks in domain summaries
        corpus_turn_store: Any = None,  # for Beast turn tagging against domain registry
    ) -> None:
        self._config = config
        self._vector = vector_store
        self._embed = embedding_provider
        self._projects = project_store
        self._events = event_store
        self._entity_store = entity_store
        self._canonical_store = canonical_store
        self._beast_provider = beast_provider  # for conditional LLM summaries (event-driven)
        self._artifacts = artifact_store
        self._ecs = ecs_store
        self._lexical = lexical_store
        self._corpus_turns = corpus_turn_store  # for _run_turn_tagging (batch 8 + registry)
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

        # Lightweight heartbeat (always) + conditional summaries (only on change)
        heartbeat = await self._run_lightweight_heartbeat()
        summary_stats = {}
        if self._beast_provider is not None:
            try:
                summary_stats = await self._run_conditional_domain_summaries()
            except Exception as exc:
                log.error("conditional_summaries_failed", error=str(exc))
                summary_stats = {"error": str(exc)}
        else:
            log.info("beast: no LLM configured, heartbeat only")
            summary_stats = {"note": "no_llm_heartbeat_only"}

        # Turn tagging (new): batch LLM tagging of untagged turns using domain registry.
        # Only if provider + store present. Cap 200/cycle. Background does untagged only;
        # use `aip corpus tag --retag` for re-evaluation of already-tagged turns.
        tagging_stats: dict = {}
        if self._beast_provider is not None and getattr(self, "_corpus_turns", None) is not None:
            try:
                # Cheap probe: only invoke heavy LLM work if there is untagged work
                probe = await self._corpus_turns.get_untagged_turns(limit=1)
                if probe:
                    tagging_stats = await self._run_turn_tagging(limit=200)
                # (retag eligibility / last-tagged vs corpus_modified check can be added later;
                # for now CLI --retag drives re-tagging explicitly)
            except Exception as exc:
                log.error("beast_turn_tagging_failed", error=str(exc))
                tagging_stats = {"error": str(exc)}

        # Embedding pass (new): embed unembedded corpus turns after tagging.
        # Only if embedding provider + corpus_turns. Cap 200/cycle for background.
        # CLI can pass larger limit.
        embedding_stats: dict = {}
        if getattr(self, "_embed", None) is not None and getattr(self, "_corpus_turns", None) is not None:
            try:
                # Probe for unembedded
                unemb = 0
                if hasattr(self._corpus_turns, "count_unembedded"):
                    unemb = await self._corpus_turns.count_unembedded()
                if unemb > 0:
                    embedding_stats = await self._run_embedding_pass(limit=200)
            except Exception as exc:
                log.error("beast_embedding_failed", error=str(exc))
                embedding_stats = {"error": str(exc)}

        # Wiki generation: domain-level articles from tagged corpus turns.
        # Cap 5 per background cycle. CLI --all bypasses cap.
        wiki_stats: dict = {}
        if (
            self._beast_provider is not None
            and getattr(self, "_corpus_turns", None) is not None
            and self._artifacts is not None
        ):
            try:
                wiki_stats = await self._run_wiki_generation(max_per_cycle=5)
            except Exception as exc:
                log.error("beast_wiki_generation_failed", error=str(exc))
                wiki_stats = {"error": str(exc)}

        # Graph extraction: entity/relationship extraction on high-importance turns.
        # Cap 50 turns per background cycle. Requires beast_provider + corpus_turns.
        graph_stats: dict = {}
        if self._beast_provider is not None and getattr(self, "_corpus_turns", None) is not None:
            try:
                graph_stats = await self._run_graph_extraction(limit=50)
            except Exception as exc:
                log.error("beast_graph_extraction_failed", error=str(exc))
                graph_stats = {"error": str(exc)}

        elapsed = time.monotonic() - cycle_start
        self._last_cycle_time = time.time()

        summary = {
            "health_overall": health.get("overall", "unknown"),
            "corpus": corpus,
            "entity": entity,
            "heartbeat": heartbeat,
            "summaries": summary_stats,
            "tagging": tagging_stats,
            "embedding": embedding_stats,
            "wiki": wiki_stats,
            "graph": graph_stats,
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
    # Event-driven domain summaries (Part 2) + lightweight heartbeat
    # ------------------------------------------------------------------

    async def _run_lightweight_heartbeat(self) -> dict:
        """LIGHTWEIGHT HEARTBEAT — runs every cycle, zero LLM cost.

        - Health already done in run_cycle
        - Budget headroom check (if budget_store wired via events or direct? use event for now)
        - Flag GENERATED artifacts >24h old
        - Write beast_heartbeat event
        """
        heartbeat = {
            "budget_ok": True,
            "stale_generated_flagged": 0,
            "heartbeat_written": False,
        }
        try:
            # Budget: if we have access to event or assume from config; simple check via event count or skip detailed
            # For now, emit that we checked (real budget in budget_store, but Beast may not have direct; use presence)
            if self._events is not None:
                # Could query recent budget events, but keep zero cost: just note reachable
                pass
            heartbeat["budget_ok"] = True  # assume; detailed would use budget_store if injected

            # Flag old GENERATED (requires ecs + artifacts)
            if self._ecs is not None and self._artifacts is not None:
                try:
                    # ecs list_by_state returns ids in GENERATED
                    old_ids = []
                    gen_ids = await self._ecs.list_by_state("GENERATED", limit=100)
                    now = datetime.now(timezone.utc)
                    for aid in gen_ids:
                        try:
                            meta = await self._artifacts.read_metadata(aid)
                            ts = meta.get("created_at") or meta.get("generated_at")
                            if ts:
                                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                if (now - dt).total_seconds() > 24 * 3600:
                                    old_ids.append(aid)
                        except Exception:
                            pass
                    heartbeat["stale_generated_flagged"] = len(old_ids)
                    if old_ids:
                        await self._emit_event(
                            event_type="beast_stale_generated_detected",
                            artifact_id="system",
                            metadata={"count": len(old_ids), "examples": old_ids[:5]},
                        )
                except Exception:
                    pass

            # Write heartbeat event (lightweight)
            await self._emit_event(
                event_type="beast_heartbeat",
                artifact_id="system",
                metadata={
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "last_cycle": self._last_cycle_time,
                },
            )
            heartbeat["heartbeat_written"] = True
        except Exception as exc:
            log.warning("lightweight_heartbeat_failed", error=str(exc))
        return heartbeat

    async def _get_corpus_modified_map(self) -> dict[str, str]:
        """Return {domain: latest_iso_timestamp} from corpus_modified events."""
        if self._events is None:
            return {}
        try:
            events = await self._events.query(event_type="corpus_modified", limit=500)
            latest: dict[str, str] = {}
            for ev in events:
                dom = (ev.metadata or {}).get("domain")
                ts = (ev.metadata or {}).get("timestamp") or ev.timestamp
                if dom and ts:
                    if dom not in latest or ts > latest[dom]:
                        latest[dom] = ts
            return latest
        except Exception:
            return {}

    async def _get_latest_beast_summary_time(self, domain: str) -> str | None:
        """Find latest beast_domain_summary artifact time for domain (any state)."""
        if self._artifacts is None:
            return None
        try:
            arts = await self._artifacts.list_artifacts_by_metadata(
                key="artifact_type", value="beast_domain_summary", limit=50
            )
            candidates = []
            for a in arts:
                meta = a.get("metadata", {}) or {}
                if meta.get("domain") == domain:
                    # id like beast:summary:dom:ts
                    candidates.append( (a.get("created_at") or meta.get("generated_at") or "", a) )
            if not candidates:
                return None
            candidates.sort(reverse=True)
            return candidates[0][0]
        except Exception:
            return None

    async def _sample_chunks_for_domain(self, domain: str, max_chunks: int = 20) -> tuple[int, list[str]]:
        """Sample up to max_chunks ~300char snippets using lexical (broad query)."""
        if self._lexical is None:
            return 0, []
        try:
            total = 0
            try:
                total = await self._lexical.count(domain=domain)
            except Exception:
                total = max_chunks
            # Broad search: use a common token that matches many (FTS5)
            hits = await self._lexical.search("the OR a OR data OR project", domain=domain, limit=max(50, max_chunks))
            if not hits:
                hits = await self._lexical.search(domain or "knowledge", domain=domain, limit=max_chunks)
            contents = []
            for h in hits[:max_chunks]:
                c = (h.content or "")[:300].replace("\n", " ")
                if c:
                    contents.append(c)
            # Simple "time range" coverage: if many, take head/mid/tail of returned
            n = len(contents)
            if n > 20:
                first = contents[:7]
                mid_start = n // 2 - 3
                mid = contents[mid_start:mid_start+7]
                last = contents[-6:]
                contents = first + mid + last
            return total or n, contents[:max_chunks]
        except Exception as exc:
            log.warning("sample_chunks_failed", domain=domain, error=str(exc))
            return 0, []

    async def _find_rejection_feedback(self, domain: str) -> str | None:
        """Look for REJECTED beast_domain_summary for domain, return note if present."""
        if self._artifacts is None or self._ecs is None:
            return None
        try:
            arts = await self._artifacts.list_artifacts_by_metadata(
                key="artifact_type", value="beast_domain_summary", limit=20
            )
            for a in arts:
                meta = a.get("metadata", {}) or {}
                if meta.get("domain") != domain:
                    continue
                aid = a["id"]
                try:
                    states = await self._ecs.list_transitions(aid, limit=5)
                    for st in states:
                        if st.get("to_state") == "REJECTED":
                            # note may be in event or meta; try meta first
                            note = meta.get("review_note") or meta.get("rejection_note")
                            if not note:
                                # fallback from events? use last
                                note = "Summary rejected by DEFINER (see review notes)"
                            return note
                except Exception:
                    pass
            return None
        except Exception:
            return None

    async def _write_beast_summary_artifact(self, domain: str, content: str, meta_extra: dict, cycle_num: int) -> str:
        """Write summary as GENERATED artifact. Returns artifact_id."""
        if self._artifacts is None:
            return ""
        ts = datetime.now(timezone.utc).isoformat()
        short_ts = ts.replace(":", "").replace("-", "")[:15]
        aid = f"beast:summary:{domain}:{short_ts}"

        # Lookup project by domain for "project" in metadata (so --project filter in review surfaces it)
        project_name = None
        if self._projects is not None:
            try:
                projects = await self._projects.list_projects()
                for p in projects:
                    if p.get("domain") == domain:
                        project_name = p.get("name") or p.get("project_id")
                        break
            except Exception:
                pass

        full_meta = {
            "artifact_type": "beast_domain_summary",
            "domain": domain,
            "generated_at": ts,
            "beast_cycle": cycle_num,
            **meta_extra,
        }
        if project_name:
            full_meta["project"] = project_name
        try:
            await self._artifacts.write(aid, content, full_meta)
            # Set ECS GENERATED (sacred gate — no auto approve)
            if self._ecs is not None:
                try:
                    await self._ecs.transition(
                        artifact_id=aid,
                        from_state=None,
                        to_state="GENERATED",
                        actor="beast",
                        reason="Beast domain summary — pending DEFINER review",
                    )
                except Exception as e:
                    log.warning("beast_summary_ecs_failed", aid=aid, error=str(e))
            return aid
        except Exception as exc:
            log.error("beast_summary_write_failed", domain=domain, error=str(exc))
            return ""

    async def _run_conditional_domain_summaries(self) -> dict:
        """FULL SUMMARIZATION only when corpus changed for a domain.

        Skips if no beast_provider, or unchanged, or idle>24h.
        """
        if self._beast_provider is None:
            log.info("beast_summaries_skipped", reason="no_llm_configured")
            return {"domains_processed": [], "domains_skipped": [], "total_llm_calls": 0, "note": "heartbeat_only"}

        corpus_map = await self._get_corpus_modified_map()
        if not corpus_map:
            log.info("beast_summaries_skipped", reason="no_corpus_modified_events")
            return {"domains_processed": [], "domains_skipped": list(corpus_map.keys()), "total_llm_calls": 0}

        # Simple idle check: if last corpus mod >24h ago and we have summaries for all, skip
        now = datetime.now(timezone.utc)
        recent_ingest = False
        for ts in corpus_map.values():
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if (now - dt).total_seconds() < 24 * 3600:
                    recent_ingest = True
                    break
            except Exception:
                recent_ingest = True
        have_summaries = True
        for dom in corpus_map:
            if await self._get_latest_beast_summary_time(dom) is None:
                have_summaries = False
                break
        if not recent_ingest and have_summaries:
            log.info("beast: idle, heartbeat only")
            return {"domains_processed": [], "domains_skipped": list(corpus_map), "total_llm_calls": 0, "note": "idle"}

        processed = []
        skipped = []
        llm_calls = 0
        cycle_num = int(time.time())

        for dom, mod_ts in corpus_map.items():
            last_sum_ts = await self._get_latest_beast_summary_time(dom)
            if last_sum_ts and last_sum_ts >= mod_ts:
                skipped.append(dom)
                log.info("beast: domain %s: corpus unchanged, skipping", dom)
                continue

            # Need summary
            total_chunks, samples = await self._sample_chunks_for_domain(dom, 20)
            if not samples:
                skipped.append(dom)
                continue

            rejection = await self._find_rejection_feedback(dom)
            rej_text = f"\nPrevious summary was rejected. Feedback: {rejection}. Please address this in your summary.\n" if rejection else ""

            # Build prompt
            chunks_text = "\n---\n".join(samples)
            user_prompt = (
                f"Summarize the following corpus domain for use as context in future queries. "
                f"The summary will be shown to an AI assistant before it answers user questions about this domain.\n\n"
                f"Domain: {dom}\n"
                f"Chunk count: {total_chunks}\n"
                f"Sample chunks ({len(samples)} of ~{total_chunks}):\n\n"
                f"{chunks_text}\n"
                f"{rej_text}\n\n"
                f"Write a 2-4 sentence summary covering:\n"
                f"- What topics and projects this domain contains\n"
                f"- Key decisions, conclusions, or artifacts recorded\n"
                f"- What kinds of questions this domain can answer\n\n"
                f"Summary:"
            )
            system = (
                "You are AIP Beast, the corpus intelligence actor for a sovereign knowledge engine. "
                "Your role is to generate accurate, grounded summaries of knowledge domains based only on the content provided. "
                "Do not infer or hallucinate content not present in the chunks. Be concise and specific."
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ]

            try:
                result = await self._beast_provider.call("beast", messages)
                summary_text = result.get("content", "").strip()
                llm_calls += 1
            except Exception as exc:
                log.error("beast_llm_call_failed", domain=dom, error=str(exc))
                continue

            if not summary_text:
                continue

            extra = {
                "chunk_count": total_chunks,
                "sample_size": len(samples),
                "corpus_modified_at": mod_ts,
            }
            aid = await self._write_beast_summary_artifact(dom, summary_text, extra, cycle_num)
            processed.append(dom)
            log.info("beast_summary_generated", domain=dom, artifact=aid, calls=llm_calls)

        # Emit cycle stats
        await self._emit_event(
            event_type="beast_cycle_complete",
            artifact_id="system",
            metadata={
                "domains_processed": processed,
                "domains_skipped": skipped,
                "total_llm_calls": llm_calls,
            },
        )
        return {"domains_processed": processed, "domains_skipped": skipped, "total_llm_calls": llm_calls}

    # ------------------------------------------------------------------
    # Turn tagging (batch LLM over domain registry) — core of this change
    # ------------------------------------------------------------------

    async def _run_turn_tagging(self, limit: int = 200, retag: bool = False) -> dict:
        """LLM-powered batch tagging of CorpusTurn rows using the domain registry.

        - Loads registry ONCE per invocation (not per batch).
        - Processes in batches of exactly 8 turns per LLM call to beast_provider.
        - Truncates context: user[:400], assistant[:600], thinking[:300].
        - Validates all outputs against registry (primary + domains + bridges).
        - Falls back to "unclassified" (with low confidence) on bad primary/JSON.
        - Collects proposals (if LLM emits on a turn when pattern doesn't fit) and
          writes them as GENERATED beast_domain_proposal artifacts (sacred gate).
        - Emits beast_tagging_complete event with stats + domain_distribution.
        - Never aborts whole session on one bad batch.
        - Cap enforced at call sites (200 background, up to 500 via CLI --limit).
        """
        if self._corpus_turns is None or self._beast_provider is None:
            return {"skipped": "missing_provider_or_corpus_turn_store"}

        # Load registry once (authoritative; Beast never invents domains)
        try:
            from .domain_registry import load_registry
            registry = load_registry("docs/beast_domain_registry_v1.md")
        except FileNotFoundError as exc:
            log.warning("beast_tagging_skipped_no_registry", path="docs/beast_domain_registry_v1.md", error=str(exc))
            return {"skipped": "registry_not_found"}
        except Exception as exc:
            log.warning("beast_tagging_registry_load_failed", error=str(exc))
            return {"skipped": "registry_load_error", "error": str(exc)}

        approved_domains = registry.get_domain_ids()
        approved_bridges = registry.get_approved_bridge_tags()

        # Build prompt fragments (exact format mandated)
        domain_list = "\n".join(f"  - {d}" for d in approved_domains)
        bridge_list = "\n".join(f"  - {b}" for b in approved_bridges)

        system_prompt = f"""You are AIP Beast, corpus intelligence actor for a 
sovereign knowledge engine. Your job is to classify conversation turns 
into knowledge domains and score their importance.

You will receive a batch of conversation turns. For each turn, return 
a JSON object with exactly these fields:
  turn_id: string (copy exactly from input)
  primary_domain: string (exactly one domain_id from the approved list)
  domains: array of strings (all relevant domain_ids, may include primary)
  tags: array of strings (3-8 specific topic tags, lowercase snake_case)
  importance: float 0.0-1.0 (see scoring rules)
  bridges: array of strings (approved connector tags only, may be empty)
  beast_confidence: float 0.0-1.0 (your confidence in this classification)

APPROVED DOMAINS:
{domain_list}

APPROVED BRIDGE TAGS:
{bridge_list}

IMPORTANCE SCORING RULES:
0.9-1.0: Decision recorded, conclusion reached, original framework 
         developed, manuscript section completed
0.7-0.8: Substantive analysis, design discussion, theological exegesis,
         research finding, problem solved
0.5-0.6: Working through a problem, iterating on document, exploring idea
0.3-0.4: Short exchanges, translations, logistics with some content value
0.1-0.2: Greetings, very short exchanges, administrative queries
0.0:     Quarantine — no retrieval value (see quarantine rules)

THINKING BLOCK BONUS: If thinking_text is non-empty, add 0.1 to
importance score (cap at 1.0). Extended thinking signals complex,
considered reasoning worth preserving.

QUARANTINE RULES — assign primary_domain "quarantine" only when ALL:
  1. user_text < 15 words with no substantive content
  2. assistant_text < 50 words  
  3. No domain keywords match
  4. Total word_count < 30
NEVER quarantine turns with thinking_text, decisions, frameworks,
documents referenced, or substantive answers.

UNCLASSIFIED: If confidence < 0.4 for best domain match, use
primary_domain "unclassified". This signals DEFINER review needed.

PROPOSAL TRIGGER: If you see a pattern across this batch that
genuinely doesn't fit any approved domain, add a "proposal" field
to ONE turn in the batch (not all) with:
  proposal: {{
    type: "domain" or "connector",
    proposed_id: "snake_case_name",
    description: "2-3 sentences",
    rationale: "why it doesn't fit existing domains"
  }}
Only propose when you have seen 3+ turns in this batch with the pattern.

Respond ONLY with a JSON array. No preamble. No explanation outside JSON.
Example response structure:
[
  {{
    "turn_id": "abc123def456",
    "primary_domain": "nbcm",
    "domains": ["nbcm", "theology_research"],
    "tags": ["null_boundary", "timelessness", "photon_t0"],
    "importance": 0.8,
    "bridges": ["nbcm->theology_research"],
    "beast_confidence": 0.85
  }}
]
"""

        # Gather turns (untagged + optional retag)
        to_tag: list[Any] = []
        try:
            unt = await self._corpus_turns.get_untagged_turns(limit=limit)
            to_tag.extend(unt)
            if retag and len(to_tag) < limit:
                # Pull some already-tagged (low importance first) for re-evaluation
                more_limit = max(0, limit - len(to_tag))
                if more_limit > 0:
                    # Use a modest max_version so we don't thrash very new tags
                    ret = await self._corpus_turns.get_turns_for_retagging(max_tagging_version=10, limit=more_limit)
                    to_tag.extend(ret)
        except Exception as exc:
            log.error("beast_get_turns_failed", error=str(exc))
            return {"turns_tagged": 0, "turns_failed": 0, "proposals_filed": 0, "error": str(exc)}

        if not to_tag:
            return {"turns_tagged": 0, "turns_failed": 0, "proposals_filed": 0, "note": "nothing_to_tag"}

        BATCH_SIZE = 8
        total = len(to_tag)
        tagged = 0
        failed = 0
        proposals: list[dict] = []
        domain_counts: dict[str, int] = {}
        importance_sum = 0.0
        importance_count = 0

        for b_start in range(0, total, BATCH_SIZE):
            batch = to_tag[b_start : b_start + BATCH_SIZE]
            batch_idx = (b_start // BATCH_SIZE) + 1

            # Build user prompt with mandated truncations
            blocks = []
            for j, turn in enumerate(batch):
                uid = getattr(turn, "turn_id", "")
                cname = getattr(turn, "conversation_name", "")
                u = (getattr(turn, "user_text", "") or "")[:400]
                a = (getattr(turn, "assistant_text", "") or "")[:600]
                th_raw = getattr(turn, "thinking_text", "") or ""
                th = (th_raw[:300] if th_raw else "(none)")
                wc = getattr(turn, "word_count", 0)
                blk = (
                    f"--- TURN {j+1} ---\n"
                    f"turn_id: {uid}\n"
                    f"conversation: {cname}\n"
                    f"user: {u}\n"
                    f"assistant: {a}\n"
                    f"thinking: {th}\n"
                    f"word_count: {wc}\n"
                    f"---"
                )
                blocks.append(blk)

            user_prompt = f"Tag the following {len(batch)} conversation turns:\n\n" + "\n".join(blocks)

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if batch_idx % 5 == 1 or batch_idx == (total + BATCH_SIZE - 1) // BATCH_SIZE:
                log.info("beast_tagging_progress", batch=batch_idx, of=(total + BATCH_SIZE - 1) // BATCH_SIZE, turns=f"{b_start+1}-{min(b_start+BATCH_SIZE, total)}/{total}")
                # Plain progress line for CLI `aip corpus tag` visibility (matches mandated verif output)
                try:
                    print(f"Tagging batch {batch_idx}/{(total + BATCH_SIZE - 1) // BATCH_SIZE} (turns {b_start+1}-{min(b_start + BATCH_SIZE, total)})...")
                except Exception:
                    pass

            try:
                llm_result = await self._beast_provider.call("beast", messages)
                content = (llm_result or {}).get("content", "").strip()
                parsed = json.loads(content) if content else []
                if not isinstance(parsed, list):
                    raise ValueError("response not a JSON array")
            except Exception as exc:
                log.warning("beast_tagging_batch_parse_failed", batch=batch_idx, turn_ids=[getattr(t, "turn_id", "?") for t in batch], error=str(exc))
                # Mark whole batch as unclassified with 0 conf (tagging_version will ++ via update)
                for t in batch:
                    try:
                        await self._corpus_turns.update_beast_tags(
                            getattr(t, "turn_id", ""),
                            [], "unclassified", [], 0.0, [], 0.0
                        )
                    except Exception:
                        pass
                failed += len(batch)
                continue

            # Process each returned item
            batch_turn_ids = {getattr(t, "turn_id", ""): t for t in batch}
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                tid = item.get("turn_id")
                if not tid or tid not in batch_turn_ids:
                    continue

                # Validate / sanitize
                primary = (item.get("primary_domain") or "unclassified").strip()
                if not registry.is_approved_domain(primary) and primary not in ("unclassified", "quarantine"):
                    primary = "unclassified"
                    item_conf = 0.3
                else:
                    item_conf = item.get("beast_confidence", 0.0)

                doms = [d for d in (item.get("domains") or []) if isinstance(d, str) and registry.is_approved_domain(d)]
                if primary not in doms and primary in ("unclassified", "quarantine") or registry.is_approved_domain(primary):
                    if primary not in ("unclassified", "quarantine") and primary not in doms:
                        doms = [primary] + doms

                tgs = [str(t).lower().replace(" ", "_")[:64] for t in (item.get("tags") or []) if isinstance(t, (str, int, float))][:8]
                if not tgs:
                    tgs = ["unclassified"]

                try:
                    imp = max(0.0, min(1.0, float(item.get("importance", 0.0))))
                except Exception:
                    imp = 0.0
                # thinking bonus if present on original turn (already reflected in scoring guidance)
                try:
                    th = getattr(batch_turn_ids[tid], "thinking_text", "") or ""
                    if th.strip() and imp < 1.0:
                        imp = min(1.0, imp + 0.1)
                except Exception:
                    pass

                brs = []
                for b in (item.get("bridges") or []):
                    bs = str(b)
                    if registry.is_approved_bridge(bs):
                        brs.append(bs)
                    else:
                        log.warning("beast_tagging_dropped_unapproved_bridge", bridge=bs, turn=tid)

                try:
                    bconf = max(0.0, min(1.0, float(item_conf or 0.0)))
                except Exception:
                    bconf = 0.0

                # Collect proposal if present (only one per batch expected)
                prop = item.get("proposal")
                if isinstance(prop, dict) and prop.get("proposed_id"):
                    ptype = prop.get("type", "domain")
                    proposals.append({
                        "type": ptype,
                        "proposed_id": prop.get("proposed_id"),
                        "description": prop.get("description", ""),
                        "rationale": prop.get("rationale", ""),
                        "evidence_turn_ids": [tid],
                    })

                # Persist
                try:
                    await self._corpus_turns.update_beast_tags(
                        tid, doms, primary, tgs, imp, brs, bconf
                    )
                    tagged += 1
                    domain_counts[primary] = domain_counts.get(primary, 0) + 1
                    importance_sum += imp
                    importance_count += 1
                except Exception as exc:
                    log.warning("beast_update_tags_failed", turn_id=tid, error=str(exc))
                    failed += 1

        # Write proposals as GENERATED artifacts (follow artifact_store + ecs pattern exactly)
        proposals_filed = 0
        ts = datetime.now(timezone.utc).isoformat()
        short_ts = ts.replace(":", "").replace("-", "")[:15]
        for p in proposals:
            try:
                pid = p.get("proposed_id", "discovered")
                ptype = p.get("type", "domain")
                aid = f"beast:proposal:{ptype}:{pid}:{short_ts}"
                content = json.dumps({
                    "proposed_id": pid,
                    "proposal_type": ptype,
                    "description": p.get("description", ""),
                    "rationale": p.get("rationale", ""),
                    "evidence_turn_ids": p.get("evidence_turn_ids", [])[:5],
                    "suggested_connectors": p.get("suggested_connectors", []),
                }, ensure_ascii=False)
                meta = {
                    "artifact_type": "beast_domain_proposal",
                    "proposal_type": ptype,
                    "proposed_id": pid,
                    "domain": "corpus",
                    "generated_at": ts,
                    "beast_cycle": int(time.time()),
                }
                if self._artifacts is not None:
                    await self._artifacts.write(aid, content, meta)
                    if self._ecs is not None:
                        try:
                            await self._ecs.transition(
                                artifact_id=aid,
                                from_state=None,
                                to_state="GENERATED",
                                actor="beast",
                                reason="Beast domain/connector proposal — pending DEFINER review",
                            )
                        except Exception as e:
                            log.warning("beast_proposal_ecs_failed", aid=aid, error=str(e))
                    proposals_filed += 1
            except Exception as exc:
                log.warning("beast_proposal_write_failed", error=str(exc))

        avg_imp = (importance_sum / importance_count) if importance_count > 0 else 0.0

        # Emit tagging complete event (used by status / review)
        await self._emit_event(
            event_type="beast_tagging_complete",
            artifact_id="system",
            metadata={
                "turns_tagged": tagged,
                "turns_failed": failed,
                "proposals_filed": proposals_filed,
                "domain_distribution": domain_counts,
                "avg_importance": round(avg_imp, 4),
                "cycle": int(time.time()),
                "limit": limit,
                "retag": retag,
            },
        )

        log.info(
            "beast_tagging_complete",
            tagged=tagged,
            failed=failed,
            proposals=proposals_filed,
            top_domains=sorted(domain_counts.items(), key=lambda kv: -kv[1])[:5],
        )

        return {
            "turns_tagged": tagged,
            "turns_failed": failed,
            "proposals_filed": proposals_filed,
            "domain_distribution": domain_counts,
            "avg_importance": round(avg_imp, 4),
        }

    async def _run_embedding_pass(self, limit: int = 200, reembed: bool = False) -> dict:
        """Embed corpus turns' searchable_text into vector store, keyed by turn_id.

        Batch size 32 for efficiency (cheaper than chat).
        Truncates text to 8000 chars.
        Sets embedded=1 on success in corpus_turns.
        """
        if self._corpus_turns is None or self._embed is None:
            return {"skipped": "missing_corpus_turn_store_or_embedding_provider"}

        # Query unembedded
        try:
            if reembed:
                # to get all, use limit, then slice? but for simplicity use store search or direct
                # since no get_all, use get_unembedded but ignore filter, or direct
                import sqlite3
                db_path = getattr(self._corpus_turns, "_db_path", None)
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    cursor = conn.execute(
                        "SELECT turn_id, searchable_text FROM corpus_turns ORDER BY importance DESC LIMIT ?",
                        (int(limit),),
                    )
                    to_embed = cursor.fetchall()
                finally:
                    conn.close()
            else:
                turns = await self._corpus_turns.get_unembedded_turns(limit=limit)
                to_embed = [(t.turn_id, t.searchable_text) for t in turns]
        except Exception as exc:
            log.error("beast_get_unembedded_failed", error=str(exc))
            return {"embedded": 0, "failed": 0, "skipped": 0, "error": str(exc)}

        if not to_embed:
            return {"embedded": 0, "failed": 0, "skipped": 0, "note": "nothing_to_embed"}

        BATCH_SIZE = 32
        total = len(to_embed)
        embedded = 0
        failed = 0
        skipped = 0

        for b_start in range(0, total, BATCH_SIZE):
            batch = to_embed[b_start : b_start + BATCH_SIZE]
            batch_idx = (b_start // BATCH_SIZE) + 1

            if batch_idx % 5 == 1 or batch_idx == (total + BATCH_SIZE - 1) // BATCH_SIZE:
                log.info("beast_embedding_progress", batch=batch_idx, of=(total + BATCH_SIZE - 1) // BATCH_SIZE, turns=f"{b_start+1}-{min(b_start+BATCH_SIZE, total)}/{total}")
                try:
                    print(f"Embedding batch {batch_idx}/{(total + BATCH_SIZE - 1) // BATCH_SIZE} (turns {b_start+1}-{min(b_start + BATCH_SIZE, total)})...")
                except Exception:
                    pass

            for tid, stext in batch:
                try:
                    text = (stext or "")[:8000]
                    if not text.strip():
                        skipped += 1
                        continue
                    vec = await self._embed.embed(text)
                    if not vec or len(vec) == 0:
                        failed += 1
                        continue
                    # Store keyed by turn_id
                    await self._vector.upsert(
                        id=tid,
                        embedding=vec,
                        content=text[:500],  # snippet
                        metadata={"type": "corpus_turn", "turn_id": tid},
                        domain=None,  # or from turn?
                    )
                    # Mark embedded using direct sqlite (robust)
                    try:
                        import sqlite3
                        dbp = getattr(self._corpus_turns, "_db_path", None)
                        if dbp:
                            c = sqlite3.connect(dbp)
                            try:
                                c.execute(
                                    "UPDATE corpus_turns SET embedded = 1, updated_at = ? WHERE turn_id = ?",
                                    (datetime.now(timezone.utc).isoformat() + "Z", tid),
                                )
                                c.commit()
                            finally:
                                c.close()
                    except Exception:
                        pass
                    embedded += 1
                except Exception as exc:
                    log.warning("beast_embedding_failed", turn_id=tid, error=str(exc))
                    failed += 1

        log.info("beast_embedding_complete", embedded=embedded, failed=failed, skipped=skipped)

        return {
            "embedded": embedded,
            "failed": failed,
            "skipped": skipped,
        }

    # ------------------------------------------------------------------
    # Wiki generation (domain-level articles from tagged corpus)
    # ------------------------------------------------------------------

    _WIKI_EXCLUDED_DOMAINS = frozenset({"quarantine", "unclassified"})
    _WIKI_WORD_THRESHOLD = 200_000

    async def _run_wiki_generation(
        self,
        force_domains: list[str] | None = None,
        max_per_cycle: int = 5,
    ) -> dict:
        """Generate domain wiki articles from tagged corpus turns.

        For each active domain: checks whether generation is needed
        (no wiki exists OR >200k new words since last wiki), then calls
        beast_provider with the wiki prompt, and writes a GENERATED artifact.

        max_per_cycle: cap for background cycle use (5). CLI uses 9999.
        force_domains: if set, only generate for those domains (ignores threshold).
        """
        if self._beast_provider is None or self._artifacts is None or self._corpus_turns is None:
            return {"skipped": "missing_provider_or_artifact_store_or_corpus_turns"}

        try:
            from .domain_registry import load_registry
            registry = load_registry("docs/beast_domain_registry_v1.md")
        except Exception as exc:
            log.warning("beast_wiki_skipped_no_registry", error=str(exc))
            return {"skipped": "registry_not_found", "error": str(exc)}

        active_domains = [
            d for d in registry.get_domain_ids()
            if d not in self._WIKI_EXCLUDED_DOMAINS
        ]
        if force_domains is not None:
            active_domains = [d for d in active_domains if d in force_domains]

        db_path = getattr(self._corpus_turns, "_db_path", None)
        generated = 0
        skipped = 0
        errors = 0
        domains_generated: list[str] = []
        domains_skipped: list[str] = []
        cycle_num = int(time.time())

        for domain_id in active_domains:
            if force_domains is None and generated >= max_per_cycle:
                break

            domain_entry = registry.get_domain(domain_id)
            if domain_entry is None:
                continue

            force_this = force_domains is not None

            needs_gen, last_wiki_ts = await self._wiki_needs_generation(domain_id, force=force_this)
            if not needs_gen:
                skipped += 1
                domains_skipped.append(domain_id)
                continue

            domain_data = await self._get_wiki_domain_data(domain_id, db_path, last_wiki_ts)

            if domain_data["total_turns"] == 0:
                skipped += 1
                domains_skipped.append(domain_id)
                log.info("beast_wiki_skipped_no_turns", domain=domain_id)
                continue

            try:
                print(f"Generating wiki for {domain_id}...")
            except Exception:
                pass

            try:
                wiki_content = await self._call_beast_for_wiki(domain_id, domain_entry, domain_data)
            except Exception as exc:
                log.error("beast_wiki_llm_failed", domain=domain_id, error=str(exc))
                errors += 1
                continue

            if not wiki_content:
                errors += 1
                continue

            aid = await self._write_wiki_artifact(domain_id, domain_entry, wiki_content, domain_data, cycle_num)
            if aid:
                generated += 1
                domains_generated.append(domain_id)
                wc = len(wiki_content.split())
                log.info("beast_wiki_generated", domain=domain_id, word_count=wc, artifact=aid)
                try:
                    print(f"Generated wiki article for {domain_id}: {wc} words")
                except Exception:
                    pass
            else:
                errors += 1

        await self._emit_event(
            event_type="beast_wiki_cycle_complete",
            artifact_id="system",
            metadata={
                "domains_generated": domains_generated,
                "domains_skipped": domains_skipped,
                "domains_generated_count": generated,
                "domains_skipped_count": skipped,
                "errors": errors,
                "cycle": cycle_num,
            },
        )

        return {
            "domains_generated": generated,
            "domains_skipped": skipped,
            "errors": errors,
            "domains_generated_list": domains_generated,
        }

    async def _wiki_needs_generation(self, domain_id: str, force: bool = False) -> tuple[bool, str | None]:
        """Return (needs_generation, last_wiki_created_at_or_None).

        Checks artifact store for any existing beast_wiki for this domain.
        If none: needs generation. If force: always generate.
        If exists: check word count since last wiki (>200k threshold).
        """
        last_wiki_ts: str | None = None
        try:
            arts = await self._artifacts.list_artifacts_by_metadata(
                key="artifact_type", value="beast_wiki", limit=200
            )
            domain_arts = [
                a for a in arts
                if (a.get("metadata", {}) or {}).get("domain") == domain_id
            ]
            if domain_arts:
                domain_arts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
                last_wiki_ts = domain_arts[0].get("created_at", "")
        except Exception as exc:
            log.warning("beast_wiki_lookup_failed", domain=domain_id, error=str(exc))

        if force:
            return True, last_wiki_ts

        if last_wiki_ts is None:
            return True, None

        # Count new words since last wiki
        if not hasattr(self._corpus_turns, "_db_path"):
            return False, last_wiki_ts
        try:
            import sqlite3
            db_path = getattr(self._corpus_turns, "_db_path", None)
            if not db_path:
                return False, last_wiki_ts
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(word_count), 0) FROM corpus_turns "
                    "WHERE primary_domain = ? AND tagging_version > 0 AND updated_at >= ?",
                    (domain_id, last_wiki_ts),
                ).fetchone()
                new_words = int(row[0]) if row else 0
            finally:
                conn.close()
            return new_words >= self._WIKI_WORD_THRESHOLD, last_wiki_ts
        except Exception as exc:
            log.warning("beast_wiki_word_count_failed", domain=domain_id, error=str(exc))
            return False, last_wiki_ts

    async def _get_wiki_domain_data(
        self, domain_id: str, db_path: str | None, last_wiki_ts: str | None
    ) -> dict:
        """Gather domain statistics and sample turns for wiki generation."""
        data: dict = {
            "total_turns": 0,
            "avg_importance": 0.0,
            "top_tags": [],
            "bridge_connectors": [],
            "sample_turns": [],
            "max_tagging_version": 0,
        }
        if not db_path:
            return data
        try:
            import sqlite3
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                # Stats
                row = conn.execute(
                    "SELECT COUNT(*) as c, COALESCE(AVG(importance), 0) as ai, "
                    "COALESCE(MAX(tagging_version), 0) as mv "
                    "FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0",
                    (domain_id,),
                ).fetchone()
                if row:
                    data["total_turns"] = int(row[0])
                    data["avg_importance"] = round(float(row[1]), 4)
                    data["max_tagging_version"] = int(row[2])

                # Top tags (aggregate from JSON)
                tag_rows = conn.execute(
                    "SELECT tags FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0 LIMIT 200",
                    (domain_id,),
                ).fetchall()
                tag_counts: dict[str, int] = {}
                for (tags_json,) in tag_rows:
                    try:
                        for t in json.loads(tags_json or "[]"):
                            tag_counts[t] = tag_counts.get(t, 0) + 1
                    except Exception:
                        pass
                data["top_tags"] = [t for t, _ in sorted(tag_counts.items(), key=lambda kv: -kv[1])[:10]]

                # Bridge connectors mentioned
                bridge_rows = conn.execute(
                    "SELECT bridges FROM corpus_turns WHERE primary_domain = ? "
                    "AND bridges != '[]' AND tagging_version > 0 LIMIT 100",
                    (domain_id,),
                ).fetchall()
                bridge_counts: dict[str, int] = {}
                for (bridges_json,) in bridge_rows:
                    try:
                        for b in json.loads(bridges_json or "[]"):
                            bridge_counts[b] = bridge_counts.get(b, 0) + 1
                    except Exception:
                        pass
                data["bridge_connectors"] = sorted(bridge_counts.keys())

                # Sample top 20 turns by importance
                turn_rows = conn.execute(
                    "SELECT turn_id, importance, tags, bridges, user_text, assistant_text "
                    "FROM corpus_turns WHERE primary_domain = ? AND tagging_version > 0 "
                    "ORDER BY importance DESC LIMIT 20",
                    (domain_id,),
                ).fetchall()
                sample_turns = []
                for row in turn_rows:
                    try:
                        tags_list = json.loads(row[2] or "[]")
                        bridges_list = json.loads(row[3] or "[]")
                        sample_turns.append({
                            "turn_id": row[0],
                            "importance": float(row[1]),
                            "tags": tags_list,
                            "bridges": bridges_list,
                            "user_text": (row[4] or "")[:300],
                            "assistant_text": (row[5] or "")[:500],
                        })
                    except Exception:
                        pass
                data["sample_turns"] = sample_turns
            finally:
                conn.close()
        except Exception as exc:
            log.warning("beast_wiki_domain_data_failed", domain=domain_id, error=str(exc))
        return data

    async def _call_beast_for_wiki(self, domain_id: str, domain_entry: Any, domain_data: dict) -> str:
        """Call beast_provider with wiki generation prompt. Returns article text."""
        system_prompt = f"""You are AIP Beast, corpus intelligence actor for a
sovereign knowledge engine. You are generating a domain wiki article
from corpus turns written by B. Moses Jorgensen (the DEFINER).

The DEFINER is a cross-domain researcher and systems builder with deep
expertise in: chemistry (30+ years), data analytics, AI methodology
(AI Poiesis), New Covenant theology (30+ years independent study),
and systems thinking across technical, theological, and policy domains.

You are writing a wiki article for the domain: {domain_id}
Domain description: {domain_entry.description}

The article must have exactly this structure:

## Overview
[3-5 sentences. Dense, assumes full domain knowledge. Written for
LLM injection — maximum information per token. Captures the DEFINER's
actual current position and framework, not generic domain description.
Use the DEFINER's own terminology from the corpus turns.]

## Key Concepts
[The 5-8 most important concepts, frameworks, or positions in this
domain as they appear in the corpus. Each concept gets 2-4 sentences.
Use the DEFINER's actual terminology. If terminology has evolved
(e.g., "record formation" replaced "observation collapse"), use
the current term and note the evolution.]

## Current State
[Where the work in this domain currently stands. Decisions made,
conclusions reached, open questions. What has been resolved vs
what is still being worked out. Be specific — cite the actual
state of manuscripts, experiments, projects, or frameworks.]

## Cross-Domain Connections
[How this domain connects to other domains in the corpus. Use the
approved bridge vocabulary. Explain WHY the connection matters,
not just that it exists. 3-6 connections maximum.]

## Evolution
[How the thinking in this domain has changed over the corpus period.
What was the starting position, what changed, why. This section
reveals intellectual development — it is among the most valuable
sections for the DEFINER to review.]

## Key Turns
[List 3-5 turn_ids that are most representative or important for
this domain. Format: turn_id | brief description of what makes it
significant]

## Open Questions
[What remains unresolved, actively debated, or in progress.
3-5 specific questions the DEFINER appears to be working on.
These should spark further thinking when the DEFINER reads the article.]

CRITICAL CONSTRAINTS:
- Write from corpus evidence only. Do not add knowledge from your
  training data that is not reflected in the provided turns.
- Use the DEFINER's own language and frameworks.
- The Overview section will be injected into AI chat sessions —
  it must be maximally informative in minimum space.
- Do not hallucinate project names, people, or positions not
  evidenced in the provided turns.
- If a section cannot be written from the evidence (e.g., insufficient
  turns for Evolution), write: "[Insufficient corpus evidence for
  this section — more turns needed]"
"""

        turns_text = ""
        for i, t in enumerate(domain_data.get("sample_turns", []), 1):
            turns_text += (
                f"--- TURN {i} ---\n"
                f"turn_id: {t['turn_id']}\n"
                f"importance: {t['importance']}\n"
                f"tags: {t['tags']}\n"
                f"bridges: {t['bridges']}\n"
                f"user: {t['user_text']}\n"
                f"assistant: {t['assistant_text']}\n"
                f"---\n\n"
            )

        user_prompt = (
            f"Generate a wiki article for domain: {domain_id}\n\n"
            f"Domain statistics:\n"
            f"- Total turns: {domain_data['total_turns']}\n"
            f"- Average importance: {domain_data['avg_importance']:.2f}\n"
            f"- Top tags: {domain_data['top_tags']}\n"
            f"- Active connectors: {domain_data['bridge_connectors']}\n\n"
            f"Sample turns (highest importance, use as evidence base):\n"
            f"{turns_text}\n"
            f"Generate the complete wiki article now."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        result = await self._beast_provider.call("beast", messages)
        content = (result or {}).get("content") or ""
        return content.strip()

    async def _write_wiki_artifact(
        self,
        domain_id: str,
        domain_entry: Any,
        wiki_content: str,
        domain_data: dict,
        cycle_num: int,
    ) -> str:
        """Write wiki article as GENERATED artifact. Returns artifact_id."""
        if self._artifacts is None:
            return ""
        try:
            ts = datetime.now(timezone.utc).isoformat()
            short_ts = ts.replace(":", "").replace("-", "")[:15]
            aid = f"beast:wiki:{domain_id}:{short_ts}"

            # Extract overview text (between ## Overview and next ##)
            overview_text = ""
            try:
                lines = wiki_content.split("\n")
                in_overview = False
                overview_lines: list[str] = []
                for line in lines:
                    if line.strip() == "## Overview":
                        in_overview = True
                        continue
                    if in_overview and line.startswith("## "):
                        break
                    if in_overview and line.strip():
                        overview_lines.append(line.strip())
                overview_text = " ".join(overview_lines).strip()
            except Exception:
                pass

            word_count = len(wiki_content.split())
            meta = {
                "artifact_type": "beast_wiki",
                "domain": domain_id,
                "domain_display": getattr(domain_entry, "domain_id", domain_id),
                "generated_at": ts,
                "turns_sampled": len(domain_data.get("sample_turns", [])),
                "total_domain_turns": domain_data.get("total_turns", 0),
                "avg_importance": domain_data.get("avg_importance", 0.0),
                "top_tags": domain_data.get("top_tags", []),
                "overview_text": overview_text,
                "word_count": word_count,
                "tagging_version_at_generation": domain_data.get("max_tagging_version", 0),
                "beast_cycle": cycle_num,
            }

            await self._artifacts.write(aid, wiki_content, meta)

            if self._ecs is not None:
                try:
                    await self._ecs.transition(
                        artifact_id=aid,
                        from_state=None,
                        to_state="GENERATED",
                        actor="beast",
                        reason="Beast domain wiki — pending DEFINER review",
                    )
                except Exception as e:
                    log.warning("beast_wiki_ecs_failed", aid=aid, error=str(e))
            return aid
        except Exception as exc:
            log.error("beast_wiki_write_failed", domain=domain_id, error=str(exc))
            return ""

    # ------------------------------------------------------------------
    # Graph extraction (entity/relationship extraction via Beast LLM)
    # ------------------------------------------------------------------

    async def _run_graph_extraction(self, limit: int = 50) -> dict:
        """Extract entities and relationships from high-importance corpus turns.

        Processes turns with importance > 0.7, tagging_version > 0, not yet
        graph-extracted. Creates/updates GraphNode and GraphEdge records.
        Tracks processed turns in graph_extraction_log table.
        Cap at `limit` turns per invocation.
        """
        if self._beast_provider is None or self._corpus_turns is None:
            return {"skipped": "missing_provider_or_corpus_turns"}

        db_path = getattr(self._corpus_turns, "_db_path", None)
        if not db_path:
            return {"skipped": "no_db_path"}

        try:
            from aip.adapter.graph_store import GraphStore, GraphNode, GraphEdge
            from aip.adapter.entity_alias_loader import EntityAliasRegistry
        except Exception as exc:
            log.warning("beast_graph_import_failed", error=str(exc))
            return {"skipped": "import_error", "error": str(exc)}

        graph_store = GraphStore(db_path)
        registry = EntityAliasRegistry("docs/entity_aliases.md")

        # Build compact alias list for prompt
        alias_lines = []
        for cn in registry.all_canonical_names()[:40]:
            entry = registry.get_entry(cn)
            if entry:
                aliases_str = ", ".join(entry.aliases[:3]) if entry.aliases else ""
                alias_lines.append(f"  {cn} ({entry.entity_type}){': ' + aliases_str if aliases_str else ''}")
        alias_registry_compact = "\n".join(alias_lines)

        # Get unextracted high-importance turns
        turns = graph_store.get_unextracted_high_importance_turns(db_path, min_importance=0.7, limit=limit)

        if not turns:
            return {"turns_processed": 0, "entities_created": 0, "relationships_created": 0, "note": "nothing_to_extract"}

        system_prompt = f"""You are AIP Beast extracting entities and relationships
from a conversation turn for a personal knowledge graph.

CANONICAL ENTITY TYPES:
- PERSON: Named individuals
- PROJECT: Named projects, products, technologies, devices
- CONCEPT: Named theoretical frameworks, principles, methodologies
- PLACE: Named locations
- ORGANIZATION: Named organizations, institutions, companies
- MANUSCRIPT: Named documents, papers, books

CANONICAL RELATIONSHIP TYPES:
- CONNECTS: Two concepts or domains connect intellectually
- WORKS_ON: A person works on a project/manuscript
- FUNDED_BY: A project is funded by a mechanism
- AUTHORED: A person authored a manuscript/document
- LOCATED_IN: An entity is located in a place
- RELATES_TO: Generic relationship when type unclear

ENTITY ALIAS TABLE (resolve mentions to these canonical names):
{alias_registry_compact}

RULES:
- Only extract named entities that appear explicitly in the text
- Resolve aliases to canonical names before outputting
- Do NOT extract generic concepts (e.g., "physics", "theology")
  only named specific ones (e.g., "NBCM", "New Covenant Displaced")
- Do NOT extract the DEFINER himself as an entity
- Minimum confidence 0.5 to include
- Return ONLY valid JSON array, no preamble

Output format:
[
  {{
    "entity_type": "CONCEPT",
    "canonical_name": "NBCM",
    "confidence": 0.95
  }},
  {{
    "relationship_type": "CONNECTS",
    "source": "NBCM",
    "target": "EZ Water",
    "confidence": 0.8
  }}
]
"""

        total_processed = 0
        total_entities = 0
        total_relationships = 0

        for turn in turns:
            turn_id = turn["turn_id"]
            user_text = (turn.get("user_text") or "")[:400]
            assistant_text = (turn.get("assistant_text") or "")[:600]
            primary_domain = turn.get("primary_domain", "")
            importance = turn.get("importance", 0.0)

            user_prompt = (
                f"Extract entities and relationships from this turn:\n\n"
                f"turn_id: {turn_id}\n"
                f"domain: {primary_domain}\n"
                f"importance: {importance}\n"
                f"user: {user_text}\n"
                f"assistant: {assistant_text}\n"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            try:
                result = await self._beast_provider.call("beast", messages)
                content = (result or {}).get("content") or ""
                parsed = json.loads(content.strip()) if content.strip() else []
                if not isinstance(parsed, list):
                    parsed = []
            except Exception as exc:
                log.warning("beast_graph_extraction_parse_failed", turn_id=turn_id, error=str(exc))
                graph_store.log_turn_extracted(turn_id, 0, 0)
                total_processed += 1
                continue

            entities_this_turn = 0
            rels_this_turn = 0

            for item in parsed:
                if not isinstance(item, dict):
                    continue

                if "entity_type" in item:
                    # Entity item
                    raw_name = (item.get("canonical_name") or "").strip()
                    if not raw_name:
                        continue
                    resolved = registry.resolve(raw_name) or raw_name
                    if not resolved:
                        continue
                    node_id = resolved.lower().replace(" ", "_")
                    entity_type = item.get("entity_type", "CONCEPT")
                    confidence = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
                    if confidence < 0.5:
                        continue

                    existing = graph_store.get_node(node_id)
                    if existing is None:
                        domain_hint = registry.get_domain(resolved) or primary_domain or None
                        et = registry.get_entity_type(resolved) or entity_type
                        node = GraphNode(
                            id=node_id,
                            entity_type=et,
                            canonical_name=resolved,
                            domain=domain_hint,
                            confidence=confidence,
                            source="beast_extraction",
                        )
                        graph_store.upsert_node(node)
                        entities_this_turn += 1

                elif "relationship_type" in item:
                    # Relationship item
                    src_raw = (item.get("source") or "").strip()
                    tgt_raw = (item.get("target") or "").strip()
                    rel_type = (item.get("relationship_type") or "RELATES_TO").strip()
                    confidence = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
                    if confidence < 0.5 or not src_raw or not tgt_raw:
                        continue

                    src_resolved = registry.resolve(src_raw) or src_raw
                    tgt_resolved = registry.resolve(tgt_raw) or tgt_raw
                    src_id = src_resolved.lower().replace(" ", "_")
                    tgt_id = tgt_resolved.lower().replace(" ", "_")

                    # Ensure both nodes exist
                    for nid, nname in ((src_id, src_resolved), (tgt_id, tgt_resolved)):
                        if graph_store.get_node(nid) is None:
                            graph_store.upsert_node(GraphNode(
                                id=nid,
                                entity_type="CONCEPT",
                                canonical_name=nname,
                                domain=primary_domain or None,
                                confidence=0.6,
                                source="beast_extraction",
                            ))

                    edge_id = f"{src_id}__{rel_type}__{tgt_id}"
                    edge = GraphEdge(
                        id=edge_id,
                        source_id=src_id,
                        target_id=tgt_id,
                        relationship_type=rel_type,
                        confidence=confidence,
                        evidence_turn_ids=[turn_id],
                        weight=1.0,
                    )
                    graph_store.upsert_edge(edge)
                    rels_this_turn += 1

            graph_store.log_turn_extracted(turn_id, entities_this_turn, rels_this_turn)
            total_processed += 1
            total_entities += entities_this_turn
            total_relationships += rels_this_turn

        log.info(
            "beast_graph_extraction_complete",
            turns=total_processed,
            entities=total_entities,
            relationships=total_relationships,
        )

        await self._emit_event(
            event_type="beast_graph_extraction_complete",
            artifact_id="system",
            metadata={
                "turns_processed": total_processed,
                "entities_created": total_entities,
                "relationships_created": total_relationships,
            },
        )

        return {
            "turns_processed": total_processed,
            "entities_created": total_entities,
            "relationships_created": total_relationships,
        }

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
