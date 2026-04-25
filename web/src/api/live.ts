/**
 * V3.3.26: Live broker + release workflow API client.
 *
 * The live UI now consumes broker-state, broker-order, readiness, submit-gate,
 * and release-gate surfaces instead of staying at the old V2.15 paper-dashboard
 * layer.
 */
import axios from 'axios'

const api = axios.create({ baseURL: '/api/live' })

/** 模拟盘部署摘要（列表页每行数据） */
export interface DeploymentSummary {
  deployment_id: string
  /** 内容寻址哈希，折叠 broker_type / shadow_broker_type（V3.2 硬化） */
  spec_id: string
  name: string
  /** 部署状态：pending / approved / running / paused / stopped / error */
  status: string
  stop_reason: string | null
  /** 来源回测 run_id */
  source_run_id: string | null
  code_commit: string | null
  /** 部署门控综合判定（pass / warn / fail） */
  gate_verdict: string | null
  created_at: string | null
  approved_at: string | null
  started_at: string | null
  stopped_at: string | null
  /** 预览态 QMT release gate（列表页轻量展示） */
  qmt_release_gate?: PreviewQMTReleaseGate | null
}

export interface DeploymentSpecSummary {
  strategy_name: string
  symbols: string[]
  market: string
  freq: string
  broker_type?: string | null
  shadow_broker_type?: string | null
  initial_cash: number
}

export interface QMTReadiness {
  status: string
  ready_for_shadow_sync: boolean
  ready_for_real_submit: boolean
  real_submit_enabled: boolean
  account_sync_mode: string | null
  asset_callback_freshness: string | null
  consumer_status: string | null
  session_runtime_kind: string | null
  session_runtime_status: string | null
  account_reconcile_status: string | null
  order_reconcile_status: string | null
  blockers: string[]
  real_submit_blockers: string[]
}

export interface BrokerReconcileSummary {
  event?: string
  status?: string
  broker_type?: string
  account_id?: string | null
  date?: string
  compared_at?: string
  message?: string
  blockers?: string[]
  details?: Record<string, unknown>
}

export interface QMTHardGate {
  event?: string
  status: string
  broker_type?: string
  account_id?: string | null
  date?: string
  compared_at?: string
  message?: string
  blockers: string[]
  details?: Record<string, unknown>
}

export interface QMTSubmitGate {
  status: string
  can_submit_now: boolean
  mode: string
  blockers: string[]
  ready_for_shadow_sync: boolean
  ready_for_real_submit: boolean
  preflight_ok: boolean
  policy: Record<string, unknown>
  account_id: string | null
  total_asset: number | null
  initial_cash: number | null
  message?: string
  hard_gate?: QMTHardGate | null
  source?: 'preview' | 'runtime'
}

export interface QMTReleaseGate {
  status: string
  eligible_for_release_candidate: boolean
  eligible_for_real_submit: boolean
  blockers: string[]
  deployment_status: string
  deploy_gate_passed: boolean | null
  submit_gate_status: string | null
  submit_gate_preflight_ok: boolean | null
  submit_gate_can_submit_now: boolean | null
}

export type PreviewQMTReleaseGate = QMTReleaseGate & {
  source: 'preview'
}

export type RuntimeQMTReleaseGate = QMTReleaseGate & {
  source: 'runtime'
}

/** 模拟盘部署详情（详情页完整数据，扩展自 DeploymentSummary） */
export interface DeploymentDetail extends DeploymentSummary {
  /** 部署规格（策略名、标的、资金等） */
  spec: DeploymentSpecSummary | null
  /** 最新一次 tick 快照（持仓 / 权益 / 订单链路等） */
  latest_snapshot: Record<string, unknown> | null
  qmt_release_gate?: PreviewQMTReleaseGate | null
}

