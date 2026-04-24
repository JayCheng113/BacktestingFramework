/**
 * V2.25-Fe Phase 3: PaperTradingPage targeted tests.
 *
 * Focus: two real production-scary regressions in V2.15 paper trading:
 *
 * 1. DeployGate 422 response shape (V2.17 fix): backend returns
 *    `detail: { message: string, verdict: {...} }` on gate failure.
 *    If showToast receives the raw object (not the extracted message
 *    string), React crashes trying to render an object as a child
 *    → black/white screen. handleAction() has a typeof check that
 *    MUST extract .message; a regression would break gate feedback.
 *
 * 2. Holdings shape drift: latest_snapshot.holdings is typed as
 *    `Record<string, unknown>`. UI assumes each entry has
 *    `.market_value`. If backend refactor renames the field (e.g. to
 *    `value`), the defensive filter should skip the entry gracefully
 *    rather than crash. Pie chart should render either empty or
 *    cash-only, not throw.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ToastProvider } from '../components/shared/Toast'

// Mock ECharts — it crashes in jsdom (no canvas / no bounding boxes).
// Expose the `option` prop so tests can inspect series data (e.g. to
// verify pie chart excludes malformed holdings entries rather than just
// counting chart containers).
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option?: unknown }) => (
    <div
      data-testid="mock-echarts"
      data-option={option ? JSON.stringify(option) : ''}
    />
  ),
}))

// Mock the live API module so tests can control responses per-test.
vi.mock('../api/live', async () => {
  return {
    listDeployments: vi.fn(),
    getDeployment: vi.fn(),
    getDashboard: vi.fn(),
    getSnapshots: vi.fn(),
    getTrades: vi.fn(),
    getBrokerState: vi.fn(),
    getBrokerOrders: vi.fn(),
    syncBrokerState: vi.fn(),
    cancelBrokerOrder: vi.fn(),
    getBrokerSubmitGate: vi.fn(),
    getReleaseGate: vi.fn(),
    approveDeployment: vi.fn(),
    startDeployment: vi.fn(),
    stopDeployment: vi.fn(),
    pauseDeployment: vi.fn(),
    resumeDeployment: vi.fn(),
    triggerTick: vi.fn(),
  }
})

import * as liveApi from '../api/live'
import PaperTradingPage from './PaperTradingPage'

type Mock = ReturnType<typeof vi.fn>
const mocked = liveApi as unknown as Record<string, Mock>

function pendingDeployment() {
  return {
    deployment_id: 'dep-1', spec_id: 'spec-1', name: 'MyDeploy',
    status: 'pending', stop_reason: null, source_run_id: 'run-1',
    code_commit: null, gate_verdict: null,
    created_at: null, approved_at: null, started_at: null, stopped_at: null,
  }
}

function makeDetail(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ...pendingDeployment(),
    spec: {
      strategy_name: 'S',
      symbols: ['AAPL'],
      market: 'cn_stock',
      freq: 'daily',
      broker_type: 'paper',
      shadow_broker_type: null,
      initial_cash: 100000,
    },
    latest_snapshot: null,
    ...overrides,
  }
}

function makePreviewReleaseGate(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    status: 'blocked',
    eligible_for_release_candidate: false,
    eligible_for_real_submit: false,
    blockers: ['shadow_only'],
    deployment_status: 'approved',
    deploy_gate_passed: true,
    submit_gate_status: 'shadow_only',
    submit_gate_preflight_ok: true,
    submit_gate_can_submit_now: false,
    source: 'preview',
    ...overrides,
  }
}

function installDefaults() {
  mocked.listDeployments.mockResolvedValue({ data: [pendingDeployment()] })
  mocked.getDashboard.mockResolvedValue({ data: { deployments: [], alerts: [] } })
  mocked.getDeployment.mockResolvedValue({ data: makeDetail() })
  mocked.getSnapshots.mockResolvedValue({ data: [] })
  mocked.getTrades.mockResolvedValue({ data: [] })
  mocked.getBrokerState.mockResolvedValue({ data: null })
  mocked.getBrokerOrders.mockResolvedValue({ data: [] })
  mocked.syncBrokerState.mockResolvedValue({ data: { status: 'broker_synced' } })
  mocked.cancelBrokerOrder.mockResolvedValue({ data: { status: 'cancel_requested' } })
}

beforeEach(() => {
  // Full reset (clears impl + calls) then re-install so per-test
  // overrides don't leak across tests.
  vi.resetAllMocks()
  installDefaults()
})

function renderPage() {
  return render(
    <ToastProvider>
      <PaperTradingPage />
    </ToastProvider>,
  )
}

describe('PaperTradingPage - DeployGate 422 error object extraction (V2.17 regression)', () => {
  it('422 with detail={message,verdict} surfaces message string in toast (not React-renderable object)', async () => {
    // Simulate V2.17 scenario: backend rejects approve with gate failure.
    // The critical test: showToast receives a STRING (React-safe), not
    // the full {message, verdict} object. Prior V2.17 bug: object was
    // passed through, React crashed rendering it → black screen.
    mocked.approveDeployment.mockImplementation(() => {
      return Promise.reject({
        response: {
          data: {
            detail: {
              message: 'DeployGate 阻止: Sharpe 不足',
              verdict: {
                summary: 'Sharpe 0.3 低于阈值 0.5',
                reasons: [{ rule: 'min_sharpe', passed: false, message: '...' }],
              },
            },
          },
        },
        message: 'Request failed with status code 422',
      })
    })

    renderPage()
    // Wait for detail panel approve button (text is "审批 (运行 DeployGate)")
    await waitFor(
      () => expect(screen.getByRole('button', { name: /运行 DeployGate/ })).toBeInTheDocument(),
      { timeout: 3000 },
    )

    // Click approve → handleAction should fire, fail, and extract message
    await userEvent.click(screen.getByRole('button', { name: /运行 DeployGate/ }))
    await waitFor(() => expect(mocked.approveDeployment).toHaveBeenCalled())

    // The error TEXT must show up as a toast — proof showToast got the
    // string (if it got the object, React would have thrown on render).
    await waitFor(() => {
      expect(screen.getByText('DeployGate 阻止: Sharpe 不足')).toBeInTheDocument()
    }, { timeout: 3000 })
    // (Reviewer round: dropped the verdict.summary negative assertion —
    // it was theater since the current code path never emits it when
    // `message` is present. The positive assertion above is what bites.)
  })

  it('422 with missing message falls back to verdict.summary', async () => {
    // Defense-in-depth: handleAction's else-branch uses verdict.summary
    // when message is absent. A regression that only checks `detail.message`
    // would emit the generic '操作失败' here.
    mocked.approveDeployment.mockRejectedValue({
      response: {
        data: {
          detail: {
            // message missing
            verdict: { summary: '硬门控未通过', reasons: [] },
          },
        },
      },
      message: 'Request failed',
    })

    renderPage()
    await waitFor(
      () => expect(screen.getByRole('button', { name: /运行 DeployGate/ })).toBeInTheDocument(),
      { timeout: 3000 },
    )
    await userEvent.click(screen.getByRole('button', { name: /运行 DeployGate/ }))

    await waitFor(() => expect(screen.getByText('硬门控未通过')).toBeInTheDocument())
  })

  it('extracts nested structured error messages instead of falling back to generic text', async () => {
    mocked.approveDeployment.mockRejectedValue({
      response: {
        data: {
          detail: {
            error: {
              reason: '风控服务超时',
            },
          },
        },
      },
      message: 'Request failed',
    })

    renderPage()
    await waitFor(
      () => expect(screen.getByRole('button', { name: /运行 DeployGate/ })).toBeInTheDocument(),
      { timeout: 3000 },
    )
    await userEvent.click(screen.getByRole('button', { name: /运行 DeployGate/ }))

    await waitFor(() => expect(screen.getByText('风控服务超时')).toBeInTheDocument())
  })

  it('does not show success toast when refresh fails after action succeeds', async () => {
    mocked.listDeployments
      .mockResolvedValueOnce({ data: [pendingDeployment()] })
      .mockRejectedValueOnce(new Error('列表刷新失败'))
    mocked.getDashboard
      .mockResolvedValueOnce({ data: { deployments: [], alerts: [] } })
      .mockResolvedValueOnce({ data: { deployments: [], alerts: [] } })
    mocked.approveDeployment.mockResolvedValue({ data: { status: 'approved' } })

    renderPage()
    await waitFor(
      () => expect(screen.getByRole('button', { name: /运行 DeployGate/ })).toBeInTheDocument(),
      { timeout: 3000 },
    )
    await userEvent.click(screen.getByRole('button', { name: /运行 DeployGate/ }))

    await waitFor(() => expect(screen.getByText('列表刷新失败')).toBeInTheDocument())
    expect(screen.queryByText('操作成功')).not.toBeInTheDocument()
  })
})

describe('PaperTradingPage - holdings shape drift (pie chart resilience)', () => {
  it('holdings entries without market_value field do not crash; pie falls back to cash-only', async () => {
    // Regression scenario: backend schema changes `market_value` →
    // `value` (or any other rename). Defensive filter (line 183) must
    // skip the bad entries and NOT throw. A regression that removes the
    // defensive check would throw "Cannot read property 'market_value'
    // of undefined" and crash the whole page.
    mocked.getSnapshots.mockResolvedValue({
      data: [{
        snapshot_date: '2026-04-12',
        equity: 105000,
        cash: 20000,
        // Wrong shape: 'value' instead of 'market_value'
        holdings: {
          AAPL: { shares: 100, value: 50000 },      // missing market_value
          TSLA: { shares: 50, value: 35000 },       // missing market_value
          BADSHAPE: null,                            // null entry
          STRING_ENTRY: 'not-an-object',             // completely wrong type
        },
        trades: [],
        risk_events: [],
      }],
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({ status: 'running' }),
    })
    mocked.listDeployments.mockResolvedValue({
      data: [{ ...pendingDeployment(), status: 'running' }],
    })

    // If the filter regresses, renderPage() throws here (error bubbles
    // from React render cycle).
    renderPage()

    // Core invariant: page renders WITHOUT crashing, sidebar shows.
    await waitFor(() => expect(screen.getByText('MyDeploy')).toBeInTheDocument(), { timeout: 3000 })

    // Inspect the pie chart's option prop to verify the malformed
    // holdings entries are actually filtered OUT, not just rendered
    // as zero-value slices. Reviewer round: the original
    // `charts.length >= 2` assertion was weak — if the defensive
    // `&& v.market_value` check were removed, map() still produces
    // data with value=0, the chart still renders, count stays >=2,
    // and the test passes falsely.
    await waitFor(() => {
      const charts = screen.getAllByTestId('mock-echarts')
      // Find the pie chart by its option payload (title: '持仓分布')
      const pieNode = charts.find((el) => {
        const opt = el.getAttribute('data-option')
        return opt && opt.includes('持仓分布')
      })
      expect(pieNode, 'pie chart should render when cash > 0').toBeTruthy()
    })

    const charts = screen.getAllByTestId('mock-echarts')
    const pieNode = charts.find((el) =>
      (el.getAttribute('data-option') || '').includes('持仓分布'),
    )!
    const pieOpt = JSON.parse(pieNode.getAttribute('data-option')!)
    const series = pieOpt.series[0].data as Array<{ name: string; value: number }>

    // Malformed entries (AAPL/TSLA with 'value' not 'market_value',
    // null entry, string entry) must be filtered out. Only cash slice
    // remains. If the `&& v.market_value` check regresses, AAPL/TSLA
    // would appear as value=0 slices and this test fails.
    expect(series).toEqual([{ name: '现金', value: 20000 }])
  })

  it('latest_snapshot with empty holdings and zero cash renders no pie without crashing', async () => {
    mocked.getSnapshots.mockResolvedValue({
      data: [{
        snapshot_date: '2026-04-12',
        equity: 0,
        cash: 0,
        holdings: {},
        trades: [],
        risk_events: [],
      }],
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({ status: 'running' }),
    })
    mocked.listDeployments.mockResolvedValue({
      data: [{ ...pendingDeployment(), status: 'running' }],
    })

    renderPage()
    await waitFor(() => expect(screen.getByText('MyDeploy')).toBeInTheDocument(), { timeout: 3000 })
    // With pieData empty AND cash=0, the `pieOption ? ... : null` gate
    // (PaperTradingPage.tsx:244) must short-circuit to null — pie chart
    // should NOT render at all. This is a stronger assertion than
    // "count <= 2" which was tautological.
    const charts = screen.queryAllByTestId('mock-echarts')
    const pieNode = charts.find((el) =>
      (el.getAttribute('data-option') || '').includes('持仓分布'),
    )
    expect(pieNode, 'pie chart must not render when holdings empty + cash 0').toBeUndefined()
  })
})

describe('PaperTradingPage - V3 broker panels', () => {
  it('renders qmt broker readiness, gates, runtime events, and broker orders', async () => {
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
      }),
    })
    mocked.listDeployments.mockResolvedValue({
      data: [{ ...pendingDeployment(), status: 'running' }],
    })
    mocked.getBrokerState.mockResolvedValue({
      data: {
        deployment_id: 'dep-1',
        latest_broker_account: null,
        recent_runtime_events: [{
          event_id: 'evt-1',
          deployment_id: 'dep-1',
          event_type: 'broker_runtime_recorded',
          event_ts: '2026-04-14T09:30:00Z',
          client_order_id: 'cid-1',
          payload: {
            runtime_kind: 'session_consumer_state',
            payload: {
              status: 'connected',
              consumer_status: 'running',
              account_sync_mode: 'callback_preferred',
              asset_callback_freshness: 'fresh',
            },
          },
        }],
        latest_session_runtime: null,
        latest_session_owner_runtime: null,
        latest_session_consumer_runtime: null,
        latest_session_consumer_state_runtime: null,
        latest_callback_account_mode: 'callback_preferred',
        latest_callback_account_freshness: 'fresh',
        latest_reconcile: { event: 'broker_reconcile', status: 'ok' },
        latest_order_reconcile: { event: 'broker_order_reconcile', status: 'ok' },
        latest_qmt_hard_gate: null,
        qmt_readiness: {
          status: 'degraded',
          ready_for_shadow_sync: false,
          ready_for_real_submit: false,
          real_submit_enabled: false,
          account_sync_mode: 'callback_preferred',
          asset_callback_freshness: 'fresh',
          consumer_status: 'running',
          session_runtime_kind: 'session_connected',
          session_runtime_status: 'connected',
          account_reconcile_status: 'ok',
          order_reconcile_status: 'ok',
          blockers: ['shadow_mode_only'],
          real_submit_blockers: ['shadow_mode_only'],
        },
        qmt_submit_gate: {
          status: 'shadow_only',
          can_submit_now: false,
          mode: 'shadow_only',
          blockers: ['shadow_mode_only'],
          ready_for_shadow_sync: false,
          ready_for_real_submit: false,
          preflight_ok: true,
          policy: { enabled: true },
          account_id: 'acct-shadow',
          total_asset: 120000,
          initial_cash: 100000,
          source: 'runtime',
        },
        qmt_release_gate: {
          status: 'blocked',
          eligible_for_release_candidate: false,
          eligible_for_real_submit: false,
          blockers: ['qmt_submit_gate_shadow_only'],
          deployment_status: 'running',
          deploy_gate_passed: true,
          submit_gate_status: 'shadow_only',
          submit_gate_preflight_ok: true,
          submit_gate_can_submit_now: false,
          source: 'runtime',
        },
      },
    })
    mocked.getBrokerOrders.mockResolvedValue({
      data: [{
        deployment_id: 'dep-1',
        broker_type: 'qmt',
        client_order_id: 'cid-1',
        broker_order_id: 'SYS-001',
        symbol: '510300.SH',
        latest_report_id: 'rep-1',
        latest_status: 'partially_filled',
        last_report_ts: '2026-04-14T09:31:00Z',
      }],
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Broker 运行态')).toBeInTheDocument()
    })
    expect(screen.getByText('QMT runtime / gate / callback / reconcile 视图')).toBeInTheDocument()
    expect(screen.getAllByText('shadow only').length).toBeGreaterThan(0)
    expect(screen.getByText('callback preferred')).toBeInTheDocument()
    expect(screen.getByText('Broker Orders (1)')).toBeInTheDocument()
    expect(screen.getByText('SYS-001')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '撤单' })).toBeInTheDocument()
    expect(screen.getByText('QMT Runtime Workflow')).toBeInTheDocument()
    expect(screen.getAllByText('blocked').length).toBeGreaterThan(0)
  })

  it('renders qmt hard gate from runtime state and suppresses repeat cancel while cancel is in flight', async () => {
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
      }),
    })
    mocked.listDeployments.mockResolvedValue({
      data: [{ ...pendingDeployment(), status: 'running' }],
    })
    mocked.getBrokerState.mockResolvedValue({
      data: {
        deployment_id: 'dep-1',
        latest_broker_account: null,
        recent_runtime_events: [],
        latest_session_runtime: null,
        latest_session_owner_runtime: null,
        latest_session_consumer_runtime: null,
        latest_session_consumer_state_runtime: null,
        latest_callback_account_mode: 'callback_preferred',
        latest_callback_account_freshness: 'fresh',
        latest_reconcile: { event: 'broker_reconcile', status: 'ok' },
        latest_order_reconcile: { event: 'broker_order_reconcile', status: 'drift' },
        latest_qmt_hard_gate: {
          event: 'qmt_reconcile_hard_gate',
          status: 'blocked',
          blockers: ['broker_order_reconcile_drift'],
          message: 'QMT reconcile checks failed; fail closed.',
        },
        qmt_readiness: {
          status: 'degraded',
          ready_for_shadow_sync: false,
          ready_for_real_submit: false,
          real_submit_enabled: false,
          account_sync_mode: 'callback_preferred',
          asset_callback_freshness: 'fresh',
          consumer_status: 'running',
          session_runtime_kind: 'session_connected',
          session_runtime_status: 'connected',
          account_reconcile_status: 'ok',
          order_reconcile_status: 'drift',
          blockers: ['order_reconcile_not_ok'],
          real_submit_blockers: ['shadow_mode_only', 'order_reconcile_not_ok'],
        },
        qmt_submit_gate: {
          status: 'blocked',
          can_submit_now: false,
          mode: 'shadow_only',
          blockers: ['shadow_mode_only', 'order_reconcile_not_ok', 'broker_order_reconcile_drift'],
          ready_for_shadow_sync: false,
          ready_for_real_submit: false,
          preflight_ok: true,
          policy: { enabled: true },
          account_id: 'acct-shadow',
          total_asset: 120000,
          initial_cash: 100000,
          hard_gate: {
            event: 'qmt_reconcile_hard_gate',
            status: 'blocked',
            blockers: ['broker_order_reconcile_drift'],
          },
          source: 'runtime',
        },
        qmt_release_gate: {
          status: 'blocked',
          eligible_for_release_candidate: false,
          eligible_for_real_submit: false,
          blockers: ['qmt_submit_gate_blocked'],
          deployment_status: 'running',
          deploy_gate_passed: true,
          submit_gate_status: 'blocked',
          submit_gate_preflight_ok: true,
          submit_gate_can_submit_now: false,
          source: 'runtime',
        },
      },
    })
    mocked.getBrokerOrders.mockResolvedValue({
      data: [{
        deployment_id: 'dep-1',
        broker_type: 'qmt',
        client_order_id: 'cid-1',
        broker_order_id: 'SYS-001',
        symbol: '510300.SH',
        latest_report_id: 'rep-2',
        latest_status: 'cancel_requested',
        last_report_ts: '2026-04-14T09:35:00Z',
      }],
    })

    renderPage()

    const runtimePanel = await screen.findByTestId('qmt-runtime-panel')
    expect(within(runtimePanel).getByTestId('runtime-hard-gate-card')).toHaveTextContent('blocked')
    expect(within(runtimePanel).getAllByText('broker order reconcile drift').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: '撤单' })).not.toBeInTheDocument()
  })

  it('filters sidebar by qmt release candidate and blocked state from dashboard health', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [
        { ...pendingDeployment(), deployment_id: 'dep-1', name: 'CandidateDeploy', status: 'running' },
        { ...pendingDeployment(), deployment_id: 'dep-2', name: 'BlockedDeploy', status: 'running' },
      ],
    })
    mocked.getDashboard.mockResolvedValue({
      data: {
        deployments: [
          {
            deployment_id: 'dep-1',
            name: 'CandidateDeploy',
            status: 'running',
            cumulative_return: 0.12,
            max_drawdown: -0.04,
            sharpe_ratio: 1.8,
            today_pnl: 1200,
            today_trades: 2,
            risk_events_today: 0,
            total_risk_events: 0,
            consecutive_loss_days: 0,
            last_execution_date: '2026-04-14',
            last_execution_duration_ms: 120,
            days_since_last_trade: 0,
            error_count: 0,
            qmt_release_gate_status: 'candidate',
            qmt_release_candidate: true,
            qmt_release_blockers: [],
          },
          {
            deployment_id: 'dep-2',
            name: 'BlockedDeploy',
            status: 'running',
            cumulative_return: 0.03,
            max_drawdown: -0.02,
            sharpe_ratio: 0.9,
            today_pnl: -300,
            today_trades: 0,
            risk_events_today: 1,
            total_risk_events: 1,
            consecutive_loss_days: 1,
            last_execution_date: '2026-04-14',
            last_execution_duration_ms: 140,
            days_since_last_trade: 1,
            error_count: 0,
            qmt_release_gate_status: 'blocked',
            qmt_release_candidate: false,
            qmt_release_blockers: ['shadow_only'],
          },
        ],
        alerts: [],
      },
    })
    mocked.getDeployment.mockImplementation((id: string) => Promise.resolve({
      data: makeDetail({
        deployment_id: id,
        name: id === 'dep-1' ? 'CandidateDeploy' : 'BlockedDeploy',
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
      }),
    }))

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('CandidateDeploy')).toBeInTheDocument()
      expect(screen.getByText('BlockedDeploy')).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole('button', { name: /QMT 候选 \(1\)/ }))
    expect(screen.getByTitle('CandidateDeploy')).toBeInTheDocument()
    expect(screen.queryByTitle('BlockedDeploy')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /QMT 受阻 \(1\)/ }))
    expect(screen.getByTitle('BlockedDeploy')).toBeInTheDocument()
    expect(screen.queryByTitle('CandidateDeploy')).not.toBeInTheDocument()
  })

  it('filters sidebar by preview qmt release gate when dashboard has no active health row', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [
        {
          ...pendingDeployment(),
          deployment_id: 'dep-1',
          name: 'PreviewCandidate',
          status: 'approved',
          qmt_release_gate: makePreviewReleaseGate({
            status: 'candidate',
            eligible_for_release_candidate: true,
            blockers: [],
          }),
        },
        {
          ...pendingDeployment(),
          deployment_id: 'dep-2',
          name: 'PreviewBlocked',
          status: 'approved',
          qmt_release_gate: makePreviewReleaseGate(),
        },
      ],
    })
    mocked.getDashboard.mockResolvedValue({
      data: { deployments: [], alerts: [] },
    })
    mocked.getDeployment.mockImplementation((id: string) => Promise.resolve({
      data: makeDetail({
        deployment_id: id,
        name: id === 'dep-1' ? 'PreviewCandidate' : 'PreviewBlocked',
        status: 'approved',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
        qmt_release_gate: id === 'dep-1'
          ? makePreviewReleaseGate({
            status: 'candidate',
            eligible_for_release_candidate: true,
            blockers: [],
          })
          : makePreviewReleaseGate(),
      }),
    }))

    renderPage()

    await waitFor(() => {
      expect(screen.getByText('PreviewCandidate')).toBeInTheDocument()
      expect(screen.getByText('PreviewBlocked')).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole('button', { name: /QMT 候选 \(1\)/ }))
    expect(screen.getByTitle('PreviewCandidate')).toBeInTheDocument()
    expect(screen.queryByTitle('PreviewBlocked')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /QMT 受阻 \(1\)/ }))
    expect(screen.getByTitle('PreviewBlocked')).toBeInTheDocument()
    expect(screen.queryByTitle('PreviewCandidate')).not.toBeInTheDocument()
    expect(screen.getAllByText('preview').length).toBeGreaterThan(0)
  })

  it('prefers runtime release truth over preview gate in the sidebar filters', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [{
        ...pendingDeployment(),
        deployment_id: 'dep-1',
        name: 'RuntimeWins',
        status: 'running',
        qmt_release_gate: makePreviewReleaseGate({
          status: 'candidate',
          eligible_for_release_candidate: true,
          blockers: [],
        }),
      }],
    })
    mocked.getDashboard.mockResolvedValue({
      data: {
        deployments: [{
          deployment_id: 'dep-1',
          name: 'RuntimeWins',
          status: 'running',
          cumulative_return: 0.01,
          max_drawdown: -0.01,
          sharpe_ratio: 1.0,
          today_pnl: 100,
          today_trades: 1,
          risk_events_today: 0,
          total_risk_events: 0,
          consecutive_loss_days: 0,
          last_execution_date: '2026-04-14',
          last_execution_duration_ms: 100,
          days_since_last_trade: 0,
          error_count: 0,
          qmt_release_gate_status: 'blocked',
          qmt_release_candidate: false,
          qmt_release_blockers: ['broker_reconcile_drift'],
        }],
        alerts: [],
      },
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        deployment_id: 'dep-1',
        name: 'RuntimeWins',
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
        qmt_release_gate: makePreviewReleaseGate({
          status: 'candidate',
          eligible_for_release_candidate: true,
          blockers: [],
        }),
      }),
    })

    renderPage()

    await waitFor(() => {
      expect(screen.getByTitle('RuntimeWins')).toBeInTheDocument()
    })

    const runtimeRow = screen.getByTitle('RuntimeWins').closest('button')
    expect(runtimeRow).not.toBeNull()
    expect(runtimeRow!).toHaveTextContent('blocked')
    expect(runtimeRow!).toHaveTextContent('runtime')

    await userEvent.click(screen.getByRole('button', { name: /QMT 受阻 \(1\)/ }))
    expect(screen.getByTitle('RuntimeWins')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /QMT 候选 \(0\)/ }))
    expect(screen.queryByTitle('RuntimeWins')).not.toBeInTheDocument()
  })

  it('does not let preview gate override runtime projection presence in the sidebar', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [{
        ...pendingDeployment(),
        deployment_id: 'dep-1',
        name: 'RuntimeProjectionNeutral',
        status: 'running',
        qmt_release_gate: makePreviewReleaseGate({
          status: 'candidate',
          eligible_for_release_candidate: true,
          blockers: [],
        }),
      }],
    })
    mocked.getDashboard.mockResolvedValue({
      data: {
        deployments: [{
          deployment_id: 'dep-1',
          name: 'RuntimeProjectionNeutral',
          status: 'running',
          cumulative_return: 0,
          max_drawdown: 0,
          sharpe_ratio: null,
          today_pnl: 0,
          today_trades: 0,
          risk_events_today: 0,
          total_risk_events: 0,
          consecutive_loss_days: 0,
          last_execution_date: '2026-04-14',
          last_execution_duration_ms: 90,
          days_since_last_trade: 0,
          error_count: 0,
          qmt_release_gate_status: null,
          qmt_release_candidate: false,
          qmt_release_blockers: [],
          qmt_projection_source: 'runtime',
          qmt_projection_ts: '2026-04-14T09:37:00Z',
        }],
        alerts: [],
      },
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        deployment_id: 'dep-1',
        name: 'RuntimeProjectionNeutral',
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'qmt',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
        qmt_release_gate: makePreviewReleaseGate({
          status: 'candidate',
          eligible_for_release_candidate: true,
          blockers: [],
        }),
      }),
    })

    renderPage()

    const row = (await screen.findByTitle('RuntimeProjectionNeutral')).closest('button')
    expect(row).not.toBeNull()
    expect(row!).not.toHaveTextContent('candidate')
    expect(row!).not.toHaveTextContent('preview')

    await userEvent.click(screen.getByRole('button', { name: /QMT 候选 \(0\)/ }))
    expect(screen.queryByTitle('RuntimeProjectionNeutral')).not.toBeInTheDocument()
  })

  it('renders blocker-only runtime release state as blocked in the sidebar', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [{
        ...pendingDeployment(),
        deployment_id: 'dep-1',
        name: 'BlockerOnlyRuntime',
        status: 'running',
        qmt_release_gate: null,
      }],
    })
    mocked.getDashboard.mockResolvedValue({
      data: {
        deployments: [{
          deployment_id: 'dep-1',
          name: 'BlockerOnlyRuntime',
          status: 'running',
          cumulative_return: 0.0,
          max_drawdown: 0.0,
          sharpe_ratio: null,
          today_pnl: 0,
          today_trades: 0,
          risk_events_today: 1,
          total_risk_events: 1,
          consecutive_loss_days: 0,
          last_execution_date: '2026-04-14',
          last_execution_duration_ms: 90,
          days_since_last_trade: 0,
          error_count: 0,
          qmt_release_gate_status: null,
          qmt_release_candidate: false,
          qmt_release_blockers: ['broker_order_reconcile_drift'],
        }],
        alerts: [],
      },
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        deployment_id: 'dep-1',
        name: 'BlockerOnlyRuntime',
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
        qmt_release_gate: null,
      }),
    })

    renderPage()

    const row = (await screen.findByTitle('BlockerOnlyRuntime')).closest('button')
    expect(row).not.toBeNull()
    expect(row!).toHaveTextContent('blocked')
    expect(row!).toHaveTextContent('1 blocker')
    expect(row!).toHaveTextContent('runtime')

    await userEvent.click(screen.getByRole('button', { name: /QMT 受阻 \(1\)/ }))
    expect(screen.getByTitle('BlockerOnlyRuntime')).toBeInTheDocument()
  })

  it('keeps preview release gate out of the runtime panel and renders it separately', async () => {
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        status: 'approved',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
        qmt_release_gate: makePreviewReleaseGate({
          status: 'candidate',
          eligible_for_release_candidate: true,
          blockers: [],
        }),
      }),
    })
    mocked.listDeployments.mockResolvedValue({
      data: [{
        ...pendingDeployment(),
        status: 'approved',
        qmt_release_gate: makePreviewReleaseGate({
          status: 'candidate',
          eligible_for_release_candidate: true,
          blockers: [],
        }),
      }],
    })
    mocked.getBrokerState.mockResolvedValue({
      data: {
        deployment_id: 'dep-1',
        latest_broker_account: null,
        recent_runtime_events: [],
        latest_session_runtime: null,
        latest_session_owner_runtime: null,
        latest_session_consumer_runtime: null,
        latest_session_consumer_state_runtime: null,
        latest_callback_account_mode: null,
        latest_callback_account_freshness: null,
        latest_reconcile: null,
        latest_order_reconcile: null,
        latest_qmt_hard_gate: null,
        qmt_readiness: {
          status: 'degraded',
          ready_for_shadow_sync: false,
          ready_for_real_submit: false,
          real_submit_enabled: false,
          account_sync_mode: null,
          asset_callback_freshness: null,
          consumer_status: null,
          session_runtime_kind: null,
          session_runtime_status: null,
          account_reconcile_status: null,
          order_reconcile_status: null,
          blockers: ['missing_runtime'],
          real_submit_blockers: ['missing_runtime'],
        },
        qmt_submit_gate: {
          status: 'shadow_only',
          can_submit_now: false,
          mode: 'shadow_only',
          blockers: ['missing_runtime'],
          ready_for_shadow_sync: false,
          ready_for_real_submit: false,
          preflight_ok: false,
          policy: { enabled: true },
          account_id: 'acct-shadow',
          total_asset: null,
          initial_cash: 100000,
          source: 'runtime',
        },
        qmt_release_gate: null,
      },
    })

    renderPage()

    const runtimePanel = await screen.findByTestId('qmt-runtime-panel')
    const previewPanel = await screen.findByTestId('qmt-preview-panel')

    expect(within(runtimePanel).getByTestId('runtime-release-gate-card')).toHaveTextContent('Release Gate')
    expect(within(runtimePanel).getByTestId('runtime-release-gate-card')).toHaveTextContent('-')
    expect(within(runtimePanel).queryByText('candidate')).not.toBeInTheDocument()

    expect(within(previewPanel).getByTestId('preview-release-gate-card')).toHaveTextContent('candidate')
    expect(within(previewPanel).getByText('审批态 release candidate 预览，不代表 broker runtime。')).toBeInTheDocument()
    expect(within(previewPanel).getAllByText('preview').length).toBeGreaterThan(0)
  })

  it('renders runtime projection and reconcile context from structured broker state', async () => {
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'qmt',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
      }),
    })
    mocked.listDeployments.mockResolvedValue({
      data: [{ ...pendingDeployment(), status: 'running' }],
    })
    mocked.getBrokerState.mockResolvedValue({
      data: {
        deployment_id: 'dep-1',
        latest_broker_account: null,
        recent_runtime_events: [],
        latest_session_runtime: null,
        latest_session_owner_runtime: null,
        latest_session_consumer_runtime: null,
        latest_session_consumer_state_runtime: null,
        latest_callback_account_mode: 'callback_preferred',
        latest_callback_account_freshness: 'fresh',
        latest_reconcile: {
          event: 'real_broker_reconcile',
          status: 'drift',
          account_id: 'acct-real',
          compared_at: '2026-04-14T09:37:00Z',
          message: 'cash drift detected',
        },
        latest_order_reconcile: {
          event: 'real_broker_order_reconcile',
          status: 'ok',
          account_id: 'acct-real',
          compared_at: '2026-04-14T09:37:05Z',
        },
        latest_qmt_hard_gate: {
          event: 'real_qmt_reconcile_hard_gate',
          status: 'blocked',
          account_id: 'acct-real',
          compared_at: '2026-04-14T09:37:06Z',
          message: 'reconcile gate blocked',
          blockers: ['broker_reconcile_drift'],
        },
        qmt_readiness: {
          status: 'ready',
          ready_for_shadow_sync: true,
          ready_for_real_submit: false,
          real_submit_enabled: true,
          account_sync_mode: 'callback_preferred',
          asset_callback_freshness: 'fresh',
          consumer_status: 'running',
          session_runtime_kind: 'session_subscribed',
          session_runtime_status: 'connected',
          account_reconcile_status: 'drift',
          order_reconcile_status: 'ok',
          blockers: [],
          real_submit_blockers: ['broker_reconcile_drift'],
        },
        qmt_submit_gate: {
          status: 'blocked',
          can_submit_now: false,
          mode: 'real',
          blockers: ['broker_reconcile_drift'],
          ready_for_shadow_sync: true,
          ready_for_real_submit: false,
          preflight_ok: true,
          policy: {},
          account_id: 'acct-real',
          total_asset: 100000,
          initial_cash: 100000,
          source: 'runtime',
        },
        qmt_release_gate: {
          status: 'blocked',
          eligible_for_release_candidate: false,
          eligible_for_real_submit: false,
          blockers: ['qmt_submit_gate_blocked'],
          deployment_status: 'running',
          deploy_gate_passed: true,
          submit_gate_status: 'blocked',
          submit_gate_preflight_ok: true,
          submit_gate_can_submit_now: false,
          source: 'runtime',
        },
        target_account_id: 'acct-real',
        projection_source: 'runtime',
        projection_ts: '2026-04-14T09:37:10Z',
      },
    })

    renderPage()

    const runtimePanel = await screen.findByTestId('qmt-runtime-panel')
    expect(within(runtimePanel).getByText('projection:runtime')).toBeInTheDocument()
    expect(within(runtimePanel).getByText('updated:2026-04-14 09:37:10')).toBeInTheDocument()
    expect(within(runtimePanel).getAllByText('account:acct-real').length).toBeGreaterThan(0)
    expect(within(runtimePanel).getByText('event:real broker reconcile')).toBeInTheDocument()
    expect(within(runtimePanel).getByText('message:cash drift detected')).toBeInTheDocument()
    expect(within(runtimePanel).getByText('message:reconcile gate blocked')).toBeInTheDocument()
  })
})

// V3.3.27 Fix-A Issue #5 + #6 + #2: new regression tests.
describe('PaperTradingPage - V3.3.27 Fix-A regressions', () => {
  it('surfaces broker-state sync failure as a visible red bar when API rejects (Issue #5)', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [{ ...pendingDeployment(), status: 'running' }],
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: null,
          initial_cash: 100000,
        },
      }),
    })
    mocked.getBrokerState.mockRejectedValue(
      new Error('broker-state 500 server error'),
    )
    // broker-orders succeeds so we can prove each subrequest is tracked
    // independently.
    mocked.getBrokerOrders.mockResolvedValue({
      data: { deployment_id: 'dep-1', target_account_id: null, orders: [] },
    })

    renderPage()

    await waitFor(
      () => expect(screen.getByText('MyDeploy')).toBeInTheDocument(),
      { timeout: 3000 },
    )

    // A red SyncErrorBar with the expected copy should render.
    await waitFor(() => {
      const bar = screen.getByTestId('sync-error-broker-state')
      expect(bar).toBeInTheDocument()
      expect(bar).toHaveTextContent(/broker-state 同步失败/)
      expect(bar).toHaveTextContent('broker-state 500 server error')
    })

    // Retry button is present.
    expect(within(screen.getByTestId('sync-error-broker-state')).getByRole('button', { name: '重试' })).toBeInTheDocument()

    // broker-orders panel should NOT show an error because it resolved.
    expect(screen.queryByTestId('sync-error-broker-orders')).not.toBeInTheDocument()
  })

  it('broker-orders failure surfaces its own red bar without touching broker-state (Issue #5)', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [{ ...pendingDeployment(), status: 'running' }],
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        status: 'running',
      }),
    })
    mocked.getBrokerState.mockResolvedValue({ data: null })
    mocked.getBrokerOrders.mockRejectedValue(
      new Error('broker-orders 503 busy'),
    )

    renderPage()

    await waitFor(
      () => expect(screen.getByText('MyDeploy')).toBeInTheDocument(),
      { timeout: 3000 },
    )
    await waitFor(() => {
      const bar = screen.getByTestId('sync-error-broker-orders')
      expect(bar).toHaveTextContent(/broker-orders 同步失败/)
      expect(bar).toHaveTextContent('broker-orders 503 busy')
    })
    expect(screen.queryByTestId('sync-error-broker-state')).not.toBeInTheDocument()
  })

  it('unwraps new broker-orders {target_account_id, orders} response shape (Issue #2)', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [{ ...pendingDeployment(), status: 'running' }],
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        status: 'running',
        spec: {
          strategy_name: 'S',
          symbols: ['510300.SH'],
          market: 'cn_stock',
          freq: 'daily',
          broker_type: 'paper',
          shadow_broker_type: 'qmt',
          initial_cash: 100000,
        },
      }),
    })
    mocked.getBrokerState.mockResolvedValue({ data: null })
    mocked.getBrokerOrders.mockResolvedValue({
      data: {
        deployment_id: 'dep-1',
        target_account_id: 'acct-shadow-from-orders',
        orders: [{
          deployment_id: 'dep-1',
          broker_type: 'qmt',
          client_order_id: 'cid-wrap-1',
          broker_order_id: 'SYS-WRAP-1',
          symbol: '510300.SH',
          latest_report_id: 'rep-wrap-1',
          latest_status: 'partially_filled',
          last_report_ts: '2026-04-14T09:30:00Z',
          account_id: 'acct-shadow-from-orders',
        }],
      },
    })

    renderPage()

    // The wrapped `orders` list must be unwrapped so the table renders
    // the broker order row.
    await waitFor(() => {
      expect(screen.getByText('Broker Orders (1)')).toBeInTheDocument()
      expect(screen.getByText('SYS-WRAP-1')).toBeInTheDocument()
    })
  })

  it('degrades release display to preview when deployment is not running even if API says runtime (Issue #6)', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [{
        ...pendingDeployment(),
        deployment_id: 'dep-1',
        name: 'NotRunning',
        status: 'approved',
        qmt_release_gate: {
          // API returned source=runtime, but the deployment is NOT running.
          // The UI should still render as preview.
          status: 'candidate',
          eligible_for_release_candidate: true,
          eligible_for_real_submit: false,
          blockers: [],
          deployment_status: 'approved',
          deploy_gate_passed: true,
          submit_gate_status: 'open',
          submit_gate_preflight_ok: true,
          submit_gate_can_submit_now: false,
          source: 'runtime',
        } as unknown as ReturnType<typeof makePreviewReleaseGate>,
      }],
    })
    mocked.getDashboard.mockResolvedValue({
      data: {
        deployments: [{
          deployment_id: 'dep-1',
          name: 'NotRunning',
          status: 'approved',
          cumulative_return: 0,
          max_drawdown: 0,
          sharpe_ratio: null,
          today_pnl: 0,
          today_trades: 0,
          risk_events_today: 0,
          total_risk_events: 0,
          consecutive_loss_days: 0,
          last_execution_date: null,
          last_execution_duration_ms: 0,
          days_since_last_trade: 0,
          error_count: 0,
          qmt_release_gate_status: 'candidate',
          qmt_release_candidate: true,
          qmt_release_blockers: [],
          qmt_projection_source: 'runtime',
          qmt_projection_ts: '2026-04-14T09:00:00Z',
        }],
        alerts: [],
      },
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        deployment_id: 'dep-1',
        name: 'NotRunning',
        status: 'approved',
      }),
    })

    renderPage()

    const row = (await screen.findByTitle('NotRunning')).closest('button')
    expect(row).not.toBeNull()
    // The row must render a 'preview' badge (degraded), NOT 'runtime'.
    expect(row!).toHaveTextContent('preview')
    expect(row!).not.toHaveTextContent('runtime')
  })

  it('runtime badge is visually distinct from preview badge (Issue #6)', async () => {
    mocked.listDeployments.mockResolvedValue({
      data: [{
        ...pendingDeployment(),
        deployment_id: 'dep-1',
        name: 'RuntimeBadgeTest',
        status: 'running',
      }],
    })
    mocked.getDashboard.mockResolvedValue({
      data: {
        deployments: [{
          deployment_id: 'dep-1',
          name: 'RuntimeBadgeTest',
          status: 'running',
          cumulative_return: 0,
          max_drawdown: 0,
          sharpe_ratio: null,
          today_pnl: 0,
          today_trades: 0,
          risk_events_today: 0,
          total_risk_events: 0,
          consecutive_loss_days: 0,
          last_execution_date: null,
          last_execution_duration_ms: 0,
          days_since_last_trade: 0,
          error_count: 0,
          qmt_release_gate_status: 'candidate',
          qmt_release_candidate: true,
          qmt_release_blockers: [],
          qmt_projection_source: 'runtime',
        }],
        alerts: [],
      },
    })
    mocked.getDeployment.mockResolvedValue({
      data: makeDetail({
        deployment_id: 'dep-1',
        name: 'RuntimeBadgeTest',
        status: 'running',
      }),
    })

    renderPage()

    const row = (await screen.findByTitle('RuntimeBadgeTest')).closest('button')
    expect(row).not.toBeNull()
    // runtime badge is rendered and has the green color style applied.
    const runtimeBadge = within(row!).getByText('runtime')
    expect(runtimeBadge).toBeInTheDocument()
    // The badge should be styled green per Fix-A Issue #6.
    // Color `#22c55e` maps to `rgb(34, 197, 94)` in browsers.
    const style = window.getComputedStyle(runtimeBadge)
    expect(style.color).toMatch(/22c55e|rgb\(34,\s*197,\s*94\)/i)
  })
})
