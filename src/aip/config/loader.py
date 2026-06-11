"""Canonical TOML config loader for AIP.

Extracted from aip.adapter.api.app so that orchestration modules
(ingestion/pipeline, embed_providers) can load config without
importing the FastAPI app factory — breaking the adapter → orchestration
circular dependency.

Usage:
    from aip.config.loader import load_toml_config, load_dotenv

    config = load_toml_config()   # dict from config/aip.config.toml
    load_dotenv()                 # load .env into os.environ
"""

from __future__ import annotations

import os
from pathlib import Path

from aip.logging import get_logger

log = get_logger(__name__)


def load_dotenv() -> None:
    """Load .env file from the project root if python-dotenv is available.

    This ensures AIP_OPENAI_API_KEY and other env vars are available
    to ModelSlotResolver without manually exporting them. Safe to call
    multiple times — dotenv won't overwrite existing env vars.
    """
    try:
        from dotenv import load_dotenv as _load_dotenv
    except ImportError:
        return
    # Search for .env in CWD and project root
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            _load_dotenv(env_path, override=False)
            log.info("dotenv_loaded", path=str(env_path))
            return


def load_toml_config() -> dict:
    """Load the AIP config from the default TOML file.

    Looks for config/aip.config.toml relative to the project root.
    The search order is:
      1. AIP_CONFIG_PATH environment variable (explicit override)
      2. config/aip.config.toml relative to CWD
      3. config/aip.config.toml relative to this file's parent (src/aip/config/ → ../../../config/)
    Returns an empty dict if no config file is found (not an error —
    the app can run with defaults, just no model slots).

    Also loads .env file if python-dotenv is available, so that
    AIP_OPENAI_API_KEY and other env vars are available to the
    ModelSlotResolver without manually exporting them.
    """
    # Load .env BEFORE reading any env vars (so AIP_OPENAI_API_KEY is available)
    load_dotenv()
    config_path = os.environ.get("AIP_CONFIG_PATH", "")
    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    else:
        candidates.append(Path.cwd() / "config" / "aip.config.toml")
        # Relative to this source file: src/aip/config/ → project root
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        candidates.append(project_root / "config" / "aip.config.toml")

    for path in candidates:
        if path.is_file():
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    log.warning("toml_config_unavailable", reason="neither tomllib nor tomli is installed")
                    return {}
            try:
                with open(path, "rb") as f:
                    cfg = tomllib.load(f)
                log.info("config_loaded", path=str(path), sections=list(cfg.keys()))
                return cfg
            except Exception as exc:
                log.warning("config_load_failed", path=str(path), error=str(exc))
                return {}

    log.info("config_not_found", searched=[str(p) for p in candidates])
    return {}
