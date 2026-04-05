import { useState, useRef, useEffect } from 'react'

interface Props {
  editorCode?: string
  onCodeUpdate?: (code: string | undefined, filename?: string, kind?: string) => void
  fileKey?: string  // Bound to a file — auto-switch/create conversation per file
}

interface ChatMsg {
  role: 'user' | 'assistant' | 'tool'
  content: string
  toolName?: string
  toolArgs?: string
}

const _TOOL_LABELS: Record<string, string> = {
  create_strategy: '创建策略', update_strategy: '更新策略',
  read_source: '读取代码', list_strategies: '查询策略列表',
  list_factors: '查询因子列表', run_backtest: '运行回测',
  run_experiment: '运行实验', list_experiments: '查询实验',
  explain_metrics: '查看指标',
}

function _toolLabel(name: string, content: string): string {
  const label = _TOOL_LABELS[name] || name
  return content.includes('调用中') ? `⏳ ${label}...` : `✓ ${label}`
}

interface Conversation {
  id: string
  title: string
  messages: ChatMsg[]
  createdAt: number
  updatedAt: number
  fileKey?: string  // Bound to a specific file (filename)
}

const STORAGE_KEY = 'ez-chat-conversations'
const ACTIVE_KEY = 'ez-chat-active-id'

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

const MAX_CONVERSATIONS = 50

function saveConversations(convs: Conversation[]) {
  // Trim to max conversations (oldest first)
  const trimmed = convs.length > MAX_CONVERSATIONS
    ? convs.slice(0, MAX_CONVERSATIONS)
    : convs
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed))
  } catch {
    // QuotaExceededError — trim aggressively
    try {
      const minimal = trimmed.slice(0, 10).map(c => ({
        ...c, messages: c.messages.slice(-20)
      }))
      localStorage.setItem(STORAGE_KEY, JSON.stringify(minimal))
    } catch { /* give up */ }
  }
}

function newId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

function titleFromMsg(msg: string): string {
  const clean = msg.replace(/\n/g, ' ').trim()
  return clean.length > 24 ? clean.slice(0, 24) + '...' : clean
}

