"""Plugin and performance configuration types.

Plugin system configuration, performance tuning, and plugin status tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Type alias for plugin status
PluginStatus = Literal["loaded", "error", "disabled"]


@dataclass
class PluginConfig:
    """Plugin system configuration.

    No hardcoded model names — plugins provide extensibility.
    enabled and sandbox_mode are toggleable.
    model_gen_assumption tags what model limitations plugins compensate for.
    """

    plugins_dir: str = "plugins"
    enabled: bool = True
    auto_discover: bool = True
    sandbox_mode: bool = True
    model_gen_assumption: str | None = None


@dataclass
class PerformanceConfig:
    """Performance tuning configuration.

    Laptop-viable — must work on 4-6 GB RAM.
    profiling_enabled is toggleable.
    """

    profiling_enabled: bool = False
    max_memory_mb: int = 4096
    retrieval_timeout_seconds: float = 30.0
    batch_embed_size: int = 32
    sqlite_wal_mode: bool = True
    sqlite_busy_timeout_ms: int = 5000
    vector_query_limit: int = 50
    fts5_query_limit: int = 50


__all__ = [
    "PluginStatus",
    "PluginConfig",
    "PerformanceConfig",
]
