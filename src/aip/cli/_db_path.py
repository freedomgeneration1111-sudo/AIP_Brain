"""Shared database path resolution for AIP CLI commands.

Ensures ALL CLI commands use the same default datastore contract:
- Read db_path from config/aip.config.toml if available
- Fall back to db/state.db (matching what `aip init` creates)
- Derive lexical DB path from the same directory

This module is the SINGLE SOURCE OF TRUTH for default DB paths
in the CLI layer. No CLI command should hardcode "data/aip.db".
"""

from __future__ import annotations

import os
from pathlib import Path


def get_default_db_path() -> str:
    """Resolve the default database path from config or fallback.

    Resolution order:
    1. AIP_DB_PATH environment variable
    2. db_path setting in config/aip.config.toml
    3. Fallback: db/state.db

    Returns a string path suitable for passing to store constructors.
    """
    # 1. Environment variable override
    env_path = os.environ.get("AIP_DB_PATH")
    if env_path:
        return env_path

    # 2. Config file — look specifically for [database] section
    config_path = Path("config/aip.config.toml")
    if config_path.exists():
        try:
            content = config_path.read_text()
            in_database_section = False
            for line in content.splitlines():
                stripped = line.strip()
                # Track TOML sections
                if stripped.startswith("["):
                    in_database_section = stripped == "[database]"
                    continue
                # Only read db_path from [database] section
                if in_database_section and stripped.startswith("db_path"):
                    _, _, val = stripped.partition("=")
                    resolved = val.strip().strip('"').strip("'")
                    if resolved:
                        return resolved
        except Exception:
            pass

    # 3. Default — matches what `aip init` creates
    return "db/state.db"


def get_default_lexical_db_path() -> str:
    """Derive the lexical (FTS5) database path from the main DB path.

    The lexical DB lives in the same directory as the main DB,
    named 'lexical.db'. This ensures that lexical search and
    the main stores are always co-located.
    """
    db_path = get_default_db_path()
    lexical_path = os.path.join(os.path.dirname(db_path), "lexical.db")
    return lexical_path


def ensure_db_dir(db_path: str) -> None:
    """Ensure the parent directory for a database path exists."""
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
