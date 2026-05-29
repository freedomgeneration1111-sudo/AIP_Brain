"""CLI collaborator management commands.

Adapter-layer. Uses container's CollaboratorManager.
DEFINER context assumed for privileged ops in 0.1 CLI.
"""

from __future__ import annotations

import click

from aip.adapter.api.dependencies import get_container


@click.group("collaborator")
def collaborator_group():
    """Collaborator management (user accounts with limited roles)."""
    pass


@collaborator_group.command("list")
@click.pass_context
def collaborator_list(ctx):
    """List collaborators (non-DEFINER users)."""
    container = get_container(ctx.obj.get("request")) if "request" in ctx.obj else None
    cm = getattr(container, "collaborator_manager", None) if container else None
    if cm is None:
        click.echo("CollaboratorManager not available.")
        return
    import asyncio

    users = asyncio.get_event_loop().run_until_complete(cm.list_collaborators())
    for u in users:
        click.echo(f"{u.get('identity')} ({u.get('role')})")


@collaborator_group.command("add")
@click.argument("identity")
@click.option("--role", default="collaborator", type=click.Choice(["collaborator", "readonly"]))
@click.option("--password", prompt=True, hide_input=True)
@click.pass_context
def collaborator_add(ctx, identity, role, password):
    """Add collaborator/readonly (DEFINER only)."""
    container = get_container(ctx.obj.get("request")) if "request" in ctx.obj else None
    cm = getattr(container, "collaborator_manager", None) if container else None
    if cm is None:
        click.echo("CollaboratorManager not available.")
        return
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(cm.create_collaborator(identity, role, password))
    click.echo(result)


@collaborator_group.command("update")
@click.argument("identity")
@click.option("--role", required=True, type=click.Choice(["collaborator", "readonly"]))
@click.pass_context
def collaborator_update(ctx, identity, role):
    """Update collaborator role (DEFINER only; cannot change DEFINER)."""
    container = get_container(ctx.obj.get("request")) if "request" in ctx.obj else None
    cm = getattr(container, "collaborator_manager", None) if container else None
    if cm is None:
        click.echo("CollaboratorManager not available.")
        return
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(cm.update_role(identity, role, "definer"))
    click.echo(result)


@collaborator_group.command("remove")
@click.argument("identity")
@click.pass_context
def collaborator_remove(ctx, identity):
    """Remove collaborator (DEFINER only; cannot remove DEFINER)."""
    container = get_container(ctx.obj.get("request")) if "request" in ctx.obj else None
    cm = getattr(container, "collaborator_manager", None) if container else None
    if cm is None:
        click.echo("CollaboratorManager not available.")
        return
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(cm.revoke_collaborator(identity, "definer"))
    click.echo(result)
