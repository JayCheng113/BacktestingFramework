/**
 * V2.15 C2: Paper Trading Dashboard — deployment list, equity curve,
 * metrics, trades, and control panel.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import ReactECharts from 'echarts-for-react'
import {
  listDeployments, getDeployment, getDashboard, getSnapshots, getTrades,
  approveDeployment, startDeployment, stopDeployment, pauseDeployment,
  resumeDeployment, triggerTick,
  type DeploymentSummary, type DeploymentDetail, type DeploymentHealth,
  type DashboardAlert, type SnapshotRecord, type TradeEntry,
} from '../api/live'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

const STATUS_LABELS: Record<string, string> = {
  pending: '待审批',
  approved: '已审批',
  running: '运行中',
  paused: '已暂停',
  stopped: '已停止',
  error: '异常',
}

const STATUS_COLORS: Record<string, string> = {
  pending: '#f59e0b',   // amber
  approved: '#3b82f6',  // blue
  running: '#22c55e',   // green
  paused: '#eab308',    // yellow
  stopped: '#6b7280',   // gray
  error: '#ef4444',     // red
}

const fmt = (v: number | null | undefined, pct = false, digits = 2) => {
  if (v == null || !isFinite(v)) return '-'
  return pct ? `${(v * 100).toFixed(digits)}%` : v.toFixed(digits)
}

export default function PaperTradingPage() {
  // ----- State -----
  const [deployments, setDeployments] = useState<DeploymentSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<DeploymentDetail | null>(null)
  const [healthMap, setHealthMap] = useState<Record<string, DeploymentHealth>>({})
  const [alerts, setAlerts] = useState<DashboardAlert[]>([])
  const [snapshots, setSnapshots] = useState<SnapshotRecord[]>([])
  const [trades, setTrades] = useState<TradeEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [tickDate, setTickDate] = useState('')
  const [tickLoading, setTickLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)

  // Race token refs
  const listTokenRef = useRef(0)
  const detailTokenRef = useRef(0)

  // ----- Fetch deployment list + dashboard -----
  const refreshList = useCallback(async () => {
    const token = ++listTokenRef.current
    try {
      const [listRes, dashRes] = await Promise.all([
        listDeployments(),
        getDashboard(),
      ])
      if (listTokenRef.current !== token) return
      setDeployments(listRes.data)
      const map: Record<string, DeploymentHealth> = {}
      for (const h of dashRes.data.deployments) {
        map[h.deployment_id] = h
      }
      setHealthMap(map)
      setAlerts(dashRes.data.alerts)
    } catch {
      // silent — dashboard auto-refreshes
    }
  }, [])

  useEffect(() => {
    refreshList()
    const timer = setInterval(refreshList, 15000) // poll every 15s
    return () => clearInterval(timer)
  }, [refreshList])

  // ----- Fetch detail when selected -----
  const refreshDetail = useCallback(async (id: string) => {
    const token = ++detailTokenRef.current
    setLoading(true)
    try {
      const [detailRes, snapRes, tradeRes] = await Promise.all([
        getDeployment(id),
        getSnapshots(id),
        getTrades(id),
      ])
      if (detailTokenRef.current !== token) return
      setDetail(detailRes.data)
      setSnapshots(snapRes.data)
      setTrades(tradeRes.data)
    } catch {
      // will show empty state
    } finally {
      if (detailTokenRef.current === token) setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      setSnapshots([])
      setTrades([])
      return
    }
    refreshDetail(selectedId)
  }, [selectedId, refreshDetail])

  // Auto-select first if none selected
  useEffect(() => {
    if (!selectedId && deployments.length > 0) {
      setSelectedId(deployments[0].deployment_id)
    }
  }, [deployments, selectedId])

  // ----- Action handlers -----
  const handleAction = async (action: () => Promise<unknown>) => {
    setActionLoading(true)
    try {
      await action()
      await refreshList()
      if (selectedId) await refreshDetail(selectedId)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      alert(err?.response?.data?.detail || err?.message || '操作失败')
    } finally {
      setActionLoading(false)
    }
  }

  const handleTick = async () => {
    if (!tickDate) {
      alert('请选择交易日期')
      return
    }
    setTickLoading(true)
    try {
      await triggerTick(tickDate)
      await refreshList()
      if (selectedId) await refreshDetail(selectedId)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      alert('Tick 失败: ' + (err?.response?.data?.detail || err?.message || ''))
    } finally {
      setTickLoading(false)
    }
  }

  // ----- Derived data -----
  const health = selectedId ? healthMap[selectedId] : null

  // Equity curve from snapshots
  const equityDates = snapshots.map(s => s.snapshot_date)
  const equityCurve = snapshots.map(s => s.equity)

  // Holdings pie from latest snapshot
  const latestSnap = snapshots.length > 0 ? snapshots[snapshots.length - 1] : null
  const positions = (latestSnap?.positions || {}) as Record<string, { shares: number; market_value: number }>
  const pieData = Object.entries(positions)
    .filter(([, v]) => v && typeof v === 'object' && (v as { market_value?: number }).market_value)
    .map(([sym, v]) => ({
      name: sym,
      value: Math.round(((v as { market_value: number }).market_value || 0)),
    }))
  const cashValue = latestSnap ? Math.round(latestSnap.cash) : 0
  if (cashValue > 0) {
    pieData.push({ name: '现金', value: cashValue })
  }

  // Risk events from snapshots
  const riskEvents: { date: string; event: string }[] = []
  for (const snap of snapshots) {
    for (const evt of (snap.risk_events || [])) {
      riskEvents.push({ date: snap.snapshot_date, event: evt })
    }
  }

  // Today's trades (last snapshot)
  const todayTrades = trades.filter(t => t.snapshot_date === latestSnap?.snapshot_date)

  // Gate verdict
  let gateVerdict: { passed?: boolean; summary?: string; reasons?: { rule: string; passed: boolean; message: string }[] } | null = null
  if (detail?.gate_verdict) {
    try {
      gateVerdict = typeof detail.gate_verdict === 'string' ? JSON.parse(detail.gate_verdict) : detail.gate_verdict
    } catch { /* ignore */ }
  }

  // ----- ECharts options -----
  const equityOption = equityDates.length > 0 ? {
    backgroundColor: '#0d1117',
    title: { text: '净值曲线', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
    tooltip: {
      trigger: 'axis' as const,
      formatter: (params: Array<{ axisValue: string; value: number }>) => {
        if (!params.length) return ''
        const p = params[0]
        return `${p.axisValue}<br/>净值: ${p.value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
      },
    },
    grid: { left: 70, right: 20, top: 40, bottom: 30 },
    xAxis: {
      type: 'category' as const,
      data: equityDates,
      axisLabel: { color: '#8b949e', rotate: 30, fontSize: 9 },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: '#21262d' } },
      axisLabel: { color: '#8b949e' },
    },
    series: [{
      type: 'line' as const,
      data: equityCurve,
      lineStyle: { color: '#2563eb' },
      areaStyle: { color: 'rgba(37, 99, 235, 0.1)' },
      showSymbol: false,
    }],
  } : null

  const pieOption = pieData.length > 0 ? {
    backgroundColor: '#0d1117',
    title: { text: '持仓分布', textStyle: { color: '#e6edf3', fontSize: 12 }, left: 'center' },
    tooltip: {
      trigger: 'item' as const,
      formatter: '{b}: {c} ({d}%)',
    },
    series: [{
      type: 'pie' as const,
      radius: ['35%', '60%'],
      data: pieData,
      label: { color: '#8b949e', fontSize: 10 },
      itemStyle: { borderColor: '#0d1117', borderWidth: 2 },
    }],
  } : null

  // ----- Render -----
  return (
    <div className="flex h-[calc(100vh-48px)]">
      {/* Left sidebar: deployment list */}
      <div className="w-72 flex-shrink-0 overflow-y-auto border-r" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
        <div className="p-3 border-b" style={{ borderColor: 'var(--border)' }}>
          <h2 className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>模拟盘部署</h2>
          <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
            {deployments.length} 个部署
            {alerts.length > 0 && <span style={{ color: '#ef4444' }}> | {alerts.length} 个告警</span>}
          </p>
        </div>
        {deployments.length === 0 && (
          <div className="p-4 text-center text-xs" style={{ color: 'var(--text-secondary)' }}>
            暂无部署。请先在组合回测中运行策略，然后点击 "部署到模拟盘"。
          </div>
        )}
        {deployments.map(d => {
          const h = healthMap[d.deployment_id]
          const isSelected = d.deployment_id === selectedId
          const hasAlert = alerts.some(a => a.deployment_id === d.deployment_id)
          return (
            <button
              key={d.deployment_id}
              onClick={() => setSelectedId(d.deployment_id)}
              className="w-full text-left px-3 py-2.5 border-b"
              style={{
                borderColor: 'var(--border)',
                backgroundColor: isSelected ? 'var(--bg-primary)' : 'transparent',
              }}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium truncate" style={{ color: 'var(--text-primary)', maxWidth: '160px' }}>
                  {d.name}
                </span>
                <div className="flex items-center gap-1.5">
                  {hasAlert && (
                    <span style={{ color: '#ef4444', fontSize: '10px' }} title="有告警">!</span>
                  )}
                  <span
                    className="text-xs px-1.5 py-0.5 rounded"
                    style={{
                      backgroundColor: `${STATUS_COLORS[d.status] || '#6b7280'}20`,
                      color: STATUS_COLORS[d.status] || '#6b7280',
                    }}
                  >
                    <span style={{
                      display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
                      backgroundColor: STATUS_COLORS[d.status] || '#6b7280',
                      marginRight: 4,
                    }} />
                    {STATUS_LABELS[d.status] || d.status}
                  </span>
                </div>
              </div>
              {h && (
                <div className="flex gap-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
                  <span style={{ color: (h.cumulative_return || 0) >= 0 ? '#22c55e' : '#ef4444' }}>
                    {fmt(h.cumulative_return, true)}
                  </span>
                  <span>今日 {fmt(h.today_pnl, true)}</span>
                </div>
              )}
              {!h && d.created_at && (
                <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  {d.created_at.slice(0, 10)}
                </div>
              )}
            </button>
          )
        })}
      </div>

      {/* Right panel: detail */}
      <div className="flex-1 overflow-y-auto p-4" style={{ backgroundColor: 'var(--bg-primary)' }}>
        {!selectedId && (
          <div className="h-full flex items-center justify-center" style={{ color: 'var(--text-secondary)' }}>
            <p className="text-sm">选择左侧部署查看详情</p>
          </div>
        )}

        {selectedId && loading && (
          <div className="h-full flex items-center justify-center" style={{ color: 'var(--text-secondary)' }}>
            <p className="text-sm">加载中...</p>
          </div>
        )}

        {selectedId && !loading && detail && (
          <div className="space-y-4">
            {/* Header + actions */}
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div>
                <h2 className="text-lg font-medium" style={{ color: 'var(--text-primary)' }}>{detail.name}</h2>
                <div className="flex items-center gap-3 mt-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
                  <span>策略: {detail.spec?.strategy_name || '-'}</span>
                  <span>市场: {detail.spec?.market || '-'}</span>
                  <span>标的: {detail.spec?.symbols?.length || 0} 只</span>
                  <span>初始资金: {detail.spec?.initial_cash?.toLocaleString() || '-'}</span>
                </div>
              </div>
              <div className="flex gap-2">
                {detail.status === 'pending' && (
                  <button
                    onClick={() => handleAction(() => approveDeployment(detail.deployment_id))}
                    disabled={actionLoading}
                    className="px-3 py-1.5 rounded text-xs font-medium text-white"
                    style={{ backgroundColor: actionLoading ? '#30363d' : '#3b82f6' }}
                  >
                    审批 (运行 DeployGate)
                  </button>
                )}
                {detail.status === 'approved' && (
                  <button
                    onClick={() => handleAction(() => startDeployment(detail.deployment_id))}
                    disabled={actionLoading}
                    className="px-3 py-1.5 rounded text-xs font-medium text-white"
                    style={{ backgroundColor: actionLoading ? '#30363d' : '#22c55e' }}
                  >
                    启动
                  </button>
                )}
                {detail.status === 'running' && (
                  <button
                    onClick={() => handleAction(() => pauseDeployment(detail.deployment_id))}
                    disabled={actionLoading}
                    className="px-3 py-1.5 rounded text-xs font-medium text-white"
                    style={{ backgroundColor: actionLoading ? '#30363d' : '#eab308' }}
                  >
                    暂停
                  </button>
                )}
                {detail.status === 'paused' && (
                  <button
                    onClick={() => handleAction(() => resumeDeployment(detail.deployment_id))}
                    disabled={actionLoading}
                    className="px-3 py-1.5 rounded text-xs font-medium text-white"
                    style={{ backgroundColor: actionLoading ? '#30363d' : '#22c55e' }}
                  >
                    恢复
                  </button>
                )}
                {(detail.status === 'running' || detail.status === 'paused') && (
                  <button
                    onClick={() => {
                      if (confirm('确定停止该部署？停止后无法恢复。')) {
                        handleAction(() => stopDeployment(detail.deployment_id, '手动停止'))
                      }
                    }}
                    disabled={actionLoading}
                    className="px-3 py-1.5 rounded text-xs font-medium text-white"
                    style={{ backgroundColor: actionLoading ? '#30363d' : '#ef4444' }}
                  >
                    停止
                  </button>
                )}
              </div>
            </div>

            {/* Gate verdict */}
            {gateVerdict && (
              <div className="p-3 rounded" style={{
                backgroundColor: 'var(--bg-secondary)',
                border: `1px solid ${gateVerdict.passed ? '#22c55e40' : '#ef444440'}`,
              }}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-medium" style={{ color: gateVerdict.passed ? '#22c55e' : '#ef4444' }}>
                    DeployGate: {gateVerdict.passed ? '通过' : '未通过'}
                  </span>
                  {gateVerdict.summary && (
                    <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{gateVerdict.summary}</span>
                  )}
                </div>
                {gateVerdict.reasons && gateVerdict.reasons.length > 0 && (
                  <div className="grid grid-cols-2 gap-1 mt-2">
                    {gateVerdict.reasons.map((r, i) => (
                      <span key={i} className="text-xs" style={{ color: r.passed ? '#8b949e' : '#ef4444' }}>
                        {r.passed ? '  ' : '  '} {r.message}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Alerts for this deployment */}
            {alerts.filter(a => a.deployment_id === selectedId).length > 0 && (
              <div className="p-3 rounded" style={{ backgroundColor: '#ef444410', border: '1px solid #ef444440' }}>
                <h4 className="text-xs font-medium mb-1" style={{ color: '#ef4444' }}>告警</h4>
                {alerts.filter(a => a.deployment_id === selectedId).map((a, i) => (
                  <div key={i} className="text-xs" style={{ color: '#ef4444' }}>{a.message}</div>
                ))}
              </div>
            )}

            {/* Metric cards */}
            {health && (
              <div className="grid grid-cols-4 gap-3">
                <MetricCard label="累计收益" value={fmt(health.cumulative_return, true)} positive={health.cumulative_return >= 0} />
                <MetricCard label="夏普比率" value={fmt(health.sharpe_ratio)} />
                <MetricCard label="最大回撤" value={fmt(health.max_drawdown, true)} negative />
                <MetricCard label="今日盈亏" value={fmt(health.today_pnl, true)} positive={health.today_pnl >= 0} />
                <MetricCard label="今日交易" value={String(health.today_trades)} />
                <MetricCard label="风控事件" value={String(health.total_risk_events)} warn={health.total_risk_events > 0} />
                <MetricCard label="连续亏损" value={`${health.consecutive_loss_days} 天`} warn={health.consecutive_loss_days >= 3} />
                <MetricCard label="错误次数" value={String(health.error_count)} warn={health.error_count > 0} />
              </div>
            )}

            {/* Equity curve */}
            {equityOption && (
              <div className="rounded" style={{ border: '1px solid var(--border)' }}>
                <ReactECharts option={equityOption} style={{ height: 300 }} />
              </div>
            )}

            {/* Holdings pie + Today's trades */}
            <div className="grid grid-cols-2 gap-4">
              {/* Pie */}
              <div className="rounded" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
                {pieOption ? (
                  <ReactECharts option={pieOption} style={{ height: 260 }} />
                ) : (
                  <div className="flex items-center justify-center h-64 text-xs" style={{ color: 'var(--text-secondary)' }}>
                    暂无持仓数据
                  </div>
                )}
              </div>

              {/* Today's trades */}
              <div className="rounded p-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
                <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>
                  当日交易 ({todayTrades.length})
                </h4>
                {todayTrades.length === 0 ? (
                  <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>暂无交易</p>
                ) : (
                  <div className="overflow-y-auto max-h-48">
                    <table className="w-full text-xs" style={{ color: 'var(--text-primary)' }}>
                      <thead>
                        <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>
                          <th className="text-left py-1">标的</th>
                          <th className="text-left py-1">方向</th>
                          <th className="text-right py-1">数量</th>
                          <th className="text-right py-1">价格</th>
                          <th className="text-right py-1">费用</th>
                        </tr>
                      </thead>
                      <tbody>
                        {todayTrades.map((t, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                            <td className="py-1">{t.symbol}</td>
                            <td className="py-1" style={{ color: t.side === 'buy' ? '#ef4444' : '#22c55e' }}>
                              {t.side === 'buy' ? '买入' : '卖出'}
                            </td>
                            <td className="text-right py-1">{t.shares}</td>
                            <td className="text-right py-1">{t.price?.toFixed(2)}</td>
                            <td className="text-right py-1">{t.cost?.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            {/* Risk events */}
            {riskEvents.length > 0 && (
              <div className="rounded p-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
                <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>
                  风控事件 ({riskEvents.length})
                </h4>
                <div className="overflow-y-auto max-h-32 space-y-1">
                  {riskEvents.map((e, i) => (
                    <div key={i} className="text-xs flex gap-2">
                      <span style={{ color: 'var(--text-secondary)' }}>{e.date}</span>
                      <span style={{ color: '#f59e0b' }}>{e.event}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* All trades */}
            {trades.length > 0 && (
              <div className="rounded p-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
                <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>
                  全部交易记录 ({trades.length})
                </h4>
                <div className="overflow-y-auto max-h-48">
                  <table className="w-full text-xs" style={{ color: 'var(--text-primary)' }}>
                    <thead>
                      <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>
                        <th className="text-left py-1">日期</th>
                        <th className="text-left py-1">标的</th>
                        <th className="text-left py-1">方向</th>
                        <th className="text-right py-1">数量</th>
                        <th className="text-right py-1">价格</th>
                        <th className="text-right py-1">费用</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trades.slice().reverse().map((t, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td className="py-1">{t.snapshot_date}</td>
                          <td className="py-1">{t.symbol}</td>
                          <td className="py-1" style={{ color: t.side === 'buy' ? '#ef4444' : '#22c55e' }}>
                            {t.side === 'buy' ? '买入' : '卖出'}
                          </td>
                          <td className="text-right py-1">{t.shares}</td>
                          <td className="text-right py-1">{t.price?.toFixed(2)}</td>
                          <td className="text-right py-1">{t.cost?.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Manual tick panel */}
            <div className="rounded p-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
              <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>手动触发 Tick</h4>
              <div className="flex gap-2 items-center">
                <input
                  type="date"
                  value={tickDate}
                  onChange={e => setTickDate(e.target.value)}
                  className="px-2 py-1 rounded text-xs"
                  style={inputStyle}
                />
                <button
                  onClick={handleTick}
                  disabled={tickLoading || !tickDate}
                  className="px-3 py-1 rounded text-xs font-medium text-white"
                  style={{ backgroundColor: tickLoading ? '#30363d' : 'var(--color-accent)' }}
                >
                  {tickLoading ? '执行中...' : '执行 Tick'}
                </button>
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  对所有运行中的部署执行指定日期的模拟交易
                </span>
              </div>
            </div>
          </div>
        )}

        {selectedId && !loading && !detail && (
          <div className="h-full flex items-center justify-center" style={{ color: 'var(--text-secondary)' }}>
            <p className="text-sm">无法加载部署详情</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ----- Sub-components -----

function MetricCard({ label, value, positive, negative, warn }: {
  label: string
  value: string
  positive?: boolean
  negative?: boolean
  warn?: boolean
}) {
  let valueColor = 'var(--text-primary)'
  if (positive === true) valueColor = '#22c55e'
  else if (positive === false) valueColor = '#ef4444'
  if (negative) valueColor = '#ef4444'
  if (warn) valueColor = '#f59e0b'

  return (
    <div className="p-3 rounded" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{label}</div>
      <div className="text-lg font-medium" style={{ color: valueColor }}>{value}</div>
    </div>
  )
}
