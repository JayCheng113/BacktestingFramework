import { useState, useEffect, useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { listPortfolioStrategies, runPortfolioBacktest, listPortfolioRuns, deletePortfolioRun, getPortfolioRun, evaluateFactors, factorCorrelation } from '../api'
import BacktestSettings, { DEFAULT_SETTINGS } from './BacktestSettings'
import type { BacktestSettingsValue } from './BacktestSettings'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

interface PortfolioMetrics {
  total_return?: number; annualized_return?: number; sharpe_ratio?: number
  max_drawdown?: number; trade_count?: number; turnover_per_rebalance?: number
  annualized_volatility?: number; n_rebalances?: number
}

interface PortfolioRunResult {
  run_id: string; metrics: PortfolioMetrics; equity_curve: number[]
  benchmark_curve: number[]; dates: string[]; trades: any[]; rebalance_dates: string[]
  symbols_fetched?: number; symbols_skipped?: string[]
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
  const [selected, setSelected] = useState('')
  const [symbols, setSymbols] = useState('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [freq, setFreq] = useState('monthly')
  const [strategyParams, setStrategyParams] = useState<Record<string, any>>({})
  const [settings, setSettings] = useState<BacktestSettingsValue>(DEFAULT_SETTINGS)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<PortfolioRunResult | null>(null)
  const [history, setHistory] = useState<HistoryRun[]>([])
  const [tab, setTab] = useState<'run' | 'factor-research' | 'history'>('run')
  // Factor research state
  const [evalFactors, setEvalFactors] = useState<string[]>(['momentum_rank_20'])
  const [evalResult, setEvalResult] = useState<any>(null)
  const [corrResult, setCorrResult] = useState<any>(null)
  const [evalLoading, setEvalLoading] = useState(false)
  const [selectedRuns, setSelectedRuns] = useState<Set<string>>(new Set())
  const [compareData, setCompareData] = useState<{ id: string; name: string; equity: number[]; dates: string[]; metrics: any }[]>([])
  const [comparing, setComparing] = useState(false)

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
    if (selectedRuns.size < 2) { alert('请至少选择 2 条记录'); return }
    if (selectedRuns.size > 10) { alert('最多对比 10 条记录'); return }
    setComparing(true)
    setCompareData([])
    try {
      const data = await Promise.all(
        Array.from(selectedRuns).map(async id => {
          const res = await getPortfolioRun(id)
          const d = res.data
          return {
            id, name: `${d.strategy_name} (${d.start_date?.slice(0, 10)}~${d.end_date?.slice(0, 10)})`,
            equity: (d.equity_curve || []).map((v: number) => v / (d.equity_curve?.[0] || 1)),  // normalize to 1.0
            dates: [], metrics: d.metrics || {},
          }
        })
      )
      setCompareData(data)
    } catch (e: any) { alert('加载失败: ' + (e?.message || '')) }
    finally { setComparing(false) }
  }

  const handleEvaluateFactors = async () => {
    const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
    if (symbolList.length < 5) { alert('因子评估需要至少 5 个标的'); return }
    if (evalFactors.length === 0) { alert('请选择至少 1 个因子'); return }
    setEvalLoading(true); setEvalResult(null); setCorrResult(null)
    try {
      const [evalRes, corrRes] = await Promise.all([
        evaluateFactors({ symbols: symbolList, start_date: startDate, end_date: endDate, factor_names: evalFactors, forward_days: 5, eval_freq: 'weekly' }),
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
    setLoading(true); setResult(null)
    try {
      const symbolList = symbols.split(',').map(s => s.trim()).filter(Boolean)
      const res = await runPortfolioBacktest({
        strategy_name: selected, symbols: symbolList,
        start_date: startDate, end_date: endDate, freq,
        strategy_params: strategyParams,
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

  const fmt = (v: number | null | undefined, pct = false) => {
    if (v == null) return '-'
    return pct ? `${(v * 100).toFixed(2)}%` : v.toFixed(4)
  }

  // Render a single param input based on schema type
  const renderParamInput = (key: string, schema: ParamSchema) => {
    const label = schema.label || key
    const value = strategyParams[key] ?? schema.default

    if (schema.type === 'select') {
      // Use schema.options if provided, otherwise fall back to available_factors
      const options: string[] = schema.options ?? (factors.length > 0 ? factors : [String(schema.default)])
      return (
        <div key={key} className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
          <select value={value} onChange={e => updateParam(key, e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
            {options.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      )
    }

    if (schema.type === 'multi_select') {
      // Use schema.options if provided, otherwise fall back to available_factors
      const options: string[] = schema.options ?? (factors.length > 0 ? factors : [])
      const selected_vals: string[] = Array.isArray(value) ? value : [String(value)]
      return (
        <div key={key} className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</label>
          <div className="flex flex-wrap gap-1">
            {options.map(o => (
              <button key={o} onClick={() => {
                const cur = Array.isArray(strategyParams[key]) ? [...strategyParams[key]] : []
                if (cur.includes(o)) updateParam(key, cur.filter(x => x !== o))
                else updateParam(key, [...cur, o])
              }}
                className="text-xs px-2 py-0.5 rounded"
                style={{ backgroundColor: selected_vals.includes(o) ? 'var(--color-accent)' : 'var(--bg-primary)',
                         color: selected_vals.includes(o) ? '#fff' : 'var(--text-secondary)',
                         border: '1px solid var(--border)' }}>
                {o}
              </button>
            ))}
          </div>
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
    total_return: '总收益', annualized_return: '年化收益', sharpe_ratio: '夏普比率',
    sortino_ratio: 'Sortino', max_drawdown: '最大回撤', max_drawdown_duration: '回撤持续(天)',
    benchmark_return: '基准收益', alpha: 'Alpha', beta: 'Beta',
    trade_count: '交易次数', turnover_per_rebalance: '换手率/次',
    annualized_volatility: '年化波动', n_rebalances: '换仓次数',
  }

  const currentDesc = strategies.find(s => s.name === selected)?.description || ''

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex gap-2 mb-4">
        <button onClick={() => setTab('run')} className={`px-4 py-1.5 rounded text-sm ${tab === 'run' ? 'bg-blue-600 text-white' : ''}`} style={tab !== 'run' ? inputStyle : {}}>组合回测</button>
        <button onClick={() => setTab('factor-research')} className={`px-4 py-1.5 rounded text-sm ${tab === 'factor-research' ? 'bg-blue-600 text-white' : ''}`} style={tab !== 'factor-research' ? inputStyle : {}}>因子研究</button>
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
            <div className="flex flex-wrap gap-3 items-end mb-3">
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>开始日期</label>
                <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle} />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>结束日期</label>
                <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle} />
              </div>
            </div>
            <div className="mb-3">
              <BacktestSettings value={settings} onChange={setSettings} />
            </div>
            <div className="mb-3">
              <div className="flex items-center gap-2 mb-1">
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>标的池 (逗号分隔)</label>
                <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH,513880.SH,513260.SH,159985.SZ')}
                  className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>宽基ETF</button>
                <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,515100.SH,159531.SZ,513100.SH,513880.SH,513260.SH,513600.SH,518880.SH,159985.SZ')}
                  className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>ETF轮动池</button>
                <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,510880.SH,513100.SH,513880.SH,513260.SH,513660.SH,518880.SH,159985.SZ,162411.SZ,512010.SH,512690.SH,515700.SH,159852.SZ,159813.SZ,159851.SZ,515220.SH,159869.SZ,515880.SH,512660.SH,512980.SH')}
                  className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>行业+宽基22只</button>
              </div>
              <textarea value={symbols} onChange={e => setSymbols(e.target.value)} rows={2} className="w-full px-3 py-1.5 rounded text-sm font-mono" style={inputStyle} />
            </div>
            <button onClick={handleRun} disabled={loading} className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: loading ? '#30363d' : 'var(--color-accent)' }}>
              {loading ? '运行中...' : '运行组合回测'}
            </button>
          </div>

          {result && (
            <div className="p-4 rounded" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
              {result.symbols_skipped && result.symbols_skipped.length > 0 && (
                <div className="mb-3 px-3 py-2 rounded text-xs" style={{ backgroundColor: '#3b2a1a', border: '1px solid #6b4c2a', color: '#f59e0b' }}>
                  {result.symbols_skipped.length} 只标的无数据被跳过: {result.symbols_skipped.join(', ')}
                  （可能未上市或 Tushare 无覆盖）
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
                      {['total_return', 'annualized_return', 'max_drawdown', 'annualized_volatility', 'turnover_per_rebalance'].includes(k) ? fmt(v as number, true) : k === 'trade_count' || k === 'n_rebalances' ? String(v) : fmt(v as number)}
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
        </>
      )}

      {tab === 'factor-research' && (
        <div className="p-4 rounded mb-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
          <h3 className="text-sm font-medium mb-3">截面因子评估</h3>
          <p className="text-xs mb-3" style={{ color: 'var(--text-secondary)' }}>
            选择因子和标的池，计算截面 IC / Rank IC / ICIR / IC 衰减 / 分位数收益。
          </p>
          <div className="flex flex-wrap gap-2 mb-3">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>因子 (多选):</label>
            {factors.map(f => (
              <button key={f} onClick={() => setEvalFactors(prev => prev.includes(f) ? prev.filter(x => x !== f) : [...prev, f])}
                className="text-xs px-2 py-0.5 rounded"
                style={{ backgroundColor: evalFactors.includes(f) ? 'var(--color-accent)' : 'var(--bg-primary)',
                         color: evalFactors.includes(f) ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                {f}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-3 items-end mb-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>开始日期</label>
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>结束日期</label>
              <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="px-3 py-1.5 rounded text-sm" style={inputStyle} />
            </div>
          </div>
          <div className="mb-3">
            <div className="flex items-center gap-2 mb-1">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>标的池</label>
              <button onClick={() => setSymbols('510300.SH,510500.SH,159915.SZ,518880.SH,513100.SH,513880.SH,513260.SH,159985.SZ')}
                className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>宽基ETF</button>
            </div>
            <textarea value={symbols} onChange={e => setSymbols(e.target.value)} rows={2} className="w-full px-3 py-1.5 rounded text-sm font-mono" style={inputStyle} />
          </div>
          <button onClick={handleEvaluateFactors} disabled={evalLoading}
            className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: evalLoading ? '#30363d' : '#0891b2' }}>
            {evalLoading ? '评估中...' : '评估因子'}
          </button>

          {/* Evaluation results */}
          {evalResult && evalResult.results && (
            <div className="mt-4">
              {/* IC summary table */}
              <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>因子 IC 汇总</h4>
              <div className="overflow-x-auto mb-4" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                  <thead><tr style={{ backgroundColor: 'var(--bg-primary)' }}>
                    {['因子', 'IC均值', 'RankIC均值', 'ICIR', 'RankICIR', '评估日数', '平均覆盖'].map(h => (
                      <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>{evalResult.results.map((r: any) => (
                    <tr key={r.factor_name} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td className="px-3 py-1.5 font-mono">{r.factor_name}</td>
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
                  title: { text: 'Rank IC 时序', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
                  tooltip: { trigger: 'axis' as const },
                  legend: { data: evalResult.results.map((r: any) => r.factor_name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 25 },
                  grid: { left: 60, right: 20, top: 50, bottom: 30 },
                  xAxis: { type: 'category' as const, data: evalResult.results[0].eval_dates.map((d: string) => d.slice(0, 10)), axisLabel: { color: '#8b949e', rotate: 30, fontSize: 9 } },
                  yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
                  color: ['#2563eb', '#ef4444', '#22c55e', '#eab308', '#8b5cf6'],
                  series: evalResult.results.map((r: any) => ({
                    name: r.factor_name, type: 'line' as const,
                    data: r.rank_ic_series, showSymbol: false,
                  })),
                }} style={{ height: 250 }} />
              )}

              {/* IC decay + Quintile returns side by side */}
              <div className="grid grid-cols-2 gap-3 mt-3">
                {/* IC Decay */}
                {evalResult.results[0]?.ic_decay && (
                  <ReactECharts option={{
                    backgroundColor: '#0d1117',
                    title: { text: 'IC 衰减', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
                    tooltip: { trigger: 'axis' as const },
                    legend: { data: evalResult.results.map((r: any) => r.factor_name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 25 },
                    grid: { left: 60, right: 20, top: 50, bottom: 30 },
                    xAxis: { type: 'category' as const, data: ['1天', '5天', '10天', '20天'], axisLabel: { color: '#8b949e' } },
                    yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
                    color: ['#2563eb', '#ef4444', '#22c55e', '#eab308', '#8b5cf6'],
                    series: evalResult.results.map((r: any) => ({
                      name: r.factor_name, type: 'line' as const,
                      data: [r.ic_decay['1'], r.ic_decay['5'], r.ic_decay['10'], r.ic_decay['20']],
                    })),
                  }} style={{ height: 220 }} />
                )}
                {/* Quintile returns */}
                {evalResult.results[0]?.quintile_returns && (
                  <ReactECharts option={{
                    backgroundColor: '#0d1117',
                    title: { text: '分位数收益 (前向5天)', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
                    tooltip: { trigger: 'axis' as const },
                    grid: { left: 60, right: 20, top: 50, bottom: 30 },
                    xAxis: { type: 'category' as const, data: ['Q1(低)', 'Q2', 'Q3', 'Q4', 'Q5(高)'], axisLabel: { color: '#8b949e' } },
                    yAxis: { type: 'value' as const, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e', formatter: (v: number) => (v * 100).toFixed(2) + '%' } },
                    color: ['#2563eb', '#ef4444', '#22c55e'],
                    series: evalResult.results.map((r: any) => ({
                      name: r.factor_name, type: 'bar' as const,
                      data: [1, 2, 3, 4, 5].map(q => r.quintile_returns[String(q)] ?? 0),
                    })),
                  }} style={{ height: 220 }} />
                )}
              </div>

              {/* Correlation heatmap */}
              {corrResult && corrResult.factor_names?.length >= 2 && (
                <div className="mt-3">
                  <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>因子相关性矩阵</h4>
                  <ReactECharts option={{
                    backgroundColor: '#0d1117',
                    tooltip: { formatter: (p: any) => `${corrResult.factor_names[p.data[1]]} × ${corrResult.factor_names[p.data[0]]}: ${p.data[2].toFixed(3)}` },
                    grid: { left: 120, right: 40, top: 10, bottom: 40 },
                    xAxis: { type: 'category' as const, data: corrResult.factor_names, axisLabel: { color: '#8b949e', fontSize: 9, rotate: 30 } },
                    yAxis: { type: 'category' as const, data: corrResult.factor_names, axisLabel: { color: '#8b949e', fontSize: 9 } },
                    visualMap: { min: -1, max: 1, calculable: true, orient: 'vertical' as const, right: 0, top: 'center', inRange: { color: ['#2563eb', '#0d1117', '#ef4444'] }, textStyle: { color: '#8b949e' } },
                    series: [{
                      type: 'heatmap', data: corrResult.correlation_matrix.flatMap((row: number[], i: number) =>
                        row.map((v: number, j: number) => [j, i, Math.round(v * 1000) / 1000])),
                      label: { show: true, color: '#e6edf3', fontSize: 10, formatter: (p: any) => p.data[2].toFixed(2) },
                    }],
                  }} style={{ height: Math.max(200, corrResult.factor_names.length * 40 + 60) }} />
                </div>
              )}
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
                  {['', '策略', '区间', '频率', '夏普', '总收益', '最大回撤', '交易数', '时间', ''].map(h => (
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
                    {['总收益', '年化收益', '夏普', 'Sortino', '最大回撤', '年化波动', '交易数'].map(h => (
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
