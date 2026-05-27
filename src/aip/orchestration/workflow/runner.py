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
from aip.orchestration.workflow.instance_store import WorkflowInstanceStore
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

            # Richer data flow (CHUNK-2.7)
            # Promote useful data from this node into the workflow context
            node_data = {
                "output": result.output,
                "metadata": result.metadata,
                "success": result.success,
            }
            if result.exports:
                node_data.update(result.exports)

            # Make data available as <node_id> and as "previous" for the next node
            self.context.set(node.node_id, node_data)
            self.context.set("previous", node_data)
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

            # Advanced Parallel execution support (CHUNK-2.10)
            if node.node_type == NodeType.PARALLEL:
                pnode = node  # the ParallelNode
                child_ids = pnode.children
                child_nodes = [n for n in self.nodes if n.node_id in child_ids]

                if child_nodes:
                    # Build simple dependency map: child_id -> set of prereqs
                    deps = getattr(pnode, "dependencies", {}) or {}
                    prereqs = {cid: set(deps.get(cid, [])) for cid in child_ids}

                    # Ready set: children with no unmet prereqs
                    ready = {cid for cid in child_ids if not prereqs[cid]}

                    # Track running tasks
                    running = {}  # child_id -> task
                    branch_results = {}  # child_id -> list[NodeResult] or exception
                    branch_contexts = {}

                    # Fork initial ready branches
                    for cid in ready:
                        cnode = next(n for n in child_nodes if n.node_id == cid)
                        child_ctx = self.context.fork_for_parallel()
                        branch_contexts[cid] = child_ctx
                        mini_runner = SequentialRunner([cnode], child_ctx)
                        running[cid] = asyncio.create_task(mini_runner.run())

                    # Main scheduling loop
                    while running:
                        done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)

                        for task in done:
                            # Find which child finished
                            for cid, t in list(running.items()):
                                if t is task:
                                    try:
                                        res = task.result()
                                        branch_results[cid] = res
                                    except Exception as e:
                                        branch_results[cid] = e

                                    del running[cid]
                                    break

                            # Check what new children become ready
                            for cid in list(prereqs.keys()):
                                if cid in ready or cid in branch_results:
                                    continue
                                unmet = prereqs[cid] - set(branch_results.keys())
                                if not unmet:
                                    ready.add(cid)
                                    cnode = next(n for n in child_nodes if n.node_id == cid)
                                    child_ctx = self.context.fork_for_parallel()
                                    branch_contexts[cid] = child_ctx
                                    mini_runner = SequentialRunner([cnode], child_ctx)
                                    running[cid] = asyncio.create_task(mini_runner.run())

                    # Error aggregation / continue behavior
                    errors = {cid: r for cid, r in branch_results.items() if isinstance(r, Exception)}
                    continue_on_error = getattr(pnode, "continue_on_error", False)

                    if errors and not continue_on_error:
                        # Propagate first error (or could aggregate)
                        first_err = next(iter(errors.values()))
                        raise first_err

                    # Result merging
                    merge_strategy = getattr(pnode, "merge_strategy", "collect_all")

                    if merge_strategy == "collect_all":
                        for cid, res in branch_results.items():
                            if isinstance(res, list):
                                results.extend(res)
                    elif merge_strategy == "first_success":
                        for cid in child_ids:
                            res = branch_results.get(cid)
                            if isinstance(res, list) and res and getattr(res[-1], "success", True):
                                results.extend(res)
                                break
                    else:
                        # Fallback to collect_all
                        for cid, res in branch_results.items():
                            if isinstance(res, list):
                                results.extend(res)

                i += 1
                continue

            i += 1

        return results

    async def run_until_pause(
        self,
        workflow_id: str | None = None,
        instance_store: WorkflowInstanceStore | None = None,
    ) -> tuple[list[NodeResult], SuspendedWorkflow | None]:
        """
        Run until completion or a dialog pause point.

        If an `instance_store` is provided, the suspended workflow will be
        automatically persisted before returning.
        """
        results, suspended = await self._run_until_pause_internal(workflow_id)

        if suspended is not None and instance_store is not None:
            await instance_store.save(suspended)

        return results, suspended

    async def _run_until_pause_internal(
        self, workflow_id: str | None = None
    ) -> tuple[list[NodeResult], SuspendedWorkflow | None]:
        """Internal version that does not touch the store (for reuse)."""
        results = await self.run()

        last_result = results[-1] if results else None
        is_paused = (
            last_result
            and isinstance(last_result.output, dict)
            and last_result.output.get("paused")
            and last_result.output.get("type") == "dialog"
        )

        if not is_paused:
            return results, None

        paused_info = last_result.output if isinstance(last_result.output, dict) else {}
        current_node_id = paused_info.get("executed") or getattr(last_result, "metadata", {}).get("node_id")

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
    async def resume_from_store(
        cls,
        run_id: str,
        decision: Any,
        nodes: list[WorkflowNode],
        instance_store: WorkflowInstanceStore,
        context: WorkflowContext | None = None,
    ) -> "SequentialRunner":
        """
        Load a suspended workflow from the store and return a runner ready to resume.
        """
        suspended = await instance_store.load(run_id)
        if suspended is None:
            raise ValueError(f"No suspended workflow found for run_id={run_id}")

        # Mark the decision in the context (the dialog node or downstream logic can read it)
        ctx = context or WorkflowContext(variables=suspended.variables.copy())
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
