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

from aip.orchestration.workflow.definition import WorkflowDefinition
from aip.orchestration.workflow.node import (
    AgentNode,
    ConditionNode,
    DialogNode,
    NodeType,
    ParallelNode,
    ReSynthesizeNode,
    ReviewNode,
    ScriptNode,
    WorkflowNode,
)


def load_workflow_from_yaml(path: str | Path) -> WorkflowDefinition:
    """
    Load a workflow definition from YAML.

    Supports the original flat "nodes" list plus optional top-level
    "finally" and "on_error" lists (CHUNK-2.11).

    Example:

    nodes:
      - id: start
        type: script
        code: "..."
      - id: synthesize
        type: agent
        model_slot: synthesis
        prompt: "..."

    finally:
      - id: cleanup
        type: script
        code: "..."

    on_error:
      - id: compensate
        type: script
        code: "..."
    """
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    def _build_nodes(defs: list[dict[str, Any]]) -> list[WorkflowNode]:
        nodes: list[WorkflowNode] = []
        for node_def in defs:
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
                cfg = {
                    "next_on_true": node_def.get("next_on_true"),
                    "next_on_false": node_def.get("next_on_false"),
                }
                nodes.append(ConditionNode(node_id, condition=node_def.get("condition", ""), config=cfg))
            elif node_type == NodeType.DIALOG:
                nodes.append(DialogNode(node_id, prompt=node_def.get("prompt", "")))
            elif node_type == NodeType.PARALLEL:
                nodes.append(ParallelNode(node_id, children=node_def.get("children", [])))
            elif node_type == "review":
                nodes.append(ReviewNode(node_id, config=node_def))
            elif node_type == "re_synthesize":
                nodes.append(ReSynthesizeNode(node_id, config=node_def))
            else:
                raise ValueError(f"Unknown node type: {node_type}")
        return nodes

    nodes = _build_nodes(data.get("nodes", []))
    finally_nodes = _build_nodes(data.get("finally", []))
    on_error_nodes = _build_nodes(data.get("on_error", []))

    return WorkflowDefinition(
        nodes=nodes,
        finally_nodes=finally_nodes,
        on_error_nodes=on_error_nodes,
        metadata=data.get("metadata", {}),
    )
