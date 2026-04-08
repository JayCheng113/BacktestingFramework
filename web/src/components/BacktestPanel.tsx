import { useState, useEffect, useRef } from 'react'
import ReactECharts from 'echarts-for-react'
import { listStrategies, runBacktest, runWalkForward } from '../api'
import type { StrategyInfo, BacktestResult, WalkForwardResult, ParamSchema } from '../types'
import BacktestSettings, { getDefaultSettings } from './BacktestSettings'
import type { BacktestSettingsValue } from './BacktestSettings'
import { useToast } from './shared/Toast'
import { CHART } from './shared/chartTheme'

interface Props {
  symbol: string; market: string; period?: string; startDate: string; endDate: string
  onTradesUpdate?: (trades: BacktestResult['trades']) => void
}

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

export default function BacktestPanel({ symbol, market, period = 'daily', startDate, endDate, onTradesUpdate }: Props) {
  const { showToast } = useToast()
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [selected, setSelected] = useState('')
  const [params, setParams] = useState<Record<string, number | string | boolean>>({})
  const [mode, setMode] = useState<'backtest' | 'walk-forward'>('backtest')
  const [nSplits, setNSplits] = useState(5)
  const [costSettings, setCostSettings] = useState<BacktestSettingsValue>(() => ({
    ...getDefaultSettings(market), benchmark: '', // no benchmark for single-stock
  }))
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [wfResult, setWfResult] = useState<WalkForwardResult | null>(null)
  const [loading, setLoading] = useState(false)
  // V2.12.2 codex round 8: monotonic run token for stale-response
  // invalidation. Every handleRun increments this; every await resolution
  // checks whether the token still matches before writing result state.
  // Input-change useEffect also increments to invalidate in-flight
  // requests when the user changes symbol/market/params mid-request.
  const runTokenRef = useRef(0)

  useEffect(() => {
    listStrategies().then(r => {
      const userStrategies = r.data.filter((s: StrategyInfo) => !s.key?.includes('research_'))
      setStrategies(userStrategies)
      if (userStrategies.length > 0) {
        // V2.12.1 post-review fix (codex): select by full key (module.class),
        // not class name. Name collisions between builtin and user strategies,
        // or after promote_research_strategy renames, would otherwise pick a
        // non-deterministic class.
        setSelected(userStrategies[0].key)
        const defaults: Record<string, number | string | boolean> = {}
        for (const [k, v] of Object.entries(userStrategies[0].parameters)) defaults[k] = (v as ParamSchema).default
        setParams(defaults)
      }
    }).catch((e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      showToast('error', err?.response?.data?.detail || err?.message || '策略列表加载失败')
    })
  }, [])

  // Reset cost settings when market changes (A-share rules only for cn_stock).
  // V2.12.1 codex follow-up: also reset benchmark — previously `...prev.benchmark`
  // kept the A-share benchmark (e.g. 510300.SH) after switching to US/HK, so the
  // result page compared US equity curves against a Chinese benchmark, giving
  // wrong alpha/beta and misleading curve overlays. getDefaultSettings() already
  // chooses a market-appropriate benchmark (empty for non-CN), so just use it.
  useEffect(() => {
    setCostSettings(getDefaultSettings(market))
  }, [market])

  // V2.12.2 codex: clear stale result when any search input changes (symbol,
  // market, period, dates). Prior version kept the old result visible
  // on-screen even though the inputs no longer matched the run, misleading
  // the user into thinking the metrics applied to the new inputs.
  //
  // Round 5 codex: also track params, costSettings, and nSplits — ALL of
  // these feed the /backtest/run or /backtest/walk-forward request. Prior
  // version left stale result visible when only strategy params or cost
  // settings changed.
  useEffect(() => {
    // V2.12.2 codex round 8: bump runTokenRef to invalidate any in-flight
    // request. Without this, a pending response from before the input
    // change would still pass its token check on return and re-populate
    // the result state the user just cleared.
    runTokenRef.current += 1
    setResult(null)
    setWfResult(null)
    onTradesUpdate?.([])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    symbol, market, period, startDate, endDate,
    JSON.stringify(params),
    JSON.stringify(costSettings),
    nSplits,
  ])

  const handleRun = async () => {
    if (!selected || !symbol) return
    if (new Date(startDate) >= new Date(endDate)) {
      showToast('warning', '开始日期必须早于结束日期')
      return
    }
    if (mode === 'walk-forward' && (nSplits < 2 || nSplits > 20)) {
      showToast('warning', '前推验证分割数须在 2-20 之间')
      return
    }
    // V2.12.2 codex round 8: capture a per-request token. The response
    // handler only applies setState if this token is still the latest —
    // otherwise the request was superseded by an input change or a new
    // run click, and its response is stale.
    const myToken = ++runTokenRef.current
    setLoading(true)
    setResult(null)
    setWfResult(null)
    // Clear stale trade markers on EVERY run (codex fix): previously the
    // walk-forward branch never called onTradesUpdate, so the KlineChart
    // kept drawing the last single-backtest trades — misleading the user.
    onTradesUpdate?.([])
    try {
      // V2.12.2 codex: pass both buy and sell rates. Prior version only
      // sent `commission_rate: buy_rate`, silently dropping the UI's
      // "sell commission" input.
      const costParams = {
        buy_commission_rate: costSettings.buy_commission_rate,
        sell_commission_rate: costSettings.sell_commission_rate,
        min_commission: costSettings.min_commission,
        slippage_rate: costSettings.slippage_rate,
        stamp_tax_rate: costSettings.stamp_tax_rate,
        lot_size: costSettings.lot_size,
        limit_pct: costSettings.limit_pct,
      }
      if (mode === 'backtest') {
        const res = await runBacktest({
          symbol, market, period, strategy_name: selected,
          strategy_params: params, start_date: startDate, end_date: endDate,
          ...costParams,
        })
        if (runTokenRef.current !== myToken) return  // superseded
        setResult(res.data)
        onTradesUpdate?.(res.data.trades || [])
      } else {
        const res = await runWalkForward({
          symbol, market, period, strategy_name: selected,
          strategy_params: params, start_date: startDate, end_date: endDate,
          n_splits: nSplits,
          ...costParams,
        })
        if (runTokenRef.current !== myToken) return  // superseded
        setWfResult(res.data)
      }
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      if (runTokenRef.current === myToken) showToast('error', err?.response?.data?.detail || 'Failed')
    } finally {
      // Only reset loading if we're still the latest request — otherwise
      // a newer in-flight request would be marked as "done" prematurely.
      if (runTokenRef.current === myToken) setLoading(false)
    }
  }

  const onStrategyChange = (key: string) => {
    setSelected(key)
    const s = strategies.find(s => s.key === key)
    if (s) {
      const defaults: Record<string, number | string | boolean> = {}
      for (const [k, v] of Object.entries(s.parameters)) defaults[k] = (v as ParamSchema).default
      setParams(defaults)
    }
    // V2.12.2 codex: clear previous-strategy results + trades so the user
    // never sees a metric panel labelled under a strategy that did not
    // actually run (prior result kept showing until next manual run).
    setResult(null)
    setWfResult(null)
    onTradesUpdate?.([])
  }

  const equityOption = result ? {
    backgroundColor: CHART.bg,
    title: { text: '权益曲线', textStyle: { color: CHART.text, fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    legend: { data: ['策略', '基准(买入持有)'], textStyle: { color: CHART.textSecondary }, top: 25 },
    grid: { left: 60, right: 20, top: 60, bottom: 30 },
    xAxis: { type: 'category', data: result.equity_curve.map((_: number, i: number) => i), axisLabel: { color: CHART.textSecondary } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: CHART.grid } }, axisLabel: { color: CHART.textSecondary } },
    series: [
      { name: '策略', type: 'line', data: result.equity_curve, lineStyle: { color: CHART.accent }, showSymbol: false },
      { name: '基准(买入持有)', type: 'line', data: result.benchmark_curve, lineStyle: { color: CHART.textSecondary, type: 'dashed' }, showSymbol: false },
    ],
  } : null

  const wfEquityOption = wfResult ? {
    backgroundColor: CHART.bg,
    title: { text: '样本外权益曲线', textStyle: { color: CHART.text, fontSize: 12 }, left: 'center' },
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: wfResult.oos_equity_curve.map((_: number, i: number) => i), axisLabel: { color: CHART.textSecondary } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: CHART.grid } }, axisLabel: { color: CHART.textSecondary } },
    series: [{ type: 'line', data: wfResult.oos_equity_curve, lineStyle: { color: CHART.down }, showSymbol: false }],
  } : null

  const exportCSV = (result: BacktestResult) => {
    const headers = '买入日期,卖出日期,买入价,卖出价,盈亏,盈亏%,手续费\n'
    const rows = result.trades.map(t =>
      `${t.entry_time.slice(0,10)},${t.exit_time.slice(0,10)},${t.entry_price},${t.exit_price},${t.pnl.toFixed(2)},${(t.pnl_pct*100).toFixed(2)}%,${t.commission.toFixed(2)}`
    ).join('\n')
    const blob = new Blob(['\uFEFF' + headers + rows], { type: 'text/csv;charset=utf-8' })
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

  // Chinese labels + color thresholds for metric cards
  const metricLabels: Record<string, string> = {
    sharpe_ratio: '夏普比率',
    total_return: '总收益率',
    annualized_return: '年化收益',
    max_drawdown: '最大回撤',
    max_drawdown_duration: '回撤持续(天)',
    alpha: 'Alpha',
    beta: 'Beta',
    win_rate: '胜率',
    trade_count: '交易次数',
    profit_factor: '盈亏比',
    avg_holding_days: '平均持仓(天)',
    annualized_volatility: '年化波动率',
    benchmark_return: '基准收益',
    sortino_ratio: 'Sortino',
  }

  // Color: good=red(up), bad=green(down), neutral=white
  function metricColor(k: string, v: number): string {
    if (k === 'sharpe_ratio') return v > 1 ? 'var(--color-up)' : v > 0 ? 'var(--text-primary)' : 'var(--color-down)'
    if (k === 'total_return' || k === 'annualized_return' || k === 'alpha') return v > 0 ? 'var(--color-up)' : v < 0 ? 'var(--color-down)' : 'var(--text-primary)'
    if (k === 'max_drawdown') return v > -0.1 ? 'var(--color-up)' : v > -0.2 ? 'var(--text-primary)' : 'var(--color-down)'
    if (k === 'win_rate') return v > 0.5 ? 'var(--color-up)' : v > 0.3 ? 'var(--text-primary)' : 'var(--color-down)'
    return 'var(--text-primary)'
  }

  return (
    <div className="p-4 rounded mt-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <h3 className="text-base font-semibold mb-3">回测</h3>
      <div className="flex flex-wrap gap-3 items-end mb-4">
        {/* Mode toggle */}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>模式</label>
          <select value={mode} onChange={e => {
            // V2.12.2 codex: also clear KlineChart trade markers. Prior
            // version left the previous mode's buy/sell markers on the
            // chart after switching modes, misleading the user into
            // thinking the markers belonged to the new mode's run.
            // V2.12.2 codex round 8 reviewer: also bump runTokenRef so
            // any in-flight request under the old mode is invalidated.
            // Without this, a pending backtest response would land in
            // `result` even after switching to walk-forward (invisible
            // bloat, not a visible bug but inconsistent with the rest
            // of round 8's race guards).
            runTokenRef.current += 1
            setMode(e.target.value as 'backtest' | 'walk-forward'); setResult(null); setWfResult(null); onTradesUpdate?.([])
          }}
            className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
            <option value="backtest">单次回测</option>
            <option value="walk-forward">前推验证</option>
          </select>
        </div>
        {/* Strategy */}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>策略</label>
          <select value={selected} onChange={e => onStrategyChange(e.target.value)}
            className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
            {/* Display class name, submit full key — avoids name collision picks */}
            {strategies.map(s => <option key={s.key} value={s.key}>{s.name}</option>)}
          </select>
          {selected && strategies.find(s => s.key === selected)?.description && (
            <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
              {strategies.find(s => s.key === selected)?.description}
            </div>
          )}
        </div>
        {/* Strategy params */}
        {Object.entries(params).map(([k, v]) => {
          const schema = strategies.find(s => s.key === selected)?.parameters?.[k]
          const ptype = schema?.type || (typeof v)
          if (ptype === 'bool' || typeof v === 'boolean') {
            return (
              <div key={k} className="flex items-center gap-2 self-end pb-1">
                <input type="checkbox" checked={!!v} onChange={e => setParams({ ...params, [k]: e.target.checked })}
                  className="rounded" />
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{schema?.label || k}</label>
              </div>
            )
          }
          if (ptype === 'str' || typeof v === 'string') {
            return (
              <div key={k} className="flex flex-col gap-1">
                <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{schema?.label || k}</label>
                <input type="text" value={String(v)} onChange={e => setParams({ ...params, [k]: e.target.value })}
                  className="px-3 py-1.5 rounded text-sm w-28" style={inputStyle} />
              </div>
            )
          }
          return (
            <div key={k} className="flex flex-col gap-1">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>{schema?.label || k}</label>
              <input type="number" value={Number(v)} onChange={e => setParams({ ...params, [k]: Number(e.target.value) })}
                className="px-3 py-1.5 rounded text-sm w-20" style={inputStyle}
                {...(schema?.min != null ? { min: schema.min } : {})}
                {...(schema?.max != null ? { max: schema.max } : {})}
                {...(schema?.step != null ? { step: schema.step } : {})} />
            </div>
          )
        })}
      </div>
      <div className="mt-4 mb-3">
        <BacktestSettings value={costSettings} onChange={setCostSettings}
          showBenchmark={false} showInitialCash={false} showSellCommission={false} />
      </div>
      <div className="flex flex-wrap gap-3 items-end mt-3 mb-4">
        {mode === 'walk-forward' && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>分割数</label>
            <input type="number" value={nSplits} min={2} max={20}
              onChange={e => setNSplits(Number(e.target.value))}
              className="px-3 py-1.5 rounded text-sm w-16" style={inputStyle} />
          </div>
        )}
        <button onClick={handleRun} disabled={loading}
          className="px-4 py-1.5 rounded text-sm font-medium text-white"
          style={{ backgroundColor: loading ? '#30363d' : 'var(--color-accent)' }}>
          {loading ? '运行中...' : '运行'}
        </button>
      </div>

      {/* Single backtest results */}
      {result && (
        <div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            {Object.entries(result.metrics).filter(([k]) => metricKeys.includes(k)).map(([k, v]) => (
              <div key={k} className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
                <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{metricLabels[k] || k.replace(/_/g, ' ')}</div>
                <div className="text-sm font-medium" style={{ color: metricColor(k, v) }}>{formatMetric(k, v)}</div>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs px-2 py-0.5 rounded"
              style={{
                backgroundColor: result.significance.is_significant ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                color: result.significance.is_significant ? CHART.success : CHART.error,
              }}>
              {result.significance.is_significant ? '显著' : '不显著'}
            </span>
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              {/* V2.12.2 codex round 7: isFinite guard on p_value and CI
                  bounds. Prior version did .toFixed(3) directly which
                  renders NaN (from constant-signal edge case or degenerate
                  data) as literal "NaN" text. Backend now returns 1.0 for
                  constant signals but this guard is defense-in-depth for
                  any future path that might return NaN. */}
              显著性 p={Number.isFinite(result.significance.p_value) ? result.significance.p_value.toFixed(3) : '—'}
              {' | '}夏普置信区间 [
                {Number.isFinite(result.significance.sharpe_ci_lower) ? result.significance.sharpe_ci_lower.toFixed(2) : '—'}
                ,{' '}
                {Number.isFinite(result.significance.sharpe_ci_upper) ? result.significance.sharpe_ci_upper.toFixed(2) : '—'}
              ]
            </span>
          </div>
          {equityOption && <ReactECharts option={equityOption} style={{ height: 300 }} />}
          {/* Trade Records Table */}
          {result.trades.length > 0 && (
            <div className="mt-6">
              <div className="flex justify-between items-center mb-2">
                <h4 className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>交易记录 ({result.trades.length})</h4>
                <button onClick={() => exportCSV(result)} className="text-xs px-2 py-1 rounded" style={{ ...inputStyle, cursor: 'pointer' }}>导出CSV</button>
              </div>
              <div className="overflow-x-auto max-h-80 overflow-y-auto" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
                <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ backgroundColor: 'var(--bg-primary)', position: 'sticky', top: 0 }}>
                      {['#', '买入日期', '卖出日期', '买入价', '卖出价', '盈亏', '盈亏%', '手续费'].map(h => (
                        <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: i % 2 === 0 ? 'rgba(255,255,255,0.04)' : 'transparent' }}>
                        <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>{i + 1}</td>
                        <td className="px-3 py-2">{t.entry_time.slice(0, 10)}</td>
                        <td className="px-3 py-2">{t.exit_time.slice(0, 10)}</td>
                        <td className="px-3 py-2">{t.entry_price.toFixed(2)}</td>
                        <td className="px-3 py-2">{t.exit_price.toFixed(2)}</td>
                        <td className="px-3 py-2" style={{ color: t.pnl >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}
                        </td>
                        <td className="px-3 py-2" style={{ color: t.pnl_pct >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                          {(t.pnl_pct * 100).toFixed(2)}%
                        </td>
                        <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>{t.commission.toFixed(2)}</td>
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
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>样本外夏普比率</div>
              <div className="text-sm font-medium">{(wfResult.oos_metrics.sharpe_ratio ?? 0).toFixed(4)}</div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>过拟合评分</div>
              <div className="text-sm font-medium" style={{ color: wfResult.overfitting_score > 0.5 ? CHART.error : CHART.success }}>
                {wfResult.overfitting_score.toFixed(4)}
              </div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>样本内外衰减</div>
              <div className="text-sm font-medium">{(wfResult.is_vs_oos_degradation * 100).toFixed(1)}%</div>
            </div>
            <div className="p-2 rounded text-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
              <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>分割数</div>
              <div className="text-sm font-medium">{wfResult.n_splits}</div>
            </div>
          </div>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs px-2 py-0.5 rounded"
              style={{
                backgroundColor: wfResult.overfitting_score <= 0.3 ? 'rgba(34,197,94,0.15)' : wfResult.overfitting_score <= 0.6 ? 'rgba(245,158,11,0.15)' : 'rgba(239,68,68,0.15)',
                color: wfResult.overfitting_score <= 0.3 ? CHART.success : wfResult.overfitting_score <= 0.6 ? CHART.warn : CHART.error,
              }}>
              {wfResult.overfitting_score <= 0.3 ? '稳健' : wfResult.overfitting_score <= 0.6 ? '轻微过拟合' : '过拟合'}
            </span>
          </div>
          {wfEquityOption && <ReactECharts option={wfEquityOption} style={{ height: 300 }} />}
        </div>
      )}
    </div>
  )
}
