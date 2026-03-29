"""LLM provider factory — creates provider from config.

Reads API keys from environment variables:
  - DEEPSEEK_API_KEY
  - QWEN_API_KEY / DASHSCOPE_API_KEY
  - OPENAI_API_KEY
"""
from __future__ import annotations

import os

from ez.config import LLMConfig
from ez.llm.openai_compat import OpenAICompatProvider
from ez.llm.provider import LLMProvider

_ENV_KEY_MAP = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "openai": "OPENAI_API_KEY",
    "local": "",
}


def create_provider(config: LLMConfig | None = None) -> LLMProvider:
    """Create an LLM provider from config. Falls back to env vars for API key."""
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

    return OpenAICompatProvider(
        provider=config.provider,
        api_key=api_key,
        model=config.model,
        base_url=config.base_url,
        timeout=config.timeout,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
    )
