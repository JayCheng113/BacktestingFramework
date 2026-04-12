/**
 * ValidationPanel — V2.22 Phase 2
 *
 * Embedded in PortfolioRunContent after backtest results. Shows the full
 * OOS validation suite for a single run_id:
 *   1. Verdict banner (pass/warn/fail + summary + per-check badges)
 *   2. Walk-Forward section (IS vs OOS Sharpe bar + degradation + overfit score)
 *   3. Significance section (Bootstrap CI bar + Monte Carlo p-value + DSR + MinBTL)
 *   4. Annual breakdown (per-year Sharpe bar chart)
 *
 * Deferred to Phase 2.1: paired comparison (baseline selector), report export.
 */
import { useEffect, useRef, useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { AxiosError } from 'axios'
import { runValidation } from '../api'
import type {
  ValidationResult,
  VerdictCheck,
  AnnualYear,
} from '../types'
import { CHART } from './shared/chartTheme'
import { useToast } from './shared/Toast'
import {
  rateDegradation,
  rateOverfit,
  ratePValue,
  rateDsr,
  rateMinBtl,
  type ValidationStatus,
} from './shared/metricRatings'

interface Props {
  runId: string
}

const VERDICT_COLOR: Record<'pass' | 'warn' | 'fail', string> = {
  pass: CHART.success,
  warn: CHART.warn,
  fail: CHART.error,
}

const VERDICT_LABEL: Record<'pass' | 'warn' | 'fail', string> = {
  pass: '通过',
  warn: '警告',
  fail: '不通过',
}

export function ValidationPanel({ runId }: Props) {
  const toast = useToast()
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ValidationResult | null>(null)
  const [nBootstrap, setNBootstrap] = useState(2000)
  const [blockSize, setBlockSize] = useState(21)
  const tokenRef = useRef(0)

  // I2: clear stale result and invalidate in-flight request when run switches
  useEffect(() => {
    tokenRef.current += 1
    setResult(null)
    setLoading(false)
  }, [runId])

  const handleRun = async () => {
    if (!runId) return
    // I4: input precheck (browser min/max only enforced on submit, not keystroke)
    if (nBootstrap < 100 || nBootstrap > 10000) {
      toast.showToast('warning', 'Bootstrap 次数需在 100-10000 之间')
      return
    }
    if (blockSize < 1 || blockSize > 252) {
      toast.showToast('warning', '块大小需在 1-252 之间')
      return
    }
    const token = ++tokenRef.current
    setLoading(true)
    setResult(null)
    try {
      const resp = await runValidation({
        run_id: runId,
        n_bootstrap: nBootstrap,
        block_size: blockSize,
      })
      if (tokenRef.current !== token) return  // superseded
      setResult(resp.data)
      toast.showToast('success', `验证完成: ${VERDICT_LABEL[resp.data.verdict.result]}`)
    } catch (e: unknown) {
      if (tokenRef.current !== token) return
      // I3: extract backend detail from Axios error (422/404 responses)
      let msg = '验证失败'
      if (e instanceof AxiosError && e.response?.data?.detail) {
        const detail = e.response.data.detail
        msg = typeof detail === 'string' ? detail : JSON.stringify(detail)
      } else if (e instanceof Error) {
        msg = e.message
      }
      toast.showToast('error', msg)
    } finally {
      if (tokenRef.current === token) setLoading(false)
    }
  }

  // I5: dynamic duration hint scaled to n_bootstrap (~2000 iters/sec on typical data)
  const estimatedSeconds = Math.max(1, Math.ceil(nBootstrap / 2000))

  return (
    <div style={{
      marginTop: 24,
      padding: 16,
      border: `1px solid ${CHART.border}`,
      borderRadius: 8,
      backgroundColor: CHART.bgSecondary,
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 12,
      }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
          策略综合验证
        </h3>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={{ fontSize: 12, color: CHART.textSecondary }}>
            Bootstrap 次数
            <input
              type="number"
              min={100}
              max={10000}
              step={100}
              value={nBootstrap}
              onChange={(e) => setNBootstrap(Number(e.target.value))}
              style={{
                width: 70,
                marginLeft: 6,
                padding: '2px 6px',
                backgroundColor: CHART.bg,
                color: CHART.text,
                border: `1px solid ${CHART.border}`,
                borderRadius: 4,
              }}
              disabled={loading}
            />
          </label>
          <label style={{ fontSize: 12, color: CHART.textSecondary }}>
            块大小
            <input
              type="number"
              min={1}
              max={252}
              value={blockSize}
              onChange={(e) => setBlockSize(Number(e.target.value))}
              style={{
                width: 50,
                marginLeft: 6,
                padding: '2px 6px',
                backgroundColor: CHART.bg,
                color: CHART.text,
                border: `1px solid ${CHART.border}`,
                borderRadius: 4,
              }}
              disabled={loading}
            />
          </label>
          <button
            onClick={handleRun}
            disabled={loading || !runId}
            style={{
              padding: '6px 14px',
              backgroundColor: loading ? CHART.textSecondary : CHART.accent,
              color: '#fff',
              border: 'none',
              borderRadius: 4,
              cursor: loading || !runId ? 'not-allowed' : 'pointer',
              fontSize: 13,
            }}
          >
            {loading ? '验证中...' : '运行验证'}
          </button>
        </div>
      </div>

      {!result && !loading && (
        <div style={{ fontSize: 13, color: CHART.textSecondary, padding: '12px 0' }}>
          点击"运行验证"执行完整 OOS 检验: Bootstrap CI, Monte Carlo 显著性,
          Deflated Sharpe, 最小回测长度, 年度稳定性 + 综合裁决.
          {'\n'}如果已运行过前推验证 (Walk-Forward), 结果会包含过拟合度与降解率分析.
        </div>
      )}

      {loading && (
        <div style={{ textAlign: 'center', padding: 24, color: CHART.textSecondary }}>
          正在运行 bootstrap 重采样 ({nBootstrap} 次) · 块大小 {blockSize} · 预计 ~{estimatedSeconds} 秒 ...
        </div>
      )}

      {result && <ValidationResultView result={result} />}
    </div>
  )
}

function ValidationResultView({ result }: { result: ValidationResult }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <VerdictBanner verdict={result.verdict} />
      {result.walk_forward && <WalkForwardSection wf={result.walk_forward} />}
      <SignificanceSection
        significance={result.significance}
        deflated={result.deflated}
        minBtl={result.min_btl}
      />
      {result.annual.per_year.length > 0 && (
        <AnnualSection annual={result.annual} />
      )}
    </div>
  )
}

function VerdictBanner({ verdict }: { verdict: ValidationResult['verdict'] }) {
  const color = VERDICT_COLOR[verdict.result]
  return (
    <div style={{
      padding: 14,
      borderLeft: `4px solid ${color}`,
      backgroundColor: CHART.bg,
      borderRadius: 4,
    }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 8 }}>
        <span style={{
          padding: '4px 12px',
          backgroundColor: color,
          color: '#fff',
          fontWeight: 700,
          fontSize: 14,
          borderRadius: 4,
        }}>
          综合裁决: {VERDICT_LABEL[verdict.result]}
        </span>
        <span style={{ fontSize: 13, color: CHART.textSecondary }}>
          通过 {verdict.passed}/{verdict.total} · 警告 {verdict.warned} · 不通过 {verdict.failed}
        </span>
      </div>
      <div style={{ fontSize: 13, color: CHART.text, lineHeight: 1.5 }}>
        {verdict.summary}
      </div>
      {verdict.checks.length > 0 && (
        <div style={{
          marginTop: 10,
          display: 'flex',
          flexWrap: 'wrap',
          gap: 6,
        }}>
          {verdict.checks.map((c, i) => (
            <CheckBadge key={i} check={c} />
          ))}
        </div>
      )}
    </div>
  )
}

