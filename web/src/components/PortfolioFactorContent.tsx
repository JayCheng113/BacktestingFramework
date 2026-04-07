import { useState, useEffect, useRef } from 'react'
import ReactECharts from 'echarts-for-react'
import DateRangePicker from './DateRangePicker'
import { mlAlphaDiagnostics } from '../api'
import type { DiagnosticsResult } from '../types'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

import { CATEGORY_LABELS, FACTOR_LABELS } from './shared/portfolioLabels'

// ── Factor evaluation types ──────────────────────────────────────

interface FactorInfo {
  key?: string
  class_name?: string
  description?: string
  needs_fina?: boolean
}

interface FactorCategory {
  key: string
  label: string
  factors: (string | FactorInfo)[]
}

interface EvalFactorResult {
  factor_name: string
  mean_ic: number | null
  mean_rank_ic: number | null
  icir: number | null
  rank_icir: number | null
  n_eval_dates: number
  avg_stocks_per_date?: number
  ic_series?: number[]
  rank_ic_series?: number[]
  eval_dates?: string[]
  ic_decay?: Record<string, number>
  quintile_returns?: Record<string, number>
}

interface EvalResponse {
  results: EvalFactorResult[]
  warnings?: string[]
}

interface CorrResponse {
  correlation_matrix: number[][]
  factor_names: string[]
}

interface QualityRow {
  symbol: string
  industry?: string
  daily_count: number
  daily_expected: number
  daily_coverage_pct: number
  has_fina: boolean
  fina_reports?: number
}

interface EChartsTooltipParam {
  data: [number, number, number]
}

const fmt = (v: number | null | undefined, pct = false) => {
  if (v == null) return '-'
  return pct ? `${(v * 100).toFixed(2)}%` : v.toFixed(4)
}

interface Props {
  // Shared state
  symbols: string; setSymbols: (v: string) => void
  market: string; setMarket: (v: string) => void
  startDate: string; setStartDate: (v: string) => void
  endDate: string; setEndDate: (v: string) => void
  factors: string[]
  factorCategories: FactorCategory[]
  // Factor research state
  evalFactors: string[]; setEvalFactors: (v: string[] | ((prev: string[]) => string[])) => void
  neutralize: boolean; setNeutralize: (v: boolean) => void
  evalResult: EvalResponse | null
  corrResult: CorrResponse | null
  evalLoading: boolean
  fetchingFunda: boolean
  fundaStatus: string
  qualityReport: QualityRow[]
  // Handlers
  handleEvaluateFactors: () => void
  handleFetchFundamental: () => void
}

