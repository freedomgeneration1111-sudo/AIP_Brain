"""
Synthesis Node Stub (CHUNK-1.3 per Rev 1.3).

This is an explicit stub for Phase 1. No model API calls are made.
Model name is resolved from config (accepting the minimal shape from 0.BOOTSTRAP).
The generated content is deterministic and must pass structural_validate (CHUNK-1.2).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from aip.foundation.schemas import RetrievalResult
from aip.foundation.validation import structural_validate


@dataclass
class SynthesisOutput:
    content: str
    model_slot: str
    model_name: str
    token_count_in: int
    token_count_out: int
    latency_ms: int


def _resolve_model_name(slot: str, config: dict | Any | None = None) -> str:
    """Resolve model slot to model name via aip.config.toml.
    In Phase 1 stub mode, returns the configured model name
    without making any API calls or network requests.
    Follows the exact pattern shown in the Rev 1.3 ANNEX for CHUNK-1.3.
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


async def synthesize(
    query: str,
    domain: str,
    retrieval_result: RetrievalResult,
    model_slot: str = "synthesis",
    config: dict | Any | None = None,
) -> SynthesisOutput:
    """
    Phase 1 stub implementation of the synthesis node.

    - Resolves model name from config (no network).
    - Produces deterministic placeholder content based on retrieval hits.
    - Content is constructed to pass structural_validate (CHUNK-1.2).
    - Returns fake but plausible token and latency numbers.
    """
    start = time.perf_counter()

    model_name = _resolve_model_name(model_slot, config)

    # Build deterministic stub content that satisfies L3a validation rules
    # (min length, section markers, no false success claims without substance)
    hit_summaries = []
    if retrieval_result.hits:
        for i, hit in enumerate(retrieval_result.hits[:3]):
            snippet = (hit.content or "")[:120].replace("\n", " ")
            hit_summaries.append(f"- Hit {i+1} (score={hit.score:.2f}, domain={hit.domain}): {snippet}...")

    hits_section = "\n".join(hit_summaries) if hit_summaries else "- No strong retrieval hits were available."

    content = f"""## Synthesis for query in domain '{domain}'

**Query:** {query}

**Retrieved Context Summary:**
{hits_section}

**Analysis (Stub):**
This is a deterministic Phase 1 stub response. In later phases a real model
call would be made using the resolved model slot. The response incorporates
key information from the top retrieval results above.

The current retrieval status was: {retrieval_result.status}
Max confidence from retrieval: {retrieval_result.max_confidence:.2f}

## Next Steps
- Review retrieved context
- Consider additional retrieval or clarification if confidence is low
"""

    # Ensure it passes our current structural_validate rules
    validation = structural_validate(content)
    if not validation.passed:
        # Make a minimal adjustment to satisfy validation if needed (still deterministic)
        content += "\n\n## Additional Context\nThis section ensures the required structural markers are present."

    latency_ms = int((time.perf_counter() - start) * 1000) + 12  # small fake latency

    # Plausible fake token counts for a stub
    token_in = 180 + len(query) // 4 + sum(len(h.content or "") for h in retrieval_result.hits[:3]) // 10
    token_out = len(content) // 4

    return SynthesisOutput(
        content=content,
        model_slot=model_slot,
        model_name=model_name,
        token_count_in=token_in,
        token_count_out=token_out,
        latency_ms=latency_ms,
    )
