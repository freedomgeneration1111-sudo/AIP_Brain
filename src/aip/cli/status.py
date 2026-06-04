"""aip status command — real system status from Protocols (offline-first).

Attempts to connect to real stores when available; falls back to
graceful degradation with clear "not configured" indicators.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click


def _check_db(db_path: str) -> dict:
    """Probe a SQLite database for table count and row stats."""
    info: dict = {"path": db_path, "exists": False, "tables": 0, "row_counts": {}}
    p = Path(db_path)
    if not p.exists():
        return info
    info["exists"] = True
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row["name"] for row in cur.fetchall()]
        info["tables"] = len(tables)
        for t in tables[:10]:  # cap at 10 tables to avoid noise
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if count > 0:
                    info["row_counts"][t] = count
            except Exception:
                pass
        conn.close()
    except Exception as exc:
        info["error"] = str(exc)
    return info


def _check_ollama() -> dict:
    """Check if Ollama is reachable."""
    info: dict = {"reachable": False, "models": []}
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2)
        if r.status_code == 200:
            info["reachable"] = True
            data = r.json()
            info["models"] = [m.get("name", "?") for m in data.get("models", [])[:5]]
    except Exception:
        pass
    return info


@click.command("status")
def status() -> None:
    """Print system status from Protocols (offline-first).

    Probes local databases, config files, and Ollama availability.
    When stores are not initialized, reports "not configured" clearly
    instead of showing fake placeholder data.
    """
    from aip.cli._db_path import get_default_db_path, get_default_lexical_db_path

    click.echo("=== AIP Status ===")

    # --- Config file ---
    config_path = Path("config/aip.config.toml")
    if config_path.exists():
        click.echo(f"config: {config_path} (found)")
        # Read key settings
        try:
            content = config_path.read_text()
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("provider") or stripped.startswith("host") or stripped.startswith("port") or stripped.startswith("db_path"):
                    click.echo(f"  {stripped}")
        except Exception:
            click.echo("  (could not read config)")
    else:
        click.echo("config: not found (run `aip init` to create)")

    # --- Effective database paths ---
    main_db = get_default_db_path()
    lexical_db = get_default_lexical_db_path()
    click.echo(f"\nEffective database paths:")
    click.echo(f"  Main DB:    {main_db}")
    click.echo(f"  Lexical DB: {lexical_db}")

    # --- Databases ---
    db_dir = Path("db")
    expected_dbs = {
        "state.db": "Core: artifacts, projects, events, ECS, canonicals",
        "trace.db": "Trace events and routing outcomes",
        "events.db": "Event store (append-only, legacy)",
        "lexical.db": "FTS5 full-text search index",
        "vectors.db": "SQLite-VSS vector index",
        "ace_playbook.db": "ACE playbook rules",
    }

    db_found = 0
    db_total_rows = 0
    for name, description in expected_dbs.items():
        db_path = db_dir / name
        if db_path.exists():
            info = _check_db(str(db_path))
            total = sum(info.get("row_counts", {}).values())
            db_found += 1
            db_total_rows += total
            if total > 0:
                click.echo(f"db/{name}: {info['tables']} tables, {total} rows — {description}")
                # Show row counts per table for state.db
                if name == "state.db" and info.get("row_counts"):
                    for table, count in info["row_counts"].items():
                        click.echo(f"    {table}: {count} rows")
            else:
                click.echo(f"db/{name}: empty ({info['tables']} tables) — {description}")
        else:
            click.echo(f"db/{name}: not found — {description}")

    if db_found == 0:
        click.echo("(no databases found — run `aip init` to create)")

    # --- Ollama / Model provider ---
    ollama = _check_ollama()
    if ollama["reachable"]:
        models_str = ", ".join(ollama["models"]) if ollama["models"] else "none listed"
        click.echo(f"ollama: reachable — models: {models_str}")
    else:
        click.echo("ollama: not reachable (embedding and local synthesis will use API fallback)")

    # --- Vector backend ---
    # NOTE: We check importability without a static import to preserve layer discipline.
    # CLI (foundation layer) should not directly import adapter code.
    try:
        import importlib

        importlib.import_module("aip.adapter.vector.factory")
        click.echo("vector_backend: factory available (will auto-select pgvector → sqlite-vss → in-memory)")
    except ImportError:
        click.echo("vector_backend: factory not importable")

    # --- Beast ---
    try:
        from aip.orchestration.actors.beast import Beast  # noqa: F401 -- import to test availability

        click.echo("beast: actor module available (runs via API lifespan scheduler)")
    except ImportError:
        click.echo("beast: not available")

    # --- Model slots (incl. beast for LLM intelligence) ---
    try:
        config_path = Path("config/aip.config.toml")
        if config_path.exists():
            import tomllib
            with open(config_path, "rb") as f:
                cfg = tomllib.load(f)
            from aip.adapter.model_slot_resolver import ModelSlotResolver
            resolver = ModelSlotResolver(cfg)
            slot_names = resolver.list_slots()
            if slot_names:
                details = []
                for s in slot_names:
                    try:
                        r = resolver._resolve_slot_config(s)
                        m = r.get("model", "<unset>")
                        has_key = bool(r.get("api_key"))
                        details.append(f"{s}={m}{' (key)' if has_key else ''}")
                    except Exception:
                        details.append(f"{s}=<error>")
                click.echo("model_slots: " + ", ".join(details))
            else:
                click.echo("model_slots: none parsed from resolver")
        else:
            click.echo("model_slots: config not found")
    except Exception as exc:
        click.echo(f"model_slots: error reading ({exc})")

    # --- Corpus turns (turn-level corpus, new atomic unit) ---
    try:
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.cli._db_path import get_default_db_path

        db_path = get_default_db_path()
        if Path(db_path).exists():
            store = CorpusTurnStore(db_path)
            # We can't easily await in sync status; use a tiny runner or just count via direct sql for status
            # For simplicity and to keep status sync, do a direct read (status is best-effort)
            import sqlite3
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                total = conn.execute("SELECT COUNT(*) FROM corpus_turns").fetchone()[0]
                untagged = conn.execute("SELECT COUNT(*) FROM corpus_turns WHERE tagging_version = 0").fetchone()[0]
                tagged = total - untagged

                # by_domain top 5 (only tagged turns)
                by_dom = {}
                for row in conn.execute(
                    "SELECT primary_domain, COUNT(*) as c FROM corpus_turns "
                    "WHERE tagging_version > 0 GROUP BY primary_domain ORDER BY c DESC LIMIT 5"
                ):
                    by_dom[row[0] or ""] = row[1]
                dom_str = ", ".join(f"{(k or ''):<18}:{v}" for k, v in list(by_dom.items())[:5]) or "none"

                by_src = {}
                for row in conn.execute("SELECT source_model, COUNT(*) as c FROM corpus_turns GROUP BY source_model"):
                    by_src[row[0] or "unknown"] = row[1]
                by_src_str = ", ".join(f"{k}={v}" for k, v in sorted(by_src.items()))

                # proposals_pending: beast_domain_proposal artifacts (GENERATED until reviewed)
                proposals_pending = 0
                try:
                    proposals_pending = conn.execute(
                        "SELECT COUNT(*) FROM artifacts WHERE metadata_json LIKE '%\"artifact_type\": \"beast_domain_proposal\"%'"
                    ).fetchone()[0]
                except Exception:
                    pass

                click.echo("corpus_turns:")
                click.echo(f"  total: {total}")
                click.echo(f"  tagged: {tagged} (tagging_version > 0)")
                click.echo(f"  untagged: {untagged} (tagging_version == 0)")
                click.echo(f"  by_domain: {dom_str}")
                click.echo(f"  proposals_pending: {proposals_pending} (beast_domain_proposal artifacts in GENERATED state)")
                click.echo(f"  by_source: {by_src_str}")
            finally:
                conn.close()
        else:
            click.echo("corpus_turns: no state.db yet")
    except Exception as exc:
        click.echo(f"corpus_turns: not initialized or error ({exc})")

    # --- Summary ---
    click.echo(f"\nDatabases: {db_found}/{len(expected_dbs)} found, {db_total_rows} total rows")
    if db_found == 0:
        click.echo("Tip: run `aip init` to set up databases and config.")

    # --- Config validation ---
    if config_path.exists():
        try:
            from aip.cli.config import validate_config_file

            if not validate_config_file(config_path):
                click.echo("\n⚠  Config validation failed. Run `aip validate` for details.")
        except Exception:
            pass  # Validation is best-effort in status command
