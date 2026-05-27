"""aip project subcommand group (CHUNK-8.2)."""

from __future__ import annotations

import click


@click.group("project")
def project() -> None:
    """Project management (uses ProjectStore / EcsStore via injection)."""
    pass


@project.command("list")
def list_projects() -> None:
    click.echo("(scaffold) Would list projects via ProjectStore.list_projects()")


@project.command("create")
@click.option("--name", required=True)
@click.option("--domain", required=True)
def create_project(name: str, domain: str) -> None:
    click.echo(f"(scaffold) Would create project {name} in domain {domain} (AutonomyGate write path)")


@project.command("show")
@click.argument("project_id")
def show_project(project_id: str) -> None:
    click.echo(f"(scaffold) Would show project {project_id} + WorkUnits")