export default function PortfolioFactorContent(props: Props) {
  const {
    symbols, setSymbols, market, setMarket,
    startDate, setStartDate, endDate, setEndDate,
    factors, factorCategories,
    evalFactors, setEvalFactors, neutralize, setNeutralize,
    evalResult, corrResult, evalLoading,
    fetchingFunda, fundaStatus, qualityReport,
    handleEvaluateFactors, handleFetchFundamental,
  } = props

  return (
    <div className="p-4 rounded mb-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <h3 className="text-sm font-medium mb-3">选股因子研究</h3>
      <p className="text-xs mb-3" style={{ color: 'var(--text-secondary)' }}>
        测试"用这个指标选股靠不靠谱"。选因子 → 填股票池 → 评估 → 看选股能力和分档收益。
      </p>
      <div className="mb-3">
        <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>选择因子 (可多选):</label>
        {factorCategories.length > 0 ? factorCategories.map(cat => {
          return (
          <div key={cat.key} className="mb-2">
            <span className="text-xs font-medium mr-2" style={{ color: 'var(--text-secondary)' }}>{CATEGORY_LABELS[cat.key] || cat.label}:</span>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {(Array.isArray(cat.factors) ? cat.factors : []).map((f: string | FactorInfo) => {
                const fKey = typeof f === 'string' ? f : (f.key || f.class_name || '')
                const fLabel = FACTOR_LABELS[fKey] || fKey
                const fDesc = typeof f === 'object' ? f.description : ''
                const needsFina = typeof f === 'object' && f.needs_fina
                return (
                  <button key={fKey} onClick={() => setEvalFactors(prev => prev.includes(fKey) ? prev.filter(x => x !== fKey) : [...prev, fKey])}
                    className="text-xs px-2 py-0.5 rounded" title={fDesc || fKey}
                    style={{ backgroundColor: evalFactors.includes(fKey) ? 'var(--color-accent)' : 'var(--bg-primary)',
                             color: evalFactors.includes(fKey) ? '#fff' : 'var(--text-secondary)',
                             border: '1px solid var(--border)', opacity: needsFina ? 0.85 : 1 }}>
                    {fLabel}{needsFina ? ' *' : ''}
                  </button>
                )
              })}
            </div>
          </div>
        )}) : (
          <div className="flex flex-wrap gap-1">
            {factors.map(f => (
              <button key={f} onClick={() => setEvalFactors(prev => prev.includes(f) ? prev.filter(x => x !== f) : [...prev, f])}
                className="text-xs px-2 py-0.5 rounded"
                style={{ backgroundColor: evalFactors.includes(f) ? 'var(--color-accent)' : 'var(--bg-primary)',
                         color: evalFactors.includes(f) ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                {FACTOR_LABELS[f] || f}
              </button>
            ))}
          </div>
        )}
        <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>带 * 的因子需要 Tushare 付费接口</p>
      </div>
      <div className="flex items-center gap-3 mb-3">
        <DateRangePicker startDate={startDate} endDate={endDate} onStartChange={setStartDate} onEndChange={setEndDate} />
        <label className="flex items-center gap-1.5 text-xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={neutralize} onChange={e => setNeutralize(e.target.checked)} />
          行业中性化
          <span style={{ color: 'var(--text-muted)', fontSize: '10px' }}>(去除行业偏差，需个股池)</span>
        </label>
      </div>
      <div className="mb-3">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>市场</label>
          <select value={market} onChange={e => setMarket(e.target.value)}
            className="text-xs px-2 py-0.5 rounded" style={inputStyle}>
            <option value="cn_stock">A股/ETF</option>
            <option value="us_stock">美股</option>
            <option value="hk_stock">港股</option>
          </select>
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>股票池</label>
          <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH,513880.SH,513260.SH,159985.SZ')}
            className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>宽基ETF</button>
        </div>
        <textarea value={symbols} onChange={e => setSymbols(e.target.value)} rows={2} className="w-full px-3 py-1.5 rounded text-sm font-mono" style={inputStyle} />
      </div>
      <div className="flex items-center gap-2 mb-3">
        <button onClick={handleFetchFundamental} disabled={fetchingFunda}
          className="px-3 py-1.5 rounded text-sm font-medium" style={{ backgroundColor: fetchingFunda ? '#30363d' : '#1e6b3a', color: '#fff' }}>
          {fetchingFunda ? '获取中...' : '获取基本面数据'}
        </button>
        <button onClick={handleEvaluateFactors} disabled={evalLoading}
          className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: evalLoading ? '#30363d' : '#0891b2' }}>
          {evalLoading ? '评估中...' : '评估因子'}
        </button>
        {fundaStatus && <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{fundaStatus}</span>}
      </div>

      {qualityReport.length > 0 && (
        <div className="mb-4 p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
          <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>数据质量报告</h4>
          <div className="overflow-x-auto" style={{ maxHeight: 200 }}>
            <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
              <thead><tr style={{ backgroundColor: 'var(--bg-secondary)' }}>
                {['标的', '行业', '日度数据', '覆盖率', '财务报告'].map(h => (
                  <th key={h} className="px-2 py-1 text-left" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {qualityReport.map(r => (
                  <tr key={r.symbol} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td className="px-2 py-1 font-mono">{r.symbol}</td>
                    <td className="px-2 py-1">{r.industry || '-'}</td>
                    <td className="px-2 py-1">{r.daily_count}/{r.daily_expected}</td>
                    <td className="px-2 py-1" style={{ color: r.daily_coverage_pct > 80 ? '#3fb950' : r.daily_coverage_pct > 50 ? '#d29922' : '#f85149' }}>
                      {r.daily_coverage_pct}%
                    </td>
                    <td className="px-2 py-1">{r.has_fina ? `${r.fina_reports} 期` : '无'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Evaluation results */}
      {evalResult && evalResult.results && (
        <div className="mt-4">
          {/* Warnings (e.g., neutralization skipped) */}
          {evalResult.warnings && evalResult.warnings.length > 0 && (
            <div className="mb-3 px-3 py-2 rounded text-xs" style={{ backgroundColor: '#3b2a1a', border: '1px solid #6b4c2a', color: '#f59e0b' }}>
              {evalResult.warnings.map((w: string, i: number) => <div key={i}>{w}</div>)}
            </div>
          )}
          {/* IC summary table */}
          <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>因子选股能力 (IC 汇总)</h4>
          <div className="overflow-x-auto mb-4" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
            <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
              <thead><tr style={{ backgroundColor: 'var(--bg-primary)' }}>
                {['因子', '选股能力(IC)', '排名IC', '稳定性(ICIR)', '排名ICIR', '评估日数', '平均覆盖'].map(h => (
                  <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                ))}
              </tr></thead>
              <tbody>{evalResult.results.map((r: EvalFactorResult) => (
                <tr key={r.factor_name} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td className="px-3 py-1.5">{FACTOR_LABELS[r.factor_name] || r.factor_name}</td>
                  <td className="px-3 py-1.5" style={{ color: (r.mean_ic ?? 0) > 0 ? 'var(--color-up)' : 'var(--color-down)' }}>{fmt(r.mean_ic)}</td>
                  <td className="px-3 py-1.5" style={{ color: (r.mean_rank_ic ?? 0) > 0 ? 'var(--color-up)' : 'var(--color-down)' }}>{fmt(r.mean_rank_ic)}</td>
                  <td className="px-3 py-1.5">{fmt(r.icir)}</td>
                  <td className="px-3 py-1.5">{fmt(r.rank_icir)}</td>
                  <td className="px-3 py-1.5">{r.n_eval_dates}</td>
                  <td className="px-3 py-1.5">{r.avg_stocks_per_date?.toFixed(0) ?? '-'}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>

          {/* IC time series chart */}
          {evalResult.results[0]?.ic_series?.length > 0 && (
            <ReactECharts option={{
              backgroundColor: '#0d1117',
              title: { text: '选股能力随时间变化 (Rank IC)', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
              tooltip: { trigger: 'axis' as const },
              legend: { data: evalResult.results.map((r: EvalFactorResult) => FACTOR_LABELS[r.factor_name] || r.factor_name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 25 },
              grid: { left: 60, right: 20, top: 50, bottom: 30 },
              xAxis: { type: 'time' as const, axisLabel: { color: '#8b949e', fontSize: 9 } },
              yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
              color: ['#2563eb', '#ef4444', '#22c55e', '#eab308', '#8b5cf6'],
              series: evalResult.results.map((r: EvalFactorResult) => ({
                name: FACTOR_LABELS[r.factor_name] || r.factor_name, type: 'line' as const,
                data: (r.eval_dates || []).map((d: string, i: number) => [d, r.rank_ic_series?.[i] ?? 0]),
                showSymbol: false,
              })),
            }} style={{ height: 250 }} />
          )}

          {/* IC decay + Quintile returns side by side */}
          <div className="grid grid-cols-2 gap-3 mt-3">
            {/* IC Decay */}
            {evalResult.results[0]?.ic_decay && (
              <ReactECharts option={{
                backgroundColor: '#0d1117',
                title: { text: '信号持续性 (IC随天数衰减)', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
                tooltip: { trigger: 'axis' as const },
                legend: { data: evalResult.results.map((r: EvalFactorResult) => FACTOR_LABELS[r.factor_name] || r.factor_name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 25 },
                grid: { left: 60, right: 20, top: 50, bottom: 30 },
                xAxis: { type: 'category' as const, data: ['1天', '5天', '10天', '20天'], axisLabel: { color: '#8b949e' } },
                yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
                color: ['#2563eb', '#ef4444', '#22c55e', '#eab308', '#8b5cf6'],
                series: evalResult.results.map((r: EvalFactorResult) => ({
                  name: FACTOR_LABELS[r.factor_name] || r.factor_name, type: 'line' as const,
                  data: [r.ic_decay['1'], r.ic_decay['5'], r.ic_decay['10'], r.ic_decay['20']],
                })),
              }} style={{ height: 220 }} />
            )}
            {/* Quintile returns */}
            {evalResult.results[0]?.quintile_returns && (
              <ReactECharts option={{
                backgroundColor: '#0d1117',
                title: { text: '按因子排名分5档的未来5天收益', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
                tooltip: { trigger: 'axis' as const },
                grid: { left: 60, right: 20, top: 50, bottom: 30 },
                xAxis: { type: 'category' as const, data: ['Q1(低)', 'Q2', 'Q3', 'Q4', 'Q5(高)'], axisLabel: { color: '#8b949e' } },
                yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e', formatter: (v: number) => (v * 100).toFixed(2) + '%' } },
                color: ['#2563eb', '#ef4444', '#22c55e'],
                series: evalResult.results.map((r: EvalFactorResult) => ({
                  name: FACTOR_LABELS[r.factor_name] || r.factor_name, type: 'bar' as const,
                  data: [1, 2, 3, 4, 5].map(q => r.quintile_returns[String(q)] ?? 0),
                })),
              }} style={{ height: 220 }} />
            )}
          </div>

          {/* Correlation heatmap */}
          {corrResult && corrResult.factor_names?.length >= 2 && (() => {
            const corrLabels = corrResult.factor_names.map((n: string) => FACTOR_LABELS[n] || n)
            return (
            <div className="mt-3">
              <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>因子相关性 (高相关说明因子重复)</h4>
              <ReactECharts option={{
                backgroundColor: '#0d1117',
                tooltip: { formatter: (p: EChartsTooltipParam) => `${corrLabels[p.data[1]]} × ${corrLabels[p.data[0]]}: ${p.data[2].toFixed(3)}` },
                grid: { left: 120, right: 40, top: 10, bottom: 40 },
                xAxis: { type: 'category' as const, data: corrLabels, axisLabel: { color: '#8b949e', fontSize: 9, rotate: 30 } },
                yAxis: { type: 'category' as const, data: corrLabels, axisLabel: { color: '#8b949e', fontSize: 9 } },
                visualMap: { min: -1, max: 1, calculable: true, orient: 'vertical' as const, right: 0, top: 'center', inRange: { color: ['#2563eb', '#0d1117', '#ef4444'] }, textStyle: { color: '#8b949e' } },
                series: [{
                  type: 'heatmap', data: corrResult.correlation_matrix.flatMap((row: number[], i: number) =>
                    row.map((v: number, j: number) => [j, i, Math.round(v * 1000) / 1000])),
                  label: { show: true, color: '#e6edf3', fontSize: 10, formatter: (p: EChartsTooltipParam) => p.data[2].toFixed(2) },
                }],
              }} style={{ height: Math.max(200, corrResult.factor_names.length * 40 + 60) }} />
            </div>
          )})()}
        </div>
      )}

      {/* V2.13.2 G2b: ML Alpha Diagnostics */}
      <MLDiagnosticsPanel
        symbols={symbols} market={market}
        startDate={startDate} endDate={endDate}
        factorCategories={factorCategories}
      />
    </div>
  )
}


// ─── ML Diagnostics Panel ────────────────────────────────────────

const VERDICT_COLORS: Record<string, string> = {
  healthy: '#22c55e', mild_overfit: '#eab308', severe_overfit: '#ef4444',
  unstable: '#f97316', insufficient_data: '#6b7280', unknown: '#6b7280',
}
const VERDICT_LABELS: Record<string, string> = {
  healthy: '健康', mild_overfit: '轻度过拟合', severe_overfit: '严重过拟合',
  unstable: '信号不稳定', insufficient_data: '数据不足', unknown: '未知',
}

function MLDiagnosticsPanel({ symbols, market, startDate, endDate, factorCategories }: {
  symbols: string; market: string; startDate: string; endDate: string
  factorCategories: FactorCategory[]
}) {
  const [selectedAlpha, setSelectedAlpha] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<DiagnosticsResult | null>(null)
  const [error, setError] = useState('')
  const diagTokenRef = useRef(0)

  // Clear stale diagnostics when ANY input changes — prevents user from
  // seeing results computed on a different market/symbols/date range/alpha.
  useEffect(() => {
    diagTokenRef.current += 1  // invalidate any in-flight request
    setLoading(false)           // unblock button if request was in-flight
    setResult(null)
    setError('')
  }, [symbols, market, startDate, endDate, selectedAlpha])

  // Reset selection when available alphas change (e.g., registry refresh)
  useEffect(() => {
    diagTokenRef.current += 1  // invalidate any in-flight request
    setSelectedAlpha('')
    setResult(null)
    setError('')
  }, [factorCategories])

  // Get ML alpha names from factorCategories
  const mlCat = factorCategories.find(c => c.key === 'ml_alpha')
  const mlAlphas: string[] = mlCat
    ? (mlCat.factors as (string | FactorInfo)[]).map((f: string | FactorInfo) => typeof f === 'string' ? f : (f.key || f.class_name || ''))
    : []

  const runDiagnostics = async () => {
    if (!selectedAlpha) return
    const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
    if (symbolList.length === 0) { setError('请填写股票池'); return }
    const token = ++diagTokenRef.current
    setLoading(true); setError(''); setResult(null)
    try {
      const resp = await mlAlphaDiagnostics({
        ml_alpha_name: selectedAlpha,
        symbols: symbolList,
        market,
        start_date: startDate,
        end_date: endDate,
      })
      if (diagTokenRef.current !== token) return  // superseded by input change or new request
      setResult(resp.data)
    } catch (e: unknown) {
      if (diagTokenRef.current !== token) return
      const err = e as { response?: { data?: { detail?: string } } }
      setError(err?.response?.data?.detail || '诊断失败')
    } finally { if (diagTokenRef.current === token) setLoading(false) }
  }

  return (
    <div className="mt-4 p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
      <h4 className="text-sm font-medium mb-2">ML Alpha 诊断</h4>
      <p className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
        评估 ML Alpha 的过拟合风险：训练/验证 IC 衰减 + 特征重要性稳定性 + 换手分析
      </p>

      {mlAlphas.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          暂无已注册的 ML Alpha。请在代码编辑器中创建并保存 ML Alpha 文件。
        </p>
      ) : (
        <>
          <div className="flex items-center gap-2 mb-2">
            <select value={selectedAlpha} onChange={e => setSelectedAlpha(e.target.value)}
              className="text-xs px-2 py-1 rounded flex-1" style={inputStyle}>
              <option value="">选择 ML Alpha...</option>
              {mlAlphas.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
            <button onClick={runDiagnostics} disabled={loading || !selectedAlpha}
              className="text-xs px-3 py-1 rounded font-medium"
              style={{ backgroundColor: '#059669', color: '#fff', opacity: loading || !selectedAlpha ? 0.5 : 1 }}>
              {loading ? '诊断中...' : '运行诊断'}
            </button>
          </div>

          {error && <p className="text-xs text-red-400 mb-2">{error}</p>}

          {result && (
            <div className="space-y-3">
              {/* Verdict badge */}
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium px-2 py-0.5 rounded"
                  style={{ backgroundColor: VERDICT_COLORS[result.verdict] || '#6b7280', color: '#fff' }}>
                  {VERDICT_LABELS[result.verdict] || result.verdict}
                </span>
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  过拟合分数: {result.overfitting_score != null ? result.overfitting_score.toFixed(3) : '-'}
                </span>
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  | 换手: {(result.avg_turnover * 100).toFixed(1)}%
                </span>
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  | 重训次数: {result.retrain_count}
                </span>
              </div>

              {/* Warnings */}
              {result.warnings.length > 0 && (
                <div className="text-xs p-2 rounded" style={{ backgroundColor: 'rgba(234, 179, 8, 0.1)', border: '1px solid rgba(234, 179, 8, 0.3)' }}>
                  {result.warnings.map((w, i) => <p key={i} className="mb-0.5">⚠️ {w}</p>)}
                </div>
              )}

              {/* Feature importance CV table */}
              {Object.keys(result.feature_importance_cv).length > 0 && (
                <div>
                  <h5 className="text-xs font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>特征重要性稳定性 (CV, 越低越稳)</h5>
                  <div className="grid grid-cols-2 gap-1 text-xs">
                    {Object.entries(result.feature_importance_cv).map(([feat, cv]) => (
                      <div key={feat} className="flex justify-between px-2 py-0.5 rounded" style={{ backgroundColor: 'var(--bg-secondary)' }}>
                        <span>{feat}</span>
                        <span style={{ color: cv != null && cv > 2 ? '#ef4444' : cv != null && cv > 1 ? '#eab308' : '#22c55e' }}>
                          {cv != null ? cv.toFixed(3) : '-'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* IS/OOS IC chart */}
              {result.ic_series.length > 0 && (
                <div>
                  <h5 className="text-xs font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
                    IS/OOS IC ({result.ic_series.length} 次重训)
                    <span className="ml-2">平均 IS: {fmt(result.mean_train_ic)} | OOS: {fmt(result.mean_oos_ic)}</span>
                  </h5>
                  <ReactECharts option={{
                    backgroundColor: 'transparent',
                    grid: { top: 30, right: 20, bottom: 30, left: 50 },
                    tooltip: { trigger: 'axis' },
                    legend: { data: ['IS IC', 'OOS IC'], textStyle: { color: '#8b949e', fontSize: 10 } },
                    xAxis: { type: 'category', data: result.ic_series.map(e => e.retrain_date), axisLabel: { color: '#8b949e', fontSize: 9 } },
                    yAxis: { type: 'value', axisLabel: { color: '#8b949e', fontSize: 9 } },
                    series: [
                      { name: 'IS IC', type: 'line', data: result.ic_series.map(e => e.train_ic), lineStyle: { color: '#3b82f6' }, itemStyle: { color: '#3b82f6' } },
                      { name: 'OOS IC', type: 'line', data: result.ic_series.map(e => e.oos_ic), lineStyle: { color: '#f97316' }, itemStyle: { color: '#f97316' } },
                    ],
                  }} style={{ height: 200 }} />
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