export interface DeploymentHealth {
  deployment_id: string
  name: string
  status: string
  cumulative_return: number
  max_drawdown: number
  sharpe_ratio: number | null
  today_pnl: number
  today_trades: number
  risk_events_today: number
  total_risk_events: number
  consecutive_loss_days: number
  last_execution_date: string | null
  last_execution_duration_ms: number
  days_since_last_trade: number
  error_count: number
  broker_reconcile_status?: string | null
  broker_order_reconcile_status?: string | null
  broker_runtime_kind?: string | null
  broker_runtime_status?: string | null
  broker_account_sync_mode?: string | null
  broker_asset_callback_freshness?: string | null
  qmt_hard_gate_status?: string | null
  qmt_hard_gate_blockers?: string[]
  qmt_release_gate_status?: string | null
  qmt_release_candidate?: boolean | null
  qmt_release_blockers?: string[]
  qmt_projection_source?: string | null
  qmt_projection_ts?: string | null
  qmt_target_account_id?: string | null
}

export interface DashboardAlert {
  deployment_id: string
  alert_type: string
  message: string
}

export interface DashboardResponse {
  deployments: DeploymentHealth[]
  alerts: DashboardAlert[]
}

export interface SnapshotRecord {
  snapshot_date: string
  equity: number
  cash: number
  holdings: Record<string, unknown>
  trades: TradeEntry[]
  risk_events: Array<string | { event?: string; status?: string; message?: string }>
}

export interface TradeEntry {
  symbol: string
  side: string
  shares: number
  price: number
  cost: number
  snapshot_date?: string
}

export interface DeployResponse {
  deployment_id: string
  spec_id: string
}

export interface TickResult {
  business_date: string
  results: Record<string, unknown>
}

export interface BrokerEvent {
  event_id: string
  deployment_id: string
  event_type: string
  event_ts: string
  client_order_id: string
  payload: Record<string, unknown>
}

export interface BrokerOrderLink {
  deployment_id: string
  broker_type: string
  client_order_id: string
  broker_order_id: string
  symbol: string
  latest_report_id: string
  latest_status: string
  last_report_ts: string | null
  // V3.3.27 Fix-A Issue #2: explicit per-link account_id so the frontend
  // stops triangulating identity through `qmt_submit_gate.account_id`.
  account_id?: string | null
  cancel_state?: string
  cancel_error_message?: string
}

export interface BrokerOrdersResponse {
  deployment_id: string
  // V3.3.27 Fix-A Issue #2: surface the resolved QMT account id at the
  // response root so UI no longer has to reach through submit_gate.
  target_account_id: string | null
  orders: BrokerOrderLink[]
}

export interface BrokerStateResponse {
  deployment_id: string
  latest_broker_account: BrokerEvent | null
  recent_runtime_events: BrokerEvent[]
  latest_session_runtime: BrokerEvent | null
  latest_session_owner_runtime: BrokerEvent | null
  latest_session_consumer_runtime: BrokerEvent | null
  latest_session_consumer_state_runtime: BrokerEvent | null
  latest_callback_account_mode: string | null
  latest_callback_account_freshness: string | null
  latest_reconcile: BrokerReconcileSummary | null
  latest_order_reconcile: BrokerReconcileSummary | null
  latest_qmt_hard_gate: QMTHardGate | null
  qmt_readiness: QMTReadiness | null
  qmt_submit_gate: QMTSubmitGate | null
  qmt_release_gate: PreviewQMTReleaseGate | RuntimeQMTReleaseGate | null
  target_account_id?: string | null
  projection_source?: string | null
  projection_ts?: string | null
}

export interface BrokerSubmitGateResponse {
  deployment_id: string
  broker_type: string
  qmt_submit_gate: QMTSubmitGate | null
  target_account_id?: string | null
  projection_source?: string | null
  projection_ts?: string | null
}

export interface ReleaseGateResponse {
  deployment_id: string
  deployment_status: string
  qmt_release_gate: PreviewQMTReleaseGate | RuntimeQMTReleaseGate | null
  target_account_id?: string | null
  projection_source?: string | null
  projection_ts?: string | null
}

