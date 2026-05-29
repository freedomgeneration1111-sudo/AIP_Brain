"""Model Slot Resolver — real model wiring via configurable provider slots.

Resolves named slots to concrete provider + model + base_url.
Supports ci_mode for fully deterministic tests (no network).

Real dispatch supports:
  - Ollama (local /api/chat endpoint)
  - OpenAI-compatible (/v1/chat/completions endpoint)

Configuration sources (in priority order):
  1. Explicit slot config dict passed to __init__ under ``models.<slot>``.
  2. Environment variables:
       AIP_<SLOT>_BASE_URL   — e.g. AIP_SYNTHESIS_BASE_URL
       AIP_<SLOT>_MODEL      — e.g. AIP_SYNTHESIS_MODEL
       AIP_<SLOT>_API_KEY    — e.g. AIP_SYNTHESIS_API_KEY (for OpenAI-compatible)
       AIP_<SLOT>_PROVIDER   — e.g. AIP_SYNTHESIS_PROVIDER (ollama | openai_compatible)
  3. Global defaults:
       AIP_OLLAMA_BASE_URL   — default base URL for all Ollama slots (default: http://localhost:11434)
       AIP_OPENAI_BASE_URL   — default base URL for all OpenAI-compatible slots
       AIP_OPENAI_API_KEY    — default API key for all OpenAI-compatible slots
"""

from __future__ import annotations

import os
import time
from typing import Any

from aip.foundation.protocols import ModelProvider
from aip.foundation.schemas import ModelSlotConfig
from aip.logging import get_logger

log = get_logger(__name__)

# Provider constants
PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"

# Default Ollama base URL (local laptop-viable default)
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


