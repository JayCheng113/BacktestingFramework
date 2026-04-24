/**
 * V3.3.26: Release workflow frontend.
 *
 * This page now surfaces QMT shadow broker readiness, submit/release gates,
 * release candidate blockers, runtime state, and broker-order links instead
 * of staying at the old V2.15 paper-only dashboard layer.
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useToast } from '../components/shared/Toast'
import ReactECharts from 'echarts-for-react'
import { CHART } from '../components/shared/chartTheme'
import {
  listDeployments, getDeployment, getDashboard, getSnapshots, getTrades,
  approveDeployment, startDeployment, stopDeployment, pauseDeployment,
  resumeDeployment, triggerTick, getBrokerOrders, getBrokerState,
  syncBrokerState, cancelBrokerOrder,
  type DeploymentSummary, type DeploymentDetail, type DeploymentHealth,
  type DashboardAlert, type SnapshotRecord, type TradeEntry, type BrokerStateResponse,
  type BrokerOrderLink, type BrokerReconcileSummary, type QMTReadiness, type QMTHardGate, type QMTSubmitGate,
  type PreviewQMTReleaseGate, type RuntimeQMTReleaseGate,
} from '../api/live'

// V3.3.27 Fix-A Issue #5: structured per-panel sync error map so a single
// failing sub-request surfaces a visible retry bar next to the affected
// panel instead of silently showing empty state.
type SyncErrors = Record<string, string>

function extractErrorMessage(e: unknown): string {
  if (e instanceof Error && e.message) return e.message
  if (typeof e === 'object' && e !== null) {
    const err = e as { response?: { data?: { detail?: unknown } }; message?: string }
    const detailMsg = extractActionErrorMessage(err?.response?.data?.detail)
    if (detailMsg) return detailMsg
    if (err?.message) return err.message
  }
  if (typeof e === 'string' && e) return e
  return '未知错误'
}

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

const gateTone = (status?: string | null) => {
  switch (status) {
    case 'open':
    case 'candidate':
    case 'ready':
    case 'ok':
    case 'fresh':
    case 'callback_preferred':
      return '#22c55e'
    case 'blocked':
    case 'shadow_only':
    case 'degraded':
    case 'drift':
    case 'query_fallback':
    case 'stale':
      return '#f59e0b'
    case 'error':
    case 'failed':
    case 'disconnected':
    case 'unavailable':
      return '#ef4444'
    default:
      return 'var(--text-secondary)'
  }
}

const fmtGateLabel = (value?: string | null) => value ? value.replaceAll('_', ' ') : '-'

const TERMINAL_BROKER_ORDER_STATUSES = new Set([
  'cancel_requested',
  'filled',
  'canceled',
  'partially_canceled',
  'rejected',
  'junk',
  'order_error',
  'cancel_error',
])

type ReleaseSummary = {
  status: string | null
  candidate: boolean
  blockers: string[]
  source: 'runtime' | 'preview' | null
}

function extractActionErrorMessage(detail: unknown): string | null {
  if (typeof detail === 'string') {
    const text = detail.trim()
    return text || null
  }
  if (Array.isArray(detail)) {
    for (const item of detail) {
      const nested = extractActionErrorMessage(item)
      if (nested) return nested
    }
    return null
  }
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    for (const key of ['message', 'summary', 'error', 'reason']) {
      const nested = extractActionErrorMessage(record[key])
      if (nested) return nested
    }
    const verdict = extractActionErrorMessage(record.verdict)
    if (verdict) return verdict
    const nestedDetail = extractActionErrorMessage(record.detail)
    if (nestedDetail) return nestedDetail
  }
  return null
}

function resolveReleaseSummary(
  deployment: DeploymentSummary,
  health?: DeploymentHealth | null,
): ReleaseSummary {
  const runtimeBlockers = health?.qmt_release_blockers || []
  const runtimeStatus = health?.qmt_release_gate_status || (runtimeBlockers.length > 0 ? 'blocked' : null)
  const runtimeCandidate = Boolean(health?.qmt_release_candidate)
  // V3.3.27 Fix-A Issue #6: runtime truth requires both
  //   (a) a runtime signal (projection/blockers/candidate/status), AND
  //   (b) the deployment is actually `running`.
  // Outside of `running`, the source is at best preview — prior code
  // accepted any runtime signal as runtime truth, which could surface a
  // stale projection from a paused/stopped deployment as if it were
  // currently authoritative.
  const hasRuntimeSignal = Boolean(
    health?.qmt_projection_source === 'runtime'
    || runtimeStatus
    || runtimeCandidate
    || runtimeBlockers.length > 0,
  )
  const isRunning = deployment.status === 'running'
  if (hasRuntimeSignal && isRunning) {
    return {
      status: runtimeStatus,
      candidate: runtimeCandidate,
      blockers: runtimeBlockers,
      source: 'runtime',
    }
  }

  const previewGate = deployment.qmt_release_gate
  if (!previewGate) {
    return {
      status: null,
      candidate: false,
      blockers: [],
      source: null,
    }
  }
  return {
    status: previewGate.status || (previewGate.blockers?.length ? 'blocked' : null),
    candidate: Boolean(previewGate.eligible_for_release_candidate),
    blockers: previewGate.blockers || [],
    // V3.3.27 Fix-A Issue #6: if the deployment is not running, we force
    // display as preview even if the API returned source=runtime. The API
    // truth can still claim runtime, but for display purposes we degrade
    // to preview to avoid misleading the operator.
    source: isRunning ? (previewGate.source || 'preview') : 'preview',
  }
}

function formatTimestamp(value?: string | null): string | null {
  if (!value) return null
  return value.slice(0, 19).replace('T', ' ')
}

function appendSummaryItem(items: string[], label: string, value?: string | null) {
  if (!value) return
  items.push(`${label}:${value}`)
}

function summarizeRiskSummary(
  summary: BrokerReconcileSummary | QMTHardGate | null | undefined,
  fallbackAccountId?: string | null,
): string[] {
  if (!summary) return []
  const items: string[] = []
  appendSummaryItem(items, 'event', summary.event)
  appendSummaryItem(items, 'status', summary.status)
  appendSummaryItem(items, 'account', summary.account_id || fallbackAccountId || null)
  appendSummaryItem(items, 'at', formatTimestamp(summary.compared_at || summary.date || null))
  appendSummaryItem(items, 'message', summary.message || null)
  return items
}

export default function PaperTradingPage() {
  const { showToast } = useToast()
  // ----- State -----
  const [deployments, setDeployments] = useState<DeploymentSummary[]>([])
  const [deploymentFilter, setDeploymentFilter] = useState<'all' | 'qmt_candidate' | 'qmt_blocked'>('all')
  const [listLoading, setListLoading] = useState(true)  // show spinner on first load
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<DeploymentDetail | null>(null)
  const [healthMap, setHealthMap] = useState<Record<string, DeploymentHealth>>({})
  const [alerts, setAlerts] = useState<DashboardAlert[]>([])
  const [snapshots, setSnapshots] = useState<SnapshotRecord[]>([])
  const [trades, setTrades] = useState<TradeEntry[]>([])
  const [brokerState, setBrokerState] = useState<BrokerStateResponse | null>(null)
  const [brokerOrders, setBrokerOrders] = useState<BrokerOrderLink[]>([])
  const [brokerOrdersAccountId, setBrokerOrdersAccountId] = useState<string | null>(null)
  // V3.3.27 Fix-A Issue #5: per-panel sync errors so broker-state /
  // broker-orders failures surface as a small red retry bar rather than
  // leaving the UI at "no data".
  const [syncErrors, setSyncErrors] = useState<SyncErrors>({})
  const [loading, setLoading] = useState(false)
  const [tickDate, setTickDate] = useState('')
  const [tickLoading, setTickLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)
  const [brokerSyncLoading, setBrokerSyncLoading] = useState(false)

  // Race token refs
  const listTokenRef = useRef(0)
  const detailTokenRef = useRef(0)

  // ----- Fetch deployment list + dashboard -----
  const refreshList = useCallback(async (options?: { surfaceErrors?: boolean }) => {
    const token = ++listTokenRef.current
    let failure: unknown = null
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
    } catch (e) {
      failure = e
      // silent — dashboard auto-refreshes
    } finally {
      if (listTokenRef.current === token) setListLoading(false)
    }
    if (failure && options?.surfaceErrors) throw failure
  }, [])

  useEffect(() => {
    refreshList()
    const timer = setInterval(refreshList, 15000) // poll every 15s
    return () => clearInterval(timer)
  }, [refreshList])

  // ----- Fetch detail when selected -----
  const refreshDetail = useCallback(async (id: string, options?: { surfaceErrors?: boolean }) => {
    const token = ++detailTokenRef.current
    setLoading(true)
    let failure: unknown = null
    try {
      const results = await Promise.allSettled([
        getDeployment(id),
        getSnapshots(id),
        getTrades(id),
        getBrokerState(id),
        getBrokerOrders(id),
      ])
      if (detailTokenRef.current !== token) return
      const [detailRes, snapRes, tradeRes, brokerStateRes, brokerOrdersRes] = results
      // V3.3.27 Fix-A Issue #5: record per-panel success/failure so the
      // UI can render a visible retry bar for the failing sub-request
      // while continuing to render the rest of the detail (Promise.allSettled
      // semantics preserved).
      const nextSyncErrors: SyncErrors = {}
      setDetail(detailRes.status === 'fulfilled' ? detailRes.value.data : null)
      if (detailRes.status === 'rejected') {
        nextSyncErrors.detail = extractErrorMessage(detailRes.reason)
      }
      setSnapshots(snapRes.status === 'fulfilled' ? snapRes.value.data : [])
      if (snapRes.status === 'rejected') {
        nextSyncErrors.snapshots = extractErrorMessage(snapRes.reason)
      }
      setTrades(tradeRes.status === 'fulfilled' ? tradeRes.value.data : [])
      if (tradeRes.status === 'rejected') {
        nextSyncErrors.trades = extractErrorMessage(tradeRes.reason)
      }
      setBrokerState(brokerStateRes.status === 'fulfilled' ? brokerStateRes.value.data : null)
      if (brokerStateRes.status === 'rejected') {
        nextSyncErrors.broker_state = extractErrorMessage(brokerStateRes.reason)
      }
      // V3.3.27 Fix-A Issue #2: broker-orders now returns a wrapped object
      // ({target_account_id, orders}). Unwrap defensively — pre-fix tests
      // and older callers may still pass an array directly, so accept both.
      if (brokerOrdersRes.status === 'fulfilled') {
        const payload = brokerOrdersRes.value.data as
          | BrokerOrderLink[]
          | { target_account_id?: string | null; orders?: BrokerOrderLink[] }
          | null
        if (Array.isArray(payload)) {
          setBrokerOrders(payload)
          setBrokerOrdersAccountId(null)
        } else if (payload && typeof payload === 'object') {
          setBrokerOrders(Array.isArray(payload.orders) ? payload.orders : [])
          setBrokerOrdersAccountId(payload.target_account_id ?? null)
        } else {
          setBrokerOrders([])
          setBrokerOrdersAccountId(null)
        }
      } else {
        setBrokerOrders([])
        setBrokerOrdersAccountId(null)
        nextSyncErrors.broker_orders = extractErrorMessage(brokerOrdersRes.reason)
      }
      setSyncErrors(nextSyncErrors)
      if (detailRes.status === 'rejected') {
        failure = detailRes.reason
      }
    } catch (e) {
      failure = e
    } finally {
      if (detailTokenRef.current === token) setLoading(false)
    }
    if (failure && options?.surfaceErrors) throw failure
  }, [])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      setSnapshots([])
      setTrades([])
      setBrokerState(null)
      setBrokerOrders([])
      setBrokerOrdersAccountId(null)
      setSyncErrors({})
      return
    }
    refreshDetail(selectedId)
  }, [selectedId, refreshDetail])

  // ----- Action handlers -----
  const handleAction = async (action: () => Promise<unknown>) => {
    setActionLoading(true)
    try {
      await action()
      await refreshList({ surfaceErrors: true })
      if (selectedId) await refreshDetail(selectedId, { surfaceErrors: true })
      showToast('success', '操作成功')
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: unknown } }; message?: string }
      const detail = err?.response?.data?.detail
      const msg = extractActionErrorMessage(detail) || err?.message || '操作失败'
      showToast('error', msg)
    } finally {
      setActionLoading(false)
    }
  }

  const handleTick = async () => {
    if (!tickDate) {
      showToast('warning', '请选择交易日期')
      return
    }
    setTickLoading(true)
    try {
      await triggerTick(tickDate)
      await refreshList()
      if (selectedId) await refreshDetail(selectedId)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      showToast('error', 'Tick 失败: ' + (err?.response?.data?.detail || err?.message || ''))
    } finally {
      setTickLoading(false)
    }
  }

  const handleBrokerSync = async () => {
    if (!selectedId) return
    setBrokerSyncLoading(true)
    try {
      await syncBrokerState(selectedId)
      await refreshList({ surfaceErrors: true })
      await refreshDetail(selectedId, { surfaceErrors: true })
      showToast('success', 'Broker 状态已同步')
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      showToast('error', 'Broker 同步失败: ' + (err?.response?.data?.detail || err?.message || ''))
    } finally {
      setBrokerSyncLoading(false)
    }
  }

  // V3.3.27 Fix-A Issue #5: simple retry handler — re-runs refreshDetail,
  // which will re-populate / clear the relevant syncErrors entries.
  const handleRetrySync = async () => {
    if (!selectedId) return
    await refreshDetail(selectedId)
  }

  // ----- Derived data -----
  const health = selectedId ? healthMap[selectedId] : null
  const qmtReadiness: QMTReadiness | null = brokerState?.qmt_readiness || null
  const qmtHardGate: QMTHardGate | null = brokerState?.latest_qmt_hard_gate || null
  const qmtSubmitGate: QMTSubmitGate | null = brokerState?.qmt_submit_gate || null
  const runtimeReleaseGate: RuntimeQMTReleaseGate | null = brokerState?.qmt_release_gate?.source === 'runtime'
    ? brokerState.qmt_release_gate
    : null
  const previewReleaseGate: PreviewQMTReleaseGate | null = detail?.qmt_release_gate?.source === 'preview'
    ? detail.qmt_release_gate
    : null
  const recentRuntimeEvents = brokerState?.recent_runtime_events || []
  const runtimeProjectionSource = brokerState?.projection_source || health?.qmt_projection_source || null
  const runtimeProjectionTs = brokerState?.projection_ts || health?.qmt_projection_ts || null
  // V3.3.27 Fix-A Issue #2: prefer broker-state's explicit target_account_id,
  // then broker-orders' explicit target_account_id (now a first-class
  // field), then fall back to the older triangulation paths.
  const targetAccountId = brokerState?.target_account_id
    || brokerOrdersAccountId
    || qmtSubmitGate?.account_id
    || health?.qmt_target_account_id
    || null
  const accountReconcile = brokerState?.latest_reconcile || null
  const orderReconcile = brokerState?.latest_order_reconcile || null
  const accountReconcileStatus = accountReconcile?.status || health?.broker_reconcile_status || null
  const accountReconcileSource = accountReconcile ? 'runtime' : (health?.broker_reconcile_status ? 'dashboard' : null)
  const orderReconcileStatus = orderReconcile?.status || health?.broker_order_reconcile_status || null
  const orderReconcileSource = orderReconcile ? 'runtime' : (health?.broker_order_reconcile_status ? 'dashboard' : null)
  const qmtHardGateStatus = (qmtHardGate?.status || health?.qmt_hard_gate_status || null)
  const qmtHardGateSource = qmtHardGate ? 'runtime' : (health?.qmt_hard_gate_status ? 'dashboard' : null)
  const projectionContext = [
    runtimeProjectionSource ? `projection:${runtimeProjectionSource}` : '',
    runtimeProjectionTs ? `updated:${formatTimestamp(runtimeProjectionTs)}` : '',
    targetAccountId ? `account:${targetAccountId}` : '',
  ].filter(Boolean)
  const accountReconcileContext = summarizeRiskSummary(accountReconcile, targetAccountId)
  const orderReconcileContext = summarizeRiskSummary(orderReconcile, targetAccountId)
  const hardGateContext = summarizeRiskSummary(qmtHardGate, targetAccountId)
  const isQmtRelated = Boolean(
    detail?.spec?.broker_type === 'qmt'
    || detail?.spec?.shadow_broker_type === 'qmt'
    || brokerState?.qmt_readiness
    || brokerState?.qmt_submit_gate
    || brokerState?.qmt_release_gate,
  )
  const releaseSummaries = deployments.reduce<Record<string, ReleaseSummary>>((acc, deployment) => {
    acc[deployment.deployment_id] = resolveReleaseSummary(deployment, healthMap[deployment.deployment_id])
    return acc
  }, {})
  const filteredDeployments = deployments.filter((deployment) => {
    const releaseSummary = releaseSummaries[deployment.deployment_id]
    if (deploymentFilter === 'qmt_candidate') {
      return releaseSummary.candidate
    }
    if (deploymentFilter === 'qmt_blocked') {
      return Boolean(
        releaseSummary.status === 'blocked'
        || releaseSummary.blockers.length > 0,
      )
    }
    return true
  })
  const qmtCandidateCount = deployments.filter((deployment) => releaseSummaries[deployment.deployment_id]?.candidate).length
  const qmtBlockedCount = deployments.filter((deployment) => {
    const releaseSummary = releaseSummaries[deployment.deployment_id]
    return Boolean(
      releaseSummary?.status === 'blocked'
      || releaseSummary?.blockers.length,
    )
  }).length
  const runtimeStages = [
    { label: 'Readiness', value: qmtReadiness?.status, color: gateTone(qmtReadiness?.status), source: qmtReadiness ? 'runtime' : null },
    { label: 'Submit Gate', value: qmtSubmitGate?.status, color: gateTone(qmtSubmitGate?.status), source: qmtSubmitGate?.source || null },
    { label: 'Release Gate', value: runtimeReleaseGate?.status, color: gateTone(runtimeReleaseGate?.status), source: runtimeReleaseGate?.source || null },
  ]

  // Keep the selected deployment aligned with the active list filter.
  useEffect(() => {
    if (filteredDeployments.length === 0) {
      if (deploymentFilter !== 'all') setSelectedId(null)
      return
    }
    if (!selectedId || !filteredDeployments.some((deployment) => deployment.deployment_id === selectedId)) {
      setSelectedId(filteredDeployments[0].deployment_id)
    }
  }, [deploymentFilter, filteredDeployments, selectedId])

  // Equity curve from snapshots
  const equityDates = snapshots.map(s => s.snapshot_date)
  const equityCurve = snapshots.map(s => s.equity)

  // Holdings pie from latest snapshot
  const latestSnap = snapshots.length > 0 ? snapshots[snapshots.length - 1] : null
  const positions = (latestSnap?.holdings || {}) as Record<string, { shares: number; market_value: number }>
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
      if (typeof evt === 'string') {
        riskEvents.push({ date: snap.snapshot_date, event: evt })
      } else if (evt && typeof evt === 'object') {
        const eventName = typeof evt.event === 'string' ? evt.event : 'risk_event'
        const status = typeof evt.status === 'string' ? ` (${evt.status})` : ''
        riskEvents.push({ date: snap.snapshot_date, event: `${eventName}${status}` })
      }
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
    backgroundColor: CHART.bg,
    title: { text: '净值曲线', textStyle: { color: CHART.text, fontSize: 12 }, left: 'center' },
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
      axisLabel: { color: CHART.textSecondary, rotate: 30, fontSize: 9 },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: CHART.grid } },
      axisLabel: { color: CHART.textSecondary },
    },
    series: [{
      type: 'line' as const,
      data: equityCurve,
      lineStyle: { color: CHART.accent },
      areaStyle: { color: 'rgba(37, 99, 235, 0.1)' },
      showSymbol: false,
    }],
  } : null

  const pieOption = pieData.length > 0 ? {
    backgroundColor: CHART.bg,
    title: { text: '持仓分布', textStyle: { color: CHART.text, fontSize: 12 }, left: 'center' },
    tooltip: {
      trigger: 'item' as const,
      formatter: '{b}: {c} ({d}%)',
    },
    series: [{
      type: 'pie' as const,
      radius: ['35%', '60%'],
      data: pieData,
      label: { color: CHART.textSecondary, fontSize: 10 },
      itemStyle: { borderColor: CHART.bg, borderWidth: 2 },
    }],
  } : null

  // ----- Render -----
  return (
    <div className="flex h-[calc(100vh-48px)]">
      {/* Left sidebar: deployment list */}
      <div className="w-64 md:w-72 shrink-0 overflow-y-auto border-r" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
        <div className="p-3 mb-3 border-b" style={{ borderColor: 'var(--border)' }}>
          <h2 className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>模拟盘部署</h2>
          <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
            {deployments.length} 个部署
            {alerts.length > 0 && <span style={{ color: '#ef4444' }}> | {alerts.length} 个告警</span>}
          </p>
          <div className="flex flex-wrap gap-2 mt-3">
            {[
              { id: 'all' as const, label: `全部 (${deployments.length})` },
              { id: 'qmt_candidate' as const, label: `QMT 候选 (${qmtCandidateCount})` },
              { id: 'qmt_blocked' as const, label: `QMT 受阻 (${qmtBlockedCount})` },
            ].map((item) => (
              <button
                key={item.id}
                onClick={() => setDeploymentFilter(item.id)}
                className="px-2 py-1 rounded text-[11px]"
                style={{
                  border: `1px solid ${deploymentFilter === item.id ? 'var(--color-accent)' : 'var(--border)'}`,
                  backgroundColor: deploymentFilter === item.id ? 'rgba(37, 99, 235, 0.12)' : 'var(--bg-primary)',
                  color: deploymentFilter === item.id ? 'var(--color-accent)' : 'var(--text-secondary)',
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        {listLoading && deployments.length === 0 && (
          <div className="p-4 text-center text-xs" style={{ color: 'var(--text-secondary)' }}>
            加载中...
          </div>
        )}
        {!listLoading && deployments.length === 0 && (
          <div className="p-4 text-center text-xs" style={{ color: 'var(--text-secondary)' }}>
            暂无部署。请先在组合回测中运行策略，然后点击 "部署到模拟盘"。
          </div>
        )}
        {!listLoading && deployments.length > 0 && filteredDeployments.length === 0 && (
          <div className="p-4 text-center text-xs" style={{ color: 'var(--text-secondary)' }}>
            当前过滤条件下没有部署。
          </div>
        )}
        {filteredDeployments.map(d => {
          const h = healthMap[d.deployment_id]
          const isSelected = d.deployment_id === selectedId
          const hasAlert = alerts.some(a => a.deployment_id === d.deployment_id)
          const releaseSummary = releaseSummaries[d.deployment_id]
          const releaseStatus = releaseSummary?.status
          const releaseBlockers = releaseSummary?.blockers || []
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
                <span className="text-sm font-medium truncate" style={{ color: 'var(--text-primary)', maxWidth: '160px' }} title={d.name}>
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
                  <span style={{ color: (h.cumulative_return || 0) >= 0 ? CHART.down : CHART.up }}>
                    {fmt(h.cumulative_return, true)}
                  </span>
                  <span>今日 {fmt(h.today_pnl)}</span>
                </div>
              )}
              {(releaseStatus || releaseBlockers.length > 0) && (
                <div className="mt-1.5 flex items-center gap-2 text-[11px]">
                  <span
                    className="px-1.5 py-0.5 rounded"
                    style={{
                      backgroundColor: `${gateTone(releaseStatus || 'blocked')}20`,
                      color: gateTone(releaseStatus || 'blocked'),
                      border: `1px solid ${gateTone(releaseStatus || 'blocked')}40`,
                    }}
                  >
                    {fmtGateLabel(releaseStatus || 'blocked')}
                  </span>
                  {releaseBlockers.length > 0 && (
                    <span style={{ color: 'var(--text-secondary)' }}>
                      {releaseBlockers.length} blocker
                    </span>
                  )}
                  {releaseSummary?.source && (
                    <SourceBadge label={releaseSummary.source} />
                  )}
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
                  <span>Broker: {detail.spec?.broker_type || 'paper'}</span>
                  {detail.spec?.shadow_broker_type && (
                    <span>Shadow: {detail.spec.shadow_broker_type}</span>
                  )}
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
                      const liq = confirm('停止前是否清仓所有持仓？\n\n确定 = 清仓后停止\n取消 = 直接停止（保留持仓记录）')
                      handleAction(() => stopDeployment(detail.deployment_id, '手动停止', liq))
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
                      <span key={i} className="text-xs" style={{ color: r.passed ? CHART.textSecondary : CHART.error }}>
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

            {/* V3.3.27 Fix-A Issue #5: render sync error bars so transient
                broker-state / broker-orders failures are visible instead of
                showing "no data". Placement is before QMT panel so both
                QMT and non-QMT deployments can surface retry hints. */}
            {syncErrors.broker_state && (
              <SyncErrorBar
                testId="sync-error-broker-state"
                label="broker-state"
                message={syncErrors.broker_state}
                onRetry={handleRetrySync}
              />
            )}
            {syncErrors.broker_orders && (
              <SyncErrorBar
                testId="sync-error-broker-orders"
                label="broker-orders"
                message={syncErrors.broker_orders}
                onRetry={handleRetrySync}
              />
            )}
            {syncErrors.snapshots && (
              <SyncErrorBar
                testId="sync-error-snapshots"
                label="snapshots"
                message={syncErrors.snapshots}
                onRetry={handleRetrySync}
              />
            )}
            {syncErrors.trades && (
              <SyncErrorBar
                testId="sync-error-trades"
                label="trades"
                message={syncErrors.trades}
                onRetry={handleRetrySync}
              />
            )}

            {isQmtRelated && (
              <div data-testid="qmt-runtime-panel" className="rounded p-3 space-y-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div>
                    <h4 className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>Broker 运行态</h4>
                    <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                      QMT runtime / gate / callback / reconcile 视图
                    </p>
                  </div>
                  <button
                    onClick={handleBrokerSync}
                    disabled={brokerSyncLoading || !selectedId}
                    className="px-3 py-1.5 rounded text-xs font-medium text-white"
                    style={{ backgroundColor: brokerSyncLoading ? '#30363d' : '#2563eb' }}
                  >
                    {brokerSyncLoading ? '同步中...' : '同步 Broker'}
                  </button>
                </div>

                <div className="grid grid-cols-4 gap-3">
                  <MetricCard
                    testId="runtime-readiness-card"
                    label="QMT Readiness"
                    value={fmtGateLabel(qmtReadiness?.status)}
                    customColor={gateTone(qmtReadiness?.status)}
                    sourceLabel={qmtReadiness ? 'runtime' : null}
                  />
                  <MetricCard
                    testId="runtime-submit-gate-card"
                    label="Submit Gate"
                    value={fmtGateLabel(qmtSubmitGate?.status)}
                    customColor={gateTone(qmtSubmitGate?.status)}
                    sourceLabel={qmtSubmitGate?.source || null}
                  />
                  <MetricCard
                    testId="runtime-release-gate-card"
                    label="Release Gate"
                    value={fmtGateLabel(runtimeReleaseGate?.status)}
                    customColor={gateTone(runtimeReleaseGate?.status)}
                    sourceLabel={runtimeReleaseGate?.source || null}
                  />
                  <MetricCard
                    label="Callback Mode"
                    value={fmtGateLabel(brokerState?.latest_callback_account_mode)}
                    customColor={gateTone(brokerState?.latest_callback_account_mode)}
                    sourceLabel={brokerState ? 'runtime' : null}
                  />
                  <MetricCard
                    label="Asset Freshness"
                    value={fmtGateLabel(brokerState?.latest_callback_account_freshness)}
                    customColor={gateTone(brokerState?.latest_callback_account_freshness)}
                    sourceLabel={brokerState ? 'runtime' : null}
                  />
                  <MetricCard
                    label="Account Reconcile"
                    value={fmtGateLabel(accountReconcileStatus)}
                    customColor={gateTone(accountReconcileStatus)}
                    sourceLabel={accountReconcileSource}
                  />
                  <MetricCard
                    label="Order Reconcile"
                    value={fmtGateLabel(orderReconcileStatus)}
                    customColor={gateTone(orderReconcileStatus)}
                    sourceLabel={orderReconcileSource}
                  />
                  <MetricCard
                    testId="runtime-hard-gate-card"
                    label="Reconcile Hard Gate"
                    value={fmtGateLabel(qmtHardGateStatus)}
                    customColor={gateTone(qmtHardGateStatus)}
                    sourceLabel={qmtHardGateSource}
                  />
                </div>

                <div className="rounded p-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-primary)' }}>
                  <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
                    <div>
                      <h5 className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>QMT Runtime Workflow</h5>
                      <p className="text-[11px] mt-1" style={{ color: 'var(--text-secondary)' }}>
                        这里只展示 runtime truth，不回填审批态 preview gate。
                      </p>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    {runtimeStages.map((stage) => (
                      <div
                        key={stage.label}
                        className="rounded p-3"
                        style={{ border: `1px solid ${stage.color}40`, backgroundColor: `${stage.color}12` }}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>{stage.label}</div>
                          {stage.source && <SourceBadge label={stage.source} />}
                        </div>
                        <div className="text-sm font-medium mt-1" style={{ color: stage.color }}>{fmtGateLabel(stage.value)}</div>
                      </div>
                    ))}
                  </div>
                  <div className="grid grid-cols-2 gap-3 mt-3">
                    <BrokerInfoBlock
                      title="Runtime Release Candidate"
                      items={runtimeReleaseGate ? [
                        runtimeReleaseGate.eligible_for_release_candidate ? 'eligible_for_release_candidate' : 'not_release_candidate',
                        runtimeReleaseGate.eligible_for_real_submit ? 'eligible_for_real_submit' : 'not_real_submit_ready',
                      ] : []}
                      emptyText="暂无 runtime release candidate 状态"
                      sourceLabel={runtimeReleaseGate?.source || null}
                    />
                    <BrokerInfoBlock
                      title="Runtime Release Context"
                      items={runtimeReleaseGate ? [
                        runtimeReleaseGate.deployment_status ? `deployment:${runtimeReleaseGate.deployment_status}` : '',
                        runtimeReleaseGate.submit_gate_status ? `submit:${runtimeReleaseGate.submit_gate_status}` : '',
                        runtimeReleaseGate.deploy_gate_passed == null ? '' : (runtimeReleaseGate.deploy_gate_passed ? 'deploy_gate:passed' : 'deploy_gate:blocked'),
                      ].filter(Boolean) : []}
                      emptyText="暂无 runtime release context"
                      sourceLabel={runtimeReleaseGate?.source || null}
                    />
                  </div>
                </div>

                <div className="grid grid-cols-4 gap-4">
                  <BrokerInfoBlock
                    title="Projection Context"
                    items={projectionContext}
                    emptyText="暂无 projection context"
                    sourceLabel={runtimeProjectionSource}
                  />
                  <BrokerInfoBlock
                    title="Account Reconcile Context"
                    items={accountReconcileContext}
                    emptyText="暂无 account reconcile context"
                    sourceLabel={accountReconcileSource}
                  />
                  <BrokerInfoBlock
                    title="Order Reconcile Context"
                    items={orderReconcileContext}
                    emptyText="暂无 order reconcile context"
                    sourceLabel={orderReconcileSource}
                  />
                  <BrokerInfoBlock
                    title="Hard Gate Context"
                    items={hardGateContext}
                    emptyText="暂无 hard gate context"
                    sourceLabel={qmtHardGateSource}
                  />
                </div>

                <div className="grid grid-cols-4 gap-4">
                  <BrokerInfoBlock
                    title="Readiness Blockers"
                    items={qmtReadiness?.blockers || []}
                    emptyText="无 blocker"
                    sourceLabel={qmtReadiness ? 'runtime' : null}
                  />
                  <BrokerInfoBlock
                    title="Submit Gate Blockers"
                    items={qmtSubmitGate?.blockers || []}
                    emptyText="无 blocker"
                    sourceLabel={qmtSubmitGate?.source || null}
                  />
                  <BrokerInfoBlock
                    title="Release Gate Blockers"
                    items={runtimeReleaseGate?.blockers || []}
                    emptyText="无 blocker"
                    sourceLabel={runtimeReleaseGate?.source || null}
                  />
                  <BrokerInfoBlock
                    title="Hard Gate Blockers"
                    items={qmtHardGate?.blockers || health?.qmt_hard_gate_blockers || []}
                    emptyText="无 blocker"
                    sourceLabel={qmtHardGateSource}
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="rounded p-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-primary)' }}>
                    <h5 className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>Recent Runtime Events</h5>
                    {recentRuntimeEvents.length === 0 ? (
                      <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>暂无 runtime 事件</p>
                    ) : (
                      <div className="space-y-2 max-h-56 overflow-y-auto">
                        {recentRuntimeEvents.map((event) => {
                          const runtimeKind = String(event.payload?.runtime_kind || '-')
                          const runtimePayload = event.payload?.payload
                          const status = typeof runtimePayload === 'object' && runtimePayload
                            ? String((runtimePayload as Record<string, unknown>).status || (runtimePayload as Record<string, unknown>).consumer_status || '')
                            : ''
                          return (
                            <div key={event.event_id} className="text-xs rounded px-2 py-1.5" style={{ backgroundColor: 'var(--bg-secondary)' }}>
                              <div className="flex items-center justify-between gap-2">
                                <span style={{ color: gateTone(status || runtimeKind) }}>{runtimeKind}</span>
                                <span style={{ color: 'var(--text-secondary)' }}>{event.event_ts.slice(0, 19).replace('T', ' ')}</span>
                              </div>
                              {status && (
                                <div style={{ color: 'var(--text-secondary)' }}>status: {status}</div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>

                  <div className="rounded p-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-primary)' }}>
                    <h5 className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>Broker Orders ({brokerOrders.length})</h5>
                    {brokerOrders.length === 0 ? (
                      <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>暂无 broker order link</p>
                    ) : (
                      <div className="overflow-y-auto max-h-56">
                        <table className="w-full text-xs" style={{ color: 'var(--text-primary)' }}>
                          <thead>
                            <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>
                              <th className="text-left py-1">Symbol</th>
                              <th className="text-left py-1">Broker ID</th>
                              <th className="text-left py-1">状态</th>
                              <th className="text-right py-1">动作</th>
                            </tr>
                          </thead>
                          <tbody>
                            {brokerOrders.map((order) => {
                              const canCancel = (
                                detail.status === 'running' || detail.status === 'paused'
                              ) && order.broker_type === 'qmt'
                                && !TERMINAL_BROKER_ORDER_STATUSES.has(order.latest_status)
                              return (
                                <tr key={order.client_order_id} style={{ borderBottom: '1px solid var(--border)' }}>
                                  <td className="py-1">{order.symbol || '-'}</td>
                                  <td className="py-1">{order.broker_order_id || '-'}</td>
                                  <td className="py-1" style={{ color: gateTone(order.latest_status) }}>{fmtGateLabel(order.latest_status)}</td>
                                  <td className="text-right py-1">
                                    {canCancel ? (
                                      <button
                                        onClick={() => handleAction(() => cancelBrokerOrder(detail.deployment_id, { broker_order_id: order.broker_order_id }))}
                                        disabled={actionLoading}
                                        className="px-2 py-1 rounded text-[11px] text-white"
                                        style={{ backgroundColor: actionLoading ? '#30363d' : '#ef4444' }}
                                      >
                                        撤单
                                      </button>
                                    ) : (
                                      <span style={{ color: 'var(--text-secondary)' }}>-</span>
                                    )}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {previewReleaseGate && (
              <div data-testid="qmt-preview-panel" className="rounded p-3 space-y-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div>
                    <h4 className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>Release Preview</h4>
                    <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                      审批态 release candidate 预览，不代表 broker runtime。
                    </p>
                  </div>
                  <SourceBadge label={previewReleaseGate.source || 'preview'} />
                </div>

                <div className="grid grid-cols-3 gap-3">
                  <MetricCard
                    testId="preview-release-gate-card"
                    label="Preview Release Gate"
                    value={fmtGateLabel(previewReleaseGate.status)}
                    customColor={gateTone(previewReleaseGate.status)}
                    sourceLabel={previewReleaseGate.source || 'preview'}
                  />
                  <MetricCard
                    label="Deployment Status"
                    value={fmtGateLabel(previewReleaseGate.deployment_status)}
                    customColor={gateTone(previewReleaseGate.deployment_status)}
                    sourceLabel={previewReleaseGate.source || 'preview'}
                  />
                  <MetricCard
                    label="Deploy Gate"
                    value={previewReleaseGate.deploy_gate_passed == null ? '-' : (previewReleaseGate.deploy_gate_passed ? 'passed' : 'blocked')}
                    customColor={previewReleaseGate.deploy_gate_passed == null ? undefined : gateTone(previewReleaseGate.deploy_gate_passed ? 'ok' : 'blocked')}
                    sourceLabel={previewReleaseGate.source || 'preview'}
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <BrokerInfoBlock
                    title="Preview Candidate"
                    items={[
                      previewReleaseGate.eligible_for_release_candidate ? 'eligible_for_release_candidate' : 'not_release_candidate',
                      previewReleaseGate.eligible_for_real_submit ? 'eligible_for_real_submit' : 'not_real_submit_ready',
                    ]}
                    emptyText="暂无 preview candidate 状态"
                    sourceLabel={previewReleaseGate.source || 'preview'}
                  />
                  <BrokerInfoBlock
                    title="Preview Context"
                    items={[
                      previewReleaseGate.submit_gate_status ? `submit:${previewReleaseGate.submit_gate_status}` : '',
                      previewReleaseGate.deploy_gate_passed == null ? '' : (previewReleaseGate.deploy_gate_passed ? 'deploy_gate:passed' : 'deploy_gate:blocked'),
                      previewReleaseGate.submit_gate_preflight_ok == null ? '' : (previewReleaseGate.submit_gate_preflight_ok ? 'preflight:ok' : 'preflight:blocked'),
                    ].filter(Boolean)}
                    emptyText="暂无 preview context"
                    sourceLabel={previewReleaseGate.source || 'preview'}
                  />
                </div>

                <BrokerInfoBlock
                  title="Preview Blockers"
                  items={previewReleaseGate.blockers || []}
                  emptyText="无 blocker"
                  sourceLabel={previewReleaseGate.source || 'preview'}
                />
              </div>
            )}

            {/* Metric cards */}
            {health && (
              <div className="grid grid-cols-4 gap-3">
                <MetricCard label="累计收益" value={fmt(health.cumulative_return, true)} positive={health.cumulative_return >= 0} />
                <MetricCard label="夏普比率" value={fmt(health.sharpe_ratio)} />
                <MetricCard label="最大回撤" value={fmt(health.max_drawdown, true)} negative />
                <MetricCard label="今日盈亏" value={fmt(health.today_pnl)} positive={health.today_pnl >= 0} />
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
                  <ReactECharts option={pieOption} style={{ height: 300 }} />
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
                          <tr key={i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: i % 2 === 0 ? 'rgba(255,255,255,0.04)' : 'transparent' }}>
                            <td className="py-1">{t.symbol}</td>
                            <td className="py-1" style={{ color: t.side === 'buy' ? CHART.up : CHART.down }}>
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
                        <tr key={i} style={{ borderBottom: '1px solid var(--border)', backgroundColor: i % 2 === 0 ? 'rgba(255,255,255,0.04)' : 'transparent' }}>
                          <td className="py-1">{t.snapshot_date}</td>
                          <td className="py-1">{t.symbol}</td>
                          <td className="py-1" style={{ color: t.side === 'buy' ? CHART.up : CHART.down }}>
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
                  className="px-3 py-1.5 rounded text-xs"
                  style={inputStyle}
                />
                <button
                  onClick={handleTick}
                  disabled={tickLoading || !tickDate}
                  className="px-3 py-1.5 rounded text-xs font-medium text-white"
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

function MetricCard({ label, value, positive, negative, warn, customColor, sourceLabel, testId }: {
  label: string
  value: string
  positive?: boolean
  negative?: boolean
  warn?: boolean
  customColor?: string
  sourceLabel?: string | null
  testId?: string
}) {
  let valueColor = 'var(--text-primary)'
  if (positive === true) valueColor = CHART.down
  else if (positive === false) valueColor = CHART.up
  if (negative) valueColor = CHART.up
  if (warn) valueColor = CHART.warn
  if (customColor) valueColor = customColor

  return (
    <div data-testid={testId} className="p-3 rounded" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</div>
        {sourceLabel && <SourceBadge label={sourceLabel} />}
      </div>
      <div className="text-lg font-medium" style={{ color: valueColor }}>{value}</div>
    </div>
  )
}

function SourceBadge({ label }: { label: string }) {
  // V3.3.27 Fix-A Issue #6: runtime badges are rendered in green and
  // preview badges in gray/amber so operators can visually distinguish
  // runtime truth from audit-time preview at a glance.
  const normalized = (label || '').toLowerCase()
  let fg = 'var(--text-secondary)'
  let bg = 'var(--bg-primary)'
  let border = 'var(--border)'
  if (normalized === 'runtime') {
    fg = '#22c55e'
    bg = 'rgba(34,197,94,0.12)'
    border = 'rgba(34,197,94,0.35)'
  } else if (normalized === 'preview') {
    fg = '#9ca3af'
    bg = 'rgba(156,163,175,0.12)'
    border = 'rgba(156,163,175,0.35)'
  }
  return (
    <span
      className="px-1.5 py-0.5 rounded text-[10px]"
      style={{
        backgroundColor: bg,
        color: fg,
        border: `1px solid ${border}`,
      }}
    >
      {label}
    </span>
  )
}

// V3.3.27 Fix-A Issue #5: small inline red bar surfacing a failed
// sub-request. Placed next to the affected panel so the operator can
// see "broker-state sync failed, click to retry" without relying on
// ephemeral toasts.
function SyncErrorBar({
  label, message, onRetry, testId,
}: {
  label: string
  message: string
  onRetry: () => void | Promise<void>
  testId?: string
}) {
  return (
    <div
      data-testid={testId}
      className="p-2 rounded flex items-center justify-between gap-2"
      style={{
        backgroundColor: 'rgba(239, 68, 68, 0.1)',
        border: '1px solid rgba(239, 68, 68, 0.4)',
      }}
    >
      <div className="text-xs" style={{ color: '#ef4444' }}>
        <span className="mr-1">⚠️</span>
        {label} 同步失败: {message}
      </div>
      <button
        onClick={() => { void onRetry() }}
        className="px-2 py-0.5 rounded text-[11px]"
        style={{
          backgroundColor: 'rgba(239, 68, 68, 0.2)',
          color: '#ef4444',
          border: '1px solid rgba(239, 68, 68, 0.4)',
        }}
      >
        重试
      </button>
    </div>
  )
}

function BrokerInfoBlock({ title, items, emptyText, sourceLabel }: {
  title: string
  items: string[]
  emptyText: string
  sourceLabel?: string | null
}) {
  return (
    <div className="rounded p-3" style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-primary)' }}>
      <div className="flex items-center justify-between gap-2 mb-2">
        <h5 className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{title}</h5>
        {sourceLabel && <SourceBadge label={sourceLabel} />}
      </div>
      {items.length === 0 ? (
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>{emptyText}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <span
              key={item}
              className="px-2 py-1 rounded text-[11px]"
              style={{ backgroundColor: '#f59e0b20', color: '#f59e0b', border: '1px solid #f59e0b40' }}
            >
              {fmtGateLabel(item)}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
