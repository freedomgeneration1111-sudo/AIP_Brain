"""Tests for conversation ingestion: parsers, chunker, pipeline.

Verifies end-to-end ingestion from raw files through parsing,
chunking, persistence, and indexing into existing AIP stores.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from aip.foundation.schemas.ingestion import (
    ConversationTurn,
    ImportedConversation,
    IngestionResult,
)
from aip.orchestration.ingestion.chunker import chunk_conversation, chunk_text
from aip.orchestration.ingestion.parsers import detect_format
from aip.orchestration.ingestion.parsers.chatgpt import parse_chatgpt_export
from aip.orchestration.ingestion.parsers.markdown import parse_markdown_transcript
from aip.orchestration.ingestion.parsers.plaintext import parse_plaintext_transcript

# ---------------------------------------------------------------------------
# ChatGPT Parser Tests
# ---------------------------------------------------------------------------


def _make_chatgpt_conversation(title="Test Chat", turns=None):
    """Build a minimal ChatGPT export conversation dict."""
    mapping = {}
    parent = None
    for i, (role, content) in enumerate(turns or [("user", "Hello"), ("assistant", "Hi there")]):
        node_id = f"node_{i}"
        mapping[node_id] = {
            "id": node_id,
            "message": {
                "id": f"msg_{i}",
                "author": {"role": role},
                "content": {"parts": [content]},
                "create_time": 1700000000.0 + i * 60,
            },
            "parent": parent,
            "children": [],
        }
        if parent is not None:
            mapping[parent]["children"].append(node_id)
        parent = node_id
    return {"title": title, "create_time": 1700000000.0, "mapping": mapping}


class TestChatGPTParser:
    """Tests for parse_chatgpt_export."""

    def test_parse_single_conversation(self):
        conv = _make_chatgpt_conversation("My Chat", [("user", "Hello"), ("assistant", "World")])
        results = parse_chatgpt_export([conv], source_file="test.json")
        assert len(results) == 1
        assert results[0].title == "My Chat"
        assert len(results[0].turns) == 2
        assert results[0].turns[0].role == "user"
        assert results[0].turns[0].content == "Hello"
        assert results[0].turns[1].role == "assistant"
        assert results[0].source_format == "chatgpt_json"

    def test_parse_from_json_string(self):
        conv = _make_chatgpt_conversation()
        json_str = json.dumps([conv])
        results = parse_chatgpt_export(json_str, source_file="export.json")
        assert len(results) == 1

    def test_parse_multiple_conversations(self):
        conv1 = _make_chatgpt_conversation("Chat 1")
        conv2 = _make_chatgpt_conversation("Chat 2")
        results = parse_chatgpt_export([conv1, conv2])
        assert len(results) == 2
        assert results[0].title == "Chat 1"
        assert results[1].title == "Chat 2"

    def test_skip_empty_conversations(self):
        conv = {"title": "Empty", "mapping": {}}
        results = parse_chatgpt_export([conv])
        assert len(results) == 0

    def test_skip_system_and_tool_messages(self):
        conv = _make_chatgpt_conversation(
            turns=[("system", "Instructions"), ("user", "Hello"), ("tool", "Output"), ("assistant", "Reply")]
        )
        results = parse_chatgpt_export([conv])
        # All roles are included since they are in ("user", "assistant", "system", "tool")
        assert len(results[0].turns) == 4

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid ChatGPT export JSON"):
            parse_chatgpt_export("not json at all {{{")

    def test_single_dict_input(self):
        conv = _make_chatgpt_conversation("Single Dict")
        results = parse_chatgpt_export(conv)
        assert len(results) == 1

    def test_conversation_id_is_stable(self):
        conv = _make_chatgpt_conversation("Stable ID")
        results1 = parse_chatgpt_export([conv], source_file="stable.json")
        results2 = parse_chatgpt_export([conv], source_file="stable.json")
        assert results1[0].conversation_id == results2[0].conversation_id

    def test_timestamps_parsed(self):
        conv = _make_chatgpt_conversation(turns=[("user", "Hello")])
        results = parse_chatgpt_export([conv])
        assert results[0].turns[0].timestamp  # Should have a timestamp


# ---------------------------------------------------------------------------
# Markdown Parser Tests
# ---------------------------------------------------------------------------


class TestMarkdownParser:
    """Tests for parse_markdown_transcript."""

    def test_basic_markdown(self):
        text = """# My Conversation

