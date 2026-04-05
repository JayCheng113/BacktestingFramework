import ReactECharts from 'echarts-for-react'
import type { HistoryRun } from '../types'

const fmt = (v: number | null | undefined, pct = false) => {
  if (v == null) return '-'
  return pct ? `${(v * 100).toFixed(2)}%` : v.toFixed(4)
}

interface Props {
  history: HistoryRun[]
  selectedRuns: Set<string>
  toggleRunSelection: (runId: string) => void
  compareData: { id: string; name: string; equity: number[]; dates: string[]; metrics: any }[]
  setCompareData: (v: { id: string; name: string; equity: number[]; dates: string[]; metrics: any }[]) => void
  comparing: boolean
  handleCompare: () => void
  handleDeleteRun: (runId: string) => void
}

export default function PortfolioHistoryContent(props: Props) {
  const {
    history, selectedRuns, toggleRunSelection,
    compareData, setCompareData, comparing,
    handleCompare, handleDeleteRun,
  } = props

  return (
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
              {['选择', '策略', '市场', '配置', '区间', '频率', '夏普比率', '总收益率', '最大回撤', '交易次数', '⚠', '创建时间', '操作'].map(h => (
                <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>{history.map(r => {
              // V2.12.2 codex: surface config summary (market, optimizer,
              // risk, index benchmark) + warning count directly in list so
              // users can audit past runs without drilling into each detail.
              const cs = r.config_summary || {}
              const marketLabel = cs.market === 'us_stock' ? '美股' : cs.market === 'hk_stock' ? '港股' : cs.market === 'cn_stock' ? 'A股' : (cs.market || '-')
              const configPieces: string[] = []
              if (cs.optimizer && cs.optimizer !== 'none') configPieces.push(`优化:${cs.optimizer}`)
              if (cs.risk_control) configPieces.push('风控')
              if (cs.index_benchmark) configPieces.push(`指数:${cs.index_benchmark}`)
              const configLabel = configPieces.length > 0 ? configPieces.join(' ') : '默认'
              return (
              <tr key={r.run_id} style={{ borderBottom: '1px solid var(--border)', backgroundColor: selectedRuns.has(r.run_id) ? '#1e3a5f20' : 'transparent' }}>
                <td className="px-3 py-1.5">
                  <input type="checkbox" checked={selectedRuns.has(r.run_id)} onChange={() => toggleRunSelection(r.run_id)} />
                </td>
                <td className="px-3 py-1.5">{r.strategy_name}</td>
                <td className="px-3 py-1.5" style={{ color: 'var(--text-secondary)' }}>{marketLabel}</td>
                <td className="px-3 py-1.5" style={{ color: 'var(--text-secondary)', fontSize: '10px' }} title={configLabel}>{configLabel}</td>
                <td className="px-3 py-1.5">{r.start_date?.slice(0, 10)}~{r.end_date?.slice(0, 10)}</td>
                <td className="px-3 py-1.5">{r.freq}</td>
                <td className="px-3 py-1.5">{fmt(r.metrics?.sharpe_ratio)}</td>
                <td className="px-3 py-1.5">{fmt(r.metrics?.total_return, true)}</td>
                <td className="px-3 py-1.5" style={{ color: 'var(--color-down)' }}>{fmt(r.metrics?.max_drawdown, true)}</td>
                <td className="px-3 py-1.5">{r.trade_count}</td>
                <td className="px-3 py-1.5" style={{ color: (r.warning_count || 0) > 0 ? '#d29922' : 'var(--text-muted)' }} title={(r.warning_count || 0) > 0 ? `${r.warning_count} 条警告` : '无警告'}>
                  {(r.warning_count || 0) > 0 ? r.warning_count : '-'}
                </td>
                <td className="px-3 py-1.5" style={{ color: 'var(--text-secondary)' }}>{r.created_at?.slice(0, 16)}</td>
                <td className="px-3 py-1.5">
                  <button onClick={() => handleDeleteRun(r.run_id)} className="text-xs px-1.5 py-0.5 rounded hover:opacity-80"
                    style={{ color: '#ef4444', border: '1px solid #7f1d1d' }}>删除</button>
                </td>
              </tr>
              )
            })}</tbody>
          </table>
        </div>
      )}

      {/* Compare overlay chart + metrics table */}
      {compareData.length >= 2 && (() => {
        // V2.12.2 codex: align overlays on real trading days when every
        // selected run has persisted dates (V2.12.2+ runs). If ANY
        // selected run is a legacy row that predates dates persistence
        // (dates is empty), fall back to index-based rendering and warn
        // the user — otherwise ECharts silently drops the date-less
        // series from a time axis.
        const allHaveDates = compareData.every(d =>
          Array.isArray(d.dates) && d.dates.length > 0 && d.dates.length === d.equity.length
        )
        const axisType = allHaveDates ? 'time' : 'value'
        const xName = allHaveDates ? '日期' : '交易日序号'
        return (
        <div className="mt-4 p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
          <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
            对比净值曲线 ({compareData.length} 条)
            {!allHaveDates && (
              <span className="ml-2" style={{ color: '#d29922', fontSize: '10px' }}>
                ⚠️ 部分历史记录无日期信息，降级按序号对齐 (V2.12.2 之前的记录)
              </span>
            )}
          </h4>
          <ReactECharts option={{
            backgroundColor: '#0d1117',
            tooltip: { trigger: 'axis' as const },
            legend: { data: compareData.map(d => d.name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 5, type: 'scroll' as const },
            grid: { left: 70, right: 20, top: 40, bottom: 30 },
            xAxis: { type: axisType as 'time' | 'value', name: xName, axisLabel: { color: '#8b949e' } },
            yAxis: { type: 'value' as const, name: '归一化净值', splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
            color: ['#2563eb', '#ef4444', '#22c55e', '#eab308', '#8b5cf6', '#f97316'],
            series: compareData.map(d => ({
              name: d.name, type: 'line' as const,
              data: allHaveDates
                ? d.equity.map((v, i) => [d.dates[i], v])
                : d.equity.map((v, i) => [i, v]),
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
        )
      })()}
    </div>
  )
}
