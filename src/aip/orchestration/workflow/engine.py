"""
High-level public API for the AIP L5 Workflow Engine (CHUNK-2.12).

This module provides a clean, opinionated facade over the low-level
workflow engine built in 2.1–2.11.

It is intended to make the common case (running Workflow 0.1 style
synthesis sessions, or custom YAML-defined workflows) simple, while
still allowing advanced users full access to the underlying primitives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from aip.orchestration.retrieval import fake_embed
from aip.orchestration.workflow.context import WorkflowContext
from aip.orchestration.workflow.definition import WorkflowDefinition
from aip.orchestration.workflow.loader import load_workflow_from_yaml
from aip.orchestration.workflow.runner import SequentialRunner


class WorkflowEngine:
    """
    High-level entry point for running AIP workflows.

    Example (minimal / testing):

        engine = WorkflowEngine()
        result = await engine.run_workflow("my_workflow.yaml")

    Example (with real stores and embedding):

        engine = WorkflowEngine(
            vector_store=real_vs,
            embed_fn=real_embed,
            ...
        )
        result = await engine.run_workflow_01(query="...", domain="...")

    Advanced users can still drop down to the low-level APIs
    (WorkflowDefinition, SequentialRunner, WorkflowContext, etc.).
    """

    def __init__(
        self,
        vector_store: Any | None = None,
        embed_fn: Callable[[str], list[float]] | None = None,
        trace_store: Any | None = None,
        artifact_store: Any | None = None,
        ecs_store: Any | None = None,
        event_store: Any | None = None,
        config: dict[str, Any] | None = None,
        # Future: instance_store, etc.
    ):
        self.vector_store = vector_store
        self.embed_fn = embed_fn or fake_embed
        self.trace_store = trace_store
        self.artifact_store = artifact_store
        self.ecs_store = ecs_store
        self.event_store = event_store
        self.config = config or {}

    async def run_workflow(
        self,
        yaml_path: str | Path,
        variables: dict[str, Any] | None = None,
    ) -> Any:
        """
        Load and execute a general YAML-defined workflow.

        Returns the final result(s) from the runner.
        """
        definition: WorkflowDefinition = load_workflow_from_yaml(yaml_path)

        protocols = {
            "vector_store": self.vector_store,
            "embed_fn": self.embed_fn,
            "trace_store": self.trace_store,
            "artifact_store": self.artifact_store,
            "ecs_store": self.ecs_store,
            "event_store": self.event_store,
            "config": self.config,
        }

        # Provide safe no-op stores for the general workflow path
        class _NoopTraceStore:
            async def write_event(self, *a, **k): pass
            async def get_recent_events(self, session_id: str, limit: int = 100) -> list[dict]:
                return []  # L4/CHUNK-3.1 additive compat for no-op path

        class _NoopStore:
            async def write(self, *a, **k): pass
            async def read(self, *a, **k): return ""

        safe_protocols = {
            "vector_store": self.vector_store,
            "embed_fn": self.embed_fn,
            "trace_store": self.trace_store or _NoopTraceStore(),
            "artifact_store": self.artifact_store or _NoopStore(),
            "ecs_store": self.ecs_store or _NoopStore(),
            "event_store": self.event_store or _NoopStore(),
            "config": self.config,
        }

        ctx = WorkflowContext(
            variables=variables or {},
            protocols=safe_protocols,
            metadata={"config": self.config},
        )

        runner = SequentialRunner(definition.nodes, ctx)
        return await runner.run_workflow(definition)

    async def run_workflow_01(self, query: str, domain: str) -> Any:
        """
        Convenience method to run a standard Workflow 0.1 (synthesis session)
        using the high-level Workflow01Runner wiring.

        This is the easiest way to execute a full synthesis session with
        the current engine.
        """
        from aip.orchestration.workflow.workflow_01 import Workflow01Runner

        runner = Workflow01Runner(
            vector_store=self.vector_store,
            embed_fn=self.embed_fn,
            trace_store=self.trace_store,
            artifact_store=self.artifact_store,
            ecs_store=self.ecs_store,
            event_store=self.event_store,
            config=self.config,
        )

        return await runner.run(query=query, domain=domain)
