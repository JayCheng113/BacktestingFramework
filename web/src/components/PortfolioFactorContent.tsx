import ReactECharts from 'echarts-for-react'
import DateRangePicker from './DateRangePicker'

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
  factorCategories: { key: string; label: string; factors: any[] }[]
  // Factor research state
  evalFactors: string[]; setEvalFactors: (v: string[] | ((prev: string[]) => string[])) => void
  neutralize: boolean; setNeutralize: (v: boolean) => void
  evalResult: any
  corrResult: any
  evalLoading: boolean
  fetchingFunda: boolean
  fundaStatus: string
  qualityReport: any[]
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
  )
}