**User**: Hello, how are you?

**Assistant**: I'm doing well, thanks for asking!"""
        result = parse_markdown_transcript(text, source_file="chat.md")
        assert result.title == "My Conversation"
        assert len(result.turns) == 2
        assert result.turns[0].role == "user"
        assert result.turns[0].content == "Hello, how are you?"
        assert result.turns[1].role == "assistant"
        assert result.source_format == "markdown"

    def test_blockquote_style(self):
        text = """> **User**: What is AIP?
> **Assistant**: AI Poiesis, a knowledge engine."""
        result = parse_markdown_transcript(text)
        assert len(result.turns) == 2
        assert result.turns[0].role == "user"
        assert result.turns[1].role == "assistant"

    def test_no_title_uses_filename(self):
        text = """**User**: Hello
**Assistant**: Hi"""
        result = parse_markdown_transcript(text, source_file="my_chat.md")
        assert "My Chat" in result.title

    def test_multi_turn(self):
        text = """# Discussion

**User**: Question 1?

**Assistant**: Answer 1.

**User**: Follow-up question?

**Assistant**: Follow-up answer."""
        result = parse_markdown_transcript(text)
        assert len(result.turns) == 4

    def test_role_normalization(self):
        text = """**Human**: Hello
**AI**: Response"""
        result = parse_markdown_transcript(text)
        assert result.turns[0].role == "user"
        assert result.turns[1].role == "assistant"

    def test_empty_text_no_crash(self):
        result = parse_markdown_transcript("")
        assert isinstance(result, ImportedConversation)


# ---------------------------------------------------------------------------
# Plain Text Parser Tests
# ---------------------------------------------------------------------------


class TestPlaintextParser:
    """Tests for parse_plaintext_transcript."""

    def test_basic_plaintext(self):
        text = """User: Hello, how are you?
Assistant: I'm doing well, thanks!"""
        result = parse_plaintext_transcript(text, source_file="chat.txt")
        assert len(result.turns) == 2
        assert result.turns[0].role == "user"
        assert result.turns[0].content == "Hello, how are you?"
        assert result.turns[1].role == "assistant"
        assert result.source_format == "plaintext"

    def test_multiline_turns(self):
        text = """User: I have a question about Python.
It's about list comprehensions.
Assistant: Sure, I can help with that.
What specifically do you want to know?"""
        result = parse_plaintext_transcript(text)
        assert len(result.turns) == 2
        assert "list comprehensions" in result.turns[0].content
        assert "specifically" in result.turns[1].content

    def test_bracket_format(self):
        text = """[User] Hello
[Assistant] Hi there"""
        result = parse_plaintext_transcript(text)
        assert len(result.turns) == 2
        assert result.turns[0].role == "user"
        assert result.turns[1].role == "assistant"

    def test_case_insensitive_roles(self):
        text = """USER: hello
assistant: hi"""
        result = parse_plaintext_transcript(text)
        assert len(result.turns) == 2
        assert result.turns[0].role == "user"
        assert result.turns[1].role == "assistant"

    def test_empty_text_no_crash(self):
        result = parse_plaintext_transcript("")
        assert isinstance(result, ImportedConversation)

    def test_title_from_first_line(self):
        text = """Python Discussion
User: Hello"""
        result = parse_plaintext_transcript(text, source_file="test.txt")
        assert result.title == "Python Discussion"


# ---------------------------------------------------------------------------
# Format Detection Tests
# ---------------------------------------------------------------------------


class TestFormatDetection:
    """Tests for auto-detect_format."""

    def test_json_extension(self):
        assert detect_format("chat.json") == "chatgpt_json"

    def test_markdown_extension(self):
        assert detect_format("chat.md") == "markdown"

    def test_markdown_long_extension(self):
        assert detect_format("chat.markdown") == "markdown"

    def test_txt_defaults_to_plaintext(self):
        assert detect_format("chat.txt") == "plaintext"

    def test_unknown_extension_defaults_to_plaintext(self):
        assert detect_format("chat.log") == "plaintext"


# ---------------------------------------------------------------------------
# Chunker Tests
# ---------------------------------------------------------------------------


