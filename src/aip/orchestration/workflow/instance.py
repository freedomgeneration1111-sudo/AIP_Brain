"""
Workflow instance / suspension model.

Provides a simple, serializable representation of a running or suspended workflow.
This is the persistence layer to support dialog pause/resume.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SuspendedWorkflow:
    """
    Represents a workflow that has been paused (typically at a dialog node).

    This object is designed to be easily serialized (JSON) and stored,
    then later resumed when a DEFINER decision arrives.
    """

    workflow_id: str
    run_id: str
    status: str = "suspended"  # running | suspended | completed | failed
    current_node_id: str | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    completed_nodes: list[dict[str, Any]] = field(default_factory=list)  # node_id + result summary
    suspended_nodes: list[dict[str, Any]] = field(default_factory=list)  # details of paused dialog nodes
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, indent=2)

    @classmethod
    def from_json(cls, data: str) -> "SuspendedWorkflow":
        obj = json.loads(data)
        return cls(**obj)


@dataclass
class WorkflowResumeRequest:
    """
    Data needed to resume a suspended workflow.
    Typically contains the DEFINER decision for a dialog node.
    """

    run_id: str
    decision: dict[str, Any]  # serialized DefinerDecision
    metadata: dict[str, Any] = field(default_factory=dict)
