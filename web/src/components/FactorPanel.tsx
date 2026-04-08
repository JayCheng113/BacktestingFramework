import { useState, useEffect } from 'react'
import ReactECharts from 'echarts-for-react'
import { listFactors, evaluateFactor } from '../api'
import type { FactorResult } from '../types'
import { useToast } from './shared/Toast'
import { CHART } from './shared/chartTheme'

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
  const { showToast } = useToast()
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
    }).catch((e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      showToast('error', err?.response?.data?.detail || err?.message || '加载因子列表失败')
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // V2.12.2 codex: clear stale factor result when any evaluation input
  // changes (symbol, market, dates, factor). Prior version kept the old
  // result visible even though the inputs no longer matched the evaluation,
  // misleading the user into thinking the metrics applied to the new inputs.
  useEffect(() => {
    setResult(null)
  }, [symbol, market, startDate, endDate, factor])

  const handleEval = async () => {
    setLoading(true)
    try {
      const res = await evaluateFactor({ symbol, market, factor_name: factor, start_date: startDate, end_date: endDate })
      setResult(res.data)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      showToast('error', err?.response?.data?.detail || err?.message || '因子评估失败')
    }
    finally { setLoading(false) }
  }

  // IC time-series with mean line + ±1 std dev bands
  const icN = result ? result.ic_series.length : 0
  const icMean = result && icN > 0 ? result.ic_series.reduce((a: number, b: number) => a + b, 0) / icN : 0
  const icStd = result && icN > 1 ? Math.sqrt(result.ic_series.reduce((s: number, v: number) => s + (v - icMean) ** 2, 0) / (icN - 1)) : 0
  const icTimeSeriesOption = result ? {
    backgroundColor: CHART.bg,
    title: { text: '预测能力随时间变化 (IC)', subtext: '柱高代表每期预测准确度，正值=方向正确，蓝线=均值', textStyle: { color: CHART.text, fontSize: 12 }, subtextStyle: { color: CHART.textSecondary, fontSize: 10 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 55, bottom: 30 },
    xAxis: { type: 'category', data: result.ic_series.map((_: number, i: number) => i), axisLabel: { color: CHART.textSecondary } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: CHART.grid } }, axisLabel: { color: CHART.textSecondary } },
    series: [
      { type: 'bar', data: result.ic_series.map((v: number) => ({ value: v, itemStyle: { color: v >= 0 ? CHART.accent + '80' : CHART.error + '80' } })), barMaxWidth: 4 },
      { type: 'line', data: result.ic_series.map(() => icMean), lineStyle: { color: CHART.warn, type: 'dashed', width: 1 }, showSymbol: false, name: 'Mean' },
      { type: 'line', data: result.ic_series.map(() => icMean + icStd), lineStyle: { color: CHART.warn + '40', type: 'dotted', width: 1 }, showSymbol: false, name: '+1σ' },
      { type: 'line', data: result.ic_series.map(() => icMean - icStd), lineStyle: { color: CHART.warn + '40', type: 'dotted', width: 1 }, showSymbol: false, name: '-1σ' },
    ],
  } : null

  // IC Decay curve
  const icDecayOption = result && result.ic_decay ? {
    backgroundColor: CHART.bg,
    title: { text: '信号持续性 (IC衰减)', subtext: '曲线越平=信号越持久，快速降零=需频繁调仓', textStyle: { color: CHART.text, fontSize: 12 }, subtextStyle: { color: CHART.textSecondary, fontSize: 10 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 55, bottom: 30 },
    xAxis: { type: 'category', data: Object.keys(result.ic_decay).map(k => `${k}d`), axisLabel: { color: CHART.textSecondary } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: CHART.grid } }, axisLabel: { color: CHART.textSecondary } },
    series: [
      { type: 'line', data: Object.values(result.ic_decay), lineStyle: { color: CHART.accent, width: 2 }, symbolSize: 8, itemStyle: { color: CHART.accent } },
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
      backgroundColor: CHART.bg,
      title: { text: '预测能力分布 (IC直方图)', subtext: '集中在正区间=因子稳定有效，分散在零附近=噪音大', textStyle: { color: CHART.text, fontSize: 12 }, subtextStyle: { color: CHART.textSecondary, fontSize: 10 }, left: 'center' },
      tooltip: { trigger: 'axis' },
      grid: { left: 60, right: 20, top: 55, bottom: 30 },
      xAxis: { type: 'category', data: labels, axisLabel: { color: CHART.textSecondary, rotate: 45, fontSize: 10 } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: CHART.grid } }, axisLabel: { color: CHART.textSecondary } },
      series: [{ type: 'bar', data: counts, itemStyle: { color: CHART.accent + '80' } }],
    }
  })() : null

  return (
    <div className="p-4 rounded mt-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <h3 className="text-base font-semibold mb-3">技术指标评估 (单股)</h3>
      {factors.length === 0 ? (
        <div className="py-6 text-center text-sm" style={{ color: 'var(--text-secondary)' }}>暂无可用因子</div>
      ) : (
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
      )}
      {result && (
        <div>
          {/* Metric cards */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
            {(() => {
              const rateIc = (v: number) => {
                const a = Math.abs(v)
                if (a >= 0.05) return { color: '#22c55e', hint: '强' }
                if (a >= 0.03) return { color: CHART.accent, hint: '中' }
                if (a >= 0.01) return { color: '#f59e0b', hint: '弱' }
                return { color: '#ef4444', hint: '无效' }
              }
              const rateIcir = (v: number) => {
                const a = Math.abs(v)
                if (a >= 0.5) return { color: '#22c55e', hint: '很稳定' }
                if (a >= 0.3) return { color: CHART.accent, hint: '较稳定' }
                if (a >= 0.1) return { color: '#f59e0b', hint: '一般' }
                return { color: '#ef4444', hint: '不稳定' }
              }
              const rateTurnover = (v: number) => {
                if (v <= 0.3) return { color: '#22c55e', hint: '低换手' }
                if (v <= 0.6) return { color: '#f59e0b', hint: '中等' }
                return { color: '#ef4444', hint: '高换手' }
              }

              const metrics: { label: string; val: number; rating: { color: string; hint: string }; tooltip: string }[] = [
                { label: '预测能力(IC)', val: result.ic_mean, rating: rateIc(result.ic_mean), tooltip: '|IC|≥0.05强, ≥0.03中, ≥0.01弱' },
                { label: '排名IC', val: result.rank_ic_mean, rating: rateIc(result.rank_ic_mean), tooltip: '排名IC更稳健, 评判标准同IC' },
                { label: '稳定性(ICIR)', val: result.icir, rating: rateIcir(result.icir), tooltip: '|ICIR|≥0.5很稳定, ≥0.3较稳定' },
                { label: '排名ICIR', val: result.rank_icir, rating: rateIcir(result.rank_icir), tooltip: '排名ICIR, 评判标准同ICIR' },
                { label: '换手率', val: result.turnover, rating: rateTurnover(result.turnover), tooltip: '≤0.3低(好), ≤0.6中, >0.6高(成本大)' },
              ]

              return metrics.map(m => (
                <div key={m.label} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }} title={m.tooltip}>
                  <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{m.label}</div>
                  <div className="text-sm font-medium" style={{ color: m.rating.color }}>
                    {m.val.toFixed(4)}
                  </div>
                  <div className="text-xs mt-0.5" style={{ color: m.rating.color, opacity: 0.8 }}>
                    {m.rating.hint}
                  </div>
                </div>
              ))
            })()}
          </div>
          {/* Charts: IC series + IC decay + IC distribution */}
          <div className="text-xs mb-2 px-1" style={{ color: 'var(--text-secondary)' }}>
            IC (Information Coefficient) = 因子值与未来收益的相关性。|IC| 越大 = 预测越准，正/负表示方向。
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {icTimeSeriesOption && <ReactECharts option={icTimeSeriesOption} style={{ height: 250 }} />}
            {icDecayOption && <ReactECharts option={icDecayOption} style={{ height: 250 }} />}
          </div>
          {icHistOption && <div className="mt-4"><ReactECharts option={icHistOption} style={{ height: 250 }} /></div>}
        </div>
      )}
    </div>
  )
}