function CheckBadge({ check }: { check: VerdictCheck }) {
  const color = VERDICT_COLOR[check.status]
  const icon = check.status === 'pass' ? '✓' : check.status === 'warn' ? '⚠' : '✗'
  return (
    <div
      title={check.reason}
      style={{
        padding: '3px 8px',
        fontSize: 11,
        backgroundColor: CHART.bgSecondary,
        border: `1px solid ${color}`,
        borderRadius: 3,
        color: CHART.text,
        cursor: 'help',
      }}
    >
      <span style={{ color, marginRight: 4 }}>{icon}</span>
      {check.name}
    </div>
  )
}

// ============================================================
// Walk-Forward section
// ============================================================

function WalkForwardSection({
  wf,
}: { wf: NonNullable<ValidationResult['walk_forward']> }) {
  const degradation = typeof wf.degradation === 'number' ? wf.degradation : undefined
  const oosSharpe = typeof wf.oos_sharpe === 'number' ? wf.oos_sharpe : undefined
  const avgIsSharpe = typeof wf.avg_is_sharpe === 'number' ? wf.avg_is_sharpe : undefined
  const overfit = typeof wf.overfitting_score === 'number' ? wf.overfitting_score : undefined

  return (
    <Section title="Walk-Forward 验证">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        <MetricCard label="IS Sharpe (均)" value={avgIsSharpe} fmt="num" />
        <MetricCard label="OOS Sharpe" value={oosSharpe} fmt="num" />
        <MetricCard
          label="降解率"
          value={degradation}
          fmt="pct"
          rating={rateDegradation(degradation)}
        />
        <MetricCard
          label="过拟合分数"
          value={overfit}
          fmt="num"
          rating={rateOverfit(overfit)}
        />
      </div>
      {avgIsSharpe !== undefined && oosSharpe !== undefined && (
        <div style={{ marginTop: 12 }}>
          <ReactECharts
            style={{ height: 180 }}
            option={{
              backgroundColor: 'transparent',
              tooltip: { trigger: 'axis' },
              legend: {
                data: ['IS', 'OOS'],
                textStyle: { color: CHART.text },
              },
              grid: { left: 50, right: 20, top: 30, bottom: 30 },
              xAxis: {
                type: 'category',
                data: ['Walk-Forward'],
                axisLabel: { color: CHART.textSecondary },
              },
              yAxis: {
                type: 'value',
                axisLabel: { color: CHART.textSecondary },
                splitLine: { lineStyle: { color: CHART.grid } },
              },
              series: [
                {
                  name: 'IS',
                  type: 'bar',
                  data: [avgIsSharpe],
                  itemStyle: { color: CHART.isIc },
                },
                {
                  name: 'OOS',
                  type: 'bar',
                  data: [oosSharpe],
                  itemStyle: { color: CHART.oosIc },
                },
              ],
            }}
          />
        </div>
      )}
    </Section>
  )
}

