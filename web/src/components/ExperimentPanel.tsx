import { useState, useEffect } from 'react'
import { listExperiments, submitExperiment, listStrategies, deleteExperiment, cleanupExperiments } from '../api'
import type { ExperimentRun, StrategyInfo, GateReason } from '../types'
import CandidateSearch from './CandidateSearch'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

export default function ExperimentPanel() {
  const [subTab, setSubTab] = useState<'single' | 'search'>('single')
  const [runs, setRuns] = useState<ExperimentRun[]>([])
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [detail, setDetail] = useState<ExperimentRun | null>(null)

  // Form state
  const [strategyName, setStrategyName] = useState('')
  const [params, setParams] = useState<Record<string, number>>({})
  const [symbol, setSymbol] = useState('000001.SZ')
  const [market] = useState('cn_stock')
  const [period, setPeriod] = useState('daily')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [runWfo, setRunWfo] = useState(true)
  const [wfoSplits, setWfoSplits] = useState(3)

  useEffect(() => {
    loadRuns()
    listStrategies().then(r => {
      setStrategies(r.data)
      if (r.data.length > 0) {
        setStrategyName(r.data[0].name)
        const defaults: Record<string, number> = {}
        for (const [k, v] of Object.entries(r.data[0].parameters)) defaults[k] = (v as any).default
        setParams(defaults)
      }
    }).catch(() => {})
  }, [])

  const loadRuns = () => {
    setLoading(true)
    listExperiments().then(r => setRuns(r.data)).catch(() => {}).finally(() => setLoading(false))
  }

  const handleStrategyChange = (name: string) => {
    setStrategyName(name)
    const s = strategies.find(s => s.name === name)
    if (s) {
      const defaults: Record<string, number> = {}
      for (const [k, v] of Object.entries(s.parameters)) defaults[k] = (v as any).default
      setParams(defaults)
    }
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const res = await submitExperiment({
        strategy_name: strategyName,
        strategy_params: params,
        symbol, market, period,
        start_date: startDate,
        end_date: endDate,
        run_wfo: runWfo,
        ...(runWfo ? { wfo_n_splits: wfoSplits } : {}),
      })
      if (res.data?.status === 'duplicate') {
        alert(`Duplicate: this experiment already has a completed run (${res.data.existing_run_id || res.data.spec_id})`)
      }
      loadRuns()
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Experiment failed')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (runId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('Delete this experiment run?')) return
    try {
      await deleteExperiment(runId)
      if (detail?.run_id === runId) setDetail(null)
      loadRuns()
    } catch { alert('Delete failed') }
  }

  const handleCleanup = async () => {
    const keepStr = prompt('Keep most recent N runs (delete older ones):', '200')
    if (!keepStr) return
    const keep = parseInt(keepStr, 10)
    if (isNaN(keep) || keep < 1) return
    try {
      const res = await cleanupExperiments(keep)
      alert(`Cleaned up ${res.data.deleted} old runs`)
      loadRuns()
    } catch { alert('Cleanup failed') }
  }

  const parseGateReasons = (r: ExperimentRun): GateReason[] => {
    if (!r.gate_reasons) return []
    if (typeof r.gate_reasons === 'string') {
      try { return JSON.parse(r.gate_reasons) } catch { return [] }
    }
    return r.gate_reasons as GateReason[]
  }

  return (
    <div className="p-6 space-y-6">
      {/* Sub-tab */}
      <div className="flex gap-2">
        {(['single', 'search'] as const).map(t => (
          <button key={t} onClick={() => setSubTab(t)}
            className="px-3 py-1.5 rounded text-sm font-medium"
            style={{
              backgroundColor: subTab === t ? 'var(--color-accent)' : 'var(--bg-secondary)',
              color: subTab === t ? '#fff' : 'var(--text-secondary)',
              border: '1px solid var(--border)',
            }}>
            {t === 'single' ? 'Single Run' : 'Param Search'}
          </button>
        ))}
      </div>

      {subTab === 'search' ? <CandidateSearch /> : <>
      {/* Submit Form */}
      <div className="rounded-lg p-4 space-y-3" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>New Experiment</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Strategy</label>
            <select value={strategyName} onChange={e => handleStrategyChange(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              {strategies.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Symbol</label>
            <input value={symbol} onChange={e => setSymbol(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Period</label>
            <select value={period} onChange={e => setPeriod(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Start</label>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>End</label>
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle} />
          </div>
        </div>
        {/* WFO Controls */}
        <div className="flex gap-4 items-center flex-wrap">
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="checkbox" checked={runWfo} onChange={e => setRunWfo(e.target.checked)} />
            Walk-Forward
          </label>
          {runWfo && (
            <div className="flex items-center gap-1.5">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Splits</label>
              <input type="number" value={wfoSplits} min={2} max={20}
                onChange={e => setWfoSplits(Number(e.target.value))}
                className="w-16 px-2 py-1 rounded text-sm" style={inputStyle} />
            </div>
          )}
        </div>
        {/* Strategy Params */}
        {Object.keys(params).length > 0 && (
          <div className="flex gap-3 flex-wrap">
            {Object.entries(params).map(([k, v]) => (
              <div key={k}>
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{k}</label>
                <input type="number" value={v} onChange={e => setParams({...params, [k]: Number(e.target.value)})}
                  className="w-24 px-2 py-1.5 rounded text-sm" style={inputStyle} />
              </div>
            ))}
          </div>
        )}
        <button onClick={handleSubmit} disabled={submitting}
          className="px-4 py-2 rounded text-sm font-medium"
          style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: submitting ? 0.5 : 1 }}>
          {submitting ? 'Running...' : 'Run Experiment'}
        </button>
      </div>

      {/* Runs Table */}
      <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
        <div className="px-4 py-3 flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)' }}>
          <h3 className="font-semibold" style={{ color: 'var(--text-primary)' }}>Experiment Runs</h3>
          <div className="flex gap-2">
            <button onClick={handleCleanup} className="text-xs px-2 py-1 rounded" style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              Cleanup
            </button>
            <button onClick={loadRuns} className="text-xs px-2 py-1 rounded" style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              {loading ? '...' : 'Refresh'}
            </button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" style={{ color: 'var(--text-primary)' }}>
            <thead>
              <tr style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                <th className="px-3 py-2 text-left">Time</th>
                <th className="px-3 py-2 text-left">Strategy</th>
                <th className="px-3 py-2 text-left">Symbol</th>
                <th className="px-3 py-2 text-right">Sharpe</th>
                <th className="px-3 py-2 text-right">Return</th>
                <th className="px-3 py-2 text-right">MaxDD</th>
                <th className="px-3 py-2 text-right">Trades</th>
                <th className="px-3 py-2 text-center">Gate</th>
                <th className="px-3 py-2 text-right">Duration</th>
                <th className="px-3 py-2 w-8"></th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 && (
                <tr><td colSpan={10} className="px-3 py-8 text-center" style={{ color: 'var(--text-secondary)' }}>
                  No experiments yet
                </td></tr>
              )}
              {runs.map(r => (
                <tr key={r.run_id} className="cursor-pointer hover:opacity-80" onClick={() => setDetail(detail?.run_id === r.run_id ? null : r)}
                  style={{ borderTop: '1px solid var(--border)' }}>
                  <td className="px-3 py-2 text-xs">{r.created_at?.slice(0, 16)}</td>
                  <td className="px-3 py-2">{r.strategy_name}</td>
                  <td className="px-3 py-2">{r.symbol}</td>
                  <td className="px-3 py-2 text-right">{r.sharpe_ratio?.toFixed(2) ?? '-'}</td>
                  <td className="px-3 py-2 text-right" style={{ color: (r.total_return ?? 0) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                    {r.total_return != null ? (r.total_return * 100).toFixed(1) + '%' : '-'}
                  </td>
                  <td className="px-3 py-2 text-right">{r.max_drawdown != null ? (r.max_drawdown * 100).toFixed(1) + '%' : '-'}</td>
                  <td className="px-3 py-2 text-right">{r.trade_count}</td>
                  <td className="px-3 py-2 text-center">
                    <span className="px-2 py-0.5 rounded text-xs font-medium"
                      style={{ backgroundColor: r.gate_passed ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                               color: r.gate_passed ? '#22c55e' : '#ef4444' }}>
                      {r.gate_passed ? 'PASS' : 'FAIL'}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-xs" style={{ color: 'var(--text-secondary)' }}>
                    {r.duration_ms?.toFixed(0)}ms
                  </td>
                  <td className="px-3 py-2 text-center">
                    <button onClick={e => handleDelete(r.run_id, e)}
                      className="text-xs px-1.5 py-0.5 rounded hover:opacity-80"
                      style={{ color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}>
                      x
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Detail Panel */}
      {detail && (
        <div className="rounded-lg p-4 space-y-3" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <div className="flex justify-between items-center">
            <h3 className="font-semibold" style={{ color: 'var(--text-primary)' }}>
              {detail.strategy_name} / {detail.symbol} — Gate {detail.gate_passed ? 'PASS' : 'FAIL'}
            </h3>
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              run_id: {detail.run_id} | commit: {detail.code_commit}
            </span>
          </div>

          {/* Gate Reasons */}
          <div className="space-y-1">
            {parseGateReasons(detail).map((g, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <span style={{ color: g.passed ? '#22c55e' : '#ef4444' }}>{g.passed ? '✓' : '✗'}</span>
                <span style={{ color: 'var(--text-primary)' }}>{g.message}</span>
              </div>
            ))}
          </div>

          {/* Metrics Grid */}
          <div className="grid grid-cols-3 md:grid-cols-6 gap-3 text-sm">
            {[
              { label: 'Sharpe', value: detail.sharpe_ratio?.toFixed(2) },
              { label: 'Return', value: detail.total_return != null ? (detail.total_return * 100).toFixed(1) + '%' : '-' },
              { label: 'MaxDD', value: detail.max_drawdown != null ? (detail.max_drawdown * 100).toFixed(1) + '%' : '-' },
              { label: 'Trades', value: String(detail.trade_count) },
              { label: 'Win Rate', value: detail.win_rate != null ? (detail.win_rate * 100).toFixed(0) + '%' : '-' },
              { label: 'p-value', value: detail.p_value?.toFixed(3) },
              { label: 'OOS Sharpe', value: detail.oos_sharpe?.toFixed(2) ?? '-' },
              { label: 'Overfitting', value: detail.overfitting_score?.toFixed(2) ?? '-' },
              { label: 'Significant', value: detail.is_significant ? 'Yes' : 'No' },
            ].map(m => (
              <div key={m.label} className="rounded p-2" style={{ backgroundColor: 'var(--bg-primary)' }}>
                <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>{m.label}</div>
                <div className="font-medium" style={{ color: 'var(--text-primary)' }}>{m.value ?? '-'}</div>
              </div>
            ))}
          </div>

          {detail.error && (
            <div className="p-2 rounded text-sm" style={{ backgroundColor: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>
              Error: {detail.error}
            </div>
          )}
        </div>
      )}
      </>}
    </div>
  )
}
