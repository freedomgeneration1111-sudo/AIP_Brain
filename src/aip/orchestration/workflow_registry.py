"""WorkflowRegistry (CHUNK-9.3).

Discovers YAML workflow templates (frontmatter + body) beyond the Phase 2 0.1 template.
Used by admin console and CLI.
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
                        if "template_id" in meta:
                            data = meta

                    if data and "template_id" in data:
                        self._templates[data["template_id"]] = WorkflowTemplate(
                            name=data.get("name", yaml_file.stem),
                            version=data.get("version", "1.0"),
                            description=data.get("description", ""),
                            path=str(yaml_file),
                        )
            except Exception:
                continue

        # Always include the original Phase 2 template
        if "synthesis_session_v1" not in self._templates:
            self._templates["synthesis_session_v1"] = WorkflowTemplate(
                name="Synthesis Session v1",
                version="1.0",
                description="Original synthesis workflow from Phase 2",
                path=str(self.workflows_dir / "synthesis_session_v1.yaml"),
            )

    def list_templates(self) -> list[WorkflowTemplate]:
        return list(self._templates.values())

    def get_template(self, template_id: str) -> WorkflowTemplate | None:
        return self._templates.get(template_id)

    def load_workflow(self, template_id: str) -> dict:
        tmpl = self.get_template(template_id)
        if not tmpl:
            raise ValueError(f"Unknown template: {template_id}")
        with open(tmpl.path, "r") as f:
            return yaml.safe_load(f) or {}
