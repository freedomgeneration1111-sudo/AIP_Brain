"""Configuration hot-reload — lightweight file-watch mechanism.

Sprint 5.25: Watches ``aip.config.toml`` for changes and reloads
specific, low-risk configuration values without a full process restart.

Supported hot-reload keys:
- ``read_pool.pool_size`` — global pool size for read-heavy stores
- ``read_pool.stores.*.pool_size`` — per-store pool size overrides
- ``sexton.graph_extraction_batch_size`` — graph extraction batch size
- ``sexton.graph_extraction_batch_size_max`` — max batch size cap
- ``sexton.graph_extraction_batch_size_min`` — min batch size floor

Design principles:
- Conservative — only reload specific, low-risk values
- Non-blocking — file checks are synchronous and fast (stat-based)
- Debounced — coalesces rapid edits within a 2-second window
- Observable — all reloads are logged and exposed via status endpoint
- Safe — parsing errors never crash the process; bad values are ignored

Implementation uses periodic polling (not inotify) for maximum
portability across Linux, macOS, and CI environments.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
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
})


@dataclass
class ConfigReloadEvent:
    """Record of a configuration hot-reload event."""

    key: str
    old_value: Any
    new_value: Any
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            from datetime import datetime, timezone
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "old_value": str(self.old_value),
            "new_value": str(self.new_value),
            "timestamp": self.timestamp,
        }


class ConfigWatcher:
    """Watches aip.config.toml for changes and applies safe hot-reloads.

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
        self._total_reloads = 0
        self._total_reload_errors = 0
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

    def _parse_and_apply(self) -> list[ConfigReloadEvent]:
        """Parse the config file and apply safe hot-reload values.

        Only reloads values in the hot-reloadable key set.  All other
        changes are ignored (they require a process restart).
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
        like ReadPoolMixin stores and the Sexton actor.
        """
        if section == "read_pool":
            self._apply_read_pool_change(dotted_key, value)
        elif section == "sexton":
            self._apply_sexton_change(dotted_key, value)

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
        """Return config watcher status for the health endpoint."""
        return {
            "enabled": self._enabled,
            "config_path": str(self._config_path),
            "config_file_exists": self._config_path.exists(),
            "last_mtime": self._last_mtime,
            "last_check": round(self._last_check, 1),
            "last_reload": round(self._last_reload, 1),
            "total_reloads": self._total_reloads,
            "total_reload_errors": self._total_reload_errors,
            "hot_reloadable_sections": list(_HOT_RELOADABLE_KEYS),
            "recent_reloads": [e.to_dict() for e in self._reload_history[-5:]],
        }
