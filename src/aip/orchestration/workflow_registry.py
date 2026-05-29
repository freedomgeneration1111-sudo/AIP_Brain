"""WorkflowRegistry.

Discovers YAML workflow templates (frontmatter + body) beyond the Phase 2 0.1 template.
Used by admin console and CLI.

Updated for WorkflowTemplate schema: template_id, name, description, yaml_path,
trigger, domains, model_gen_assumption.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aip.foundation.schemas import WorkflowTemplate


class WorkflowRegistry:
    """Registry for extended workflow templates (Phase 7)."""

    def __init__(self, workflows_dir: str = "workflows") -> None:
        self.workflows_dir = Path(workflows_dir)
        self._templates: dict[str, WorkflowTemplate] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        for yaml_file in self.workflows_dir.glob("*.yaml"):
            try:
                with open(yaml_file, "r") as f:
                    content = f.read()
                    data = yaml.safe_load(content) or {}

                    # Support comment-based frontmatter (the style used in 9.3 templates)
                    if not data or "template_id" not in data:
                        meta = {}
                        for line in content.splitlines():
                            line = line.strip()
                            if line.startswith("# template_id:"):
                                meta["template_id"] = line.split(":", 1)[1].strip()
                            elif line.startswith("# name:"):
                                meta["name"] = line.split(":", 1)[1].strip()
                            elif line.startswith("# description:"):
                                meta["description"] = line.split(":", 1)[1].strip()
                            elif line.startswith("# trigger:"):
                                meta["trigger"] = line.split(":", 1)[1].strip()
                            elif line.startswith("# domains:"):
                                domains_str = line.split(":", 1)[1].strip()
                                meta["domains"] = [d.strip() for d in domains_str.split(",") if d.strip()]
                        if "template_id" in meta:
                            data = meta

                    if data and "template_id" in data:
                        tid = data["template_id"]
                        self._templates[tid] = WorkflowTemplate(
                            template_id=tid,
                            name=data.get("name", yaml_file.stem),
                            description=data.get("description", ""),
                            yaml_path=str(yaml_file.relative_to(self.workflows_dir))
                            if yaml_file.is_relative_to(self.workflows_dir)
                            else str(yaml_file),
                            trigger=data.get("trigger", "manual"),
                            domains=data.get("domains", []),
                        )
            except Exception:
                continue

        # Always include the original Phase 2 template
        if "synthesis_session_v1" not in self._templates:
            self._templates["synthesis_session_v1"] = WorkflowTemplate(
                template_id="synthesis_session_v1",
                name="Synthesis Session v1",
                description="Original synthesis workflow from Phase 2",
                yaml_path="synthesis_session_v1.yaml",
            )

    def list_templates(self) -> list[WorkflowTemplate]:
        return list(self._templates.values())

    def get_template(self, template_id: str) -> WorkflowTemplate | None:
        return self._templates.get(template_id)

    def load_workflow(self, template_id: str) -> dict:
        tmpl = self.get_template(template_id)
        if not tmpl:
            raise ValueError(f"Unknown template: {template_id}")
        # Resolve yaml_path relative to workflows_dir
        yaml_path = self.workflows_dir / tmpl.yaml_path
        with open(yaml_path, "r") as f:
            return yaml.safe_load(f) or {}
