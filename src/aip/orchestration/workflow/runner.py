"""
Sequential workflow runner (CHUNK-2.1 foundation).

Executes nodes one after another. Supports basic script and agent nodes
for now. Condition, dialog, and parallel support will be added in
subsequent chunks.
"""

from __future__ import annotations

import asyncio
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
        """
        Execute nodes sequentially, with basic support for condition branching.

        A ConditionNode can influence the next node via:
        - node.config['next_on_true'] / node.config['next_on_false']
        """
        results: list[NodeResult] = []
        i = 0
        n = len(self.nodes)

        while i < n:
            node = self.nodes[i]

            # Budget guard
            if node.requires_model() and not self.context.consume_budget(100):
                results.append(NodeResult(success=False, error="Budget exhausted"))
                break

            result = await node.run(self.context)
            results.append(result)
            self.context.set("last_result", result.output)

            if not result.success:
                break

            # Basic branching support for conditions
            if node.node_type == NodeType.CONDITION:
                cond_result = result.output.get("result", False) if isinstance(result.output, dict) else False
                next_key = "next_on_true" if cond_result else "next_on_false"
                target_id = node.config.get(next_key)

                if target_id:
                    # Find the target node by id
                    try:
                        target_idx = next(idx for idx, nd in enumerate(self.nodes) if nd.node_id == target_id)
                        i = target_idx
                        continue
                    except StopIteration:
                        pass

            # Pause execution if a dialog node signaled it needs to wait for DEFINER input
            if node.node_type == NodeType.DIALOG:
                if isinstance(result.output, dict) and result.output.get("paused"):
                    # Stop the runner; the caller is responsible for resuming later
                    break

            # Parallel execution support (CHUNK-2.4)
            if node.node_type == NodeType.PARALLEL:
                child_ids = getattr(node, "children", [])
                child_nodes = [n for n in self.nodes if n.node_id in child_ids]

                if child_nodes:
                    # Each parallel branch gets a forked context (budget & variables inherited)
                    tasks = []
                    branch_contexts = []
                    for child in child_nodes:
                        child_ctx = self.context.fork_for_parallel()
                        branch_contexts.append(child_ctx)
                        mini_runner = SequentialRunner([child], child_ctx)
                        tasks.append(mini_runner.run())

                    branch_results = await asyncio.gather(*tasks, return_exceptions=True)

                    # Collect results and merge key outputs back to parent context if desired
                    for br in branch_results:
                        if isinstance(br, list):
                            results.extend(br)

                i += 1
                continue

            i += 1

        return results
