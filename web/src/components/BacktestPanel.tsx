import { useState, useEffect } from 'react'
import ReactECharts from 'echarts-for-react'
import { listStrategies, runBacktest, runWalkForward } from '../api'
import type { StrategyInfo, BacktestResult, WalkForwardResult } from '../types'

interface Props {
  symbol: string; market: string; startDate: string; endDate: string
  onTradesUpdate?: (trades: BacktestResult['trades']) => void
}

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

export default function BacktestPanel({ symbol, market, startDate, endDate, onTradesUpdate }: Props) {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [selected, setSelected] = useState('')
  const [params, setParams] = useState<Record<string, number>>({})
  const [mode, setMode] = useState<'backtest' | 'walk-forward'>('backtest')
  const [nSplits, setNSplits] = useState(5)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [wfResult, setWfResult] = useState<WalkForwardResult | null>(null)
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
    setResult(null)
    setWfResult(null)
    try {
      if (mode === 'backtest') {
        const res = await runBacktest({
          symbol, market, period: 'daily', strategy_name: selected,
          strategy_params: params, start_date: startDate, end_date: endDate,
        })
        setResult(res.data)
        onTradesUpdate?.(res.data.trades || [])
      } else {
        const res = await runWalkForward({
          symbol, market, period: 'daily', strategy_name: selected,
          strategy_params: params, start_date: startDate, end_date: endDate,
          n_splits: nSplits,
        })
        setWfResult(res.data)
      }
    } catch (e: any) { alert(e?.response?.data?.detail || 'Failed') }
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
    legend: { data: ['Strategy', `Benchmark (Buy & Hold)`], textStyle: { color: '#8b949e' }, top: 25 },
    grid: { left: 60, right: 20, top: 60, bottom: 30 },
    xAxis: { type: 'category', data: result.equity_curve.map((_: number, i: number) => i), axisLabel: { color: '#8b949e' } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
    series: [
      { name: 'Strategy', type: 'line', data: result.equity_curve, lineStyle: { color: '#2563eb' }, showSymbol: false },
      { name: 'Benchmark (Buy & Hold)', type: 'line', data: result.benchmark_curve, lineStyle: { color: '#8b949e', type: 'dashed' }, showSymbol: false },
    ],
  } : null

  const wfEquityOption = wfResult ? {
    backgroundColor: '#0d1117',
    title: { text: 'OOS Equity Curve', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: wfResult.oos_equity_curve.map((_: number, i: number) => i), axisLabel: { color: '#8b949e' } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
    series: [{ type: 'line', data: wfResult.oos_equity_curve, lineStyle: { color: '#22c55e' }, showSymbol: false }],
  } : null

  const exportCSV = (result: BacktestResult) => {
    const headers = 'Entry Date,Exit Date,Entry Price,Exit Price,PnL,PnL%,Commission\n'
    const rows = result.trades.map(t =>
      `${t.entry_time.slice(0,10)},${t.exit_time.slice(0,10)},${t.entry_price},${t.exit_price},${t.pnl.toFixed(2)},${(t.pnl_pct*100).toFixed(2)}%,${t.commission.toFixed(2)}`
    ).join('\n')
    const blob = new Blob([headers + rows], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `backtest-trades-${new Date().toISOString().slice(0,10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const metricKeys = ['sharpe_ratio', 'total_return', 'max_drawdown', 'alpha', 'beta', 'win_rate', 'trade_count', 'avg_holding_days', 'max_drawdown_duration']
  const formatMetric = (k: string, v: number) => {
    if ((k.includes('return') || k.includes('rate') || k.includes('drawdown')) && !k.includes('duration'))
      return `${(v * 100).toFixed(2)}%`
    if (k === 'trade_count' || k.includes('duration') || k.includes('days')) return Math.round(v).toString()
    return v.toFixed(4)
  }

  return (
    <div className="p-4 rounded mt-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <h3 className="text-sm font-medium mb-3">Backtest</h3>
      <div className="flex flex-wrap gap-3 items-end mb-4">
        {/* Mode toggle */}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Mode</label>
          <select value={mode} onChange={e => { setMode(e.target.value as any); setResult(null); setWfResult(null) }}
            className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
            <option value="backtest">Single Backtest</option>
            <option value="walk-forward">Walk-Forward</option>
          </select>
        </div>
        {/* Strategy */}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Strategy</label>
          <select value={selected} onChange={e => onStrategyChange(e.target.value)}
            className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
            {strategies.map(s => <option key={s.key} value={s.name}>{s.name}</option>)}
          </select>
        </div>
        {/* Strategy params */}
        {Object.entries(params).map(([k, v]) => (
          <div key={k} className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{k}</label>
            <input type="number" value={v} onChange={e => setParams({ ...params, [k]: Number(e.target.value) })}
              className="px-3 py-1.5 rounded text-sm w-20" style={inputStyle} />
          </div>
        ))}
        {/* WF splits */}
        {mode === 'walk-forward' && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Splits</label>
            <input type="number" value={nSplits} min={2} max={20}
              onChange={e => setNSplits(Number(e.target.value))}
              className="px-3 py-1.5 rounded text-sm w-16" style={inputStyle} />
          </div>
        )}
        <button onClick={handleRun} disabled={loading}
          className="px-4 py-1.5 rounded text-sm font-medium text-white"
          style={{ backgroundColor: loading ? '#30363d' : 'var(--color-accent)' }}>
          {loading ? 'Running...' : 'Run'}
        </button>
      </div>

      {/* Single backtest results */}
      {result && (
        <div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            {Object.entries(result.metrics).filter(([k]) => metricKeys.includes(k)).map(([k, v]) => (
              <div key={k} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{k.replace(/_/g, ' ')}</div>
                <div className="text-sm font-medium">{formatMetric(k, v)}</div>
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
          {/* Trade Records Table */}
          {result.trades.length > 0 && (
            <div className="mt-4">
              <div className="flex justify-between items-center mb-2">
                <h4 className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>Trade Records ({result.trades.length})</h4>
                <button onClick={() => exportCSV(result)} className="text-xs px-2 py-1 rounded" style={{ ...inputStyle, cursor: 'pointer' }}>Export CSV</button>
              </div>
              <div className="overflow-x-auto max-h-64 overflow-y-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ backgroundColor: 'var(--bg-primary)', position: 'sticky', top: 0 }}>
                      {['#', 'Entry', 'Exit', 'Entry Price', 'Exit Price', 'PnL', 'PnL%', 'Commission'].map(h => (
                        <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td className="px-3 py-1.5" style={{ color: 'var(--text-secondary)' }}>{i + 1}</td>
                        <td className="px-3 py-1.5">{t.entry_time.slice(0, 10)}</td>
                        <td className="px-3 py-1.5">{t.exit_time.slice(0, 10)}</td>
                        <td className="px-3 py-1.5">{t.entry_price.toFixed(2)}</td>
                        <td className="px-3 py-1.5">{t.exit_price.toFixed(2)}</td>
                        <td className="px-3 py-1.5" style={{ color: t.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}
                        </td>
                        <td className="px-3 py-1.5" style={{ color: t.pnl_pct >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {(t.pnl_pct * 100).toFixed(2)}%
                        </td>
                        <td className="px-3 py-1.5" style={{ color: 'var(--text-secondary)' }}>{t.commission.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Walk-Forward results */}
      {wfResult && (
        <div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>OOS Sharpe</div>
              <div className="text-sm font-medium">{(wfResult.oos_metrics.sharpe_ratio ?? 0).toFixed(4)}</div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Overfitting Score</div>
              <div className="text-sm font-medium" style={{ color: wfResult.overfitting_score > 0.5 ? '#ef4444' : '#22c55e' }}>
                {wfResult.overfitting_score.toFixed(4)}
              </div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>IS vs OOS Degradation</div>
              <div className="text-sm font-medium">{(wfResult.is_vs_oos_degradation * 100).toFixed(1)}%</div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Splits</div>
              <div className="text-sm font-medium">{wfResult.n_splits}</div>
            </div>
          </div>
          <div className="flex items-center gap-2 mb-3">
            <span className={`text-xs px-2 py-0.5 rounded ${wfResult.overfitting_score <= 0.3 ? 'bg-green-900 text-green-300' : wfResult.overfitting_score <= 0.6 ? 'bg-yellow-900 text-yellow-300' : 'bg-red-900 text-red-300'}`}>
              {wfResult.overfitting_score <= 0.3 ? 'Robust' : wfResult.overfitting_score <= 0.6 ? 'Moderate Overfit' : 'Overfitting'}
            </span>
          </div>
          {wfEquityOption && <ReactECharts option={wfEquityOption} style={{ height: 300 }} />}
        </div>
      )}
    </div>
  )
}
