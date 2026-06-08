"""Sexton Actor — background maintenance worker (ADR-011).

Vigil cycle (300s cadence): tagging → embedding → wiki → graph → classification.
Uses the "sexton" model slot for LLM calls. All DB access goes through
async store methods — no raw sqlite3 in async context.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import (
    EmbeddingProvider,
    EventStore,
    VectorStore,
)
from aip.foundation.schemas import SextonConfig
from aip.logging import get_logger

log = get_logger(__name__)


def _extract_json_array(text: str) -> list:
    """Robustly extract a JSON array from an LLM response.

    Free models (e.g. Gemma) often return conversational text or
    markdown-wrapped JSON instead of raw JSON. This function:
      1. Strips markdown code fences (```json ... ```)
      2. Finds the first '[' and last ']' to extract a JSON array
      3. Falls back to returning [] on any failure
    """
    if not text or not text.strip():
        return []

    s = text.strip()

    # Strip markdown code fences
    if s.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1:]
        # Remove closing fence
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()

    # Try direct parse first
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: find the first '[' and last ']' and try to parse that substring
    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(s[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return []


class Sexton:
    """Sexton — background maintenance actor (ADR-011).

    Runs the vigil cycle every 300s performing: tagging, wiki compilation,
    graph extraction, embedding, and failure classification.

    Constructor accepts optional stores for graceful operation when those
    components are not yet configured. Uses the "sexton" model slot
    (free mid-tier model) for LLM calls.
    """

    # Wiki generation thresholds (same as Beast originals)
    _WIKI_EXCLUDED_DOMAINS = frozenset({"quarantine", "unclassified"})
    _WIKI_WORD_THRESHOLD = 200_000

    def __init__(
        self,
        sexton_provider: Any = None,  # ModelSlotResolver for "sexton" slot, or None
        corpus_turn_store: Any = None,  # CorpusTurnStore for turn tagging/embedding
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
        artifact_store: Any = None,
        ecs_store: Any = None,
        event_store: EventStore | None = None,
        trace_store: Any = None,  # TraceStore for failure classification
        lexical_store: Any = None,  # for sampling chunks (optional)
        config: SextonConfig | None = None,
        alert_manager: Any = None,  # Sprint 5.25: AlertManager for batch reduction alerts
    ) -> None:
        self._sexton_provider = sexton_provider
        self._corpus_turns = corpus_turn_store
        self._embed = embedding_provider
        self._vector = vector_store
        self._artifacts = artifact_store
        self._ecs = ecs_store
        self._events = event_store
        self._trace_store = trace_store
        self._lexical = lexical_store
        self._config = config or SextonConfig()
        self._last_cycle_time: float | None = None

        # LLM batching telemetry — accumulates across cycles
        self._batch_telemetry = {
            "total_batch_extractions": 0,   # number of batch-mode LLM calls
            "total_per_turn_extractions": 0, # number of per-turn LLM calls
            "total_turns_via_batch": 0,      # turns processed in batch mode
            "total_turns_via_per_turn": 0,   # turns processed in per-turn mode
            "total_estimated_tokens_saved": 0, # approximate tokens saved by batching
        }

        # Sprint 5.23: Batch size auto-tuning state
        # Tracks parse success/failure rate for recent batches
        self._batch_parse_results: list[bool] = []  # True=success, False=failure
        self._current_batch_size: int = self._config.graph_extraction_batch_size
        self._auto_tune_adjustments: list[dict] = []  # History of auto-tune adjustments

        # Sprint 5.25: Per-batch telemetry for operator visibility
        # Each entry records a single batch's outcome with detail.
        self._per_batch_telemetry: list[dict] = []  # Last 30 batches
        self._total_batch_successes = 0
        self._total_batch_failures = 0

        # Sprint 5.25: Alert manager for batch reduction notifications
        self._alert_manager = alert_manager

        # Import existing Sexton for failure classification delegation
        self._failure_classifier: Any = None
        if trace_store is not None:
            try:
                from aip.orchestration.sexton.sexton import Sexton as FailureSexton
                self._failure_classifier = FailureSexton(
                    config=self._config,
                    model_resolver=sexton_provider,
                    trace_store=trace_store,
                    event_store=event_store,
                )
            except Exception as exc:
                log.warning("sexton_failure_classifier_init_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Main vigil cycle (ADR-011)
    # ------------------------------------------------------------------

    async def run_cycle(self) -> dict:
        """Execute the full Sexton vigil cycle per ADR-011.

        Operations run in this order:
        1. Turn tagging (limit=200)
        2. Embedding pass (limit=50)
        3. Wiki generation (max_per_cycle=3)
        4. Graph extraction — only if bridge-tagged turns exist
        5. Failure classification

        Returns a summary dict with results from each operation.
        """
        cycle_start = time.monotonic()

        await self._emit_event(
            event_type="sexton_vigil_start",
            artifact_id="system",
            metadata={"timestamp": datetime.now(timezone.utc).isoformat()},
        )

        # 1. Turn tagging
        tagging_result = await self._run_turn_tagging(limit=200)

        # 2. Embedding pass
        embedding_result = await self._run_embedding_pass(limit=50)

        # 3. Wiki generation
        wiki_result = await self._run_wiki_generation(max_per_cycle=3)

        # 4. Graph extraction (only if bridge-tagged turns exist)
        graph_result: dict = {"skipped": "no_bridge_tagged_turns"}
        if await self._has_bridge_tagged_turns():
            graph_result = await self._run_graph_extraction()

        # 5. Failure classification
        classification_result = await self._run_failure_classification()

        elapsed = time.monotonic() - cycle_start
        self._last_cycle_time = time.time()

        summary = {
            "tagging": tagging_result,
            "embedding": embedding_result,
            "wiki": wiki_result,
            "graph": graph_result,
            "classification": classification_result,
            "batch_telemetry": dict(self._batch_telemetry),
            "cycle_elapsed_seconds": round(elapsed, 3),
            "last_cycle_time": self._last_cycle_time,
        }

        await self._emit_event(
            event_type="sexton_vigil_complete",
            artifact_id="system",
            metadata=summary,
        )

        log.info(
            "sexton_vigil_complete",
            elapsed=round(elapsed, 3),
            tagging_tagged=tagging_result.get("turns_tagged", 0),
            embedding_done=embedding_result.get("embedded", 0),
            wiki_generated=wiki_result.get("domains_generated", 0),
            graph_entities=graph_result.get("entities_created", 0),
            classification_count=classification_result.get("classified", 0),
        )

        return summary

    # ------------------------------------------------------------------
    # Bridge-tagged turn detection
    # ------------------------------------------------------------------

    async def _has_bridge_tagged_turns(self) -> bool:
        """Check if any turns with bridge tags exist in the corpus."""
        if self._corpus_turns is None:
            return False
        try:
            if hasattr(self._corpus_turns, "has_bridge_tagged_turns"):
                return await self._corpus_turns.has_bridge_tagged_turns()
            return False
        except Exception as exc:
            log.warning("sexton_bridge_check_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Turn tagging (batch LLM over domain registry) — from Beast
    # ------------------------------------------------------------------

    async def _run_turn_tagging(self, limit: int = 200, retag: bool = False) -> dict:
        """LLM-powered batch tagging of CorpusTurn rows using the domain registry.

        - Loads registry ONCE per invocation (not per batch).
        - Processes in batches of exactly 8 turns per LLM call to sexton_provider.
        - Truncates context: user[:400], assistant[:600], thinking[:300].
        - Validates all outputs against registry (primary + domains + bridges).
        - Falls back to "unclassified" (with low confidence) on bad primary/JSON.
        - Collects proposals and writes them as GENERATED artifacts (sacred gate).
        - Emits sexton_tagging_complete event with stats + domain_distribution.
        - Never aborts whole session on one bad batch.
        - Cap enforced at 200 for background cycle.
        """
        if self._corpus_turns is None or self._sexton_provider is None:
            return {"skipped": "missing_provider_or_corpus_turn_store"}

        # Load registry once (authoritative; Sexton never invents domains)
        try:
            from .domain_registry import load_registry
            registry = load_registry("docs/beast_domain_registry_v1.md")
        except FileNotFoundError as exc:
            log.warning("sexton_tagging_skipped_no_registry", path="docs/beast_domain_registry_v1.md", error=str(exc))
            return {"skipped": "registry_not_found"}
        except Exception as exc:
            log.warning("sexton_tagging_registry_load_failed", error=str(exc))
            return {"skipped": "registry_load_error", "error": str(exc)}

        approved_domains = registry.get_domain_ids()
        approved_bridges = registry.get_approved_bridge_tags()

        # Build prompt fragments (exact format mandated)
        domain_list = "\n".join(f"  - {d}" for d in approved_domains)
        bridge_list = "\n".join(f"  - {b}" for b in approved_bridges)

        system_prompt = f"""You are AIP Sexton, corpus maintenance actor for a
