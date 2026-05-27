"""Tests for CHUNK-5.1 Ollama Embedding Client (using mock for CI)."""
import pytest

from aip.adapter.embedding.ollama_embed import MockOllamaEmbeddingClient
from aip.foundation.protocols import EmbeddingProvider


def test_mock_client_implements_protocol():
    client = MockOllamaEmbeddingClient()
    assert isinstance(client, EmbeddingProvider)


@pytest.mark.asyncio
async def test_mock_embed_returns_correct_dimensions():
    client = MockOllamaEmbeddingClient(dimensions=768)
    vec = await client.embed("hello world")
    assert len(vec) == 768
    assert all(isinstance(x, float) for x in vec)


@pytest.mark.asyncio
async def test_mock_embed_different_inputs_different_vectors():
    client = MockOllamaEmbeddingClient(dimensions=768)
    v1 = await client.embed("hello")
    v2 = await client.embed("goodbye")
    # They should not be identical (extremely unlikely with the hash method)
    assert v1 != v2
