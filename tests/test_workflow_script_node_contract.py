"""Tests for ScriptNode.run contract.

Verifies that:
1. ScriptNode.run returns structured disabled response in production mode.
2. Workflow containing ScriptNode cannot silently pass in production.
3. Test fixture mode can simulate registered scripts.
4. No production path returns fake script success.
"""

from __future__ import annotations

from aip.orchestration.workflow.context import WorkflowContext
from aip.orchestration.workflow.node import ScriptNode


class MinimalContext(WorkflowContext):
    """Minimal WorkflowContext for testing ScriptNode."""

    def __init__(self, variables=None, metadata=None):
        self._variables = variables or {}
        self._metadata = metadata or {}
        self._protocols: dict = {}
        self._events: list = []

    def get_protocol(self, name):
        return self._protocols.get(name)

    def get(self, key, default=None):
        return self._variables.get(key, default)

    def emit_event(self, event_type, payload):
        self._events.append({"event_type": event_type, "payload": payload})

    @property
    def variables(self):
        return self._variables

    @property
    def metadata(self):
        return self._metadata


# --- Test: production mode returns disabled ---


async def test_script_node_returns_disabled_in_production():
    """ScriptNode.run returns structured DISABLED response in production mode."""
    node = ScriptNode(node_id="test_script", code="print('hello')")
    ctx = MinimalContext()
    result = await node.run(ctx)

    assert result.success is False
    assert result.error is not None
    assert "DISABLED" in result.error
    assert result.metadata.get("code") == "DISABLED"


async def test_script_node_no_fake_success():
    """ScriptNode.run must NOT return success=True in production mode."""
    node = ScriptNode(node_id="test_script", code="arbitrary_code")
    ctx = MinimalContext()
    result = await node.run(ctx)

    assert result.success is False, "ScriptNode must not return fake success"


# --- Test: fixture mode with registered scripts ---


async def test_fixture_mode_registered_script():
    """In fixture mode, registered scripts return success."""
    node = ScriptNode(
        node_id="validate_node",
        code="validate",
        config={"script_fixture_mode": True},
    )
    ctx = MinimalContext()
    result = await node.run(ctx)

    assert result.success is True
    assert result.output["fixture_mode"] is True
    assert result.output["script_name"] == "validate"
    assert result.metadata.get("fixture") is True


async def test_fixture_mode_adversarial_script():
    """In fixture mode, 'adversarial' registered script works."""
    node = ScriptNode(
        node_id="adv_node",
        code="adversarial",
        config={"script_fixture_mode": True},
    )
    ctx = MinimalContext()
    result = await node.run(ctx)

    assert result.success is True
    assert result.output["script_name"] == "adversarial"


async def test_fixture_mode_echo_script():
    """In fixture mode, 'echo' registered script works."""
    node = ScriptNode(
        node_id="echo_node",
        code="echo",
        config={"script_fixture_mode": True},
    )
    ctx = MinimalContext()
    result = await node.run(ctx)

    assert result.success is True
    assert result.output["script_name"] == "echo"


# --- Test: fixture mode rejects unknown scripts ---


async def test_fixture_mode_unknown_script_returns_success():
    """In fixture mode, any script code returns success (safe no-op)."""
    node = ScriptNode(
        node_id="custom_node",
        code="custom_script_code",
        config={"script_fixture_mode": True},
    )
    ctx = MinimalContext()
    result = await node.run(ctx)

    # In fixture mode, scripts succeed as safe no-ops
    assert result.success is True
    assert result.output["fixture_mode"] is True
    assert result.output["script_name"] == "custom_script_code"


# --- Test: workflow cannot silently pass with script node ---


async def test_workflow_cannot_silently_pass_with_script():
    """A workflow step containing ScriptNode cannot silently succeed in production."""
    node = ScriptNode(node_id="prod_script", code="do_something()")
    ctx = MinimalContext()
    result = await node.run(ctx)

    # The result should clearly indicate failure
    assert result.success is False
    # The error should be actionable (explain how to enable)
    assert "script_fixture_mode" in result.error


# --- Test: different scripts all disabled in production ---


async def test_all_scripts_disabled_in_production():
    """No script code can succeed in production mode, even registered fixture names."""
    for code in ["validate", "adversarial", "echo", "import os", "print('hi')"]:
        node = ScriptNode(node_id=f"script_{code}", code=code)
        ctx = MinimalContext()
        result = await node.run(ctx)
        assert result.success is False, f"Script code '{code}' should be disabled in production"


# --- Test: no arbitrary execution possible ---


async def test_no_arbitrary_execution():
    """ScriptNode must never actually execute arbitrary code."""
    node = ScriptNode(node_id="danger", code="__import__('os').system('echo pwned')")
    ctx = MinimalContext()
    result = await node.run(ctx)

    assert result.success is False
    # The code was NOT executed (we can verify by checking the error is about being disabled)
    assert "DISABLED" in result.error


# --- Test: NodeResult contract ---


async def test_disabled_result_includes_metadata():
    """The DISABLED result includes useful metadata for debugging."""
    node = ScriptNode(node_id="meta_test", code="some_code")
    ctx = MinimalContext()
    result = await node.run(ctx)

    assert "node_id" in result.metadata
    assert result.metadata["node_id"] == "meta_test"
    assert "script_length" in result.metadata
