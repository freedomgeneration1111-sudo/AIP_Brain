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
        # Placeholder — will delegate to the Phase 1 retrieve + synthesis machinery in later chunks
        return NodeResult(
            success=True,
            output={"executed": self.node_id, "type": "agent", "model_slot": self.model_slot}
        )


class ConditionNode(WorkflowNode):
    """Jinja2-based branching node (zero tokens)."""

    def __init__(self, node_id: str, condition: str, config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.CONDITION, config)
        self.condition = condition

    async def run(self, context: "WorkflowContext") -> NodeResult:
        # Placeholder
        return NodeResult(success=True, output={"executed": self.node_id, "type": "condition"})


class DialogNode(WorkflowNode):
    """Structured DEFINER gate node that pauses the workflow."""

    def __init__(self, node_id: str, prompt: str, config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.DIALOG, config)
        self.prompt = prompt

    async def run(self, context: "WorkflowContext") -> NodeResult:
        # Must emit an event before "pausing" (per spec)
        # In this foundation chunk we just record the intent.
        context.emit_event("dialog.paused", {"node_id": self.node_id, "prompt": self.prompt})
        return NodeResult(success=True, output={"executed": self.node_id, "type": "dialog", "paused": True})


class ParallelNode(WorkflowNode):
    """Concurrent execution node (inherits parent budget)."""

    def __init__(self, node_id: str, children: list[str], config: dict[str, Any] | None = None):
        super().__init__(node_id, NodeType.PARALLEL, config)
        self.children = children

    async def run(self, context: "WorkflowContext") -> NodeResult:
        # Real parallel execution in a later chunk
        return NodeResult(success=True, output={"executed": self.node_id, "type": "parallel", "children": self.children})