sovereign knowledge engine. Your job is to classify conversation turns
into knowledge domains and score their importance.

You will receive a batch of conversation turns. For each turn, return
a JSON object with exactly these fields:
  turn_id: string (copy exactly from input)
  primary_domain: string (exactly one domain_id from the approved list)
  domains: array of strings (all relevant domain_ids, may include primary)
  tags: array of strings (3-8 specific topic tags, lowercase snake_case)
  importance: float 0.0-1.0 (see scoring rules)
  bridges: array of strings (approved connector tags only, may be empty)
  beast_confidence: float 0.0-1.0 (your confidence in this classification)

APPROVED DOMAINS:
{domain_list}

APPROVED BRIDGE TAGS:
{bridge_list}

IMPORTANCE SCORING RULES:
0.9-1.0: Decision recorded, conclusion reached, original framework
         developed, manuscript section completed
0.7-0.8: Substantive analysis, design discussion, theological exegesis,
         research finding, problem solved
0.5-0.6: Working through a problem, iterating on document, exploring idea
0.3-0.4: Short exchanges, translations, logistics with some content value
0.1-0.2: Greetings, very short exchanges, administrative queries
0.0:     Quarantine — no retrieval value (see quarantine rules)

THINKING BLOCK BONUS: If thinking_text is non-empty, add 0.1 to
importance score (cap at 1.0). Extended thinking signals complex,
considered reasoning worth preserving.

QUARANTINE RULES — assign primary_domain "quarantine" only when ALL:
  1. user_text < 15 words with no substantive content
  2. assistant_text < 50 words
  3. No domain keywords match
  4. Total word_count < 30
NEVER quarantine turns with thinking_text, decisions, frameworks,
documents referenced, or substantive answers.

UNCLASSIFIED: If confidence < 0.4 for best domain match, use
primary_domain "unclassified". This signals DEFINER review needed.

PROPOSAL TRIGGER: If you see a pattern across this batch that
genuinely doesn't fit any approved domain, add a "proposal" field
to ONE turn in the batch (not all) with:
  proposal: {{
    type: "domain" or "connector",
    proposed_id: "snake_case_name",
    description: "2-3 sentences",
    rationale: "why it doesn't fit existing domains"
  }}
Only propose when you have seen 3+ turns in this batch with the pattern.

