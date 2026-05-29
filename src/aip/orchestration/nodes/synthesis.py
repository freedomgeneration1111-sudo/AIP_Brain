"""
Synthesis node — primary generation via model slot.

Phase 1: deterministic fixture stub.
Phase 4: real ModelSlotResolver integration with CI mode fallback.

Harness mediates everything the model sees.
Models are replaceable execution engines.
Synthesis slot resolves to DeepSeek-V3 by default.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aip.foundation.schemas import RetrievalResult
from aip.foundation.validation import structural_validate


@dataclass
class SynthesisOutput:
    """Legacy dataclass for Phase 1/2/3 compatibility."""

    content: str
    model_slot: str
    model_name: str
    token_count_in: int
    token_count_out: int
    latency_ms: int


# Phase 1 backward-compatible stub (used when model_resolver is None)
def _stub_synthesize(query: str, domain: str, context: str) -> str:
    """Deterministic fixture for CI testing — no model call."""
    input_hash = hashlib.sha256(f"{query}:{domain}:{context}".encode()).hexdigest()[:8]
    return f"[Synthesized output for '{query}' in domain '{domain}'] hash={input_hash}"


def _resolve_model_name(slot: str, config: dict | Any | None = None) -> str:
    """Resolve model slot to model name via aip.config.toml.
    In stub mode, returns the configured model name without network.
    """
    if config is None:
        cfg: dict = {}
    elif hasattr(config, "model_dump"):
        cfg = config.model_dump()
    elif isinstance(config, dict):
        cfg = config
    else:
        cfg = {}

    models = cfg.get("models", {}) if isinstance(cfg, dict) else {}
    slot_cfg = models.get(slot, {}) if isinstance(models, dict) else {}
    if isinstance(slot_cfg, dict):
        return slot_cfg.get("model", f"<{slot}-slot-unconfigured>")
    return f"<{slot}-slot-unconfigured>"


def _build_context_from_retrieval(retrieval_result: RetrievalResult | None) -> str:
    """Convert legacy RetrievalResult to context string for compat."""
    if not retrieval_result or not retrieval_result.hits:
        return "No retrieved context available."
    lines = []
    for i, hit in enumerate(retrieval_result.hits[:5]):
        snippet = (hit.content or "")[:200].replace("\n", " ")
        lines.append(f"[ID: {hit.id}] (score={hit.score:.2f}, domain={hit.domain}): {snippet}...")
    return "\n".join(lines)


async def synthesize(
    query: str,
    domain: str,
    context: str = "",
    retrieval_result: RetrievalResult | None = None,
    model_resolver: Any | None = None,
    config: Any | None = None,
    token_budget: int | None = None,
    model_slot: str = "synthesis",
) -> dict | SynthesisOutput:
    """Synthesize output from query, domain, and retrieved context.

    Phase 4 promoted version:
    - Accepts optional model_resolver (from ModelSlotResolver).
    - Loads prompts/synthesis.md when available.
    - Assembles messages.
    - Calls resolver for real (or CI fixture) synthesis.
    - Returns dict with content, model, usage, latency_ms, cost_usd.

    Backward compatibility (Rule #10 reconciliation):
    - Old callers passing retrieval_result=... continue to work.
    - When model_resolver is None, falls back to deterministic stub (Phase 1 path).
    - Old path returns SynthesisOutput; new resolver path returns dict.
    """
    # Handle legacy calling convention (retrieval_result instead of context str)
    effective_context = context
    if not effective_context and retrieval_result is not None:
        effective_context = _build_context_from_retrieval(retrieval_result)

    # Phase 1 backward compatibility: no model resolver → stub (return legacy type for compat)
    if model_resolver is None:
        stub_content = _stub_synthesize(query, domain, effective_context or "no context")
        # Preserve old behavior for callers expecting SynthesisOutput
        start = time.perf_counter()
        model_name = _resolve_model_name(model_slot, config)
        latency_ms = int((time.perf_counter() - start) * 1000) + 12

        token_in = 180 + len(query) // 4 + len(effective_context) // 10
        token_out = len(stub_content) // 4

        return SynthesisOutput(
            content=stub_content,
            model_slot=model_slot,
            model_name=model_name,
            token_count_in=token_in,
            token_count_out=token_out,
            latency_ms=latency_ms,
        )

    # Phase 4: real model call via ModelSlotResolver
    max_tokens = token_budget or 4096

    # Load synthesis prompt template (new in 6.1)
    prompt_path = Path(__file__).parent.parent.parent / "prompts" / "synthesis.md"
    system_prompt = ""
    if prompt_path.exists():
        system_prompt = prompt_path.read_text()

    # Assemble messages
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append(
        {
            "role": "user",
            "content": f"Domain: {domain}\n\nRetrieved Context:\n{effective_context}\n\nQuery: {query}",
        },
    )

    # Call model via synthesis slot
    result = await model_resolver.call(
        "synthesis",
        messages,
        temperature=0.7,
        max_tokens=max_tokens,
    )

    # Issue 20: Ensure return dict has spec-required keys
    return {
        "content": result.get("content", ""),
        "model": result.get("model", ""),
        "usage": result.get("usage", {}),
        "latency_ms": result.get("latency_ms", 0),
        "cost_usd": result.get("cost_usd", 0.0),
    }
