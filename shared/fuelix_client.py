"""
Nexus Trader — FuelIX AI Gateway Client.

Unified OpenAI-compatible client that routes to multiple models through
the FuelIX API (Claude, Gemini, GPT).
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from shared.http_client import new_client
import structlog

logger = structlog.get_logger(__name__)

FUELIX_BASE_URL = os.getenv(
    "FUELIX_BASE_URL", "https://api.fuelix.ai/v1/chat/completions"
)
FUELIX_API_KEY = os.getenv("FUELIX_API_KEY", "")

MODEL_ALIASES: dict[str, str] = {
    "primary":   os.getenv("FUELIX_MODEL_PRIMARY", "claude-sonnet-4-5"),
    "fast":      os.getenv("FUELIX_MODEL_FAST", "gemini-3-pro"),
    "reasoning": os.getenv("FUELIX_MODEL_REASONING", "gpt-5.4"),
}


class FuelixClient:
    """Async client for the FuelIX /v1/chat/completions endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key or FUELIX_API_KEY
        self._base_url = base_url or FUELIX_BASE_URL
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = new_client(
                timeout=httpx.Timeout(self._timeout),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def resolve_model(self, alias_or_name: str) -> str:
        return MODEL_ALIASES.get(alias_or_name, alias_or_name)

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "primary",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Send a chat completion request.

        Parameters
        ----------
        messages : list of {"role": ..., "content": ...}
        model : model alias ("primary", "fast", "reasoning") or raw model name
        temperature : sampling temperature
        max_tokens : max output tokens
        """
        resolved = self.resolve_model(model)
        client = await self._get_client()

        payload: dict[str, Any] = {
            "model": resolved,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }

        logger.debug(
            "fuelix.request",
            model=resolved,
            messages_count=len(messages),
            temperature=temperature,
        )

        resp = await client.post(self._base_url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        usage = data.get("usage", {})
        logger.info(
            "fuelix.response",
            model=resolved,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
        return data

    async def ask(
        self,
        prompt: str,
        system: str = "",
        model: str = "primary",
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        """Convenience: single-turn prompt -> content string."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        data = await self.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return data["choices"][0]["message"]["content"]

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Singleton for easy import
fuelix = FuelixClient()
