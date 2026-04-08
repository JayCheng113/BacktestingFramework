import ReactECharts from 'echarts-for-react'
import { CHART } from './shared/chartTheme'
import type { PortfolioRunResult, ParamSchema, ActiveWeight } from '../types'
import type { BacktestSettingsValue } from './BacktestSettings'
import BacktestSettings from './BacktestSettings'
import DateRangePicker from './DateRangePicker'
import { useState, useEffect, useRef } from 'react'
import { getPortfolioRunHoldings } from '../api'
import { deployToLive } from '../api/live'
import EnsembleBuilder from './EnsembleBuilder'
import type { EnsembleConfig } from './EnsembleBuilder'
import { useToast } from './shared/Toast'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

// ── 实盘策略快速加载 (static, hoisted outside component) ──
// NOTE: EtfStockEnhance preset removed (codex review #1) — without individual
// stocks in the symbol pool it degrades to pure ETF rotation, misleading users.
const LIVE_PRESETS = [
  {
    id: 'macd_rotation',
    name: 'ETF MACD轮动',
    desc: '10只ETF, 20日均线动量 + 周线MACD过滤, 选前2等权, 周四调仓',
    strategy: 'EtfMacdRotation',
    params: { top_n: 2, rank_period: 20 } as Record<string, number>,
    symbols: '510500.SH,159915.SZ,515100.SH,159531.SZ,513100.SH,513880.SH,513260.SH,513600.SH,518880.SH,159985.SZ',
    freq: 'weekly',
    rebalWeekday: 3 as number | null,  // QMT: 周四 (weekday=3)
    color: '#2563eb',
  },
  {
    id: 'sector_switch',
    name: 'ETF行业宽基切换',
    desc: '22只ETF, 多因子加权 + 累积投票 + 宽基/行业切换, 选前1, 周五调仓',
    strategy: 'EtfSectorSwitch',
    params: { top_n: 1 } as Record<string, number>,
    symbols: '510300.SH,510500.SH,159915.SZ,510880.SH,513100.SH,513880.SH,513260.SH,513660.SH,518880.SH,159985.SZ,162411.SZ,512010.SH,512690.SH,515700.SH,159852.SZ,159813.SZ,159851.SZ,515220.SH,159869.SZ,515880.SH,512660.SH,512980.SH',
    freq: 'weekly',
    rebalWeekday: 4 as number | null,  // QMT: 周五 (weekday=4)
    color: '#059669',
  },
]

import { CATEGORY_LABELS, FACTOR_LABELS } from './shared/portfolioLabels'

// ── Types for PortfolioRunContent ────────────────────────────────

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

interface PortfolioWalkForwardResult {
  oos_sharpe: number | null
  oos_total_return: number | null
  oos_max_drawdown: number | null
  overfitting_score: number
  is_vs_oos_degradation: number
  n_splits: number
  fold_results?: Record<string, unknown>[]
  oos_equity_curve?: number[]
  oos_dates?: string[]
  oos_metrics?: Record<string, number>
  significance?: {
    is_significant: boolean
    p_value: number
  }
  is_sharpes?: number[]
  oos_sharpes?: number[]
  warnings?: string[]
}

interface SearchResultRow {
  rank: number
  params: Record<string, unknown>
  metrics?: Record<string, number | null>
  sharpe?: number | null
  total_return?: number | null
  max_drawdown?: number | null
  annualized_return?: number | null
  trade_count?: number | null
  warnings?: string[]
}

interface SearchMeta {
  sampled: number; completed: number; failed: number;
  total_combinations: number;
  failed_combos: Array<{ combo_index: number; params: Record<string, unknown>; error: string }>;
}

// Strategy params are dynamic (int/float/bool/str/multi_select).
type ParamValue = number | string | boolean | string[]

const metricLabels: Record<string, string> = {
  total_return: '总收益率', annualized_return: '年化收益率', sharpe_ratio: '夏普比率',
  sortino_ratio: '索提诺比率', max_drawdown: '最大回撤', max_drawdown_duration: '回撤持续(天)',
  benchmark_return: '基准收益率', alpha: '超额收益(Alpha)', beta: '市场敏感度(Beta)',
  trade_count: '交易次数', turnover_per_rebalance: '每次调仓换手率',
  annualized_volatility: '年化波动率', n_rebalances: '调仓次数',
  concentration_hhi: '持仓集中度',
}

const fmt = (v: number | null | undefined, pct = false) => {
  if (v == null) return '-'
  return pct ? `${(v * 100).toFixed(2)}%` : v.toFixed(4)
}

// Color-coded metric ratings for portfolio backtest results
const rateMetric = (key: string, v: number): { color: string; hint: string; tooltip: string } | null => {
  if (v == null || !isFinite(v)) return null
  switch (key) {
    case 'sharpe_ratio': {
      if (v >= 1.5) return { color: CHART.success, hint: '优秀', tooltip: '>=1.5优秀, >=1.0良好, >=0.5一般, <0亏损' }
      if (v >= 1.0) return { color: CHART.accent, hint: '良好', tooltip: '>=1.5优秀, >=1.0良好, >=0.5一般, <0亏损' }
      if (v >= 0.5) return { color: CHART.warn, hint: '一般', tooltip: '>=1.5优秀, >=1.0良好, >=0.5一般, <0亏损' }
      return { color: CHART.error, hint: v < 0 ? '亏损' : '偏弱', tooltip: '>=1.5优秀, >=1.0良好, >=0.5一般, <0亏损' }
    }
    case 'sortino_ratio': {
      if (v >= 2.0) return { color: CHART.success, hint: '优秀', tooltip: '>=2.0优秀, >=1.0良好, >=0.5一般' }
      if (v >= 1.0) return { color: CHART.accent, hint: '良好', tooltip: '>=2.0优秀, >=1.0良好, >=0.5一般' }
      if (v >= 0.5) return { color: CHART.warn, hint: '一般', tooltip: '>=2.0优秀, >=1.0良好, >=0.5一般' }
      return { color: CHART.error, hint: '偏弱', tooltip: '>=2.0优秀, >=1.0良好, >=0.5一般' }
    }
    case 'max_drawdown': {
      const a = Math.abs(v)
      if (a <= 0.1) return { color: CHART.success, hint: '低风险', tooltip: '<=10%低, <=20%中, <=30%高, >30%极高' }
      if (a <= 0.2) return { color: CHART.accent, hint: '可控', tooltip: '<=10%低, <=20%中, <=30%高, >30%极高' }
      if (a <= 0.3) return { color: CHART.warn, hint: '偏高', tooltip: '<=10%低, <=20%中, <=30%高, >30%极高' }
      return { color: CHART.error, hint: '高风险', tooltip: '<=10%低, <=20%中, <=30%高, >30%极高' }
    }
    case 'annualized_return': {
      if (v >= 0.2) return { color: CHART.success, hint: '高收益', tooltip: '>=20%高, >=10%中, >=0正, <0亏损' }
      if (v >= 0.1) return { color: CHART.accent, hint: '中等', tooltip: '>=20%高, >=10%中, >=0正, <0亏损' }
      if (v >= 0) return { color: CHART.warn, hint: '微利', tooltip: '>=20%高, >=10%中, >=0正, <0亏损' }
      return { color: CHART.error, hint: '亏损', tooltip: '>=20%高, >=10%中, >=0正, <0亏损' }
    }
    case 'alpha': {
      if (v >= 0.1) return { color: CHART.success, hint: '强超额', tooltip: '>=10%强, >=5%中, >=0正, <0跑输基准' }
      if (v >= 0.05) return { color: CHART.accent, hint: '正超额', tooltip: '>=10%强, >=5%中, >=0正, <0跑输基准' }
      if (v >= 0) return { color: CHART.warn, hint: '持平', tooltip: '>=10%强, >=5%中, >=0正, <0跑输基准' }
      return { color: CHART.error, hint: '跑输基准', tooltip: '>=10%强, >=5%中, >=0正, <0跑输基准' }
    }
    case 'annualized_volatility': {
      if (v <= 0.15) return { color: CHART.success, hint: '低波动', tooltip: '<=15%低, <=25%中, >25%高' }
      if (v <= 0.25) return { color: CHART.warn, hint: '中波动', tooltip: '<=15%低, <=25%中, >25%高' }
      return { color: CHART.error, hint: '高波动', tooltip: '<=15%低, <=25%中, >25%高' }
    }
    default: return null
  }
}

