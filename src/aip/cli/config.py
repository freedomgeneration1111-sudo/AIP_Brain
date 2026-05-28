"""aip config command — reads/writes config via TOML.

Writes require AutonomyGate (admin level).
Reads are always available.
"""

from __future__ import annotations

from pathlib import Path

import click


def _read_config(path: Path) -> dict[str, dict[str, str]]:
    """Minimal TOML-like config reader (no pyyaml dependency needed).

    Returns {section: {key: value}} dict.
    """
    config: dict[str, dict[str, str]] = {}
    current_section = "default"
    if not path.exists():
        return config
    try:
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped[1:-1].strip()
                if current_section not in config:
                    config[current_section] = {}
            elif "=" in stripped:
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if current_section not in config:
                    config[current_section] = {}
                config[current_section][key] = value
    except Exception:
        pass
    return config


@click.command("config")
@click.argument("key", required=False)
@click.argument("value", required=False)
def config(key: str | None, value: str | None) -> None:
    """Read/write config values from config/aip.config.toml.

    Reads: aip config vector_backend.provider
    Writes: aip config vector_backend.provider pgvector
      (writes require AutonomyGate admin approval in production)
    """
    config_path = Path("config/aip.config.toml")

    if not key:
        # List all sections
        cfg = _read_config(config_path)
        if not cfg:
            click.echo(f"No config found at {config_path} — run `aip init` to create.")
            return
        click.echo(f"Config: {config_path}")
        for section, entries in cfg.items():
            if section == "default":
                continue
            click.echo(f"\n[{section}]")
            for k, v in entries.items():
                click.echo(f"  {k} = {v}")
        return

    # Parse dotted key like "vector_backend.provider"
    parts = key.split(".")
    cfg = _read_config(config_path)

    if value:
        # Write path — would require AutonomyGate in production
        if len(parts) < 2:
            click.echo(f"Error: key must be in section.key format (e.g., vector_backend.provider)")
            return
        section, subkey = parts[0], ".".join(parts[1:])
        # TODO: Wire through AutonomyGate for admin-level write approval
        click.echo(f"[NOT IMPLEMENTED] Would write [{section}] {subkey} = {value}")
        click.echo("  Writes require AutonomyGate admin approval — not yet wired in CLI.")
    else:
        # Read path
        if len(parts) == 2:
            section, subkey = parts[0], parts[1]
            val = cfg.get(section, {}).get(subkey)
        elif len(parts) == 1:
            # Show entire section
            section = parts[0]
            entries = cfg.get(section, {})
            if entries:
                click.echo(f"[{section}]")
                for k, v in entries.items():
                    click.echo(f"  {k} = {v}")
                return
            val = None
        else:
            val = cfg.get(parts[0], {}).get(".".join(parts[1:]))

        if val is not None:
            click.echo(val)
        else:
            click.echo(f"Key '{key}' not found in {config_path}")