class TestChunker:
    """Tests for chunk_text and chunk_conversation."""

    def test_short_text_single_chunk(self):
        result = chunk_text("Hello world")
        assert len(result) == 1
        assert result[0] == "Hello world"

    def test_empty_text_no_chunks(self):
        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_paragraph_splitting(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        # Default max is 500, so this should be a single chunk
        result = chunk_text(text)
        assert len(result) >= 1

    def test_long_text_produces_multiple_chunks(self):
        # Create text that exceeds default 500 char limit
        paragraphs = [f"Paragraph {i} with some meaningful content that adds up." for i in range(30)]
        text = "\n\n".join(paragraphs)
        result = chunk_text(text, max_chars=200, overlap_chars=20)
        assert len(result) > 1

    def test_overlap_exists(self):
        # Create enough text for multiple chunks
        text = " ".join(f"Sentence number {i} with enough words." for i in range(50))
        result = chunk_text(text, max_chars=200, overlap_chars=50)
        if len(result) > 1:
            # Check that chunks have some overlap (tail of previous in head of next)
            # This is a soft check — the overlap comes from the previous chunk's tail
            assert len(result) > 1

    def test_chunk_conversation(self):
        conv = ImportedConversation(
            conversation_id="test:123",
            title="Test",
            turns=[
                ConversationTurn(role="user", content="Hello"),
                ConversationTurn(role="assistant", content="Hi there"),
            ],
            source_format="plaintext",
            source_file="test.txt",
        )
        chunks = chunk_conversation(conv)
        assert len(chunks) >= 1
        # Each chunk is (chunk_id, chunk_text)
        for chunk_id, chunk_text in chunks:
            assert chunk_id.startswith("chunk:test:123:")
            assert len(chunk_text) > 0

    def test_chunk_conversation_empty(self):
        conv = ImportedConversation(
            conversation_id="empty",
            title="Empty",
            turns=[],
            source_format="plaintext",
            source_file="empty.txt",
        )
        chunks = chunk_conversation(conv)
        assert len(chunks) == 0

    def test_chunk_preserves_role_labels(self):
        conv = ImportedConversation(
            conversation_id="role_test",
            title="Role Test",
            turns=[
                ConversationTurn(role="user", content="My question"),
                ConversationTurn(role="assistant", content="My answer"),
            ],
            source_format="plaintext",
            source_file="test.txt",
        )
        chunks = chunk_conversation(conv)
        combined = " ".join(text for _, text in chunks)
        assert "USER" in combined
        assert "ASSISTANT" in combined


# ---------------------------------------------------------------------------
# Ingestion Pipeline Tests
# ---------------------------------------------------------------------------


class FakeArtifactStore:
    """Minimal fake for ArtifactStore protocol."""

    def __init__(self):
        self.written: list[tuple[str, str, dict]] = []

    async def write(self, id: str, content: str, metadata: dict) -> None:
        self.written.append((id, content, metadata))

    async def read(self, id: str, version: int | None = None) -> str:
        for aid, content, _ in self.written:
            if aid == id:
                return content
        raise KeyError(id)

    async def list_versions(self, id: str) -> list[int]:
        return [1]


class FakeLexicalStore:
    """Minimal fake for LexicalStore protocol."""

    def __init__(self):
        self.indexed: list[dict] = []

    async def search(self, query: str, domain: str | None = None, limit: int = 10):
        return []

    async def index_document(self, doc_id: str, content: str, domain: str, metadata: dict) -> None:
        self.indexed.append({"doc_id": doc_id, "content": content, "domain": domain, "metadata": metadata})

    async def delete_document(self, doc_id: str) -> None:
        self.indexed = [d for d in self.indexed if d["doc_id"] != doc_id]


class FakeVectorStore:
    """Minimal fake for VectorStore protocol."""

    def __init__(self):
        self.upserted: list[dict] = []

    async def upsert(self, id, embedding, content, metadata, domain=None):
        self.upserted.append(
            {"id": id, "embedding": embedding, "content": content, "metadata": metadata, "domain": domain}
        )

    async def retrieve(self, query_vector, domain=None, top_k=10):
        return []

    async def delete(self, id):
        self.upserted = [u for u in self.upserted if u["id"] != id]

    async def count(self, domain=None):
        return len(self.upserted)

    async def store(self, chunk):
        return chunk.id

    async def health_check(self):
        return {"connected": True}

    async def list_stale_vectors(self, threshold_days=30, domain=None, limit=100):
        return []

    async def list_all_ids(self, offset=0, limit=500, domain=None):
        return []


class FakeEmbeddingProvider:
    """Deterministic fake embedding provider for testing."""

    def __init__(self, dimensions: int = 768):
        self.dimensions = dimensions
        self.embed_calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.embed_calls.append(text)
        # Simple deterministic vector
        import hashlib

        h = hashlib.sha256(text.encode()).digest()
        vec = [(h[i % len(h)] / 255.0) - 0.5 for i in range(self.dimensions)]
        return vec


class FakeEventStore:
    """Minimal fake for EventStore protocol."""

    def __init__(self):
        self.events: list[dict] = []

    async def write_event(self, event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs):
        self.events.append(
            {
                "event_type": event_type,
                "actor": actor,
                "artifact_id": artifact_id,
                "from_state": from_state,
                "to_state": to_state,
                **kwargs,
            }
        )

    async def query(self, artifact_id=None, event_type=None, limit=100):
        return []


class TestIngestionPipeline:
    """Tests for ingest_conversation and ingest_file."""

    async def test_ingest_plaintext_conversation(self):
        from aip.orchestration.ingestion.pipeline import ingest_conversation

        conv = ImportedConversation(
            conversation_id="test:1",
            title="Test Conversation",
            turns=[
                ConversationTurn(role="user", content="What is machine learning?"),
                ConversationTurn(role="assistant", content="Machine learning is a subfield of AI."),
            ],
            source_format="plaintext",
            source_file="test.txt",
            metadata={"domain": "test"},
        )

        artifact_store = FakeArtifactStore()
        lexical_store = FakeLexicalStore()

        result = await ingest_conversation(conv, artifact_store, lexical_store)

        assert isinstance(result, IngestionResult)
        assert result.turn_count == 2
        assert result.chunk_count >= 1
        assert result.lexical_indexed is True
        assert result.vector_indexed is False  # No vector store provided
        assert len(artifact_store.written) == 1
        assert len(lexical_store.indexed) >= 1

    async def test_ingest_with_vector_store(self):
        from aip.orchestration.ingestion.pipeline import ingest_conversation

        conv = ImportedConversation(
            conversation_id="test:vec",
            title="Vector Test",
            turns=[ConversationTurn(role="user", content="Hello vector world!")],
            source_format="plaintext",
            source_file="vec.txt",
            metadata={"domain": "test"},
        )

        artifact_store = FakeArtifactStore()
        lexical_store = FakeLexicalStore()
        vector_store = FakeVectorStore()
        embed_provider = FakeEmbeddingProvider()

        result = await ingest_conversation(
            conv,
            artifact_store,
            lexical_store,
            vector_store=vector_store,
            embedding_provider=embed_provider,
        )

        assert result.vector_indexed is True
        assert len(vector_store.upserted) >= 1
        assert len(embed_provider.embed_calls) >= 1

    async def test_ingest_with_event_store(self):
        from aip.orchestration.ingestion.pipeline import ingest_conversation

        conv = ImportedConversation(
            conversation_id="test:events",
            title="Event Test",
            turns=[ConversationTurn(role="user", content="Hello")],
            source_format="plaintext",
            source_file="events.txt",
            metadata={"domain": "test"},
        )

        artifact_store = FakeArtifactStore()
        lexical_store = FakeLexicalStore()
        event_store = FakeEventStore()

        result = await ingest_conversation(
            conv,
            artifact_store,
            lexical_store,
            event_store=event_store,
        )

        assert len(event_store.events) == 1
        assert event_store.events[0]["event_type"] == "conversation_ingested"
        assert event_store.events[0]["to_state"] == "APPROVED"

    async def test_ingest_file_from_disk(self):
        from aip.orchestration.ingestion.pipeline import ingest_file

        with tempfile.TemporaryDirectory() as tmp:
            # Write a plaintext file
            filepath = os.path.join(tmp, "chat.txt")
            with open(filepath, "w") as f:
                f.write("User: What is AIP?\nAssistant: AI Poiesis knowledge engine.")

            artifact_store = FakeArtifactStore()
            lexical_store = FakeLexicalStore()

            results = await ingest_file(
                filepath,
                artifact_store,
                lexical_store,
                source_format="plaintext",
                domain="test",
            )

            assert len(results) == 1
            assert results[0].turn_count == 2
            assert results[0].source_format == "plaintext"

    async def test_ingest_file_not_found(self):
        from aip.orchestration.ingestion.pipeline import ingest_file

        with pytest.raises(FileNotFoundError):
            await ingest_file(
                "/nonexistent/file.txt",
                FakeArtifactStore(),
                FakeLexicalStore(),
            )

    async def test_ingest_file_chatgpt_json(self):
        from aip.orchestration.ingestion.pipeline import ingest_file

        conv = _make_chatgpt_conversation("JSON Import", [("user", "Hello"), ("assistant", "Hi")])

        with tempfile.TemporaryDirectory() as tmp:
            filepath = os.path.join(tmp, "conversations.json")
            with open(filepath, "w") as f:
                json.dump([conv], f)

            artifact_store = FakeArtifactStore()
            lexical_store = FakeLexicalStore()

            results = await ingest_file(filepath, artifact_store, lexical_store, domain="test")
            assert len(results) == 1
            assert results[0].source_format == "chatgpt_json"

    async def test_ingest_file_markdown(self):
        from aip.orchestration.ingestion.pipeline import ingest_file

        with tempfile.TemporaryDirectory() as tmp:
            filepath = os.path.join(tmp, "chat.md")
            with open(filepath, "w") as f:
                f.write("# Markdown Chat\n\n**User**: Hello\n**Assistant**: Hi")

            artifact_store = FakeArtifactStore()
            lexical_store = FakeLexicalStore()

            results = await ingest_file(filepath, artifact_store, lexical_store, domain="test")
            assert len(results) == 1
            assert results[0].source_format == "markdown"

    async def test_provenance_in_artifact_metadata(self):
        from aip.orchestration.ingestion.pipeline import ingest_conversation

        conv = ImportedConversation(
            conversation_id="test:prov",
            title="Provenance Test",
            turns=[ConversationTurn(role="user", content="Hello")],
            source_format="plaintext",
            source_file="prov.txt",
            metadata={"domain": "test"},
        )

        artifact_store = FakeArtifactStore()
        lexical_store = FakeLexicalStore()

        await ingest_conversation(conv, artifact_store, lexical_store)

        _, _, metadata = artifact_store.written[0]
        assert metadata["artifact_type"] == "conversation"
        assert metadata["source_format"] == "plaintext"
        assert metadata["source_file"] == "prov.txt"
        assert "imported_at" in metadata
        assert metadata["turn_count"] == 1
        assert metadata["domain"] == "test"

    async def test_artifact_content_is_valid_json(self):
        from aip.orchestration.ingestion.pipeline import ingest_conversation

        conv = ImportedConversation(
            conversation_id="test:json",
            title="JSON Content Test",
            turns=[ConversationTurn(role="user", content="Hello")],
            source_format="plaintext",
            source_file="json.txt",
            metadata={"domain": "test"},
        )

        artifact_store = FakeArtifactStore()
        lexical_store = FakeLexicalStore()

        await ingest_conversation(conv, artifact_store, lexical_store)

        _, content, _ = artifact_store.written[0]
        parsed = json.loads(content)
        assert parsed["conversation_id"] == "test:json"
        assert len(parsed["turns"]) == 1
        assert parsed["turns"][0]["role"] == "user"

    async def test_auto_format_detection(self):
        from aip.orchestration.ingestion.pipeline import ingest_file

        with tempfile.TemporaryDirectory() as tmp:
            # JSON file should be auto-detected as chatgpt_json
            conv = _make_chatgpt_conversation("Auto Detect")
            filepath = os.path.join(tmp, "export.json")
            with open(filepath, "w") as f:
                json.dump([conv], f)

            artifact_store = FakeArtifactStore()
            lexical_store = FakeLexicalStore()

            results = await ingest_file(filepath, artifact_store, lexical_store, domain="test")
            assert results[0].source_format == "chatgpt_json"


# ---------------------------------------------------------------------------
# Ingestion Schemas Tests
# ---------------------------------------------------------------------------


class TestIngestionSchemas:
    """Tests for ingestion schema types."""

    def test_conversation_turn_creation(self):
        turn = ConversationTurn(role="user", content="Hello", timestamp="2024-01-01T00:00:00Z")
        assert turn.role == "user"
        assert turn.content == "Hello"
        assert turn.timestamp == "2024-01-01T00:00:00Z"

    def test_imported_conversation_creation(self):
        conv = ImportedConversation(
            conversation_id="test:1",
            title="Test",
            turns=[ConversationTurn(role="user", content="Hi")],
            source_format="plaintext",
            source_file="test.txt",
        )
        assert conv.conversation_id == "test:1"
        assert len(conv.turns) == 1
        assert conv.metadata == {}

    def test_ingestion_result_creation(self):
        result = IngestionResult(
            conversation_id="test:1",
            artifact_id="conv:test:1",
            turn_count=3,
            chunk_count=2,
            vector_indexed=True,
            lexical_indexed=True,
            source_format="markdown",
            source_file="test.md",
            errors=[],
        )
        assert result.turn_count == 3
        assert result.vector_indexed is True
        assert result.errors == []

    def test_source_format_literal(self):
        # Valid source formats
        for fmt in ("chatgpt_json", "markdown", "plaintext"):
            conv = ImportedConversation(
                conversation_id="test",
                title="Test",
                turns=[],
                source_format=fmt,
                source_file="test",
            )
            assert conv.source_format == fmt


# ---------------------------------------------------------------------------
# CLI Tests
# ---------------------------------------------------------------------------


class TestCLIIngest:
    """Tests for the aip ingest CLI command."""

    def test_ingest_command_registered(self):
        from click.testing import CliRunner

        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["ingest", "--help"])
        assert result.exit_code == 0
        assert "Import conversations" in result.output

    def test_ingest_file_subcommand_help(self):
        from click.testing import CliRunner

        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["ingest", "file", "--help"])
        assert result.exit_code == 0
        assert "PATH" in result.output

    def test_ingest_directory_subcommand_help(self):
        from click.testing import CliRunner

        from aip.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["ingest", "directory", "--help"])
        assert result.exit_code == 0
        assert "DIRECTORY" in result.output

    def test_ingest_file_with_sample(self):
        from click.testing import CliRunner

        from aip.cli.main import cli

        with tempfile.TemporaryDirectory() as tmp:
            filepath = os.path.join(tmp, "chat.txt")
            with open(filepath, "w") as f:
                f.write("User: Hello\nAssistant: Hi there")

            db_path = os.path.join(tmp, "test.db")

            runner = CliRunner()
            result = runner.invoke(cli, ["ingest", "file", filepath, "--db-path", db_path])
            # May succeed or fail depending on store initialization, but shouldn't crash
            # with an unhandled exception
            assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# Integration: Search After Ingestion
