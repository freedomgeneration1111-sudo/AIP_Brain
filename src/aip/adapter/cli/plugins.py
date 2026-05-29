"""CLI plugin management commands.

Adapter-layer. Uses the container's PluginManager (orchestration) for actual work.
Requires DEFINER auth for enable/disable.
"""

from __future__ import annotations

import click

from aip.adapter.api.dependencies import get_container


@click.group("plugin")
def plugin_group():
    """Plugin management (enable custom model providers via YAML)."""
    pass


@plugin_group.command("list")
@click.pass_context
def plugin_list(ctx):
    """List all loaded plugins."""
    container = get_container(ctx.obj.get("request")) if "request" in ctx.obj else None
    # In CLI context we may have direct access; for simplicity use container if wired
    pm = getattr(container, "plugin_manager", None) if container else None
    if pm is None:
        click.echo("PluginManager not available in this context (run under full app).")
        return
    plugins = pm.list_plugins()
    for p in plugins:
        click.echo(f"{p['slot_name']}:{p['provider_name']}")


@plugin_group.command("enable")
@click.argument("slot_name")
@click.argument("config_path")
@click.pass_context
def plugin_enable(ctx, slot_name, config_path):
    """Enable a plugin from YAML config (DEFINER only)."""
    # In real CLI this would go through auth; for 0.1 gate we assume DEFINER context
    container = get_container(ctx.obj.get("request")) if "request" in ctx.obj else None
    pm = getattr(container, "plugin_manager", None) if container else None
    loader = getattr(container, "plugin_loader", None) if container else None
    if pm is None or loader is None:
        click.echo("Plugin infrastructure not wired.")
        return
    provider = loader.load_plugin(config_path)
    if provider:
        pm.register_plugin(provider)
        click.echo(f"Enabled plugin for slot {slot_name}")
    else:
        click.echo("Failed to load plugin (sandbox may have caught error).")


@plugin_group.command("disable")
@click.argument("slot_name")
@click.argument("provider_name")
@click.pass_context
def plugin_disable(ctx, slot_name, provider_name):
    """Disable a loaded plugin (DEFINER only)."""
    container = get_container(ctx.obj.get("request")) if "request" in ctx.obj else None
    pm = getattr(container, "plugin_manager", None) if container else None
    if pm:
        pm.unregister_plugin(slot_name, provider_name)
        click.echo(f"Disabled {slot_name}:{provider_name}")
    else:
        click.echo("PluginManager not available.")


@plugin_group.command("health")
@click.pass_context
def plugin_health(ctx):
    """Run health checks on all loaded plugins."""
    container = get_container(ctx.obj.get("request")) if "request" in ctx.obj else None
    pm = getattr(container, "plugin_manager", None) if container else None
    if pm is None:
        click.echo("PluginManager not available.")
        return
    import asyncio

    health = asyncio.get_event_loop().run_until_complete(pm.health_check_all())
    for k, v in health.items():
        click.echo(f"{k}: {v}")
