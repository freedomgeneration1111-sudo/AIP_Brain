"""Canonical Promotion Pipeline.

The missing orchestration driver for the full REVIEWED→APPROVED→CANONICAL lifecycle.
Composes 8.0b stores + 8.4 review paths + 6.2 evaluation nodes + 9.1 Vigil health recording + AutonomyGate.

ci_fixture handling:
    In production mode (when the CI environment variable is not set), promotion is
    blocked if any evaluation result has ``ci_fixture=True``. This prevents artifacts
    evaluated only with CI fixture scores from being promoted to canonical status.

    In CI mode (CI=true environment variable), ci_fixture results are allowed through
    since no real model evaluation is available.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from aip.foundation.protocols import (
    AutonomyGate,
    CanonicalStore,
    ArtifactStore,
    EcsStore,
    EventStore,
    VectorStore,
    LexicalStore,
    ModelProvider,
    EmbeddingProvider,
    VigilStore,
)
from aip.foundation.schemas import CanonicalPromotionConfig, coerce_autonomy_level

logger = logging.getLogger(__name__)


def _is_ci_environment() -> bool:
    """Check whether we are running in a CI environment.

    Returns True if the CI environment variable is set to a truthy value
    (e.g. "true", "1", "yes"). This allows CI pipelines to use fixture
    scores without blocking promotion, while production deployments block
    artifacts evaluated with fixture scores.
    """
    return os.environ.get("CI", "").lower() in ("true", "1", "yes")


class CanonicalPipeline:
    """Canonical promotion pipeline (Phase 7)."""

    def __init__(
        self,
        config: CanonicalPromotionConfig,
        autonomy_gate: AutonomyGate,
        canonical_store: CanonicalStore,
        artifact_store: ArtifactStore,
        ecs_store: EcsStore,
        event_store: EventStore,
        vector_store: VectorStore,
        lexical_store: LexicalStore,
        model_provider: ModelProvider,
        embedding_provider: EmbeddingProvider,
        vigil_store: VigilStore,
    ) -> None:
        self.config = config
        self.autonomy_gate = autonomy_gate
        self.canonical_store = canonical_store
        self.artifact_store = artifact_store
        self.ecs_store = ecs_store
        self.event_store = event_store
        self.vector_store = vector_store
        self.lexical_store = lexical_store
        self.model_provider = model_provider
        self.embedding_provider = embedding_provider
        self.vigil_store = vigil_store

    async def evaluate_for_promotion(self, artifact_id: str) -> dict:
        """Read-only readiness check (steps 1-4). Returns scores + pass/fail + whether gate would be required.

        If evaluation fails, scores are set to 0.0 and passes_threshold is False.
        This ensures artifacts cannot pass the promotion gate without real evaluation.
        In CI mode, evaluation nodes return ci_fixture scores with explicit flags.

        The result includes a ``ci_fixture`` field that is True when any evaluation
        was performed with CI fixture scores rather than real model evaluation.
        In production mode, ci_fixture=True means the artifact should NOT be promoted.
        """
        # 1. Verify REVIEWED state
        try:
            current = await self.ecs_store.current_state(artifact_id)
        except Exception:
            current = "UNKNOWN"
            logger.warning("Could not determine ECS state for %s; assuming UNKNOWN", artifact_id)

        # 2-4. Run faithfulness + domain coherence evaluations
        # Scores default to 0.0 (fail) — must be set by real evaluation or CI fixtures
        faithfulness_score = 0.0
        domain_coherence_score = 0.0
        evaluation_succeeded = False
        ci_fixture = False
        try:
            content = await self.artifact_store.read(artifact_id)
            content_str = str(content) if content else ""
            from aip.orchestration.nodes.faithfulness import evaluate_faithfulness
            stage2 = await evaluate_faithfulness(
                artifact_id=artifact_id,
                artifact_content=content_str,
                retrieved_context=[],
                model_resolver=self.model_provider,
            )
            faithfulness_score = stage2.faithfulness_score
            if stage2.ci_fixture:
                ci_fixture = True
                logger.info(
                    "Faithfulness evaluation for %s returned ci_fixture=True (score=%.2f)",
                    artifact_id, faithfulness_score,
                )

            from aip.orchestration.nodes.domain_coherence import evaluate_domain_coherence
            stage3 = await evaluate_domain_coherence(
                artifact_id=artifact_id,
                artifact_content=content_str,
                domain="default",
                model_resolver=self.model_provider,
            )
            domain_coherence_score = stage3.coherence_score
            if stage3.ci_fixture:
                ci_fixture = True
                logger.info(
                    "Domain coherence evaluation for %s returned ci_fixture=True (score=%.2f)",
                    artifact_id, domain_coherence_score,
                )

            evaluation_succeeded = True
        except Exception:
            logger.error("Evaluation FAILED for artifact %s; scores set to 0.0 (will not pass threshold)", artifact_id, exc_info=True)

        # In production mode, block promotion if evaluation used CI fixtures
        ci_fixture_blocked = False
        if ci_fixture and not _is_ci_environment():
            ci_fixture_blocked = True
            logger.warning(
                "Artifact %s evaluated with ci_fixture=True in production mode; "
                "promotion will be blocked. Set CI=true to allow fixture promotion.",
                artifact_id,
            )

        # Use config thresholds (issue 12)
        passes_threshold = (
            evaluation_succeeded
            and not ci_fixture_blocked
            and (not self.config.require_faithfulness_check or faithfulness_score >= self.config.faithfulness_threshold)
            and (not self.config.require_domain_coherence or domain_coherence_score >= self.config.domain_coherence_threshold)
        )

        return {
            "artifact_id": artifact_id,
            "current_state": current,
            "faithfulness_score": faithfulness_score,
            "domain_coherence_score": domain_coherence_score,
            "evaluation_succeeded": evaluation_succeeded,
            "ci_fixture": ci_fixture,
            "ci_fixture_blocked": ci_fixture_blocked,
            "passes_threshold": passes_threshold,
            "requires_definer_approval": self.config.require_definer_approval,
        }

    async def promote_to_canonical(self, artifact_id: str, approved_by: str) -> dict:
        """Full 10-step pipeline.

        Blocks promotion in production mode when evaluation results have
        ci_fixture=True, since fixture scores are not real quality measurements.
        """
        # 1. Verify REVIEWED
        current = await self.ecs_store.current_state(artifact_id)
        if current != "REVIEWED":
            raise ValueError(f"Artifact {artifact_id} not in REVIEWED state (current: {current})")

        # 2-4. Evaluations (faithfulness, domain coherence)
        # Issue 8: Read content BEFORE using it
        content = await self.artifact_store.read(artifact_id)
        content_str = str(content) if content else ""

        # Scores default to 0.0 (fail) — must be set by real evaluation or CI fixtures
        faithfulness = 0.0
        domain_coherence = 0.0
        evaluation_succeeded = False
        ci_fixture = False
        try:
            from aip.orchestration.nodes.faithfulness import evaluate_faithfulness
            stage2 = await evaluate_faithfulness(
                artifact_id=artifact_id,
                artifact_content=content_str,
                retrieved_context=[],
                model_resolver=self.model_provider,
            )
            faithfulness = stage2.faithfulness_score
            if stage2.ci_fixture:
                ci_fixture = True

            from aip.orchestration.nodes.domain_coherence import evaluate_domain_coherence
            stage3 = await evaluate_domain_coherence(
                artifact_id=artifact_id,
                artifact_content=content_str,
                domain="default",
                model_resolver=self.model_provider,
            )
            domain_coherence = stage3.coherence_score
            if stage3.ci_fixture:
                ci_fixture = True

            evaluation_succeeded = True
        except Exception:
            logger.error("Evaluation FAILED for artifact %s; scores set to 0.0 — promotion will be blocked", artifact_id, exc_info=True)

        if not evaluation_succeeded:
            raise ValueError(f"Evaluation failed for artifact {artifact_id}; cannot proceed with promotion")

        # In production mode, block promotion if evaluation used CI fixtures
        if ci_fixture and not _is_ci_environment():
            logger.warning(
                "BLOCKING promotion of artifact %s: evaluation used ci_fixture scores "
                "in production mode. CI fixture scores are not real quality measurements. "
                "Set CI=true environment variable to allow fixture-based promotion in CI.",
                artifact_id,
            )
            raise ValueError(
                f"Promotion blocked for artifact {artifact_id}: evaluation used CI fixture "
                f"scores (ci_fixture=True) in production mode. Real model evaluation is required."
            )

        # Issue 12: Use config thresholds instead of hardcoded values
        if self.config.require_faithfulness_check and faithfulness < self.config.faithfulness_threshold:
            raise ValueError("Faithfulness below threshold")
        if self.config.require_domain_coherence and domain_coherence < self.config.domain_coherence_threshold:
            raise ValueError("Domain coherence below threshold")

        # 5. AutonomyGate admin escalate (belt + suspenders with approved_by check)
        if self.config.require_definer_approval:
            esc = await self.autonomy_gate.escalate(
                action_type="approve_artifact",
                resource_id=artifact_id,
                requested_level=coerce_autonomy_level("admin"),
                requested_by=approved_by,
            )
            if not esc.granted:
                raise PermissionError(f"Autonomy gate blocked promotion: {esc.reason}")

        if approved_by != "definer":
            raise PermissionError("approved_by must be 'definer'")

        # 7. Write canonical + 8. ECS transition REVIEWED → APPROVED
        # Issue 9: transition() now requires actor and reason, must be awaited
        await self.canonical_store.write_canonical(artifact_id, content, approved_by="definer")
        await self.ecs_store.transition(
            artifact_id=artifact_id,
            from_state="REVIEWED",
            to_state="APPROVED",
            actor="canonical_pipeline",
            reason="Canonical promotion approved by definer",
        )

        # 9. Re-index (Vector + Lexical) if configured
        if self.config.auto_reindex_on_promotion:
            # Simplified: re-embed + re-index
            embedding = await self.embedding_provider.embed(str(content))
            await self.vector_store.upsert(
                artifact_id, embedding, str(content),
                {"source": "canonical_promotion"}, domain="canonical",
            )
            await self.lexical_store.index_document(
                artifact_id, str(content), "canonical", {"source": "canonical_promotion"}
            )

        # 10. Write health to VigilStore + Event
        # Issue 11: Use correct VigilStore method
        await self.vigil_store.record_vigil_check(
            canonical_count=1, stale_count=0, status="healthy",
        )
        # Issue 10: Use proper EventStore Protocol signature
        await self.event_store.write_event(
            event_type="canonical_promotion",
            actor="canonical_pipeline",
            artifact_id=artifact_id,
            from_state="REVIEWED",
            to_state="APPROVED",
            detail=f"Artifact {artifact_id} promoted to canonical by {approved_by}",
        )

        return {"artifact_id": artifact_id, "state": "APPROVED", "canonical_written": True}

    async def reject_promotion(self, artifact_id: str, reason: str) -> dict:
        """Record rejection. Does NOT change ECS state (remains REVIEWED)."""
        # Issue 10: Use proper EventStore Protocol signature
        await self.event_store.write_event(
            event_type="canonical_rejection",
            actor="canonical_pipeline",
            artifact_id=artifact_id,
            from_state="REVIEWED",
            to_state=None,
            detail=f"Artifact {artifact_id} rejected: {reason}",
        )
        return {"artifact_id": artifact_id, "action": "rejected", "reason": reason}

    async def list_promotion_candidates(self) -> list[dict]:
        """Return REVIEWED artifacts eligible for promotion."""
        # Issue 13: Query ecs_store for REVIEWED artifacts instead of returning []
        try:
            # Use EventStore query to find REVIEWED artifacts
            events = await self.event_store.query(event_type="ecs_transition", limit=500)
            reviewed = set()
            for ev in events:
                if hasattr(ev, 'to_state') and ev.to_state == "REVIEWED":
                    reviewed.add(ev.artifact_id)
                elif isinstance(ev, dict) and ev.get("to_state") == "REVIEWED":
                    reviewed.add(ev.get("artifact_id", ""))
            # Filter out artifacts that have since moved past REVIEWED
            candidates = []
            for aid in reviewed:
                if aid:
                    try:
                        state = await self.ecs_store.current_state(aid)
                        if state == "REVIEWED":
                            candidates.append({"artifact_id": aid, "ecs_state": "REVIEWED"})
                    except Exception:
                        logger.debug("Could not check state for artifact %s", aid, exc_info=True)
            return candidates
        except Exception:
            logger.warning("Could not list promotion candidates", exc_info=True)
            return []

    async def get_promotion_status(self, artifact_id: str) -> dict:
        """Run evaluate_for_promotion + return current readiness (no state change)."""
        return await self.evaluate_for_promotion(artifact_id)
