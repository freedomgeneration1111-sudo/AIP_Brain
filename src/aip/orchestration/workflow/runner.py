"""
Sequential workflow runner (CHUNK-2.1 foundation).

Executes nodes one after another. Supports basic script and agent nodes
for now. Condition, dialog, and parallel support will be added in
subsequent chunks.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from aip.orchestration.workflow.context import WorkflowContext
from aip.orchestration.workflow.instance import SuspendedWorkflow
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

    async def run_until_pause(self, workflow_id: str | None = None) -> tuple[list[NodeResult], SuspendedWorkflow | None]:
        """
        Run the workflow until it either completes or hits a dialog pause point.

        Returns:
            (list of NodeResults, SuspendedWorkflow or None)
        """
        results = await self.run()

        # Check if the last event or result indicates a dialog pause
        last_result = results[-1] if results else None
        is_paused = (
            last_result
            and isinstance(last_result.output, dict)
            and last_result.output.get("paused")
            and last_result.output.get("type") == "dialog"
        )

        if not is_paused:
            return results, None

        # Try to get the node id from the dialog result itself
        paused_info = last_result.output if isinstance(last_result.output, dict) else {}
        current_node_id = paused_info.get("executed") or getattr(last_result, "metadata", {}).get("node_id")

        # Build a suspended workflow snapshot
        suspended = SuspendedWorkflow(
            workflow_id=workflow_id or str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            status="suspended",
            current_node_id=current_node_id,
            variables=self.context.variables.copy(),
            completed_nodes=[
                {"node_id": getattr(r, "metadata", {}).get("node_id"), "result": r.output}
                for r in results
            ],
            suspended_nodes=[paused_info] if paused_info else [],
            metadata={"last_event": self.context.events[-1] if self.context.events else None},
        )

        return results, suspended

    @classmethod
    def from_suspended(
        cls,
        suspended: SuspendedWorkflow,
        decision: Any,   # DefinerDecision or dict
        nodes: list[WorkflowNode],
        context: WorkflowContext | None = None,
    ) -> "SequentialRunner":
        """
        Reconstruct a runner from a suspended workflow + a DEFINER decision.

        This is a minimal foundation version. It restores variables and
        continues execution from after the suspended dialog node.
        """
        ctx = context or WorkflowContext(variables=suspended.variables.copy())

        # Inject the decision into the context so the next dialog node (if any)
        # or downstream logic can see it.
        ctx.set("last_definer_decision", decision)

        # Find where we were suspended
        suspended_node_id = suspended.current_node_id

        start_index = 0
        for idx, node in enumerate(nodes):
            if node.node_id == suspended_node_id:
                start_index = idx + 1
                break

        remaining_nodes = nodes[start_index:]
        return cls(remaining_nodes, ctx)
