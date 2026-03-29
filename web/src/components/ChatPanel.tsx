import { useState, useRef, useEffect } from 'react'

interface Props {
  editorCode?: string
}

interface ChatMsg {
  role: 'user' | 'assistant' | 'tool'
  content: string
  toolName?: string
  toolArgs?: string
}

export default function ChatPanel({ editorCode = '' }: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [llmStatus, setLlmStatus] = useState<{ available: boolean; provider?: string; model?: string } | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch('/api/chat/status').then(r => r.json()).then(setLlmStatus).catch(() => setLlmStatus({ available: false }))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async () => {
    if (!input.trim() || streaming) return
    const userMsg: ChatMsg = { role: 'user', content: input.trim() }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setInput('')
    setStreaming(true)

    // Build message history for API
    const apiMessages = newMessages
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .map(m => ({ role: m.role, content: m.content }))

    try {
      const res = await fetch('/api/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: apiMessages, editor_code: editorCode }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Request failed' }))
        setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${err.detail || res.statusText}` }])
        setStreaming(false)
        return
      }

      // Parse SSE stream
      const reader = res.body?.getReader()
      if (!reader) return
      const decoder = new TextDecoder()
      let assistantContent = ''
      let buffer = ''
      let eventType = '' // Persists across chunks

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // Process complete SSE events
        const lines = buffer.split('\n')
        buffer = lines.pop() || '' // Keep incomplete line in buffer

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            const dataStr = line.slice(6)
            try {
              const data = JSON.parse(dataStr)
              if (eventType === 'content') {
                assistantContent += data.text || ''
                setMessages(prev => {
                  const updated = [...prev]
                  const lastIdx = updated.length - 1
                  if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                    updated[lastIdx] = { ...updated[lastIdx], content: assistantContent }
                  } else {
                    updated.push({ role: 'assistant', content: assistantContent })
                  }
                  return updated
                })
              } else if (eventType === 'tool_start') {
                setMessages(prev => [...prev, {
                  role: 'tool',
                  content: `Calling ${data.name}...`,
                  toolName: data.name,
                  toolArgs: JSON.stringify(data.args, null, 2),
                }])
              } else if (eventType === 'tool_result') {
                setMessages(prev => {
                  const updated = [...prev]
                  for (let i = updated.length - 1; i >= 0; i--) {
                    if (updated[i].toolName === data.name && updated[i].role === 'tool') {
                      const result = typeof data.result === 'string' ? data.result : JSON.stringify(data.result)
                      const short = result.length > 300 ? result.slice(0, 300) + '...' : result
                      updated[i] = { ...updated[i], content: `${data.name}: ${short}` }
                      break
                    }
                  }
                  return updated
                })
                // Reset for next assistant content after tool results
                assistantContent = ''
              } else if (eventType === 'error') {
                setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${data.message}` }])
              }
            } catch {}
          }
        }
      }
    } catch (e: any) {
      setMessages(prev => [...prev, { role: 'assistant', content: `Network error: ${e.message}` }])
    } finally {
      setStreaming(false)
    }
  }

  return (
    <div className="flex flex-col h-full" style={{ backgroundColor: 'var(--bg-primary)' }}>
      {/* Header */}
      <div className="px-3 py-2 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
        <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>AI Assistant</span>
        {llmStatus && (
          <span className="text-xs" style={{ color: llmStatus.available ? '#22c55e' : '#ef4444' }}>
            {llmStatus.available ? `${llmStatus.provider}/${llmStatus.model}` : 'Not configured'}
          </span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3" style={{ minHeight: 0 }}>
        {messages.length === 0 && (
          <div className="text-xs text-center py-8" style={{ color: 'var(--text-secondary)' }}>
            <p>Ask me to help write strategies, debug code, or run backtests.</p>
            <p className="mt-2 opacity-70">Example: "Help me write a RSI reversal strategy"</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className="max-w-full rounded-lg px-3 py-2 text-xs" style={{
              backgroundColor: m.role === 'user' ? 'var(--color-accent)' : m.role === 'tool' ? '#1e293b' : 'var(--bg-secondary)',
              color: m.role === 'user' ? '#fff' : 'var(--text-primary)',
              border: m.role === 'tool' ? '1px solid #334155' : 'none',
              maxWidth: '90%',
            }}>
              {m.role === 'tool' && (
                <div className="text-xs opacity-60 mb-1" style={{ color: '#94a3b8' }}>Tool: {m.toolName}</div>
              )}
              <pre className="whitespace-pre-wrap font-sans" style={{ fontFamily: m.role === 'tool' ? 'monospace' : 'inherit' }}>
                {m.content}
              </pre>
            </div>
          </div>
        ))}
        {streaming && (
          <div className="flex justify-start">
            <div className="rounded-lg px-3 py-2 text-xs animate-pulse" style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
              Thinking...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="p-2 border-t" style={{ borderColor: 'var(--border)' }}>
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() } }}
            placeholder={llmStatus?.available ? "Ask me anything..." : "Configure LLM API key first"}
            disabled={streaming || !llmStatus?.available}
            className="flex-1 text-xs px-3 py-2 rounded"
            style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border)' }}
          />
          <button
            onClick={sendMessage}
            disabled={streaming || !input.trim() || !llmStatus?.available}
            className="text-xs px-3 py-2 rounded"
            style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: streaming || !input.trim() ? 0.5 : 1 }}>
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
