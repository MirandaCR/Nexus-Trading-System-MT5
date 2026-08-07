"""
Unified LLM factory — choose the AI provider via ``NEXUS_LLM_PROVIDER``.

DeepSeek (default), OpenAI (GPT) and Google Gemini all expose an
OpenAI-compatible chat endpoint, so a single ``ChatOpenAI`` client serves all
three — only ``base_url``, the API-key env var and the default model differ.
``local`` targets an Ollama (or any OpenAI-compatible) server.

Set the provider in ``.env``::

    NEXUS_LLM_PROVIDER=deepseek     # deepseek (default) | openai | gemini | local
    DEEPSEEK_API_KEY=...            # for deepseek
    OPENAI_API_KEY=...              # for openai / gpt
    GEMINI_API_KEY=...              # for gemini
    # optional global model override (else each provider's default is used):
    NEXUS_LLM_MODEL=

No extra dependencies: everything goes through ``langchain_openai.ChatOpenAI``.
"""
from __future__ import annotations

import os

# provider -> (base_url, api_key_env_var, default_model)
_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
    "openai":   ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "gemini":   ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-2.0-flash"),
}

# Friendly aliases so both "gpt" and "openai", "cloud" and "deepseek", etc. work.
_ALIASES: dict[str, str] = {
    "cloud": "deepseek",
    "gpt": "openai",
    "chatgpt": "openai",
    "openai-gpt": "openai",
    "google": "gemini",
    "google-gemini": "gemini",
}

DEFAULT_PROVIDER = "deepseek"
SUPPORTED_PROVIDERS = ("deepseek", "openai", "gemini", "local")


def resolve_provider() -> str:
    """Return the normalized provider name (default: ``deepseek``).

    Priority: the live dashboard selection (runtime settings, editable without a
    restart) → the ``NEXUS_LLM_PROVIDER`` env var → ``deepseek``.
    """
    raw = ""
    try:
        from shared import runtime_settings
        raw = str(runtime_settings.get_cached().get("llm_provider") or "").strip().lower()
    except Exception:
        raw = ""
    if not raw:
        raw = os.getenv("NEXUS_LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    provider = _ALIASES.get(raw, raw)
    if provider not in _PROVIDERS and provider != "local":
        return DEFAULT_PROVIDER
    return provider


def provider_model(provider: str | None = None) -> str:
    """Default model name for a provider (honors NEXUS_LLM_MODEL override)."""
    override = os.getenv("NEXUS_LLM_MODEL", "").strip()
    if override:
        return override
    provider = provider or resolve_provider()
    if provider == "local":
        return os.getenv("NEXUS_LOCAL_MODEL", "llama3.3:70b")
    return _PROVIDERS.get(provider, _PROVIDERS[DEFAULT_PROVIDER])[2]


def make_llm(
    *,
    temperature: float = 0.3,
    max_tokens: int = 800,
    model: str | None = None,
    request_timeout: int = 30,
):
    """Build a LangChain chat model for the configured provider.

    ``model`` overrides the provider default (used e.g. to honor a per-agent
    DeepSeek model). ``NEXUS_LLM_MODEL`` overrides everything when set.
    """
    from langchain_openai import ChatOpenAI

    provider = resolve_provider()
    global_override = os.getenv("NEXUS_LLM_MODEL", "").strip()

    if provider == "local":
        return ChatOpenAI(
            base_url=os.getenv("NEXUS_LOCAL_LLM_URL", "http://localhost:11434/v1"),
            api_key="ollama",
            model=global_override or model or os.getenv("NEXUS_LOCAL_MODEL", "llama3.3:70b"),
            temperature=temperature,
            max_tokens=max_tokens,
            request_timeout=request_timeout,
        )

    base_url, key_env, default_model = _PROVIDERS.get(provider, _PROVIDERS[DEFAULT_PROVIDER])
    return ChatOpenAI(
        base_url=base_url,
        api_key=os.getenv(key_env, ""),
        model=global_override or model or default_model,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=request_timeout,
    )


def provider_ready(provider: str | None = None) -> bool:
    """True if the configured provider has an API key (or is local)."""
    provider = provider or resolve_provider()
    if provider == "local":
        return True
    key_env = _PROVIDERS.get(provider, _PROVIDERS[DEFAULT_PROVIDER])[1]
    return bool(os.getenv(key_env, "").strip())
