import { useState, useEffect, useRef, useCallback } from 'react'

interface ResearchTask {
  task_id: string; goal: string; config: string; status: string
  created_at: string; completed_at: string | null
  stop_reason: string; summary: string; error: string
  iterations?: ResearchIteration[]
}
interface ResearchIteration {
  iteration: number; hypotheses: string; strategies_tried: number
  strategies_passed: number; best_sharpe: number; analysis: string
}
interface SSEEvent { event: string; data: Record<string, unknown> }

// Format SSE events as readable Chinese text
function formatEvent(e: SSEEvent): { icon: string; text: string; color: string } {
  const d = e.data
  switch (e.event) {
    case 'iteration_start':
      return { icon: '🔄', text: `第 ${(d.iteration as number) + 1}/${d.max_iterations} 轮开始`, color: '#3b82f6' }
    case 'hypothesis':
      return { icon: '💡', text: `假设 ${(d.index as number) + 1}/${d.total}: ${(d.text as string || '').substring(0, 80)}`, color: '#f59e0b' }
    case 'code_success':
      return { icon: '✓', text: `策略创建成功: ${d.class_name}`, color: '#22c55e' }
    case 'code_failed':
      return { icon: '✗', text: `策略创建失败: ${(d.error as string || '').substring(0, 60)}`, color: '#ef4444' }
    case 'batch_start':
      return { icon: '⚙', text: `开始回测 ${d.total_specs} 个策略...`, color: '#8b949e' }
    case 'batch_complete':
      return { icon: '📊', text: `回测完成: ${d.passed}/${d.executed} 通过 Gate${d.best_sharpe ? `, 最佳 Sharpe ${(d.best_sharpe as number).toFixed(2)}` : ''}`, color: (d.passed as number) > 0 ? '#22c55e' : '#f59e0b' }
    case 'analysis':
      return { icon: '🧠', text: `分析: ${d.direction}`, color: '#a78bfa' }
    case 'iteration_end':
      return { icon: '📋', text: `第 ${d.iteration} 轮结束 — 累计 ${d.cumulative_passed} 通过, ${d.cumulative_specs} 回测`, color: '#8b949e' }
    case 'task_complete':
      return { icon: '🎯', text: `研究完成! ${d.total_passed} 个策略通过, 停止原因: ${d.stop_reason}`, color: '#22c55e' }
    case 'task_cancelled':
      return { icon: '⏹', text: `已取消: ${d.stop_reason}`, color: '#6b7280' }
    case 'task_failed':
      return { icon: '❌', text: `失败: ${d.error}`, color: '#ef4444' }
    default:
      return { icon: '·', text: JSON.stringify(d), color: '#8b949e' }
  }
}

// Date quick-select buttons
const DATE_PRESETS = [
  { label: '近1年', years: 1 }, { label: '近3年', years: 3 },
  { label: '近5年', years: 5 }, { label: '近10年', years: 10 },
]