// ============================================================
// Significance section
// ============================================================

function SignificanceSection({
  significance,
  deflated,
  minBtl,
}: {
  significance: ValidationResult['significance']
  deflated: ValidationResult['deflated']
  minBtl: ValidationResult['min_btl']
}) {
  const ciExcludesZero =
    significance.ci_lower > 0 || significance.ci_upper < 0

  return (
    <Section title="统计显著性">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        <MetricCard
          label="观察 Sharpe"
          value={significance.observed_sharpe}
          fmt="num"
        />
        <MetricCard
          label="p-value"
          value={significance.p_value}
          fmt="num"
          rating={ratePValue(significance.p_value)}
        />
        <MetricCard
          label="Deflated Sharpe"
          value={deflated?.deflated_sharpe}
          fmt="num"
          rating={rateDsr(deflated?.deflated_sharpe)}
        />
        <MetricCard
          label="MinBTL"
          value={
            minBtl.min_btl_years !== null
              ? `${minBtl.actual_years.toFixed(1)}y / ${minBtl.min_btl_years.toFixed(1)}y`
              : '—'
          }
          fmt="str"
          rating={rateMinBtl(minBtl.actual_years, minBtl.min_btl_years)}
        />
      </div>

      {/* CI interval bar */}
      <div style={{ marginTop: 14 }}>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 12,
          color: CHART.textSecondary,
          marginBottom: 6,
        }}>
          <span>Bootstrap 95% CI</span>
          <span style={{ color: ciExcludesZero ? CHART.success : CHART.error }}>
            {ciExcludesZero ? '不含 0 ✓' : '包含 0 ✗'}
          </span>
        </div>
        <CIBar
          lower={significance.ci_lower}
          upper={significance.ci_upper}
          observed={significance.observed_sharpe}
        />
      </div>

      {deflated?.warning && (
        <div style={{
          marginTop: 10,
          padding: 8,
          fontSize: 12,
          color: CHART.warn,
          backgroundColor: CHART.bg,
          borderRadius: 4,
        }}>
          ⚠ {deflated.warning}
        </div>
      )}
    </Section>
  )
}

