"""Embedding adapter package.

Currently contains the Ollama embedding client (real + mock for CI).
"""

from .ollama_embed import MockOllamaEmbeddingClient, OllamaEmbeddingClient

__all__ = ["OllamaEmbeddingClient", "MockOllamaEmbeddingClient"]
