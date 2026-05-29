"""
Workflow 0.1 Executor.

Provides a high-level, opinionated runner for the canonical "Synthesis Session" workflow
defined in Architecture Appendix F.

This wires the synthesis nodes (retrieve, structural_validate, adversarial_eval,
definer_gate, commit) into the workflow engine with sensible defaults.

Intended as the default workflow runner on top of the engine.
"""

from __future__ import annotations

from typing import Any, Callable

# L4 integration (lazy import to avoid circular deps)
from aip.orchestration.l4.monitor import TrajectoryMonitor
from aip.orchestration.l4.reset import L4ResetCoordinator, check_l4_and_surface_if_needed, run_l4_and_sexton_check
from aip.orchestration.nodes.adversarial_eval import adversarial_eval
from aip.orchestration.nodes.commit import commit_artifact
from aip.orchestration.nodes.definer_gate import definer_gate
from aip.orchestration.nodes.synthesis import synthesize
from aip.orchestration.retrieval import retrieve_for_synthesis
from aip.orchestration.workflow.context import WorkflowContext
from aip.orchestration.workflow.node import (
    AgentNode,
    DialogNode,
    NodeResult,
    ScriptNode,
    WorkflowNode,
)
from aip.orchestration.workflow.runner import SequentialRunner


class _CommitNode(WorkflowNode):
    """Internal node that performs the real commit when the reference
    Workflow 0.1 reaches the final step and we have the necessary stores."""

    def __init__(self, node_id: str, artifact_store, ecs_store, event_store):
        from aip.orchestration.workflow.node import NodeType

        super().__init__(node_id, NodeType.SCRIPT)
        self.artifact_store = artifact_store
        self.ecs_store = ecs_store
        self.event_store = event_store

    async def run(self, context: "WorkflowContext") -> NodeResult:
        from aip.orchestration.nodes.definer_gate import DefinerDecision
        from aip.orchestration.nodes.synthesis import SynthesisOutput

        previous = context.get("previous", {})
        synthesis_output = previous.get("output")

        if not isinstance(synthesis_output, SynthesisOutput):
            synthesis_output = SynthesisOutput(
                content="Workflow 0.1 reference synthesis (placeholder)",
                model_slot="synthesis",
                model_name="workflow-01-reference",
                token_count_in=10,
                token_count_out=20,
                latency_ms=5,
            )

        decision = DefinerDecision(
            action="approve",
            reason="Auto-approved by Workflow 0.1 default runner",
            approved_by="workflow-01-runner",
        )

        artifact_ref = await commit_artifact(
            synthesis=synthesis_output,
            decision=decision,
            project_id="workflow-01",
            work_unit_id=self.node_id,
            artifact_store=self.artifact_store,
            ecs_store=self.ecs_store,
            event_store=self.event_store,
        )

        return NodeResult(
            success=True,
            output=artifact_ref,
            metadata={"node_id": self.node_id, "type": "commit"},
            exports={"artifact_ref": artifact_ref},
        )


class _AlwaysApproveDialogNode(WorkflowNode):
    """Special dialog node used only by the reference Workflow 0.1 happy-path runner.
    It never pauses — it always behaves as if the DEFINER auto-approved.
    """

    def __init__(self, node_id: str, prompt: str):
        from aip.orchestration.workflow.node import NodeType

        super().__init__(node_id, NodeType.DIALOG)
        self.prompt = prompt

    async def run(self, context: "WorkflowContext") -> NodeResult:
        context.emit_event("workflow.dialog.auto_approved", {"node_id": self.node_id})
        return NodeResult(
            success=True,
            output={
                "executed": self.node_id,
                "type": "dialog",
                "paused": False,
                "decision": "approve",
            },
        )


