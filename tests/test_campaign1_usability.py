"""Tests for Campaign 1: Make It Usable changes.

- WorkflowContext budget defaults to DEFAULT_WORKFLOW_BUDGET (500k), not None
- consume_budget() logs warnings and enforces limits
- ReviewNode + eval_fn: eval_error handling, eval_fn exception handling,
  safe PENDING verdict on review_artifact exceptions
"""

import pytest

from aip.foundation.schemas import ReviewVerdict
from aip.orchestration.workflow.context import DEFAULT_WORKFLOW_BUDGET, WorkflowContext

# --- Task 1: WorkflowContext budget defaults ---


class TestWorkflowContextBudgetDefault:
    """Verify that WorkflowContext now has a finite default budget."""

    def test_default_budget_is_not_none(self):
        """WorkflowContext() should have a finite default budget, not None."""
        ctx = WorkflowContext()
        assert ctx.budget_remaining is not None
        assert ctx.budget_remaining == DEFAULT_WORKFLOW_BUDGET

    def test_default_budget_value_matches_config(self):
        """DEFAULT_WORKFLOW_BUDGET should match BudgetConfig.session_token_limit."""
        assert DEFAULT_WORKFLOW_BUDGET == 500_000

    def test_consume_budget_within_default_succeeds(self):
        """Consuming a small amount from the default budget should succeed."""
        ctx = WorkflowContext()
        assert ctx.consume_budget(100) is True
        assert ctx.budget_remaining == DEFAULT_WORKFLOW_BUDGET - 100

    def test_consume_budget_exceeds_default_fails(self):
        """Consuming more than the default budget should fail."""
        ctx = WorkflowContext()
        assert ctx.consume_budget(DEFAULT_WORKFLOW_BUDGET + 1) is False
        assert ctx.budget_remaining == DEFAULT_WORKFLOW_BUDGET  # unchanged

    def test_explicit_none_budget_still_works(self):
        """Explicitly setting budget_remaining=None still works (for test fixtures)."""
        ctx = WorkflowContext(budget_remaining=None)
        assert ctx.consume_budget(100) is True  # infinite budget

    def test_explicit_budget_override(self):
        """Explicitly setting a budget overrides the default."""
        ctx = WorkflowContext(budget_remaining=1000)
        assert ctx.budget_remaining == 1000
        assert ctx.consume_budget(500) is True
        assert ctx.consume_budget(600) is False

    def test_fork_preserves_default_budget(self):
        """fork_for_parallel() copies the default budget."""
        ctx = WorkflowContext()
        child = ctx.fork_for_parallel()
        assert child.budget_remaining == DEFAULT_WORKFLOW_BUDGET

    def test_budget_exhaustion_is_logged(self, caplog):
        """Budget exhaustion should log a warning."""
        import logging

        ctx = WorkflowContext(budget_remaining=50)
        with caplog.at_level(logging.WARNING, logger="aip.orchestration.workflow.context"):
            result = ctx.consume_budget(100)
        assert result is False
        assert "Budget exhausted" in caplog.text


# --- Task 2: ReviewNode + eval_fn robustness ---