export default function ResearchPanel() {
  const [tasks, setTasks] = useState<ResearchTask[]>([])
  const [selectedTask, setSelectedTask] = useState<ResearchTask | null>(null)
  const [events, setEvents] = useState<SSEEvent[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamingTaskId, setStreamingTaskId] = useState('')
  const [loading, setLoading] = useState(false)

  const [goal, setGoal] = useState('')
  const [symbol, setSymbol] = useState('000001.SZ')
  const [startDate, setStartDate] = useState(() => {
    const d = new Date(); d.setFullYear(d.getFullYear() - 3)
    return d.toISOString().split('T')[0]
  })
  const [endDate, setEndDate] = useState(() => new Date().toISOString().split('T')[0])
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [maxIterations, setMaxIterations] = useState(10)
  const [maxSpecs, setMaxSpecs] = useState(500)
  const [nHypotheses, setNHypotheses] = useState(5)
  const [gateMinSharpe, setGateMinSharpe] = useState(0.5)
  const [gateMaxDrawdown, setGateMaxDrawdown] = useState(0.3)

  const eventsEndRef = useRef<HTMLDivElement>(null)

  const loadTasks = useCallback(async () => {
    try {
      const res = await fetch('/api/research/tasks')
      if (res.ok) setTasks(await res.json())
    } catch {}
  }, [])

  useEffect(() => { loadTasks() }, [loadTasks])

  const setDatePreset = (years: number) => {
    const end = new Date()
    const start = new Date(); start.setFullYear(start.getFullYear() - years)
    setStartDate(start.toISOString().split('T')[0])
    setEndDate(end.toISOString().split('T')[0])
  }

  const startResearch = async () => {
    if (!goal.trim()) return
    setLoading(true)
    try {
      const res = await fetch('/api/research/start', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          goal, symbol, start_date: startDate, end_date: endDate,
          max_iterations: maxIterations, max_specs: maxSpecs,
          n_hypotheses: nHypotheses,
          gate_min_sharpe: gateMinSharpe, gate_max_drawdown: gateMaxDrawdown,
        }),
      })
      if (res.ok) {
        const data = await res.json()
        setGoal('')
        await loadTasks()
        streamTask(data.task_id)
      } else if (res.status === 409) {
        alert('已有研究任务运行中，请等待完成或取消后重试')
      }
    } catch (e) {
      console.error('Start research failed:', e)
    } finally {
      setLoading(false)
    }
  }

  const streamTask = async (taskId: string) => {
    setStreaming(true)
    setStreamingTaskId(taskId)
    setEvents([])
    setSelectedTask(null)
    try {
      const res = await fetch(`/api/research/tasks/${taskId}/stream`)
      if (!res.ok || !res.body) { setStreaming(false); return }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = '', eventType = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ') && eventType) {
            try {
              const data = JSON.parse(line.slice(6))
              setEvents(prev => [...prev, { event: eventType, data }])
            } catch {}
            eventType = ''
          }
        }
      }
    } catch {}
    setStreaming(false)
    setStreamingTaskId('')
    loadTasks()
  }

  const cancelTask = async (taskId: string) => {
    try {
      await fetch(`/api/research/tasks/${taskId}/cancel`, { method: 'POST' })
      // Refresh immediately so status updates
      await loadTasks()
    } catch {}
  }

  const selectTask = async (taskId: string) => {
    // If clicking the already-streaming task, just scroll to progress
    if (streamingTaskId === taskId) return
    try {
      const res = await fetch(`/api/research/tasks/${taskId}`)
      if (res.ok) setSelectedTask(await res.json())
    } catch {}
  }

  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  const statusColor = (s: string) => ({ running: '#3b82f6', completed: '#22c55e', failed: '#ef4444', cancelled: '#6b7280' }[s] || '#6b7280')
  const statusLabel = (s: string) => ({ running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消' }[s] || s)

  return (
    <div className="p-6 max-w-6xl mx-auto" style={{ color: 'var(--text-primary)' }}>
      {/* Goal Form */}
      <div className="rounded-lg p-4 mb-6" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-lg font-semibold mb-3">自主研究任务</h3>
        <div className="flex flex-col gap-3">
          <textarea value={goal} onChange={e => setGoal(e.target.value)}
            placeholder="描述研究目标，例如：探索A股动量策略，目标 Sharpe > 1，最大回撤 < 20%"
            className="w-full p-3 rounded text-sm resize-none"
            style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)', minHeight: '80px' }} />
          <div className="flex gap-3 items-end flex-wrap">
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>股票代码</label>
              <input value={symbol} onChange={e => setSymbol(e.target.value)}
                className="px-3 py-1.5 rounded text-sm w-32"
                style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>日期范围</label>
              <div className="flex items-center gap-1">
                <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
                  className="px-2 py-1.5 rounded text-sm"
                  style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>至</span>
                <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
                  className="px-2 py-1.5 rounded text-sm"
                  style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
              </div>
            </div>
            <div className="flex gap-1">
              {DATE_PRESETS.map(p => (
                <button key={p.label} onClick={() => setDatePreset(p.years)}
                  className="text-xs px-2 py-1.5 rounded"
                  style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                  {p.label}
                </button>
              ))}
            </div>
            <button onClick={() => setShowAdvanced(!showAdvanced)}
              className="px-3 py-1.5 rounded text-sm"
              style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
              {showAdvanced ? '收起' : '高级设置'}
            </button>
            <button onClick={startResearch} disabled={!goal.trim() || loading || streaming}
              className="px-4 py-1.5 rounded text-sm font-medium"
              style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: (!goal.trim() || loading || streaming) ? 0.5 : 1 }}>
              {loading ? '启动中...' : '开始研究'}
            </button>
          </div>
          {showAdvanced && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 pt-2" style={{ borderTop: '1px solid var(--border)' }}>
              {[
                { label: '最大轮次', value: maxIterations, set: setMaxIterations, min: 1, max: 50 },
                { label: '回测上限', value: maxSpecs, set: setMaxSpecs, min: 10, max: 5000 },
                { label: '假设数/轮', value: nHypotheses, set: setNHypotheses, min: 1, max: 20 },
                { label: '最低Sharpe', value: gateMinSharpe, set: setGateMinSharpe, min: 0, max: 3, step: 0.1 },
                { label: '最大回撤', value: gateMaxDrawdown, set: setGateMaxDrawdown, min: 0.05, max: 0.8, step: 0.05 },
              ].map(f => (
                <div key={f.label}>
                  <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>{f.label}</label>
                  <input type="number" value={f.value} onChange={e => f.set(Number(e.target.value))}
                    min={f.min} max={f.max} step={(f as { step?: number }).step || 1}
                    className="px-2 py-1 rounded text-sm w-full"
                    style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Live Progress — formatted events */}
      {(streaming || events.length > 0) && (
        <div className="rounded-lg p-4 mb-6" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <div className="flex justify-between items-center mb-3">
            <h3 className="font-semibold">{streaming ? '研究进行中...' : '本次研究日志'}</h3>
            <div className="flex gap-2">
              {streaming && (
                <>
                  <span className="text-xs px-2 py-0.5 rounded animate-pulse" style={{ backgroundColor: '#3b82f620', color: '#3b82f6' }}>
                    实时
                  </span>
                  {streamingTaskId && (
                    <button onClick={() => cancelTask(streamingTaskId)}
                      className="text-xs px-2 py-0.5 rounded"
                      style={{ color: '#ef4444', border: '1px solid #ef444440' }}>
                      取消
                    </button>
                  )}
                </>
              )}
              {!streaming && events.length > 0 && (
                <button onClick={() => setEvents([])} className="text-xs px-2 py-0.5 rounded"
                  style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>清除</button>
              )}
            </div>
          </div>
          <div className="space-y-1.5 max-h-80 overflow-y-auto" style={{ color: 'var(--text-secondary)' }}>
            {events.map((e, i) => {
              const f = formatEvent(e)
              return (
                <div key={i} className="flex items-start gap-2 text-sm">
                  <span style={{ flexShrink: 0 }}>{f.icon}</span>
                  <span style={{ color: f.color }}>{f.text}</span>
                </div>
              )
            })}
            <div ref={eventsEndRef} />
          </div>
        </div>
      )}

      {/* Task List */}
      <div className="rounded-lg p-4 mb-6" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <div className="flex justify-between items-center mb-3">
          <h3 className="font-semibold">研究任务</h3>
          <button onClick={loadTasks} className="text-xs px-2 py-1 rounded"
            style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>刷新</button>
        </div>
        {tasks.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>暂无研究任务</p>
        ) : (
          <div className="space-y-2">
            {tasks.map(t => (
              <div key={t.task_id} className="flex items-center justify-between p-3 rounded cursor-pointer hover:opacity-80"
                style={{ backgroundColor: 'var(--bg-primary)', border: `1px solid ${selectedTask?.task_id === t.task_id ? 'var(--color-accent)' : 'var(--border)'}` }}
                onClick={() => selectTask(t.task_id)}>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: statusColor(t.status) + '20', color: statusColor(t.status) }}>
                      {statusLabel(t.status)}
                    </span>
                    <span className="text-sm font-medium">{t.goal.substring(0, 60)}{t.goal.length > 60 ? '...' : ''}</span>
                  </div>
                  <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                    {t.created_at ? new Date(t.created_at).toLocaleString('zh-CN') : ''}
                    {t.stop_reason ? ` · ${t.stop_reason}` : ''}
                  </div>
                </div>
                {t.status === 'running' && (
                  <button onClick={e => { e.stopPropagation(); cancelTask(t.task_id) }}
                    className="text-xs px-2 py-1 rounded" style={{ color: '#ef4444', border: '1px solid #ef444440' }}>
                    取消
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Task Detail / Report — from DB, persistent */}
      {selectedTask && (
        <div className="rounded-lg p-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <div className="flex justify-between items-center mb-4">
            <h3 className="font-semibold">研究报告</h3>
            <button onClick={() => setSelectedTask(null)} className="text-xs px-2 py-0.5 rounded"
              style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>关闭</button>
          </div>

          {/* Summary */}
          {selectedTask.summary && (
            <div className="p-3 rounded mb-4 text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
              <div className="text-xs mb-1 font-medium" style={{ color: 'var(--text-secondary)' }}>AI 总结</div>
              {selectedTask.summary}
            </div>
          )}

          {/* Metrics grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            {[
              { label: '状态', value: statusLabel(selectedTask.status), color: statusColor(selectedTask.status) },
              { label: '停止原因', value: selectedTask.stop_reason || '-' },
              { label: '迭代轮数', value: selectedTask.iterations?.length || 0 },
              { label: '总策略数', value: selectedTask.iterations?.reduce((s, it) => s + it.strategies_tried, 0) || 0 },
            ].map(m => (
              <div key={m.label} className="rounded p-2 text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>{m.label}</div>
                <div className="text-sm font-medium" style={{ color: (m as { color?: string }).color || 'var(--text-primary)' }}>{String(m.value)}</div>
              </div>
            ))}
          </div>

          {/* Error */}
          {selectedTask.error && (
            <div className="p-2 rounded mb-4 text-xs" style={{ backgroundColor: '#7f1d1d20', color: '#ef4444', border: '1px solid #ef444440' }}>
              {selectedTask.error}
            </div>
          )}

          {/* Iterations */}
          {selectedTask.iterations && selectedTask.iterations.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold mb-2">迭代历史</h4>
              <div className="space-y-2">
                {selectedTask.iterations.map(it => {
                  let hypotheses: string[] = []
                  try { hypotheses = JSON.parse(it.hypotheses || '[]') } catch {}
                  let analysis: { direction?: string; suggestions?: string[] } = {}
                  try { analysis = JSON.parse(it.analysis || '{}') } catch {}
                  return (
                    <div key={it.iteration} className="p-3 rounded text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                      <div className="flex justify-between items-center mb-2">
                        <span className="font-medium">第 {it.iteration + 1} 轮</span>
                        <div className="flex items-center gap-2">
                          <span className="text-xs px-1.5 py-0.5 rounded" style={{
                            backgroundColor: it.strategies_passed > 0 ? '#22c55e20' : '#ef444420',
                            color: it.strategies_passed > 0 ? '#22c55e' : '#ef4444'
                          }}>
                            {it.strategies_passed}/{it.strategies_tried} 通过
                          </span>
                          {it.best_sharpe > 0 && (
                            <span className="text-xs" style={{ color: '#f59e0b' }}>Sharpe {it.best_sharpe.toFixed(2)}</span>
                          )}
                        </div>
                      </div>
                      {hypotheses.length > 0 && (
                        <div className="mb-1">
                          {hypotheses.map((h, hi) => (
                            <div key={hi} className="text-xs flex items-start gap-1 mb-0.5" style={{ color: 'var(--text-secondary)' }}>
                              <span style={{ color: '#f59e0b', flexShrink: 0 }}>💡</span>
                              <span>{h.length > 80 ? h.substring(0, 80) + '...' : h}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {analysis.direction && (
                        <div className="text-xs flex items-start gap-1" style={{ color: '#a78bfa' }}>
                          <span style={{ flexShrink: 0 }}>🧠</span>
                          <span>{analysis.direction}</span>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