Respond ONLY with a JSON array. No preamble. No explanation outside JSON.
Example response structure:
[
  {{
    "turn_id": "abc123def456",
    "primary_domain": "nbcm",
    "domains": ["nbcm", "theology_research"],
    "tags": ["null_boundary", "timelessness", "photon_t0"],
    "importance": 0.8,
    "bridges": ["nbcm->theology_research"],
    "beast_confidence": 0.85
  }}
]
"""

        # Gather turns (untagged + optional retag)
        to_tag: list[Any] = []
        try:
            unt = await self._corpus_turns.get_untagged_turns(limit=limit)
            to_tag.extend(unt)
            if retag and len(to_tag) < limit:
                more_limit = max(0, limit - len(to_tag))
                if more_limit > 0:
                    ret = await self._corpus_turns.get_turns_for_retagging(max_tagging_version=10, limit=more_limit)
                    to_tag.extend(ret)
        except Exception as exc:
            log.error("sexton_get_turns_failed", error=str(exc))
            return {"turns_tagged": 0, "turns_failed": 0, "proposals_filed": 0, "error": str(exc)}

        if not to_tag:
            return {"turns_tagged": 0, "turns_failed": 0, "proposals_filed": 0, "note": "nothing_to_tag"}

        BATCH_SIZE = 8
        total = len(to_tag)
        tagged = 0
        failed = 0
        proposals: list[dict] = []
        domain_counts: dict[str, int] = {}
        importance_sum = 0.0
        importance_count = 0

        for b_start in range(0, total, BATCH_SIZE):
            batch = to_tag[b_start : b_start + BATCH_SIZE]
            batch_idx = (b_start // BATCH_SIZE) + 1

            # Build user prompt with mandated truncations
            blocks = []
            for j, turn in enumerate(batch):
                uid = getattr(turn, "turn_id", "")
                cname = getattr(turn, "conversation_name", "")
                u = (getattr(turn, "user_text", "") or "")[:400]
                a = (getattr(turn, "assistant_text", "") or "")[:600]
                th_raw = getattr(turn, "thinking_text", "") or ""
                th = (th_raw[:300] if th_raw else "(none)")
                wc = getattr(turn, "word_count", 0)
                blk = (
                    f"--- TURN {j+1} ---\n"
                    f"turn_id: {uid}\n"
                    f"conversation: {cname}\n"
                    f"user: {u}\n"
                    f"assistant: {a}\n"
                    f"thinking: {th}\n"
                    f"word_count: {wc}\n"
                    f"---"
                )
                blocks.append(blk)

            user_prompt = f"Tag the following {len(batch)} conversation turns:\n\n" + "\n".join(blocks)

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if batch_idx % 5 == 1 or batch_idx == (total + BATCH_SIZE - 1) // BATCH_SIZE:
                log.info("sexton_tagging_progress", batch=batch_idx, of=(total + BATCH_SIZE - 1) // BATCH_SIZE, turns=f"{b_start+1}-{min(b_start+BATCH_SIZE, total)}/{total}")

            try:
                llm_result = await self._sexton_provider.call("sexton", messages)
                content = (llm_result or {}).get("content", "").strip()
                parsed = _extract_json_array(content)
            except Exception as exc:
                log.warning("sexton_tagging_batch_parse_failed", batch=batch_idx, turn_ids=[getattr(t, "turn_id", "?") for t in batch], error=str(exc))
                for t in batch:
                    try:
                        await self._corpus_turns.update_beast_tags(
                            getattr(t, "turn_id", ""),
                            [], "unclassified", [], 0.0, [], 0.0
                        )
                    except Exception:
                        pass
                failed += len(batch)
                continue

            # Process each returned item
            batch_turn_ids = {getattr(t, "turn_id", ""): t for t in batch}
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                tid = item.get("turn_id")
                if not tid or tid not in batch_turn_ids:
                    continue

                # Validate / sanitize
                primary = (item.get("primary_domain") or "unclassified").strip()
                if not registry.is_approved_domain(primary) and primary not in ("unclassified", "quarantine"):
                    primary = "unclassified"
                    item_conf = 0.3
                else:
                    item_conf = item.get("beast_confidence", 0.0)

                doms = [d for d in (item.get("domains") or []) if isinstance(d, str) and registry.is_approved_domain(d)]
                if primary not in doms and primary in ("unclassified", "quarantine") or registry.is_approved_domain(primary):
                    if primary not in ("unclassified", "quarantine") and primary not in doms:
                        doms = [primary] + doms

                tgs = [str(t).lower().replace(" ", "_")[:64] for t in (item.get("tags") or []) if isinstance(t, (str, int, float))][:8]
                if not tgs:
                    tgs = ["unclassified"]

                try:
                    imp = max(0.0, min(1.0, float(item.get("importance", 0.0))))
                except Exception:
                    imp = 0.0
                # thinking bonus if present on original turn
                try:
                    th = getattr(batch_turn_ids[tid], "thinking_text", "") or ""
                    if th.strip() and imp < 1.0:
                        imp = min(1.0, imp + 0.1)
                except Exception:
                    pass

                brs = []
                for b in (item.get("bridges") or []):
                    bs = str(b)
                    if registry.is_approved_bridge(bs):
                        brs.append(bs)
                    else:
                        log.warning("sexton_tagging_dropped_unapproved_bridge", bridge=bs, turn=tid)

                try:
                    bconf = max(0.0, min(1.0, float(item_conf or 0.0)))
                except Exception:
                    bconf = 0.0

                # Collect proposal if present
                prop = item.get("proposal")
                if isinstance(prop, dict) and prop.get("proposed_id"):
                    ptype = prop.get("type", "domain")
                    proposals.append({
                        "type": ptype,
                        "proposed_id": prop.get("proposed_id"),
                        "description": prop.get("description", ""),
                        "rationale": prop.get("rationale", ""),
                        "evidence_turn_ids": [tid],
                    })

                # Persist
                try:
                    await self._corpus_turns.update_beast_tags(
                        tid, doms, primary, tgs, imp, brs, bconf
                    )
                    tagged += 1
                    domain_counts[primary] = domain_counts.get(primary, 0) + 1
                    importance_sum += imp
                    importance_count += 1
                except Exception as exc:
                    log.warning("sexton_update_tags_failed", turn_id=tid, error=str(exc))
                    failed += 1

            # Rate-limit: pause between sequential LLM calls to stay under
            # free-model per-minute token limits (429 prevention)
            await asyncio.sleep(5)

        # Write proposals as GENERATED artifacts
        proposals_filed = 0
        ts = datetime.now(timezone.utc).isoformat()
        short_ts = ts.replace(":", "").replace("-", "")[:15]
        for p in proposals:
            try:
                pid = p.get("proposed_id", "discovered")
                ptype = p.get("type", "domain")
                aid = f"sexton:proposal:{ptype}:{pid}:{short_ts}"
                content = json.dumps({
                    "proposed_id": pid,
                    "proposal_type": ptype,
                    "description": p.get("description", ""),
                    "rationale": p.get("rationale", ""),
                    "evidence_turn_ids": p.get("evidence_turn_ids", [])[:5],
                    "suggested_connectors": p.get("suggested_connectors", []),
                }, ensure_ascii=False)
                meta = {
                    "artifact_type": "sexton_domain_proposal",
                    "proposal_type": ptype,
                    "proposed_id": pid,
                    "domain": "corpus",
                    "generated_at": ts,
                    "sexton_cycle": int(time.time()),
                }
                if self._artifacts is not None:
                    await self._artifacts.write(aid, content, meta)
                    if self._ecs is not None:
                        try:
                            await self._ecs.transition(
                                artifact_id=aid,
                                from_state=None,
                                to_state="GENERATED",
                                actor="sexton",
                                reason="Sexton domain/connector proposal — pending DEFINER review",
                            )
                        except Exception as e:
                            log.warning("sexton_proposal_ecs_failed", aid=aid, error=str(e))
                    proposals_filed += 1
            except Exception as exc:
                log.warning("sexton_proposal_write_failed", error=str(exc))

        avg_imp = (importance_sum / importance_count) if importance_count > 0 else 0.0

        # Emit tagging complete event
        await self._emit_event(
            event_type="sexton_tagging_complete",
            artifact_id="system",
            metadata={
                "turns_tagged": tagged,
                "turns_failed": failed,
                "proposals_filed": proposals_filed,
                "domain_distribution": domain_counts,
                "avg_importance": round(avg_imp, 4),
                "cycle": int(time.time()),
                "limit": limit,
                "retag": retag,
            },
        )

        log.info(
            "sexton_tagging_complete",
            tagged=tagged,
            failed=failed,
            proposals=proposals_filed,
            top_domains=sorted(domain_counts.items(), key=lambda kv: -kv[1])[:5],
        )

        return {
            "turns_tagged": tagged,
            "turns_failed": failed,
            "proposals_filed": proposals_filed,
            "domain_distribution": domain_counts,
            "avg_importance": round(avg_imp, 4),
        }

    # ------------------------------------------------------------------
    # Embedding pass — from Beast
    # ------------------------------------------------------------------

    async def _run_embedding_pass(self, limit: int = 50, reembed: bool = False) -> dict:
        """Embed corpus turns' searchable_text into vector store, keyed by turn_id.

        Batch size 32 for efficiency (cheaper than chat).
        Truncates text to 8000 chars.
        Sets embedded=1 on success in corpus_turns.
        """
        if self._corpus_turns is None or self._embed is None:
            return {"skipped": "missing_corpus_turn_store_or_embedding_provider"}

        # Query unembedded
        try:
            if reembed:
                turns = await self._corpus_turns.get_unembedded_turns(limit=limit)
                to_embed = [(t.turn_id, t.searchable_text) for t in turns]
            else:
                turns = await self._corpus_turns.get_unembedded_turns(limit=limit)
                to_embed = [(t.turn_id, t.searchable_text) for t in turns]
        except Exception as exc:
            log.error("sexton_get_unembedded_failed", error=str(exc))
            return {"embedded": 0, "failed": 0, "skipped": 0, "error": str(exc)}

        if not to_embed:
            return {"embedded": 0, "failed": 0, "skipped": 0, "note": "nothing_to_embed"}

        BATCH_SIZE = 32
        total = len(to_embed)
        embedded = 0
        failed = 0
        skipped = 0

        for b_start in range(0, total, BATCH_SIZE):
            batch = to_embed[b_start : b_start + BATCH_SIZE]
            batch_idx = (b_start // BATCH_SIZE) + 1

            if batch_idx % 5 == 1 or batch_idx == (total + BATCH_SIZE - 1) // BATCH_SIZE:
                log.info("sexton_embedding_progress", batch=batch_idx, of=(total + BATCH_SIZE - 1) // BATCH_SIZE, turns=f"{b_start+1}-{min(b_start+BATCH_SIZE, total)}/{total}")

            for tid, stext in batch:
                try:
                    text = (stext or "")[:8000]
                    if not text.strip():
                        skipped += 1
                        continue
                    vec = await self._embed.embed(text)
                    if not vec or len(vec) == 0:
                        failed += 1
                        continue
                    # Store keyed by turn_id
                    if self._vector is not None:
                        await self._vector.upsert(
                            id=tid,
                            embedding=vec,
                            content=text[:500],  # snippet
                            metadata={"type": "corpus_turn", "turn_id": tid},
                            domain=None,
                        )
                    # Mark embedded via CorpusTurnStore async method
                    try:
                        if hasattr(self._corpus_turns, "mark_embedded"):
                            await self._corpus_turns.mark_embedded(tid)
                    except Exception:
                        pass
                    embedded += 1
                except Exception as exc:
                    log.warning("sexton_embedding_failed", turn_id=tid, error=str(exc))
                    failed += 1

        log.info("sexton_embedding_complete", embedded=embedded, failed=failed, skipped=skipped)

        return {
            "embedded": embedded,
            "failed": failed,
            "skipped": skipped,
        }

    # ------------------------------------------------------------------
    # Wiki generation (domain-level articles from tagged corpus) — from Beast
    # ------------------------------------------------------------------

    async def _run_wiki_generation(
        self,
        force_domains: list[str] | None = None,
        max_per_cycle: int = 3,
    ) -> dict:
        """Generate domain wiki articles from tagged corpus turns.

        For each active domain: checks whether generation is needed
        (no wiki exists OR >200k new words since last wiki), then calls
        sexton_provider with the wiki prompt, and writes a GENERATED artifact.

        max_per_cycle: cap for background cycle use (3 per ADR-011). CLI uses higher.
        force_domains: if set, only generate for those domains (ignores threshold).
        """
        if self._sexton_provider is None or self._artifacts is None or self._corpus_turns is None:
            return {"skipped": "missing_provider_or_artifact_store_or_corpus_turns"}

        try:
            from .domain_registry import load_registry
            registry = load_registry("docs/beast_domain_registry_v1.md")
        except Exception as exc:
            log.warning("sexton_wiki_skipped_no_registry", error=str(exc))
            return {"skipped": "registry_not_found", "error": str(exc)}

        active_domains = [
            d for d in registry.get_domain_ids()
            if d not in self._WIKI_EXCLUDED_DOMAINS
        ]
        if force_domains is not None:
            active_domains = [d for d in active_domains if d in force_domains]

        db_path = getattr(self._corpus_turns, "_db_path", None)
        generated = 0
        skipped = 0
        errors = 0
        domains_generated: list[str] = []
        domains_skipped: list[str] = []
        cycle_num = int(time.time())

        for domain_id in active_domains:
            if force_domains is None and generated >= max_per_cycle:
                break

            domain_entry = registry.get_domain(domain_id)
            if domain_entry is None:
                continue

            force_this = force_domains is not None

            needs_gen, last_wiki_ts = await self._wiki_needs_generation(domain_id, force=force_this)
            if not needs_gen:
                skipped += 1
                domains_skipped.append(domain_id)
                continue

            domain_data = await self._get_wiki_domain_data(domain_id, db_path, last_wiki_ts)

            if domain_data["total_turns"] == 0:
                skipped += 1
                domains_skipped.append(domain_id)
                log.info("sexton_wiki_skipped_no_turns", domain=domain_id)
                continue

            try:
                wiki_content = await self._call_sexton_for_wiki(domain_id, domain_entry, domain_data)
            except Exception as exc:
                log.error("sexton_wiki_llm_failed", domain=domain_id, error=str(exc))
                errors += 1
                continue

            if not wiki_content:
                errors += 1
                continue

            aid = await self._write_wiki_artifact(domain_id, domain_entry, wiki_content, domain_data, cycle_num)
            if aid:
                generated += 1
                domains_generated.append(domain_id)
                wc = len(wiki_content.split())
                log.info("sexton_wiki_generated", domain=domain_id, word_count=wc, artifact=aid)
            else:
                errors += 1

            # Rate-limit: pause between sequential LLM calls to stay under
            # free-model per-minute token limits (429 prevention)
            await asyncio.sleep(5)

        await self._emit_event(
            event_type="sexton_wiki_cycle_complete",
            artifact_id="system",
            metadata={
                "domains_generated": domains_generated,
                "domains_skipped": domains_skipped,
                "domains_generated_count": generated,
                "domains_skipped_count": skipped,
                "errors": errors,
                "cycle": cycle_num,
            },
        )

        return {
            "domains_generated": generated,
            "domains_skipped": skipped,
            "errors": errors,
            "domains_generated_list": domains_generated,
        }

    async def _wiki_needs_generation(self, domain_id: str, force: bool = False) -> tuple[bool, str | None]:
        """Return (needs_generation, last_wiki_created_at_or_None)."""
        last_wiki_ts: str | None = None
        try:
            arts = await self._artifacts.list_artifacts_by_metadata(
                key="artifact_type", value="sexton_wiki", limit=200
            )
            domain_arts = [
                a for a in arts
                if (a.get("metadata", {}) or {}).get("domain") == domain_id
            ]
            if domain_arts:
                domain_arts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
                last_wiki_ts = domain_arts[0].get("created_at", "")
        except Exception as exc:
            log.warning("sexton_wiki_lookup_failed", domain=domain_id, error=str(exc))

        if force:
            return True, last_wiki_ts

        if last_wiki_ts is None:
            return True, None

        # Count new words since last wiki
        try:
            if hasattr(self._corpus_turns, "count_domain_words_since"):
                new_words = await self._corpus_turns.count_domain_words_since(domain_id, last_wiki_ts)
                return new_words >= self._WIKI_WORD_THRESHOLD, last_wiki_ts
            return False, last_wiki_ts
        except Exception as exc:
            log.warning("sexton_wiki_word_count_failed", domain=domain_id, error=str(exc))
            return False, last_wiki_ts

    async def _get_wiki_domain_data(
        self, domain_id: str, db_path: str | None, last_wiki_ts: str | None
    ) -> dict:
        """Gather domain statistics and sample turns for wiki generation."""
        try:
            if hasattr(self._corpus_turns, "get_domain_stats"):
                return await self._corpus_turns.get_domain_stats(domain_id)
        except Exception as exc:
            log.warning("sexton_wiki_domain_data_failed", domain=domain_id, error=str(exc))
        return {
            "total_turns": 0,
            "avg_importance": 0.0,
            "top_tags": [],
            "bridge_connectors": [],
            "sample_turns": [],
            "max_tagging_version": 0,
        }

    async def _call_sexton_for_wiki(self, domain_id: str, domain_entry: Any, domain_data: dict) -> str:
        """Call sexton_provider with wiki generation prompt. Returns article text."""
        system_prompt = f"""You are AIP Sexton, corpus maintenance actor for a
