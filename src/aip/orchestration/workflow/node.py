"""
L5 Workflow Node abstractions (CHUNK-2.1 foundation).

Defines the common contract and the five node type bases required by
Architecture Rev 5.2 §11.1.

All storage access must go through injected protocols (enforced by
the Phase 1 layering rules).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import jinja2


class NodeType(str, Enum):
    SCRIPT = "script"
    AGENT = "agent"
    CONDITION = "condition"
    DIALOG = "dialog"
    PARALLEL = "parallel"


@dataclass
class NodeResult:
    """Result returned by any node execution."""
    success: bool
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    exports: dict[str, Any] = field(default_factory=dict)  # Data this node wants to expose to later nodes


class WorkflowNode(ABC):
    """
    Abstract base for all workflow nodes.

    Invariants (from Architecture §11.1):
    - script and condition nodes must consume zero tokens.
    - agent nodes must declare an explicit model_slot.
    - dialog nodes must emit an event before returning a "paused" result.
    - parallel nodes inherit the parent's budget context.
    - No node implementation may import storage classes directly.
    """

    def __init__(self, node_id: str, node_type: NodeType, config: dict[str, Any] | None = None):
        self.node_id = node_id
        self.node_type = node_type
        self.config = config or {}

    @abstractmethod
    async def run(self, context: "WorkflowContext") -> NodeResult:
        """
        Execute the node.

        The context provides:
        - variables / state
        - budget tracking
        - protocol instances (stores, etc.)
        - event emission capability (for dialog nodes)
        """
        raise NotImplementedError

    def requires_model(self) -> bool:
        """Whether this node type performs a model call."""
        return self.node_type == NodeType.AGENT

    def is_deterministic(self) -> bool:
        """Whether this node is guaranteed to be deterministic (zero tokens or pure logic)."""
        return self.node_type in (NodeType.SCRIPT, NodeType.CONDITION)


class ScriptNode(WorkflowNode):
    """Deterministic Python execution node (zero tokens)."""

    def __init__(self, node_id: str, code: str, config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.SCRIPT, config)
        self.code = code  # In a real implementation this would be a safe exec or registered function

    async def run(self, context: "WorkflowContext") -> NodeResult:
        # Placeholder for CHUNK-2.1 — real implementation comes in a follow-up chunk
        return NodeResult(success=True, output={"executed": self.node_id, "type": "script"})


class AgentNode(WorkflowNode):
    """Model-backed synthesis node. Must declare model_slot."""

    def __init__(self, node_id: str, model_slot: str, prompt_template: str, config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.AGENT, config)
        if not model_slot:
            raise ValueError("AgentNode requires an explicit model_slot")
        self.model_slot = model_slot
        self.prompt_template = prompt_template

    async def run(self, context: "WorkflowContext") -> NodeResult:
        """
        Real agent execution for CHUNK-2.5.

        Uses the Phase 1 retrieval + synthesis path.
        Expects the following to be available via context.protocols or context:
          - vector_store (VectorStore protocol)
          - embed_fn (callable)
          - trace_store (optional TraceStore)
          - config (optional)
        Falls back to fake_embed + no-op stores when running in minimal test mode.
        """
        from aip.orchestration.retrieval import retrieve_for_synthesis, fake_embed
        from aip.orchestration.nodes.synthesis import synthesize as phase1_synthesize

        # Resolve dependencies from context (with safe fallbacks)
        vector_store = context.get_protocol("vector_store")
        embed_fn = context.get_protocol("embed_fn") or (lambda text: fake_embed(text))
        trace_store = context.get_protocol("trace_store")
        config = context.get_protocol("config") or context.metadata.get("config")

        # For a minimal viable agent in a workflow, we use the node_id + prompt as the "query"
        # In real usage the prompt_template would be rendered with context variables.
        query = self.prompt_template or f"Execute agent node {self.node_id}"

        # Default domain can come from context or config
        domain = context.get("domain") or (config.get("default_domain") if isinstance(config, dict) else "default")

        retrieval_result = await retrieve_for_synthesis(
            query=query,
            domain=domain,
            vector_store=vector_store,
            embed_fn=embed_fn,
            trace_store=trace_store,
            config=config,
        )

        synthesis_output = await phase1_synthesize(
            query=query,
            domain=domain,
            retrieval_result=retrieval_result,
            model_slot=self.model_slot,
            config=config,
        )

        # Export useful data for downstream nodes (CHUNK-2.7)
        exports = {
            "content": synthesis_output.content,
            "model_name": synthesis_output.model_name,
            "token_count_in": synthesis_output.token_count_in,
            "token_count_out": synthesis_output.token_count_out,
        }

        return NodeResult(
            success=True,
            output=synthesis_output,
            metadata={
                "node_id": self.node_id,
                "model_slot": self.model_slot,
                "retrieval_status": retrieval_result.status,
            },
            exports=exports,
        )


class ConditionNode(WorkflowNode):
    """Jinja2-based branching node (zero tokens)."""

    def __init__(self, node_id: str, condition: str, config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.CONDITION, config)
        self.condition = condition
        self._env = jinja2.Environment(autoescape=False)

    async def run(self, context: "WorkflowContext") -> NodeResult:
        template = self._env.from_string(self.condition)
        rendered = template.render(**context.variables, **context.metadata)
        # Very simple truthiness evaluation for now
        result = rendered.strip().lower() not in ("", "false", "0", "none", "null")
        return NodeResult(
            success=True,
            output={"result": result, "rendered": rendered},
            metadata={"condition": self.condition}
        )


class DialogNode(WorkflowNode):
    """Structured DEFINER gate node that can pause the workflow and emit events."""

    def __init__(
        self,
        node_id: str,
        prompt: str,
        gate_callable=None,   # Optional callable: async def(synthesis, validation, eval) -> DefinerDecision
        synthesis_output=None,
        validation_result=None,
        eval_result=None,
        config: dict[str, Any] | None = None,
    ):
        super().__init__(node_id, NodeType.DIALOG, config)
        self.prompt = prompt
        self.gate_callable = gate_callable
        self.synthesis_output = synthesis_output
        self.validation_result = validation_result
        self.eval_result = eval_result

    async def run(self, context: "WorkflowContext") -> NodeResult:
        """
        Execute the dialog / DEFINER gate.

        If a gate_callable is provided (or can be retrieved from context as 'definer_gate'),
        it is invoked with the provided synthesis/validation/eval results.

        Per Architecture §11.1: dialog nodes must produce an event before resuming.
        The runner will stop when this node returns paused=True.
        """
        gate = self.gate_callable or context.get_protocol("definer_gate")

        decision = None
        if gate is not None and all([self.synthesis_output, self.validation_result, self.eval_result]):
            try:
                decision = await gate(
                    self.synthesis_output,
                    self.validation_result,
                    self.eval_result,
                )
            except Exception:
                decision = None

        event_payload = {
            "node_id": self.node_id,
            "prompt": self.prompt,
            "decision": getattr(decision, "action", "pending") if decision else "pending",
            "reason": getattr(decision, "reason", None) if decision else "Dialog node reached - awaiting DEFINER input",
        }

        context.emit_event("workflow.dialog.paused", event_payload)

        paused = decision is None or getattr(decision, "action", None) != "approve"

        return NodeResult(
            success=True,
            output={
                "executed": self.node_id,
                "type": "dialog",
                "paused": paused,
                "decision": getattr(decision, "action", "pending") if decision else "pending",
            },
            metadata={"event_emitted": True},
        )


class ParallelNode(WorkflowNode):
    """Concurrent execution node (inherits parent budget).

    Advanced configuration (via config dict or direct attributes for convenience):
      - children: list of child node ids to run in parallel
      - dependencies: dict mapping child_id -> list of prerequisite child_ids
      - merge_strategy: "collect_all", "first_success", "fail_fast", or custom callable
      - continue_on_error: bool (default False)
    """

    def __init__(self, node_id: str, children: list[str], config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.PARALLEL, config)
        self.children = children or []
        self.dependencies = (config or {}).get("dependencies", {}) if config else {}
        self.merge_strategy = (config or {}).get("merge_strategy", "collect_all") if config else "collect_all"
        self.continue_on_error = (config or {}).get("continue_on_error", False) if config else False

    async def run(self, context: "WorkflowContext") -> NodeResult:
        """
        ParallelNode itself is mostly a marker + metadata holder.
        The actual concurrent execution (including dependency resolution,
        merging, and error aggregation) is driven by the runner.
        """
        return NodeResult(
            success=True,
            output={
                "executed": self.node_id,
                "type": "parallel",
                "children": self.children,
                "dependencies": self.dependencies,
                "merge_strategy": self.merge_strategy,
                "continue_on_error": self.continue_on_error,
            }
        )


# --- CHUNK-4.5 extensions: Review and Re-Synthesis nodes (additive) ---

from aip.orchestration.review import review_artifact
from aip.orchestration.re_synthesize import re_synthesize


class ReviewNode(WorkflowNode):
    """Node that runs the Phase 2 review gate (4.1)."""

    def __init__(self, node_id: str, config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.DIALOG, config)  # Treated as dialog-like for pausing semantics

    async def run(self, context: "WorkflowContext") -> NodeResult:
        # Prefer the Phase 2 versioned/queryable stores (4.3/4.4) when available
        artifact_store = context.get_protocol("versioned_artifact_store") or context.get_protocol("artifact_store")
        ecs_store = context.get_protocol("guardrailed_ecs_store") or context.get_protocol("ecs_store")
        event_store = context.get_protocol("queryable_event_store") or context.get_protocol("event_store")
        trace_store = context.get_protocol("trace_store")
        config = context.get_protocol("config")

        # For now, we assume the artifact_id is in context or config
        artifact_id = context.get("artifact_id") or self.config.get("artifact_id")
        if not artifact_id:
            return NodeResult(success=False, error="ReviewNode requires artifact_id in context or config")

        try:
            verdict = await review_artifact(
                artifact_id=artifact_id,
                artifact_store=artifact_store,
                ecs_store=ecs_store,
                event_store=event_store,
                trace_store=trace_store,
                config=config,
            )

            # Signal pause to the runner if the verdict requires intervention or re-synthesis
            is_pause = verdict.verdict in ("REJECTED", "NEEDS_REVISION")
            output = {
                "verdict": verdict,
                "paused": is_pause,
            }

            return NodeResult(
                success=True,
                output=output,
                metadata={"verdict": verdict.verdict},
                exports={"review_verdict": verdict},
            )
        except Exception as e:
            return NodeResult(success=False, error=str(e))


class ReSynthesizeNode(WorkflowNode):
    """Node that runs the re-synthesis loop (4.2) on rejection."""

    def __init__(self, node_id: str, config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.AGENT, config)  # Agent-like (can consume tokens)

    async def run(self, context: "WorkflowContext") -> NodeResult:
        # Prefer the Phase 2 versioned/queryable stores (4.3/4.4) when available
        artifact_store = context.get_protocol("versioned_artifact_store") or context.get_protocol("artifact_store")
        ecs_store = context.get_protocol("guardrailed_ecs_store") or context.get_protocol("ecs_store")
        event_store = context.get_protocol("queryable_event_store") or context.get_protocol("event_store")
        trace_store = context.get_protocol("trace_store")
        config = context.get_protocol("config")

        artifact_id = context.get("artifact_id") or self.config.get("artifact_id")

        # Robust lookup for the previous review verdict (from 4.1 ReviewNode)
        rejection = (
            context.get("review_verdict")
            or (context.get("previous", {}) or {}).get("review_verdict")
            or (context.get("last_result") or {}).get("review_verdict")
        )

        if not artifact_id or not rejection:
            return NodeResult(success=False, error="ReSynthesizeNode requires artifact_id and review_verdict in context")

        try:
            # Use the Phase 1 synthesis stub as the synthesize_fn for now
            from aip.orchestration.nodes.synthesis import synthesize as phase1_synth

            async def synth_wrapper(artifact_id, failure_context):
                # Simplified wrapper — real integration in 4.5 will be richer
                return await phase1_synth(
                    query=str(failure_context),
                    domain="re_synthesis",
                    retrieval_result=None,
                    config=config,
                )

            verdict = await re_synthesize(
                artifact_id=artifact_id,
                rejection=rejection,
                artifact_store=artifact_store,
                ecs_store=ecs_store,
                event_store=event_store,
                trace_store=trace_store,
                synthesize_fn=synth_wrapper,
                config=config,
            )
            return NodeResult(success=True, output=verdict, exports={"re_synthesis_verdict": verdict})
        except Exception as e:
            return NodeResult(success=False, error=str(e))