function CIBar({
  lower,
  upper,
  observed,
}: {
  lower: number
  upper: number
  observed: number
}) {
  // C1: widen floor so narrow CIs near zero remain visible. The CI width
  // itself contributes so tight CIs (e.g. 0.001..0.003) don't collapse to
  // 1% of the bar. Minimum range of 0.5 ensures typical Sharpe values render.
  const ciWidth = Math.abs(upper - lower)
  const range = Math.max(
    Math.abs(lower), Math.abs(upper), Math.abs(observed),
    ciWidth * 2, 0.5,
  ) * 1.2
  const scale = (v: number) => ((v + range) / (2 * range)) * 100
  const zeroPct = scale(0)
  const lowerPct = scale(lower)
  const upperPct = scale(upper)
  const obsPct = scale(observed)
  const ciColor = lower > 0 || upper < 0 ? CHART.success : CHART.warn
  return (
    <div style={{
      position: 'relative',
      height: 36,
      backgroundColor: CHART.bg,
      borderRadius: 4,
      border: `1px solid ${CHART.border}`,
    }}>
      {/* Zero line */}
      <div style={{
        position: 'absolute',
        left: `${zeroPct}%`,
        top: 0,
        bottom: 0,
        width: 1,
        backgroundColor: CHART.textSecondary,
      }} />
      {/* CI interval */}
      <div style={{
        position: 'absolute',
        left: `${lowerPct}%`,
        width: `${upperPct - lowerPct}%`,
        top: 14,
        height: 8,
        backgroundColor: ciColor,
        opacity: 0.35,
        borderRadius: 2,
      }} />
      {/* Observed marker */}
      <div style={{
        position: 'absolute',
        left: `calc(${obsPct}% - 4px)`,
        top: 8,
        width: 8,
        height: 20,
        backgroundColor: ciColor,
        borderRadius: 2,
      }} />
      {/* Labels */}
      <div style={{
        position: 'absolute',
        left: 4,
        bottom: -16,
        fontSize: 10,
        color: CHART.textSecondary,
      }}>
        {lower.toFixed(2)}
      </div>
      <div style={{
        position: 'absolute',
        left: `calc(${obsPct}% - 14px)`,
        top: -16,
        fontSize: 10,
        color: CHART.text,
        fontWeight: 600,
      }}>
        {observed.toFixed(2)}
      </div>
      <div style={{
        position: 'absolute',
        right: 4,
        bottom: -16,
        fontSize: 10,
        color: CHART.textSecondary,
      }}>
        {upper.toFixed(2)}
      </div>
    </div>
  )
}

