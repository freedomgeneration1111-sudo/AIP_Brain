"""aip status command (CHUNK-8.2)."""

from __future__ import annotations

import click


@click.command("status")
def status() -> None:
    """Print system status from Protocols (offline-first)."""
    click.echo("=== AIP Status ===")
    click.echo("vector_backend: placeholder (run init or wire 8.0b adapters)")
    click.echo("model_slots: synthesis, evaluation, sexton, embedding (validated at init)")
    click.echo("active_sessions: 0 (SessionManager not wired in this scaffold)")
    click.echo("budget: default scopes available (BudgetManager from Phase 5)")
    click.echo("beast_last_run: never (cadence actor delivered in 7.5)")
    click.echo("sexton_unclassified: 0 (classification actor delivered in 7.1)")
    click.echo("\nTip: full status available once 8.1 container + 8.0b adapters are wired into CLI.")
