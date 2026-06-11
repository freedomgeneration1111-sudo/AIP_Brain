"""OpenAI-Compatible Embedding Client — real embedding via OpenAI /v1/embeddings API.

Implements EmbeddingProvider. Works with OpenRouter, OpenAI, and any provider
that implements the OpenAI embeddings API (POST /v1/embeddings).

Supports deterministic mock mode for CI (no real API required).
"""

from __future__ import annotations

import hashlib

import httpx

from aip.foundation.protocols import EmbeddingProvider
from aip.logging import get_logger

log = get_logger(__name__)


class OpenAICompatibleEmbeddingClient(EmbeddingProvider):
    """Embedding client for OpenAI-compatible /v1/embeddings endpoints.

    Works with OpenRouter, OpenAI, DeepSeek, and any provider that
    implements the OpenAI embeddings API format.

    The API call format is:
        POST {base_url}/v1/embeddings
        Authorization: Bearer {api_key}
        {"model": "...", "input": "text to embed"}
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        dimensions: int | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dimensions = dimensions
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
        )

    async def embed(self, text: str) -> list[float]:
        """Embed text using the OpenAI-compatible embeddings endpoint.

        On failure (API down, auth error, etc.), raises ConnectionError
        with a descriptive message (no silent fake fallback).
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict = {
            "model": self.model,
            "input": text,
        }
        # Some models support explicit dimensions (e.g. text-embedding-3-small)
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions

        try:
            resp = await self._client.post(
                "/v1/embeddings",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            # OpenAI embeddings response format:
            # {"data": [{"embedding": [0.1, 0.2, ...], "index": 0}], ...}
            embedding_data = data.get("data", [])
            if not embedding_data:
                raise ConnectionError(
                    f"Empty embedding response from {self.base_url}/v1/embeddings "
                    f"(model={self.model}). Response: {data}"
                )

            vec = embedding_data[0].get("embedding", [])
            if not vec:
                raise ConnectionError(
                    f"No embedding vector in response from {self.base_url}/v1/embeddings (model={self.model})."
                )

            log.debug(
                "embedding_generated",
                model=self.model,
                dimensions=len(vec),
                text_preview=text[:80],
            )
            return vec

        except httpx.HTTPStatusError as e:
            raise ConnectionError(
                f"Embedding API returned HTTP {e.response.status_code} for "
                f"{self.base_url}/v1/embeddings (model={self.model}). "
                f"Response: {e.response.text[:500]}"
            ) from e
        except httpx.RequestError as e:
            raise ConnectionError(
                f"Failed to connect to embedding API at {self.base_url}/v1/embeddings (model={self.model}): {e}"
            ) from e
        except ConnectionError:
            raise
        except Exception as e:
            raise ConnectionError(
                f"Unexpected error embedding via {self.base_url}/v1/embeddings (model={self.model}): {e}"
            ) from e

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    def __repr__(self) -> str:
        return (
            f"OpenAICompatibleEmbeddingClient("
            f"base_url={self.base_url!r}, model={self.model!r}, "
            f"dimensions={self.dimensions})"
        )


class MockOpenAICompatibleEmbeddingClient(EmbeddingProvider):
    """Deterministic mock embedding client for CI / testing.

    Returns a vector derived from the input text hash, similar to
    MockOllamaEmbeddingClient but named for clarity about which
    provider type it mocks.
    """

    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        """Return a deterministic fake embedding vector."""
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        i = 0
        while len(vec) < self.dimensions:
            val = (h[i % len(h)] / 255.0) - 0.5
            vec.append(val)
            i += 1
        return vec[: self.dimensions]
