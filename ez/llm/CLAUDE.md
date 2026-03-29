# ez/llm — LLM Provider Abstraction Layer (V2.7+)

## Responsibility
Unified interface for multiple LLM providers. Handles chat completion + tool calling + streaming (sync + async).

## Public Interfaces
- `LLMProvider(ABC)` — Base class: `chat()`, `stream_chat()`, `achat()`, `astream_chat()`, `aclose()`
- `LLMProvider.provider_name` / `model_name` / `has_api_key` — Public properties (V2.7.1)
- `LLMMessage` — Message dataclass (role, content, tool_calls)
- `LLMResponse` — Complete response (content, tool_calls, finish_reason, usage)
- `LLMEvent` — Streaming event (content/tool_call/done/error)
- `ToolCall` — Tool invocation (id, name, arguments)
- `OpenAICompatProvider` — Works with DeepSeek, Qwen, Ollama, OpenAI; persistent `httpx.AsyncClient`
- `create_provider(config)` — Factory with singleton caching (V2.7.1)
- `reset_provider_cache()` — Invalidate cached provider (settings change)

## Files
| File | Role |
|------|------|
| provider.py | ABC + data types |
| openai_compat.py | OpenAI-compatible provider (DeepSeek, Qwen, Local, OpenAI) |
| factory.py | Provider factory from config |

## Dependencies
- Upstream: ez/config (LLMConfig)
- Downstream: ez/agent/ (assistant, tools)

## Provider Priority
| Provider | China Direct | tool_use | Priority |
|----------|-------------|----------|----------|
| DeepSeek | Yes | Yes (OpenAI compat) | **P0** |
| Qwen | Yes | Yes | P1 |
| Local (Ollama) | Yes (offline) | Partial | P1 |
| OpenAI/Claude | No (proxy needed) | Yes | P2 |

## Configuration
```yaml
llm:
  provider: deepseek
  api_key: ${DEEPSEEK_API_KEY}
  model: deepseek-chat
  base_url: ~
  timeout: 60
  max_tokens: 4096
  temperature: 0.3
```

## V2.7.1 Changes
- Async methods: `achat()`, `astream_chat()` with `httpx.AsyncClient` (non-blocking)
- Persistent client: `OpenAICompatProvider` holds `httpx.AsyncClient` instance (connection pool)
- Public properties: `provider_name`, `model_name`, `has_api_key` (replaces private attr access)
- Factory singleton: `create_provider()` caches by config fingerprint, `reset_provider_cache()` invalidates
- `aclose()`: Graceful shutdown of async client

## Status
- experimental (V2.7+)
