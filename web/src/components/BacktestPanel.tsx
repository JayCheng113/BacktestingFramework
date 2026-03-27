import { useState, useEffect } from 'react'
import ReactECharts from 'echarts-for-react'
import { listStrategies, runBacktest } from '../api'
import type { StrategyInfo, BacktestResult } from '../types'

interface Props {
  symbol: string; market: string; startDate: string; endDate: string
}

export default function BacktestPanel({ symbol, market, startDate, endDate }: Props) {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [selected, setSelected] = useState('')
  const [params, setParams] = useState<Record<string, number>>({})
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    listStrategies().then(r => {
      setStrategies(r.data)
      if (r.data.length > 0) {
        setSelected(r.data[0].name)
        const defaults: Record<string, number> = {}
        for (const [k, v] of Object.entries(r.data[0].parameters)) defaults[k] = (v as any).default
        setParams(defaults)
      }
    }).catch(() => {})
  }, [])

  const handleRun = async () => {
    if (!selected || !symbol) return
    setLoading(true)
    try {
      const res = await runBacktest({
        symbol, market, period: 'daily', strategy_name: selected,
        strategy_params: params, start_date: startDate, end_date: endDate,
      })
      setResult(res.data)
    } catch (e: any) { alert(e?.response?.data?.detail || 'Backtest failed') }
    finally { setLoading(false) }
  }

  const onStrategyChange = (name: string) => {
    setSelected(name)
    const s = strategies.find(s => s.name === name)
    if (s) {
      const defaults: Record<string, number> = {}
      for (const [k, v] of Object.entries(s.parameters)) defaults[k] = (v as any).default
      setParams(defaults)
    }
  }

  const equityOption = result ? {
    backgroundColor: '#0d1117',
    title: { text: 'Equity Curve', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    legend: { data: ['Strategy', 'Benchmark'], textStyle: { color: '#8b949e' }, top: 25 },
    grid: { left: 60, right: 20, top: 60, bottom: 30 },
    xAxis: { type: 'category', data: result.equity_curve.map((_, i) => i), axisLabel: { color: '#8b949e' } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
    series: [
      { name: 'Strategy', type: 'line', data: result.equity_curve, lineStyle: { color: '#2563eb' }, showSymbol: false },
      { name: 'Benchmark', type: 'line', data: result.benchmark_curve, lineStyle: { color: '#8b949e', type: 'dashed' }, showSymbol: false },
    ],
  } : null

  return (
    <div className="p-4 rounded mt-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <h3 className="text-sm font-medium mb-3">Backtest</h3>
      <div className="flex flex-wrap gap-3 items-end mb-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Strategy</label>
          <select value={selected} onChange={e => onStrategyChange(e.target.value)}
            className="px-3 py-1.5 rounded text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
            {strategies.map(s => <option key={s.key} value={s.name}>{s.name}</option>)}
          </select>
        </div>
        {Object.entries(params).map(([k, v]) => (
          <div key={k} className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{k}</label>
            <input type="number" value={v} onChange={e => setParams({ ...params, [k]: Number(e.target.value) })}
              className="px-3 py-1.5 rounded text-sm w-20" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
          </div>
        ))}
        <button onClick={handleRun} disabled={loading}
          className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: loading ? '#30363d' : 'var(--color-accent)' }}>
          {loading ? 'Running...' : 'Run'}
        </button>
      </div>

      {result && (
        <div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            {Object.entries(result.metrics).filter(([k]) => ['sharpe_ratio', 'total_return', 'max_drawdown', 'win_rate'].includes(k)).map(([k, v]) => (
              <div key={k} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{k.replace(/_/g, ' ')}</div>
                <div className="text-sm font-medium">{typeof v === 'number' ? (k.includes('return') || k.includes('rate') || k.includes('drawdown') ? `${(v * 100).toFixed(2)}%` : v.toFixed(4)) : v}</div>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 mb-3">
            <span className={`text-xs px-2 py-0.5 rounded ${result.significance.is_significant ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}`}>
              {result.significance.is_significant ? 'Significant' : 'Not Significant'}
            </span>
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              p={result.significance.p_value.toFixed(3)} | Sharpe CI [{result.significance.sharpe_ci_lower.toFixed(2)}, {result.significance.sharpe_ci_upper.toFixed(2)}]
            </span>
          </div>
          {equityOption && <ReactECharts option={equityOption} style={{ height: 300 }} />}
        </div>
      )}
    </div>
  )
}