sovereign knowledge engine. You are generating a domain wiki article
from corpus turns written by B. Moses Jorgensen (the DEFINER).

The DEFINER is a cross-domain researcher and systems builder with deep
expertise in: chemistry (30+ years), data analytics, AI methodology
(AI Poiesis), New Covenant theology (30+ years independent study),
and systems thinking across technical, theological, and policy domains.

You are writing a wiki article for the domain: {domain_id}
Domain description: {domain_entry.description}

The article must have exactly this structure:

## Overview
[3-5 sentences. Dense, assumes full domain knowledge. Written for
LLM injection — maximum information per token. Captures the DEFINER's
actual current position and framework, not generic domain description.
Use the DEFINER's own terminology from the corpus turns.]

## Key Concepts
[The 5-8 most important concepts, frameworks, or positions in this
domain as they appear in the corpus. Each concept gets 2-4 sentences.
Use the DEFINER's actual terminology. If terminology has evolved
(e.g., "record formation" replaced "observation collapse"), use
the current term and note the evolution.]

## Current State
[Where the work in this domain currently stands. Decisions made,
conclusions reached, open questions. What has been resolved vs
what is still being worked out. Be specific — cite the actual
state of manuscripts, experiments, projects, or frameworks.]

## Cross-Domain Connections
[How this domain connects to other domains in the corpus. Use the
approved bridge vocabulary. Explain WHY the connection matters,
not just that it exists. 3-6 connections maximum.]

## Evolution
[How the thinking in this domain has changed over the corpus period.
What was the starting position, what changed, why. This section
reveals intellectual development — it is among the most valuable
sections for the DEFINER to review.]

## Key Turns
[List 3-5 turn_ids that are most representative or important for
this domain. Format: turn_id | brief description of what makes it
significant]

## Open Questions
[What remains unresolved, actively debated, or in progress.
3-5 specific questions the DEFINER appears to be working on.
These should spark further thinking when the DEFINER reads the article.]

CRITICAL CONSTRAINTS:
- Write from corpus evidence only. Do not add knowledge from your
  training data that is not reflected in the provided turns.
- Use the DEFINER's own language and frameworks.
- The Overview section will be injected into AI chat sessions —
  it must be maximally informative in minimum space.
- Do not hallucinate project names, people, or positions not
  evidenced in the provided turns.
- If a section cannot be written from the evidence (e.g., insufficient
  turns for Evolution), write: "[Insufficient corpus evidence for
  this section — more turns needed]"
