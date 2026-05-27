"""
Embedding Providers (CHUNK-3.9 foundation for Phase 3 real embedding slot).

Per Rev 1.3 Phase 3 notes and Architecture §4.
In Phase 1/CI: always fake_embed (deterministic, zero external deps).
In Phase 3+: can load a real provider (e.g. local sentence-transformers or API)
based on [embedding] section in config.

This file provides the loader. The actual real implementation is stubbed for foundation;
real model wiring would be in a follow-on Phase 3 chunk.
"""

from __future__ import annotations

from typing import Any, Callable

from aip.orchestration.retrieval import fake_embed


def get_embed_fn(config: dict | Any | None = None) -> Callable[[str], list[float]]:
    """
    Returns an embed callable based on config.

    - If no [embedding] section or provider == "fake" (default): returns fake_embed.
    - Otherwise: returns a stub (for foundation). Real implementation would load
      the model specified in config["embedding"]["model"] etc.
    """
    if config is None:
        return fake_embed

    if hasattr(config, "model_dump"):
        cfg = config.model_dump()
    elif isinstance(config, dict):
        cfg = config
    else:
        cfg = {}

    emb = cfg.get("embedding", {}) if isinstance(cfg, dict) else {}
    provider = emb.get("provider", "fake")

    if provider == "fake":
        return fake_embed

    # Foundation stub for "real" provider (e.g. "local" or "api").
    # In a real Phase 3 chunk this would return a loaded model.
    def _stub_embed(text: str, dimensions: int = 768) -> list[float]:
        # Same deterministic behavior as fake for now (so tests/CI unaffected).
        # Real version would call the actual embedder.
        return fake_embed(text, dimensions)

    return _stub_embed