class ModelSlotResolver(ModelProvider):
    """Resolves model slots and executes calls (with ci_mode support).

    In ``ci_mode=True`` (default), returns deterministic fixtures with no network.
    In ``ci_mode=False``, dispatches to the real provider (Ollama or OpenAI-compatible)
    using httpx for async HTTP calls.

    Provider selection is per-slot via ``models.<slot>.provider`` or the
    ``AIP_<SLOT>_PROVIDER`` environment variable.
    """

    def __init__(self, config: dict | Any) -> None:
        if hasattr(config, "model_dump"):
            self._cfg = config.model_dump()
        elif isinstance(config, dict):
            self._cfg = config
        else:
            self._cfg = {}

        self._models = self._cfg.get("models", {})
        # ci_mode defaults to True unless explicitly set to False
        self._ci_mode = self._models.get("ci_mode", True)

        # Lazy httpx client — created on first real call, reused thereafter
        self._http_client: Any = None

    def _get_http_client(self) -> Any:
        """Lazily create an httpx.AsyncClient for real provider calls.

        Returns the client; callers must use it as an async context manager
        or call aclose() when done.
        """
        if self._http_client is None:
            try:
                import httpx
            except ImportError:
                raise RuntimeError(
                    "httpx is required for real model calls but is not installed. Install it with: pip install httpx",
                )
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    async def close(self) -> None:
        """Close the httpx client if it was created."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Configuration resolution with environment variable fallbacks
    # ------------------------------------------------------------------

    def _resolve_slot_config(self, slot_name: str) -> dict:
        """Resolve slot config from dict + environment variable overrides.

        Environment variables follow the pattern AIP_<SLOT>_KEY (uppercased).
        For example, slot "synthesis" checks:
          AIP_SYNTHESIS_BASE_URL, AIP_SYNTHESIS_MODEL,
          AIP_SYNTHESIS_PROVIDER, AIP_SYNTHESIS_API_KEY
        """
        slot_cfg = self._models.get(slot_name, {})
        if not isinstance(slot_cfg, dict):
            slot_cfg = {}

        env_prefix = f"AIP_{slot_name.upper()}_"

        # Environment variable overrides (highest priority)
        env_base_url = os.environ.get(f"{env_prefix}BASE_URL")
        env_model = os.environ.get(f"{env_prefix}MODEL")
        env_provider = os.environ.get(f"{env_prefix}PROVIDER")
        env_api_key = os.environ.get(f"{env_prefix}API_KEY")

        resolved = {
            "provider": env_provider or slot_cfg.get("provider", PROVIDER_OLLAMA),
            "model": env_model or slot_cfg.get("model", f"<{slot_name}>"),
            "base_url": env_base_url or slot_cfg.get("base_url"),
            "api_key": env_api_key or slot_cfg.get("api_key"),
            "fallback_provider": slot_cfg.get("fallback_provider"),
            "fallback_model": slot_cfg.get("fallback_model"),
            "dimensions": slot_cfg.get("dimensions"),
        }

        # Apply global defaults if still missing
        if not resolved["base_url"]:
            if resolved["provider"] == PROVIDER_OLLAMA:
                resolved["base_url"] = os.environ.get("AIP_OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL)
            elif resolved["provider"] == PROVIDER_OPENAI_COMPATIBLE:
                resolved["base_url"] = os.environ.get("AIP_OPENAI_BASE_URL", "https://api.openai.com")

        if not resolved["api_key"] and resolved["provider"] == PROVIDER_OPENAI_COMPATIBLE:
            resolved["api_key"] = os.environ.get("AIP_OPENAI_API_KEY")

        return resolved

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, slot_name: str) -> ModelSlotConfig:
        """Resolve a slot name to a ModelSlotConfig."""
        if slot_name not in self._models:
            raise ValueError(f"Unknown model slot: {slot_name}")

        slot_cfg = self._models[slot_name]
        # Filter: only process dict values (skip non-dict like ci_mode flag)
        if not isinstance(slot_cfg, dict):
            raise ValueError(f"Unknown model slot: {slot_name}")

        # Merge with env var overrides for the returned config
        resolved = self._resolve_slot_config(slot_name)

        return ModelSlotConfig(
            slot_name=slot_name,
            provider=resolved["provider"],
            model=resolved["model"],
            base_url=resolved["base_url"],
            fallback_provider=resolved.get("fallback_provider"),
            fallback_model=resolved.get("fallback_model"),
            dimensions=resolved.get("dimensions"),
        )

    def list_slots(self) -> list[str]:
        """List all configured slot names (excluding non-dict entries like ci_mode)."""
        return [k for k, v in self._models.items() if isinstance(v, dict)]

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        """Execute a model call for the given slot.

        In ci_mode: returns a deterministic fixture (no network).
        Otherwise: dispatches to the appropriate provider via HTTP.

        Returns a dict with: content, model, usage, latency_ms, cost_usd.
        On provider failure, returns an error dict with content set to an
        error message and error=True, rather than raising.
        """
        resolved = self._resolve_slot_config(slot_name)
        provider = resolved["provider"]
        model = resolved["model"]
        base_url = resolved["base_url"]

        if self._ci_mode:
            # Deterministic fixture — content is a hash of the input for reproducibility
            prompt = messages[-1]["content"] if messages else ""
            content = f"[CI-FIXTURE for {slot_name}] {prompt[:80]}..."
            log.debug("ci_fixture_response", slot=slot_name)
            return {
                "content": content,
                "model": model,
                "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 50},
                "latency_ms": 5,
                "cost_usd": 0.0,
            }

        # Real dispatch
        start = time.perf_counter()
        try:
            if provider == PROVIDER_OLLAMA:
                result = await self._call_ollama(base_url, model, messages, **kwargs)
            elif provider == PROVIDER_OPENAI_COMPATIBLE:
                result = await self._call_openai_compatible(
                    base_url,
                    model,
                    resolved.get("api_key"),
                    messages,
                    **kwargs,
                )
            else:
                raise ValueError(
                    f"Unsupported provider '{provider}' for slot '{slot_name}'. "
                    f"Supported providers: {PROVIDER_OLLAMA}, {PROVIDER_OPENAI_COMPATIBLE}",
                )
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            log.error(
                "model_call_failed",
                slot=slot_name,
                provider=provider,
                model=model,
                error=str(exc),
                exc_info=True,
            )
            # Return a structured error result instead of raising
            return {
                "content": "",
                "model": model,
                "usage": {},
                "latency_ms": elapsed_ms,
                "cost_usd": 0.0,
                "error": True,
                "error_message": (
                    f"Model call failed for slot '{slot_name}' (provider={provider}, model={model}): {exc}"
                ),
            }

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # Ensure result has required keys
        result.setdefault("model", model)
        result.setdefault("usage", {})
        result.setdefault("latency_ms", elapsed_ms)
        result.setdefault("cost_usd", 0.0)
        result.setdefault("error", False)

        usage = result.get("usage", {})
        log.info(
            "model_call_complete",
            slot=slot_name,
            provider=provider,
            model=model,
            latency_ms=elapsed_ms,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cost_usd=result.get("cost_usd", 0.0),
        )

        return result

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    async def _call_ollama(
        self,
        base_url: str,
        model: str,
        messages: list[dict],
        **kwargs: Any,
    ) -> dict:
        """Call an Ollama /api/chat endpoint.

        Ollama chat API: POST /api/chat with {model, messages, stream: false, options: {...}}.
        Returns the assistant message content and usage stats.
        """
        client = self._get_http_client()

        url = f"{base_url.rstrip('/')}/api/chat"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        # Map common kwargs to Ollama options
        options: dict[str, Any] = {}
        if "temperature" in kwargs:
            options["temperature"] = kwargs["temperature"]
        if "num_predict" in kwargs:
            options["num_predict"] = kwargs["num_predict"]
        elif "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]
        if "top_p" in kwargs:
            options["top_p"] = kwargs["top_p"]
        if options:
            payload["options"] = options

        log.debug("provider_dispatch", provider="ollama", url=url, model=model)
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        # Extract content from Ollama response
        content = ""
        message = data.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", "")
        elif isinstance(message, str):
            content = message

        # Ollama returns eval_count / prompt_eval_count for usage
        eval_count = data.get("eval_count", 0) or 0
        prompt_eval_count = data.get("prompt_eval_count", 0) or 0

        return {
            "content": content,
            "model": data.get("model", model),
            "usage": {
                "prompt_tokens": prompt_eval_count,
                "completion_tokens": eval_count,
                "total_tokens": prompt_eval_count + eval_count,
            },
        }

    async def _call_openai_compatible(
        self,
        base_url: str,
        model: str,
        api_key: str | None,
        messages: list[dict],
        **kwargs: Any,
    ) -> dict:
        """Call an OpenAI-compatible /v1/chat/completions endpoint.

        Works with OpenAI, DeepSeek, Together, Fireworks, and any provider
        that implements the OpenAI chat completions API.
        """
        client = self._get_http_client()

        url = f"{base_url.rstrip('/')}/v1/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        if "top_p" in kwargs:
            payload["top_p"] = kwargs["top_p"]

        log.debug("provider_dispatch", provider="openai_compatible", url=url, model=model)
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # Extract content from OpenAI-format response
        content = ""
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")

        # Extract usage from OpenAI-format response
        usage = data.get("usage", {})
        usage_data = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

        return {
            "content": content,
            "model": data.get("model", model),
            "usage": usage_data,
        }