"""

        turns_text = ""
        for i, t in enumerate(domain_data.get("sample_turns", []), 1):
            turns_text += (
                f"--- TURN {i} ---\n"
                f"turn_id: {t['turn_id']}\n"
                f"importance: {t['importance']}\n"
                f"tags: {t['tags']}\n"
                f"bridges: {t['bridges']}\n"
                f"user: {t['user_text']}\n"
                f"assistant: {t['assistant_text']}\n"
                f"---\n\n"
            )

        user_prompt = (
            f"Generate a wiki article for domain: {domain_id}\n\n"
            f"Domain statistics:\n"
            f"- Total turns: {domain_data['total_turns']}\n"
            f"- Average importance: {domain_data['avg_importance']:.2f}\n"
            f"- Top tags: {domain_data['top_tags']}\n"
            f"- Active connectors: {domain_data['bridge_connectors']}\n\n"
            f"Sample turns (highest importance, use as evidence base):\n"
            f"{turns_text}\n"
            f"Generate the complete wiki article now."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        result = await self._sexton_provider.call("sexton", messages)
        content = (result or {}).get("content") or ""
        return content.strip()

    async def _write_wiki_artifact(
        self,
        domain_id: str,
        domain_entry: Any,
        wiki_content: str,
        domain_data: dict,
        cycle_num: int,
    ) -> str:
        """Write wiki article as GENERATED artifact. Returns artifact_id."""
        if self._artifacts is None:
            return ""
        try:
            ts = datetime.now(timezone.utc).isoformat()
            short_ts = ts.replace(":", "").replace("-", "")[:15]
            aid = f"sexton:wiki:{domain_id}:{short_ts}"

            # Extract overview text (between ## Overview and next ##)
            overview_text = ""
            try:
                lines = wiki_content.split("\n")
                in_overview = False
                overview_lines: list[str] = []
                for line in lines:
                    if line.strip() == "## Overview":
                        in_overview = True
                        continue
                    if in_overview and line.startswith("## "):
                        break
                    if in_overview and line.strip():
                        overview_lines.append(line.strip())
                overview_text = " ".join(overview_lines).strip()
            except Exception:
                pass

            word_count = len(wiki_content.split())
            meta = {
                "artifact_type": "sexton_wiki",
                "domain": domain_id,
                "domain_display": getattr(domain_entry, "domain_id", domain_id),
                "generated_at": ts,
                "turns_sampled": len(domain_data.get("sample_turns", [])),
                "total_domain_turns": domain_data.get("total_turns", 0),
                "avg_importance": domain_data.get("avg_importance", 0.0),
                "top_tags": domain_data.get("top_tags", []),
                "overview_text": overview_text,
                "word_count": word_count,
                "tagging_version_at_generation": domain_data.get("max_tagging_version", 0),
                "sexton_cycle": cycle_num,
            }

            await self._artifacts.write(aid, wiki_content, meta)

            if self._ecs is not None:
                try:
                    await self._ecs.transition(
                        artifact_id=aid,
                        from_state=None,
                        to_state="GENERATED",
                        actor="sexton",
                        reason="Sexton domain wiki — pending DEFINER review",
                    )
                except Exception as e:
                    log.warning("sexton_wiki_ecs_failed", aid=aid, error=str(e))
            return aid
        except Exception as exc:
            log.error("sexton_wiki_write_failed", domain=domain_id, error=str(exc))
            return ""

    # ------------------------------------------------------------------
    # Graph extraction (entity/relationship extraction via Sexton LLM)
    # ------------------------------------------------------------------

    async def _run_graph_extraction(self, limit: int = 50) -> dict:
        """Extract entities and relationships from high-importance corpus turns.

        Processes turns with importance > 0.7, tagging_version > 0, not yet
        graph-extracted. Creates/updates GraphNode and GraphEdge records.
        Tracks processed turns in graph_extraction_log table.
        Cap at `limit` turns per invocation.

        When ``graph_extraction_batch_enabled`` is True and
        ``graph_extraction_batch_size > 1``, multiple turns are sent in a
        single LLM call (similar to tagging batches). The batch response
        must include ``turn_id`` in each element to map results back.
        Falls back to per-turn processing if batch parsing fails.
        Rate-limit sleep is between batches, not between individual turns.
        """
        if self._sexton_provider is None or self._corpus_turns is None:
            return {"skipped": "missing_provider_or_corpus_turns"}

        db_path = getattr(self._corpus_turns, "_db_path", None)
        if not db_path:
            return {"skipped": "no_db_path"}

        try:
            from aip.adapter.graph_store import GraphStore, GraphNode, GraphEdge
            from aip.adapter.entity_alias_loader import EntityAliasRegistry
        except Exception as exc:
            log.warning("sexton_graph_import_failed", error=str(exc))
            return {"skipped": "import_error", "error": str(exc)}

        graph_store = GraphStore(db_path)
        await graph_store.initialize()
        try:
            registry = EntityAliasRegistry("docs/entity_aliases.md")
        except Exception as exc:
            log.warning("sexton_alias_registry_failed", error=str(exc))
            registry = None

        # Build compact alias list for prompt
        alias_lines = []
        if registry is not None:
            for cn in registry.all_canonical_names()[:40]:
                entry = registry.get_entry(cn)
                if entry:
                    aliases_str = ", ".join(entry.aliases[:3]) if entry.aliases else ""
                    alias_lines.append(f"  {cn} ({entry.entity_type}){': ' + aliases_str if aliases_str else ''}")
        alias_registry_compact = "\n".join(alias_lines)

        # Get unextracted high-importance turns
        turns = await graph_store.get_unextracted_high_importance_turns(min_importance=0.7, limit=limit)

        if not turns:
            return {"turns_processed": 0, "entities_created": 0, "relationships_created": 0, "note": "nothing_to_extract"}

        # Determine batch vs per-turn mode
        batch_enabled = (
            self._config.graph_extraction_batch_enabled
            and self._current_batch_size > 1
        )
        # Use the (potentially auto-tuned) current batch size
        batch_size = self._current_batch_size if batch_enabled else 1

        if batch_enabled:
            log.info(
                "sexton_graph_extraction_batch_mode",
                batch_size=batch_size,
                total_turns=len(turns),
            )

        # Single-turn system prompt (used for per-turn and as base for batch)
        _single_system_prompt = f"""You are AIP Sexton extracting entities and relationships
