"""aip config command  — writes go through AutonomyGate."""

from __future__ import annotations

import click


@click.command("config")
@click.argument("key", required=False)
@click.argument("value", required=False)
def config(key: str | None, value: str | None) -> None:
    """Read/write config. Writes require AutonomyGate (admin level)."""
    if not key:
        click.echo("[api], [cli], [mcp], [chat], [autonomy], [lexical], [vector_backend], ... (see config/aip.config.toml)")
        return

    if value:
        # In real impl: call AutonomyGate with action_type="modify_config", level="admin"
        # If blocked, prompt "This operation requires DEFINER approval. Approve? [y/N]"
        click.echo(f"(scaffold) Would write {key}={value} after AutonomyGate check + possible DEFINER prompt")
    else:
        click.echo(f"(scaffold) Would read {key} from config/aip.config.toml")
