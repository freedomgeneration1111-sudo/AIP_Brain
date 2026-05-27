"""
Sequential workflow runner (CHUNK-2.1 foundation).

Executes nodes one after another. Supports basic script and agent nodes
for now. Condition, dialog, and parallel support will be added in
subsequent chunks.
"""

from __future__ import annotations

from typing import Any

from aip.orchestration.workflow.context import WorkflowContext
from aip.orchestration.workflow.node import NodeResult, NodeType, WorkflowNode


class SequentialRunner:
    """
    Very simple sequential executor for the foundation phase.

    In later chunks this will evolve into a proper graph executor
    that handles branching (condition), pausing (dialog), and concurrency (parallel).
    """

    def __init__(self, nodes: list[WorkflowNode], context: WorkflowContext | None = None):
        self.nodes = nodes
        self.context = context or WorkflowContext()

    async def run(self) -> list[NodeResult]:
        results: list[NodeResult] = []

        for node in self.nodes:
            # Budget guard (very basic for foundation)
            if node.requires_model() and not self.context.consume_budget(100):  # arbitrary unit
                results.append(NodeResult(success=False, error="Budget exhausted"))
                break

            result = await node.run(self.context)
            results.append(result)

            # Store the last result under a conventional key for simple chaining
            self.context.set("last_result", result.output)

            if not result.success:
                break

        return results