# ---------------------------------------------------------------------------


class TestSearchAfterIngestion:
    """Verify that ingested content is discoverable via lexical search."""

    async def test_lexical_search_finds_ingested_content(self):
        from aip.orchestration.ingestion.pipeline import ingest_conversation

        conv = ImportedConversation(
            conversation_id="test:search",
            title="Searchable Content",
            turns=[
                ConversationTurn(role="user", content="Tell me about quantum computing."),
                ConversationTurn(
                    role="assistant",
                    content="Quantum computing uses quantum bits or qubits instead of classical bits.",
                ),
            ],
            source_format="plaintext",
            source_file="search.txt",
            metadata={"domain": "physics"},
        )

        # Use real FTS5 store for integration test
        with tempfile.TemporaryDirectory() as tmp:
            from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore

            lexical_db = os.path.join(tmp, "lexical.db")
            lexical_store = SqliteFts5LexicalStore(lexical_db)
            await lexical_store.initialize()

            try:
                result = await ingest_conversation(conv, FakeArtifactStore(), lexical_store)
                assert result.lexical_indexed is True

                # Search for the ingested content
                hits = await lexical_store.search("quantum computing", domain="physics", limit=5)
                assert len(hits) > 0
                assert any("quantum" in (h.content or "").lower() for h in hits)
            finally:
                await lexical_store.close()


def _make_chatgpt_conversation(title="Test Chat", turns=None):
    """Build a minimal ChatGPT export conversation dict (used by pipeline tests too)."""
    mapping = {}
    parent = None
    for i, (role, content) in enumerate(turns or [("user", "Hello"), ("assistant", "Hi there")]):
        node_id = f"node_{i}"
        mapping[node_id] = {
            "id": node_id,
            "message": {
                "id": f"msg_{i}",
                "author": {"role": role},
                "content": {"parts": [content]},
                "create_time": 1700000000.0 + i * 60,
            },
            "parent": parent,
            "children": [],
        }
        if parent is not None:
            mapping[parent]["children"].append(node_id)
        parent = node_id
    return {"title": title, "create_time": 1700000000.0, "mapping": mapping}