// WF OOS rating functions — hoisted to module scope (pure, no component deps).
// Thresholds are intentionally lower than backtest `rateMetric` because
// out-of-sample performance is expected to degrade 30-50% vs in-sample.
type Rating = { color: string; hint: string }
const rateWfSharpe = (v: number): Rating => {
  if (v >= 1.0) return { color: CHART.success, hint: '优秀' }
  if (v >= 0.5) return { color: CHART.accent, hint: '可接受' }
  if (v >= 0) return { color: CHART.warn, hint: '偏弱' }
  return { color: CHART.error, hint: '亏损' }
}
const rateWfReturn = (v: number): Rating => {
  if (v >= 0.2) return { color: CHART.success, hint: '高收益' }
  if (v >= 0.05) return { color: CHART.accent, hint: '正收益' }
  if (v >= 0) return { color: CHART.warn, hint: '微利' }
  return { color: CHART.error, hint: '亏损' }
}
const rateWfOverfit = (v: number): Rating => {
  if (v <= 0.2) return { color: CHART.success, hint: '稳健' }
  if (v <= 0.3) return { color: CHART.accent, hint: '轻微' }
  if (v <= 0.5) return { color: CHART.warn, hint: '中等' }
  return { color: CHART.error, hint: '严重过拟合' }
}
const rateWfPval = (sig: boolean, p: number): Rating => {
  if (sig && p < 0.01) return { color: CHART.success, hint: '极显著' }
  if (sig) return { color: CHART.accent, hint: '显著' }
  if (p < 0.1) return { color: CHART.warn, hint: '边缘' }
  return { color: CHART.error, hint: '不显著' }
}

interface Props {
  // Shared state
  symbols: string; setSymbols: (v: string) => void
  market: string; setMarket: (v: string) => void
  startDate: string; setStartDate: (v: string) => void
  endDate: string; setEndDate: (v: string) => void
  freq: string; setFreq: (v: string) => void
  rebalWeekday: number | null; setRebalWeekday: (v: number | null) => void
  settings: BacktestSettingsValue; setSettings: (v: BacktestSettingsValue) => void
  strategies: { name: string; description: string; parameters: Record<string, ParamSchema> }[]
  factors: string[]
  factorCategories: FactorCategory[]
  selected: string; setSelected: (v: string) => void
  strategyParams: Record<string, ParamValue>; updateParam: (key: string, value: ParamValue) => void
  currentSchema: Record<string, ParamSchema>
  currentDesc: string
  // Run state
  result: PortfolioRunResult | null
  loading: boolean
  wfResult: PortfolioWalkForwardResult | null; setWfResult: (v: PortfolioWalkForwardResult | null) => void
  wfLoading: boolean
  wfSplits: number; setWfSplits: (v: number) => void
  wfTrainRatio: number; setWfTrainRatio: (v: number) => void
  // Optimizer
  optimizer: string; setOptimizer: (v: string) => void
  riskAversion: number; setRiskAversion: (v: number) => void
  maxWeight: number; setMaxWeight: (v: number) => void
  maxIndustryWeight: number; setMaxIndustryWeight: (v: number) => void
  covLookback: number; setCovLookback: (v: number) => void
  // V2.12.1: Index benchmark
  indexBenchmark: string; setIndexBenchmark: (v: string) => void
  trackingError: number; setTrackingError: (v: number) => void
  // Risk control
  riskControl: boolean; setRiskControl: (v: boolean) => void
  maxDrawdown: number; setMaxDrawdown: (v: number) => void
  drawdownReduce: number; setDrawdownReduce: (v: number) => void
  drawdownRecovery: number; setDrawdownRecovery: (v: number) => void
  maxTurnover: number; setMaxTurnover: (v: number) => void
  showOptimizer: boolean; setShowOptimizer: (v: boolean) => void
  showRiskControl: boolean; setShowRiskControl: (v: boolean) => void
  showAttribution: boolean; setShowAttribution: (v: boolean) => void
  // Ensemble
  ensembleConfigRef: React.MutableRefObject<EnsembleConfig | null>
  // Search
  searchMode: boolean; setSearchMode: (v: boolean) => void
  comboSearch: boolean; setComboSearch: (v: boolean) => void
  searchGrid: Record<string, string>; setSearchGrid: (v: Record<string, string> | ((prev: Record<string, string>) => Record<string, string>)) => void
  expandedParams: Record<string, boolean>; setExpandedParams: (v: Record<string, boolean> | ((prev: Record<string, boolean>) => Record<string, boolean>)) => void
  searchLoading: boolean; searchResults: SearchResultRow[]
  // V2.12.2 codex: search counts + failed combos so UI surfaces failures
  // that prior version silently dropped from results.
  searchMeta?: SearchMeta | null
  // Handlers
  handleRun: () => void
  handleWalkForward: () => void
  handleSearch: () => void
  exportEquityCurve: () => void
  exportTrades: () => void
  // Render helper
  renderParamInput: (key: string, schema: ParamSchema) => React.ReactNode
}

