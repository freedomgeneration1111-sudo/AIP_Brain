"""
Minimal YAML loader for workflows (CHUNK-2.1 foundation).

Parses a simple YAML structure into a list of node definitions.
This is intentionally minimal — full validation, Jinja templating in conditions,
and rich node configuration will be expanded in later chunks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aip.orchestration.workflow.node import (
    AgentNode,
    ConditionNode,
    DialogNode,
    NodeType,
    ParallelNode,
    ScriptNode,
    WorkflowNode,
)


def load_workflow_from_yaml(path: str | Path) -> list[WorkflowNode]:
    """
    Load a workflow definition from YAML.

    Expected minimal format (for CHUNK-2.1):

    nodes:
      - id: start
        type: script
        code: "print('hello')"
      - id: synthesize
        type: agent
        model_slot: synthesis
        prompt: "Summarize the following: {{ previous }}"
    """
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    nodes: list[WorkflowNode] = []
    for node_def in data.get("nodes", []):
        node_id = node_def["id"]
        node_type = NodeType(node_def["type"])

        if node_type == NodeType.SCRIPT:
            nodes.append(ScriptNode(node_id, code=node_def.get("code", "")))
        elif node_type == NodeType.AGENT:
            nodes.append(AgentNode(
                node_id,
                model_slot=node_def["model_slot"],
                prompt_template=node_def.get("prompt", ""),
            ))
        elif node_type == NodeType.CONDITION:
            nodes.append(ConditionNode(node_id, condition=node_def.get("condition", "")))
        elif node_type == NodeType.DIALOG:
            nodes.append(DialogNode(node_id, prompt=node_def.get("prompt", "")))
        elif node_type == NodeType.PARALLEL:
            nodes.append(ParallelNode(node_id, children=node_def.get("children", [])))
        else:
            raise ValueError(f"Unknown node type: {node_type}")

    return nodes
