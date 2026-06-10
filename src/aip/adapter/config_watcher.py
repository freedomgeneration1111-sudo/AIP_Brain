"""Configuration hot-reload — lightweight file-watch mechanism.

Sprint 5.25: Watches ``aip.config.toml`` for changes and reloads
specific, low-risk configuration values without a full process restart.

Sprint 5.26: Safety audit improvements:
- Validation for hot-reloaded values (pool_size 1-20, batch_size respects min/max)
- Rejection logging with clear messages
- Admin endpoint for hot-reload status, pending/rejected changes
- Support for ``[auto_tuning_policy]`` section hot-reload

Supported hot-reload keys:
- ``read_pool.pool_size`` — global pool size for read-heavy stores
- ``read_pool.stores.*.pool_size`` — per-store pool size overrides
- ``sexton.graph_extraction_batch_size`` — graph extraction batch size
- ``sexton.graph_extraction_batch_size_max`` — max batch size cap
- ``sexton.graph_extraction_batch_size_min`` — min batch size floor
- ``auto_tuning_policy.*`` — auto-tuning policy parameters (Sprint 5.26)

Design principles:
- Conservative — only reload specific, low-risk values
- Non-blocking — file checks are synchronous and fast (stat-based)
- Debounced — coalesces rapid edits within a 2-second window
- Observable — all reloads are logged and exposed via status endpoint
- Safe — parsing errors never crash the process; bad values are rejected
- Validated — values are checked against safe ranges before applying
  (Sprint 5.26)

Implementation uses periodic polling (not inotify) for maximum
portability across Linux, macOS, and CI environments.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aip.logging import get_logger

logger = get_logger(__name__)

# How often to check the config file for changes (seconds)
_DEFAULT_POLL_INTERVAL = 5.0

# Minimum time between actual reloads (debounce window, seconds)
_DEBOUNCE_INTERVAL = 2.0

# Keys that are safe to hot-reload
_HOT_RELOADABLE_KEYS = frozenset({
    "read_pool",
    "sexton",
    "auto_tuning_policy",  # Sprint 5.26
    "vigil_quality",       # Sprint 5.31
})

# ---------------------------------------------------------------------------
# Validation rules for hot-reloaded values (Sprint 5.26)
# ---------------------------------------------------------------------------

# Mapping: dotted_key -> (min_value, max_value, description)
_VALUE_RANGES: dict[str, tuple[float, float, str]] = {
    "read_pool.pool_size": (1, 20, "pool_size must be between 1 and 20"),
    "sexton.graph_extraction_batch_size": (1, 16, "batch_size must be between 1 and 16"),
    "sexton.graph_extraction_batch_size_max": (2, 16, "batch_size_max must be between 2 and 16"),
    "sexton.graph_extraction_batch_size_min": (1, 4, "batch_size_min must be between 1 and 4"),
    # Per-store pool_size overrides — validated by the same range as global
}

# Auto-tuning policy validation ranges
_POLICY_RANGES: dict[str, tuple[float, float, str]] = {
    "auto_tuning_policy.read_pool_exhaustion_threshold": (0.1, 0.9, "must be 0.1-0.9"),
    "auto_tuning_policy.read_pool_auto_apply_consecutive": (2, 20, "must be 2-20"),
    "auto_tuning_policy.read_pool_auto_apply_max_increase": (1, 10, "must be 1-10"),
    "auto_tuning_policy.read_pool_auto_apply_max_pool": (3, 20, "must be 3-20"),
    "auto_tuning_policy.read_pool_auto_rollback_consecutive": (2, 20, "must be 2-20"),
    "auto_tuning_policy.read_pool_auto_rollback_healthy": (0.01, 0.5, "must be 0.01-0.5"),
    "auto_tuning_policy.graph_batch_decrease_threshold": (0.1, 0.8, "must be 0.1-0.8"),
    "auto_tuning_policy.graph_batch_increase_threshold": (0.0, 0.5, "must be 0.0-0.5"),
    "auto_tuning_policy.graph_batch_auto_tune_window": (2, 20, "must be 2-20"),
    "auto_tuning_policy.graph_batch_min_size": (1, 4, "must be 1-4"),
    "auto_tuning_policy.graph_batch_max_size": (2, 16, "must be 2-16"),
    "auto_tuning_policy.cooldown_seconds": (0, 3600, "must be 0-3600"),
}

# Sprint 5.31: Vigil quality retention config validation ranges
_VIGIL_QUALITY_RANGES: dict[str, tuple[float, float, str]] = {
    "vigil_quality.retention_days": (0, 365, "must be 0-365"),
    "vigil_quality.rollup_age_days": (0, 365, "must be 0-365"),
    "vigil_quality.weekly_rollup_age_weeks": (0, 52, "must be 0-52"),
    "vigil_quality.max_history_rows": (100, 100000, "must be 100-100000"),
}


@dataclass
class ConfigReloadEvent:
    """Record of a configuration hot-reload event."""

    key: str
    old_value: Any
    new_value: Any
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "old_value": str(self.old_value),
            "new_value": str(self.new_value),
            "timestamp": self.timestamp,
        }


@dataclass
class ConfigRejectedEvent:
    """Record of a rejected hot-reload value (Sprint 5.26).

    When a hot-reloaded value fails validation, it is recorded here
    with the reason for rejection. The old value is preserved.
    """

    key: str
    rejected_value: Any
    reason: str
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "rejected_value": str(self.rejected_value),
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


class ConfigWatcher:
    """Watches aip.config.toml for changes and applies safe hot-reloads.

    Sprint 5.26 improvements:
    - Validates hot-reloaded values against safe ranges before applying
    - Records rejected values with clear reason strings
    - Supports ``[auto_tuning_policy]`` section
    - Provides detailed status via get_status() including rejected changes

    Usage::

        watcher = ConfigWatcher(
            config_path="config/aip.config.toml",
            container=container,
        )
        # Call periodically (e.g., from a background task or health check)
        watcher.check_and_reload()
        # Get status for health endpoint
        status = watcher.get_status()
    """

    # Maximum number of rejected events to keep
    _MAX_REJECTED_HISTORY = 30

    def __init__(
        self,
        config_path: str | Path,
        container: Any = None,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._config_path = Path(config_path)
        self._container = container
        self._poll_interval = poll_interval
        self._last_mtime: float = 0.0
        self._last_check: float = 0.0
        self._last_reload: float = 0.0
        self._reload_history: list[ConfigReloadEvent] = []
        self._rejected_history: list[ConfigRejectedEvent] = []  # Sprint 5.26
        self._total_reloads = 0
        self._total_reload_errors = 0
        self._total_rejected = 0  # Sprint 5.26
        self._enabled = True

        # Initialize mtime
        if self._config_path.exists():
            try:
                self._last_mtime = self._config_path.stat().st_mtime
            except OSError:
                pass

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check_and_reload(self) -> list[ConfigReloadEvent]:
        """Check if config file changed and apply hot-reload if so.

        Returns a list of ConfigReloadEvent for any changes applied.
        Returns empty list if no changes detected or if within debounce
        window.
        """
        if not self._enabled or not self._config_path.exists():
            return []

        # Rate-limit checks
        now = time.time()
        if now - self._last_check < self._poll_interval:
            return []
        self._last_check = now

        # Check file modification time
        try:
            current_mtime = self._config_path.stat().st_mtime
        except OSError:
            return []

        if current_mtime <= self._last_mtime:
            return []

        # File changed — but debounce rapid edits
        if now - self._last_reload < _DEBOUNCE_INTERVAL:
            return []

        logger.info(
            "config_hot_reload_detected",
            path=str(self._config_path),
            old_mtime=self._last_mtime,
            new_mtime=current_mtime,
        )

        # Parse and apply
        events = self._parse_and_apply()
        self._last_mtime = current_mtime
        self._last_reload = now
        return events

    def _validate_value(self, dotted_key: str, value: Any) -> tuple[bool, str]:
        """Validate a hot-reloaded value against safe ranges.

        Returns (is_valid, reason).  If valid, reason is empty string.
        """
        # Check direct key ranges
        if dotted_key in _VALUE_RANGES:
            min_val, max_val, desc = _VALUE_RANGES[dotted_key]
            if isinstance(value, (int, float)):
                if not (min_val <= value <= max_val):
                    return False, desc
            return True, ""

        # Check per-store pool_size overrides
        if "read_pool.stores" in dotted_key and dotted_key.endswith(".pool_size"):
            if isinstance(value, (int, float)):
                if not (1 <= value <= 20):
                    return False, "per-store pool_size must be between 1 and 20"
            return True, ""

        # Check auto-tuning policy ranges
        if dotted_key in _POLICY_RANGES:
            min_val, max_val, desc = _POLICY_RANGES[dotted_key]
            if isinstance(value, (int, float)):
                if not (min_val <= value <= max_val):
                    return False, desc
            return True, ""

        # Sprint 5.31: Check vigil quality retention ranges
        if dotted_key in _VIGIL_QUALITY_RANGES:
            min_val, max_val, desc = _VIGIL_QUALITY_RANGES[dotted_key]
            if isinstance(value, (int, float)):
                if not (min_val <= value <= max_val):
                    return False, desc
            return True, ""

        # No specific validation rule — allow (but log)
        return True, ""

    def _parse_and_apply(self) -> list[ConfigReloadEvent]:
        """Parse the config file and apply safe hot-reload values.

        Only reloads values in the hot-reloadable key set.  All other
        changes are ignored (they require a process restart).

        Sprint 5.26: Validates values before applying and records
        rejected changes.
        """
        events: list[ConfigReloadEvent] = []

        try:
            # Use tomllib (Python 3.11+) or fall back to toml package
            try:
                import tomllib
                with open(self._config_path, "rb") as f:
                    new_config = tomllib.load(f)
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                    with open(self._config_path, "rb") as f:
                        new_config = tomllib.load(f)
                except ImportError:
                    # Last resort — try the toml package
                    import toml  # type: ignore[import-not-found]
                    new_config = toml.load(str(self._config_path))
        except Exception as exc:
            self._total_reload_errors += 1
            logger.warning(
                "config_hot_reload_parse_failed",
                path=str(self._config_path),
                error=str(exc),
            )
            return events

        if self._container is None:
            return events

        # Get the current in-memory config
        current_config = getattr(self._container, "config", None)
        if current_config is None or not isinstance(current_config, dict):
            return events

        # Apply hot-reloadable keys
        for key in _HOT_RELOADABLE_KEYS:
            if key not in new_config:
                continue

            new_section = new_config[key]
            current_section = current_config.get(key, {})

            if not isinstance(new_section, dict) or not isinstance(current_section, dict):
                continue

            # Find changed values within this section
            changed = self._find_changes(key, current_section, new_section)
            for event in changed:
                # Sprint 5.26: Validate before applying
                is_valid, reason = self._validate_value(event.key, event.new_value)
                if not is_valid:
                    self._total_rejected += 1
                    rejected = ConfigRejectedEvent(
                        key=event.key,
                        rejected_value=event.new_value,
                        reason=reason,
                    )
                    self._rejected_history.append(rejected)
                    if len(self._rejected_history) > self._MAX_REJECTED_HISTORY:
                        self._rejected_history = self._rejected_history[-self._MAX_REJECTED_HISTORY:]
                    logger.warning(
                        "config_hot_reload_rejected",
                        key=event.key,
                        rejected_value=event.new_value,
                        reason=reason,
                    )
                    continue  # Skip this change

                events.append(event)
                # Apply to in-memory config
                self._apply_to_config(key, event.key, event.new_value)
                # Apply to live components
                self._apply_to_components(key, event.key, event.new_value)

        if events:
            self._total_reloads += 1
            self._reload_history.extend(events)
            # Keep last 30 reload events
            if len(self._reload_history) > 30:
                self._reload_history = self._reload_history[-30:]

            logger.info(
                "config_hot_reload_applied",
                changes=len(events),
                keys=[e.key for e in events],
            )

        return events

    def _find_changes(
        self,
        section: str,
        current: dict,
        new: dict,
        prefix: str = "",
    ) -> list[ConfigReloadEvent]:
        """Recursively find changed values between current and new config sections."""
        events: list[ConfigReloadEvent] = []

        for key, new_value in new.items():
            full_key = f"{prefix}.{key}" if prefix else key
            current_value = current.get(key)

            if isinstance(new_value, dict) and isinstance(current_value, dict):
                # Recurse into nested dicts
                events.extend(self._find_changes(section, current_value, new_value, full_key))
            elif new_value != current_value:
                # Only reload numeric and boolean values (safe types)
                if isinstance(new_value, (int, float, bool)):
                    events.append(ConfigReloadEvent(
                        key=f"{section}.{full_key}",
                        old_value=current_value,
                        new_value=new_value,
                    ))

        return events

    def _apply_to_config(self, section: str, dotted_key: str, value: Any) -> None:
        """Apply a value to the in-memory config dict."""
        current_config = getattr(self._container, "config", None)
        if current_config is None:
            return

        # Parse the dotted key: "section.subkey.leaf" → ["subkey", "leaf"]
        # The section is already the top-level key, so strip it
        parts = dotted_key.removeprefix(section + ".").split(".")

        target = current_config.setdefault(section, {})
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        if parts:
            target[parts[-1]] = value

    def _apply_to_components(self, section: str, dotted_key: str, value: Any) -> None:
        """Apply a hot-reloaded value to live components.

        This is where we wire config changes to actual running components
        like ReadPoolMixin stores, the Sexton actor, and the auto-sizer.
        """
        if section == "read_pool":
            self._apply_read_pool_change(dotted_key, value)
        elif section == "sexton":
            self._apply_sexton_change(dotted_key, value)
        elif section == "auto_tuning_policy":
            self._apply_policy_change(dotted_key, value)
        elif section == "vigil_quality":
            self._apply_vigil_quality_change(dotted_key, value)

    def _apply_read_pool_change(self, dotted_key: str, value: Any) -> None:
        """Apply read pool config changes to live stores."""
        try:
            if dotted_key == "read_pool.pool_size":
                # Update all pool-enabled stores that don't have per-store overrides
                self._update_all_pool_sizes(value)
            elif "read_pool.stores" in dotted_key:
                # Per-store override: read_pool.stores.<store_name>.pool_size
                parts = dotted_key.split(".")
                if len(parts) >= 4 and parts[-1] == "pool_size":
                    store_name = parts[2]
                    self._update_store_pool_size(store_name, value)
        except Exception as exc:
            self._total_reload_errors += 1
            logger.warning(
                "config_hot_reload_pool_apply_failed",
                key=dotted_key,
                value=value,
                error=str(exc),
            )

    def _apply_sexton_change(self, dotted_key: str, value: Any) -> None:
        """Apply Sexton config changes to the live Sexton actor."""
        try:
            sexton = getattr(self._container, "sexton_actor", None)
            if sexton is None:
                return

            config = getattr(sexton, "_config", None)
            if config is None:
                return

            if dotted_key == "sexton.graph_extraction_batch_size":
                old_size = getattr(sexton, "_current_batch_size", 0)
                # Only update if auto-tune hasn't changed it
                configured = getattr(config, "graph_extraction_batch_size", 2)
                if old_size == configured:
                    sexton._current_batch_size = int(value)
                config.graph_extraction_batch_size = int(value)
                logger.info(
                    "config_hot_reload_batch_size",
                    old=old_size,
                    new=int(value),
                )
            elif dotted_key == "sexton.graph_extraction_batch_size_max":
                config.graph_extraction_batch_size_max = int(value)
            elif dotted_key == "sexton.graph_extraction_batch_size_min":
                config.graph_extraction_batch_size_min = int(value)
        except Exception as exc:
            self._total_reload_errors += 1
            logger.warning(
                "config_hot_reload_sexton_apply_failed",
                key=dotted_key,
                value=value,
                error=str(exc),
            )

    def _apply_policy_change(self, dotted_key: str, value: Any) -> None:
        """Apply auto-tuning policy changes to live components.

        When policy values change, we reload the full policy from the
        in-memory config and apply it to the auto-sizer and Sexton.
        """
        try:
            from aip.adapter.auto_tuning_policy import (
                load_policy_from_config,
                apply_policy_to_auto_sizer,
                apply_policy_to_sexton,
            )

            current_config = getattr(self._container, "config", {})
            policy = load_policy_from_config(current_config)

            if not policy.is_valid():
                logger.warning(
                    "config_hot_reload_policy_invalid",
                    errors=policy._validation_errors,
                )
                return

            # Apply to auto-sizer
            auto_sizer = getattr(self._container, "_read_pool_auto_sizer", None)
            if auto_sizer is not None:
                apply_policy_to_auto_sizer(policy, auto_sizer)

            # Apply to Sexton
            sexton = getattr(self._container, "sexton_actor", None)
            if sexton is not None:
                apply_policy_to_sexton(policy, sexton)

            logger.info(
                "config_hot_reload_policy_applied",
                changed_key=dotted_key,
            )
        except Exception as exc:
            self._total_reload_errors += 1
            logger.warning(
                "config_hot_reload_policy_apply_failed",
                key=dotted_key,
                value=value,
                error=str(exc),
            )

    def _apply_vigil_quality_change(self, dotted_key: str, value: Any) -> None:
        """Apply vigil quality retention config changes to the live store.

        Sprint 5.31: When retention_days, rollup_age_days, or
        weekly_rollup_age_weeks are changed via config file hot-reload,
        applies the changes to the running VigilQualityStore and triggers
        appropriate pruning/rollup behavior.
        """
        try:
            quality_store = getattr(self._container, "_vigil_quality_store", None)
            if quality_store is None:
                return

            # Map config keys to update_config parameters
            update_params: dict[str, int] = {}
            if dotted_key == "vigil_quality.retention_days":
                update_params["retention_days"] = int(value)
            elif dotted_key == "vigil_quality.rollup_age_days":
                update_params["rollup_age_days"] = int(value)
            elif dotted_key == "vigil_quality.weekly_rollup_age_weeks":
                update_params["weekly_rollup_age_weeks"] = int(value)
            elif dotted_key == "vigil_quality.max_history_rows":
                # max_history_rows is not updatable via update_config,
                # but we can update the internal attribute directly
                quality_store._max_history_rows = int(value)
                logger.info(
                    "config_hot_reload_vigil_quality",
                    key=dotted_key,
                    new_value=int(value),
                )
                return

            if update_params:
                result = quality_store.update_config(**update_params)
                has_errors = bool(result.get("validation_errors", []))
                if has_errors:
                    logger.warning(
                        "config_hot_reload_vigil_quality_validation_failed",
                        errors=result.get("validation_errors", []),
                    )
                else:
                    logger.info(
                        "config_hot_reload_vigil_quality_applied",
                        changed_key=dotted_key,
                        new_config=result,
                    )
                    # Trigger immediate pruning with new retention settings
                    try:
                        import asyncio
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(quality_store._prune_if_needed())
                        except RuntimeError:
                            asyncio.run(quality_store._prune_if_needed())
                    except Exception:
                        pass  # Pruning failure is not critical
        except Exception as exc:
            self._total_reload_errors += 1
            logger.warning(
                "config_hot_reload_vigil_quality_apply_failed",
                key=dotted_key,
                value=value,
                error=str(exc),
            )

    def _update_all_pool_sizes(self, new_size: int) -> None:
        """Update pool size on all pool-enabled stores."""
        new_size = max(1, min(20, int(new_size)))
        # Find all stores with ReadPoolMixin
        store_names = [
            "lexical_store", "vector_store", "graph_store", "corpus_turn_store",
        ]
        for store_name in store_names:
            self._update_store_pool_size(store_name, new_size)

    def _update_store_pool_size(self, store_name: str, new_size: int) -> None:
        """Update pool size on a specific store."""
        new_size = max(1, min(20, int(new_size)))
        store = getattr(self._container, store_name, None)
        if store is not None and hasattr(store, "_read_pool_size"):
            old_size = store._read_pool_size
            if old_size != new_size:
                store._read_pool_size = new_size
                # Mark pool as needing re-initialization
                if hasattr(store, "_read_pool_initialized"):
                    store._read_pool_initialized = False
                logger.info(
                    "config_hot_reload_pool_size",
                    store=store_name,
                    old_size=old_size,
                    new_size=new_size,
                )

    def get_status(self) -> dict:
        """Return config watcher status for the health/admin endpoint."""
        return {
            "enabled": self._enabled,
            "config_path": str(self._config_path),
            "config_file_exists": self._config_path.exists(),
            "last_mtime": self._last_mtime,
            "last_check": round(self._last_check, 1),
            "last_reload": round(self._last_reload, 1),
            "total_reloads": self._total_reloads,
            "total_reload_errors": self._total_reload_errors,
            "total_rejected": self._total_rejected,
            "hot_reloadable_sections": list(_HOT_RELOADABLE_KEYS),
            "recent_reloads": [e.to_dict() for e in self._reload_history[-5:]],
            "recent_rejections": [r.to_dict() for r in self._rejected_history[-5:]],
        }
