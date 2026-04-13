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
import { render, screen, waitFor } from '@testing-library/react'
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
    spec: { strategy_name: 'S', symbols: ['AAPL'], market: 'cn_stock', freq: 'daily', initial_cash: 100000 },
    latest_snapshot: null,
    ...overrides,
  }
}

function installDefaults() {
  mocked.listDeployments.mockResolvedValue({ data: [pendingDeployment()] })
  mocked.getDashboard.mockResolvedValue({ data: { deployments: [], alerts: [] } })
  mocked.getDeployment.mockResolvedValue({ data: makeDetail() })
  mocked.getSnapshots.mockResolvedValue({ data: [] })
  mocked.getTrades.mockResolvedValue({ data: [] })
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
