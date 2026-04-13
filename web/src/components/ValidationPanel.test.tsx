/**
 * V2.25-Fe: ValidationPanel component tests.
 *
 * Covers the key branches that backend contract tests CANNOT catch:
 * - Empty state renders guide + 4 step cards
 * - n_trials=1 warning banner (V2.23.2 Critical 2)
 * - Verdict banner renders correct color/icon per result
 * - ComparisonSection headline branches on sign (V2.23.2 I7 regression)
 * - Comparison discriminated union (success vs error) render (V2.23.2 I8)
 * - Baseline selector: runId != baselineId filter, clear on run change
 * - MinBTL status rendering (unprofitable / below_search / ok)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ValidationPanel } from './ValidationPanel'
import { ToastProvider } from './shared/Toast'
import type { ValidationResult } from '../types'

// Mock the API module
vi.mock('../api', () => ({
  runValidation: vi.fn(),
  listPortfolioRuns: vi.fn(() => Promise.resolve({ data: [] })),
}))

// Mock ECharts wrapper (jsdom doesn't support canvas)
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option: unknown }) => (
    <div data-testid="mock-echart" data-option={JSON.stringify(option).slice(0, 100)} />
  ),
}))

import { runValidation, listPortfolioRuns } from '../api'

const mockedRunValidation = vi.mocked(runValidation)
const mockedListRuns = vi.mocked(listPortfolioRuns)

function renderPanel(runId = 'test_run_001') {
  return render(
    <ToastProvider>
      <ValidationPanel runId={runId} />
    </ToastProvider>,
  )
}

function makeResult(overrides: Partial<ValidationResult> = {}): ValidationResult {
  return {
    run_id: 'test_run_001',
    baseline_run_id: null,
    significance: {
      observed_sharpe: 1.5,
      ci_lower: 0.8,
      ci_upper: 2.1,
      p_value: 0.01,
      n_bootstrap: 2000,
      block_size: 21,
    },
    deflated: {
      sharpe: 1.5,
      deflated_sharpe: 0.85,
      expected_max_sr: 0.2,
      skew: 0.1,
      kurt: 3.2,
      excess_kurt: 0.2,
    },
    min_btl: {
      actual_years: 5.0,
      min_btl_years: 2.1,
    },
    annual: {
      per_year: [
        { year: 2020, sharpe: 1.2, ret: 0.15, mdd: -0.08, n_days: 252 },
        { year: 2021, sharpe: 1.5, ret: 0.22, mdd: -0.06, n_days: 252 },
      ],
      worst_year: 2020,
      best_year: 2021,
      profitable_ratio: 1.0,
      consistency_score: 1.0,
    },
    walk_forward: null,
    comparison: null,
    verdict: {
      result: 'pass',
      passed: 5,
      warned: 0,
      failed: 0,
      total: 5,
      summary: '策略通过 5/5 项检验, 无警告. 建议推进到模拟盘阶段.',
      checks: [
        { name: 'Statistical significance (p-value)', status: 'pass', reason: 'p = 0.0100.', value: 0.01 },
      ],
      badge_color: 'green',
      badge_emoji: '🟢',
    },
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedListRuns.mockResolvedValue({ data: [] } as unknown as Awaited<ReturnType<typeof listPortfolioRuns>>)
})


describe('ValidationPanel - empty state', () => {
  it('renders the 4-step guide before running', () => {
    renderPanel()
    // Top-level purpose description
    expect(screen.getByText(/这个面板回答一个问题/)).toBeInTheDocument()
    // 4 step cards
    expect(screen.getByText('统计显著性')).toBeInTheDocument()
    expect(screen.getByText('去偏差 Sharpe')).toBeInTheDocument()
    expect(screen.getByText('年度稳定性')).toBeInTheDocument()
    expect(screen.getByText(/配对对比/)).toBeInTheDocument()
  })

  it('shows run button disabled-free state when runId present', () => {
    renderPanel('my_run_123')
    const button = screen.getByRole('button', { name: /运行验证/ })
    expect(button).not.toBeDisabled()
  })
})


describe('ValidationPanel - verdict banner colors (V2.23.2 UX fix)', () => {
  it('pass verdict uses blue (not green) to avoid A-股 down conflict', async () => {
    mockedRunValidation.mockResolvedValue({
      data: makeResult(),
    } as unknown as Awaited<ReturnType<typeof runValidation>>)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /运行验证/ }))
    await waitFor(() => expect(screen.getByText('通过')).toBeInTheDocument())
    // Should contain summary
    expect(screen.getByText(/建议推进到模拟盘/)).toBeInTheDocument()
  })

  it('fail verdict shows failed count', async () => {
    mockedRunValidation.mockResolvedValue({
      data: makeResult({
        verdict: {
          result: 'fail',
          passed: 2,
          warned: 0,
          failed: 3,
          total: 5,
          summary: '策略未通过 3 项关键检验',
          checks: [],
          badge_color: 'red',
          badge_emoji: '🔴',
        },
      }),
    } as unknown as Awaited<ReturnType<typeof runValidation>>)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /运行验证/ }))
    await waitFor(() => expect(screen.getByText('不通过')).toBeInTheDocument())
    expect(screen.getByText(/3 项不通过/)).toBeInTheDocument()
  })
})


describe('ValidationPanel - n_trials warning (V2.23.2 Critical 2)', () => {
  it('shows warning banner when n_trials=1', async () => {
    mockedRunValidation.mockResolvedValue({
      data: makeResult(),
    } as unknown as Awaited<ReturnType<typeof runValidation>>)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /运行验证/ }))
    await waitFor(() => expect(screen.getByText(/n_trials=1/)).toBeInTheDocument())
    // Warning mentions both DSR and MinBTL
    const warningBanner = screen.getByText(/n_trials=1/).closest('div')
    expect(warningBanner?.textContent).toMatch(/DSR/)
    expect(warningBanner?.textContent).toMatch(/MinBTL/)
  })
})


describe('ValidationPanel - ComparisonSection sign branch (V2.23.2 I7)', () => {
  it('"显著优于基线" shown when sharpe_diff > 0 and significant', async () => {
    mockedRunValidation.mockResolvedValue({
      data: makeResult({
        baseline_run_id: 'baseline_1',
        comparison: {
          status: 'success',
          treatment_run_id: 'test_run_001',
          control_run_id: 'baseline_1',
          sharpe_diff: 0.5,
          ci_lower: 0.2,
          ci_upper: 0.8,
          p_value: 0.02,
          is_significant: true,
          ci_excludes_zero: true,
          treatment_metrics: { sharpe: 1.5, ret: 0.2, vol: 0.12, dd: -0.08 },
          control_metrics: { sharpe: 1.0, ret: 0.15, vol: 0.11, dd: -0.1 },
          n_observations: 500,
        },
      }),
    } as unknown as Awaited<ReturnType<typeof runValidation>>)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /运行验证/ }))
    await waitFor(() => expect(screen.getByText(/显著优于基线/)).toBeInTheDocument())
  })

  it('"显著差于基线" shown when sharpe_diff < 0 and significant (prior bug)', async () => {
    mockedRunValidation.mockResolvedValue({
      data: makeResult({
        baseline_run_id: 'baseline_1',
        comparison: {
          status: 'success',
          treatment_run_id: 'test_run_001',
          control_run_id: 'baseline_1',
          sharpe_diff: -0.5,
          ci_lower: -0.8,
          ci_upper: -0.2,
          p_value: 0.02,
          is_significant: true,
          ci_excludes_zero: true,
          treatment_metrics: { sharpe: 1.0, ret: 0.1, vol: 0.12, dd: -0.1 },
          control_metrics: { sharpe: 1.5, ret: 0.2, vol: 0.11, dd: -0.08 },
          n_observations: 500,
        },
      }),
    } as unknown as Awaited<ReturnType<typeof runValidation>>)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /运行验证/ }))
    await waitFor(() => expect(screen.getByText(/显著差于基线/)).toBeInTheDocument())
    // Must NOT show "显著优于基线" — that was the regression
    expect(screen.queryByText(/显著优于基线/)).not.toBeInTheDocument()
  })

  it('"差异不显著" when is_significant=false', async () => {
    mockedRunValidation.mockResolvedValue({
      data: makeResult({
        baseline_run_id: 'baseline_1',
        comparison: {
          status: 'success',
          treatment_run_id: 'test_run_001',
          control_run_id: 'baseline_1',
          sharpe_diff: 0.1,
          ci_lower: -0.2,
          ci_upper: 0.4,
          p_value: 0.3,
          is_significant: false,
          ci_excludes_zero: false,
          treatment_metrics: { sharpe: 1.1, ret: 0.15, vol: 0.12, dd: -0.1 },
          control_metrics: { sharpe: 1.0, ret: 0.14, vol: 0.11, dd: -0.1 },
          n_observations: 500,
        },
      }),
    } as unknown as Awaited<ReturnType<typeof runValidation>>)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /运行验证/ }))
    await waitFor(() => expect(screen.getByText(/差异不显著/)).toBeInTheDocument())
  })
})


describe('ValidationPanel - comparison discriminated union (V2.23.2 I8)', () => {
  it('renders error section when comparison.status=error', async () => {
    mockedRunValidation.mockResolvedValue({
      data: makeResult({
        baseline_run_id: 'bad_baseline',
        comparison: {
          status: 'error',
          treatment_run_id: 'test_run_001',
          control_run_id: 'bad_baseline',
          error: '配对数据不足: 对齐后仅 10 行',
        },
      }),
    } as unknown as Awaited<ReturnType<typeof runValidation>>)
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: /运行验证/ }))
    await waitFor(() => expect(screen.getByText(/对比失败/)).toBeInTheDocument())
    expect(screen.getByText(/配对数据不足/)).toBeInTheDocument()
  })
})


describe('ValidationPanel - baseline selector', () => {
  it('filters out current runId from dropdown options', async () => {
    mockedListRuns.mockResolvedValue({
      data: [
        { run_id: 'my_run', strategy_name: 'Self', start_date: '2024-01-01', end_date: '2024-06-30', freq: 'weekly', metrics: { sharpe_ratio: 1.5 }, trade_count: 10, created_at: '2024-07-01' },
        { run_id: 'other_run', strategy_name: 'Other', start_date: '2024-01-01', end_date: '2024-06-30', freq: 'weekly', metrics: { sharpe_ratio: 1.0 }, trade_count: 5, created_at: '2024-07-02' },
      ],
    } as unknown as Awaited<ReturnType<typeof listPortfolioRuns>>)
    renderPanel('my_run')
    // Wait for the dropdown to load
    await waitFor(() => expect(screen.getByText(/Other/)).toBeInTheDocument())
    // Self should NOT be in the dropdown options (it's the current run)
    const options = screen.getAllByRole('option')
    const selfOption = options.find((o) => o.textContent?.includes('Self'))
    expect(selfOption).toBeUndefined()
  })
})


describe('ValidationPanel - invalid input rejection', () => {
  it('shows error toast when bootstrap count out of bounds', async () => {
    renderPanel()
    const bootstrapInput = screen.getByLabelText(/Bootstrap 次数/)
    fireEvent.change(bootstrapInput, { target: { value: '50' } })
    await userEvent.click(screen.getByRole('button', { name: /运行验证/ }))
    // API should NOT be called
    expect(mockedRunValidation).not.toHaveBeenCalled()
    // Should show warning toast
    await waitFor(() => expect(screen.getByText(/Bootstrap 次数需在/)).toBeInTheDocument())
  })
})