class TestEvalErrorHandling:
    """Verify that eval_error and eval_fn exceptions are handled correctly."""

    @pytest.mark.asyncio
    async def test_automated_review_eval_error_returns_needs_revision(self, monkeypatch):
        """When eval_fn returns eval_error=True, review returns NEEDS_REVISION."""
        monkeypatch.delenv("CI", raising=False)

        from aip.orchestration.review import review_artifact

        class FakeArtifactStore:
            async def write(self, id, content, metadata):
                pass

            async def read(self, id, version=None):
                return "content"

            async def list_versions(self, id):
                return [1]

        class FakeEcsStore:
            async def transition(self, **kw):
                pass

            async def current_state(self, id):
                return "GENERATED"

        class FakeEventStore:
            async def write_event(self, **kw):
                pass

            async def query(self, **kw):
                return []

        class FakeTraceStore:
            async def write_event(self, **kw):
                pass

        async def failing_eval(content, artifact_id):
            return {
                "confidence": 0.0,
                "failure_types": ["eval_error"],
                "detail": "Model call failed",
                "ci_fixture": False,
                "eval_error": True,
            }

        verdict = await review_artifact(
            "test_art",
            FakeArtifactStore(),
            FakeEcsStore(),
            FakeEventStore(),
            FakeTraceStore(),
            eval_fn=failing_eval,
        )
        assert verdict.verdict == "NEEDS_REVISION"
        assert "eval_error" in verdict.failure_types

    @pytest.mark.asyncio
    async def test_automated_review_eval_fn_exception_returns_needs_revision(self, monkeypatch):
        """When eval_fn raises an exception, review returns NEEDS_REVISION."""
        monkeypatch.delenv("CI", raising=False)

        from aip.orchestration.review import review_artifact

        class FakeArtifactStore:
            async def write(self, id, content, metadata):
                pass

            async def read(self, id, version=None):
                return "content"

            async def list_versions(self, id):
                return [1]

        class FakeEcsStore:
            async def transition(self, **kw):
                pass

            async def current_state(self, id):
                return "GENERATED"

        class FakeEventStore:
            async def write_event(self, **kw):
                pass

            async def query(self, **kw):
                return []

        class FakeTraceStore:
            async def write_event(self, **kw):
                pass

        async def crashing_eval(content, artifact_id):
            raise RuntimeError("Model endpoint unavailable")

        verdict = await review_artifact(
            "test_art",
            FakeArtifactStore(),
            FakeEcsStore(),
            FakeEventStore(),
            FakeTraceStore(),
            eval_fn=crashing_eval,
        )
        assert verdict.verdict == "NEEDS_REVISION"
        assert "eval_error" in verdict.failure_types
        assert "unavailable" in verdict.detail or "RuntimeError" in verdict.detail

    @pytest.mark.asyncio
    async def test_definer_review_eval_fn_exception_returns_needs_revision(self, monkeypatch):
        """When eval_fn raises during definer review, returns NEEDS_REVISION."""
        monkeypatch.delenv("CI", raising=False)

        from aip.orchestration.review import review_artifact

        class FakeArtifactStore:
            async def write(self, id, content, metadata):
                pass

            async def read(self, id, version=None):
                return "content"

            async def list_versions(self, id):
                return [1]

        class FakeEcsStore:
            async def transition(self, **kw):
                pass

            async def current_state(self, id):
                return "GENERATED"

        class FakeEventStore:
            async def write_event(self, **kw):
                pass

            async def query(self, **kw):
                return []

        class FakeTraceStore:
            async def write_event(self, **kw):
                pass

        async def crashing_eval(content, artifact_id):
            raise ConnectionError("Ollama not running")

        config = {"review": {"mode": "definer"}}
        verdict = await review_artifact(
            "test_art",
            FakeArtifactStore(),
            FakeEcsStore(),
            FakeEventStore(),
            FakeTraceStore(),
            eval_fn=crashing_eval,
            config=config,
        )
        assert verdict.verdict == "NEEDS_REVISION"
        assert "eval_error" in verdict.failure_types

    @pytest.mark.asyncio
    async def test_definer_review_eval_error_flag_returns_needs_revision(self, monkeypatch):
        """When eval_fn returns eval_error=True during definer review, returns NEEDS_REVISION."""
        monkeypatch.delenv("CI", raising=False)

        from aip.orchestration.review import review_artifact

        class FakeArtifactStore:
            async def write(self, id, content, metadata):
                pass

            async def read(self, id, version=None):
                return "content"

            async def list_versions(self, id):
                return [1]

        class FakeEcsStore:
            async def transition(self, **kw):
                pass

            async def current_state(self, id):
                return "GENERATED"

        class FakeEventStore:
            async def write_event(self, **kw):
                pass

            async def query(self, **kw):
                return []

        class FakeTraceStore:
            async def write_event(self, **kw):
                pass

        async def error_eval(content, artifact_id):
            return {
                "confidence": 0.0,
                "failure_types": ["eval_error"],
                "detail": "Model returned unparseable JSON",
                "ci_fixture": False,
                "eval_error": True,
            }

        config = {"review": {"mode": "definer"}}
        verdict = await review_artifact(
            "test_art",
            FakeArtifactStore(),
            FakeEcsStore(),
            FakeEventStore(),
            FakeTraceStore(),
            eval_fn=error_eval,
            config=config,
        )
        assert verdict.verdict == "NEEDS_REVISION"
        assert "eval_error" in verdict.failure_types


class TestReviewNodeErrorHandling:
    """Verify that ReviewNode returns PENDING-safe verdict on exceptions."""

    @pytest.mark.asyncio
    async def test_review_node_returns_pending_on_exception(self):
        """When review_artifact raises, ReviewNode returns a PENDING-safe verdict."""
        from aip.orchestration.workflow.node import ReviewNode

        # Create a context with no stores — this will cause review_artifact to
        # fail when it tries to read the artifact
        ctx = WorkflowContext(
            variables={"artifact_id": "test_art"},
            protocols={
                "artifact_store": None,  # None will cause AttributeError
                "ecs_store": None,
                "event_store": None,
                "trace_store": None,
            },
        )
        node = ReviewNode("review_step", config={"artifact_id": "test_art"})
        result = await node.run(ctx)

        # The node should succeed but return PENDING (safe default)
        assert result.success is True
        assert result.output["paused"] is True
        verdict = result.output["verdict"]
        assert verdict.verdict == "PENDING"
        assert "review_error" in verdict.failure_types

    @pytest.mark.asyncio
    async def test_build_default_eval_fn_returns_eval_error_on_failure(self):
        """_build_default_eval_fn should return eval_error=True when model fails."""
        from aip.orchestration.workflow.node import _build_default_eval_fn

        class FailingModelProvider:
            async def call(self, slot, messages):
                raise RuntimeError("Model endpoint down")

        eval_fn = _build_default_eval_fn(FailingModelProvider(), None)
        result = await eval_fn("test content", "test_id")

        assert result["ci_fixture"] is False  # NOT a CI fixture — it's a real failure
        assert result["eval_error"] is True
        assert result["confidence"] == 0.0
        assert "eval_error" in result["failure_types"]