export interface BrokerSyncResponse {
  deployment_id: string
  status: string
  business_date: string
  broker_type: string
  account_event_count: number
  runtime_event_count: number
  execution_report_count: number
  reconcile_status?: string | null
  order_reconcile_status?: string | null
  qmt_hard_gate_status?: string | null
  qmt_readiness?: QMTReadiness | null
  qmt_submit_gate?: QMTSubmitGate | null
  qmt_release_gate?: QMTReleaseGate | null
}

// V3.3.27 Fix-A Issue #4: explicit idempotency status. `already_canceling`
// is returned when the persisted link is already in a cancel-inflight /
// terminal cancel state (and `if_not_already` is not disabled).
export type CancelOrderStatus =
  | 'already_canceling'
  | 'cancel_requested'
  | 'canceled'
  | 'cancel_error'
  | string

export interface CancelOrderResponse {
  status: CancelOrderStatus
  broker_order_id?: string | null
  client_order_id?: string | null
  deployment_id?: string
  symbol?: string
  detail?: string
  link?: {
    client_order_id?: string
    broker_order_id?: string
    symbol?: string
    account_id?: string | null
    latest_status?: string
    last_report_ts?: string | null
  }
}

export const deployToLive = (data: { source_run_id: string; name: string }) =>
  api.post<DeployResponse>('/deploy', data)

export const listDeployments = (status?: string) =>
  api.get<DeploymentSummary[]>('/deployments', { params: status ? { status } : {} })

export const getDeployment = (id: string) =>
  api.get<DeploymentDetail>(`/deployments/${id}`)

export const approveDeployment = (id: string) =>
  api.post(`/deployments/${id}/approve`)

export const startDeployment = (id: string) =>
  api.post(`/deployments/${id}/start`)

export const stopDeployment = (id: string, reason?: string, liquidate?: boolean) =>
  api.post(`/deployments/${id}/stop`, { reason: reason || '手动停止' }, { params: liquidate ? { liquidate: 'true' } : {} })

export const pauseDeployment = (id: string) =>
  api.post(`/deployments/${id}/pause`)

export const resumeDeployment = (id: string) =>
  api.post(`/deployments/${id}/resume`)

export const triggerTick = (business_date: string) =>
  api.post<TickResult>('/tick', { business_date })

export const getDashboard = () =>
  api.get<DashboardResponse>('/dashboard')

export const getSnapshots = (id: string) =>
  api.get<SnapshotRecord[]>(`/deployments/${id}/snapshots`)

export const getTrades = (id: string) =>
  api.get<TradeEntry[]>(`/deployments/${id}/trades`)

export const getBrokerOrders = (id: string) =>
  api.get<BrokerOrdersResponse>(`/deployments/${id}/broker-orders`)

export const getBrokerState = (id: string, runtime_limit = 10) =>
  api.get<BrokerStateResponse>(`/deployments/${id}/broker-state`, { params: { runtime_limit } })

export const getBrokerSubmitGate = (id: string) =>
  api.get<BrokerSubmitGateResponse>(`/deployments/${id}/broker-submit-gate`)

export const getReleaseGate = (id: string) =>
  api.get<ReleaseGateResponse>(`/deployments/${id}/release-gate`)

export const syncBrokerState = (id: string) =>
  api.post<BrokerSyncResponse>(`/deployments/${id}/broker-sync`)

// V3.3.27 Fix-A Issue #4 & #7: surface `if_not_already` idempotency knob.
// Default server-side is `true`; callers can set `false` to force-send a
// cancel (e.g. operator override). Passing the param explicitly lets the
// API contract document the guarantee.
export const cancelBrokerOrder = (
  id: string,
  data: { client_order_id?: string; broker_order_id?: string },
  options?: { if_not_already?: boolean },
) => {
  const params: Record<string, string> = {}
  if (options && options.if_not_already === false) {
    params.if_not_already = 'false'
  }
  return api.post<CancelOrderResponse>(
    `/deployments/${id}/cancel`,
    data,
    Object.keys(params).length ? { params } : undefined,
  )
}
