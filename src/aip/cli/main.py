"""AIP CLI entrypoint.

Implements the declared pyproject script `aip = "aip.cli.main:cli"`.
Uses Click. Composes via Protocols / 8.1 AipContainer where possible (offline-first).
"""

from __future__ import annotations

import click

from aip.cli import init as init_cmd
from aip.cli import status as status_cmd
from aip.cli import config as config_cmd
from aip.cli import project as project_cmd
from aip.cli import session as session_cmd


@click.group()
@click.version_option(version="0.1", prog_name="aip")
def cli() -> None:
    """AIP 0.1 — AI Poiesis local-first harness.

    Primary offline DEFINER surface. All privileged operations go through AutonomyGate.
    """
    pass


# Register subcommands
cli.add_command(init_cmd.init)
cli.add_command(status_cmd.status)
cli.add_command(config_cmd.config)
cli.add_command(project_cmd.project)
cli.add_command(session_cmd.session)


if __name__ == "__main__":
    cli()
