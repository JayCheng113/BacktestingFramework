import { useState, useEffect, useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { listPortfolioStrategies, runPortfolioBacktest, listPortfolioRuns, deletePortfolioRun, getPortfolioRun, evaluateFactors, factorCorrelation, portfolioWalkForward, fetchFundamentalData, fundamentalDataQuality, portfolioSearch } from '../api'
import BacktestSettings, { DEFAULT_SETTINGS } from './BacktestSettings'
import DateRangePicker from './DateRangePicker'
import type { BacktestSettingsValue } from './BacktestSettings'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

// Category display names
const CATEGORY_LABELS: Record<string, string> = {
  technical: '量价', value: '估值', quality: '质量', growth: '成长',
  size: '规模', liquidity: '流动性', leverage: '杠杆', industry: '行业', other: '其他',
}

// Factor display names: raw key → Chinese label
const FACTOR_LABELS: Record<string, string> = {
  // 量价
  momentum_rank_20: '20日动量', momentum_rank_10: '10日动量', momentum_rank_60: '60日动量',
  volume_rank_20: '成交量排名', reverse_vol_rank_20: '低波动',
  // 估值
  ep: '盈利收益率(EP)', bp: '市净率倒数(BP)', sp: '市销率倒数(SP)', dp: '股息率',
  // 质量
  roe: 'ROE', roa: 'ROA', gross_margin: '毛利率', net_profit_margin: '净利率',
  // 成长
  revenue_growth_yoy: '营收增速', profit_growth_yoy: '利润增速', roe_change: 'ROE变化',
  // 规模
  ln_market_cap: '总市值(小盘优先)', ln_circ_mv: '流通市值(小盘优先)',
  // 流动性
  turnover_rate: '换手率', amihud_illiquidity: '流动性',
  // 杠杆
  debt_to_assets: '低负债率', current_ratio: '流动比率',
  // 行业
  industry_momentum: '行业动量',
  // 合成
  alpha_combiner: '多因子合成',
}

interface PortfolioMetrics {
  total_return?: number; annualized_return?: number; sharpe_ratio?: number
  sortino_ratio?: number; max_drawdown?: number; max_drawdown_duration?: number
  benchmark_return?: number; alpha?: number; beta?: number
  trade_count?: number; turnover_per_rebalance?: number
  annualized_volatility?: number; n_rebalances?: number
  concentration_hhi?: number
}

interface PortfolioRunResult {
  run_id: string; metrics: PortfolioMetrics; equity_curve: number[]
  benchmark_curve: number[]; dates: string[]; trades: any[]; rebalance_dates: string[]
  symbols_fetched?: number; symbols_skipped?: string[]
  weights_history?: { date: string; weights: Record<string, number> }[]
  latest_weights?: Record<string, number>
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
  const [factorCategories, setFactorCategories] = useState<{ key: string; label: string; factors: any[] }[]>([])
  const [selected, setSelected] = useState('')
  const [symbols, setSymbols] = useState('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH')
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
  // Parameter search state — dynamic from schema
  const [searchMode, setSearchMode] = useState(false)
  const [searchGrid, setSearchGrid] = useState<Record<string, string>>({})  // key → comma-separated values
  const [expandedParams, setExpandedParams] = useState<Record<string, boolean>>({})  // UI-only expand state
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchResults, setSearchResults] = useState<any[]>([])

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
    setComparing(true)
    setCompareData([])
    const results: typeof compareData = []
    let errors: string[] = []
    for (const id of ids) {
      try {
        const res = await getPortfolioRun(id)
        const d = res.data
        const ec = d.equity_curve || d.equity || []
        results.push({
          id, name: `${d.strategy_name || '未知'} (${(d.start_date || '').slice(0, 10)}~${(d.end_date || '').slice(0, 10)})`,
          equity: ec.map((v: number) => v / (ec[0] || 1)),
          dates: [], metrics: d.metrics || {},
        })
      } catch (e: any) {
        errors.push(`${id}: ${e?.response?.data?.detail || e?.message || '失败'}`)
      }
    }
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
    setEvalLoading(true); setEvalResult(null); setCorrResult(null)
    try {
      const [evalRes, corrRes] = await Promise.all([
        evaluateFactors({ symbols: symbolList, start_date: startDate, end_date: endDate, factor_names: evalFactors, forward_days: 5, eval_freq: 'weekly', neutralize }),
        evalFactors.length >= 2
          ? factorCorrelation({ symbols: symbolList, start_date: startDate, end_date: endDate, factor_names: evalFactors })
          : Promise.resolve(null),
      ])
      setEvalResult(evalRes.data)
      if (corrRes) setCorrResult(corrRes.data)
    } catch (e: any) { alert(e?.response?.data?.detail || e?.message || '评估失败') }
    finally { setEvalLoading(false) }
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
    setLoading(true); setResult(null); setWfResult(null)
    try {
      const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
      // Filter out UI-only keys (e.g., _expand_*) from strategy params
      const cleanParams = Object.fromEntries(Object.entries(strategyParams).filter(([k]) => !k.startsWith('_')))
      const res = await runPortfolioBacktest({
        strategy_name: selected, symbols: symbolList,
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
      })
      setResult(res.data)
      loadHistory()
    } catch (e: any) {
      alert(e?.response?.data?.detail || JSON.stringify(e?.response?.data) || 'Failed')
    } finally { setLoading(false) }
  }

  const handleWalkForward = async () => {
    setWfLoading(true); setWfResult(null)
    try {
      const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
      const res = await portfolioWalkForward({
        strategy_name: selected, symbols: symbolList,
        start_date: startDate, end_date: endDate, freq,
        strategy_params: Object.fromEntries(Object.entries(strategyParams).filter(([k]) => !k.startsWith('_'))),
        initial_cash: settings.initial_cash,
        n_splits: wfSplits, train_ratio: wfTrainRatio,
        benchmark_symbol: settings.benchmark,
        buy_commission_rate: settings.buy_commission_rate,
        sell_commission_rate: settings.sell_commission_rate,
        min_commission: settings.min_commission,
        stamp_tax_rate: settings.stamp_tax_rate,
        slippage_rate: settings.slippage_rate,
        lot_size: settings.lot_size, limit_pct: settings.limit_pct,
      })
      setWfResult(res.data)
    } catch (e: any) {
      alert('前推验证 失败: ' + (e?.response?.data?.detail || e?.message || ''))
    } finally { setWfLoading(false) }
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
      // Use schema.options if provided, otherwise fall back to available_factors (grouped by category)
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
          {/* 已选标签（始终显示） */}
          {selected_vals.length > 0 && !expanded && (
            <div className="flex flex-wrap gap-1">
              {selected_vals.filter(v => v && options.includes(v)).map(v => (
                <span key={v} className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: 'var(--color-accent)', color: '#fff' }}>
                  {FACTOR_LABELS[v] || v}
                  <button onClick={() => updateParam(key, selected_vals.filter(x => x !== v))} className="ml-1 opacity-70 hover:opacity-100">×</button>
                </span>
              ))}
            </div>
          )}
          {/* 展开后的选择面板 */}
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
    total_return: '总收益率', annualized_return: '年化收益率', sharpe_ratio: '夏普比率',
    sortino_ratio: '索提诺比率', max_drawdown: '最大回撤', max_drawdown_duration: '回撤持续(天)',
    benchmark_return: '基准收益率', alpha: '超额收益(Alpha)', beta: '市场敏感度(Beta)',
    trade_count: '交易次数', turnover_per_rebalance: '每次调仓换手率',
    annualized_volatility: '年化波动率', n_rebalances: '调仓次数',
    concentration_hhi: '持仓集中度',
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
              {/* V2.11.1: AlphaCombiner sub-panel when factor=alpha_combiner */}
              {strategyParams.factor === 'alpha_combiner' && (
                <div className="col-span-full p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                  <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>子因子 (多选)</label>
                  <div className="flex flex-wrap gap-1 mb-2">
                    {factors.filter(f => f !== 'alpha_combiner').map(f => (
                      <button key={f} onClick={() => {
                        const cur = strategyParams.alpha_factors || []
                        if (cur.includes(f)) updateParam('alpha_factors', cur.filter((x: string) => x !== f))
                        else updateParam('alpha_factors', [...cur, f])
                      }}
                        className="text-xs px-2 py-0.5 rounded"
                        style={{ backgroundColor: (strategyParams.alpha_factors || []).includes(f) ? 'var(--color-accent)' : 'var(--bg-secondary)',
                                 color: (strategyParams.alpha_factors || []).includes(f) ? '#fff' : 'var(--text-secondary)',
                                 border: '1px solid var(--border)' }}>
                        {FACTOR_LABELS[f] || f}
                      </button>
                    ))}
                  </div>
                  <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>合成方法</label>
                  <select value={strategyParams.alpha_method || 'equal'} onChange={e => updateParam('alpha_method', e.target.value)}
                    className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
                    <option value="equal">等权</option>
                    <option value="ic">IC加权 (选股能力越强权重越大)</option>
                    <option value="icir">ICIR加权 (又强又稳的权重更大)</option>
                  </select>
                </div>
              )}
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
            <div className="mb-3">
              <DateRangePicker startDate={startDate} endDate={endDate} onStartChange={setStartDate} onEndChange={setEndDate} />
            </div>
            <div className="mb-3">
              <BacktestSettings value={settings} onChange={setSettings} />
            </div>
            <div className="mb-3">
              <div className="flex items-center gap-2 mb-1">
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
            <div className="flex gap-2 flex-wrap">
              <button onClick={handleRun} disabled={loading} className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: loading ? '#30363d' : 'var(--color-accent)' }}>
                {loading ? '运行中...' : '运行组合回测'}
              </button>
              <button onClick={handleWalkForward} disabled={wfLoading} className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: wfLoading ? '#30363d' : '#7c3aed' }}>
                {wfLoading ? '验证中...' : '前推验证'}
              </button>
              <button onClick={() => setSearchMode(!searchMode)} className="px-3 py-1.5 rounded text-sm font-medium"
                style={{ backgroundColor: searchMode ? '#1e6b3a' : 'var(--bg-primary)', color: searchMode ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                参数搜索
              </button>
              <input type="number" value={wfSplits} min={2} max={20} onChange={e => setWfSplits(Number(e.target.value) || 5)}
                className="w-14 px-2 py-1.5 rounded text-xs" style={inputStyle} title="折数" />
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>折</span>
              <input type="number" value={wfTrainRatio} min={0.1} max={0.9} step={0.1} onChange={e => setWfTrainRatio(Number(e.target.value) || 0.7)}
                className="w-16 px-2 py-1.5 rounded text-xs" style={inputStyle} title="训练比例" />
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>训练</span>
            </div>
          </div>

          {/* V2.11.1: Parameter Search Panel */}
          {searchMode && (
            <div className="p-4 rounded mb-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
              <h4 className="text-sm font-medium mb-1">参数搜索 — {strategies.find(s => s.name === selected)?.name || selected}</h4>
              <p className="text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
                为当前策略的每个参数设置多个候选值（逗号分隔），系统自动组合并按夏普排名。
              </p>
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
                        <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>{label} (多选)</label>
                        {useCategories ? factorCategories.map(cat => {
                          const catFactors = (Array.isArray(cat.factors) ? cat.factors : [])
                            .map((f: any) => typeof f === 'string' ? f : (f.key || f.class_name || ''))
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
              <button onClick={async () => {
                const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
                if (symbolList.length === 0) { alert('请填写股票池'); return }

                // Build param_grid from searchGrid
                const paramGrid: Record<string, any[]> = {}
                let totalCombos = 1
                for (const [key, schema] of Object.entries(currentSchema)) {
                  const raw = searchGrid[key] || ''
                  if (!raw) continue
                  if (schema.type === 'select') {
                    const vals = raw.split(',').filter(Boolean)
                    if (vals.length > 0) { paramGrid[key] = vals; totalCombos *= vals.length }
                  } else if (schema.type === 'multi_select') {
                    // Each selected value becomes a single-element list (one factor per combo)
                    const vals = raw.split(',').filter(Boolean)
                    if (vals.length > 0) { paramGrid[key] = vals.map(v => [v]); totalCombos *= vals.length }
                  } else if (schema.type === 'int') {
                    const vals = raw.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n))
                    if (vals.length > 0) { paramGrid[key] = vals; totalCombos *= vals.length }
                  } else if (schema.type === 'float') {
                    const vals = raw.split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n))
                    if (vals.length > 0) { paramGrid[key] = vals; totalCombos *= vals.length }
                  }
                }
                if (Object.keys(paramGrid).length === 0) { alert('请至少为一个参数设置多个候选值'); return }

                setSearchLoading(true); setSearchResults([])
                try {
                  const res = await portfolioSearch({
                    strategy_name: selected, symbols: symbolList, market: 'cn_stock',
                    start_date: startDate, end_date: endDate, freq,
                    param_grid: paramGrid, max_combinations: 50,
                    buy_commission_rate: settings.buy_commission_rate, sell_commission_rate: settings.sell_commission_rate,
                    min_commission: settings.min_commission, stamp_tax_rate: settings.stamp_tax_rate,
                    slippage_rate: settings.slippage_rate, lot_size: settings.lot_size, limit_pct: settings.limit_pct,
                    initial_cash: settings.initial_cash, benchmark_symbol: settings.benchmark,
                  })
                  setSearchResults(res.data.results || [])
                } catch (e: any) { alert(e?.response?.data?.detail || '搜索失败') }
                finally { setSearchLoading(false) }
              }} disabled={searchLoading}
                className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: searchLoading ? '#30363d' : '#1e6b3a' }}>
                {searchLoading ? '搜索中...' : '开始搜索'}
              </button>
              {searchResults.length > 0 && (
                <div className="mt-3 overflow-x-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                  <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                    <thead><tr style={{ backgroundColor: 'var(--bg-primary)' }}>
                      {['#', '参数', '夏普比率', '总收益率', '年化收益率', '最大回撤', '交易次数'].map(h => (
                        <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                      ))}
                    </tr></thead>
                    <tbody>{searchResults.map((r, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: i === 0 ? 'rgba(34,197,94,0.08)' : undefined }}>
                        <td className="px-3 py-1.5 font-medium">{r.rank}</td>
                        <td className="px-3 py-1.5">{Object.entries(r.params || {}).map(([k, v]) => {
                          const label = currentSchema[k]?.label || k
                          const val = typeof v === 'string' ? (FACTOR_LABELS[v] || v) : String(v)
                          return `${label}=${val}`
                        }).join(', ')}</td>
                        <td className="px-3 py-1.5" style={{ color: (r.sharpe || 0) > 1 ? '#22c55e' : 'var(--text-primary)' }}>{r.sharpe?.toFixed(3) ?? '-'}</td>
                        <td className="px-3 py-1.5">{r.total_return != null ? (r.total_return * 100).toFixed(1) + '%' : '-'}</td>
                        <td className="px-3 py-1.5">{r.annualized_return != null ? (r.annualized_return * 100).toFixed(1) + '%' : '-'}</td>
                        <td className="px-3 py-1.5" style={{ color: 'var(--color-down)' }}>{r.max_drawdown != null ? (r.max_drawdown * 100).toFixed(1) + '%' : '-'}</td>
                        <td className="px-3 py-1.5">{r.trade_count ?? '-'}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
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
              <div className="flex gap-2 mb-3">
                <button onClick={exportEquityCurve} className="text-xs px-2 py-1 rounded" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>导出净值CSV</button>
                {result.trades.length > 0 && <button onClick={exportTrades} className="text-xs px-2 py-1 rounded" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>导出交易CSV</button>}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                {Object.entries(result.metrics).filter(([k]) => k in metricLabels).map(([k, v]) => (
                  <div key={k} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                    <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{metricLabels[k] || k}</div>
                    <div className="text-sm font-medium" style={{ color: k === 'max_drawdown' ? 'var(--color-down)' : k === 'sharpe_ratio' && (v as number) > 1 ? 'var(--color-up)' : 'var(--text-primary)' }}>
                      {['total_return', 'annualized_return', 'max_drawdown', 'annualized_volatility', 'turnover_per_rebalance', 'benchmark_return', 'alpha'].includes(k) ? fmt(v as number, true) : k === 'trade_count' || k === 'n_rebalances' || k === 'max_drawdown_duration' ? String(v) : fmt(v as number)}
                    </div>
                  </div>
                ))}
              </div>
              {equityOption && <ReactECharts option={equityOption} style={{ height: 300 }} />}
              {/* 持仓分布饼图 */}
              {result.latest_weights && Object.keys(result.latest_weights).length > 0 && (
                <div className="mt-3">
                  <ReactECharts option={{
                    backgroundColor: '#0d1117',
                    title: { text: '最新持仓分布', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
                    tooltip: { trigger: 'item' as const, formatter: '{b}: {d}%' },
                    series: [{
                      type: 'pie', radius: ['30%', '55%'], center: ['50%', '55%'],
                      label: { color: '#8b949e', fontSize: 10 },
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
              {/* 持仓变动表 */}
              {result.weights_history && result.weights_history.length > 0 && (
                <div className="mt-3">
                  <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>持仓变动 (最近{result.weights_history.length}期)</h4>
                  <div className="overflow-x-auto max-h-48 overflow-y-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                    <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                      <thead><tr style={{ backgroundColor: 'var(--bg-primary)', position: 'sticky', top: 0 }}>
                        <th className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>调仓日期</th>
                        <th className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>持仓</th>
                      </tr></thead>
                      <tbody>{result.weights_history.map((wh, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
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

          {/* 前推验证 Result */}
          {wfResult && (
            <div className="p-4 rounded mt-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
              <h4 className="text-sm font-medium mb-2">前推验证结果 ({wfResult.n_splits} 折)</h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                  <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>样本外夏普</div>
                  <div className="text-sm font-medium">{wfResult.oos_metrics?.sharpe_ratio?.toFixed(4) ?? '-'}</div>
                </div>
                <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                  <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>样本外总收益</div>
                  <div className="text-sm font-medium">{wfResult.oos_metrics?.total_return != null ? (wfResult.oos_metrics.total_return * 100).toFixed(2) + '%' : '-'}</div>
                </div>
                <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                  <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>过拟合评分 (越小越好)</div>
                  <div className="text-sm font-medium" style={{ color: wfResult.overfitting_score > 0.3 ? 'var(--color-down)' : 'var(--text-primary)' }}>
                    {wfResult.overfitting_score?.toFixed(2) ?? '-'}
                  </div>
                </div>
                {wfResult.significance && (
                  <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                    <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>显著性 p</div>
                    <div className="text-sm font-medium" style={{ color: wfResult.significance.is_significant ? 'var(--color-up)' : 'var(--color-down)' }}>
                      {wfResult.significance.p_value?.toFixed(3) ?? '-'}
                    </div>
                  </div>
                )}
              </div>
              <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                样本内夏普: [{wfResult.is_sharpes?.map((s: number) => s.toFixed(2)).join(', ')}] |
                样本外夏普: [{wfResult.oos_sharpes?.map((s: number) => s.toFixed(2)).join(', ')}]
              </div>
              <button onClick={() => setWfResult(null)} className="mt-2 text-xs px-2 py-1 rounded"
                style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>关闭</button>
            </div>
          )}
        </>
      )}

      {tab === 'factor-research' && (
        <div className="p-4 rounded mb-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <h3 className="text-sm font-medium mb-3">选股因子研究</h3>
          <p className="text-xs mb-3" style={{ color: 'var(--text-secondary)' }}>
            测试"用这个指标选股靠不靠谱"。选因子 → 填股票池 → 评估 → 看选股能力和分档收益。
          </p>
          <div className="mb-3">
            <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>选择因子 (可多选):</label>
            {factorCategories.length > 0 ? factorCategories.map(cat => {
              // Simplify category label: remove English
              return (
              <div key={cat.key} className="mb-2">
                <span className="text-xs font-medium mr-2" style={{ color: 'var(--text-secondary)' }}>{CATEGORY_LABELS[cat.key] || cat.label}:</span>
                <div className="flex flex-wrap gap-1 mt-0.5">
                  {(Array.isArray(cat.factors) ? cat.factors : []).map((f: any) => {
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
            <div className="flex items-center gap-2 mb-1">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>股票池</label>
              <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH,513880.SH,513260.SH,159985.SZ')}
                className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>宽基ETF</button>
            </div>
            <textarea value={symbols} onChange={e => setSymbols(e.target.value)} rows={2} className="w-full px-3 py-1.5 rounded text-sm font-mono" style={inputStyle} />
          </div>
          <div className="flex items-center gap-2 mb-3">
            <button onClick={async () => {
              const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
              if (symbolList.length === 0) return
              setFetchingFunda(true); setFundaStatus('获取中...')
              try {
                // Determine if fina_indicator data needed based on factor categories metadata
                const finaFactorKeys = new Set<string>()
                factorCategories.forEach(cat => {
                  if (Array.isArray(cat.factors)) cat.factors.forEach((f: any) => {
                    if (typeof f === 'object' && f.needs_fina) finaFactorKeys.add(f.key || f.class_name || '')
                  })
                })
                const hasFina = evalFactors.some(f => finaFactorKeys.has(f))
                const res = await fetchFundamentalData({ symbols: symbolList, start_date: startDate, end_date: endDate, include_fina: hasFina })
                setFundaStatus(res.data.message || '完成')
                // Fetch quality report
                const qr = await fundamentalDataQuality({ symbols: symbolList, start_date: startDate, end_date: endDate })
                setQualityReport(qr.data.report || [])
              } catch (e: any) { setFundaStatus(e.response?.data?.detail || '获取失败') }
              finally { setFetchingFunda(false) }
            }} disabled={fetchingFunda}
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
                  <tbody>{evalResult.results.map((r: any) => (
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
                  legend: { data: evalResult.results.map((r: any) => FACTOR_LABELS[r.factor_name] || r.factor_name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 25 },
                  grid: { left: 60, right: 20, top: 50, bottom: 30 },
                  xAxis: { type: 'time' as const, axisLabel: { color: '#8b949e', fontSize: 9 } },
                  yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
                  color: ['#2563eb', '#ef4444', '#22c55e', '#eab308', '#8b5cf6'],
                  series: evalResult.results.map((r: any) => ({
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
                    legend: { data: evalResult.results.map((r: any) => FACTOR_LABELS[r.factor_name] || r.factor_name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 25 },
                    grid: { left: 60, right: 20, top: 50, bottom: 30 },
                    xAxis: { type: 'category' as const, data: ['1天', '5天', '10天', '20天'], axisLabel: { color: '#8b949e' } },
                    yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
                    color: ['#2563eb', '#ef4444', '#22c55e', '#eab308', '#8b5cf6'],
                    series: evalResult.results.map((r: any) => ({
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
                    series: evalResult.results.map((r: any) => ({
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
                    tooltip: { formatter: (p: any) => `${corrLabels[p.data[1]]} × ${corrLabels[p.data[0]]}: ${p.data[2].toFixed(3)}` },
                    grid: { left: 120, right: 40, top: 10, bottom: 40 },
                    xAxis: { type: 'category' as const, data: corrLabels, axisLabel: { color: '#8b949e', fontSize: 9, rotate: 30 } },
                    yAxis: { type: 'category' as const, data: corrLabels, axisLabel: { color: '#8b949e', fontSize: 9 } },
                    visualMap: { min: -1, max: 1, calculable: true, orient: 'vertical' as const, right: 0, top: 'center', inRange: { color: ['#2563eb', '#0d1117', '#ef4444'] }, textStyle: { color: '#8b949e' } },
                    series: [{
                      type: 'heatmap', data: corrResult.correlation_matrix.flatMap((row: number[], i: number) =>
                        row.map((v: number, j: number) => [j, i, Math.round(v * 1000) / 1000])),
                      label: { show: true, color: '#e6edf3', fontSize: 10, formatter: (p: any) => p.data[2].toFixed(2) },
                    }],
                  }} style={{ height: Math.max(200, corrResult.factor_names.length * 40 + 60) }} />
                </div>
              )})()}
            </div>
          )}
        </div>
      )}

      {tab === 'history' && (
        <div className="p-4 rounded" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium">历史组合回测</h3>
            {selectedRuns.size >= 2 && (
              <button onClick={handleCompare} disabled={comparing}
                className="text-xs px-3 py-1 rounded font-medium text-white"
                style={{ backgroundColor: comparing ? '#30363d' : '#2563eb' }}>
                {comparing ? '加载中...' : `对比选中 (${selectedRuns.size})`}
              </button>
            )}
          </div>
          {history.length === 0 ? <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>暂无记录</p> : (
            <div className="overflow-x-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
              <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                <thead><tr style={{ backgroundColor: 'var(--bg-primary)' }}>
                  {['选择', '策略', '区间', '频率', '夏普比率', '总收益率', '最大回撤', '交易次数', '创建时间', '操作'].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>{history.map(r => (
                  <tr key={r.run_id} style={{ borderBottom: '1px solid var(--border)', backgroundColor: selectedRuns.has(r.run_id) ? '#1e3a5f20' : 'transparent' }}>
                    <td className="px-3 py-1.5">
                      <input type="checkbox" checked={selectedRuns.has(r.run_id)} onChange={() => toggleRunSelection(r.run_id)} />
                    </td>
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

          {/* Compare overlay chart + metrics table */}
          {compareData.length >= 2 && (
            <div className="mt-4 p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
              <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>对比净值曲线 ({compareData.length} 条)</h4>
              <ReactECharts option={{
                backgroundColor: '#0d1117',
                tooltip: { trigger: 'axis' as const },
                legend: { data: compareData.map(d => d.name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 5, type: 'scroll' as const },
                grid: { left: 70, right: 20, top: 40, bottom: 30 },
                xAxis: { type: 'value' as const, name: '交易日序号', axisLabel: { color: '#8b949e' } },
                yAxis: { type: 'value' as const, name: '归一化净值', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
                color: ['#2563eb', '#ef4444', '#22c55e', '#eab308', '#8b5cf6', '#f97316'],
                series: compareData.map(d => ({
                  name: d.name, type: 'line' as const,
                  data: d.equity.map((v, i) => [i, v]),
                  showSymbol: false, lineStyle: { width: 1.5 },
                })),
              }} style={{ height: 280 }} />

              <h4 className="text-xs font-medium mt-3 mb-2" style={{ color: 'var(--text-secondary)' }}>指标对比</h4>
              <div className="overflow-x-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                  <thead><tr style={{ backgroundColor: 'var(--bg-secondary)' }}>
                    <th className="px-3 py-1.5 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>策略</th>
                    {['总收益率', '年化收益率', '夏普比率', '索提诺', '最大回撤', '年化波动率', '交易次数'].map(h => (
                      <th key={h} className="px-3 py-1.5 text-right font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>{compareData.map(d => (
                    <tr key={d.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td className="px-3 py-1.5 font-medium" style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</td>
                      <td className="px-3 py-1.5 text-right">{fmt(d.metrics?.total_return, true)}</td>
                      <td className="px-3 py-1.5 text-right">{fmt(d.metrics?.annualized_return, true)}</td>
                      <td className="px-3 py-1.5 text-right">{fmt(d.metrics?.sharpe_ratio)}</td>
                      <td className="px-3 py-1.5 text-right">{fmt(d.metrics?.sortino_ratio)}</td>
                      <td className="px-3 py-1.5 text-right" style={{ color: 'var(--color-down)' }}>{fmt(d.metrics?.max_drawdown, true)}</td>
                      <td className="px-3 py-1.5 text-right">{fmt(d.metrics?.annualized_volatility, true)}</td>
                      <td className="px-3 py-1.5 text-right">{d.metrics?.trade_count ?? '-'}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <button onClick={() => setCompareData([])} className="mt-2 text-xs px-2 py-1 rounded"
                style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>关闭对比</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
