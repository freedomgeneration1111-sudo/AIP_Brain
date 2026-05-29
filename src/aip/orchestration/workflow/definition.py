"""
Workflow definition model.

Provides a richer structure than a flat list of nodes so that
workflows can declare top-level "finally" (cleanup) and "on_error"
(compensation) handler nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aip.orchestration.workflow.node import WorkflowNode


@dataclass
class WorkflowDefinition:
    """
    Represents a complete workflow definition.

    - nodes: the main sequence of nodes to execute.
    - finally_nodes: nodes that should always run at the end (success or failure),
      in the order they are declared (they will be executed in reverse for typical
      "finally" semantics if desired by the runner).
    - on_error_nodes: nodes that run only on failure paths (compensation).
    - metadata: arbitrary workflow-level configuration.
    """

    nodes: list[WorkflowNode]
    finally_nodes: list[WorkflowNode] = field(default_factory=list)
    on_error_nodes: list[WorkflowNode] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
