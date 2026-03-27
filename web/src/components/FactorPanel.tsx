import { useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { evaluateFactor } from '../api'
import type { FactorResult } from '../types'

interface Props {
  symbol: string; market: string; startDate: string; endDate: string
}

const FACTORS = ['ma', 'ema', 'rsi', 'macd', 'boll']

export default function FactorPanel({ symbol, market, startDate, endDate }: Props) {
  const [factor, setFactor] = useState('ma')
  const [result, setResult] = useState<FactorResult | null>(null)
  const [loading, setLoading] = useState(false)

  const handleEval = async () => {
    setLoading(true)
    try {
      const res = await evaluateFactor({
        symbol, market, factor_name: factor,
        start_date: startDate, end_date: endDate,
      })
      setResult(res.data)
    } catch (e: any) { alert(e?.response?.data?.detail || 'Evaluation failed') }
    finally { setLoading(false) }
  }

  const icOption = result ? {
    backgroundColor: '#0d1117',
    title: { text: 'IC Series', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: result.ic_series.map((_, i) => i), axisLabel: { color: '#8b949e' } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
    series: [
      { type: 'bar', data: result.ic_series.map(v => ({ value: v, itemStyle: { color: v >= 0 ? '#2563eb80' : '#ef444480' } })) },
    ],
  } : null

  return (
    <div className="p-4 rounded mt-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <h3 className="text-sm font-medium mb-3">Factor Analysis</h3>
      <div className="flex gap-3 items-end mb-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Factor</label>
          <select value={factor} onChange={e => setFactor(e.target.value)}
            className="px-3 py-1.5 rounded text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
            {FACTORS.map(f => <option key={f} value={f}>{f.toUpperCase()}</option>)}
          </select>
        </div>
        <button onClick={handleEval} disabled={loading}
          className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: loading ? '#30363d' : 'var(--color-accent)' }}>
          {loading ? 'Evaluating...' : 'Evaluate'}
        </button>
      </div>
      {result && (
        <div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>IC Mean</div>
              <div className="text-sm font-medium">{result.ic_mean.toFixed(4)}</div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Rank IC</div>
              <div className="text-sm font-medium">{result.rank_ic_mean.toFixed(4)}</div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>ICIR</div>
              <div className="text-sm font-medium">{result.icir.toFixed(4)}</div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Turnover</div>
              <div className="text-sm font-medium">{result.turnover.toFixed(4)}</div>
            </div>
          </div>
          {icOption && <ReactECharts option={icOption} style={{ height: 200 }} />}
        </div>
      )}
    </div>
  )
}
