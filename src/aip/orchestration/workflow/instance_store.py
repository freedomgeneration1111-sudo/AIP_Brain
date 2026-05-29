"""
Production-grade workflow instance persistence.

Defines the `WorkflowInstanceStore` protocol and a simple reference implementation
that can be swapped for a real database-backed store later.

The goal is durable storage of suspended workflows so they can survive restarts
and be resumed after DEFINER decisions (especially for dialog nodes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from aip.orchestration.workflow.instance import SuspendedWorkflow


class WorkflowInstanceStore(ABC):
    """
    Protocol for storing and retrieving suspended workflow instances.

    Implementations can be in-memory (for tests), file-based, or backed by
    the ArtifactStore / EventStore / a dedicated table.
    """

    @abstractmethod
    async def save(self, instance: SuspendedWorkflow) -> None:
        """Persist a suspended workflow instance."""
        ...

    @abstractmethod
    async def load(self, run_id: str) -> SuspendedWorkflow | None:
        """Load a suspended workflow by its run_id. Returns None if not found."""
        ...

    @abstractmethod
    async def delete(self, run_id: str) -> None:
        """Remove a suspended workflow (usually after successful resumption or cancellation)."""
        ...


class FileWorkflowInstanceStore(WorkflowInstanceStore):
    """
    Simple file-based implementation for development and testing.

    Each suspended workflow is stored as a JSON file named <run_id>.json
    inside the given directory.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.directory / f"{run_id}.json"

    async def save(self, instance: SuspendedWorkflow) -> None:
        path = self._path(instance.run_id)
        path.write_text(instance.to_json(), encoding="utf-8")

    async def load(self, run_id: str) -> SuspendedWorkflow | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        return SuspendedWorkflow.from_json(path.read_text(encoding="utf-8"))

    async def delete(self, run_id: str) -> None:
        path = self._path(run_id)
        if path.exists():
            path.unlink()
