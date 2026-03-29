# ez/llm — LLM Provider Abstraction Layer (V2.7)

## Responsibility
Unified interface for multiple LLM providers. Handles chat completion + tool calling + streaming.

## Public Interfaces
- `LLMProvider(ABC)` — Base class: `chat()`, `stream_chat()`
- `LLMMessage` — Message dataclass (role, content, tool_calls)
- `LLMResponse` — Complete response (content, tool_calls, finish_reason, usage)
- `LLMEvent` — Streaming event (content/tool_call/done/error)
- `ToolCall` — Tool invocation (id, name, arguments)
- `OpenAICompatProvider` — Works with DeepSeek, Qwen, Ollama, OpenAI
- `create_provider(config)` — Factory function from LLMConfig

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

## Status
- experimental (V2.7)
