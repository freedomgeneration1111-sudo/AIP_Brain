"""CHUNK-9.3 gate: Extended Workflow Templates (three new YAMLs + Registry discovery + engine compatibility)."""

from __future__ import annotations

from aip.orchestration.workflow_registry import WorkflowRegistry


def test_registry_discovers_all_templates():
    reg = WorkflowRegistry("workflows")
    templates = reg.list_templates()
    ids = [t.name for t in templates]
    assert any("Incremental Update" in n for n in ids)
    assert any("Adversarial Red-Team" in n for n in ids)
    assert any("Corpus Maintenance" in n for n in ids)
    assert any("Synthesis Session" in n for n in ids)  # the original 0.1


def test_registry_loads_yaml():
    reg = WorkflowRegistry("workflows")
    wf = reg.load_workflow("incremental_update_v1")
    assert "nodes" in wf or "template_id" in wf


def test_layering():
    from pathlib import Path

    reg_file = Path(__file__).parent.parent / "src/aip/orchestration/workflow_registry.py"
    if reg_file.exists():
        text = reg_file.read_text()
        assert "from aip.adapter." not in text  # pure config + engine