from a conversation turn for a personal knowledge graph.

CANONICAL ENTITY TYPES:
- PERSON: Named individuals
- PROJECT: Named projects, products, technologies, devices
- CONCEPT: Named theoretical frameworks, principles, methodologies
- PLACE: Named locations
- ORGANIZATION: Named organizations, institutions, companies
- MANUSCRIPT: Named documents, papers, books

CANONICAL RELATIONSHIP TYPES:
- CONNECTS: Two concepts or domains connect intellectually
- WORKS_ON: A person works on a project/manuscript
- FUNDED_BY: A project is funded by a mechanism
- AUTHORED: A person authored a manuscript/document
- LOCATED_IN: An entity is located in a place
- RELATES_TO: Generic relationship when type unclear

ENTITY ALIAS TABLE (resolve mentions to these canonical names):
{alias_registry_compact}

RULES:
- Only extract named entities that appear explicitly in the text
- Resolve aliases to canonical names before outputting
- Do NOT extract generic concepts (e.g., "physics", "theology")
  only named specific ones (e.g., "NBCM", "New Covenant Displaced")
- Do NOT extract the DEFINER himself as an entity
- Minimum confidence 0.5 to include
- Return ONLY valid JSON array, no preamble

Output format:
[
  {{
    "entity_type": "CONCEPT",
    "canonical_name": "NBCM",
    "confidence": 0.95
  }},
  {{
    "relationship_type": "CONNECTS",
    "source": "NBCM",
    "target": "EZ Water",
    "confidence": 0.8
  }}
]
"""

        # Batch system prompt — adds turn_id requirement
        _batch_system_prompt = f"""You are AIP Sexton extracting entities and relationships
from multiple conversation turns for a personal knowledge graph.

CANONICAL ENTITY TYPES:
- PERSON: Named individuals
- PROJECT: Named projects, products, technologies, devices
- CONCEPT: Named theoretical frameworks, principles, methodologies
- PLACE: Named locations
- ORGANIZATION: Named organizations, institutions, companies
- MANUSCRIPT: Named documents, papers, books

CANONICAL RELATIONSHIP TYPES:
- CONNECTS: Two concepts or domains connect intellectually
- WORKS_ON: A person works on a project/manuscript
- FUNDED_BY: A project is funded by a mechanism
- AUTHORED: A person authored a manuscript/document
- LOCATED_IN: An entity is located in a place
- RELATES_TO: Generic relationship when type unclear

ENTITY ALIAS TABLE (resolve mentions to these canonical names):
{alias_registry_compact}

RULES:
- Only extract named entities that appear explicitly in the text
- Resolve aliases to canonical names before outputting
- Do NOT extract generic concepts (e.g., "physics", "theology")
  only named specific ones (e.g., "NBCM", "New Covenant Displaced")
- Do NOT extract the DEFINER himself as an entity
- Minimum confidence 0.5 to include
- Return ONLY valid JSON array, no preamble

You will receive multiple turns. For EACH entity or relationship extracted,
include a "turn_id" field that exactly matches the turn_id from the input.
This is critical for mapping results back to the correct turn.