export default function ChatPanel({ editorCode = '', onCodeUpdate, fileKey }: Props) {
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations())
  const [activeId, setActiveId] = useState<string>(() => localStorage.getItem(ACTIVE_KEY) || '')
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const aiCreatedFileRef = useRef(false)  // Distinguishes AI-created file from user-clicked file
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

  // Auto-switch/create conversation when fileKey changes
  useEffect(() => {
    if (!fileKey) return
    // Find existing conversation for this file
    const existing = conversations.find(c => c.fileKey === fileKey)
    if (existing) {
      if (activeId !== existing.id) setActiveId(existing.id)
    } else {
      // Check if this fileKey change came from AI creating a file (not user clicking sidebar)
      const active = conversations.find(c => c.id === activeId)
      if (active && !active.fileKey && active.messages.length > 0 && aiCreatedFileRef.current) {
        // AI just created a file in this conversation — bind it (don't create new conversation)
        aiCreatedFileRef.current = false
        setConversations(prev => prev.map(c =>
          c.id === activeId ? { ...c, fileKey, title: fileKey.replace('.py', '') } : c
        ))
      } else {
        // Truly new file opened by user — create new conversation
        const label = fileKey.replace('.py', '')
        const conv: Conversation = {
          id: newId(),
          title: label,
          messages: [],
          createdAt: Date.now(),
          updatedAt: Date.now(),
          fileKey,
        }
        setConversations(prev => [conv, ...prev])
        setActiveId(conv.id)
      }
    }
  }, [fileKey])  // eslint-disable-line react-hooks/exhaustive-deps

  const createConversation = (boundFileKey?: string) => {
    const conv: Conversation = {
      id: newId(),
      title: boundFileKey ? boundFileKey.replace('.py', '') : '新对话',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
      fileKey: boundFileKey,
    }
    setConversations(prev => [conv, ...prev])
    setActiveId(conv.id)
    setShowList(false)
  }

  const deleteConversation = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (activeId === id) {
      const remaining = conversations.filter(c => c.id !== id)
      setActiveId(remaining.length > 0 ? remaining[0].id : '')
    }
    setConversations(prev => prev.filter(c => c.id !== id))
  }

  const clearAllConversations = () => {
    if (!confirm('确定清空所有对话历史？此操作不可撤销。')) return
    setConversations([])
    setActiveId('')
    localStorage.removeItem(STORAGE_KEY)
    localStorage.removeItem(ACTIVE_KEY)
  }

  const switchConversation = (id: string) => {
    setActiveId(id)
    setShowList(false)
  }

  const sendMessage = async () => {
    if (!input.trim() || streaming) return

    const userMsg: ChatMsg = { role: 'user', content: input.trim() }

    // Build API messages BEFORE any state updates (avoid stale closure)
    let currentId = activeId
    const existingMsgs = currentId
      ? (conversations.find(c => c.id === currentId)?.messages || [])
      : []
    const allMsgs = [...existingMsgs, userMsg]
    const apiMessages = allMsgs
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .map(m => ({ role: m.role, content: m.content }))

    // Auto-create conversation if none active
    if (!currentId) {
      const conv: Conversation = {
        id: newId(),
        title: titleFromMsg(input.trim()),
        messages: [userMsg],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }
      setConversations(prev => [conv, ...prev])
      currentId = conv.id
      setActiveId(conv.id)
    } else {
      // Append user message to existing conversation
      setConversations(prev => prev.map(c => {
        if (c.id !== currentId) return c
        const updated = { ...c, messages: [...c.messages, userMsg], updatedAt: Date.now() }
        if (c.messages.length === 0) updated.title = titleFromMsg(input.trim())
        return updated
      }))
    }

    setInput('')
    setStreaming(true)

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
                if ((data.name === 'create_strategy' || data.name === 'update_strategy'
                    || data.name === 'create_portfolio_strategy' || data.name === 'create_cross_factor') && onCodeUpdate) {
                  try {
                    const r = typeof data.result === 'string' ? JSON.parse(data.result) : data.result
                    if (r.success && r.path) {
                      const bareName = r.path.replace('strategies/', '').replace('portfolio_strategies/', '').replace('cross_factors/', '').replace('factors/', '')
                      // Detect kind from path prefix for CodeEditor
                      const detectedKind = r.path.startsWith('portfolio_strategies/') ? 'portfolio_strategy'
                        : r.path.startsWith('cross_factors/') ? 'cross_factor'
                        : r.path.startsWith('factors/') ? 'factor' : 'strategy'
                      // V2.12.2 codex: fileKey must match CodeEditor's
                      // `${kind}:${filename}` format, otherwise useEffect[fileKey]
                      // in this component creates a duplicate conversation when
                      // the user later opens the same file from the sidebar.
                      // Prior version used bare filename, producing two
                      // conversations per AI-created file.
                      const boundKey = `${detectedKind}:${bareName}`
                      // Bind current conversation to the new file BEFORE updating fileKey
                      // This prevents useEffect[fileKey] from creating a new conversation.
                      // V2.12.2 codex: use `targetId` captured at message send
                      // time, not `activeId` from closure. If user switches
                      // conversations while the stream is active, activeId
                      // points to the new conversation and binds the file
                      // to the wrong one.
                      aiCreatedFileRef.current = true  // Mark: fileKey change is from AI, not user click
                      setConversations(prev => prev.map(c =>
                        c.id === targetId ? { ...c, fileKey: boundKey, title: bareName.replace('.py', '') } : c
                      ))
                      // V2.12.2 codex: on fetch success, switch filename +
                      // kind + code atomically so editor/filename/kind stay
                      // consistent. On fetch failure, do NOT touch filename
                      // or kind — prior version split-updated (filename
                      // changed, code stayed), leaving the editor pointing
                      // at the new file but displaying the previous file's
                      // source. Subsequent save/run would operate on the
                      // wrong content. Instead, refresh the sidebar and
                      // append a user-visible warning instructing them to
                      // open the file manually from the sidebar.
                      const fetchedFile = await fetch(`/api/code/files/${bareName}?kind=${detectedKind}`)
                        .then(resp => resp.ok ? resp.json() : null)
                        .catch(() => null)
                      if (fetchedFile && fetchedFile.code) {
                        onCodeUpdate(fetchedFile.code, bareName, detectedKind)
                      } else {
                        // Only refresh the sidebar (file list) — do not
                        // touch filename / kind / code.
                        onCodeUpdate(undefined, undefined, undefined)
                        updateMsgs(prev => [...prev, {
                          role: 'assistant',
                          content: `⚠️ 文件 ${bareName} 已创建但内容读取失败，请从左侧文件列表手动打开。`
                        }])
                      }
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
          <button onClick={() => createConversation(fileKey)}
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
                <div className="text-xs truncate flex items-center gap-1" style={{ color: 'var(--text-primary)' }}>
                  {c.fileKey && <span style={{ color: 'var(--color-accent)', fontSize: '9px' }}>📎</span>}
                  {c.title}
                </div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  {c.messages.length} 条 · {c.fileKey || '未绑定文件'}
                </div>
              </div>
              <button onClick={e => deleteConversation(c.id, e)}
                className="opacity-0 group-hover:opacity-100 text-xs ml-2 px-1 rounded"
                style={{ color: '#ef4444' }}>x</button>
            </div>
          ))}
          {conversations.length > 0 && (
            <div className="px-3 py-2 text-center" style={{ borderTop: '1px solid var(--border)' }}>
              <button onClick={clearAllConversations} className="text-xs px-2 py-0.5 rounded"
                style={{ color: '#ef4444', border: '1px solid #ef4444' }}>清空所有对话</button>
            </div>
          )}
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
          m.role === 'tool' ? (
            <div key={i} className="flex justify-start">
              <div className="text-xs px-3 py-1 rounded" style={{ color: 'var(--text-secondary)', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                {m.toolName && _toolLabel(m.toolName, m.content)}
              </div>
            </div>
          ) : (
            <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className="rounded-lg px-3 py-2 text-xs" style={{
                backgroundColor: m.role === 'user' ? 'var(--color-accent)' : 'var(--bg-secondary)',
                color: m.role === 'user' ? '#fff' : 'var(--text-primary)',
                maxWidth: '90%', overflow: 'hidden',
              }}>
                <pre className="whitespace-pre-wrap font-sans" style={{ overflowWrap: 'break-word', wordBreak: 'break-word', maxWidth: '100%' }}>
                  {m.content}
                </pre>
              </div>
            </div>
          )
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
