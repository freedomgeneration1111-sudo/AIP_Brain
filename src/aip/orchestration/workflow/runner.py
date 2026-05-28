"""
Sequential + feature-rich workflow runner for the AIP L5 engine (up to CHUNK-2.11).

This file contains a consolidated, working implementation of the runner
with support for:
- Sequential execution
- Condition / branching (2.2)
- Dialog pause + event emission (2.3)
- Basic and advanced Parallel execution (2.4 + 2.10)
- Persistence / suspend-resume (2.6 + 2.9)
- Finally / on_error handlers at the workflow level (2.11)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from aip.orchestration.workflow.context import WorkflowContext
from aip.orchestration.workflow.definition import WorkflowDefinition
from aip.orchestration.workflow.instance import SuspendedWorkflow
from aip.orchestration.workflow.instance_store import WorkflowInstanceStore
from aip.orchestration.workflow.node import NodeResult, NodeType, ReviewNode, WorkflowNode


class SequentialRunner:
    """
    Feature-rich sequential (and limited concurrent) runner for AIP workflows.
    """

    def __init__(self, nodes: list[WorkflowNode], context: WorkflowContext | None = None):
        self.nodes = nodes
        self.context = context or WorkflowContext()

    async def run(self) -> list[NodeResult]:
        """
        Basic sequential execution with support for conditions, dialog pause,
        and the advanced parallel block (delegated to _run_parallel).
        """
        results: list[NodeResult] = []
        i = 0
        n = len(self.nodes)

        while i < n:
            node = self.nodes[i]

            if node.requires_model() and not self.context.consume_budget(100):
                results.append(NodeResult(success=False, error="Budget exhausted"))
                break

            # CHUNK-3.12: minimal autonomy request for observability (foundation wiring only;
            # does not gate execution or introduce policy yet — direct completion of 3.11 stub)
            try:
                self.context.request_autonomy(0, {"node_id": getattr(node, "node_id", None), "phase": "pre-agent"})
            except Exception:
                pass

            result = await node.run(self.context)
            results.append(result)
            self.context.set(node.node_id, {"output": result.output})
            self.context.set("previous", {"output": result.output})
            self.context.set("last_result", result.output)

            if not result.success:
                break

            # Condition branching (2.2)
            if node.node_type == NodeType.CONDITION:
                cond_result = result.output.get("result", False) if isinstance(result.output, dict) else False
                next_key = "next_on_true" if cond_result else "next_on_false"
                target_id = node.config.get(next_key)
                if target_id:
                    try:
                        target_idx = next(idx for idx, nd in enumerate(self.nodes) if nd.node_id == target_id)
                        i = target_idx
                        continue
                    except StopIteration:
                        pass

            # Dialog pause (2.3)
            if node.node_type == NodeType.DIALOG:
                if isinstance(result.output, dict) and result.output.get("paused"):
                    break

            # Review pause (CHUNK-4.5 integration of 4.1 ReviewNode)
            # Treat review nodes that return a non-final verdict as pause points
            if isinstance(node, ReviewNode) or (isinstance(result.output, dict) and result.output.get("paused")):
                if isinstance(result.output, dict) and result.output.get("paused"):
                    break

            # Parallel block (2.4 + 2.10 advanced logic)
            if node.node_type == NodeType.PARALLEL:
                await self._run_parallel(node)
                i += 1
                continue

            i += 1

        return results

    async def _run_parallel(self, pnode: WorkflowNode) -> None:
        """Advanced parallel execution (extracted for clarity)."""
        child_ids = getattr(pnode, "children", [])
        child_nodes = [n for n in self.nodes if n.node_id in child_ids]
        if not child_nodes:
            return

        deps = getattr(pnode, "dependencies", {}) or {}
        prereqs = {cid: set(deps.get(cid, [])) for cid in child_ids}

        ready = {cid for cid in child_ids if not prereqs[cid]}
        running = {}
        branch_results = {}

        for cid in ready:
            cnode = next(n for n in child_nodes if n.node_id == cid)
            child_ctx = self.context.fork_for_parallel()
            mini = SequentialRunner([cnode], child_ctx)
            running[cid] = asyncio.create_task(mini.run())

        while running:
            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                for cid, t in list(running.items()):
                    if t is task:
                        try:
                            res = task.result()
                            branch_results[cid] = res
                        except Exception as e:
                            branch_results[cid] = e
                        del running[cid]
                        break

                for cid in list(prereqs.keys()):
                    if cid in ready or cid in branch_results:
                        continue
                    if not (prereqs[cid] - set(branch_results.keys())):
                        ready.add(cid)
                        cnode = next(n for n in child_nodes if n.node_id == cid)
                        child_ctx = self.context.fork_for_parallel()
                        mini = SequentialRunner([cnode], child_ctx)
                        running[cid] = asyncio.create_task(mini.run())

        errors = {cid: r for cid, r in branch_results.items() if isinstance(r, Exception)}
        continue_on_error = getattr(pnode, "continue_on_error", False)

        if errors and not continue_on_error:
            raise next(iter(errors.values()))

        merge = getattr(pnode, "merge_strategy", "collect_all")
        if merge == "collect_all":
            for res in branch_results.values():
                if isinstance(res, list):
                    # results are collected by caller if needed
                    pass

    # --- Persistence helpers (2.6 / 2.9) ---
    async def run_until_pause(
        self,
        workflow_id: str | None = None,
        instance_store: WorkflowInstanceStore | None = None,
    ) -> tuple[list[NodeResult], SuspendedWorkflow | None]:
        results = await self.run()
        last = results[-1] if results else None
        is_paused = last and isinstance(last.output, dict) and last.output.get("paused")

        if not is_paused:
            return results, None

        paused_info = last.output if isinstance(last.output, dict) else {}
        current = paused_info.get("executed")

        suspended = SuspendedWorkflow(
            workflow_id=workflow_id or str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            status="suspended",
            current_node_id=current,
            variables=self.context.variables.copy(),
            suspended_nodes=[paused_info],
        )

        if instance_store is not None:
            await instance_store.save(suspended)

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
        suspended = await instance_store.load(run_id)
        if suspended is None:
            raise ValueError(f"No suspended workflow for run_id={run_id}")

        ctx = context or WorkflowContext(variables=suspended.variables.copy())
        ctx.set("last_definer_decision", decision)

        start = 0
        for idx, n in enumerate(nodes):
            if n.node_id == suspended.current_node_id:
                start = idx + 1
                break
        return cls(nodes[start:], ctx)

    @classmethod
    def from_suspended(
        cls,
        suspended: "SuspendedWorkflow",
        decision: Any,
        nodes: list[WorkflowNode],
        context: WorkflowContext | None = None,
    ) -> "SequentialRunner":
        """Synchronous classmethod per CHUNK-2.9: create a runner from a suspended workflow.

        Sets the DEFINER decision in context and resumes from the node after
        the suspended position.
        """
        ctx = context or WorkflowContext(variables=suspended.variables.copy())
        ctx.set("last_definer_decision", decision)

        start = 0
        for idx, n in enumerate(nodes):
            if n.node_id == suspended.current_node_id:
                start = idx + 1
                break
        return cls(nodes[start:], ctx)

    # --- Finally / on_error support (2.11) ---
    async def run_workflow(self, definition: WorkflowDefinition) -> list[NodeResult]:
        results: list[NodeResult] = []
        error_occurred = False

        try:
            for node in definition.nodes:
                if node.requires_model() and not self.context.consume_budget(100):
                    results.append(NodeResult(success=False, error="Budget exhausted"))
                    error_occurred = True
                    break

                # CHUNK-3.12: minimal autonomy request for observability (foundation wiring only;
                # does not gate execution or introduce policy yet — direct completion of 3.11 stub)
                try:
                    self.context.request_autonomy(0, {"node_id": getattr(node, "node_id", None), "phase": "pre-agent"})
                except Exception:
                    pass

                result = await node.run(self.context)
                results.append(result)
                self.context.set(node.node_id, {"output": result.output})
                self.context.set("previous", {"output": result.output})
                self.context.set("last_result", result.output)

                if not result.success:
                    error_occurred = True
                    break

            if any(r.output.get("paused") for r in results if isinstance(r.output, dict)):
                error_occurred = True

        except Exception as e:
            error_occurred = True
            results.append(NodeResult(success=False, error=str(e)))

        finally_nodes = list(reversed(definition.finally_nodes))

        if error_occurred:
            for node in definition.on_error_nodes:
                try:
                    r = await node.run(self.context)
                    results.append(r)
                except Exception as ce:
                    results.append(NodeResult(success=False, error=f"Compensation error: {ce}"))

        for node in finally_nodes:
            try:
                r = await node.run(self.context)
                results.append(r)
            except Exception as fe:
                results.append(NodeResult(success=False, error=f"Finally error: {fe}"))

        if error_occurred:
            for r in results:
                if not r.success and r.error:
                    raise RuntimeError(r.error)

        return results