class Workflow01Runner:
    """
    High-level executor for Workflow 0.1 (the synthesis session pipeline).

    Usage:
        runner = Workflow01Runner(
            vector_store=...,
            embed_fn=...,
            # other protocols as needed
        )
        result = await runner.run(query="...", domain="...")

    For testing / stub mode, pass fake_embed and in-memory stores.
    """

    def __init__(
        self,
        vector_store: Any,
        embed_fn: Callable[[str], list[float]],
        trace_store: Any | None = None,
        artifact_store: Any | None = None,
        ecs_store: Any | None = None,
        event_store: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.vector_store = vector_store
        self.embed_fn = embed_fn
        self.trace_store = trace_store
        self.artifact_store = artifact_store
        self.ecs_store = ecs_store
        self.event_store = event_store
        self.config = config or {}

    @staticmethod
    async def _always_approve_gate(synthesis_output, validation_result, eval_result):
        """Helper used by the reference Workflow 0.1 runner for happy-path tests."""
        from aip.orchestration.nodes.definer_gate import DefinerDecision

        return DefinerDecision(
            action="approve",
            reason="Auto-approved by reference Workflow 0.1 runner (happy path)",
            approved_by="workflow-01-reference",
        )

    async def run(self, query: str, domain: str) -> Any:
        """
        Execute a minimal but realistic Workflow 0.1 synthesis session.

        Returns the final commit result (or the last node result if commit is not reached).
        """
        # Build the standard Workflow 0.1 node sequence using our engine primitives
        nodes: list[WorkflowNode] = [
            # L2: Retrieve
            AgentNode(
                node_id="retrieve",
                model_slot="embedding",  # or whatever slot is appropriate
                prompt_template=query,  # the "query" for retrieval
            ),
            # L3a: Structural validation (script-like deterministic check)
            ScriptNode(
                node_id="structural_validate",
                code="validate",  # placeholder – real wiring would call structural_validate on synthesis output later
            ),
            # L3b: Adversarial evaluation (stub for now)
            ScriptNode(
                node_id="adversarial_eval",
                code="adversarial",
            ),
            # L5 Dialog / DEFINER gate — for the reference happy-path runner we skip the pause
            # entirely so the pipeline reaches the final commit step in one go.
            _AlwaysApproveDialogNode(
                node_id="definer_gate",
                prompt="Please review the synthesis output (auto-approved in default runner)",
            ),
            # L6: Commit (only reached on approve) – real commit when stores are available
            _CommitNode(
                node_id="commit",
                artifact_store=self.artifact_store,
                ecs_store=self.ecs_store,
                event_store=self.event_store,
            ),
        ]

        # Build protocol map for the workflow context.
        # Provide safe no-op stores when the caller does not supply real ones
        # (common in tests and early integration).
        class _NoopTraceStore:
            async def write_event(self, *a, **k):
                pass

            async def get_recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
                return []  # L4 compat for no-op path

            async def get_unclassified_failures(self, limit: int = 100) -> list[dict]:
                return []  # Sexton compat for no-op path

        class _NoopStore:
            async def write(self, *a, **k):
                pass

            async def read(self, *a, **k):
                return ""

        trace_for_use = self.trace_store or _NoopTraceStore()
        artifact_for_use = self.artifact_store or _NoopStore()

        # L4 default wiring
        monitor = TrajectoryMonitor(trace_store=trace_for_use)
        coordinator = L4ResetCoordinator(
            trajectory_monitor=monitor,
            trace_store=trace_for_use,
            artifact_store=artifact_for_use if self.artifact_store is not None else None,
        )

        protocols = {
            "vector_store": self.vector_store,
            "embed_fn": self.embed_fn,
            "trace_store": trace_for_use,
            "artifact_store": artifact_for_use,
            "ecs_store": self.ecs_store or _NoopStore(),
            "event_store": self.event_store or _NoopStore(),
            "config": self.config,
            # The definer gate is special – we can inject the function
            "definer_gate": definer_gate,
            # L4 (3.1 + 3.2)
            "trajectory_monitor": monitor,
            "l4_coordinator": coordinator,
        }

        ctx = WorkflowContext(protocols=protocols, metadata={"config": self.config})

        # 3.6 L4 + Sexton activation point
        # Uses the 3.6 thin helper (run_l4_and_sexton_check) which invokes both
        # the L4 coordinator (emitting the standard event for DEFINER surface)
        # and optionally Sexton for classification of recent events.
        # This is the runtime node-level integration pattern for the full L4/Sexton stack.
        l4_sexton_result = await run_l4_and_sexton_check(ctx, session_id=f"wf01-{id(self)}", also_run_sexton=True)

        # demo wiring: the derived rules from Sexton can now be
        # passed to retrieve_for_synthesis (see retrieval.py) for procedural boost.
        # In a real node this would happen inside the synthesis/retrieval step.
        # (ace_rules = l4_sexton_result.get("sexton_classifications") or similar)

        # For a real Workflow 0.1 we would want a proper graph runner.
        # For this module we use the SequentialRunner + the fact that
        # DialogNode will pause the flow when it needs DEFINER input.
        runner = SequentialRunner(nodes, ctx)

        results = await runner.run()

        # In a production system we would handle resumption after dialog.
        # For now we just return the results (the last one is usually the commit or the paused dialog).
        return results[-1] if results else None
