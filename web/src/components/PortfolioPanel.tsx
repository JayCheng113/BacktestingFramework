import { useState, useEffect, useMemo, useRef } from 'react'
import { listPortfolioStrategies, runPortfolioBacktest, listPortfolioRuns, deletePortfolioRun, getPortfolioRun, evaluateFactors, factorCorrelation, portfolioWalkForward, fetchFundamentalData, fundamentalDataQuality, portfolioSearch } from '../api'
import { DEFAULT_SETTINGS, getDefaultSettings } from './BacktestSettings'
import type { PortfolioRunResult, HistoryRun, ParamSchema } from '../types'
import type { BacktestSettingsValue } from './BacktestSettings'
import type { EnsembleConfig } from './EnsembleBuilder'
import PortfolioRunContent from './PortfolioRunContent'
import PortfolioFactorContent from './PortfolioFactorContent'
import PortfolioHistoryContent from './PortfolioHistoryContent'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

import { CATEGORY_LABELS, FACTOR_LABELS } from './shared/portfolioLabels'

export default function PortfolioPanel() {
  const [strategies, setStrategies] = useState<{ name: string; description: string; parameters: Record<string, ParamSchema> }[]>([])
  const [factors, setFactors] = useState<string[]>([])
  const [factorCategories, setFactorCategories] = useState<{ key: string; label: string; factors: any[] }[]>([])
  const [selected, setSelected] = useState('')
  const [symbols, setSymbols] = useState('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH')
  // V2.12.2 codex: previously PortfolioPanel had no market state at all and
  // every API call defaulted to backend's cn_stock. This silently applied
  // A-share T+1 / stamp tax / limit-pct rules to US/HK backtests.
  const [market, setMarket] = useState('cn_stock')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [freq, setFreq] = useState('monthly')
  const [strategyParams, setStrategyParams] = useState<Record<string, any>>({})
  const [settings, setSettings] = useState<BacktestSettingsValue>(DEFAULT_SETTINGS)
  const [loading, setLoading] = useState(false)
  const [wfLoading, setWfLoading] = useState(false)
  const [wfResult, setWfResult] = useState<any>(null)
  const [wfSplits, setWfSplits] = useState(5)
  const [wfTrainRatio, setWfTrainRatio] = useState(0.7)
  const [result, setResult] = useState<PortfolioRunResult | null>(null)
  const [history, setHistory] = useState<HistoryRun[]>([])
  const [tab, setTab] = useState<'run' | 'factor-research' | 'history'>('run')
  // Factor research state
  const [evalFactors, setEvalFactors] = useState<string[]>(['momentum_rank_20'])
  const [neutralize, setNeutralize] = useState(false)
  const [evalResult, setEvalResult] = useState<any>(null)
  const [corrResult, setCorrResult] = useState<any>(null)
  const [evalLoading, setEvalLoading] = useState(false)
  const [fetchingFunda, setFetchingFunda] = useState(false)
  const [fundaStatus, setFundaStatus] = useState<string>('')
  const [qualityReport, setQualityReport] = useState<any[]>([])
  // Clear stale quality report when inputs change
  useEffect(() => { setQualityReport([]); setFundaStatus('') }, [symbols, startDate, endDate])

  const [selectedRuns, setSelectedRuns] = useState<Set<string>>(new Set())
  const [compareData, setCompareData] = useState<{ id: string; name: string; equity: number[]; dates: string[]; metrics: any }[]>([])
  const [comparing, setComparing] = useState(false)
  // V2.12: Optimizer + Risk Control
  const [optimizer, setOptimizer] = useState('none')
  const [riskAversion, setRiskAversion] = useState(1.0)
  const [maxWeight, setMaxWeight] = useState(10)
  const [maxIndustryWeight, setMaxIndustryWeight] = useState(30)
  const [covLookback, setCovLookback] = useState(60)
  const [riskControl, setRiskControl] = useState(false)
  const [maxDrawdown, setMaxDrawdown] = useState(20)
  const [drawdownReduce, setDrawdownReduce] = useState(50)
  const [drawdownRecovery, setDrawdownRecovery] = useState(10)
  const [maxTurnover, setMaxTurnover] = useState(50)
  const [showOptimizer, setShowOptimizer] = useState(false)
  const [showRiskControl, setShowRiskControl] = useState(false)
  const [showAttribution, setShowAttribution] = useState(false)
  // V2.12.1: Index benchmark
  const [indexBenchmark, setIndexBenchmark] = useState('')
  const [trackingError, setTrackingError] = useState(5)
  // V2.14 B3: Ensemble config ref (updated by EnsembleBuilder via callback)
  const ensembleConfigRef = useRef<EnsembleConfig | null>(null)

  // Parameter search state -- dynamic from schema
  const [searchMode, setSearchMode] = useState(false)
  const [comboSearch, setComboSearch] = useState(false)
  const [searchGrid, setSearchGrid] = useState<Record<string, string>>({})
  const [expandedParams, setExpandedParams] = useState<Record<string, boolean>>({})
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchResults, setSearchResults] = useState<any[]>([])
  // V2.12.2 codex round 8: monotonic tokens for stale-response
  // invalidation. Each handler (run/wf/search) bumps its own token
  // before awaiting and checks on resume whether the token is still
  // the latest. Input-change useEffects also bump to invalidate
  // in-flight requests when inputs change mid-request.
  const runTokenRef = useRef(0)
  const wfTokenRef = useRef(0)
  const searchTokenRef = useRef(0)
  // V2.13.2 G3.7: race token coverage for uncovered handlers
  const evalTokenRef = useRef(0)
  const fundaTokenRef = useRef(0)
  const compareTokenRef = useRef(0)
  // V2.12.2 codex: track sampled/completed/failed counts + failed combos
  // so the UI can show "N of M combos failed" banner instead of silently
  // dropping failures from the results list.
  const [searchMeta, setSearchMeta] = useState<{
    sampled: number; completed: number; failed: number;
    total_combinations: number;
    failed_combos: Array<{ combo_index: number; params: any; error: string }>;
  } | null>(null)

  // Current strategy's parameter schema
  const currentSchema = useMemo(() => {
    const s = strategies.find(s => s.name === selected)
    return s?.parameters || {}
  }, [strategies, selected])

  // Initialize params from schema defaults when strategy changes.
  // V2.12.1 codex follow-up: also clear searchGrid and searchResults so the
  // previous strategy's search state (parameter ranges and ranked results)
  // doesn't leak onto the new strategy — especially dangerous when two
  // strategies share a parameter name like 'top_n' which would silently reuse
  // the old range but apply it to a different strategy's behavior.
  useEffect(() => {
    const defaults: Record<string, any> = {}
    for (const [key, schema] of Object.entries(currentSchema)) {
      defaults[key] = schema.default
    }
    setStrategyParams(defaults)
    setSearchGrid({})
    setSearchResults([])
    setSearchMeta(null)
    setExpandedParams({})
  }, [currentSchema])

  // V2.12.2 codex: clear stale result/wfResult when any run input changes.
  // Prior version only the market-change useEffect did this, so symbol /
  // date / strategy / freq changes left the previous run's curve and
  // metrics visible. Users could believe they had already run the current
  // configuration when they hadn't. This mirrors BacktestPanel's pattern.
  //
  // Round 5 codex: also track strategyParams, settings, optimizer/risk/
  // index config — ALL of these are sent to the backend and affect the
  // run's output. Prior version left results stale when users changed
  // optimizer or cost model. Using JSON stringify for deep equality on
  // dict-shaped state (acceptable overhead for ~10 KB).
  useEffect(() => {
    // V2.12.2 codex round 8: bump all three request tokens to invalidate
    // any in-flight run / WF / search that would otherwise re-populate
    // the state we just cleared when its response finally arrives.
    runTokenRef.current += 1
    wfTokenRef.current += 1
    searchTokenRef.current += 1
    setResult(null)
    setWfResult(null)
    setSearchResults([])
    setSearchMeta(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    symbols, startDate, endDate, freq, selected,
    JSON.stringify(strategyParams),
    JSON.stringify(settings),
    optimizer, riskAversion, maxWeight, maxIndustryWeight, covLookback,
    riskControl, maxDrawdown, drawdownReduce, drawdownRecovery, maxTurnover,
    indexBenchmark, trackingError,
  ])

  // V2.12.2 codex: market change must fully reset market-sensitive state.
  // Prior version only set `market` and let all downstream state (settings
  // cost model, index benchmark, tracking error, eval/corr/search/quality
  // results) leak from the previous market. Backend silently accepted the
  // stale A-share cost model and index benchmark when user ran US/HK runs.
  useEffect(() => {
    // V2.12.2 codex round 8: bump tokens so any in-flight request under
    // the previous market is invalidated (would otherwise mix A-share
    // results into US/HK page).
    runTokenRef.current += 1
    wfTokenRef.current += 1
    searchTokenRef.current += 1
    // Re-apply market-appropriate cost model + rules
    setSettings(getDefaultSettings(market))
    // Non-cn_stock has no A-share index benchmark — reset to "none"
    // AND reset tracking error to default, since TE is A-share specific
    // (CSI300/500/1000 index enhancement).
    if (market !== 'cn_stock') {
      setIndexBenchmark('')
      setTrackingError(5)
    }
    // Clear stale evaluation / search / quality results from previous market
    setEvalResult(null)
    setCorrResult(null)
    setSearchResults([])
    setQualityReport([])
    setFundaStatus('')
    // Clear backtest result + WF result + full-weights snapshot — they
    // were computed under the previous market's rules.
    setResult(null)
    setWfResult(null)
    // Clear cross-run compare data — comparing runs from different
    // markets would yield meaningless overlays.
    setCompareData([])
    setSelectedRuns(new Set())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market])

  useEffect(() => {
    listPortfolioStrategies().then(r => {
      const data = r.data
      setStrategies(data.strategies || [])
      setFactors(data.available_factors || [])
      setFactorCategories(data.factor_categories || [])
      if (data.strategies?.length > 0 && !selected) setSelected(data.strategies[0].name)
    }).catch(() => {})
    loadHistory()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const loadHistory = () => {
    listPortfolioRuns(20).then(r => setHistory(r.data || [])).catch(() => {})
  }

  const toggleRunSelection = (runId: string) => {
    setSelectedRuns(prev => {
      const next = new Set(prev)
      if (next.has(runId)) next.delete(runId)
      else next.add(runId)
      return next
    })
  }

  const handleCompare = async () => {
    const ids = Array.from(selectedRuns)
    if (ids.length < 2) { alert('请至少选择 2 条记录'); return }
    if (ids.length > 10) { alert('最多对比 10 条记录'); return }
    const myToken = ++compareTokenRef.current
    setComparing(true)
    setCompareData([])
    const results: typeof compareData = []
    let errors: string[] = []
    for (const id of ids) {
      try {
        const res = await getPortfolioRun(id)
        const d = res.data
        const ec = d.equity_curve || d.equity || []
        // V2.12.2 codex: propagate per-bar dates from the run so the
        // compare chart can align runs by real trading days. Prior version
        // left `dates: []` which forced PortfolioHistoryContent into an
        // index-based x-axis that visually aligns runs with different
        // date ranges or trading-day gaps, producing misleading overlays.
        // Pre-V2.12.2 runs have no stored dates → empty list, chart falls
        // back to index-based rendering for those legacy rows only.
        const dates = Array.isArray(d.dates) ? d.dates : []
        results.push({
          id, name: `${d.strategy_name || '未知'} (${(d.start_date || '').slice(0, 10)}~${(d.end_date || '').slice(0, 10)})`,
          equity: ec.map((v: number) => v / (ec[0] || 1)),
          dates,
          metrics: d.metrics || {},
        })
      } catch (e: any) {
        errors.push(`${id}: ${e?.response?.data?.detail || e?.message || '失败'}`)
      }
    }
    if (compareTokenRef.current !== myToken) return  // superseded
    if (results.length >= 2) {
      setCompareData(results)
    } else if (errors.length > 0) {
      alert('对比加载失败:\n' + errors.join('\n'))
      loadHistory()
    } else {
      alert('对比数据不足（需至少 2 条有效记录）')
    }
    setComparing(false)
  }

  const handleEvaluateFactors = async () => {
    const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
    if (symbolList.length < 5) { alert('因子评估需要至少 5 个标的'); return }
    if (evalFactors.length === 0) { alert('请选择至少 1 个因子'); return }
    const myToken = ++evalTokenRef.current
    setEvalLoading(true); setEvalResult(null); setCorrResult(null)
    try {
      const [evalRes, corrRes] = await Promise.all([
        evaluateFactors({ symbols: symbolList, market, start_date: startDate, end_date: endDate, factor_names: evalFactors, forward_days: 5, eval_freq: 'weekly', neutralize }),
        evalFactors.length >= 2
          ? factorCorrelation({ symbols: symbolList, market, start_date: startDate, end_date: endDate, factor_names: evalFactors })
          : Promise.resolve(null),
      ])
      if (evalTokenRef.current !== myToken) return  // superseded
      setEvalResult(evalRes.data)
      if (corrRes) setCorrResult(corrRes.data)
    } catch (e: any) { if (evalTokenRef.current === myToken) alert(e?.response?.data?.detail || e?.message || '评估失败') }
    finally { if (evalTokenRef.current === myToken) setEvalLoading(false) }
  }

  const handleFetchFundamental = async () => {
    const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
    if (symbolList.length === 0) return
    const myToken = ++fundaTokenRef.current
    setFetchingFunda(true); setFundaStatus('获取中...')
    try {
      const finaFactorKeys = new Set<string>()
      factorCategories.forEach(cat => {
        if (Array.isArray(cat.factors)) cat.factors.forEach((f: any) => {
          if (typeof f === 'object' && f.needs_fina) finaFactorKeys.add(f.key || f.class_name || '')
        })
      })
      const hasFina = evalFactors.some(f => finaFactorKeys.has(f))
      const res = await fetchFundamentalData({ symbols: symbolList, market, start_date: startDate, end_date: endDate, include_fina: hasFina })
      if (fundaTokenRef.current !== myToken) return
      setFundaStatus(res.data.message || '完成')
      const qr = await fundamentalDataQuality({ symbols: symbolList, market, start_date: startDate, end_date: endDate })
      if (fundaTokenRef.current !== myToken) return
      setQualityReport(qr.data.report || [])
    } catch (e: any) { if (fundaTokenRef.current === myToken) setFundaStatus(e.response?.data?.detail || '获取失败') }
    finally { if (fundaTokenRef.current === myToken) setFetchingFunda(false) }
  }

  const downloadCSV = (filename: string, content: string) => {
    const blob = new Blob(['\uFEFF' + content], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = filename; a.click()
    URL.revokeObjectURL(url)
  }

  const exportEquityCurve = () => {
    if (!result) return
    const rows = ['日期,组合净值,基准净值']
    result.dates.forEach((d, i) => {
      rows.push(`${d},${result.equity_curve[i]?.toFixed(2) ?? ''},${result.benchmark_curve[i]?.toFixed(2) ?? ''}`)
    })
    downloadCSV('equity_curve.csv', rows.join('\n'))
  }

  const exportTrades = () => {
    if (!result) return
    const rows = ['日期,标的,方向,股数,价格,成本']
    result.trades.forEach(t => {
      rows.push(`${t.date},${t.symbol},${t.side},${t.shares},${Number(t.price).toFixed(2)},${Number(t.cost).toFixed(2)}`)
    })
    downloadCSV('trades.csv', rows.join('\n'))
  }

  const handleDeleteRun = async (runId: string) => {
    if (!confirm('确认删除此回测记录?')) return
    try {
      await deletePortfolioRun(runId)
      loadHistory()
    } catch (e: any) {
      alert('删除失败: ' + (e?.response?.data?.detail || e?.message || '未知错误'))
    }
  }

  const updateParam = (key: string, value: any) => {
    setStrategyParams(prev => ({ ...prev, [key]: value }))
  }

  const handleRun = async () => {
    // V2.12.2 codex round 8: capture per-request token. Check on resume
    // to reject stale responses after input change or new run.
    const myToken = ++runTokenRef.current
    setLoading(true); setResult(null); setWfResult(null)
    try {
      const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
      const cleanParams = selected === 'StrategyEnsemble' && ensembleConfigRef.current
        ? ensembleConfigRef.current
        : Object.fromEntries(Object.entries(strategyParams).filter(([k]) => !k.startsWith('_')))
      const res = await runPortfolioBacktest({
        strategy_name: selected, symbols: symbolList,
        market,
        start_date: startDate, end_date: endDate, freq,
        strategy_params: cleanParams,
        initial_cash: settings.initial_cash,
        buy_commission_rate: settings.buy_commission_rate,
        sell_commission_rate: settings.sell_commission_rate,
        min_commission: settings.min_commission,
        stamp_tax_rate: settings.stamp_tax_rate,
        slippage_rate: settings.slippage_rate,
        lot_size: settings.lot_size,
        limit_pct: settings.limit_pct,
        benchmark_symbol: settings.benchmark,
        optimizer,
        risk_aversion: riskAversion,
        max_weight: maxWeight / 100,
        max_industry_weight: maxIndustryWeight / 100,
        cov_lookback: covLookback,
        risk_control: riskControl,
        max_drawdown: maxDrawdown / 100,
        drawdown_reduce: drawdownReduce / 100,
        drawdown_recovery: drawdownRecovery / 100,
        max_turnover: maxTurnover / 100,
        index_benchmark: indexBenchmark,
        max_tracking_error: trackingError / 100,
      })
      if (runTokenRef.current !== myToken) return  // superseded
      setResult(res.data)
      loadHistory()
    } catch (e: any) {
      if (runTokenRef.current === myToken) {
        alert(e?.response?.data?.detail || JSON.stringify(e?.response?.data) || 'Failed')
      }
    } finally {
      if (runTokenRef.current === myToken) setLoading(false)
    }
  }

  const handleWalkForward = async () => {
    // V2.12.2 codex round 8: stale-response token.
    const myToken = ++wfTokenRef.current
    setWfLoading(true); setWfResult(null)
    try {
      const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
      // V2.12.2 codex: propagate optimizer / risk_control / index_benchmark /
      // tracking_error / market to walk-forward. Prior version only /run
      // passed these, so WF results were computed under a different
      // execution environment than the main backtest — silently equal-weight
      // instead of user's optimizer, A-share cost model instead of selected,
      // etc. Backend already supports all these via PortfolioCommonConfig.
      const res = await portfolioWalkForward({
        strategy_name: selected, symbols: symbolList,
        market,
        start_date: startDate, end_date: endDate, freq,
        strategy_params: selected === 'StrategyEnsemble' && ensembleConfigRef.current
          ? ensembleConfigRef.current
          : Object.fromEntries(Object.entries(strategyParams).filter(([k]) => !k.startsWith('_'))),
        initial_cash: settings.initial_cash,
        n_splits: wfSplits, train_ratio: wfTrainRatio,
        benchmark_symbol: settings.benchmark,
        buy_commission_rate: settings.buy_commission_rate,
        sell_commission_rate: settings.sell_commission_rate,
        min_commission: settings.min_commission,
        stamp_tax_rate: settings.stamp_tax_rate,
        slippage_rate: settings.slippage_rate,
        lot_size: settings.lot_size, limit_pct: settings.limit_pct,
        optimizer,
        risk_aversion: riskAversion,
        max_weight: maxWeight / 100,
        max_industry_weight: maxIndustryWeight / 100,
        cov_lookback: covLookback,
        risk_control: riskControl,
        max_drawdown: maxDrawdown / 100,
        drawdown_reduce: drawdownReduce / 100,
        drawdown_recovery: drawdownRecovery / 100,
        max_turnover: maxTurnover / 100,
        index_benchmark: indexBenchmark,
        max_tracking_error: trackingError / 100,
      })
      if (wfTokenRef.current !== myToken) return  // superseded
      setWfResult(res.data)
      if (res.data.warnings?.length) alert('前推验证提示: ' + res.data.warnings.join('\n'))
    } catch (e: any) {
      if (wfTokenRef.current === myToken) {
        alert('前推验证 失败: ' + (e?.response?.data?.detail || e?.message || ''))
      }
    } finally {
      if (wfTokenRef.current === myToken) setWfLoading(false)
    }
  }

  const handleSearch = async () => {
    const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
    if (symbolList.length === 0) { alert('请填写股票池'); return }

    const paramGrid: Record<string, any[]> = {}
    let totalCombos = 1
    for (const [key, schema] of Object.entries(currentSchema)) {
      const raw = searchGrid[key] || ''
      if (!raw) continue
      if (schema.type === 'select') {
        const vals = raw.split(',').filter(Boolean)
        if (vals.length > 0) { paramGrid[key] = vals; totalCombos *= vals.length }
      } else if (schema.type === 'multi_select') {
        if (comboSearch) {
          // V2.14: Power-set mode — auto-generate all non-empty subsets
          const items = raw.split(',').map(x => x.trim()).filter(Boolean)
          if (items.length > 0) {
            const subsets: string[][] = []
            for (let mask = 1; mask < (1 << items.length); mask++) {
              const subset: string[] = []
              for (let j = 0; j < items.length; j++) {
                if (mask & (1 << j)) subset.push(items[j])
              }
              subsets.push(subset)
            }
            if (subsets.length > 64) {
              alert(`因子组合数 ${subsets.length} 超过 64 上限，请减少选中因子数`)
              return
            }
            paramGrid[key] = subsets
            totalCombos *= subsets.length
          }
        } else {
          // V2.12.2 codex: `|` separates subsets, `,` separates items within a
          // subset. User input "ep,bp,sp" → ONE combo with the 3-factor list.
          // User input "ep,bp|ep,sp" → 2 subsets.
          const subsets = raw.split('|')
            .map(s => s.split(',').map(x => x.trim()).filter(Boolean))
            .filter(a => a.length > 0)
          if (subsets.length > 0) {
            paramGrid[key] = subsets
            totalCombos *= subsets.length
          }
        }
      } else if (schema.type === 'int') {
        const vals = raw.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n))
        if (vals.length > 0) { paramGrid[key] = vals; totalCombos *= vals.length }
      } else if (schema.type === 'float') {
        const vals = raw.split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n))
        if (vals.length > 0) { paramGrid[key] = vals; totalCombos *= vals.length }
      }
    }
    if (Object.keys(paramGrid).length === 0) { alert('请至少为一个参数设置多个候选值'); return }

    // V2.12.2 codex round 8: stale-response token for search.
    const myToken = ++searchTokenRef.current
    setSearchLoading(true); setSearchResults([])
    try {
      // V2.12.2 codex: propagate optimizer/risk/index to search so each
      // candidate runs under the same execution environment as /run. Prior
      // version silently ran all candidates with default (none) optimizer
      // regardless of user's UI selection.
      const res = await portfolioSearch({
        strategy_name: selected, symbols: symbolList,
        market,
        start_date: startDate, end_date: endDate, freq,
        param_grid: paramGrid, max_combinations: 50,
        buy_commission_rate: settings.buy_commission_rate, sell_commission_rate: settings.sell_commission_rate,
        min_commission: settings.min_commission, stamp_tax_rate: settings.stamp_tax_rate,
        slippage_rate: settings.slippage_rate, lot_size: settings.lot_size, limit_pct: settings.limit_pct,
        initial_cash: settings.initial_cash, benchmark_symbol: settings.benchmark,
        optimizer,
        risk_aversion: riskAversion,
        max_weight: maxWeight / 100,
        max_industry_weight: maxIndustryWeight / 100,
        cov_lookback: covLookback,
        risk_control: riskControl,
        max_drawdown: maxDrawdown / 100,
        drawdown_reduce: drawdownReduce / 100,
        drawdown_recovery: drawdownRecovery / 100,
        max_turnover: maxTurnover / 100,
        index_benchmark: indexBenchmark,
        max_tracking_error: trackingError / 100,
      })
      if (searchTokenRef.current !== myToken) return  // superseded
      setSearchResults(res.data.results || [])
      setSearchMeta({
        sampled: res.data.sampled || 0,
        completed: res.data.completed || 0,
        failed: res.data.failed || 0,
        total_combinations: res.data.total_combinations || 0,
        failed_combos: res.data.failed_combos || [],
      })
      const tc = res.data.total_combinations || 0
      let searchMsg = res.data.warnings?.length ? res.data.warnings.join('\n') + '\n' : ''
      if (tc > 50) searchMsg += `共 ${tc} 种组合，随机采样 50 个展示（可能不完整）`
      if (searchMsg) alert(searchMsg.trim())
    } catch (e: any) {
      if (searchTokenRef.current === myToken) alert(e?.response?.data?.detail || '搜索失败')
    } finally {
      if (searchTokenRef.current === myToken) setSearchLoading(false)
    }
  }

  // Render a single param input based on schema type
  const renderParamInput = (key: string, schema: ParamSchema) => {
    const label = schema.label || key
    const value = strategyParams[key] ?? schema.default

    if (schema.type === 'select') {
      const options: string[] = schema.options ?? (factors.length > 0 ? factors : [String(schema.default)])
      const useCategories = !schema.options && factorCategories.length > 0
      return (
        <div key={key} className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
          <select value={value} onChange={e => updateParam(key, e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
            {useCategories ? (<>
              {factorCategories.map(cat => {
                const catLabel = CATEGORY_LABELS
                return (
                <optgroup key={cat.key} label={catLabel[cat.key] || cat.label}>
                  {(Array.isArray(cat.factors) ? cat.factors : []).map((f: any) => {
                    const fKey = typeof f === 'string' ? f : (f.key || f.class_name || '')
                    const needsFina = typeof f === 'object' && f.needs_fina
                    return <option key={fKey} value={fKey}>{FACTOR_LABELS[fKey] || fKey}{needsFina ? ' *' : ''}</option>
                  })}
                </optgroup>
              )})}
              <optgroup label="合成">
                <option value="alpha_combiner">{FACTOR_LABELS['alpha_combiner'] || '多因子合成'}</option>
              </optgroup>
            </>) : options.map(o => <option key={o} value={o}>{FACTOR_LABELS[o] || o}</option>)}
          </select>
        </div>
      )
    }

    if (schema.type === 'multi_select') {
      const options: string[] = schema.options ?? (factors.length > 0 ? factors.filter(f => f !== 'alpha_combiner') : [])
      const selected_vals: string[] = Array.isArray(value) ? value : [String(value)]
      const useCategories = !schema.options && factorCategories.length > 0
      const expanded = expandedParams[key] ?? false
      const setExpanded = (v: boolean) => setExpandedParams(prev => ({ ...prev, [key]: v }))
      return (
        <div key={key} className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
            <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: 'var(--bg-primary)', color: 'var(--color-accent)' }}>
              已选 {selected_vals.filter(v => v && options.includes(v)).length} 个
            </span>
            <button onClick={() => setExpanded(!expanded)} className="text-xs" style={{ color: 'var(--color-accent)' }}>
              {expanded ? '收起' : '展开选择'}
            </button>
          </div>
          {selected_vals.length > 0 && !expanded && (
            <div className="flex flex-wrap gap-1">
              {selected_vals.filter(v => v && options.includes(v)).map(v => (
                <span key={v} className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: 'var(--color-accent)', color: '#fff' }}>
                  {FACTOR_LABELS[v] || v}
                  <button onClick={() => updateParam(key, selected_vals.filter(x => x !== v))} className="ml-1 opacity-70 hover:opacity-100">x</button>
                </span>
              ))}
            </div>
          )}
          {expanded && (
            <div className="p-2 rounded mt-1" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', maxHeight: 200, overflowY: 'auto' }}>
              {useCategories ? factorCategories.map(cat => {
                const catFactors = (Array.isArray(cat.factors) ? cat.factors : [])
                  .map((f: any) => typeof f === 'string' ? f : (f.key || f.class_name || ''))
                  .filter((f: string) => f && f !== 'alpha_combiner')
                if (catFactors.length === 0) return null
                return (
                  <div key={cat.key} className="mb-1">
                    <span className="text-xs mr-1 font-medium" style={{ color: 'var(--text-muted)' }}>{CATEGORY_LABELS[cat.key] || cat.label}:</span>
                    <span className="inline-flex flex-wrap gap-1">
                      {catFactors.map((fKey: string) => (
                        <button key={fKey} onClick={() => {
                          const cur = Array.isArray(strategyParams[key]) ? [...strategyParams[key]] : []
                          if (cur.includes(fKey)) updateParam(key, cur.filter(x => x !== fKey))
                          else updateParam(key, [...cur, fKey])
                        }} className="text-xs px-1.5 py-0.5 rounded"
                          style={{ backgroundColor: selected_vals.includes(fKey) ? 'var(--color-accent)' : 'var(--bg-secondary)',
                                   color: selected_vals.includes(fKey) ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                          {FACTOR_LABELS[fKey] || fKey}
                        </button>
                      ))}
                    </span>
                  </div>
                )
              }) : (
                <div className="flex flex-wrap gap-1">
                  {options.map(o => (
                    <button key={o} onClick={() => {
                      const cur = Array.isArray(strategyParams[key]) ? [...strategyParams[key]] : []
                      if (cur.includes(o)) updateParam(key, cur.filter(x => x !== o))
                      else updateParam(key, [...cur, o])
                    }} className="text-xs px-1.5 py-0.5 rounded"
                      style={{ backgroundColor: selected_vals.includes(o) ? 'var(--color-accent)' : 'var(--bg-secondary)',
                               color: selected_vals.includes(o) ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                      {FACTOR_LABELS[o] || o}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
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

  const currentDesc = strategies.find(s => s.name === selected)?.description || ''

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex gap-2 mb-4">
        <button onClick={() => setTab('run')} className={`px-4 py-1.5 rounded text-sm ${tab === 'run' ? 'bg-blue-600 text-white' : ''}`} style={tab !== 'run' ? inputStyle : {}}>组合回测</button>
        <button onClick={() => setTab('factor-research')} className={`px-4 py-1.5 rounded text-sm ${tab === 'factor-research' ? 'bg-blue-600 text-white' : ''}`} style={tab !== 'factor-research' ? inputStyle : {}}>选股因子研究</button>
        <button onClick={() => setTab('history')} className={`px-4 py-1.5 rounded text-sm ${tab === 'history' ? 'bg-blue-600 text-white' : ''}`} style={tab !== 'history' ? inputStyle : {}}>历史记录 ({history.length})</button>
      </div>

      {tab === 'run' && (
        <PortfolioRunContent
          symbols={symbols} setSymbols={setSymbols}
          market={market} setMarket={setMarket}
          startDate={startDate} setStartDate={setStartDate}
          endDate={endDate} setEndDate={setEndDate}
          freq={freq} setFreq={setFreq}
          settings={settings} setSettings={setSettings}
          strategies={strategies} factors={factors} factorCategories={factorCategories}
          selected={selected} setSelected={setSelected}
          strategyParams={strategyParams} updateParam={updateParam}
          currentSchema={currentSchema} currentDesc={currentDesc}
          result={result} loading={loading}
          wfResult={wfResult} setWfResult={setWfResult}
          wfLoading={wfLoading} wfSplits={wfSplits} setWfSplits={setWfSplits}
          wfTrainRatio={wfTrainRatio} setWfTrainRatio={setWfTrainRatio}
          optimizer={optimizer} setOptimizer={setOptimizer}
          riskAversion={riskAversion} setRiskAversion={setRiskAversion}
          maxWeight={maxWeight} setMaxWeight={setMaxWeight}
          maxIndustryWeight={maxIndustryWeight} setMaxIndustryWeight={setMaxIndustryWeight}
          covLookback={covLookback} setCovLookback={setCovLookback}
          indexBenchmark={indexBenchmark} setIndexBenchmark={setIndexBenchmark}
          trackingError={trackingError} setTrackingError={setTrackingError}
          riskControl={riskControl} setRiskControl={setRiskControl}
          maxDrawdown={maxDrawdown} setMaxDrawdown={setMaxDrawdown}
          drawdownReduce={drawdownReduce} setDrawdownReduce={setDrawdownReduce}
          drawdownRecovery={drawdownRecovery} setDrawdownRecovery={setDrawdownRecovery}
          maxTurnover={maxTurnover} setMaxTurnover={setMaxTurnover}
          showOptimizer={showOptimizer} setShowOptimizer={setShowOptimizer}
          showRiskControl={showRiskControl} setShowRiskControl={setShowRiskControl}
          showAttribution={showAttribution} setShowAttribution={setShowAttribution}
          searchMode={searchMode} setSearchMode={setSearchMode}
          comboSearch={comboSearch} setComboSearch={setComboSearch}
          ensembleConfigRef={ensembleConfigRef}
          searchGrid={searchGrid} setSearchGrid={setSearchGrid}
          expandedParams={expandedParams} setExpandedParams={setExpandedParams}
          searchLoading={searchLoading} searchResults={searchResults}
          searchMeta={searchMeta}
          handleRun={handleRun} handleWalkForward={handleWalkForward}
          handleSearch={handleSearch}
          exportEquityCurve={exportEquityCurve} exportTrades={exportTrades}
          renderParamInput={renderParamInput}
        />
      )}

      {tab === 'factor-research' && (
        <PortfolioFactorContent
          symbols={symbols} setSymbols={setSymbols}
          market={market} setMarket={setMarket}
          startDate={startDate} setStartDate={setStartDate}
          endDate={endDate} setEndDate={setEndDate}
          factors={factors} factorCategories={factorCategories}
          evalFactors={evalFactors} setEvalFactors={setEvalFactors}
          neutralize={neutralize} setNeutralize={setNeutralize}
          evalResult={evalResult} corrResult={corrResult}
          evalLoading={evalLoading}
          fetchingFunda={fetchingFunda} fundaStatus={fundaStatus}
          qualityReport={qualityReport}
          handleEvaluateFactors={handleEvaluateFactors}
          handleFetchFundamental={handleFetchFundamental}
        />
      )}

      {tab === 'history' && (
        <PortfolioHistoryContent
          history={history}
          selectedRuns={selectedRuns}
          toggleRunSelection={toggleRunSelection}
          compareData={compareData} setCompareData={setCompareData}
          comparing={comparing}
          handleCompare={handleCompare}
          handleDeleteRun={handleDeleteRun}
        />
      )}
    </div>
  )
}
