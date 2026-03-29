import { useState, useRef, useEffect, useCallback } from 'react'

interface Props {
  editorCode?: string
  onCodeUpdate?: (code: string, filename?: string) => void
}

interface ChatMsg {
  role: 'user' | 'assistant' | 'tool'
  content: string
  toolName?: string
  toolArgs?: string
}

interface Conversation {
  id: string
  title: string
  messages: ChatMsg[]
  createdAt: number
  updatedAt: number
}

const STORAGE_KEY = 'ez-chat-conversations'
const ACTIVE_KEY = 'ez-chat-active-id'

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

function saveConversations(convs: Conversation[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(convs))
}

function newId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

function titleFromMsg(msg: string): string {
  const clean = msg.replace(/\n/g, ' ').trim()
  return clean.length > 24 ? clean.slice(0, 24) + '...' : clean
}

export default function ChatPanel({ editorCode = '', onCodeUpdate }: Props) {
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations())
  const [activeId, setActiveId] = useState<string>(() => localStorage.getItem(ACTIVE_KEY) || '')
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [llmStatus, setLlmStatus] = useState<{ available: boolean; provider?: string; model?: string } | null>(null)
  const [showList, setShowList] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const activeConv = conversations.find(c => c.id === activeId) || null
  const messages = activeConv?.messages || []

  // Persist whenever conversations change
  useEffect(() => {
    saveConversations(conversations)
  }, [conversations])

  useEffect(() => {
    if (activeId) localStorage.setItem(ACTIVE_KEY, activeId)
  }, [activeId])

  useEffect(() => {
    fetch('/api/chat/status').then(r => r.json()).then(setLlmStatus).catch(() => setLlmStatus({ available: false }))
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const updateActiveMessages = useCallback((updater: (prev: ChatMsg[]) => ChatMsg[]) => {
    setConversations(prev => prev.map(c =>
      c.id === activeId ? { ...c, messages: updater(c.messages), updatedAt: Date.now() } : c
    ))
  }, [activeId])

  const createConversation = () => {
    const conv: Conversation = {
      id: newId(),
      title: '新对话',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    setConversations(prev => [conv, ...prev])
    setActiveId(conv.id)
    setShowList(false)
  }

  const deleteConversation = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setConversations(prev => prev.filter(c => c.id !== id))
    if (activeId === id) {
      const remaining = conversations.filter(c => c.id !== id)
      setActiveId(remaining.length > 0 ? remaining[0].id : '')
    }
  }

  const switchConversation = (id: string) => {
    setActiveId(id)
    setShowList(false)
  }

  const sendMessage = async () => {
    if (!input.trim() || streaming) return

    // Auto-create conversation if none active
    let currentId = activeId
    if (!currentId) {
      const conv: Conversation = {
        id: newId(),
        title: titleFromMsg(input.trim()),
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }
      setConversations(prev => [conv, ...prev])
      currentId = conv.id
      setActiveId(conv.id)
    }

    const userMsg: ChatMsg = { role: 'user', content: input.trim() }

    // Update title if first message
    setConversations(prev => prev.map(c => {
      if (c.id !== currentId) return c
      const updated = { ...c, messages: [...c.messages, userMsg], updatedAt: Date.now() }
      if (c.messages.length === 0) updated.title = titleFromMsg(input.trim())
      return updated
    }))

    setInput('')
    setStreaming(true)

    // Build API messages from the conversation (after adding user msg)
    const convNow = conversations.find(c => c.id === currentId)
    const allMsgs = [...(convNow?.messages || []), userMsg]
    const apiMessages = allMsgs
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .map(m => ({ role: m.role, content: m.content }))

    // We need a local ref to activeId for the streaming callbacks
    const targetId = currentId

    const updateMsgs = (updater: (prev: ChatMsg[]) => ChatMsg[]) => {
      setConversations(prev => prev.map(c =>
        c.id === targetId ? { ...c, messages: updater(c.messages), updatedAt: Date.now() } : c
      ))
    }

    try {
      const res = await fetch('/api/chat/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: apiMessages, editor_code: editorCode }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Request failed' }))
        updateMsgs(prev => [...prev, { role: 'assistant', content: `错误: ${err.detail || res.statusText}` }])
        setStreaming(false)
        return
      }

      const reader = res.body?.getReader()
      if (!reader) return
      const decoder = new TextDecoder()
      let assistantContent = ''
      let buffer = ''
      let eventType = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            const dataStr = line.slice(6)
            try {
              const data = JSON.parse(dataStr)
              if (eventType === 'content') {
                assistantContent += data.text || ''
                const snap = assistantContent
                updateMsgs(prev => {
                  const updated = [...prev]
                  const lastIdx = updated.length - 1
                  if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                    updated[lastIdx] = { ...updated[lastIdx], content: snap }
                  } else {
                    updated.push({ role: 'assistant', content: snap })
                  }
                  return updated
                })
              } else if (eventType === 'tool_start') {
                updateMsgs(prev => [...prev, {
                  role: 'tool', content: `${data.name} 调用中...`,
                  toolName: data.name, toolArgs: JSON.stringify(data.args, null, 2),
                }])
              } else if (eventType === 'tool_result') {
                updateMsgs(prev => {
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
                // Push code to editor when strategy is created/updated
                if ((data.name === 'create_strategy' || data.name === 'update_strategy') && onCodeUpdate) {
                  try {
                    const r = typeof data.result === 'string' ? JSON.parse(data.result) : data.result
                    if (r.success && r.path) {
                      const fname = r.path.replace('strategies/', '')
                      fetch(`/api/code/files/${fname}`).then(resp => resp.json()).then(f => {
                        if (f.code) onCodeUpdate(f.code, fname)
                      }).catch(() => {})
                    }
                  } catch {}
                }
                assistantContent = ''
              } else if (eventType === 'error') {
                updateMsgs(prev => [...prev, { role: 'assistant', content: `错误: ${data.message}` }])
              }
              eventType = ''
            } catch {}
          } else if (line.trim() === '') {
            eventType = ''
          }
        }
      }
    } catch (e: any) {
      updateMsgs(prev => [...prev, { role: 'assistant', content: `网络错误: ${e.message}` }])
    } finally {
      setStreaming(false)
    }
  }

  return (
    <div className="flex flex-col h-full" style={{ backgroundColor: 'var(--bg-primary)' }}>
      {/* Header */}
      <div className="px-3 py-2 border-b flex items-center justify-between gap-2" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowList(!showList)}
            className="text-xs px-1.5 py-0.5 rounded"
            style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
            title="对话列表">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
            </svg>
          </button>
          <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
            {activeConv ? activeConv.title : 'AI 助手'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {llmStatus && (
            <span className="text-xs" style={{ color: llmStatus.available ? '#22c55e' : '#ef4444' }}>
              {llmStatus.available ? `${llmStatus.provider}` : '未配置'}
            </span>
          )}
          <button onClick={createConversation}
            className="text-xs px-1.5 py-0.5 rounded"
            style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
            title="新建对话">+</button>
        </div>
      </div>

      {/* Conversation list dropdown */}
      {showList && (
        <div className="border-b overflow-y-auto" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)', maxHeight: '200px' }}>
          {conversations.length === 0 && (
            <div className="px-3 py-3 text-xs text-center" style={{ color: 'var(--text-secondary)' }}>暂无对话，点击 + 新建</div>
          )}
          {conversations.map(c => (
            <div key={c.id}
              className="flex items-center justify-between px-3 py-2 cursor-pointer group"
              style={{ backgroundColor: c.id === activeId ? 'var(--bg-primary)' : 'transparent', borderBottom: '1px solid var(--border)' }}
              onClick={() => switchConversation(c.id)}>
              <div className="flex-1 min-w-0">
                <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>{c.title}</div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  {c.messages.length} 条消息 · {new Date(c.updatedAt).toLocaleDateString()}
                </div>
              </div>
              <button onClick={e => deleteConversation(c.id, e)}
                className="opacity-0 group-hover:opacity-100 text-xs ml-2 px-1 rounded"
                style={{ color: '#ef4444' }}>x</button>
            </div>
          ))}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3" style={{ minHeight: 0 }}>
        {messages.length === 0 && (
          <div className="text-xs text-center py-8" style={{ color: 'var(--text-secondary)' }}>
            <p>可以让我帮你编写策略、调试代码或运行回测</p>
            <p className="mt-2 opacity-70">示例: "帮我写一个 RSI 超卖反转策略"</p>
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
              思考中...
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
            placeholder={llmStatus?.available ? "输入消息..." : "请先配置 LLM API Key"}
            disabled={streaming || !llmStatus?.available}
            className="flex-1 text-xs px-3 py-2 rounded"
            style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border)' }}
          />
          <button
            onClick={sendMessage}
            disabled={streaming || !input.trim() || !llmStatus?.available}
            className="text-xs px-3 py-2 rounded"
            style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: streaming || !input.trim() ? 0.5 : 1 }}>
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
