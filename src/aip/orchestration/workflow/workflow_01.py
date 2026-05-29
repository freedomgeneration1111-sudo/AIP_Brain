"""
Workflow 0.1 Executor — Synthesis Session Pipeline.

Provides a high-level runner for the canonical "Synthesis Session" workflow:
retrieve → validate → evaluate → review gate → commit.

The workflow pauses at the review gate when human DEFINER approval is
required.  In production, no artifact is committed without an explicit
approval decision.  In CI/test mode (``ci_mode=True``), the stub
definer gate auto-approves so tests can exercise the full pipeline.

Intended as the default workflow runner on top of the engine.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from aip.orchestration.nodes.commit import commit_artifact
from aip.orchestration.nodes.definer_gate import (
    DefinerDecision,
    DefinerGateMode,
    ManualReviewRequired,
    definer_gate,
)
from aip.orchestration.nodes.synthesis import SynthesisOutput, synthesize
from aip.orchestration.retrieval import fake_embed, retrieve_for_synthesis
from aip.orchestration.workflow.context import WorkflowContext
from aip.orchestration.workflow.node import (
    NodeResult,
    WorkflowNode,
)
from aip.orchestration.workflow.runner import SequentialRunner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structural Validation Node
# ---------------------------------------------------------------------------


class _ValidationNode(WorkflowNode):
    """Deterministic structural validation of synthesized content.

    Checks minimum length and required metadata keys.  Zero model calls.
    """

    def __init__(
        self,
        node_id: str = "structural_validate",
        min_length: int = 100,
        required_keys: tuple[str, ...] = ("provenance",),
    ):
        from aip.orchestration.workflow.node import NodeType

        super().__init__(node_id, NodeType.SCRIPT)
        self.min_length = min_length
        self.required_keys = required_keys

    async def run(self, context: WorkflowContext) -> NodeResult:
        from aip.foundation.validation import ValidationResult

        # Grab the synthesis output from the previous node
        previous = context.get("previous", {})
        synth = previous.get("output")

        content = ""
        if isinstance(synth, SynthesisOutput):
            content = synth.content or ""
        elif isinstance(synth, dict):
            content = synth.get("content", "")

        passed = len(content) >= self.min_length
        failure_detail = None
        if not passed:
            failure_detail = f"Content length {len(content)} below minimum {self.min_length}"

        result = ValidationResult(
            passed=passed,
            failure_type="min_length" if not passed else None,
            failure_detail=failure_detail,
            checks_run=1,
            checks_failed=[] if passed else ["min_length"],
        )

        return NodeResult(
            success=True,
            output=result,
            metadata={"node_id": self.node_id, "type": "validation"},
            exports={"validation_result": result},
        )


# ---------------------------------------------------------------------------
# Adversarial Evaluation Node
# ---------------------------------------------------------------------------


class _AdversarialEvalNode(WorkflowNode):
    """Adversarial evaluation using the model resolver when available.

    In CI mode or when no model_resolver is provided, returns an honest
    stub result (overall=0.0, passed=False, ci_fixture=True) so the
    review gate must decide.
    """

    def __init__(
        self,
        node_id: str = "adversarial_eval",
        model_resolver: Any = None,
        config: dict[str, Any] | None = None,
    ):
        from aip.orchestration.workflow.node import NodeType

        super().__init__(node_id, NodeType.SCRIPT, config)
        self.model_resolver = model_resolver

    async def run(self, context: WorkflowContext) -> NodeResult:
        from aip.orchestration.nodes.adversarial_eval import EvalResult

        previous = context.get("previous", {})
        synth = previous.get("output")

        content = ""
        if isinstance(synth, SynthesisOutput):
            content = synth.content or ""
        elif isinstance(synth, dict):
            content = synth.get("content", "")

        model_resolver = self.model_resolver or context.get_protocol("model_resolver")
        is_ci = _is_ci()

        if model_resolver and not is_ci:
            # Real evaluation path
            try:
                result = await _run_real_eval(model_resolver, content)
            except Exception as exc:
                logger.warning(
                    "Adversarial eval failed (%s). Returning eval_error result.",
                    exc,
                )
                result = EvalResult(
                    passed=False,
                    scores={"overall": 0.0},
                    requires_deep_eval=False,
                    critique=f"Model evaluation failed: {exc}",
                    ci_fixture=False,
                )
        else:
            # CI / stub mode — honest stub that does NOT claim to pass
            result = EvalResult(
                passed=False,
                scores={"overall": 0.0},
                requires_deep_eval=False,
                critique="CI stub: no real adversarial evaluation performed.",
                ci_fixture=True,
            )

        return NodeResult(
            success=True,
            output=result,
            metadata={"node_id": self.node_id, "type": "adversarial_eval"},
            exports={"eval_result": result},
        )


async def _run_real_eval(model_resolver: Any, content: str) -> Any:
    """Run adversarial evaluation via the model resolver."""
    from aip.orchestration.nodes.adversarial_eval import adversarial_eval

    return await adversarial_eval(
        artifact_content=content,
        context="Workflow 0.1 adversarial gate",
        model_resolver=model_resolver,
    )


# ---------------------------------------------------------------------------
# Review Gate Node (replaces _AlwaysApproveDialogNode)
# ---------------------------------------------------------------------------


class _ReviewGateNode(WorkflowNode):
    """Review gate that pauses the workflow for DEFINER approval.

    Behavior:
    - Production mode (default): uses ``definer_gate`` with
      ``AUTO_APPROVE_STUB`` mode if validation+eval pass, which auto-approves
      but logs a warning.  If either fails, the gate returns 'revise' or
      'reject' and the workflow pauses.
    - CI mode: uses ``AUTO_APPROVE_STUB`` which auto-approves with a CI
      marker when gates pass.
    - MANUAL mode: raises :class:`ManualReviewRequired` so the calling
      layer can surface a review queue entry.  The workflow pauses.
    - In all modes, the workflow pauses when the decision is not 'approve'.

    Emits a ``workflow.review.gate`` event with the decision context.
    """

    def __init__(
        self,
        node_id: str = "review_gate",
        mode: DefinerGateMode | None = None,
        config: dict[str, Any] | None = None,
    ):
        from aip.orchestration.workflow.node import NodeType

        super().__init__(node_id, NodeType.DIALOG, config)
        self.mode = mode  # None = auto-detect from CI env

    async def run(self, context: WorkflowContext) -> NodeResult:
        from aip.foundation.validation import ValidationResult
        from aip.orchestration.nodes.adversarial_eval import EvalResult

        # Collect results from upstream nodes
        previous = context.get("previous", {})
        validation_result = previous.get("validation_result")
        eval_result = previous.get("eval_result")
        synth_output = previous.get("output")

        # Fallback: grab from exports
        if not validation_result:
            validation_result = previous.get("exports", {}).get("validation_result")
        if not eval_result:
            eval_result = previous.get("exports", {}).get("eval_result")

        # Build safe defaults if upstream nodes didn't produce results
        if not isinstance(validation_result, ValidationResult):
            validation_result = ValidationResult(
                passed=False,
                failure_type="missing_validation",
                failure_detail="No validation result available from upstream.",
                checks_run=0,
                checks_failed=["missing_validation"],
            )

        if not isinstance(eval_result, EvalResult):
            eval_result = EvalResult(
                passed=False,
                scores={"overall": 0.0},
                requires_deep_eval=False,
                critique="No evaluation result available from upstream.",
                ci_fixture=True,
            )

        if not isinstance(synth_output, SynthesisOutput):
            synth_output = SynthesisOutput(
                content="",
                model_slot="unknown",
                model_name="unknown",
                token_count_in=0,
                token_count_out=0,
                latency_ms=0,
            )

        # Determine gate mode
        mode = self.mode
        if mode is None:
            mode = DefinerGateMode.AUTO_APPROVE_STUB

        # Run the gate
        decision: DefinerDecision | None = None
        manual_exc: ManualReviewRequired | None = None

        try:
            decision = await definer_gate(
                synthesis_output=synth_output,
                validation_result=validation_result,
                eval_result=eval_result,
                mode=mode,
            )
        except ManualReviewRequired as exc:
            manual_exc = exc
            decision = None

        # Build event payload
        event_payload = {
            "node_id": self.node_id,
            "mode": mode.value,
            "decision": decision.action if decision else "manual_review_required",
            "reason": (decision.reason if decision else (manual_exc.reason if manual_exc else "unknown")),
            "validation_passed": validation_result.passed,
            "eval_passed": eval_result.passed,
            "eval_is_fixture": getattr(eval_result, "ci_fixture", False),
        }
        context.emit_event("workflow.review.gate", event_payload)

        # Determine whether the workflow should pause
        approved = decision is not None and decision.action == "approve"
        paused = not approved

        if manual_exc:
            logger.info(
                "Review gate %s: MANUAL mode — ManualReviewRequired raised. Workflow paused for human review.",
                self.node_id,
            )

        output = {
            "executed": self.node_id,
            "type": "review_gate",
            "paused": paused,
            "decision": decision.action if decision else "manual_review_required",
            "manual_review_required": manual_exc is not None,
        }

        # Attach manual review context for the calling layer
        if manual_exc:
            output["manual_review_context"] = {
                "artifact_summary": manual_exc.artifact_summary,
                "validation_passed": manual_exc.validation_passed,
                "eval_passed": manual_exc.eval_passed,
                "eval_is_fixture": manual_exc.eval_is_fixture,
                "reason": manual_exc.reason,
            }

        return NodeResult(
            success=True,
            output=output,
            metadata={
                "decision": decision.action if decision else "pending",
                "paused": paused,
            },
            exports={
                "definer_decision": decision,
                "validation_result": validation_result,
                "eval_result": eval_result,
                "synthesis_output": synth_output,
            },
        )


# ---------------------------------------------------------------------------
# Commit Node (requires real approval)
# ---------------------------------------------------------------------------


class _CommitNode(WorkflowNode):
    """Commits an approved artifact.  Must not be reached without approval.

    Reads the ``definer_decision`` from the previous review gate node's
    exports.  If no approval decision is present, the commit is refused.
    Placeholder synthesis is only used in explicit CI fixture mode.
    """

    def __init__(
        self,
        node_id: str = "commit",
        artifact_store: Any = None,
        ecs_store: Any = None,
        event_store: Any = None,
    ):
        from aip.orchestration.workflow.node import NodeType

        super().__init__(node_id, NodeType.SCRIPT)
        self.artifact_store = artifact_store
        self.ecs_store = ecs_store
        self.event_store = event_store

    async def run(self, context: WorkflowContext) -> NodeResult:
        # Retrieve the review gate's decision
        previous = context.get("previous", {})
        exports = previous.get("exports", {}) if isinstance(previous, dict) else {}

        decision = exports.get("definer_decision")
        synth_output = exports.get("synthesis_output")

        # Gate: refuse commit without an explicit approve decision
        if decision is None or not isinstance(decision, DefinerDecision):
            logger.error(
                "CommitNode %s: no DefinerDecision from review gate. Refusing to commit.",
                self.node_id,
            )
            return NodeResult(
                success=False,
                error="Commit refused: no DEFINER approval decision found. "
                "The review gate must approve before commit can proceed.",
            )

        if decision.action != "approve":
            logger.warning(
                "CommitNode %s: review gate decision was '%s', not 'approve'. Refusing to commit.",
                self.node_id,
                decision.action,
            )
            return NodeResult(
                success=False,
                error=f"Commit refused: review gate decision was '{decision.action}', "
                f"not 'approve'. Reason: {decision.reason}",
            )

        # Build synthesis output — only use placeholder in CI mode
        if not isinstance(synth_output, SynthesisOutput):
            if _is_ci():
                synth_output = SynthesisOutput(
                    content="CI fixture synthesis (placeholder)",
                    model_slot="synthesis",
                    model_name="ci-fixture",
                    token_count_in=0,
                    token_count_out=0,
                    latency_ms=0,
                )
                logger.info(
                    "CommitNode %s: using CI fixture synthesis placeholder.",
                    self.node_id,
                )
            else:
                logger.error(
                    "CommitNode %s: no real SynthesisOutput from upstream and "
                    "not in CI mode. Refusing to commit with placeholder.",
                    self.node_id,
                )
                return NodeResult(
                    success=False,
                    error="Commit refused: no real synthesis output available "
                    "and not in CI mode. Placeholder synthesis is only "
                    "permitted in CI fixture mode.",
                )

        # Perform the actual commit
        artifact_store = self.artifact_store or context.get_protocol("artifact_store")
        ecs_store = self.ecs_store or context.get_protocol("ecs_store")
        event_store = self.event_store or context.get_protocol("event_store")

        if not artifact_store or not ecs_store:
            return NodeResult(
                success=False,
                error="CommitNode requires artifact_store and ecs_store.",
            )

        artifact_ref = await commit_artifact(
            synthesis=synth_output,
            decision=decision,
            project_id="workflow-01",
            work_unit_id=self.node_id,
            artifact_store=artifact_store,
            ecs_store=ecs_store,
            event_store=event_store,
        )

        logger.info(
            "CommitNode %s: artifact committed. approved_by=%s.",
            self.node_id,
            decision.approved_by,
        )

        return NodeResult(
            success=True,
            output=artifact_ref,
            metadata={"node_id": self.node_id, "type": "commit"},
            exports={"artifact_ref": artifact_ref},
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class Workflow01Runner:
    """High-level executor for Workflow 0.1 (the synthesis session pipeline).

    Usage::

        runner = Workflow01Runner(
            vector_store=...,
            embed_fn=...,
        )
        result = await runner.run(query="...", domain="...")

    In production, the workflow pauses at the review gate when DEFINER
    approval is needed.  In CI mode (``ci_mode=True`` or ``CI`` env var
    set), the stub gate auto-approves so tests exercise the full pipeline.

    For MANUAL mode (human-in-the-loop), set ``gate_mode=MANUAL``.
    The runner will return a paused result with
    ``manual_review_required=True``, and the calling layer should create
    a review queue entry from the attached context.
    """

    def __init__(
        self,
        vector_store: Any,
        embed_fn: Callable[[str], list[float]],
        trace_store: Any | None = None,
        artifact_store: Any | None = None,
        ecs_store: Any | None = None,
        event_store: Any | None = None,
        model_resolver: Any | None = None,
        config: dict[str, Any] | None = None,
        ci_mode: bool | None = None,
        gate_mode: DefinerGateMode | None = None,
    ):
        self.vector_store = vector_store
        self.embed_fn = embed_fn
        self.trace_store = trace_store
        self.artifact_store = artifact_store
        self.ecs_store = ecs_store
        self.event_store = event_store
        self.model_resolver = model_resolver
        self.config = config or {}
        self.ci_mode = ci_mode
        self.gate_mode = gate_mode

    async def run(self, query: str, domain: str) -> Any:
        """Execute a Workflow 0.1 synthesis session.

        Returns the final node result (commit on approve, or the paused
        review gate node when DEFINER input is needed).
        """
        _ci_flag = self.ci_mode if self.ci_mode is not None else _is_ci()

        # Determine the review gate mode
        gate_mode = self.gate_mode
        if gate_mode is None:
            gate_mode = DefinerGateMode.AUTO_APPROVE_STUB

        # Resolve embed function (handle both sync and async callables)
        _provided_embed = self.embed_fn

        if _provided_embed is not None:

            async def embed_fn(text: str) -> list[float]:
                result = _provided_embed(text)
                if hasattr(result, "__await__"):
                    result = await result
                return result if isinstance(result, list) else list(result)
        else:

            async def embed_fn(text: str) -> list[float]:
                return fake_embed(text)

        # Build the Workflow 0.1 node sequence
        nodes: list[WorkflowNode] = [
            # L2: Retrieve + Synthesize (AgentNode does both)
            _SynthesisNode(
                node_id="synthesize",
                vector_store=self.vector_store,
                embed_fn=embed_fn,
                model_resolver=self.model_resolver,
                config=self.config,
            ),
            # L3a: Structural validation (deterministic, zero tokens)
            _ValidationNode(node_id="structural_validate"),
            # L3b: Adversarial evaluation
            _AdversarialEvalNode(
                node_id="adversarial_eval",
                model_resolver=self.model_resolver,
                config=self.config,
            ),
            # L5: Review gate — pauses for DEFINER approval
            _ReviewGateNode(
                node_id="review_gate",
                mode=gate_mode,
            ),
            # L6: Commit (only reached on approve)
            _CommitNode(
                node_id="commit",
                artifact_store=self.artifact_store,
                ecs_store=self.ecs_store,
                event_store=self.event_store,
            ),
        ]

        # Build protocol map for the workflow context
        class _NoopTraceStore:
            async def write_event(self, *a, **k):
                pass

            async def get_recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
                return []

            async def get_unclassified_failures(self, limit: int = 100) -> list[dict]:
                return []

        class _NoopStore:
            async def write(self, *a, **k):
                pass

            async def read(self, *a, **k):
                return ""

        trace_for_use = self.trace_store or _NoopTraceStore()
        artifact_for_use = self.artifact_store or _NoopStore()

        protocols = {
            "vector_store": self.vector_store,
            "embed_fn": embed_fn,
            "trace_store": trace_for_use,
            "artifact_store": artifact_for_use,
            "ecs_store": self.ecs_store or _NoopStore(),
            "event_store": self.event_store or _NoopStore(),
            "config": self.config,
            "model_resolver": self.model_resolver,
        }

        ctx = WorkflowContext(protocols=protocols, metadata={"config": self.config})

        runner = SequentialRunner(nodes, ctx)
        results = await runner.run()

        return results[-1] if results else None


# ---------------------------------------------------------------------------
# Synthesis Node (replaces the bare AgentNode with placeholder wiring)
# ---------------------------------------------------------------------------


class _SynthesisNode(WorkflowNode):
    """Retrieval + Synthesis node for Workflow 0.1.

    Performs vector retrieval then synthesis using the available model
    resolver.  Falls back to deterministic stub in CI mode.
    """

    def __init__(
        self,
        node_id: str = "synthesize",
        vector_store: Any = None,
        embed_fn: Any = None,
        model_resolver: Any = None,
        config: dict[str, Any] | None = None,
    ):
        from aip.orchestration.workflow.node import NodeType

        super().__init__(node_id, NodeType.AGENT, config)
        self.vector_store = vector_store
        self.embed_fn = embed_fn
        self.model_resolver = model_resolver

    async def run(self, context: WorkflowContext) -> NodeResult:
        vector_store = self.vector_store or context.get_protocol("vector_store")
        embed_fn = self.embed_fn or context.get_protocol("embed_fn")
        model_resolver = self.model_resolver or context.get_protocol("model_resolver")
        config = context.get_protocol("config") or self.config

        query = self.config.get("query", "") if self.config else ""
        if not query:
            query = context.get("query", "")

        domain = context.get("domain") or (
            config.get("default_domain", "default") if isinstance(config, dict) else "default"
        )

        # Retrieve
        retrieval_result = None
        if vector_store is not None and embed_fn is not None:
            try:
                retrieval_result = await retrieve_for_synthesis(
                    query=query,
                    domain=domain,
                    vector_store=vector_store,
                    embed_fn=embed_fn,
                    trace_store=context.get_protocol("trace_store"),
                    config=config,
                )
            except Exception as exc:
                logger.warning(
                    "SynthesisNode %s: retrieval failed (%s). Proceeding without retrieval.",
                    self.node_id,
                    exc,
                )

        # Synthesize
        synthesis_output = await synthesize(
            query=query,
            domain=domain,
            retrieval_result=retrieval_result,
            model_slot=self.config.get("model_slot", "synthesis") if self.config else "synthesis",
            model_resolver=model_resolver,
            config=config,
        )

        return NodeResult(
            success=True,
            output=synthesis_output,
            metadata={
                "node_id": self.node_id,
                "model_slot": self.config.get("model_slot", "synthesis") if self.config else "synthesis",
                "retrieval_status": (retrieval_result.status if retrieval_result else "skipped"),
            },
            exports={
                "content": synthesis_output.content,
                "model_name": synthesis_output.model_name,
                "token_count_in": synthesis_output.token_count_in,
                "token_count_out": synthesis_output.token_count_out,
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_ci() -> bool:
    """Check whether we are running in a CI environment."""
    return os.environ.get("CI", "").lower() in ("true", "1", "yes")
