"""CLI commands for browsing conversation history.

Provides ``aip history`` with subcommands:
- list: List recent conversation turns (stored as artifact_type="conversation")
- show: Display full user/assistant content for a specific turn (artifact)

Conversations are already persisted by the ingestion pipeline (for `aip ingest`)
and by chat auto-save (for augmented/normal sessions). This is a read-only
viewer using the VersionedArtifactStore directly (no new storage, no orchestration
imports — follows adapter layer discipline).

All operations are read-only; no DEFINER approval or ECS changes involved.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys

import click


@click.group("history")
def history() -> None:
    """Browse stored conversation turns from the corpus.

    Conversation artifacts (type=conversation) are created by:
      - aip ingest file ...
      - chat sessions with auto-save (both normal and augmented modes)

    Use list to discover, show to inspect full prompt/response.
    """
    pass


@history.command("list")
@click.option("--project", default=None, help="Filter by project name (resolves its domain).")
@click.option("--limit", default=20, type=int, help="Max number of turns to show (default 20).")
def history_list(project: str | None, limit: int) -> None:
    """List recent conversation turns stored in corpus.

    Queries ArtifactStore for artifacts with metadata artifact_type="conversation".
    If --project given, filters to that project's domain.
    Sorted reverse chronological by created_at (latest first).
    Displays: turn_id | timestamp | mode | first 80 chars of first user turn.
    """
    try:
        result = asyncio.run(_history_list_async(project, limit))
        _print_list_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@history.command("show")
@click.argument("turn_id")
def history_show(turn_id: str) -> None:
    """Show full prompt/response for a stored conversation turn.

    Reads the artifact by ID (IDs start with "conv:").
    Parses the JSON content to extract user + assistant messages.
    Shows domain, session_id, model, augmented_mode (if present in metadata).
    """
    try:
        result = asyncio.run(_history_show_async(turn_id))
        _print_show_result(result)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers (direct adapter access — no orchestration imports, per layer rules)
# ---------------------------------------------------------------------------


def _get_db_path() -> str:
    """Resolve default DB (env, config, or db/state.db)."""
    from aip.cli._db_path import ensure_db_dir, get_default_db_path

    db_path = get_default_db_path()
    ensure_db_dir(db_path)
    return db_path


def _resolve_project_domain(project: str, db_path: str) -> str | None:
    """Lookup domain for a project name or id using direct sqlite (like other CLI)."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT domain FROM projects WHERE name = ? OR project_id = ? LIMIT 1",
            (project, project),
        )
        row = cursor.fetchone()
        conn.close()
        if row and row["domain"]:
            return row["domain"]
    except Exception:
        pass
    return None


async def _history_list_async(project: str | None, limit: int) -> dict:
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore

    db_path = _get_db_path()
    store = VersionedArtifactStore(db_path)
    await store.initialize()
    try:
        # Use the metadata query method on the concrete store (latest versions only).
        # Filter by artifact_type using the kv (the method's artifact_type= param is
        # for additional AND filter; using key=artifact_type here gets exactly the convos).
        fetch_limit = limit * 5 if project else limit
        raw = await store.list_artifacts_by_metadata(
            key="artifact_type",
            value="conversation",
            artifact_type=None,
            limit=max(fetch_limit, 100),
        )

        # Optional project/domain filter (client side, since single-kv query)
        if project:
            dom = _resolve_project_domain(project, db_path)
            target = dom or project  # fallback to name-as-domain
            raw = [
                a for a in raw
                if (a.get("metadata") or {}).get("domain") == target
            ]

        # Already ordered DESC by created_at from the store query; apply limit
        raw = raw[:limit]

        items = []
        for art in raw:
            meta = art.get("metadata") or {}
            content = art.get("content") or ""
            created_at = art.get("created_at") or meta.get("created_at", "")

            # Derive mode / augmented_mode for display
            mode = (
                meta.get("augmented_mode")
                or meta.get("mode")
                or meta.get("source")
                or "chat"
            )

            # Parse content JSON to find first user turn's text (first 80 chars)
            first_user = ""
            try:
                data = json.loads(content)
                for t in data.get("turns", []):
                    if t.get("role") == "user":
                        first_user = (t.get("content") or "")[:80].replace("\n", " ").strip()
                        break
            except Exception:
                # If not json or parse fail, use prefix of raw content
                first_user = content[:80].replace("\n", " ").strip()

            items.append({
                "turn_id": art["id"],
                "timestamp": created_at,
                "mode": mode,
                "first_user": first_user or "(no user content)",
                "domain": meta.get("domain", ""),
                "session_id": meta.get("session_id") or meta.get("conversation_id", ""),
            })

        return {"artifacts": items, "count": len(items), "project": project}
    finally:
        await store.close()


