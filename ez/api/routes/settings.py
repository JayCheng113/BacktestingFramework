"""V2.7: Settings API — read/write runtime configuration."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class LLMSettings(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    temperature: float = 0.3


def _read_env() -> dict[str, str]:
    """Read .env file into dict."""
    result: dict[str, str] = {}
    if not _ENV_FILE.exists():
        return result
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


import threading

_env_lock = threading.Lock()


def _write_env(updates: dict[str, str]) -> None:
    """Update .env file — preserves comments and other keys."""
    # Sanitize: reject newlines/CR/null (prevents .env injection)
    for key, value in updates.items():
        if any(c in value for c in ('\n', '\r', '\0')):
            raise ValueError(f"Invalid characters in value for {key}")

    with _env_lock:
        lines: list[str] = []
        if _ENV_FILE.exists():
            lines = _ENV_FILE.read_text().splitlines()

        updated_keys: set[str] = set()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    new_lines.append(f"{key}={updates[key]}")
                    updated_keys.add(key)
                    continue
            new_lines.append(line)

        # Append new keys not already in file
        for key, value in updates.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={value}")

        _ENV_FILE.write_text("\n".join(new_lines) + "\n")


_KEY_MAP = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "openai": "OPENAI_API_KEY",
}


@router.get("/llm")
def get_llm_settings():
    """Get current LLM settings."""
    from ez.config import load_config
    config = load_config()
    env = _read_env()

    # Determine actual API key (from config or env)
    provider = config.llm.provider
    env_key = _KEY_MAP.get(provider, "")
    api_key = config.llm.api_key or env.get(env_key, "") if env_key else config.llm.api_key

    return {
        "provider": provider,
        "api_key_set": bool(api_key),
        "api_key_preview": f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else ("***" if api_key else ""),
        "model": config.llm.model or "(默认)",
        "base_url": config.llm.base_url or "(默认)",
        "temperature": config.llm.temperature,
        "available_providers": [
            {"id": "deepseek", "name": "DeepSeek", "env_key": "DEEPSEEK_API_KEY", "needs_key": True},
            {"id": "qwen", "name": "通义千问 (Qwen)", "env_key": "QWEN_API_KEY", "needs_key": True},
            {"id": "openai", "name": "OpenAI", "env_key": "OPENAI_API_KEY", "needs_key": True},
            {"id": "local", "name": "本地模型 (Ollama)", "env_key": "", "needs_key": False},
        ],
    }


@router.post("/llm")
def update_llm_settings(req: LLMSettings):
    """Update LLM settings — writes to .env and reloads config."""
    updates: dict[str, str] = {}

    # Set the API key for the selected provider
    env_key = _KEY_MAP.get(req.provider, "")
    if env_key and req.api_key:
        updates[env_key] = req.api_key
        os.environ[env_key] = req.api_key

    if updates:
        _write_env(updates)

    # Reload config to pick up changes (reset forces re-read of .env + yaml)
    from ez.config import reset_config, load_config
    reset_config()
    load_config()

    return {"status": "ok", "provider": req.provider, "api_key_set": bool(req.api_key)}


@router.get("/tushare")
def get_tushare_settings():
    """Get Tushare token status."""
    env = _read_env()
    token = env.get("TUSHARE_TOKEN", "")
    return {
        "token_set": bool(token),
        "token_preview": f"{token[:8]}...{token[-4:]}" if len(token) > 12 else ("***" if token else ""),
    }


class TushareSettings(BaseModel):
    token: str = ""


@router.post("/tushare")
def update_tushare_settings(data: TushareSettings):
    """Update Tushare token."""
    token = data.token
    if token:
        _write_env({"TUSHARE_TOKEN": token})
        os.environ["TUSHARE_TOKEN"] = token
    return {"status": "ok", "token_set": bool(token)}
