# Conversation Ingestion

AIP supports importing external conversation transcripts into the
knowledge substrate, where they become first-class searchable artifacts
with full provenance tracking.

## Supported Formats

| Format | Extension | Description |
|--------|-----------|-------------|
| ChatGPT Export JSON | `.json` | Standard `conversations.json` export from ChatGPT |
| Markdown Transcript | `.md` | `**Role**: content` format with `# Title` headers |
| Plain Text Transcript | `.txt` | `Role: content` or `[Role] content` format |

## CLI Usage

```bash
# Import a single file (format auto-detected from extension)
aip ingest file conversations.json

# Import with explicit format
aip ingest file chat.md --format markdown

# Import with a custom domain tag
aip ingest file meeting.txt --domain meetings

# Import into a specific project
aip ingest file meeting.txt --project my_project

# Import all files in a directory
aip ingest directory ./transcripts/ --domain imported

# Recurse into subdirectories
aip ingest directory ./exports/ --recursive
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--project` | (none) | Project name to associate ingested conversations with |
| `--domain` | (none) | Domain tag for the ingested conversations |
| `--format` | (auto-detected) | Explicit format override (`chatgpt_json`, `markdown`, `plaintext`) |
| `--recursive` | `false` | Recurse into subdirectories (directory mode only) |
| `--db-path` | `db/state.db` | SQLite database path |

## How It Works

The ingestion pipeline follows these steps:

1. **Parse**: The source file is parsed into an `ImportedConversation`
   containing `ConversationTurn` objects with role, content, and
   optional timestamps.

2. **Persist**: The raw conversation is stored as an artifact in the
   `ArtifactStore` with provenance metadata including source format,
   source file path, import timestamp, and turn count.

3. **Chunk**: The conversation content is split into overlapping chunks
   (default 500 characters with 50-character overlap) that respect
   paragraph and sentence boundaries.

4. **Index (Lexical)**: Each chunk is indexed into the FTS5 lexical
   store for full-text search. This always succeeds even without
   an embedding provider.

5. **Index (Vector)**: When an `EmbeddingProvider` is available, each
   chunk is also embedded and upserted into the `VectorStore` for
   semantic search. Without an embedding provider, this step is
   gracefully skipped.

6. **Record Event**: An ingestion event is written to the `EventStore`
   recording the artifact ID, source format, and indexing status.

## No Parallel Storage

Ingested conversations use the **same stores** as all other AIP content:
- `ArtifactStore` for raw conversation persistence
- `LexicalStore` (FTS5) for text search
- `VectorStore` for semantic search
- `EventStore` for audit trail

There is no separate conversation store. Imported conversations are
artifacts with `artifact_type: "conversation"` in their metadata.

## Provenance

Every ingested conversation carries provenance metadata:

```json
{
  "artifact_type": "conversation",
  "source_format": "chatgpt_json",
  "source_file": "conversations.json",
  "imported_at": "2024-01-15T10:30:00+00:00",
  "turn_count": 42,
  "title": "Machine Learning Discussion",
  "domain": "imported",
  "conversation_id": "chatgpt:abc123-def456"
}
```

Chunk IDs follow the pattern `chunk:{conversation_id}:{index}`,
making it easy to trace any search hit back to its source conversation.

## Search Integration

Once ingested, conversation chunks are discoverable through the
existing retrieval infrastructure:

- **FTS5 search**: `LexicalStore.search("quantum computing")`
- **Vector search**: `VectorStore.retrieve(query_vector)`
- **Hybrid retrieval**: `retrieve_for_synthesis()` with four-factor
  reranking (semantic, recency, authority, frequency)

Imported conversations enter at `APPROVED` state since they are
already human-authored content. They participate in the same
retrieval pipeline as canonical artifacts and compiled knowledge.

## Programmatic Usage

```python
from aip.orchestration.ingestion import ingest_file, ingest_conversation
from aip.orchestration.ingestion.pipeline import create_ingestion_stores

# Create stores (encapsulates adapter-layer imports)
stores = await create_ingestion_stores("db/state.db")

# Ingest a file (auto-detects format)
results = await ingest_file(
    path="chat.json",
    artifact_store=stores.artifact_store,
    lexical_store=stores.lexical_store,
    vector_store=stores.vector_store,
    event_store=stores.event_store,
    domain="imported",
)

# Or ingest a pre-parsed conversation
from aip.orchestration.ingestion.parsers.plaintext import parse_plaintext_transcript

conversation = parse_plaintext_transcript(text, source_file="notes.txt")
result = await ingest_conversation(
    conversation=conversation,
    artifact_store=stores.artifact_store,
    lexical_store=stores.lexical_store,
)

await stores.close()
```

## Chunking Strategy

The chunker uses a three-level strategy:

1. **Paragraph boundaries** (double newlines)
2. **Sentence boundaries** (period, exclamation, question mark)
3. **Hard character split** with overlap (last resort)

This ensures that chunks are as natural as possible while staying
under the 500-character default limit. Conversation role labels
(`[USER]`, `[ASSISTANT]`) are preserved within chunks so that
search results retain conversational context.
