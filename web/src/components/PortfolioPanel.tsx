import { useState, useEffect, useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { listPortfolioStrategies, runPortfolioBacktest, listPortfolioRuns, deletePortfolioRun } from '../api'
import BacktestSettings, { DEFAULT_SETTINGS } from './BacktestSettings'
import type { BacktestSettingsValue } from './BacktestSettings'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

interface PortfolioMetrics {
  total_return?: number; annualized_return?: number; sharpe_ratio?: number
  max_drawdown?: number; trade_count?: number; turnover_per_rebalance?: number
  annualized_volatility?: number; n_rebalances?: number
}

interface PortfolioRunResult {
  run_id: string; metrics: PortfolioMetrics; equity_curve: number[]
  benchmark_curve: number[]; dates: string[]; trades: any[]; rebalance_dates: string[]
  symbols_fetched?: number; symbols_skipped?: string[]
}

interface HistoryRun {
  run_id: string; strategy_name: string; start_date: string; end_date: string
  freq: string; metrics: PortfolioMetrics; trade_count: number; created_at: string
}

interface ParamSchema {
  type: string; default: any; min?: number; max?: number; label?: string
  options?: string[]  // for select / multi_select types
}

export default function PortfolioPanel() {
  const [strategies, setStrategies] = useState<{ name: string; description: string; parameters: Record<string, ParamSchema> }[]>([])
  const [factors, setFactors] = useState<string[]>([])
  const [selected, setSelected] = useState('')
  const [symbols, setSymbols] = useState('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [freq, setFreq] = useState('monthly')
  const [strategyParams, setStrategyParams] = useState<Record<string, any>>({})
  const [settings, setSettings] = useState<BacktestSettingsValue>(DEFAULT_SETTINGS)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<PortfolioRunResult | null>(null)
  const [history, setHistory] = useState<HistoryRun[]>([])
  const [tab, setTab] = useState<'run' | 'history'>('run')

  // Current strategy's parameter schema
  const currentSchema = useMemo(() => {
    const s = strategies.find(s => s.name === selected)
    return s?.parameters || {}
  }, [strategies, selected])

  // Initialize params from schema defaults when strategy changes
  useEffect(() => {
    const defaults: Record<string, any> = {}
    for (const [key, schema] of Object.entries(currentSchema)) {
      defaults[key] = schema.default
    }
    setStrategyParams(defaults)
  }, [currentSchema])

  useEffect(() => {
    listPortfolioStrategies().then(r => {
      const data = r.data
      setStrategies(data.strategies || [])
      setFactors(data.available_factors || [])
      if (data.strategies?.length > 0 && !selected) setSelected(data.strategies[0].name)
    }).catch(() => {})
    loadHistory()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const loadHistory = () => {
    listPortfolioRuns(20).then(r => setHistory(r.data || [])).catch(() => {})
  }

  const handleDeleteRun = async (runId: string) => {
    if (!confirm('确认删除此回测记录?')) return
    try {
      await deletePortfolioRun(runId)
      loadHistory()
    } catch {}
  }

  const updateParam = (key: string, value: any) => {
    setStrategyParams(prev => ({ ...prev, [key]: value }))
  }

  const handleRun = async () => {
    setLoading(true); setResult(null)
    try {
      const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
      const res = await runPortfolioBacktest({
        strategy_name: selected, symbols: symbolList,
        start_date: startDate, end_date: endDate, freq,
        strategy_params: strategyParams,
        initial_cash: settings.initial_cash,
        buy_commission_rate: settings.buy_commission_rate,
        sell_commission_rate: settings.sell_commission_rate,
        min_commission: settings.min_commission,
        stamp_tax_rate: settings.stamp_tax_rate,
        slippage_rate: settings.slippage_rate,
        lot_size: settings.lot_size,
        limit_pct: settings.limit_pct,
        benchmark_symbol: settings.benchmark,
      })
      setResult(res.data)
      loadHistory()
    } catch (e: any) {
      alert(e?.response?.data?.detail || JSON.stringify(e?.response?.data) || 'Failed')
    } finally { setLoading(false) }
  }

  const fmt = (v: number | null | undefined, pct = false) => {
    if (v == null) return '-'
    return pct ? `${(v * 100).toFixed(2)}%` : v.toFixed(4)
  }

  // Render a single param input based on schema type
  const renderParamInput = (key: string, schema: ParamSchema) => {
    const label = schema.label || key
    const value = strategyParams[key] ?? schema.default

    if (schema.type === 'select') {
      // Use schema.options if provided, otherwise fall back to available_factors
      const options: string[] = schema.options ?? (factors.length > 0 ? factors : [String(schema.default)])
      return (
        <div key={key} className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
          <select value={value} onChange={e => updateParam(key, e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
            {options.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      )
    }

    if (schema.type === 'multi_select') {
      // Use schema.options if provided, otherwise fall back to available_factors
      const options: string[] = schema.options ?? (factors.length > 0 ? factors : [])
      const selected_vals: string[] = Array.isArray(value) ? value : [String(value)]
      return (
        <div key={key} className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
          <div className="flex flex-wrap gap-1">
            {options.map(o => (
              <button key={o} onClick={() => {
                const cur = Array.isArray(strategyParams[key]) ? [...strategyParams[key]] : []
                if (cur.includes(o)) updateParam(key, cur.filter(x => x !== o))
                else updateParam(key, [...cur, o])
              }}
                className="text-xs px-2 py-0.5 rounded"
                style={{ backgroundColor: selected_vals.includes(o) ? 'var(--color-accent)' : 'var(--bg-primary)',
                         color: selected_vals.includes(o) ? '#fff' : 'var(--text-secondary)',
                         border: '1px solid var(--border)' }}>
                {o}
              </button>
            ))}
          </div>
        </div>
      )
    }

    if (schema.type === 'int') {
      return (
        <div key={key} className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
          <input type="number" value={value} min={schema.min} max={schema.max}
            onChange={e => { const v = parseInt(e.target.value); updateParam(key, isNaN(v) ? schema.default : v) }}
            className="px-3 py-1.5 rounded text-sm w-20" style={inputStyle} />
        </div>
      )
    }

    if (schema.type === 'float') {
      return (
        <div key={key} className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
          <input type="number" value={value} min={schema.min} max={schema.max} step={0.01}
            onChange={e => { const v = parseFloat(e.target.value); updateParam(key, isNaN(v) ? schema.default : v) }}
            className="px-3 py-1.5 rounded text-sm w-24" style={inputStyle} />
        </div>
      )
    }

    // Fallback: text input
    return (
      <div key={key} className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
        <input type="text" value={value} onChange={e => updateParam(key, e.target.value)}
          className="px-3 py-1.5 rounded text-sm w-32" style={inputStyle} />
      </div>
    )
  }

  const equityOption = result ? {
    backgroundColor: '#0d1117',
    title: { text: '组合净值曲线', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' as const },
    legend: { data: ['组合', settings.benchmark ? `基准(${settings.benchmark})` : '基准(现金)'], textStyle: { color: '#8b949e' }, top: 25 },
    grid: { left: 70, right: 20, top: 55, bottom: 30 },
    xAxis: { type: 'category' as const, data: result.dates.map(d => d.slice(0, 10)), axisLabel: { color: '#8b949e', rotate: 30, fontSize: 9 } },
    yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
    series: [
      { name: '组合', type: 'line' as const, data: result.equity_curve, lineStyle: { color: '#2563eb' }, showSymbol: false },
      { name: settings.benchmark ? `基准(${settings.benchmark})` : '基准(现金)', type: 'line' as const, data: result.benchmark_curve, lineStyle: { color: '#8b949e', type: 'dashed' as const }, showSymbol: false },
    ],
  } : null

  const metricLabels: Record<string, string> = {
    total_return: '总收益', annualized_return: '年化收益', sharpe_ratio: '夏普比率',
    sortino_ratio: 'Sortino', max_drawdown: '最大回撤', max_drawdown_duration: '回撤持续(天)',
    benchmark_return: '基准收益', alpha: 'Alpha', beta: 'Beta',
    trade_count: '交易次数', turnover_per_rebalance: '换手率/次',
    annualized_volatility: '年化波动', n_rebalances: '换仓次数',
  }

  const currentDesc = strategies.find(s => s.name === selected)?.description || ''

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex gap-2 mb-4">
        <button onClick={() => setTab('run')} className={`px-4 py-1.5 rounded text-sm ${tab === 'run' ? 'bg-blue-600 text-white' : ''}`} style={tab !== 'run' ? inputStyle : {}}>组合回测</button>
        <button onClick={() => setTab('history')} className={`px-4 py-1.5 rounded text-sm ${tab === 'history' ? 'bg-blue-600 text-white' : ''}`} style={tab !== 'history' ? inputStyle : {}}>历史记录 ({history.length})</button>
      </div>

      {tab === 'run' && (
        <>
          <div className="p-4 rounded mb-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
            <h3 className="text-sm font-medium mb-3">组合回测配置</h3>
            <div className="flex flex-wrap gap-3 items-end mb-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>策略</label>
                <select value={selected} onChange={e => setSelected(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
                  {strategies.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
                </select>
              </div>
              {/* Dynamic strategy parameters from schema */}
              {Object.entries(currentSchema).map(([key, schema]) => renderParamInput(key, schema))}
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>换仓频率</label>
                <select value={freq} onChange={e => setFreq(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
                  <option value="daily">日度</option>
                  <option value="weekly">周度</option>
                  <option value="monthly">月度</option>
                  <option value="quarterly">季度</option>
                </select>
              </div>
            </div>
            {currentDesc && (
              <div className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>{currentDesc}</div>
            )}
            <div className="flex flex-wrap gap-3 items-end mb-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>开始日期</label>
                <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle} />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>结束日期</label>
                <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle} />
              </div>
            </div>
            <div className="mb-3">
              <BacktestSettings value={settings} onChange={setSettings} />
            </div>
            <div className="mb-3">
              <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>标的池 (逗号分隔)</label>
              <textarea value={symbols} onChange={e => setSymbols(e.target.value)} rows={2} className="w-full px-3 py-1.5 rounded text-sm font-mono" style={inputStyle} />
            </div>
            <button onClick={handleRun} disabled={loading} className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: loading ? '#30363d' : 'var(--color-accent)' }}>
              {loading ? '运行中...' : '运行组合回测'}
            </button>
          </div>

          {result && (
            <div className="p-4 rounded" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
              {result.symbols_skipped && result.symbols_skipped.length > 0 && (
                <div className="mb-3 px-3 py-2 rounded text-xs" style={{ backgroundColor: '#3b2a1a', border: '1px solid #6b4c2a', color: '#f59e0b' }}>
                  {result.symbols_skipped.length} 只标的无数据被跳过: {result.symbols_skipped.join(', ')}
                  （可能未上市或 Tushare 无覆盖）
                </div>
              )}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                {Object.entries(result.metrics).filter(([k]) => k in metricLabels).map(([k, v]) => (
                  <div key={k} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                    <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{metricLabels[k] || k}</div>
                    <div className="text-sm font-medium" style={{ color: k === 'max_drawdown' ? 'var(--color-down)' : k === 'sharpe_ratio' && (v as number) > 1 ? 'var(--color-up)' : 'var(--text-primary)' }}>
                      {['total_return', 'annualized_return', 'max_drawdown', 'annualized_volatility', 'turnover_per_rebalance'].includes(k) ? fmt(v as number, true) : k === 'trade_count' || k === 'n_rebalances' ? String(v) : fmt(v as number)}
                    </div>
                  </div>
                ))}
              </div>
              {equityOption && <ReactECharts option={equityOption} style={{ height: 300 }} />}
              {result.trades.length > 0 && (
                <div className="mt-4">
                  <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>交易记录 ({result.trades.length}{result.trades.length >= 100 ? '+' : ''})</h4>
                  <div className="overflow-x-auto max-h-48 overflow-y-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                    <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                      <thead><tr style={{ backgroundColor: 'var(--bg-primary)', position: 'sticky', top: 0 }}>
                        {['日期', '标的', '方向', '股数', '价格', '成本'].map(h => (
                          <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                        ))}
                      </tr></thead>
                      <tbody>{result.trades.map((t, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td className="px-3 py-1">{t.date}</td>
                          <td className="px-3 py-1">{t.symbol}</td>
                          <td className="px-3 py-1" style={{ color: t.side === 'buy' ? 'var(--color-up)' : 'var(--color-down)' }}>{t.side === 'buy' ? '买入' : '卖出'}</td>
                          <td className="px-3 py-1">{t.shares}</td>
                          <td className="px-3 py-1">{Number(t.price).toFixed(2)}</td>
                          <td className="px-3 py-1">{Number(t.cost).toFixed(2)}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {tab === 'history' && (
        <div className="p-4 rounded" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <h3 className="text-sm font-medium mb-3">历史组合回测</h3>
          {history.length === 0 ? <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>暂无记录</p> : (
            <div className="overflow-x-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
              <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                <thead><tr style={{ backgroundColor: 'var(--bg-primary)' }}>
                  {['策略', '区间', '频率', '夏普', '总收益', '最大回撤', '交易数', '时间', ''].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>{history.map(r => (
                  <tr key={r.run_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td className="px-3 py-1.5">{r.strategy_name}</td>
                    <td className="px-3 py-1.5">{r.start_date?.slice(0, 10)}~{r.end_date?.slice(0, 10)}</td>
                    <td className="px-3 py-1.5">{r.freq}</td>
                    <td className="px-3 py-1.5">{fmt(r.metrics?.sharpe_ratio)}</td>
                    <td className="px-3 py-1.5">{fmt(r.metrics?.total_return, true)}</td>
                    <td className="px-3 py-1.5" style={{ color: 'var(--color-down)' }}>{fmt(r.metrics?.max_drawdown, true)}</td>
                    <td className="px-3 py-1.5">{r.trade_count}</td>
                    <td className="px-3 py-1.5" style={{ color: 'var(--text-secondary)' }}>{r.created_at?.slice(0, 16)}</td>
                    <td className="px-3 py-1.5">
                      <button onClick={() => handleDeleteRun(r.run_id)} className="text-xs px-1.5 py-0.5 rounded hover:opacity-80"
                        style={{ color: '#ef4444', border: '1px solid #7f1d1d' }}>删除</button>
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
