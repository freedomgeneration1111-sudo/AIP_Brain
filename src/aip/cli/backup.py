"""aip backup command — consistent SQLite backup for all stores.

Uses VACUUM INTO for each .db file to produce a consistent snapshot
that does not lock the database or interrupt concurrent reads (WAL mode).

The backup directory defaults to ./backups/ and includes:
  - Per-DB VACUUM INTO snapshots (consistent, no write lock)
  - Config directory copy
  - A manifest.json listing all backed-up files and their sizes

This satisfies the Chunk 4 dogfood gate: a backup/export story exists
for all stores in the honest multi-file local datastore.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click


# The canonical list of DB files in the honest multi-file local datastore.
# This must match the store registry populated during app startup.
_KNOWN_DB_FILES = [
    "state.db",
    "lexical.db",
    "vectors.db",
    "vigil_quality.db",
    "alert_history.db",
    "ace_playbook.db",
]

_KNOWN_OPTIONAL_DB_FILES = [
    "trace.db",
]


def _vacuum_into(db_path: Path, backup_path: Path) -> dict[str, Any]:
    """Use VACUUM INTO to create a consistent snapshot of a SQLite database.

    VACUUM INTO writes a consistent snapshot to a new file without
    requiring an exclusive lock on the source database. This is safe
    to run while the database is in use (WAL mode ensures readers
    see a consistent view).

    Returns a dict with the result.
    """
    if not db_path.exists():
        return {"file": str(db_path), "status": "skipped", "reason": "file_not_found"}

    try:
        source_size = db_path.stat().st_size
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"VACUUM INTO '{backup_path}'")
        conn.close()
        backup_size = backup_path.stat().st_size
        return {
            "file": db_path.name,
            "status": "ok",
            "source_size_mb": round(source_size / (1024 * 1024), 2),
            "backup_size_mb": round(backup_size / (1024 * 1024), 2),
            "compression_ratio": round(backup_size / source_size, 2) if source_size > 0 else 0,
        }
    except Exception as exc:
        return {"file": db_path.name, "status": "error", "error": str(exc)}


@click.command("backup")
@click.option("--db-dir", default="db", help="Directory containing database files")
@click.option("--config-dir", default="config", help="Directory containing config files")
@click.option("--output-dir", default="backups", help="Output directory for backups")
@click.option("--include-optional", is_flag=True, help="Also backup optional DBs (trace, ace_playbook)")
def backup(db_dir: str, config_dir: str, output_dir: str, include_optional: bool) -> None:
    """Create a consistent backup of all AIP stores.

    Uses SQLite VACUUM INTO for each database file, producing consistent
    snapshots without locking the running application. Also copies the
    config directory and writes a manifest.json describing the backup.

    AIP uses an honest multi-file local datastore (Option B):
      - state.db:         Core entity/canonical/event/artifact/budget/project/
                           ECS/review/graph/corpus/session/autonomy data
      - lexical.db:       FTS5 full-text search index
      - vectors.db:       Vector embeddings (VSS or brute-force)
      - vigil_quality.db: Vigil quality cycle history
      - alert_history.db: Alert/delivery/experiment/mute rule persistence
      - ace_playbook.db:  ACE procedural intervention rules

    Optional (backed up with --include-optional):
      - trace.db:         Trace events and routing outcomes
    """
    db_path = Path(db_dir)
    config_path = Path(config_dir)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_root = Path(output_dir) / f"aip-backup-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=True)

    click.echo(f"=== AIP backup ===")
    click.echo(f"DB dir:    {db_path.resolve()}")
    click.echo(f"Config dir: {config_path.resolve()}")
    click.echo(f"Output:    {backup_root.resolve()}")
    click.echo()

    # Backup all known DB files using VACUUM INTO
    db_files = list(_KNOWN_DB_FILES)
    if include_optional:
        db_files.extend(_KNOWN_OPTIONAL_DB_FILES)

    manifest: dict[str, Any] = {
        "timestamp": timestamp,
        "architecture": "multi-file local datastore (Option B)",
        "databases": [],
        "config_included": False,
    }

    backed_up = 0
    skipped = 0
    errors = 0

    for db_name in db_files:
        source = db_path / db_name
        dest = backup_root / db_name
        click.echo(f"  {db_name}: ", nl=False)

        if not source.exists():
            click.echo("skipped (not found)")
            manifest["databases"].append({"file": db_name, "status": "skipped", "reason": "not_found"})
            skipped += 1
            continue

        result = _vacuum_into(source, dest)
        manifest["databases"].append(result)

        if result["status"] == "ok":
            click.echo(
                f"ok ({result['source_size_mb']}MB → {result['backup_size_mb']}MB, ratio={result['compression_ratio']})"
            )
            backed_up += 1
        else:
            click.echo(f"ERROR: {result.get('error', 'unknown')}")
            errors += 1

    # Also scan for any .db files in db_dir that we don't know about
    if db_path.exists():
        known_set = set(db_files)
        for extra_db in sorted(db_path.glob("*.db")):
            if extra_db.name not in known_set:
                source = extra_db
                dest = backup_root / extra_db.name
                click.echo(f"  {extra_db.name} (extra): ", nl=False)
                result = _vacuum_into(source, dest)
                manifest["databases"].append(result)
                if result["status"] == "ok":
                    click.echo(f"ok ({result['source_size_mb']}MB)")
                    backed_up += 1
                else:
                    click.echo(f"skipped: {result.get('reason', result.get('error', 'unknown'))}")
                    skipped += 1

    # Backup config directory
    if config_path.exists():
        config_backup = backup_root / "config"
        try:
            shutil.copytree(config_path, config_backup, dirs_exist_ok=True)
            manifest["config_included"] = True
            click.echo(f"  config/: copied")
        except Exception as exc:
            manifest["config_included"] = False
            manifest["config_error"] = str(exc)
            click.echo(f"  config/: ERROR: {exc}")
            errors += 1
    else:
        click.echo(f"  config/: skipped (not found)")

    # Write manifest
    manifest_path = backup_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    click.echo(f"  manifest.json: written")

    # Summary
    click.echo(f"\n=== Backup complete ===")
    click.echo(f"  Databases backed up: {backed_up}")
    click.echo(f"  Databases skipped:   {skipped}")
    click.echo(f"  Errors:              {errors}")
    click.echo(f"  Location:            {backup_root.resolve()}")
    click.echo(f"\nTo restore:")
    click.echo(f"  1. Stop the AIP application")
    click.echo(f"  2. Copy .db files from {backup_root}/ to {db_path}/")
    click.echo(f"  3. Copy config/ from {backup_root}/config/ to {config_path}/")
    click.echo(f"  4. Restart the application")
