# AIP Loom Architecture Decisions

**User**: Let's discuss the architecture decisions we've made for AIP Loom so far.

**Assistant**: Sure! Here's a summary of the key architecture decisions we've documented for AIP Loom:

1. **Three-layer architecture**: foundation (pure validation), orchestration (business logic), adapter (external interfaces)
2. **Protocol-based dependency injection**: All store interactions go through Protocol interfaces
3. **SQLite-first storage**: All adapter stores use aiosqlite for async-safe database access
4. **ECS lifecycle**: Every artifact follows SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED
5. **DEFINER sovereignty**: No artifact may bypass DEFINER gates

**User**: What about artifact storage specifically?

**Assistant**: For artifact storage, we decided on:

- **VersionedArtifactStore**: Every write creates a new version; no version is ever overwritten
- **CanonicalStore**: Separate store for DEFINER-approved canonical artifacts
- **Separation of generated and canonical**: Generated content goes through review before being promoted to canonical
- **Provenance metadata**: Every artifact carries source_ids, source_types, and session_id for traceability
- **FTS5 indexing**: All content is indexed into a lexical store for full-text search

**User**: And the model provider approach?

**Assistant**: The model provider setup:

- **ModelSlotResolver**: Routes requests to the right model based on slot (synthesis, evaluation, sexton, embedding)
- **Environment variable configuration**: AIP_SYNTHESIS_BASE_URL, AIP_SYNTHESIS_MODEL, etc.
- **Ollama support**: Local models via Ollama for offline-first operation
- **OpenAI-compatible**: Any OpenAI-compatible API endpoint works
- **No fake responses**: The system never silently returns placeholder model responses

**User**: What's the review and approval process?

**Assistant**: The review workflow:

- **GENERATED state**: Artifacts from `aip ask --save-artifact` start in GENERATED state
- **DEFINER review**: The human operator uses `aip review list/show/sources` to inspect
- **Approve path**: GENERATED → REVIEWED → APPROVED (two ECS transitions)
- **Reject path**: GENERATED → REJECTED (artifact preserved, not deleted)
- **Needs-revision**: Verdict stored as event, artifact stays in current state
- **Export gate**: Only APPROVED artifacts export without --force

**User**: How does the ingestion pipeline work?

**Assistant**: Ingestion follows this flow:

- **Format detection**: Auto-detect ChatGPT JSON, markdown, or plaintext
- **Conversation parsing**: Parse into ImportedConversation with turns
- **Artifact storage**: Store raw conversation as artifact with provenance metadata
- **Chunking**: Split into 500-character chunks with 50-character overlap
- **Lexical indexing**: Index chunks into FTS5 for full-text search
- **Vector indexing**: Optional vector indexing when embedding provider available
- **Event recording**: Log ingestion event in EventStore for audit trail