export default function PortfolioRunContent(props: Props) {
  const { showToast } = useToast()
  const {
    symbols, setSymbols, market, setMarket,
    startDate, setStartDate, endDate, setEndDate, freq, setFreq, rebalWeekday, setRebalWeekday,
    settings, setSettings, strategies, factors, factorCategories,
    selected, setSelected, strategyParams, updateParam, currentSchema, currentDesc,
    result, loading, wfResult, setWfResult, wfLoading, wfSplits, setWfSplits, wfTrainRatio, setWfTrainRatio,
    optimizer, setOptimizer, riskAversion, setRiskAversion, maxWeight, setMaxWeight,
    maxIndustryWeight, setMaxIndustryWeight, covLookback, setCovLookback,
    indexBenchmark, setIndexBenchmark, trackingError, setTrackingError,
    riskControl, setRiskControl, maxDrawdown, setMaxDrawdown, drawdownReduce, setDrawdownReduce,
    drawdownRecovery, setDrawdownRecovery, maxTurnover, setMaxTurnover,
    showOptimizer, setShowOptimizer, showRiskControl, setShowRiskControl, showAttribution, setShowAttribution,
    ensembleConfigRef,
    searchMode, setSearchMode, comboSearch, setComboSearch, searchGrid, setSearchGrid,
    searchLoading, searchResults, searchMeta,
    handleRun, handleWalkForward, handleSearch, exportEquityCurve, exportTrades,
    renderParamInput,
  } = props

  // V2.14 B3: Ensemble configuration
  const isEnsemble = selected === 'StrategyEnsemble'

  // V2.15 C3: Deploy to paper trading
  const [deployLoading, setDeployLoading] = useState(false)
  const handleDeploy = async () => {
    if (!result?.run_id) return
    const name = window.prompt('部署名称', selected || '策略')
    if (!name) return
    setDeployLoading(true)
    try {
      // V2.16 S1: wf_metrics are now persisted server-side by /walk-forward.
      // DeployGate reads them from DB — no need to pass from frontend.
      const res = await deployToLive({ source_run_id: result.run_id, name })
      showToast('success', `部署成功！ID: ${res.data.deployment_id}\n请前往 "模拟盘" 页面查看和审批。`)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      showToast('error', '部署失败: ' + (err?.response?.data?.detail || err?.message || ''))
    } finally {
      setDeployLoading(false)
    }
  }

  // Local state for full weights loading
  const [fullWeights, setFullWeights] = useState<{ date: string; weights: Record<string, number> }[] | null>(null)
  const [weightsLoading, setWeightsLoading] = useState(false)
  // V2.12.2 codex round 8: track the current result.run_id in a ref so
  // the async handler can check whether the run is still current on
  // resume. Prior version only cleared fullWeights on run_id change but
  // did not invalidate in-flight requests — a late response from the
  // previous run would still write its data back, polluting the new run.
  //
  // NOTE: this guard works in tandem with the `setFullWeights(null)`
  // clearing effect below — if a microtask slips through the ref-sync
  // timing window (React batches setState → useEffect → ref update),
  // the sibling clearing effect still wipes any stale write in the
  // same batch. Do not remove the clearing effect without replacing
  // the guard with a tighter synchronization primitive.
  const currentRunIdRef = useRef<string | undefined>(result?.run_id)
  useEffect(() => {
    currentRunIdRef.current = result?.run_id
  }, [result?.run_id])

  // V2.12.1 post-review (codex #21): clear stale fullWeights whenever the
  // underlying run changes. Prior version only set fullWeights on manual
  // "load full history" click and never reset it, so a new run inherited
  // the previous run's full-weights snapshot (weightsToShow would still
  // point to the old run's data).
  useEffect(() => {
    setFullWeights(null)
    setWeightsLoading(false)
  }, [result?.run_id])

  const handleLoadFullWeights = async () => {
    const requestedRunId = result?.run_id
    if (!requestedRunId) return
    setWeightsLoading(true)
    try {
      // V2.12.2 codex: call /holdings to get ACTUAL post-execution weights
      // (daily weights_history from engine) instead of /weights which
      // returns rebalance target weights. Prior version mixed two semantics
      // under the same "加载完整历史" button: before click the pie chart
      // showed actual latest_weights; after click the table switched to
      // rebalance targets. Users could not tell which was which.
      const res = await getPortfolioRunHoldings(requestedRunId)
      // V2.12.2 codex round 8: only apply the response if the current run
      // is STILL the one we requested. Prior version wrote the data
      // unconditionally, so switching runs during fetch caused the old
      // run's holdings table to appear under the new run's pie chart.
      if (currentRunIdRef.current !== requestedRunId) return  // superseded
      setFullWeights(res.data.weights_history || [])
    } catch (e: unknown) {
      if (currentRunIdRef.current === requestedRunId) {
        const err = e as { response?: { data?: { detail?: string } }; message?: string }
        showToast('error', '加载完整历史失败: ' + (err?.response?.data?.detail || err?.message || ''))
      }
    } finally {
      if (currentRunIdRef.current === requestedRunId) setWeightsLoading(false)
    }
  }

  const equityOption = result ? {
    backgroundColor: CHART.bg,
    title: { text: '组合净值曲线', textStyle: { color: CHART.text, fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' as const },
    legend: { data: ['组合', settings.benchmark ? `基准(${settings.benchmark})` : '基准(现金)'], textStyle: { color: CHART.textSecondary }, top: 25 },
    grid: { left: 70, right: 20, top: 55, bottom: 30 },
    xAxis: { type: 'category' as const, data: result.dates.map(d => d.slice(0, 10)), axisLabel: { color: CHART.textSecondary, rotate: 30, fontSize: 9 } },
    yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: CHART.grid } }, axisLabel: { color: CHART.textSecondary } },
    series: [
      { name: '组合', type: 'line' as const, data: result.equity_curve, lineStyle: { color: CHART.accent }, showSymbol: false },
      { name: settings.benchmark ? `基准(${settings.benchmark})` : '基准(现金)', type: 'line' as const, data: result.benchmark_curve, lineStyle: { color: CHART.textSecondary, type: 'dashed' as const }, showSymbol: false },
    ],
  } : null

  // BUG-07: guard on result existence — never show stale fullWeights when
  // result is null (input changed, no current run). Timing-proof: doesn't
  // depend on useEffect ordering or React batching.
  const weightsToShow = result ? (fullWeights || result.weights_history) : undefined

  // Fix codex #2: deferred param hydration — wait for schema to update after
  // strategy switch instead of using fragile setTimeout(50ms).
  const [pendingPresetParams, setPendingPresetParams] = useState<Record<string, ParamValue> | null>(null)
  // Search result sorting — reset when new results arrive
  const [searchSortKey, setSearchSortKey] = useState<string>('')
  const [searchSortDir, setSearchSortDir] = useState<'asc' | 'desc'>('desc')
  useEffect(() => { setSearchSortKey(''); setSearchSortDir('desc') }, [searchResults])

  useEffect(() => {
    if (pendingPresetParams && Object.keys(currentSchema).length > 0) {
      for (const [k, v] of Object.entries(pendingPresetParams)) {
        if (k in currentSchema) {
          updateParam(k, v)
        }
      }
      setPendingPresetParams(null)
    }
  }, [currentSchema, pendingPresetParams]) // eslint-disable-line react-hooks/exhaustive-deps

  const applyPreset = (preset: typeof LIVE_PRESETS[number]) => {
    setSelected(preset.strategy as string)
    setSymbols(preset.symbols as string)
    setFreq(preset.freq as string)
    setRebalWeekday('rebalWeekday' in preset ? (preset as Record<string, unknown>).rebalWeekday as number | null : null)
    setMarket('cn_stock')
    setPendingPresetParams({ ...preset.params } as Record<string, ParamValue>)
    showToast('success', `已加载实盘预设: ${preset.name}`)
  }

  return (
    <>
      {/* 实盘策略快速加载 */}
      <div className="mb-3 p-3 rounded" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>实盘策略预设</span>
          <span className="text-xs" style={{ color: 'var(--text-muted)', fontSize: '10px' }}>一键加载 QMT 实盘配置</span>
        </div>
        <div className="flex gap-2 flex-wrap">
          {LIVE_PRESETS.map(p => (
            <button key={p.id} onClick={() => applyPreset(p)}
              className="text-xs px-3 py-1.5 rounded flex flex-col items-start"
              style={{ border: `1px solid ${p.color}40`, backgroundColor: `${p.color}10`, minWidth: 160 }}>
              <span style={{ color: p.color, fontWeight: 600 }}>{p.name}</span>
              <span style={{ color: 'var(--text-secondary)', fontSize: '10px', marginTop: 2 }}>{p.desc}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="p-4 rounded mb-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-sm font-medium mb-3">组合回测配置</h3>
        <div className="flex flex-wrap gap-3 items-end mb-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>策略</label>
            <select value={selected} onChange={e => setSelected(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
              {strategies.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
          {/* Dynamic strategy parameters from schema — or EnsembleBuilder */}
          {!isEnsemble && Object.entries(currentSchema).map(([key, schema]) => renderParamInput(key, schema))}
          {/* V2.14 B3: EnsembleBuilder when StrategyEnsemble selected */}
          {isEnsemble && (
            <div className="w-full mt-2">
              <EnsembleBuilder
                strategies={strategies}
                factors={factors}
                onChange={(cfg) => { ensembleConfigRef.current = cfg }}
              />
            </div>
          )}
          {/* V2.11.1: AlphaCombiner sub-panel when factor=alpha_combiner */}
          {strategyParams.factor === 'alpha_combiner' && (
            <div className="col-span-full p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
              <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>子因子 (多选)</label>
              <div className="flex flex-wrap gap-1 mb-2">
                {factors.filter(f => f !== 'alpha_combiner').map(f => (
                  <button key={f} onClick={() => {
                    const cur = Array.isArray(strategyParams.alpha_factors) ? strategyParams.alpha_factors : []
                    if (cur.includes(f)) updateParam('alpha_factors', cur.filter((x: string) => x !== f))
                    else updateParam('alpha_factors', [...cur, f])
                  }}
                    className="text-xs px-2 py-0.5 rounded"
                    style={{ backgroundColor: (Array.isArray(strategyParams.alpha_factors) ? strategyParams.alpha_factors : []).includes(f) ? 'var(--color-accent)' : 'var(--bg-secondary)',
                             color: (Array.isArray(strategyParams.alpha_factors) ? strategyParams.alpha_factors : []).includes(f) ? '#fff' : 'var(--text-secondary)',
                             border: '1px solid var(--border)' }}>
                    {FACTOR_LABELS[f] || f}
                  </button>
                ))}
              </div>
              <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>合成方法</label>
              <select value={String(strategyParams.alpha_method || 'equal')} onChange={e => updateParam('alpha_method', e.target.value)}
                className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
                <option value="equal">等权</option>
                <option value="ic">IC加权 (选股能力越强权重越大)</option>
                <option value="icir">ICIR加权 (又强又稳的权重更大)</option>
              </select>
              {/* V2.12.1: Orthogonalize checkbox */}
              <label className="flex items-center gap-1.5 text-xs mt-2 cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
                <input type="checkbox" checked={!!strategyParams.orthogonalize} onChange={e => updateParam('orthogonalize', e.target.checked)} />
                因子正交化
                <span style={{ color: 'var(--text-muted)', fontSize: '10px' }}>(Gram-Schmidt去除因子间共线性)</span>
              </label>
            </div>
          )}
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>换仓频率</label>
            <select value={freq} onChange={e => { setFreq(e.target.value); if (e.target.value !== 'weekly') setRebalWeekday(null) }} className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
              <option value="daily">日度</option>
              <option value="weekly">周度</option>
              <option value="monthly">月度</option>
              <option value="quarterly">季度</option>
            </select>
          </div>
          {freq === 'weekly' && (
            <div className="flex flex-col gap-1">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>调仓日</label>
              <select value={rebalWeekday === null ? '' : String(rebalWeekday)}
                onChange={e => setRebalWeekday(e.target.value === '' ? null : Number(e.target.value))}
                className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
                <option value="">默认 (周末)</option>
                <option value="0">周一</option>
                <option value="1">周二</option>
                <option value="2">周三</option>
                <option value="3">周四</option>
                <option value="4">周五</option>
              </select>
            </div>
          )}
        </div>
        {currentDesc && (
          <div className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>{currentDesc}</div>
        )}
        <div className="mb-3">
          <DateRangePicker startDate={startDate} endDate={endDate} onStartChange={setStartDate} onEndChange={setEndDate} />
        </div>
        <div className="mb-3">
          <BacktestSettings value={settings} onChange={setSettings} />
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
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>股票池 (逗号分隔)</label>
            <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH,513880.SH,513260.SH,159985.SZ')}
              className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>宽基ETF</button>
            <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,515100.SH,159531.SZ,513100.SH,513880.SH,513260.SH,513600.SH,518880.SH,159985.SZ')}
              className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>ETF轮动池</button>
            <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,510880.SH,513100.SH,513880.SH,513260.SH,513660.SH,518880.SH,159985.SZ,162411.SZ,512010.SH,512690.SH,515700.SH,159852.SZ,159813.SZ,159851.SZ,515220.SH,159869.SZ,515880.SH,512660.SH,512980.SH')}
              className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>行业+宽基22只</button>
          </div>
          <textarea value={symbols} onChange={e => setSymbols(e.target.value)} rows={2} className="w-full px-3 py-1.5 rounded text-sm font-mono" style={inputStyle} />
        </div>
        {/* V2.12: Optimizer Panel */}
        <div className="border rounded mt-2" style={{ borderColor: 'var(--border)' }}>
          <button onClick={() => setShowOptimizer(!showOptimizer)} className="w-full text-left px-3 py-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            {showOptimizer ? '▼' : '▶'} 组合优化
          </button>
          {showOptimizer && (
            <div className="px-3 pb-3 space-y-2">
              <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>优化方法
                <select value={optimizer} onChange={e => setOptimizer(e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle}>
                  <option value="none">不优化</option>
                  <option value="mean_variance">均值-方差</option>
                  <option value="min_variance">最小方差</option>
                  <option value="risk_parity">风险平价</option>
                </select>
              </label>
              {optimizer === 'mean_variance' && (
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>风险厌恶系数 λ
                  <input type="number" step="0.1" value={riskAversion} onChange={e => setRiskAversion(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                </label>
              )}
              {optimizer !== 'none' && (<>
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>协方差回看期 (天)
                  <input type="number" value={covLookback} onChange={e => setCovLookback(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                </label>
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>单股上限 (%)
                  <input type="number" step="1" value={maxWeight} onChange={e => setMaxWeight(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                </label>
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>行业上限 (%)
                  <input type="number" step="5" value={maxIndustryWeight} onChange={e => setMaxIndustryWeight(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                </label>
                {/* V2.12.1: Index benchmark + tracking error */}
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>指数基准
                  <select value={indexBenchmark} onChange={e => setIndexBenchmark(e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle}>
                    <option value="">无 (绝对收益)</option>
                    {market === 'cn_stock' ? <>
                      <option value="000300">沪深300</option>
                      <option value="000905">中证500</option>
                      <option value="000852">中证1000</option>
                    </> : (
                      <option value="" disabled>暂不支持非A股指数基准</option>
                    )}
                  </select>
                </label>
                {indexBenchmark && (
                  <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>跟踪误差上限 (%)
                    <input type="number" step="0.5" min={0} value={trackingError} onChange={e => setTrackingError(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                  </label>
                )}
              </>)}
            </div>
          )}
        </div>
        {/* V2.12: Risk Control Panel */}
        <div className="border rounded mt-2" style={{ borderColor: 'var(--border)' }}>
          <button onClick={() => setShowRiskControl(!showRiskControl)} className="w-full text-left px-3 py-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            {showRiskControl ? '▼' : '▶'} 风险控制
          </button>
          {showRiskControl && (
            <div className="px-3 pb-3 space-y-2">
              <label className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
                <input type="checkbox" checked={riskControl} onChange={e => setRiskControl(e.target.checked)} /> 启用风控
              </label>
              {riskControl && (<>
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>最大回撤阈值 (%)
                  <input type="number" step="5" value={maxDrawdown} onChange={e => setMaxDrawdown(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                </label>
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>回撤减仓比例 (%)
                  <input type="number" step="10" value={drawdownReduce} onChange={e => setDrawdownReduce(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                </label>
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>回撤恢复阈值 (%)
                  <input type="number" step="5" value={drawdownRecovery} onChange={e => setDrawdownRecovery(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                </label>
                <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>换手率上限 (%)
                  <input type="number" step="10" value={maxTurnover} onChange={e => setMaxTurnover(+e.target.value)} className="w-full mt-1 rounded px-2 py-1 text-sm" style={inputStyle} />
                </label>
              </>)}
            </div>
          )}
        </div>
        <div className="flex gap-2 flex-wrap mt-2">
          <button onClick={handleRun} disabled={loading} className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: loading ? CHART.border : 'var(--color-accent)' }}>
            {loading ? '运行中...' : '运行组合回测'}
          </button>
          <button onClick={handleWalkForward} disabled={wfLoading} className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: wfLoading ? CHART.border : '#7c3aed' }}>
            {wfLoading ? '验证中...' : '前推验证'}
          </button>
          {!isEnsemble && (
            <button onClick={() => setSearchMode(!searchMode)} className="px-3 py-1.5 rounded text-sm font-medium"
              style={{ backgroundColor: searchMode ? '#1e6b3a' : 'var(--bg-primary)', color: searchMode ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
              参数搜索
            </button>
          )}
          {result && (
            <button onClick={handleDeploy} disabled={deployLoading || !wfResult}
              className="px-3 py-1.5 rounded text-sm font-medium text-white"
              style={{ backgroundColor: (deployLoading || !wfResult) ? CHART.border : '#059669' }}
              title={wfResult ? '包含前推验证结果' : '请先运行前推验证'}>
              {deployLoading ? '部署中...' : !wfResult ? '需先前推验证' : '部署到模拟盘'}
            </button>
          )}
          <input type="number" value={wfSplits} min={2} max={20} onChange={e => setWfSplits(Number(e.target.value) || 5)}
            className="w-14 px-2 py-1.5 rounded text-xs" style={inputStyle}
            title="将数据分成N段，每段轮流做测试集。折数越多验证越充分，但每段越短" />
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>折</span>
          <input type="number" value={wfTrainRatio} min={0.1} max={0.9} step={0.1} onChange={e => setWfTrainRatio(Number(e.target.value) || 0.7)}
            className="w-16 px-2 py-1.5 rounded text-xs" style={inputStyle}
            title="每段中用于训练的比例。0.7=70%训练+30%测试，越高训练数据越多但测试段越短" />
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>训练</span>
          <span className="text-xs" style={{ color: 'var(--text-muted)', fontSize: '10px' }}>
            ({wfSplits}段 × {Math.round(wfTrainRatio * 100)}%训练/{Math.round((1 - wfTrainRatio) * 100)}%测试)
          </span>
        </div>
      </div>

      {/* V2.11.1: Parameter Search Panel */}
      {searchMode && (
        <div className="p-4 rounded mb-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <h4 className="text-sm font-medium mb-1">参数搜索 — {strategies.find(s => s.name === selected)?.name || selected}</h4>
          <p className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
            为当前策略的每个参数设置多个候选值（逗号分隔），系统自动组合并按夏普排名。
          </p>
          {/* V2.14 B2: combo search toggle for multi_select params */}
          {Object.values(currentSchema).some(s => s.type === 'multi_select') && (
            <div className="flex items-center gap-3 mb-2">
              <label className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                <input type="checkbox" checked={comboSearch} onChange={e => setComboSearch(e.target.checked)} />
                组合搜索 (自动生成所有因子子集)
              </label>
              {comboSearch && (() => {
                const msKey = Object.entries(currentSchema).find(([, s]) => s.type === 'multi_select')?.[0]
                const selCount = msKey ? (searchGrid[msKey] || '').split(',').filter(Boolean).length : 0
                const psSize = selCount > 0 ? (1 << selCount) - 1 : 0
                const over = psSize > 64
                return (
                  <span className="text-xs" style={{ color: over ? CHART.error : 'var(--text-secondary)' }}>
                    {selCount > 0 ? `${selCount} 因子 → ${psSize} 种组合` : '请先选择因子'}
                    {over && ' (超过 64 上限，请减少因子)'}
                  </span>
                )
              })()}
            </div>
          )}
          <div className="space-y-2 mb-3">
            {Object.entries(currentSchema).map(([key, schema]) => {
              const label = schema.label || key
              const gridVal = searchGrid[key] || ''

              // select/multi_select: show clickable buttons (factor-style)
              if (schema.type === 'select' || schema.type === 'multi_select') {
                const options: string[] = schema.options ?? (factors.length > 0 ? factors.filter(f => f !== 'alpha_combiner') : [])
                const selectedVals = gridVal ? gridVal.split(',').filter(Boolean) : []
                const toggleVal = (v: string) => {
                  const cur = selectedVals.includes(v) ? selectedVals.filter(x => x !== v) : [...selectedVals, v]
                  setSearchGrid(prev => ({ ...prev, [key]: cur.join(',') }))
                }

                // Group by category if available and this is a factor-type param
                const useCategories = !schema.options && factorCategories.length > 0
                return (
                  <div key={key}>
                    <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>
                      {label} {comboSearch && schema.type === 'multi_select'
                        ? '(选中因子将自动生成所有子集组合)'
                        : schema.type === 'multi_select'
                          ? '(多选，所有勾选作为一个组合)'
                          : '(多选)'}
                    </label>
                    {useCategories ? factorCategories.map(cat => {
                      const catFactors = (Array.isArray(cat.factors) ? cat.factors : [])
                        .map((f: string | FactorInfo) => typeof f === 'string' ? f : (f.key || f.class_name || ''))
                        .filter((f: string) => f && f !== 'alpha_combiner')
                      if (catFactors.length === 0) return null
                      return (
                        <div key={cat.key} className="mb-1">
                          <span className="text-xs mr-1" style={{ color: 'var(--text-muted)' }}>{CATEGORY_LABELS[cat.key] || cat.label}:</span>
                          <span className="inline-flex flex-wrap gap-1">
                            {catFactors.map((f: string) => (
                              <button key={f} onClick={() => toggleVal(f)} className="text-xs px-2 py-0.5 rounded"
                                style={{ backgroundColor: selectedVals.includes(f) ? 'var(--color-accent)' : 'var(--bg-primary)',
                                         color: selectedVals.includes(f) ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                                {FACTOR_LABELS[f] || f}
                              </button>
                            ))}
                          </span>
                        </div>
                      )
                    }) : (
                      <div className="flex flex-wrap gap-1">
                        {options.map(o => (
                          <button key={o} onClick={() => toggleVal(o)} className="text-xs px-2 py-0.5 rounded"
                            style={{ backgroundColor: selectedVals.includes(o) ? 'var(--color-accent)' : 'var(--bg-primary)',
                                     color: selectedVals.includes(o) ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                            {FACTOR_LABELS[o] || o}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )
              }

              // int/float: comma-separated input
              return (
                <div key={key} className="flex items-center gap-2">
                  <label className="text-xs w-24 shrink-0" style={{ color: 'var(--text-secondary)' }}>{label}</label>
                  <input value={gridVal} onChange={e => setSearchGrid(prev => ({ ...prev, [key]: e.target.value }))}
                    className="flex-1 px-3 py-1.5 rounded text-sm" style={inputStyle}
                    placeholder={`多个值用逗号分隔，如 ${schema.min ?? 3},${schema.default ?? 5},${schema.max ?? 10}`} />
                </div>
              )
            })}
          </div>
          {(() => {
            const msKey = comboSearch ? Object.entries(currentSchema).find(([, s]) => s.type === 'multi_select')?.[0] : undefined
            const psOver = msKey ? ((1 << (searchGrid[msKey] || '').split(',').filter(Boolean).length) - 1) > 64 : false
            return (
              <button onClick={handleSearch} disabled={searchLoading || psOver}
                className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: (searchLoading || psOver) ? CHART.border : '#1e6b3a' }}>
                {searchLoading ? '搜索中...' : psOver ? '因子过多' : '开始搜索'}
              </button>
            )
          })()}
          {/* V2.12.2 codex: show sampled/completed/failed counts so users
              can see when combos were silently dropped due to errors. */}
          {searchMeta && (searchMeta.sampled > 0 || searchMeta.failed > 0) && (
            <div className="mt-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
              共 {searchMeta.total_combinations} 种组合, 采样 {searchMeta.sampled},
              成功 <span style={{ color: CHART.success }}>{searchMeta.completed}</span>
              {searchMeta.failed > 0 && (
                <>
                  , 失败 <span style={{ color: CHART.error, fontWeight: 600 }}>{searchMeta.failed}</span>
                </>
              )}
            </div>
          )}
          {searchMeta && searchMeta.failed > 0 && searchMeta.failed_combos.length > 0 && (
            <details className="mt-1">
              <summary className="text-xs cursor-pointer" style={{ color: '#f59e0b' }}>
                展开失败详情 ({searchMeta.failed} 条)
              </summary>
              <div className="mt-1 px-2 py-1 rounded text-xs" style={{ backgroundColor: '#3b2a1a', border: '1px solid #6b4c2a', maxHeight: 200, overflowY: 'auto' }}>
                {searchMeta.failed_combos.slice(0, 20).map((fc, i) => (
                  <div key={i} style={{ color: '#f59e0b', marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-muted)' }}>#{fc.combo_index + 1}</span> {JSON.stringify(fc.params)}: {fc.error}
                  </div>
                ))}
                {searchMeta.failed_combos.length > 20 && (
                  <div style={{ color: 'var(--text-muted)' }}>... 还有 {searchMeta.failed_combos.length - 20} 条</div>
                )}
              </div>
            </details>
          )}
          {searchResults.length > 0 ? (() => {
            const sortableColumns: { key: string; label: string; field: keyof SearchResultRow }[] = [
              { key: 'sharpe', label: '夏普比率', field: 'sharpe' },
              { key: 'total_return', label: '总收益率', field: 'total_return' },
              { key: 'annualized_return', label: '年化收益率', field: 'annualized_return' },
              { key: 'max_drawdown', label: '最大回撤', field: 'max_drawdown' },
              { key: 'trade_count', label: '交易次数', field: 'trade_count' },
            ]
            const sorted = [...searchResults].sort((a, b) => {
              if (!searchSortKey) return 0
              const rawA = a[searchSortKey as keyof SearchResultRow] as number | null | undefined
              const rawB = b[searchSortKey as keyof SearchResultRow] as number | null | undefined
              if (rawA == null && rawB == null) return 0
              if (rawA == null) return 1   // nulls always last
              if (rawB == null) return -1
              const isAbs = searchSortKey === 'max_drawdown'
              const av = isAbs ? Math.abs(rawA) : rawA
              const bv = isAbs ? Math.abs(rawB) : rawB
              return searchSortDir === 'asc' ? av - bv : bv - av
            })
            return (
              <div className="mt-3">
                <div className="text-xs mb-1 px-1" style={{ color: 'var(--text-muted)', fontSize: '10px' }}>
                  点击列头可按该指标排序 (再次点击切换升序/降序){searchSortKey ? `，当前: ${sortableColumns.find(c => c.field === searchSortKey)?.label ?? searchSortKey} ${searchSortDir === 'desc' ? '降序' : '升序'}` : ''}
                </div>
                <div className="overflow-x-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                  <thead><tr style={{ backgroundColor: 'var(--bg-primary)' }}>
                    <th className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>#</th>
                    <th className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>参数</th>
                    {sortableColumns.map(col => (
                      <th key={col.key} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)', cursor: 'pointer', userSelect: 'none' }}
                        onClick={() => {
                          if (searchSortKey === col.field) {
                            setSearchSortDir(prev => prev === 'desc' ? 'asc' : 'desc')
                          } else {
                            setSearchSortKey(col.field as string)
                            setSearchSortDir(col.field === 'max_drawdown' ? 'asc' : 'desc')
                          }
                        }}>
                        {col.label} {searchSortKey === col.field ? (searchSortDir === 'desc' ? '▼' : '▲') : '↕'}
                      </th>
                    ))}
                  </tr></thead>
                  <tbody>{sorted.map((r, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: i === 0 ? 'rgba(34,197,94,0.08)' : i % 2 === 0 ? 'rgba(255,255,255,0.04)' : 'transparent' }}>
                      <td className="px-3 py-1.5 font-medium">{i + 1}</td>
                      <td className="px-3 py-1.5">{Object.entries(r.params || {}).map(([k, v]) => {
                        const label = currentSchema[k]?.label || k
                        const val = typeof v === 'string' ? (FACTOR_LABELS[v] || v) : String(v)
                        return `${label}=${val}`
                      }).join(', ')}</td>
                      <td className="px-3 py-1.5" style={{ color: (r.sharpe || 0) > 1 ? CHART.success : 'var(--text-primary)' }}>{r.sharpe?.toFixed(3) ?? '-'}</td>
                      <td className="px-3 py-1.5">{r.total_return != null ? (r.total_return * 100).toFixed(1) + '%' : '-'}</td>
                      <td className="px-3 py-1.5">{r.annualized_return != null ? (r.annualized_return * 100).toFixed(1) + '%' : '-'}</td>
                      <td className="px-3 py-1.5" style={{ color: 'var(--color-down)' }}>{r.max_drawdown != null ? (r.max_drawdown * 100).toFixed(1) + '%' : '-'}</td>
                      <td className="px-3 py-1.5">{r.trade_count ?? '-'}</td>
                    </tr>
                  ))}</tbody>
                </table>
                </div>
              </div>
            )
          })() : searchMeta && !searchLoading ? (
            <div className="mt-3 py-6 text-center text-sm" style={{ color: 'var(--text-secondary)' }}>搜索结果为空</div>
          ) : null}
        </div>
      )}

      {result && (
        <div className="p-4 rounded" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          {result.symbols_skipped && result.symbols_skipped.length > 0 && (
            <div className="mb-3 px-3 py-2 rounded text-xs" style={{ backgroundColor: '#3b2a1a', border: '1px solid #6b4c2a', color: '#f59e0b' }}>
              {result.symbols_skipped.length} 只标的在回测起始日无数据: {result.symbols_skipped.join(', ')}
              （可能尚未上市，有数据后会自动纳入）
            </div>
          )}
          {result.warnings && result.warnings.length > 0 && (
            <div className="mb-3 px-3 py-2 rounded text-xs" style={{ backgroundColor: '#3b2a1a', border: '1px solid #6b4c2a', color: '#f59e0b' }}>
              {result.warnings.map((w, i) => <div key={i}>{w}</div>)}
            </div>
          )}
          <div className="flex gap-2 mb-3">
            <button onClick={exportEquityCurve} className="text-xs px-2 py-1 rounded" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>导出净值CSV</button>
            {result.trades.length > 0 && <button onClick={exportTrades} className="text-xs px-2 py-1 rounded" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>导出交易CSV</button>}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            {Object.entries(result.metrics).filter(([k]) => k in metricLabels).map(([k, v]) => {
              const rating = rateMetric(k, v as number)
              const displayValue = ['total_return', 'annualized_return', 'max_drawdown', 'annualized_volatility', 'turnover_per_rebalance', 'benchmark_return', 'alpha'].includes(k) ? fmt(v as number, true) : k === 'trade_count' || k === 'n_rebalances' || k === 'max_drawdown_duration' ? String(v) : fmt(v as number)
              return (
                <div key={k} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }} title={rating?.tooltip}>
                  <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{metricLabels[k] || k}</div>
                  <div className="text-sm font-medium" style={{ color: rating?.color || 'var(--text-primary)' }}>
                    {displayValue}
                  </div>
                  {rating && <div className="text-xs mt-0.5" style={{ color: rating.color, opacity: 0.8 }}>{rating.hint}</div>}
                </div>
              )
            })}
          </div>
          {equityOption && <ReactECharts option={equityOption} style={{ height: 300 }} />}
          {/* 持仓分布饼图.
              V2.12.2 codex round 7: after round 5 weights_history is daily
              drift-adjusted actual holdings (not rebalance target snapshots).
              `latest_weights` = the LAST NON-EMPTY daily entry, which is
              the actual held positions at market close on the last trading
              day before any terminal liquidation. Prior label "最后一次
              调仓目标 (期末已清仓)" implied this was the last rebalance
              target — misleading because the last non-empty day may not
              be a rebalance day, and the data is drift-adjusted actual,
              not target. New labels accurately describe the semantic:
              - Normal: "最新持仓分布" (last day of backtest = current)
              - Terminal liquidation: "期末前最后持仓 (次日已全部清仓)"
                — explicitly signals this is the pre-liquidation snapshot. */}
          {result.latest_weights && Object.keys(result.latest_weights).length > 0 && (
            <div className="mt-3">
              <ReactECharts option={{
                backgroundColor: CHART.bg,
                title: {
                  text: result.terminal_liquidated
                    ? '期末前最后持仓 (次日已全部清仓)'
                    : '最新持仓分布',
                  textStyle: { color: CHART.text, fontSize: 12 }, left: 'center',
                },
                tooltip: { trigger: 'item' as const, formatter: '{b}: {d}%' },
                series: [{
                  type: 'pie', radius: ['30%', '55%'], center: ['50%', '55%'],
                  label: { color: CHART.textSecondary, fontSize: 10 },
                  data: [
                    ...Object.entries(result.latest_weights)
                      .filter(([, w]) => w > 0.001)
                      .sort((a, b) => b[1] - a[1])
                      .map(([sym, w]) => ({ name: sym, value: Math.round(w * 10000) / 100 })),
                    ...(1 - Object.values(result.latest_weights).reduce((s, w) => s + w, 0) > 0.001
                      ? [{ name: '现金', value: Math.round((1 - Object.values(result.latest_weights).reduce((s, w) => s + w, 0)) * 10000) / 100 }]
                      : []),
                  ],
                }],
              }} style={{ height: 250 }} />
            </div>
          )}
          {/* V2.12: Attribution */}
          {result.attribution?.cumulative && (
            <div className="border rounded mt-3" style={{ borderColor: 'var(--border)' }}>
              <button onClick={() => setShowAttribution(!showAttribution)} className="w-full text-left px-3 py-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
                {showAttribution ? '▼' : '▶'} 归因分析
              </button>
              {showAttribution && (
                <div className="px-3 pb-3 text-sm space-y-1">
                  {[
                    ['配置效应', result.attribution.cumulative.allocation],
                    ['选股效应', result.attribution.cumulative.selection],
                    ['交互效应', result.attribution.cumulative.interaction],
                    ['交易成本', -result.attribution.cost_drag],
                  ].map(([label, val]) => (
                    <div key={label as string} className="flex justify-between">
                      <span style={{ color: 'var(--text-secondary)' }}>{label as string}</span>
                      <span style={{ color: (val as number) >= 0 ? '#f85149' : '#3fb950' }}>
                        {((val as number) * 100).toFixed(2)}%
                      </span>
                    </div>
                  ))}
                  <div className="flex justify-between font-medium pt-1 mt-1" style={{ borderTop: '1px solid var(--border)' }}>
                    <span>累计超额</span>
                    <span>{(result.attribution.cumulative.total_excess * 100).toFixed(2)}%</span>
                  </div>
                </div>
              )}
            </div>
          )}
          {/* V2.12.1: Active Weights Table */}
          {result.active_weights && Object.keys(result.active_weights).length > 0 && (
            <div className="mt-3">
              <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>主动权重 (组合 vs 基准)</h4>
              <div className="overflow-x-auto max-h-48 overflow-y-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                  <thead><tr style={{ backgroundColor: 'var(--bg-primary)', position: 'sticky', top: 0 }}>
                    {['标的', '组合权重', '基准权重', '主动权重'].map(h => (
                      <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>{Object.entries(result.active_weights)
                    .sort((a, b) => Math.abs((b[1] as ActiveWeight).active) - Math.abs((a[1] as ActiveWeight).active))
                    .map(([sym, aw], i) => (
                    <tr key={sym} style={{ borderBottom: '1px solid var(--border)', backgroundColor: i % 2 === 0 ? 'rgba(255,255,255,0.04)' : 'transparent' }}>
                      <td className="px-3 py-1 font-mono">{sym}</td>
                      <td className="px-3 py-1">{((aw as ActiveWeight).portfolio * 100).toFixed(1)}%</td>
                      <td className="px-3 py-1">{((aw as ActiveWeight).benchmark * 100).toFixed(1)}%</td>
                      <td className="px-3 py-1" style={{ color: (aw as ActiveWeight).active >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                        {(aw as ActiveWeight).active >= 0 ? '+' : ''}{((aw as ActiveWeight).active * 100).toFixed(1)}%
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}
          {/* V2.12: Risk Events */}
          {result.risk_events && result.risk_events.length > 0 && (
            <div className="border rounded mt-3" style={{ borderColor: '#d29922' }}>
              <div className="px-3 py-1.5 text-sm" style={{ color: '#d29922' }}>
                风控事件 ({result.risk_events.length})
              </div>
              <div className="px-3 pb-3 text-xs max-h-40 overflow-y-auto" style={{ color: 'var(--text-secondary)' }}>
                {result.risk_events.map((e, i) => (
                  <div key={i} className="py-0.5">{e.date}  {e.event}</div>
                ))}
              </div>
            </div>
          )}
          {/* 持仓变动表 */}
          {weightsToShow && weightsToShow.length > 0 && (
            <div className="mt-3">
              <div className="flex items-center gap-2 mb-2">
                <h4 className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>持仓变动 ({fullWeights ? '完整' : `最近${weightsToShow.length}期`})</h4>
                {!fullWeights && result.run_id && (
                  <button onClick={handleLoadFullWeights} disabled={weightsLoading}
                    className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>
                    {weightsLoading ? '加载中...' : '加载完整历史'}
                  </button>
                )}
              </div>
              <div className="overflow-x-auto max-h-48 overflow-y-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                  <thead><tr style={{ backgroundColor: 'var(--bg-primary)', position: 'sticky', top: 0 }}>
                    <th className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>调仓日期</th>
                    <th className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>持仓</th>
                  </tr></thead>
                  <tbody>{weightsToShow.map((wh, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: i % 2 === 0 ? 'rgba(255,255,255,0.04)' : 'transparent' }}>
                      <td className="px-3 py-1">{wh.date}</td>
                      <td className="px-3 py-1">
                        {Object.entries(wh.weights).filter(([, w]) => w > 0.001).sort((a, b) => b[1] - a[1])
                          .map(([sym, w]) => `${sym}(${(w * 100).toFixed(1)}%)`).join(', ') || '全现金'}
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}
          {result.trades.length > 0 && (
            <div className="mt-4">
              {/* V2.12.2 codex round 7: drop the "100+" suffix. /run now
                  returns the full trade list (round 3 fix), so the count
                  is exact and the "+" indicator falsely suggested
                  truncation that no longer happens. */}
              <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>交易记录 ({result.trades.length})</h4>
              <div className="overflow-x-auto max-h-48 overflow-y-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                  <thead><tr style={{ backgroundColor: 'var(--bg-primary)', position: 'sticky', top: 0 }}>
                    {['日期', '标的', '方向', '股数', '价格', '成本'].map(h => (
                      <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>{result.trades.map((t, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: i % 2 === 0 ? 'rgba(255,255,255,0.04)' : 'transparent' }}>
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

      {/* 前推验证 Result */}
      {wfResult && (() => {
        const oosSharpe = wfResult.oos_metrics?.sharpe_ratio
        const oosReturn = wfResult.oos_metrics?.total_return
        const overfit = wfResult.overfitting_score
        const pVal = wfResult.significance?.p_value
        const isSig = wfResult.significance?.is_significant ?? false

        const nullRating = { color: 'var(--text-muted)', hint: '—' }
        const sharpeR = oosSharpe != null ? rateWfSharpe(oosSharpe) : nullRating
        const returnR = oosReturn != null ? rateWfReturn(oosReturn) : nullRating
        const overfitR = overfit != null ? rateWfOverfit(overfit) : nullRating
        const pvalR = pVal != null ? rateWfPval(isSig, pVal) : nullRating

        const metrics = [
          { label: '样本外夏普', value: oosSharpe != null ? oosSharpe.toFixed(4) : '-', ...sharpeR, tooltip: '>=1.0优秀, >=0.5可接受, <0偏弱' },
          { label: '样本外总收益', value: oosReturn != null ? (oosReturn * 100).toFixed(2) + '%' : '-', ...returnR, tooltip: '>=20%高, >=5%正, <0亏损' },
          { label: '过拟合评分', value: overfit != null ? overfit.toFixed(2) : '-', ...overfitR, tooltip: '<=0.2稳健, <=0.3轻微, >0.5严重' },
          ...(wfResult.significance ? [{ label: '显著性 p', value: pVal != null ? pVal.toFixed(3) : '-', ...pvalR, tooltip: 'p<0.05显著, p<0.01极显著' }] : []),
        ]

        return (
          <div className="p-4 rounded mt-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
            <h4 className="text-sm font-medium mb-2">前推验证结果 ({wfResult.n_splits} 折)</h4>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
              {metrics.map(m => (
                <div key={m.label} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }} title={m.tooltip}>
                  <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{m.label}</div>
                  <div className="text-sm font-medium" style={{ color: m.color }}>{m.value}</div>
                  <div className="text-xs mt-0.5" style={{ color: m.color, opacity: 0.8 }}>{m.hint}</div>
                </div>
              ))}
            </div>
            <div className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
              样本内夏普: [{wfResult.is_sharpes?.map((s: number) => s.toFixed(2)).join(', ')}] |
              样本外夏普: [{wfResult.oos_sharpes?.map((s: number) => s.toFixed(2)).join(', ')}]
            </div>
            <div className="text-xs mb-2 px-1" style={{ color: 'var(--text-muted)', fontSize: '10px' }}>
              前推验证将数据分为训练/测试段,检验策略在未见数据上的表现。过拟合评分 = (样本内 - 样本外) / |样本内|,越低越好。
            </div>
            <button onClick={() => setWfResult(null)} className="text-xs px-2 py-1 rounded"
              style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>关闭</button>
          </div>
        )
      })()}
    </>
  )
}
