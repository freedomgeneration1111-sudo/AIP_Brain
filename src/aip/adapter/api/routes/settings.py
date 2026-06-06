"""Settings API routes — DEFINER profile and configuration.

Provides endpoints for the unified chat surface's Settings tab:
  - GET  /settings/definer-profile   — read the DEFINER profile markdown
  - POST /settings/definer-profile   — write the DEFINER profile markdown
  - GET  /settings/epistemic-flags   — read epistemic flag state
  - POST /settings/epistemic-flags   — write epistemic flag state

Per AIP-G-01: DEFINER direct edits bypass the ECS approval gate.
The profile is written directly to the file the ask pipeline reads from.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

# Default profile path — matches app.py startup logic:
#   definer_cfg.get("profile_path", "examples/seed_corpus/definer_profile_v1.md")
_DEFAULT_PROFILE_PATH = "examples/seed_corpus/definer_profile_v1.md"

# Config path — matches app.py _load_config() candidate resolution
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent.parent / "config" / "aip.config.toml"


# ── Pydantic models ──────────────────────────────────────────────────


class SaveDefinerProfileRequest(BaseModel):
    """Request body for POST /settings/definer-profile."""

    content: str


class SaveEpistemicFlagsRequest(BaseModel):
    """Request body for POST /settings/epistemic-flags."""

    no_flattery: bool = True
    flag_uncertainty: bool = True
    suggest_validation: bool = True
    report_conflicts: bool = True


# ── DEFINER Profile ──────────────────────────────────────────────────


@router.get("/settings/definer-profile")
async def get_definer_profile() -> dict[str, Any]:
    """Read the DEFINER profile markdown file.

    Per AIP-G-02: if the file is missing, return {content: "", path, missing: true}
    rather than raising 404. The UI shows a placeholder and the Save button
    creates the file.
    """
    profile_path = _resolve_profile_path()
    path = Path(profile_path)

    if not path.exists():
        return {"content": "", "path": profile_path, "missing": True}

    try:
        content = path.read_text(encoding="utf-8")
        return {"content": content, "path": profile_path, "missing": False}
    except Exception as exc:
        logger.error("Failed to read DEFINER profile: %s", exc)
        return {"content": "", "path": profile_path, "missing": True}


@router.post("/settings/definer-profile")
async def save_definer_profile(body: SaveDefinerProfileRequest) -> dict[str, Any]:
    """Write the DEFINER profile markdown file.

    Per AIP-G-01: this is a DEFINER direct edit — not subject to the ECS
    approval gate. Write directly to the same path the ask pipeline reads from.
    The DefinerProfile adapter caches for 300s, so changes take effect on
    next cache expiry or server restart.
    """
    profile_path = _resolve_profile_path()
    path = Path(profile_path)

    try:
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.content, encoding="utf-8")
        logger.info("DEFINER profile saved: %s (%d bytes)", profile_path, len(body.content))
        return {"ok": True, "path": profile_path}
    except Exception as exc:
        logger.error("Failed to save DEFINER profile: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── Epistemic Flags ──────────────────────────────────────────────────

# Default flag values (all True = all epistemic sentences active)
_DEFAULT_FLAGS: dict[str, bool] = {
    "no_flattery": True,
    "flag_uncertainty": True,
    "suggest_validation": True,
    "report_conflicts": True,
}


@router.get("/settings/epistemic-flags")
async def get_epistemic_flags() -> dict[str, Any]:
    """Read epistemic flag state from config/aip.config.toml.

    Returns the current flag state. If the file or [chat.epistemic_flags]
    section is missing, returns all defaults (True).
    """
    flags = dict(_DEFAULT_FLAGS)

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {"flags": flags, "source": "defaults"}

    config_path = _CONFIG_PATH
    if not config_path.exists():
        return {"flags": flags, "source": "defaults"}

    try:
        with open(config_path, "rb") as f:
            cfg = tomllib.load(f)
        epi_cfg = cfg.get("chat", {}).get("epistemic_flags", {})
        if isinstance(epi_cfg, dict):
            for key in _DEFAULT_FLAGS:
                if key in epi_cfg:
                    flags[key] = bool(epi_cfg[key])
        return {"flags": flags, "source": "config"}
    except Exception as exc:
        logger.error("Failed to read epistemic flags from config: %s", exc)
        return {"flags": flags, "source": "defaults"}


@router.post("/settings/epistemic-flags")
async def save_epistemic_flags(body: SaveEpistemicFlagsRequest) -> dict[str, Any]:
    """Write epistemic flag state to config/aip.config.toml.

    Appends or updates the [chat.epistemic_flags] section in the TOML file.
    Uses manual string building since tomli-w is not a dependency.
    """
    flags = {
        "no_flattery": body.no_flattery,
        "flag_uncertainty": body.flag_uncertainty,
        "suggest_validation": body.suggest_validation,
        "report_conflicts": body.report_conflicts,
    }

    try:
        _write_epistemic_flags_section(flags)
        return {"ok": True, "flags": flags}
    except Exception as exc:
        logger.error("Failed to save epistemic flags: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_profile_path() -> str:
    """Resolve the DEFINER profile path from config, falling back to default.

    Mirrors app.py startup logic: reads [definer].profile_path from the
    config TOML, defaulting to examples/seed_corpus/definer_profile_v1.md.
    """
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return _DEFAULT_PROFILE_PATH

    config_path = _CONFIG_PATH
    if not config_path.exists():
        return _DEFAULT_PROFILE_PATH

    try:
        with open(config_path, "rb") as f:
            cfg = tomllib.load(f)
        definer_cfg = cfg.get("definer", {})
        if isinstance(definer_cfg, dict):
            return definer_cfg.get("profile_path", _DEFAULT_PROFILE_PATH)
    except Exception:
        pass
    return _DEFAULT_PROFILE_PATH


def _write_epistemic_flags_section(flags: dict[str, bool]) -> None:
    """Write the [chat.epistemic_flags] section into config/aip.config.toml.

    Strategy: read existing file, remove any existing [chat.epistemic_flags]
    lines, then append the new section. This preserves all other content.
    If the file doesn't exist, create it with just the section.
    """
    config_path = _CONFIG_PATH

    # Build the new section as TOML text
    section_lines = [
        "",
        "# Epistemic flags for chat mode modifiers (Phase 4)",
        "[chat.epistemic_flags]",
    ]
    for key, val in flags.items():
        section_lines.append(f"{key} = {str(val).lower()}")
    new_section = "\n".join(section_lines) + "\n"

    if not config_path.exists():
        # Create new file with just the section
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(new_section.lstrip("\n"), encoding="utf-8")
        return

    # Read existing content
    existing = config_path.read_text(encoding="utf-8")
    lines = existing.splitlines()

    # Remove existing [chat.epistemic_flags] section and its content
    filtered: list[str] = []
    in_epistemic_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[chat.epistemic_flags]":
            in_epistemic_section = True
            continue
        # Detect start of a new section (lines starting with [)
        if in_epistemic_section and stripped.startswith("[") and not stripped.startswith("#"):
            in_epistemic_section = False
        # Also remove Phase 4 comment just above the section
        if in_epistemic_section:
            continue
        # Remove trailing comment line that precedes the section header
        filtered.append(line)

    # Remove trailing blank lines before appending
    while filtered and filtered[-1].strip() == "":
        filtered.pop()

    # Append the new section
    result = "\n".join(filtered) + new_section

    config_path.write_text(result, encoding="utf-8")
