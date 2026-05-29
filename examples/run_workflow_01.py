"""
Reference implementation for running Workflow 0.1 (the canonical synthesis
session pipeline) using the high-level public API of the AIP L5 Workflow Engine.

This example demonstrates the "happy path" using the public `WorkflowEngine`
and the Phase 1 node implementations, with safe defaults suitable for
testing and early integration.

In a real deployment you would inject production-grade stores
(ArtifactStore, VectorStore, EventStore, EcsStore, etc.) and a real
embedding function.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aip.orchestration.retrieval import fake_embed
from aip.orchestration.workflow.engine import WorkflowEngine


async def main() -> None:
    # ------------------------------------------------------------------
    # 1. Set up the high-level WorkflowEngine with safe defaults
    # ------------------------------------------------------------------
    # In production you would pass real stores and a real embed_fn.
    # Here we use the safe no-op / fake implementations so the example
    # runs out of the box.
    engine = WorkflowEngine(
        embed_fn=fake_embed,
        # All other stores default to safe no-ops inside the engine
    )

    # ------------------------------------------------------------------
    # 2. Run the canonical Workflow 0.1 pipeline
    # ------------------------------------------------------------------
    _workflow_path = Path(__file__).parent / "workflow_01.yaml"

    print("Starting Workflow 0.1 synthesis session...\n")

    # Using the convenience method that knows about the standard
    # Workflow 0.1 structure and the Phase 1 node implementations.
    result = await engine.run_workflow_01(
        query="What are the key principles of sovereign AI memory systems?",
        domain="ai-architecture",
    )

    print("\n=== Workflow 0.1 completed ===")
    print("Final result type :", type(result).__name__)
    print("Result            :", result)

    # In a real run that reaches the commit step, `result` would be
    # an ArtifactRef (or similar) pointing at the newly created canonical artifact.


if __name__ == "__main__":
    asyncio.run(main())
