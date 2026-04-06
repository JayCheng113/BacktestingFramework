/**
 * V2.15 C2: Paper Trading API client — typed functions for /api/live endpoints.
 */
import axios from 'axios'

const api = axios.create({ baseURL: '/api/live' })

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DeploymentSummary {
  deployment_id: string
  spec_id: string
  name: string
  status: string
  stop_reason: string | null
  source_run_id: string | null
  code_commit: string | null
  gate_verdict: string | null
  created_at: string | null
  approved_at: string | null
  started_at: string | null
  stopped_at: string | null
}

export interface DeploymentDetail extends DeploymentSummary {
  spec: {
    strategy_name: string
    symbols: string[]
    market: string
    freq: string
    initial_cash: number
  } | null
  latest_snapshot: Record<string, unknown> | null
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
  risk_events: string[]
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

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export const deployToLive = (data: { source_run_id: string; name: string; wf_metrics?: Record<string, unknown> }) =>
  api.post<DeployResponse>('/deploy', data)

export const listDeployments = (status?: string) =>
  api.get<DeploymentSummary[]>('/deployments', { params: status ? { status } : {} })

export const getDeployment = (id: string) =>
  api.get<DeploymentDetail>(`/deployments/${id}`)

export const approveDeployment = (id: string) =>
  api.post(`/deployments/${id}/approve`)

export const startDeployment = (id: string) =>
  api.post(`/deployments/${id}/start`)

export const stopDeployment = (id: string, reason?: string) =>
  api.post(`/deployments/${id}/stop`, { reason: reason || '手动停止' })

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
