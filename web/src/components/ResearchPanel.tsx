import { useState, useEffect, useRef, useCallback } from 'react'

interface ResearchTask {
  task_id: string
  goal: string
  config: string
  status: string
  created_at: string
  completed_at: string | null
  stop_reason: string
  summary: string
  error: string
  iterations?: ResearchIteration[]
}

interface ResearchIteration {
  task_id: string
  iteration: number
  hypotheses: string
  strategies_tried: number
  strategies_passed: number
  best_sharpe: number
  analysis: string
  spec_ids: string
  created_at: string
}

interface SSEEvent {
  event: string
  data: Record<string, unknown>
}

export default function ResearchPanel() {
  const [tasks, setTasks] = useState<ResearchTask[]>([])
  const [selectedTask, setSelectedTask] = useState<ResearchTask | null>(null)
  const [events, setEvents] = useState<SSEEvent[]>([])
  const [streaming, setStreaming] = useState(false)
  const [loading, setLoading] = useState(false)

  // Form state
  const [goal, setGoal] = useState('')
  const [symbol, setSymbol] = useState('000001.SZ')
  const [startDate, setStartDate] = useState(() => {
    const d = new Date()
    d.setFullYear(d.getFullYear() - 3)
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
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { loadTasks() }, [loadTasks])

  const startResearch = async () => {
    if (!goal.trim()) return
    setLoading(true)
    try {
      const res = await fetch('/api/research/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
        // Start streaming
        streamTask(data.task_id)
      }
    } catch (e) {
      console.error('Start research failed:', e)
    } finally {
      setLoading(false)
    }
  }

  const streamTask = async (taskId: string) => {
    setStreaming(true)
    setEvents([])
    try {
      const res = await fetch(`/api/research/tasks/${taskId}/stream`)
      if (!res.ok || !res.body) { setStreaming(false); return }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
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
          } else if (line.startsWith('data: ') && eventType) {
            try {
              const data = JSON.parse(line.slice(6))
              setEvents(prev => [...prev, { event: eventType, data }])
            } catch { /* ignore */ }
            eventType = ''
          }
        }
      }
    } catch { /* ignore */ }
    setStreaming(false)
    loadTasks()
  }

  const cancelTask = async (taskId: string) => {
    await fetch(`/api/research/tasks/${taskId}/cancel`, { method: 'POST' })
    loadTasks()
  }

  const selectTask = async (taskId: string) => {
    try {
      const res = await fetch(`/api/research/tasks/${taskId}`)
      if (res.ok) setSelectedTask(await res.json())
    } catch { /* ignore */ }
  }

  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  const statusColor = (s: string) => {
    if (s === 'running') return '#3b82f6'
    if (s === 'completed') return '#22c55e'
    if (s === 'failed') return '#ef4444'
    return '#6b7280'
  }

  const statusLabel = (s: string) => {
    if (s === 'running') return '运行中'
    if (s === 'completed') return '已完成'
    if (s === 'failed') return '失败'
    if (s === 'cancelled') return '已取消'
    return s
  }

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
              <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>开始日期</label>
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
                className="px-3 py-1.5 rounded text-sm"
                style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
            </div>
            <div>
              <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>结束日期</label>
              <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
                className="px-3 py-1.5 rounded text-sm"
                style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
            </div>
            <button onClick={() => setShowAdvanced(!showAdvanced)}
              className="px-3 py-1.5 rounded text-sm"
              style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
              {showAdvanced ? '收起' : '高级设置'}
            </button>
            <button onClick={startResearch} disabled={!goal.trim() || loading}
              className="px-4 py-1.5 rounded text-sm font-medium"
              style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: (!goal.trim() || loading) ? 0.5 : 1 }}>
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

      {/* SSE Progress */}
      {(streaming || events.length > 0) && (
        <div className="rounded-lg p-4 mb-6" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <div className="flex justify-between items-center mb-3">
            <h3 className="font-semibold">{streaming ? '研究进行中...' : '研究日志'}</h3>
            {streaming && (
              <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: '#3b82f620', color: '#3b82f6' }}>
                实时
              </span>
            )}
          </div>
          <div className="space-y-1 max-h-64 overflow-y-auto text-sm font-mono"
            style={{ color: 'var(--text-secondary)' }}>
            {events.map((e, i) => (
              <div key={i} className="flex gap-2">
                <span style={{ color: e.event.includes('fail') || e.event === 'task_failed' ? '#ef4444' :
                  e.event.includes('success') || e.event === 'task_complete' ? '#22c55e' :
                  e.event === 'hypothesis' ? '#f59e0b' : '#8b949e', minWidth: '120px' }}>
                  [{e.event}]
                </span>
                <span>{JSON.stringify(e.data)}</span>
              </div>
            ))}
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
                style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}
                onClick={() => selectTask(t.task_id)}>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: statusColor(t.status) + '20', color: statusColor(t.status) }}>
                      {statusLabel(t.status)}
                    </span>
                    <span className="text-sm font-medium">{t.goal.substring(0, 60)}{t.goal.length > 60 ? '...' : ''}</span>
                  </div>
                  <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                    {t.created_at ? new Date(t.created_at).toLocaleString('zh-CN') : ''} {t.stop_reason ? `| ${t.stop_reason}` : ''}
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

      {/* Task Detail / Report */}
      {selectedTask && (
        <div className="rounded-lg p-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <div className="flex justify-between items-center mb-4">
            <h3 className="font-semibold">研究报告: {selectedTask.goal.substring(0, 40)}</h3>
            <button onClick={() => setSelectedTask(null)} className="text-sm"
              style={{ color: 'var(--text-secondary)' }}>关闭</button>
          </div>

          {/* Summary */}
          {selectedTask.summary && (
            <div className="p-3 rounded mb-4 text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
              {selectedTask.summary}
            </div>
          )}

          {/* Metrics */}
          <div className="grid grid-cols-4 gap-3 mb-4">
            {[
              { label: '状态', value: statusLabel(selectedTask.status) },
              { label: '停止原因', value: selectedTask.stop_reason || '-' },
              { label: '错误', value: selectedTask.error || '-' },
              { label: '迭代数', value: selectedTask.iterations?.length || 0 },
            ].map(m => (
              <div key={m.label} className="rounded p-2 text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>{m.label}</div>
                <div className="text-sm font-medium">{String(m.value)}</div>
              </div>
            ))}
          </div>

          {/* Iterations */}
          {selectedTask.iterations && selectedTask.iterations.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold mb-2">迭代历史</h4>
              <div className="space-y-2">
                {selectedTask.iterations.map(it => {
                  let hypotheses: string[] = []
                  try { hypotheses = JSON.parse(it.hypotheses || '[]') } catch { /* ignore */ }
                  let analysis: { direction?: string } = {}
                  try { analysis = JSON.parse(it.analysis || '{}') } catch { /* ignore */ }
                  return (
                    <div key={it.iteration} className="p-3 rounded text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                      <div className="flex justify-between items-center mb-2">
                        <span className="font-medium">第 {it.iteration + 1} 轮</span>
                        <span style={{ color: it.strategies_passed > 0 ? '#22c55e' : '#ef4444' }}>
                          {it.strategies_passed}/{it.strategies_tried} 通过
                          {it.best_sharpe > 0 ? ` | Sharpe ${it.best_sharpe.toFixed(2)}` : ''}
                        </span>
                      </div>
                      {hypotheses.length > 0 && (
                        <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>
                          假设: {hypotheses.map(h => h.substring(0, 40)).join(' | ')}
                        </div>
                      )}
                      {analysis.direction && (
                        <div className="text-xs" style={{ color: '#f59e0b' }}>
                          方向: {analysis.direction}
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
