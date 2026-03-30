import { useState, useEffect } from 'react'
import DatePicker from 'react-datepicker'
import 'react-datepicker/dist/react-datepicker.css'
import { listExperiments, submitExperiment, listStrategies, deleteExperiment, cleanupExperiments } from '../api'
import type { ExperimentRun, StrategyInfo, GateReason } from '../types'
import CandidateSearch from './CandidateSearch'
import DateBtn from './shared/DateBtn'

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
  const [startDate, setStartDate] = useState<Date>(new Date(2020, 0, 1))
  const [endDate, setEndDate] = useState<Date>(new Date(2024, 11, 31))
  const [runWfo, setRunWfo] = useState(true)
  const [wfoSplits, setWfoSplits] = useState(3)
  const [useMarketRules, setUseMarketRules] = useState(true)

  useEffect(() => {
    loadRuns()
    listStrategies().then(r => {
      const userStrategies = r.data.filter((s: StrategyInfo) => !s.name.startsWith('Research'))
      setStrategies(userStrategies)
      if (userStrategies.length > 0) {
        setStrategyName(userStrategies[0].name)
        const defaults: Record<string, number> = {}
        for (const [k, v] of Object.entries(r.data[0].parameters)) defaults[k] = (v as any).default
        setParams(defaults)
      }
    }).catch(() => {})
  }, [])

  const loadRuns = () => {
    setLoading(true)
    listExperiments().then(r => {
      // Filter out research agent experiments (strategy names starting with Research)
      const userRuns = r.data.filter((run: ExperimentRun) => !run.strategy_name?.startsWith('Research'))
      setRuns(userRuns)
    }).catch(() => {}).finally(() => setLoading(false))
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
      const toStr = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      const res = await submitExperiment({
        strategy_name: strategyName,
        strategy_params: params,
        symbol, market, period,
        start_date: toStr(startDate),
        end_date: toStr(endDate),
        run_wfo: runWfo,
        ...(runWfo ? { wfo_n_splits: wfoSplits } : {}),
        use_market_rules: useMarketRules,
      })
      if (res.data?.status === 'duplicate') {
        alert(`重复: 该实验已有完成的运行记录 (${res.data.existing_run_id || res.data.spec_id})`)
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
    if (!confirm('确认删除此实验记录?')) return
    try {
      await deleteExperiment(runId)
      if (detail?.run_id === runId) setDetail(null)
      loadRuns()
    } catch { alert('删除失败') }
  }

  const handleCleanup = async () => {
    const keepStr = prompt('保留最近 N 条记录(删除更旧的):', '200')
    if (!keepStr) return
    const keep = parseInt(keepStr, 10)
    if (isNaN(keep) || keep < 1) return
    try {
      const res = await cleanupExperiments(keep)
      alert(`已清理 ${res.data.deleted} 条旧记录`)
      loadRuns()
    } catch { alert('清理失败') }
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
            {t === 'single' ? '单次运行' : '参数搜索'}
          </button>
        ))}
      </div>

      {subTab === 'search' ? <CandidateSearch /> : <>
      {/* Submit Form */}
      <div className="rounded-lg p-4 space-y-3" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>新建实验</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>策略</label>
            <select value={strategyName} onChange={e => handleStrategyChange(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              {strategies.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>股票代码</label>
            <input value={symbol} onChange={e => setSymbol(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>周期</label>
            <select value={period} onChange={e => setPeriod(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              <option value="daily">日线</option>
              <option value="weekly">周线</option>
              <option value="monthly">月线</option>
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>开始</label>
            <DatePicker selected={startDate} dateFormat="yyyy-MM-dd"
              onChange={(d: Date | null) => { if (d) { setStartDate(d); if (d > endDate) setEndDate(d) } }}
              maxDate={endDate} showMonthDropdown showYearDropdown dropdownMode="select"
              customInput={<DateBtn />} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>结束</label>
            <DatePicker selected={endDate} dateFormat="yyyy-MM-dd"
              onChange={(d: Date | null) => { if (d) { setEndDate(d); if (d < startDate) setStartDate(d) } }}
              minDate={startDate} maxDate={new Date()} showMonthDropdown showYearDropdown dropdownMode="select"
              customInput={<DateBtn />} />
          </div>
        </div>
        {/* WFO + Market Rules Controls */}
        <div className="flex gap-4 items-center flex-wrap">
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="checkbox" checked={runWfo} onChange={e => setRunWfo(e.target.checked)} />
            前推验证
          </label>
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="checkbox" checked={useMarketRules} onChange={e => setUseMarketRules(e.target.checked)} />
            A股规则 (T+1 / 涨跌停10% / 整手100股)
          </label>
          {runWfo && (
            <div className="flex items-center gap-1.5">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>分割数</label>
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
        <button onClick={handleSubmit} disabled={submitting || !strategyName}
          className="px-4 py-2 rounded text-sm font-medium"
          style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: (submitting || !strategyName) ? 0.5 : 1 }}>
          {submitting ? '运行中...' : !strategyName ? '无可用策略' : '运行实验'}
        </button>
      </div>

      {/* Runs Table */}
      <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
        <div className="px-4 py-3 flex justify-between items-center" style={{ backgroundColor: 'var(--bg-secondary)' }}>
          <h3 className="font-semibold" style={{ color: 'var(--text-primary)' }}>实验记录</h3>
          <div className="flex gap-2">
            <button onClick={handleCleanup} className="text-xs px-2 py-1 rounded" style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              清理
            </button>
            <button onClick={loadRuns} className="text-xs px-2 py-1 rounded" style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
              {loading ? '...' : '刷新'}
            </button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" style={{ color: 'var(--text-primary)' }}>
            <thead>
              <tr style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                <th className="px-3 py-2 text-left">时间</th>
                <th className="px-3 py-2 text-left">策略</th>
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-right">Sharpe</th>
                <th className="px-3 py-2 text-right">收益</th>
                <th className="px-3 py-2 text-right">回撤</th>
                <th className="px-3 py-2 text-right">交易数</th>
                <th className="px-3 py-2 text-center">Gate</th>
                <th className="px-3 py-2 text-right">耗时</th>
                <th className="px-3 py-2 w-8"></th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 && (
                <tr><td colSpan={10} className="px-3 py-8 text-center" style={{ color: 'var(--text-secondary)' }}>
                  暂无实验记录
                </td></tr>
              )}
              {runs.map(r => (
                <tr key={r.run_id} className="cursor-pointer hover:opacity-80" onClick={() => setDetail(detail?.run_id === r.run_id ? null : r)}
                  style={{ borderTop: '1px solid var(--border)' }}>
                  <td className="px-3 py-2 text-xs">{r.created_at ? new Date(r.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'}</td>
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
              { label: '收益率', value: detail.total_return != null ? (detail.total_return * 100).toFixed(1) + '%' : '-' },
              { label: '最大回撤', value: detail.max_drawdown != null ? (detail.max_drawdown * 100).toFixed(1) + '%' : '-' },
              { label: '交易数', value: String(detail.trade_count) },
              { label: '胜率', value: detail.win_rate != null ? (detail.win_rate * 100).toFixed(0) + '%' : '-' },
              { label: 'p-value', value: detail.p_value?.toFixed(3) },
              { label: 'OOS Sharpe', value: detail.oos_sharpe?.toFixed(2) ?? '-' },
              { label: '过拟合分', value: detail.overfitting_score?.toFixed(2) ?? '-' },
              { label: '显著性', value: detail.is_significant ? '是' : '否' },
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