async def _history_show_async(turn_id: str) -> dict:
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore

    db_path = _get_db_path()
    store = VersionedArtifactStore(db_path)
    await store.initialize()
    try:
        try:
            content, metadata = await store.read_with_metadata(turn_id)
        except KeyError:
            return {"error": {"message": f"Turn not found: {turn_id}", "code": "NOT_FOUND"}}

        meta = metadata or {}
        user_msg = ""
        asst_msg = ""
        try:
            data = json.loads(content)
            turns = data.get("turns", [])
            for t in turns:
                role = t.get("role")
                txt = t.get("content") or ""
                if role == "user" and not user_msg:
                    user_msg = txt
                elif role == "assistant" and not asst_msg:
                    asst_msg = txt
            if not user_msg and not asst_msg:
                # Fallback for non-turn json content
                user_msg = content[:500]
        except Exception:
            user_msg = content[:500]
            asst_msg = "(could not parse turns)"

        return {
            "turn_id": turn_id,
            "domain": meta.get("domain", ""),
            "session_id": meta.get("session_id") or meta.get("conversation_id", ""),
            "model": meta.get("model") or meta.get("model_slot") or meta.get("model_name", "unknown"),
            "augmented_mode": meta.get("augmented_mode") or meta.get("mode", "unknown"),
            "user_message": user_msg,
            "assistant_response": asst_msg,
            "created_at": meta.get("created_at") or meta.get("imported_at", ""),
            "metadata": meta,
        }
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Output formatting (mirrors review.py style)
# ---------------------------------------------------------------------------


def _print_list_result(result: dict) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error: {err.get('message', err)}", err=True)
        sys.exit(1)

    items = result.get("artifacts", [])
    if not items:
        proj = result.get("project")
        msg = f"No conversation turns found{f' for project {proj}' if proj else ''}."
        click.echo(msg)
        click.echo("Ingest with: aip ingest file <path> --project <name>")
        click.echo("Or use chat (auto-saves turns when session auto_save enabled).")
        return

    click.echo(f"Conversation turns ({len(items)}):")
    click.echo("=" * 100)
    for it in items:
        ts = (it.get("timestamp") or "")[:19]
        mode = it.get("mode", "")
        snippet = it.get("first_user", "")[:80]
        click.echo(f"{it['turn_id']} | {ts} | {mode:12} | {snippet}")
    click.echo("=" * 100)
    click.echo("Use `aip history show <turn_id>` for full content.")


def _print_show_result(result: dict) -> None:
    if "error" in result:
        err = result["error"]
        click.echo(f"Error: {err.get('message', err)}", err=True)
        # Do not hard exit(1) here so caller can decide; but for CLI consistency:
        sys.exit(1)

    click.echo("=" * 60)
    click.echo(f"Conversation Turn: {result['turn_id']}")
    click.echo("=" * 60)
    click.echo(f"  Domain:         {result.get('domain', '')}")
    click.echo(f"  Session:        {result.get('session_id', '')}")
    click.echo(f"  Model:          {result.get('model', '')}")
    click.echo(f"  Mode:           {result.get('augmented_mode', '')}")
    if result.get("created_at"):
        click.echo(f"  Created:        {result['created_at']}")
    click.echo("")

    click.echo("--- User Message ---")
    click.echo(result.get("user_message", "(none)"))
    click.echo("")

    click.echo("--- Assistant Response ---")
    click.echo(result.get("assistant_response", "(none)"))
