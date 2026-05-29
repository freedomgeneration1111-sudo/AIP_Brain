"""Basic validation tests for the Workflow 0.1 YAML definition (CHUNK-4.6)."""

import pytest

from aip.orchestration.workflow.loader import load_workflow_from_yaml


def test_synthesis_session_v1_loads():
    """CHUNK-4.6: The reference Workflow 0.1 YAML must load without error."""
    definition = load_workflow_from_yaml("workflows/synthesis_session_v1.yaml")
    assert definition is not None
    node_ids = [n.node_id for n in definition.nodes]
    assert "retrieve" in node_ids
    assert "synthesize" in node_ids
    assert "review" in node_ids
    assert "re_synthesize" in node_ids
    assert "commit" in node_ids


def test_synthesis_session_v1_has_review_and_re_synth():
    """CHUNK-4.6: The YAML must include the Phase 2 review and re-synthesis steps."""
    definition = load_workflow_from_yaml("workflows/synthesis_session_v1.yaml")
    node_ids = [n.node_id for n in definition.nodes]
    assert "review" in node_ids
    assert "re_synthesize" in node_ids