// ============================================================
// Annual breakdown
// ============================================================

function AnnualSection({ annual }: { annual: ValidationResult['annual'] }) {
  const data = annual.per_year
  const years = data.map((y) => String(y.year))
  const sharpes = data.map((y) => y.sharpe)
  const n = data.length
  // C2: compute count locally to avoid round(ratio * n) inconsistency
  const nProfitable = data.filter((y) => y.ret > 0).length
  const profitablePct = n > 0 ? (nProfitable / n) * 100 : 0

  return (
    <Section title="年度稳定性">
      <div style={{
        display: 'flex',
        gap: 20,
        marginBottom: 10,
        fontSize: 13,
        color: CHART.textSecondary,
      }}>
        <span>
          盈利年份: <strong style={{ color: CHART.text }}>
            {nProfitable}/{n} ({profitablePct.toFixed(0)}%)
          </strong>
        </span>
        {annual.best_year !== null && (
          <span>
            最好年份: <strong style={{ color: CHART.success }}>{annual.best_year}</strong>
          </span>
        )}
        {annual.worst_year !== null && (
          <span>
            最差年份: <strong style={{ color: CHART.error }}>{annual.worst_year}</strong>
          </span>
        )}
      </div>
      <ReactECharts
        style={{ height: 200 }}
        option={{
          backgroundColor: 'transparent',
          tooltip: {
            trigger: 'axis',
            formatter: (params: Array<{ dataIndex: number }>) => {
              const idx = params[0]?.dataIndex ?? 0
              const y = data[idx] as AnnualYear
              return `${y.year}<br/>Sharpe: ${y.sharpe.toFixed(2)}<br/>收益: ${(y.ret * 100).toFixed(1)}%<br/>回撤: ${(y.mdd * 100).toFixed(1)}%<br/>天数: ${y.n_days}`
            },
          },
          grid: { left: 50, right: 20, top: 20, bottom: 30 },
          xAxis: {
            type: 'category',
            data: years,
            axisLabel: { color: CHART.textSecondary },
          },
          yAxis: {
            type: 'value',
            axisLabel: { color: CHART.textSecondary },
            splitLine: { lineStyle: { color: CHART.grid } },
          },
          series: [
            {
              type: 'bar',
              data: sharpes.map((s) => ({
                value: s,
                itemStyle: {
                  color: s >= 0.5 ? CHART.success : s >= 0 ? CHART.warn : CHART.error,
                },
              })),
            },
          ],
        }}
      />
    </Section>
  )
}

// ============================================================
// Shared UI primitives
// ============================================================

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 style={{
        fontSize: 14,
        fontWeight: 600,
        color: CHART.text,
        borderBottom: `1px solid ${CHART.border}`,
        paddingBottom: 6,
        marginBottom: 10,
        marginTop: 0,
      }}>
        {title}
      </h4>
      {children}
    </div>
  )
}


function MetricCard({
  label,
  value,
  fmt,
  rating,
}: {
  label: string
  value: number | string | undefined | null
  fmt: 'num' | 'pct' | 'str'
  rating?: ValidationStatus
}) {
  let display: string
  if (value === undefined || value === null) {
    display = '—'
  } else if (typeof value === 'string') {
    display = value
  } else if (fmt === 'pct') {
    display = `${(value * 100).toFixed(1)}%`
  } else {
    display = value.toFixed(3)
  }
  const color = rating ? VERDICT_COLOR[rating] : CHART.text
  return (
    <div style={{
      padding: 10,
      backgroundColor: CHART.bg,
      border: `1px solid ${CHART.border}`,
      borderRadius: 4,
    }}>
      <div style={{
        fontSize: 11,
        color: CHART.textSecondary,
        marginBottom: 4,
      }}>
        {label}
      </div>
      <div style={{ fontSize: 16, fontWeight: 600, color }}>
        {display}
      </div>
    </div>
  )
}

