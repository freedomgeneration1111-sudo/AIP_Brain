"""Embedding adapter package.

Contains the Ollama embedding client (real + mock for CI) and the
canonical factory for creating embedding providers from config.
"""

from .factory import create_embedding_provider
from .ollama_embed import MockOllamaEmbeddingClient, OllamaEmbeddingClient

__all__ = ["OllamaEmbeddingClient", "MockOllamaEmbeddingClient", "create_embedding_provider"]
