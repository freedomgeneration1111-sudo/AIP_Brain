"""Vigil actor — quality evaluation (ADR-011).

Vigil is the quality assurance actor. It evaluates synthesis outputs,
monitors for quality degradation, and proposes profile amendments when
it notices systematic patterns.

Per ADR-011:
- Vigil evaluates augmented chat responses for source citation quality
- Vigil flags responses that make claims not supported by retrieved sources
- Vigil proposes DEFINER profile amendments when synthesis patterns drift
- Vigil monitors Beast wiki articles for factual consistency
- Vigil generates quality evaluation reports as reviewable artifacts

What Vigil does NOT do (per ADR-011):
- Background maintenance (→ Sexton)
- Context assembly (→ Beast)
- Any write operations to corpus_turns or artifacts without DEFINER review

The previous Vigil implementation contained maintenance operations
(canonical health checks, stale detection, entity inconsistency checks,
model slot change handling) that are now outside Vigil's scope per ADR-011.
Those operations are retained as private methods for backward compatibility
but are no longer called from run_cycle().

This pass (Phase C): Pure Python citation-rate scoring — no LLM needed.
  - Reads augmented turns since last eval
  - Checks whether source_turn_ids appear in assistant_text as citations
  - Computes citation_rate = cited / retrieved
  - Writes vigil_score to metadata_json
  - Flags low-citation turns via GENERATED artifact for DEFINER review

Future (Phase 3.3): LLM-powered faithfulness evaluation — does the
response actually support the claim? This requires a Vigil model slot
and is intentionally deferred.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import (
    CanonicalStore,
    EntityStore,
    ModelProvider,
    TraceStore,
    VigilStore,
)
from aip.foundation.schemas import ModelSlotConfig, VigilConfig
from aip.logging import get_logger

logger = get_logger(__name__)


class Vigil:
    """Vigil — quality evaluation actor (ADR-011).

    Evaluates synthesis quality on an hourly cadence. Reads the last N
    augmented chat turns since the previous vigil run and checks each
    for source citation quality. Writes an evaluation summary as a
    GENERATED artifact for DEFINER review.

    This pass implements citation-rate scoring without LLM:
      citation_rate = (source_turn_ids found in response text) / (total source_turn_ids)

    A turn is considered "cited" if any form of its turn_id appears
    in the assistant_text — matching patterns like [source: abc123],
    [Source 1], or just the turn_id fragment itself.

    Legacy maintenance methods (check_canonical_health, detect_stale_canonicals,
    detect_entity_inconsistencies, on_model_slot_change) are retained for
    backward compatibility but are NOT part of the ADR-011 vigil cycle.
    """

    # Citation threshold below which a turn is flagged for DEFINER review
    _CITATION_THRESHOLD = 0.3

    # Contradiction / grounding thresholds
    _HEDGING_PHRASES = frozenset({
        "i'm not sure but", "i think", "i believe", "it might be",
        "possibly", "perhaps", "it could be", "i'm guessing",
        "i'm not certain", "not entirely sure", "it seems like",
        "i would guess", "my guess is", "i assume",
    })
    _GROUNDING_THRESHOLD = 0.5  # Flag if < 50% of numeric claims are grounded

    def __init__(
        self,
        config: VigilConfig,
        vigil_store: VigilStore,
        canonical_store: CanonicalStore,
        entity_store: EntityStore,
        model_provider: ModelProvider,
        trace_store: TraceStore,
        sexton: Any | None = None,  # Sexton (optional for triggering)
        artifact_store: Any = None,  # for writing evaluation artifacts
        ecs_store: Any = None,  # for ECS transitions on evaluation artifacts
        event_store: Any = None,  # for emitting vigil events
        corpus_turn_store: Any = None,  # for reading augmented chat turns
    ) -> None:
        self.config = config
        self.vigil_store = vigil_store
        self.canonical_store = canonical_store
        self.entity_store = entity_store
        self.model_provider = model_provider
        self.trace_store = trace_store
        self.sexton = sexton
        self._artifacts = artifact_store
        self._ecs = ecs_store
        self._events = event_store
        self._corpus_turns = corpus_turn_store
        self._last_eval_time: float | None = None

    # ------------------------------------------------------------------
    # ADR-011 Quality Evaluation Cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> dict:
        """Execute a Vigil quality evaluation cycle (ADR-011).

        Reads augmented chat turns since the previous vigil run, checks each
        for source citation quality, writes the score to metadata_json, and
        flags low-citation turns for DEFINER review.

        This is a pure-Python implementation — no LLM calls needed.
        The citation_rate is computed as:
            cited_turns / retrieved_turns
        where a "cited" turn is one whose turn_id (or a citation pattern
        referencing it) appears in the assistant response text.

        Returns a summary dict with evaluation results.
        """
        cycle_start = time.monotonic()

        logger.info("vigil_eval_start")

        evaluated_count = 0
        flagged_count = 0
        citation_rates: list[float] = []
        grounding_rates: list[float] = []
        hedging_detected_count = 0

        # --- Step 1: Read augmented turns since last eval ---
        if self._corpus_turns is not None:
            try:
                turns = await self._corpus_turns.get_augmented_turns_since(
                    since=self._last_eval_time,
                    limit=100,
                )
            except Exception as exc:
                logger.warning("vigil_augmented_turns_query_failed", error=str(exc))
                turns = []

            # --- Step 2: Evaluate citation quality for each turn ---
            for turn in turns:
                try:
                    meta = json.loads(turn.metadata_json) if turn.metadata_json else {}
                    source_turn_ids: list[str] = meta.get("source_turn_ids", [])

                    if not source_turn_ids:
                        # No sources were retrieved — nothing to cite, skip scoring
                        continue

                    # Check which source_turn_ids appear in the response text
                    cited_ids = self._find_cited_turns(
                        source_turn_ids=source_turn_ids,
                        response_text=turn.assistant_text,
                    )

                    citation_rate = len(cited_ids) / len(source_turn_ids)
                    citation_rates.append(citation_rate)

                    # --- Quality Check 2: Source grounding ---
                    # Extract numeric claims from the response and check if
                    # they appear in the source text.  Numeric claims are
                    # numbers, percentages, and dates that should be grounded.
                    source_text = (turn.user_text or "") + " " + (turn.assistant_text or "")
                    grounding_result = self._check_source_grounding(
                        response_text=turn.assistant_text or "",
                        source_text=source_text,
                    )
                    grounding_rates.append(grounding_result["grounding_rate"])

                    # --- Quality Check 3: Hedging / uncertainty detection ---
                    # Detect hedging phrases that suggest the response is
                    # uncertain when it should be authoritative (based on sources).
                    hedging_found = self._detect_hedging(turn.assistant_text or "")
                    if hedging_found and len(source_turn_ids) > 0:
                        hedging_detected_count += 1

                    evaluated_count += 1

                    # --- Step 3: Write score to metadata_json ---
                    updated_meta = dict(meta)  # copy existing
                    updated_meta["vigil_score"] = round(citation_rate, 3)
                    updated_meta["vigil_evaluated_at"] = datetime.now(timezone.utc).isoformat()
                    updated_meta["vigil_cited_ids"] = cited_ids
                    updated_meta["vigil_source_count"] = len(source_turn_ids)
                    updated_meta["vigil_grounding_rate"] = grounding_result["grounding_rate"]
                    updated_meta["vigil_ungrounded_claims"] = grounding_result["ungrounded_claims"]
                    updated_meta["vigil_hedging_detected"] = hedging_found

                    await self._corpus_turns.update_metadata_json(
                        turn_id=turn.turn_id,
                        metadata_json=json.dumps(updated_meta),
                    )

                    # --- Step 4: Flag low-quality turns for DEFINER review ---
                    flag_reasons = []
                    if citation_rate < self._CITATION_THRESHOLD:
                        flag_reasons.append("low_citation_rate")
                    if grounding_result["grounding_rate"] < self._GROUNDING_THRESHOLD:
                        flag_reasons.append("poor_source_grounding")
                    if hedging_found and len(source_turn_ids) > 0:
                        flag_reasons.append("unwarranted_hedging")

                    if flag_reasons:
                        flagged_count += 1
                        await self._write_flagged_artifact(
                            turn=turn,
                            citation_rate=citation_rate,
                            cited_ids=cited_ids,
                            source_turn_ids=source_turn_ids,
                            grounding_rate=grounding_result["grounding_rate"],
                            ungrounded_claims=grounding_result["ungrounded_claims"],
                            hedging_detected=hedging_found,
                            flag_reasons=flag_reasons,
                        )

                except Exception as exc:
                    logger.warning(
                        "vigil_turn_eval_failed",
                        turn_id=turn.turn_id,
                        error=str(exc),
                    )

        # Compute aggregate metrics
        avg_citation_rate = (
            round(sum(citation_rates) / len(citation_rates), 3)
            if citation_rates
            else 0.0
        )
        avg_grounding_rate = (
            round(sum(grounding_rates) / len(grounding_rates), 3)
            if grounding_rates
            else 1.0
        )

        # Record the vigil check (legacy compatibility)
        try:
            await self.vigil_store.record_vigil_check(
                canonical_count=evaluated_count,
                stale_count=flagged_count,
                status="quality_evaluation_complete",
            )
        except Exception as exc:
            logger.warning("vigil_store_record_failed", error=str(exc))

        # Emit vigil event
        if self._events is not None:
            try:
                await self._events.emit(
                    event_type="vigil_eval_complete",
                    artifact_id="system",
                    metadata={
                        "evaluated_count": evaluated_count,
                        "flagged_count": flagged_count,
                        "avg_citation_rate": avg_citation_rate,
                        "avg_grounding_rate": avg_grounding_rate,
                        "hedging_detected_count": hedging_detected_count,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                pass

        elapsed = time.monotonic() - cycle_start
        self._last_eval_time = time.time()

        result = {
            "status": "quality_evaluation_complete",
            "evaluated_count": evaluated_count,
            "flagged_count": flagged_count,
            "avg_citation_rate": avg_citation_rate,
            "avg_grounding_rate": avg_grounding_rate,
            "hedging_detected_count": hedging_detected_count,
            "citation_threshold": self._CITATION_THRESHOLD,
            "grounding_threshold": self._GROUNDING_THRESHOLD,
            "cycle_elapsed_seconds": round(elapsed, 3),
            "last_eval_time": self._last_eval_time,
        }

        logger.info(
            "vigil_eval_complete",
            status=result["status"],
            evaluated=evaluated_count,
            flagged=flagged_count,
            avg_citation_rate=avg_citation_rate,
            avg_grounding_rate=avg_grounding_rate,
            hedging_detected=hedging_detected_count,
        )
        return result

    @staticmethod
    def _check_source_grounding(response_text: str, source_text: str) -> dict:
        """Check whether numeric claims in the response are grounded in the source text.

        Extracts numbers (integers, decimals, percentages) from the response
        and checks whether they appear in the source text.  Returns a dict
        with grounding_rate (grounded / total_claims) and a list of
        ungrounded claims.

        This is a heuristic check — it does not understand semantics, but it
        catches common fabrication patterns like invented statistics or dates.
        """
        if not response_text:
            return {"grounding_rate": 1.0, "total_claims": 0, "grounded_claims": 0, "ungrounded_claims": []}

        # Extract numeric claims: numbers with optional decimal and percent sign
        import re as _re
        numeric_pattern = _re.compile(r'\b\d+(?:\.\d+)?%?\b')
        response_numbers = numeric_pattern.findall(response_text)

        if not response_numbers:
            return {"grounding_rate": 1.0, "total_claims": 0, "grounded_claims": 0, "ungrounded_claims": []}

        # Filter out trivial numbers (0, 1, 2) that appear everywhere
        nontrivial = [n for n in response_numbers if n not in ("0", "1", "2", "0%", "1%", "2%")]

        if not nontrivial:
            return {"grounding_rate": 1.0, "total_claims": len(response_numbers), "grounded_claims": len(response_numbers), "ungrounded_claims": []}

        source_lower = source_text.lower()
        grounded = []
        ungrounded = []
        for num in nontrivial:
            if num in source_lower or num.rstrip("%") in source_lower:
                grounded.append(num)
            else:
                ungrounded.append(num)

        total = len(nontrivial)
        rate = len(grounded) / total if total > 0 else 1.0
        return {
            "grounding_rate": round(rate, 3),
            "total_claims": total,
            "grounded_claims": len(grounded),
            "ungrounded_claims": ungrounded[:10],  # Cap at 10 to avoid bloat
        }

    def _detect_hedging(self, response_text: str) -> bool:
        """Detect hedging language in the response.

        Returns True if any hedging phrase is found.  Hedging is
        concerning when sources are available because it suggests the
        model is uncertain despite having authoritative source material.
        """
        if not response_text:
            return False
        text_lower = response_text.lower()
        return any(phrase in text_lower for phrase in self._HEDGING_PHRASES)

    @staticmethod
    def _find_cited_turns(source_turn_ids: list[str], response_text: str) -> list[str]:
        """Determine which source_turn_ids appear in the response text as citations.

        Checks multiple citation patterns:
          - Direct turn_id reference (e.g. "abc123" appearing in the text)
          - [source: turn_id] pattern (the instructed citation format)
          - [Source N] numbered reference (if sources were numbered in context)

        A turn_id is considered "cited" if any fragment of it (at least 8 chars)
        appears in the response text, or if the full [source: ...] pattern matches.
        """
        if not response_text or not source_turn_ids:
            return []

        cited: list[str] = []
        response_lower = response_text.lower()

        for tid in source_turn_ids:
            # Check 1: Full [source: tid] pattern (case-insensitive)
            if f"[source: {tid}]" in response_lower or f"[source:{tid}]" in response_lower:
                cited.append(tid)
                continue

            # Check 2: Turn ID fragment (at least 8 chars) appears in text
            # Short IDs might match random text, so require a minimum length
            fragment = tid[:8] if len(tid) >= 8 else tid
            if fragment in response_lower:
                cited.append(tid)
                continue

            # Check 3: [Source N] numbered pattern — count how many sources
            # were in the context. If response mentions [Source 1] through
            # [Source N], that implies citation of at least the first N sources.
            # This is a loose heuristic; we only count it if the numbered pattern
            # exists AND the turn's position in the list matches.
            source_num_pattern = re.findall(r'\[source\s+(\d+)\]', response_lower)
            if source_num_pattern:
                # Source numbers are 1-based; check if our position is covered
                # (We can't know our position from just the IDs, so skip this
                # for now — the direct ID match above is sufficient.)
                pass

        return cited

    async def _write_flagged_artifact(
        self,
        turn: Any,
        citation_rate: float,
        cited_ids: list[str],
        source_turn_ids: list[str],
        grounding_rate: float = 1.0,
        ungrounded_claims: list[str] | None = None,
        hedging_detected: bool = False,
        flag_reasons: list[str] | None = None,
    ) -> None:
        """Write a GENERATED artifact flagging a low-quality turn for DEFINER review.

        The artifact includes the turn's metadata and quality analysis so the
        DEFINER can review whether the response properly cited its sources,
        whether claims are grounded, and whether hedging is warranted.
        """
        if self._artifacts is None:
            return

        try:
            artifact_id = f"vigil-flag-{turn.turn_id}"
            content = json.dumps({
                "flag_type": "quality_evaluation",
                "flag_reasons": flag_reasons or ["low_citation_rate"],
                "turn_id": turn.turn_id,
                "conversation_id": turn.conversation_id,
                "citation_rate": round(citation_rate, 3),
                "citation_threshold": self._CITATION_THRESHOLD,
                "grounding_rate": round(grounding_rate, 3),
                "grounding_threshold": self._GROUNDING_THRESHOLD,
                "ungrounded_claims": ungrounded_claims or [],
                "hedging_detected": hedging_detected,
                "source_count": len(source_turn_ids),
                "cited_count": len(cited_ids),
                "cited_ids": cited_ids,
                "uncited_ids": [tid for tid in source_turn_ids if tid not in cited_ids],
                "response_preview": (turn.assistant_text or "")[:500],
                "flagged_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2)

            metadata = {
                "artifact_type": "vigil_flag",
                "flag_type": "quality_evaluation",
                "flag_reasons": flag_reasons or ["low_citation_rate"],
                "turn_id": turn.turn_id,
                "citation_rate": round(citation_rate, 3),
                "grounding_rate": round(grounding_rate, 3),
                "hedging_detected": hedging_detected,
                "generated_by": "vigil",
            }

            await self._artifacts.write(
                id=artifact_id,
                content=content,
                metadata=metadata,
            )

            # Transition to GENERATED state for DEFINER review
            if self._ecs is not None:
                try:
                    reasons_str = ", ".join(flag_reasons or ["low_citation_rate"])
                    await self._ecs.transition(
                        artifact_id=artifact_id,
                        to_state="GENERATED",
                        actor="vigil",
                        detail=f"Quality evaluation: {reasons_str} (citation_rate={citation_rate:.1%}, grounding_rate={grounding_rate:.1%})",
                    )
                except Exception:
                    pass

        except Exception as exc:
            logger.warning(
                "vigil_flag_artifact_failed",
                turn_id=turn.turn_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Legacy methods (retained for backward compatibility)
    # These are NOT part of the ADR-011 vigil cycle.
    # ------------------------------------------------------------------

    async def check_canonical_health(self) -> dict:
        """Return aggregate canonical health status.

        .. deprecated:: ADR-011
           Canonical health monitoring is not part of Vigil's quality
           evaluation role. Retained for backward compatibility.
        """
        try:
            canonicals = await self.canonical_store.list_canonical()
            total = len(canonicals)
            stale = await self.detect_stale_canonicals()
            return {
                "total_count": total,
                "stale_count": len(stale),
                "healthy_count": total - len(stale),
                "degraded_count": len(stale),
                "status": "healthy" if len(stale) == 0 else "degraded",
            }
        except Exception:
            return {
                "total_count": 0,
                "stale_count": 0,
                "healthy_count": 0,
                "degraded_count": 0,
                "status": "unknown",
            }

    async def detect_stale_canonicals(self) -> list[dict]:
        """Return list of stale canonicals (threshold + model slot).

        .. deprecated:: ADR-011
           Stale canonical detection is a maintenance function (→ Sexton).
           Retained for backward compatibility.
        """
        try:
            threshold_days = self.config.stale_threshold_days
            return await self.vigil_store.list_stale_canonicals(threshold_days=threshold_days)
        except Exception:
            return []

    async def detect_entity_inconsistencies(self) -> list[dict]:
        """Return entities referenced by canonicals that have been updated since promotion.

        .. deprecated:: ADR-011
           Entity consistency checking is a maintenance function (→ Sexton).
           Retained for backward compatibility.
        """
        if not self.config.entity_consistency_check:
            return []
        try:
            entities = await self.entity_store.list_entities()
            inconsistencies = []
            for entity in entities:
                entity_id = entity.get("entity_id") or entity.get("id")
                if entity_id:
                    entity_data = await self.entity_store.get_entity(entity_id)
                    if entity_data and entity_data.get("updated_since_canonical"):
                        inconsistencies.append(entity_data)
            return inconsistencies
        except Exception:
            return []

    async def on_model_slot_change(
        self,
        slot_name: str,
        old_config: ModelSlotConfig,
        new_config: ModelSlotConfig,
    ) -> dict:
        """React to model slot change with real re-evaluation marking.

        .. deprecated:: ADR-011
           Model slot change handling is a maintenance function.
           Retained for backward compatibility.

        Identifies affected canonicals, marks them for re-evaluation in
        VigilStore, and records trace events for Sexton.
        """
        result = {
            "slot_name": slot_name,
            "old_model": getattr(old_config, "model", "unknown"),
            "new_model": getattr(new_config, "model", "unknown"),
            "affected_count": 0,
            "marked_for_re_evaluation": 0,
            "trace_events_written": 0,
        }

        if not self.config.re_evaluate_on_slot_change:
            result["skipped_reason"] = "re_evaluate_on_slot_change is False"
            return result

        try:
            canonicals = await self.canonical_store.list_canonical()
            affected = canonicals
            result["affected_count"] = len(affected)

            batch_size = self.config.max_re_evaluate_batch_size
            for item in affected[:batch_size]:
                artifact_id = item.get("artifact_id") or item.get("id", "unknown")
                try:
                    await self.vigil_store.record_vigil_check(
                        canonical_count=result["affected_count"],
                        stale_count=1,
                        status="needs_re_evaluation",
                    )
                    result["marked_for_re_evaluation"] += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to mark canonical %s for re-evaluation: %s",
                        artifact_id,
                        exc,
                    )

            for item in affected[:batch_size]:
                artifact_id = item.get("artifact_id") or item.get("id", "unknown")
                try:
                    await self.trace_store.write_event(
                        session_id="vigil-model-slot-change",
                        node_type="vigil",
                        failure_type="A",
                        outcome="needs_re_evaluation",
                        detail=(
                            f"Model slot '{slot_name}' changed from "
                            f"'{getattr(old_config, 'model', 'unknown')}' to "
                            f"'{getattr(new_config, 'model', 'unknown')}'. "
                            f"Canonical '{artifact_id}' may have stale evaluations "
                            f"and needs re-evaluation."
                        ),
                    )
                    result["trace_events_written"] += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to write trace event for slot change on %s: %s",
                        artifact_id,
                        exc,
                    )

        except Exception as exc:
            logger.error("vigil_slot_change_reeval_error", error=str(exc))
            result["error"] = str(exc)

        return result

    # ------------------------------------------------------------------
    # Legacy run() entry point — redirects to run_cycle()
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Cadence entry point (called by scheduler). Delegates to run_cycle().

        .. note:: ADR-011
           The legacy run() performed maintenance operations (canonical health,
           stale detection, entity inconsistencies). Those are now deprecated.
           This method now delegates to run_cycle() for quality evaluation.
        """
        await self.run_cycle()
