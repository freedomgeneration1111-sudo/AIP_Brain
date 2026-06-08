# ADR-001: Turn-Level Corpus Ingestion

**Date:** 2026-06-04
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

---

## Context

AIP needed a corpus ingestion strategy for multi-platform AI conversation exports
(Claude, ChatGPT, DeepSeek, GLM, Gemini, xAI). The initial implementation
chunked conversations into fixed-size text windows and indexed them into FTS5.

## Problem

Fixed-size chunking produced epistemically incoherent retrieval. A chunk might
start mid-sentence, split a thought in two, or combine unrelated content from
adjacent messages. Retrieving a chunk gave no context about what was asked or why
the response was given. Extended thinking blocks (Claude's reasoning traces) were
discarded entirely.

## Decision

The atomic unit of the AIP corpus is the **conversation turn** — one user message
plus the immediately following assistant response, stored together as a CorpusTurn.

Key properties of this decision:
- Both sides of the exchange are preserved (user_text + assistant_text)
- Extended thinking blocks are preserved in a separate field (thinking_text)
  and included in searchable_text, making Beast's reasoning retrievable
- Turn identity is deterministic: sha256(conversation_id + turn_index)[:16]
  making re-ingestion idempotent (duplicates skipped, not duplicated)
- Beast-assigned metadata (domains, tags, importance, bridges) is stored
  on the turn, not derived at query time

## Alternatives Considered

**Fixed-size chunking** — rejected because it splits thoughts mid-sentence and
loses the question-answer relationship that gives context to retrieved content.

**Conversation-level ingestion** — rejected because a single conversation can
span five domains and 50,000 words. Retrieving the whole conversation is too
coarse; Beast cannot tag it meaningfully at conversation level.

**Semantic chunking** — considered but deferred. Semantic chunking would split
long assistant responses at topic boundaries. This is a potential future upgrade
to the parser, but turn-level is the right foundation.

## Consequences

- Each conversation export produces N turns where N = number of user+assistant pairs
- 452 Claude conversations → 2,691 turns
- Turns with empty assistant_text are valid (conversation ended on user message)
- Turns with non-empty thinking_text (1,743 of 2,691) carry extended reasoning
- The CorpusTurn schema is the foundation for wiki, graph, and vector embeddings
- Re-ingesting an export is safe (idempotent by turn_id)

## Related

- ADR-002: Beast Domain Registry
- ADR-003: Multi-Corpus Architecture
- src/aip/foundation/schemas/corpus_turn.py
- src/aip/adapter/corpus_turn_store.py
- src/aip/orchestration/ingestion/parsers/claude_parser.py
