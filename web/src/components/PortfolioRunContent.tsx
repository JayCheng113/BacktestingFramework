import ReactECharts from 'echarts-for-react'
import type { PortfolioRunResult, ParamSchema, ActiveWeight } from '../types'
import type { BacktestSettingsValue } from './BacktestSettings'
import BacktestSettings from './BacktestSettings'
import DateRangePicker from './DateRangePicker'
import { useState } from 'react'
import { getPortfolioRunWeights } from '../api'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

const CATEGORY_LABELS: Record<string, string> = {
  technical: '量价', value: '估值', quality: '质量', growth: '成长',
  size: '规模', liquidity: '流动性', leverage: '杠杆', industry: '行业', other: '其他',
}

const FACTOR_LABELS: Record<string, string> = {
  momentum_rank_20: '20日动量', momentum_rank_10: '10日动量', momentum_rank_60: '60日动量',
  volume_rank_20: '成交量排名', reverse_vol_rank_20: '低波动',
  ep: '盈利收益率(EP)', bp: '市净率倒数(BP)', sp: '市销率倒数(SP)', dp: '股息率',
  roe: 'ROE', roa: 'ROA', gross_margin: '毛利率', net_profit_margin: '净利率',
  revenue_growth_yoy: '营收增速', profit_growth_yoy: '利润增速', roe_change: 'ROE变化',
  ln_market_cap: '总市值(小盘优先)', ln_circ_mv: '流通市值(小盘优先)',
  turnover_rate: '换手率', amihud_illiquidity: '流动性',
  debt_to_assets: '低负债率', current_ratio: '流动比率',
  industry_momentum: '行业动量',
  alpha_combiner: '多因子合成',
}

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

interface Props {
  // Shared state
  symbols: string; setSymbols: (v: string) => void
  startDate: string; setStartDate: (v: string) => void
  endDate: string; setEndDate: (v: string) => void
  freq: string; setFreq: (v: string) => void
  settings: BacktestSettingsValue; setSettings: (v: BacktestSettingsValue) => void
  strategies: { name: string; description: string; parameters: Record<string, ParamSchema> }[]
  factors: string[]
  factorCategories: { key: string; label: string; factors: any[] }[]
  selected: string; setSelected: (v: string) => void
  strategyParams: Record<string, any>; updateParam: (key: string, value: any) => void
  currentSchema: Record<string, ParamSchema>
  currentDesc: string
  // Run state
  result: PortfolioRunResult | null
  loading: boolean
  wfResult: any; setWfResult: (v: any) => void
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
  // Search
  searchMode: boolean; setSearchMode: (v: boolean) => void
  searchGrid: Record<string, string>; setSearchGrid: (v: Record<string, string> | ((prev: Record<string, string>) => Record<string, string>)) => void
  expandedParams: Record<string, boolean>; setExpandedParams: (v: Record<string, boolean> | ((prev: Record<string, boolean>) => Record<string, boolean>)) => void
  searchLoading: boolean; searchResults: any[]
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
  const {
    symbols, setSymbols, startDate, setStartDate, endDate, setEndDate, freq, setFreq,
    settings, setSettings, strategies, factors, factorCategories,
    selected, setSelected, strategyParams, updateParam, currentSchema, currentDesc,
    result, loading, wfResult, setWfResult, wfLoading, wfSplits, setWfSplits, wfTrainRatio, setWfTrainRatio,
    optimizer, setOptimizer, riskAversion, setRiskAversion, maxWeight, setMaxWeight,
    maxIndustryWeight, setMaxIndustryWeight, covLookback, setCovLookback,
    indexBenchmark, setIndexBenchmark, trackingError, setTrackingError,
    riskControl, setRiskControl, maxDrawdown, setMaxDrawdown, drawdownReduce, setDrawdownReduce,
    drawdownRecovery, setDrawdownRecovery, maxTurnover, setMaxTurnover,
    showOptimizer, setShowOptimizer, showRiskControl, setShowRiskControl, showAttribution, setShowAttribution,
    searchMode, setSearchMode, searchGrid, setSearchGrid,
    searchLoading, searchResults,
    handleRun, handleWalkForward, handleSearch, exportEquityCurve, exportTrades,
    renderParamInput,
  } = props

  // Local state for full weights loading
  const [fullWeights, setFullWeights] = useState<{ date: string; weights: Record<string, number> }[] | null>(null)
  const [weightsLoading, setWeightsLoading] = useState(false)

  const handleLoadFullWeights = async () => {
    if (!result?.run_id) return
    setWeightsLoading(true)
    try {
      const res = await getPortfolioRunWeights(result.run_id)
      setFullWeights(res.data.weights_history || res.data || [])
    } catch (e: any) {
      alert('加载完整历史失败: ' + (e?.response?.data?.detail || e?.message || ''))
    } finally { setWeightsLoading(false) }
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

  const weightsToShow = fullWeights || result?.weights_history

  return (
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
                    <option value="000300.SH">沪深300</option>
                    <option value="000905.SH">中证500</option>
                    <option value="000852.SH">中证1000</option>
                    <option value="000016.SH">上证50</option>
                    <option value="399006.SZ">创业板指</option>
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
          <button onClick={handleSearch} disabled={searchLoading}
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
                    .map(([sym, aw]) => (
                    <tr key={sym} style={{ borderBottom: '1px solid var(--border)' }}>
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
  )
}
