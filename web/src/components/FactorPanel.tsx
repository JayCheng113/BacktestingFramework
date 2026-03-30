import { useState, useEffect } from 'react'
import ReactECharts from 'echarts-for-react'
import { listFactors, evaluateFactor } from '../api'
import type { FactorResult } from '../types'

interface Props {
  symbol: string; market: string; startDate: string; endDate: string
}

// Chinese labels for known factors; unknown factors show class name
const _LABELS: Record<string, string> = {
  ma: '移动平均 (MA)', ema: '指数均线 (EMA)', rsi: '相对强弱 (RSI)',
  macd: 'MACD', boll: '布林带 (BOLL)', momentum: '动量 (Momentum)',
  vwap: '成交量加权均价 (VWAP)', obv: '能量潮 (OBV)', atr: '真实波幅 (ATR)',
}
const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

export default function FactorPanel({ symbol, market, startDate, endDate }: Props) {
  const [factors, setFactors] = useState<{ value: string; label: string }[]>([])
  const [factor, setFactor] = useState('')
  const [result, setResult] = useState<FactorResult | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    listFactors().then(r => {
      const list = (r.data as { name: string; class: string }[]).map(f => ({
        value: f.name,
        label: _LABELS[f.name] || `${f.class} (${f.name})`,
      }))
      setFactors(list)
      if (list.length > 0 && !factor) setFactor(list[0].value)
    }).catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleEval = async () => {
    setLoading(true)
    try {
      const res = await evaluateFactor({ symbol, market, factor_name: factor, start_date: startDate, end_date: endDate })
      setResult(res.data)
    } catch (e: any) { alert(e?.response?.data?.detail || 'Evaluation failed') }
    finally { setLoading(false) }
  }

  // IC time-series with mean line + ±1 std dev bands
  const icN = result ? result.ic_series.length : 0
  const icMean = result && icN > 0 ? result.ic_series.reduce((a: number, b: number) => a + b, 0) / icN : 0
  const icStd = result && icN > 1 ? Math.sqrt(result.ic_series.reduce((s: number, v: number) => s + (v - icMean) ** 2, 0) / (icN - 1)) : 0
  const icTimeSeriesOption = result ? {
    backgroundColor: '#0d1117',
    title: { text: 'IC 时间序列', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: result.ic_series.map((_: number, i: number) => i), axisLabel: { color: '#8b949e' } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
    series: [
      { type: 'bar', data: result.ic_series.map((v: number) => ({ value: v, itemStyle: { color: v >= 0 ? '#2563eb80' : '#ef444480' } })), barMaxWidth: 4 },
      { type: 'line', data: result.ic_series.map(() => icMean), lineStyle: { color: '#f59e0b', type: 'dashed', width: 1 }, showSymbol: false, name: 'Mean' },
      { type: 'line', data: result.ic_series.map(() => icMean + icStd), lineStyle: { color: '#f59e0b40', type: 'dotted', width: 1 }, showSymbol: false, name: '+1σ' },
      { type: 'line', data: result.ic_series.map(() => icMean - icStd), lineStyle: { color: '#f59e0b40', type: 'dotted', width: 1 }, showSymbol: false, name: '-1σ' },
    ],
  } : null

  // IC Decay curve
  const icDecayOption = result && result.ic_decay ? {
    backgroundColor: '#0d1117',
    title: { text: 'IC 衰减', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: Object.keys(result.ic_decay).map(k => `${k}d`), axisLabel: { color: '#8b949e' } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
    series: [
      { type: 'line', data: Object.values(result.ic_decay), lineStyle: { color: '#2563eb', width: 2 }, symbolSize: 8, itemStyle: { color: '#2563eb' } },
    ],
  } : null

  // IC Distribution histogram
  const icHistOption = result && result.ic_series.length > 0 ? (() => {
    const bins = 20
    const vals = result.ic_series
    if (vals.length === 0) return null
    let min = vals[0], max = vals[0]
    for (const v of vals) { if (v < min) min = v; if (v > max) max = v }
    const step = (max - min) / bins || 0.01
    const counts = Array(bins).fill(0)
    vals.forEach((v: number) => { const idx = Math.min(Math.floor((v - min) / step), bins - 1); counts[idx]++ })
    const labels = Array.from({ length: bins }, (_, i) => (min + step * (i + 0.5)).toFixed(3))
    return {
      backgroundColor: '#0d1117',
      title: { text: 'IC 分布', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
      tooltip: { trigger: 'axis' },
      grid: { left: 60, right: 20, top: 40, bottom: 30 },
      xAxis: { type: 'category', data: labels, axisLabel: { color: '#8b949e', rotate: 45, fontSize: 10 } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
      series: [{ type: 'bar', data: counts, itemStyle: { color: '#2563eb80' } }],
    }
  })() : null

  return (
    <div className="p-4 rounded mt-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <h3 className="text-sm font-medium mb-3">因子分析</h3>
      <div className="flex gap-3 items-end mb-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>因子</label>
          <select value={factor} onChange={e => setFactor(e.target.value)}
            className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
            {factors.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </div>
        <button onClick={handleEval} disabled={loading || !factor}
          className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: loading ? '#30363d' : 'var(--color-accent)' }}>
          {loading ? '评估中...' : '评估'}
        </button>
      </div>
      {result && (
        <div>
          {/* Metric cards */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
            {[
              ['IC 均值', result.ic_mean], ['Rank IC', result.rank_ic_mean],
              ['ICIR', result.icir], ['Rank ICIR', result.rank_icir], ['换手率', result.turnover],
            ].map(([label, val]) => (
              <div key={label as string} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{label as string}</div>
                <div className="text-sm font-medium" style={{ color: Math.abs(val as number) > 0.03 ? '#2563eb' : 'var(--text-primary)' }}>
                  {(val as number).toFixed(4)}
                </div>
              </div>
            ))}
          </div>
          {/* Charts: IC series + IC decay + IC distribution */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {icTimeSeriesOption && <ReactECharts option={icTimeSeriesOption} style={{ height: 220 }} />}
            {icDecayOption && <ReactECharts option={icDecayOption} style={{ height: 220 }} />}
          </div>
          {icHistOption && <div className="mt-4"><ReactECharts option={icHistOption} style={{ height: 200 }} /></div>}
        </div>
      )}
    </div>
  )
}