Output format:
[
  {{
    "turn_id": "abc123",
    "entity_type": "CONCEPT",
    "canonical_name": "NBCM",
    "confidence": 0.95
  }},
  {{
    "turn_id": "abc123",
    "relationship_type": "CONNECTS",
    "source": "NBCM",
    "target": "EZ Water",
    "confidence": 0.8
  }},
  {{
    "turn_id": "def456",
    "entity_type": "PROJECT",
    "canonical_name": "Spectrometer",
    "confidence": 0.9
  }}
]
"""

        total_processed = 0
        total_entities = 0
        total_relationships = 0

        # ---- Helper: process parsed items for a single turn ----
        async def _process_turn_items(
            turn_id: str,
            primary_domain: str,
            items: list[dict],
        ) -> tuple[int, int]:
            """Process parsed entities/relationships for one turn. Returns (entities, rels)."""
            entities_this_turn = 0
            rels_this_turn = 0
            batch_nodes: list[GraphNode] = []
            batch_edges: list[GraphEdge] = []
            implied_nodes: dict[str, GraphNode] = {}

            for item in items:
                if not isinstance(item, dict):
                    continue

                if "entity_type" in item:
                    raw_name = (item.get("canonical_name") or "").strip()
                    if not raw_name:
                        continue
                    resolved = registry.resolve(raw_name) if registry else raw_name
                    if not resolved:
                        continue
                    node_id = resolved.lower().replace(" ", "_")
                    entity_type = item.get("entity_type", "CONCEPT")
                    confidence = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
                    if confidence < 0.5:
                        continue

                    existing = await graph_store.get_node(node_id)
                    if existing is None:
                        domain_hint = registry.get_domain(resolved) if registry else primary_domain or None
                        et = registry.get_entity_type(resolved) if registry else entity_type
                        batch_nodes.append(GraphNode(
                            id=node_id,
                            entity_type=et,
                            canonical_name=resolved,
                            domain=domain_hint,
                            confidence=confidence,
                            source="sexton_extraction",
                        ))
                        entities_this_turn += 1

                elif "relationship_type" in item:
                    src_raw = (item.get("source") or "").strip()
                    tgt_raw = (item.get("target") or "").strip()
                    rel_type = (item.get("relationship_type") or "RELATES_TO").strip()
                    confidence = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
                    if confidence < 0.5 or not src_raw or not tgt_raw:
                        continue

                    src_resolved = registry.resolve(src_raw) if registry else src_raw
                    tgt_resolved = registry.resolve(tgt_raw) if registry else tgt_raw
                    src_id = src_resolved.lower().replace(" ", "_")
                    tgt_id = tgt_resolved.lower().replace(" ", "_")

                    # Track implied nodes for batch creation
                    for nid, nname in ((src_id, src_resolved), (tgt_id, tgt_resolved)):
                        if nid not in implied_nodes:
                            implied_nodes[nid] = GraphNode(
                                id=nid,
                                entity_type="CONCEPT",
                                canonical_name=nname,
                                domain=primary_domain or None,
                                confidence=0.6,
                                source="sexton_extraction",
                            )

                    edge_id = f"{src_id}__{rel_type}__{tgt_id}"
                    batch_edges.append(GraphEdge(
                        id=edge_id,
                        source_id=src_id,
                        target_id=tgt_id,
                        relationship_type=rel_type,
                        confidence=confidence,
                        evidence_turn_ids=[turn_id],
                        weight=1.0,
                    ))
                    rels_this_turn += 1

            # Batch upsert: merge implied nodes (from edges) with explicit nodes
            all_new_nodes = {n.id: n for n in batch_nodes}
            for nid, node in implied_nodes.items():
                if nid not in all_new_nodes:
                    # Only add if not already in the DB
                    if await graph_store.get_node(nid) is None:
                        all_new_nodes[nid] = node

            if all_new_nodes:
                await graph_store.upsert_nodes_batch(list(all_new_nodes.values()))
            if batch_edges:
                await graph_store.upsert_edges_batch(batch_edges)

            await graph_store.log_turn_extracted(turn_id, entities_this_turn, rels_this_turn)
            return entities_this_turn, rels_this_turn

        # ---- Helper: extract a single turn via LLM (per-turn mode / fallback) ----
        async def _extract_single_turn(turn: dict) -> tuple[int, int]:
            """LLM call for one turn. Returns (entities, rels)."""
            turn_id = turn["turn_id"]
            user_text = (turn.get("user_text") or "")[:400]
            assistant_text = (turn.get("assistant_text") or "")[:600]
            primary_domain = turn.get("primary_domain", "")
            importance = turn.get("importance", 0.0)

            user_prompt = (
                f"Extract entities and relationships from this turn:\n\n"
                f"turn_id: {turn_id}\n"
                f"domain: {primary_domain}\n"
                f"importance: {importance}\n"
                f"user: {user_text}\n"
                f"assistant: {assistant_text}\n"
            )

            messages = [
                {"role": "system", "content": _single_system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            try:
                result = await self._sexton_provider.call("sexton", messages)
                content = (result or {}).get("content") or ""
                parsed = _extract_json_array(content.strip())
            except Exception as exc:
                log.warning("sexton_graph_extraction_parse_failed", turn_id=turn_id, error=str(exc))
                await graph_store.log_turn_extracted(turn_id, 0, 0)
                return 0, 0

            return await _process_turn_items(turn_id, primary_domain, parsed)

        if not batch_enabled:
            # ---- Per-turn mode (default, identical to original behavior) ----
            for turn in turns:
                turn_id = turn["turn_id"]
                ents, rels = await _extract_single_turn(turn)
                total_processed += 1
                total_entities += ents
                total_relationships += rels
                self._batch_telemetry["total_per_turn_extractions"] += 1
                self._batch_telemetry["total_turns_via_per_turn"] += 1

                # Rate-limit: pause between sequential LLM calls
                await asyncio.sleep(5)

        else:
            # ---- Batch mode ----
            for b_start in range(0, len(turns), batch_size):
                batch = turns[b_start : b_start + batch_size]
                batch_idx = (b_start // batch_size) + 1
                total_batches = (len(turns) + batch_size - 1) // batch_size

                # Build batch user prompt with all turns in the batch
                turn_blocks = []
                for j, turn in enumerate(batch):
                    tid = turn["turn_id"]
                    u = (turn.get("user_text") or "")[:400]
                    a = (turn.get("assistant_text") or "")[:600]
                    dom = turn.get("primary_domain", "")
                    imp = turn.get("importance", 0.0)
                    blk = (
                        f"--- TURN {j+1} ---\n"
                        f"turn_id: {tid}\n"
                        f"domain: {dom}\n"
                        f"importance: {imp}\n"
                        f"user: {u}\n"
                        f"assistant: {a}\n"
                        f"---"
                    )
                    turn_blocks.append(blk)

                user_prompt = (
                    f"Extract entities and relationships from the following "
                    f"{len(batch)} conversation turns:\n\n"
                    + "\n".join(turn_blocks)
                )

                messages = [
                    {"role": "system", "content": _batch_system_prompt},
                    {"role": "user", "content": user_prompt},
                ]

                if batch_idx % 5 == 1 or batch_idx == total_batches:
                    log.info(
                        "sexton_graph_extraction_batch_progress",
                        batch=batch_idx,
                        of=total_batches,
                        turns=f"{b_start+1}-{min(b_start+batch_size, len(turns))}/{len(turns)}",
                    )

                try:
                    result = await self._sexton_provider.call("sexton", messages)
                    content = (result or {}).get("content") or ""
                    parsed = _extract_json_array(content.strip())
                except Exception as exc:
                    log.warning(
                        "sexton_graph_extraction_batch_parse_failed",
                        batch=batch_idx,
                        turn_ids=[t["turn_id"] for t in batch],
                        error=str(exc),
                    )
                    # Track batch parse failure for auto-tuning (Sprint 5.23)
                    self._batch_parse_results.append(False)
                    # Sprint 5.25: Per-batch telemetry — record failure
                    self._total_batch_failures += 1
                    self._per_batch_telemetry.append({
                        "batch_idx": batch_idx,
                        "batch_size": len(batch),
                        "success": False,
                        "error_reason": str(exc)[:200],
                        "turn_ids": [t["turn_id"] for t in batch][:5],
                        "fell_back_to_per_turn": True,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    if len(self._per_batch_telemetry) > 30:
                        self._per_batch_telemetry = self._per_batch_telemetry[-30:]
                    # Fallback: process each turn individually
                    for turn in batch:
                        ents, rels = await _extract_single_turn(turn)
                        total_processed += 1
                        total_entities += ents
                        total_relationships += rels
                        self._batch_telemetry["total_per_turn_extractions"] += 1
                        self._batch_telemetry["total_turns_via_per_turn"] += 1
                    # Rate-limit after fallback processing
                    await asyncio.sleep(5)
                    continue

                # Group parsed items by turn_id
                batch_turn_ids = {t["turn_id"] for t in batch}
                items_by_turn: dict[str, list[dict]] = {tid: [] for tid in batch_turn_ids}
                has_turn_id_mapping = False

                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    tid = item.get("turn_id")
                    if tid and tid in batch_turn_ids:
                        has_turn_id_mapping = True
                        items_by_turn[tid].append(item)

                # Track batch parse success for auto-tuning (Sprint 5.23)
                self._batch_parse_results.append(True)
                # Sprint 5.25: Per-batch telemetry — record success
                self._total_batch_successes += 1
                self._per_batch_telemetry.append({
                    "batch_idx": batch_idx,
                    "batch_size": len(batch),
                    "success": True,
                    "items_extracted": len(parsed),
                    "has_turn_id_mapping": has_turn_id_mapping,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                if len(self._per_batch_telemetry) > 30:
                    self._per_batch_telemetry = self._per_batch_telemetry[-30:]

                if not has_turn_id_mapping:
                    # Batch response didn't include turn_id — fall back to per-turn
                    log.warning(
                        "sexton_graph_extraction_batch_no_turn_id",
                        batch=batch_idx,
                        note="falling_back_to_per_turn",
                    )
                    for turn in batch:
                        ents, rels = await _extract_single_turn(turn)
                        total_processed += 1
                        total_entities += ents
                        total_relationships += rels
                        self._batch_telemetry["total_per_turn_extractions"] += 1
                        self._batch_telemetry["total_turns_via_per_turn"] += 1
                    await asyncio.sleep(5)
                    continue

                # Process each turn's items
                batch_turns_count = 0
                for turn in batch:
                    tid = turn["turn_id"]
                    primary_domain = turn.get("primary_domain", "")
                    ents, rels = await _process_turn_items(
                        tid, primary_domain, items_by_turn.get(tid, [])
                    )
                    total_processed += 1
                    batch_turns_count += 1
                    total_entities += ents
                    total_relationships += rels

                # Update batch telemetry
                self._batch_telemetry["total_batch_extractions"] += 1
                self._batch_telemetry["total_turns_via_batch"] += batch_turns_count
                # Estimate token savings: each per-turn call has ~800 tokens of
                # system prompt overhead. Batching N turns into 1 call saves
                # (N-1) * 800 prompt tokens.
                tokens_saved = max(0, (batch_turns_count - 1)) * 800
                self._batch_telemetry["total_estimated_tokens_saved"] += tokens_saved

                # Rate-limit: pause between batches (not between individual turns)
                await asyncio.sleep(5)

        log.info(
            "sexton_graph_extraction_complete",
            turns=total_processed,
            entities=total_entities,
            relationships=total_relationships,
            batch_mode=batch_enabled,
            batch_size=batch_size if batch_enabled else 1,
        )

        # Sprint 5.23: Auto-tune batch size based on parse success rate
        auto_tune_result = self._auto_tune_batch_size()

        await self._emit_event(
            event_type="sexton_graph_extraction_complete",
            artifact_id="system",
            metadata={
                "turns_processed": total_processed,
                "entities_created": total_entities,
                "relationships_created": total_relationships,
                "batch_mode": batch_enabled,
                "batch_size": batch_size,
            },
        )

        return {
            "turns_processed": total_processed,
            "entities_created": total_entities,
            "relationships_created": total_relationships,
            "batch_mode": batch_enabled,
            "batch_size": batch_size,
            "batch_telemetry": dict(self._batch_telemetry),
            "auto_tune": auto_tune_result,
        }

    # ------------------------------------------------------------------
    # Batch size auto-tuning (Sprint 5.23)
    # ------------------------------------------------------------------

    def _auto_tune_batch_size(self) -> dict:
        """Adjust batch_size based on parse success/failure rate.

        Conservative auto-tuning logic:
        - Computes failure rate over the last N batch results
        - If failure rate > decrease_threshold (0.3): reduce batch_size by 1
        - If failure rate < increase_threshold (0.1): increase batch_size by 1
        - Always stays within [batch_size_min, batch_size_max] bounds
        - Only runs when graph_extraction_batch_auto_tune_enabled is True

        Returns a dict with the auto-tune decision and current state.
        """
        result = {
            "enabled": self._config.graph_extraction_batch_auto_tune_enabled,
            "previous_batch_size": self._current_batch_size,
            "new_batch_size": self._current_batch_size,
            "failure_rate": 0.0,
            "action": "none",
            "window_size": 0,
        }

        if not self._config.graph_extraction_batch_auto_tune_enabled:
            return result

        window = self._config.graph_extraction_auto_tune_window
        recent = self._batch_parse_results[-window:] if self._batch_parse_results else []
        result["window_size"] = len(recent)

        if not recent:
            return result

        # Compute failure rate
        failures = sum(1 for success in recent if not success)
        failure_rate = failures / len(recent)
        result["failure_rate"] = round(failure_rate, 3)

        old_size = self._current_batch_size
        min_size = self._config.graph_extraction_batch_size_min
        max_size = self._config.graph_extraction_batch_size_max

        if failure_rate > self._config.graph_extraction_auto_tune_decrease_threshold:
            # High failure rate — decrease batch size
            new_size = max(min_size, old_size - 1)
            if new_size < old_size:
                self._current_batch_size = new_size
                result["new_batch_size"] = new_size
                result["action"] = "decreased"
                adjustment = {
                    "action": "decreased",
                    "from": old_size,
                    "to": new_size,
                    "failure_rate": round(failure_rate, 3),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._auto_tune_adjustments.append(adjustment)
                log.info(
                    "sexton_batch_auto_tune_decreased",
                    from_size=old_size,
                    to_size=new_size,
                    failure_rate=round(failure_rate, 3),
                )
                # Sprint 5.25: Alert on batch size reduction
                if self._alert_manager is not None:
                    try:
                        from aip.adapter.alerting import Alert
                        self._alert_manager.send_alert(Alert(
                            alert_type="batch_reduction",
                            severity="warning",
                            subject="graph_extraction_batch_size",
                            message=(
                                f"Graph extraction batch size reduced from {old_size} to {new_size} "
                                f"due to high parse failure rate ({failure_rate:.1%} over last "
                                f"{len(recent)} batches). Operators should investigate LLM parse errors."
                            ),
                            data={
                                "old_batch_size": old_size,
                                "new_batch_size": new_size,
                                "failure_rate": round(failure_rate, 3),
                                "window_size": len(recent),
                                "min_batch_size": min_size,
                                "max_batch_size": max_size,
                            },
                        ))
                    except Exception as exc:
                        log.warning("sexton_alert_failed", error=str(exc))

        elif failure_rate < self._config.graph_extraction_auto_tune_increase_threshold:
            # Low failure rate — can increase batch size
            new_size = min(max_size, old_size + 1)
            if new_size > old_size:
                self._current_batch_size = new_size
                result["new_batch_size"] = new_size
                result["action"] = "increased"
                adjustment = {
                    "action": "increased",
                    "from": old_size,
                    "to": new_size,
                    "failure_rate": round(failure_rate, 3),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._auto_tune_adjustments.append(adjustment)
                log.info(
                    "sexton_batch_auto_tune_increased",
                    from_size=old_size,
                    to_size=new_size,
                    failure_rate=round(failure_rate, 3),
                )
        else:
            result["action"] = "no_change"

        # Trim parse results history to prevent unbounded growth
        if len(self._batch_parse_results) > 50:
            self._batch_parse_results = self._batch_parse_results[-50:]

        # Trim adjustment history
        if len(self._auto_tune_adjustments) > 20:
            self._auto_tune_adjustments = self._auto_tune_adjustments[-20:]

        return result

    # ------------------------------------------------------------------
    # Failure classification — delegates to existing Sexton
    # ------------------------------------------------------------------

    async def _run_failure_classification(self) -> dict:
        """Run failure classification via the existing Sexton failure classifier.

        Delegates to aip.orchestration.sexton.sexton.Sexton which handles
        deterministic Appendix E taxonomy rules and optional LLM-assisted
        classification via the "sexton" model slot.
        """
        if self._failure_classifier is None:
            return {"skipped": "no_trace_store_or_classifier_not_initialized"}

        try:
            classified = await self._failure_classifier.classify_recent_failures(
                limit=self._config.classification_batch_size
            )
            return {
                "classified": len(classified),
                "items": classified[:10],  # summary only
            }
        except Exception as exc:
            log.error("sexton_failure_classification_failed", error=str(exc))
            return {"classified": 0, "error": str(exc)}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _emit_event(
        self,
        event_type: str,
        artifact_id: str,
        metadata: dict | None = None,
    ) -> None:
        """Write an event to the EventStore if wired.

        Silently no-ops if no event store is configured. Never raises.
        """
        if self._events is None:
            return
        try:
            await self._events.write_event(
                event_type=event_type,
                actor="sexton",
                artifact_id=artifact_id,
                from_state=None,
                to_state=None,
                **(metadata or {}),
            )
        except Exception as exc:
            log.warning("event_emit_failed", event_type=event_type, error=str(exc))
