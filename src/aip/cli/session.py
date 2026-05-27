"""aip session subcommand group (CHUNK-8.2)."""

from __future__ import annotations

import click


@click.group("session")
def session() -> None:
    """Session management (loads ACE Playbook on start per §8.1)."""
    pass


@session.command("start")
@click.option("--project-id", required=True)
@click.option("--domain", required=True)
def start_session(project_id: str, domain: str) -> None:
    click.echo(f"(scaffold) Would create session for project {project_id} (domain {domain}) + load ACE Playbook")


@session.command("resume")
@click.argument("session_id")
def resume_session(session_id: str) -> None:
    click.echo(f"(scaffold) Would resume session {session_id}")


@session.command("list")
def list_sessions() -> None:
    click.echo("(scaffold) Would list active sessions via SessionManager")
