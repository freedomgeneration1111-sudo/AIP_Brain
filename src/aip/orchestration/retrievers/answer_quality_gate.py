"""Answer Quality Gate — evaluate context sufficiency before model dispatch.

The Quality Gate is invoked after context assembly but before the model
call. It evaluates whether the assembled context is sufficient for the
model to generate a reliable answer. This prevents:

1. Wasting model tokens on empty or near-empty context
2. Generating confident-sounding answers from insufficient evidence
3. Missing entity-specific information that the query clearly needs

Simple heuristics are used (no model call needed):
- Minimum evidence tokens threshold
- Entity coverage: do key entities from the query appear in context?
- Domain coverage: does the context cover the expected domain?
- Score distribution: are the top hits high-quality or just noise?

If context is insufficient, the gate can:
- Signal NEEDS_MORE_CONTEXT (caller can retry or inform the user)
- Suggest a second retrieval round with expanded queries
- Populate quality scores in the RetrievalTrace for observability

Phase 5.5 deliverable: Answer Quality Gate.

Layer: orchestration. Imports from foundation (schemas).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aip.foundation.schemas.retrieval_trace import (
    ContextQualityStatus,
    RetrievalBudget,
    RetrievalHit,
    RetrievalTrace,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quality Gate Configuration
# ---------------------------------------------------------------------------


@dataclass
class QualityGateConfig:
    """Configuration for the Answer Quality Gate.

    These thresholds control when the gate signals insufficient context.
    They are intentionally conservative — false positives (flagging good
    context as insufficient) are better than false negatives (sending
    weak context to the model).
    """

    # Minimum evidence tokens to consider context sufficient
    min_evidence_tokens: int = 200  # ~800 chars / ~2-3 paragraphs

    # Minimum score of the top hit to consider context relevant
    min_top_score: float = 0.15

    # Entity coverage threshold: fraction of query entities that
    # should appear in the context for entity-centric queries
    min_entity_coverage: float = 0.3

    # Minimum number of hits to consider context sufficient
    min_hit_count: int = 1

    # Score for the "marginal" band (between sufficient and insufficient)
    marginal_score_threshold: float = 0.5

    # Whether to enable the quality gate (can be disabled for testing)
    enabled: bool = True

    # Phase 5.6: Optional model-assisted sufficiency check
    enable_model_assisted: bool = False  # Off by default — heuristic-only is the fast path
    model_assisted_slot: str = "fast"  # Model slot for the sufficiency check


# ---------------------------------------------------------------------------
# Quality Gate Result
# ---------------------------------------------------------------------------


@dataclass
class QualityGateResult:
    """Result of the Answer Quality Gate evaluation.

    Contains the status, individual dimension scores, and recommendations
    for what to do if the context is insufficient.
    """

    status: ContextQualityStatus = ContextQualityStatus.SUFFICIENT
    scores: dict[str, float] = field(default_factory=dict)
    # Individual dimension scores:
    #   evidence_tokens: raw evidence token count
    #   top_hit_score: score of the best hit
    #   entity_coverage: fraction of query entities found in context
    #   hit_count: total number of hits
    #   overall_quality: weighted combination

    recommendations: list[str] = field(default_factory=list)
    # What to do if context is insufficient:
    #   "expand_query" — try expanded queries
    #   "broaden_search" — relax domain filters
    #   "signal_user" — inform user that context may be insufficient

    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Answer Quality Gate
# ---------------------------------------------------------------------------


class AnswerQualityGate:
    """Evaluates context sufficiency before model dispatch.

    The gate is a lightweight, heuristic-based check that runs after
    context assembly. By default it does NOT call any model — it uses
    simple statistics about the assembled context to determine whether
    the model is likely to produce a reliable answer.

    Phase 5.6 enhancement: An optional model-assisted sufficiency check
    can be enabled via config. This uses a fast model slot to ask the
    model whether the context is sufficient for answering the query.
    The pure heuristic path remains the default/fast path.

    Evaluation dimensions:
    1. Evidence tokens: is there enough text for the model to work with?
    2. Top hit score: is the best hit relevant or just noise?
    3. Entity coverage: do key entities from the query appear in context?
    4. Hit count: are there enough diverse sources?
    5. (Optional) Model-assisted check: ask a fast model to verify sufficiency

    The gate produces a ContextQualityStatus and individual scores.
    These are written into the RetrievalTrace for observability and
    regression detection.

    Design decisions:
    - Simple heuristics are the default — no model call (fast, free, deterministic)
    - Model-assisted check is optional and uses a fast model slot
    - Conservative thresholds — prefer signaling insufficient over missing
    - The gate is advisory — callers decide what to do with the result
    - The gate can be disabled via configuration for testing/overrides
    """

    def __init__(
        self,
        config: QualityGateConfig | None = None,
        model_provider: Any = None,
    ) -> None:
        self._config = config or QualityGateConfig()
        self._model_provider = model_provider  # Optional: for model-assisted check

    def evaluate(
        self,
        hits: list[RetrievalHit],
        trace: RetrievalTrace,
        budget: RetrievalBudget | None = None,
    ) -> QualityGateResult:
        """Evaluate context sufficiency.

        Args:
            hits: The curated hit list from the orchestrator.
            trace: The RetrievalTrace (for entity detection data).
            budget: Optional budget for token threshold calculation.

        Returns:
            QualityGateResult with status, scores, and recommendations.
        """
        started = time.monotonic()

        if not self._config.enabled:
            return QualityGateResult(
                status=ContextQualityStatus.SUFFICIENT,
                scores={"enabled": 0.0},
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )

        scores: dict[str, float] = {}
        recommendations: list[str] = []

        # --- Dimension 1: Evidence tokens ---
        budget = budget or RetrievalBudget()
        evidence_tokens = 0
        for hit in hits:
            char_count = len(hit.text) if hit.text else 0
            evidence_tokens += char_count // 4

        scores["evidence_tokens"] = float(evidence_tokens)
        scores["evidence_budget"] = float(budget.total_tokens * budget.evidence_allocation)
        evidence_ratio = evidence_tokens / max(
            budget.total_tokens * budget.evidence_allocation, 1
        )
        scores["evidence_ratio"] = round(min(evidence_ratio, 1.0), 3)

        # --- Dimension 2: Top hit score ---
        top_score = hits[0].score if hits else 0.0
        scores["top_hit_score"] = round(top_score, 4)

        # --- Dimension 3: Entity coverage ---
        detected_entities = trace.detected_entities or []
        entity_coverage = 0.0
        if detected_entities:
            # Check how many detected entities appear in the context
            context_text = " ".join(h.text or "" for h in hits).lower()
            entities_found = sum(
                1 for e in detected_entities
                if e.lower() in context_text
            )
            entity_coverage = entities_found / len(detected_entities)
        scores["entity_coverage"] = round(entity_coverage, 3)
        scores["entities_detected"] = float(len(detected_entities))

        # --- Dimension 4: Hit count ---
        hit_count = len(hits)
        scores["hit_count"] = float(hit_count)

        # --- Dimension 5: Channel diversity ---
        channels = set(h.retrieval_channel for h in hits) if hits else set()
        scores["channel_diversity"] = float(len(channels))

        # --- Overall quality score (weighted combination) ---
        quality = 0.0
        if evidence_tokens > 0:
            # Evidence component (0-1): how much of the evidence budget is used
            evidence_component = min(1.0, evidence_ratio)

            # Relevance component (0-1): is the top hit actually relevant?
            relevance_component = min(1.0, top_score / max(self._config.min_top_score, 0.01))

            # Coverage component (0-1): do entities appear in context?
            coverage_component = entity_coverage if detected_entities else 0.5
            # If no entities detected, give neutral coverage score

            # Weighted combination
            quality = (
                0.40 * evidence_component
                + 0.30 * relevance_component
                + 0.20 * coverage_component
                + 0.10 * min(1.0, hit_count / 5.0)
            )

        scores["overall_quality"] = round(quality, 3)

        # --- Determine status ---
        status = self._determine_status(
            hits=hits,
            evidence_tokens=evidence_tokens,
            top_score=top_score,
            entity_coverage=entity_coverage,
            quality=quality,
        )

        # --- Generate recommendations ---
        if status != ContextQualityStatus.SUFFICIENT:
            recommendations = self._generate_recommendations(
                status=status,
                evidence_tokens=evidence_tokens,
                top_score=top_score,
                entity_coverage=entity_coverage,
                detected_entities=detected_entities,
                quality=quality,
            )

        elapsed_ms = (time.monotonic() - started) * 1000.0

        result = QualityGateResult(
            status=status,
            scores=scores,
            recommendations=recommendations,
            elapsed_ms=round(elapsed_ms, 2),
        )

        # Write into trace
        trace.context_quality_status = status.value
        trace.context_quality_scores = scores
        trace.quality_gate_elapsed_ms = result.elapsed_ms

        return result

    def _determine_status(
        self,
        hits: list[RetrievalHit],
        evidence_tokens: int,
        top_score: float,
        entity_coverage: float,
        quality: float,
    ) -> ContextQualityStatus:
        """Determine the overall context quality status.

        Decision logic:
        - EMPTY: no hits at all
        - NEEDS_MORE_CONTEXT: hits exist but evidence is too thin
        - MARGINAL: some evidence but coverage gaps
        - SUFFICIENT: enough evidence with good coverage
        """
        # No hits at all → EMPTY
        if not hits:
            return ContextQualityStatus.EMPTY

        # Hard failure: not enough evidence tokens
        if evidence_tokens < self._config.min_evidence_tokens // 2:
            return ContextQualityStatus.NEEDS_MORE_CONTEXT

        # Hard failure: top hit is noise
        if top_score < self._config.min_top_score:
            return ContextQualityStatus.NEEDS_MORE_CONTEXT

        # Hard failure: not enough hits
        if len(hits) < self._config.min_hit_count:
            return ContextQualityStatus.NEEDS_MORE_CONTEXT

        # Marginal check: entity coverage is low
        detected_entities_count = 0
        # Use entity_coverage < min_entity_coverage as a signal
        if entity_coverage < self._config.min_entity_coverage and evidence_tokens < self._config.min_evidence_tokens:
            return ContextQualityStatus.MARGINAL

        # Quality-based thresholds
        if quality < 0.3:
            return ContextQualityStatus.NEEDS_MORE_CONTEXT
        elif quality < self._config.marginal_score_threshold:
            return ContextQualityStatus.MARGINAL

        return ContextQualityStatus.SUFFICIENT

    def _generate_recommendations(
        self,
        status: ContextQualityStatus,
        evidence_tokens: int,
        top_score: float,
        entity_coverage: float,
        detected_entities: list[str],
        quality: float,
    ) -> list[str]:
        """Generate actionable recommendations for insufficient context.

        These recommendations guide the caller (ask_pipeline or
        orchestrator) on what to try next.
        """
        recs: list[str] = []

        if evidence_tokens < self._config.min_evidence_tokens:
            recs.append("expand_query")
            recs.append("broaden_search")

        if top_score < self._config.min_top_score:
            recs.append("expand_query")

        if detected_entities and entity_coverage < self._config.min_entity_coverage:
            recs.append("expand_query")

        if status == ContextQualityStatus.NEEDS_MORE_CONTEXT:
            recs.append("signal_user")

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_recs: list[str] = []
        for r in recs:
            if r not in seen:
                seen.add(r)
                unique_recs.append(r)

        return unique_recs

    async def evaluate_model_assisted(
        self,
        hits: list[RetrievalHit],
        trace: RetrievalTrace,
        budget: RetrievalBudget | None = None,
    ) -> QualityGateResult:
        """Evaluate context sufficiency with optional model-assisted check.

        Phase 5.6: First runs the fast heuristic check, then optionally
        asks a fast model to verify the sufficiency assessment. The model
        can override MARGINAL → SUFFICIENT or MARGINAL → NEEDS_MORE_CONTEXT,
        but cannot override EMPTY (no hits = no context).

        The model-assisted check is useful when the heuristic is uncertain
        (MARGINAL status) and the cost of a fast model call is acceptable.

        Args:
            hits: The curated hit list from the orchestrator.
            trace: The RetrievalTrace (for entity detection data).
            budget: Optional budget for token threshold calculation.

        Returns:
            QualityGateResult with potentially model-adjusted status.
        """
        # Step 1: Run the fast heuristic check
        result = self.evaluate(hits, trace, budget)

        # Step 2: If model-assisted check is enabled and we have a provider,
        # and the status is MARGINAL (uncertain), ask the model
        if (
            self._config.enable_model_assisted
            and self._model_provider is not None
            and result.status == ContextQualityStatus.MARGINAL
            and hits
        ):
            try:
                model_result = await self._run_model_sufficiency_check(
                    hits, trace, result
                )
                if model_result is not None:
                    # Model can upgrade MARGINAL → SUFFICIENT or
                    # downgrade MARGINAL → NEEDS_MORE_CONTEXT
                    result.status = model_result
                    result.scores["model_assisted"] = 1.0
                    result.scores["model_assisted_status"] = {
                        ContextQualityStatus.SUFFICIENT: 1.0,
                        ContextQualityStatus.MARGINAL: 0.5,
                        ContextQualityStatus.NEEDS_MORE_CONTEXT: 0.2,
                    }.get(model_result, 0.5)
                    # Update trace
                    trace.context_quality_status = model_result.value
                    trace.context_quality_scores = result.scores
            except Exception as exc:
                logger.debug("Model-assisted quality check failed (non-fatal): %s", exc)
                result.scores["model_assisted"] = 0.0
                result.scores["model_assisted_error"] = 1.0

        return result

    async def _run_model_sufficiency_check(
        self,
        hits: list[RetrievalHit],
        trace: RetrievalTrace,
        heuristic_result: QualityGateResult,
    ) -> ContextQualityStatus | None:
        """Ask a fast model whether the context is sufficient.

        Uses a structured prompt that asks the model to assess whether
        the retrieved context contains enough information to answer the
        user's question. The model responds with a simple verdict.

        Returns None if the check fails (graceful degradation).
        """
        if not self._model_provider:
            return None

        query_text = trace.query.raw_query if trace.query else ""
        if not query_text:
            return None

        # Build a compact context preview (first 500 chars per hit, max 3 hits)
        context_preview_parts = []
        for hit in hits[:3]:
            text = (hit.text or hit.snippet or "")[:500]
            context_preview_parts.append(text)
        context_preview = "\n---\n".join(context_preview_parts)

        prompt = (
            "Given the user question and the retrieved context below, "
            "is the context sufficient to provide a meaningful answer?\n\n"
            f"Question: {query_text}\n\n"
            f"Context preview:\n{context_preview[:1500]}\n\n"
            "Respond with ONLY one word: SUFFICIENT, MARGINAL, or INSUFFICIENT."
        )

        try:
            import asyncio
            messages = [
                {"role": "system", "content": "You are a retrieval quality evaluator. Respond with only one word."},
                {"role": "user", "content": prompt},
            ]
            response = await asyncio.wait_for(
                self._model_provider.call(
                    self._config.model_assisted_slot,
                    messages,
                    temperature=0.0,
                ),
                timeout=5.0,  # 5 second timeout for the fast check
            )

            if response and not response.get("error"):
                content = response.get("content", "").strip().upper()
                if "SUFFICIENT" in content and "INSUFFICIENT" not in content:
                    return ContextQualityStatus.SUFFICIENT
                elif "INSUFFICIENT" in content:
                    return ContextQualityStatus.NEEDS_MORE_CONTEXT
                elif "MARGINAL" in content:
                    return ContextQualityStatus.MARGINAL  # Keep heuristic decision
        except Exception as exc:
            logger.debug("Model sufficiency check error: %s", exc)

        return None  # Could not determine — keep heuristic result


__all__ = [
    "AnswerQualityGate",
    "QualityGateConfig",
    "QualityGateResult",
]
