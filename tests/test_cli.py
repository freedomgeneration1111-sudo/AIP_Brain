"""CHUNK-8.2 gate: CLI (aip init, status, config, project, session) using CliRunner."""

from __future__ import annotations

import pytest

try:
    from click.testing import CliRunner
except ImportError:
    CliRunner = None  # type: ignore

from aip.cli.main import cli


@pytest.mark.skipif(CliRunner is None, reason="click not installed (required for 8.2 CLI surface)")
def test_cli_init_creates_expected_dbs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    assert "Init complete" in result.output
    # The init command touches the expected DB files under db/
    assert (tmp_path / "db" / "state.db").exists()
    assert (tmp_path / "db" / "ace_playbook.db").exists()
    assert (tmp_path / "db" / "lexical.db").exists()


@pytest.mark.skipif(CliRunner is None, reason="click not installed")
def test_cli_status_prints_info():
    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "AIP Status" in result.output
    assert "vector_backend" in result.output


@pytest.mark.skipif(CliRunner is None, reason="click not installed")
def test_cli_config_and_gate_path():
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "api.port", "8001"])
    assert result.exit_code == 0
    assert "AutonomyGate" in result.output or "scaffold" in result.output.lower()


@pytest.mark.skipif(CliRunner is None, reason="click not installed")
def test_cli_project_and_session_subcommands():
    runner = CliRunner()
    assert runner.invoke(cli, ["project", "list"]).exit_code == 0
    assert runner.invoke(cli, ["session", "start", "--project-id", "p1", "--domain", "test"]).exit_code == 0


def test_adapter_layer_does_not_import_orchestration_impls():
    """Layering invariant (enforced in the combined gate with test_layering.py)."""
    # Source-level guard for the new cli package
    from pathlib import Path
    cli_root = Path(__file__).parent.parent / "src/aip/cli"
    for py in cli_root.rglob("*.py"):
        text = py.read_text()
        assert "from aip.adapter.budget_store" not in text
        assert "from aip.adapter.vector" not in text  # direct storage


