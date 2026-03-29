"""LLM provider factory — creates provider from config.

Reads API keys from environment variables:
  - DEEPSEEK_API_KEY
  - QWEN_API_KEY / DASHSCOPE_API_KEY
  - OPENAI_API_KEY

V2.7.1: Singleton caching — same config fingerprint reuses the same provider
instance (and its persistent httpx.AsyncClient).
"""
from __future__ import annotations

import os
import threading

from ez.config import LLMConfig
from ez.llm.openai_compat import OpenAICompatProvider
from ez.llm.provider import LLMProvider

_ENV_KEY_MAP = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "openai": "OPENAI_API_KEY",
    "local": "",
}

_lock = threading.Lock()
_cached_provider: LLMProvider | None = None
_cached_fingerprint: str = ""


def _fingerprint(config: LLMConfig, api_key: str) -> str:
    """Create a cache key from config + resolved API key."""
    return f"{config.provider}|{api_key}|{config.model}|{config.base_url}|{config.timeout}|{config.max_tokens}|{config.temperature}"


def create_provider(config: LLMConfig | None = None) -> LLMProvider:
    """Create or return cached LLM provider from config."""
    global _cached_provider, _cached_fingerprint

    if config is None:
        from ez.config import load_config
        config = load_config().llm

    api_key = config.api_key
    if not api_key:
        env_var = _ENV_KEY_MAP.get(config.provider, "")
        if env_var:
            api_key = os.environ.get(env_var, "")
        # Qwen fallback: DASHSCOPE_API_KEY
        if not api_key and config.provider == "qwen":
            api_key = os.environ.get("DASHSCOPE_API_KEY", "")

    fp = _fingerprint(config, api_key)

    with _lock:
        if _cached_provider is not None and _cached_fingerprint == fp:
            return _cached_provider

        provider = OpenAICompatProvider(
            provider=config.provider,
            api_key=api_key,
            model=config.model,
            base_url=config.base_url,
            timeout=config.timeout,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
        _cached_provider = provider
        _cached_fingerprint = fp
        return provider


def reset_provider_cache() -> None:
    """Clear cached provider (for settings changes or testing)."""
    global _cached_provider, _cached_fingerprint
    with _lock:
        _cached_provider = None
        _cached_fingerprint = ""
