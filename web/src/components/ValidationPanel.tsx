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
import { runValidation, listPortfolioRuns } from '../api'
import type {
  ValidationResult,
  VerdictCheck,
  AnnualYear,
  HistoryRun,
  ComparisonResult,
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

// V2.23.2 UX 修: 用品牌蓝色做"通过"避免和 A 股"绿跌"冲突,
// 整体色系更安静, 只在警告/不通过时才用强色.
const VERDICT_COLOR: Record<'pass' | 'warn' | 'fail', string> = {
  pass: '#3b82f6',   // blue-500 — 安静的"通过", 不是 A-股绿
  warn: '#f59e0b',   // amber
  fail: '#ef4444',   // red
}

// 次要色: 用于 metric 卡片的柔和着色 (透明度降低)
const VERDICT_TINT: Record<'pass' | 'warn' | 'fail', string> = {
  pass: 'rgba(59,130,246,0.12)',
  warn: 'rgba(245,158,11,0.12)',
  fail: 'rgba(239,68,68,0.12)',
}

const VERDICT_LABEL: Record<'pass' | 'warn' | 'fail', string> = {
  pass: '通过',
  warn: '警告',
  fail: '不通过',
}

const VERDICT_ICON: Record<'pass' | 'warn' | 'fail', string> = {
  pass: '✓',
  warn: '!',
  fail: '✕',
}

export function ValidationPanel({ runId }: Props) {
  const toast = useToast()
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ValidationResult | null>(null)
  const [nBootstrap, setNBootstrap] = useState(2000)
  const [blockSize, setBlockSize] = useState(21)
  const [nTrials, setNTrials] = useState(1)  // V2.23.2 Critical 2: search count
  const [baselineId, setBaselineId] = useState('')
  const [runList, setRunList] = useState<HistoryRun[]>([])
  const tokenRef = useRef(0)

  // I2: clear stale result and invalidate in-flight request when run switches
  useEffect(() => {
    tokenRef.current += 1
    setResult(null)
    setLoading(false)
    setBaselineId('')  // Phase 2.1: clear baseline selection on run change
  }, [runId])

  // Phase 2.1: load available runs for baseline dropdown
  useEffect(() => {
    listPortfolioRuns(50, 0)
      .then(r => setRunList(r.data as HistoryRun[]))
      .catch((e) => {
        // V2.23 review I4: V2.16.1 "silent catch 清零" principle —
        // notify user why baseline dropdown is empty. Validation still
        // works without comparison.
        console.warn('ValidationPanel: listPortfolioRuns failed', e)
        toast.showToast('warning', '无法加载基线列表, 仍可进行单策略验证')
      })
  }, [toast])

  // I-1: clear stale result when user changes baseline without re-running.
  // Prevents ComparisonSection from showing data from a previous baseline.
  useEffect(() => {
    setResult(null)
    tokenRef.current += 1
  }, [baselineId])

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
        baseline_run_id: baselineId || undefined,
        n_bootstrap: nBootstrap,
        block_size: blockSize,
        n_trials: nTrials,
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
      padding: 18,
      border: `1px solid ${CHART.border}`,
      borderRadius: 8,
      backgroundColor: CHART.bgSecondary,
    }}>
      {/* Header with title + subtitle 说明做什么 */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <h3 style={{ fontSize: 17, fontWeight: 600, margin: 0, color: CHART.text }}>
            策略综合验证
          </h3>
          <span style={{
            fontSize: 10,
            padding: '2px 6px',
            backgroundColor: 'rgba(59,130,246,0.15)',
            color: '#60a5fa',
            border: '1px solid rgba(59,130,246,0.3)',
            borderRadius: 3,
            fontWeight: 500,
          }}>
            OOS 检验
          </span>
        </div>
        <p style={{
          fontSize: 12,
          color: CHART.textSecondary,
          margin: '4px 0 0 0',
          lineHeight: 1.5,
        }}>
          综合 Bootstrap 置信区间 · 去偏差 Sharpe (DSR) · 最小回测长度 (MinBTL) · 年度稳定性 · 可选基线对比,
          给出 <strong style={{ color: CHART.text }}>是否值得部署</strong> 的综合裁决。
        </p>
      </div>

      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: 12,
        flexWrap: 'wrap',
        gap: 10,
        paddingBottom: 12,
        borderBottom: `1px solid ${CHART.border}`,
      }}>
        <span style={{ fontSize: 12, color: CHART.textSecondary, fontWeight: 500 }}>
          配置参数 →
        </span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: CHART.textSecondary }}>
            对比基线
            <select
              value={baselineId}
              onChange={(e) => setBaselineId(e.target.value)}
              disabled={loading}
              style={{
                marginLeft: 6,
                padding: '2px 6px',
                backgroundColor: CHART.bg,
                color: CHART.text,
                border: `1px solid ${CHART.border}`,
                borderRadius: 4,
                maxWidth: 260,
              }}
              title="选择另一个 run 作为对比基线, 可配对 bootstrap 检验 Sharpe 差值显著性"
            >
              <option value="">(无对比)</option>
              {runList
                .filter((r) => r.run_id !== runId)
                .map((r) => {
                  const sharpe = r.metrics?.sharpe_ratio
                  const sharpeStr = typeof sharpe === 'number' ? sharpe.toFixed(2) : '—'
                  const date = r.created_at ? r.created_at.slice(0, 10) : ''
                  return (
                    <option key={r.run_id} value={r.run_id}>
                      {r.strategy_name} · {date} · Sharpe {sharpeStr}
                    </option>
                  )
                })}
            </select>
          </label>
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
          <label
            style={{ fontSize: 12, color: CHART.textSecondary }}
            title="搜过的策略/参数组合数. 影响 DSR 和 MinBTL 的多重检验惩罚. 1=未搜索, 100=搜了100组. 越大越保守."
          >
            搜索数
            <input
              type="number"
              min={1}
              max={100000}
              value={nTrials}
              onChange={(e) => setNTrials(Number(e.target.value))}
              style={{
                width: 60,
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
        <div style={{
          padding: '20px 16px',
          textAlign: 'center',
          color: CHART.textSecondary,
        }}>
          <div style={{
            fontSize: 13,
            lineHeight: 1.8,
            marginBottom: 16,
          }}>
            <div style={{ marginBottom: 4, fontWeight: 500, color: CHART.text }}>
              这个面板回答一个问题: <span style={{ color: '#60a5fa' }}>"这个策略能不能部署到模拟盘?"</span>
            </div>
            <div style={{ fontSize: 12 }}>
              点击右上角 <span style={{
                color: '#60a5fa',
                fontWeight: 500,
                padding: '1px 6px',
                border: '1px solid #60a5fa',
                borderRadius: 3,
              }}>运行验证</span> 启动 OOS 检验流程。
            </div>
          </div>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
            gap: 10,
            textAlign: 'left',
            fontSize: 11,
            maxWidth: 760,
            margin: '0 auto',
          }}>
            {[
              { step: '01', title: '统计显著性', desc: 'Bootstrap CI + p-value 判断是否非运气' },
              { step: '02', title: '去偏差 Sharpe', desc: '用 DSR 扣除多重检验带来的虚高' },
              { step: '03', title: '年度稳定性', desc: '按年分解, 检查是否依赖某个 regime' },
              { step: '04', title: '配对对比 (可选)', desc: '选基线 run 算 Sharpe 差值 CI' },
            ].map((item) => (
              <div key={item.step} style={{
                padding: 10,
                backgroundColor: CHART.bg,
                border: `1px solid ${CHART.border}`,
                borderRadius: 6,
              }}>
                <div style={{ color: '#60a5fa', fontWeight: 600, fontSize: 10, letterSpacing: 1 }}>
                  STEP {item.step}
                </div>
                <div style={{ color: CHART.text, fontWeight: 500, margin: '3px 0' }}>
                  {item.title}
                </div>
                <div style={{ color: CHART.textSecondary, lineHeight: 1.4 }}>
                  {item.desc}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {loading && (
        <div style={{ textAlign: 'center', padding: 30 }}>
          <div style={{
            display: 'inline-block',
            padding: '8px 16px',
            backgroundColor: 'rgba(59,130,246,0.1)',
            border: '1px solid rgba(59,130,246,0.3)',
            borderRadius: 4,
            color: '#60a5fa',
            fontSize: 13,
            fontWeight: 500,
          }}>
            ⟳ 运行 Bootstrap 重采样...
          </div>
          <div style={{
            fontSize: 11,
            color: CHART.textSecondary,
            marginTop: 8,
          }}>
            {nBootstrap} 次 · 块大小 {blockSize} · 预计 ~{estimatedSeconds} 秒
          </div>
        </div>
      )}

      {result && (
        <>
          {nTrials === 1 && (
            <div style={{
              marginBottom: 12,
              padding: 10,
              fontSize: 12,
              color: CHART.warn,
              backgroundColor: CHART.bg,
              border: `1px solid ${CHART.warn}`,
              borderRadius: 4,
            }}>
              ⚠ 当前裁决假设只搜索了 1 个策略 (n_trials=1). 若实际做过参数/策略搜索,
              请将 "搜索数" 设为搜索过的组合数, 以激活 DSR / MinBTL 的多重检验惩罚.
              否则裁决可能过于乐观.
            </div>
          )}
          <ValidationResultView result={result} />
        </>
      )}
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
      {result.comparison && (
        <ComparisonSection comparison={result.comparison} />
      )}
      {result.annual.per_year.length > 0 && (
        <AnnualSection annual={result.annual} />
      )}
      <ReportExportBar result={result} />
    </div>
  )
}

function VerdictBanner({ verdict }: { verdict: ValidationResult['verdict'] }) {
  const color = VERDICT_COLOR[verdict.result]
  const tint = VERDICT_TINT[verdict.result]
  return (
    <div style={{
      padding: 16,
      backgroundColor: tint,
      border: `1px solid ${color}`,
      borderLeft: `4px solid ${color}`,
      borderRadius: 6,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 10 }}>
        {/* 大号图标圆圈 */}
        <div style={{
          width: 40,
          height: 40,
          borderRadius: '50%',
          backgroundColor: color,
          color: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 20,
          fontWeight: 700,
          flexShrink: 0,
        }}>
          {VERDICT_ICON[verdict.result]}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{
            fontSize: 11,
            color: CHART.textSecondary,
            letterSpacing: 1,
            marginBottom: 2,
          }}>
            综合裁决
          </div>
          <div style={{
            fontSize: 18,
            fontWeight: 700,
            color,
          }}>
            {VERDICT_LABEL[verdict.result]}
            <span style={{
              fontSize: 12,
              fontWeight: 400,
              color: CHART.textSecondary,
              marginLeft: 10,
            }}>
              {verdict.passed}/{verdict.total} 项通过
              {verdict.warned > 0 && ` · ${verdict.warned} 项警告`}
              {verdict.failed > 0 && ` · ${verdict.failed} 项不通过`}
            </span>
          </div>
        </div>
      </div>
      <div style={{
        fontSize: 13,
        color: CHART.text,
        lineHeight: 1.6,
        paddingLeft: 54,
      }}>
        {verdict.summary}
      </div>
      {verdict.checks.length > 0 && (
        <div style={{
          marginTop: 12,
          paddingLeft: 54,
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
  return (
    <div
      title={check.reason}
      style={{
        padding: '3px 9px',
        fontSize: 11,
        backgroundColor: CHART.bg,
        border: `1px solid ${CHART.border}`,
        borderRadius: 3,
        color: CHART.textSecondary,
        cursor: 'help',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
      }}
    >
      <span style={{
        width: 14,
        height: 14,
        borderRadius: '50%',
        backgroundColor: color,
        color: '#fff',
        fontSize: 9,
        fontWeight: 700,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        lineHeight: 1,
      }}>
        {VERDICT_ICON[check.status]}
      </span>
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
    <Section title="Walk-Forward 验证" subtitle="— 样本外是否坍塌">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        <MetricCard label="聚合 IS Sharpe" value={avgIsSharpe} fmt="num" />
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
    <Section title="统计显著性" subtitle="— 是不是运气">
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
    <Section title="年度稳定性" subtitle="— 是否依赖某年 regime">
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
// Phase 2.1: Paired comparison section
// ============================================================

function ComparisonSection({ comparison }: { comparison: ComparisonResult }) {
  if (comparison.status === 'error') {
    return (
      <Section title="配对对比" subtitle="— 比基线 run 真的更好吗">
        <div style={{
          padding: 10,
          fontSize: 13,
          color: CHART.error,
          backgroundColor: CHART.bg,
          borderRadius: 4,
        }}>
          对比失败: {comparison.error}
        </div>
      </Section>
    )
  }
  const ciExcludesZero = comparison.ci_excludes_zero
  const sig = comparison.is_significant
  // V2.23.2 Important 7: headline must branch on sign, not just significance
  const diffPositive = comparison.sharpe_diff > 0
  const headlineText =
    !sig ? '差异不显著' :
    diffPositive ? '显著优于基线 ✓' :
    '显著差于基线 ✗'
  const headlineColor =
    !sig ? CHART.warn :
    diffPositive ? CHART.success :
    CHART.error
  return (
    <Section title="配对对比" subtitle="— 比基线 run 真的更好吗">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
        <MetricCard
          label="Sharpe 差值"
          value={comparison.sharpe_diff}
          fmt="num"
          rating={sig ? (comparison.sharpe_diff > 0 ? 'pass' : 'fail') : 'warn'}
        />
        <MetricCard
          label="p-value"
          value={comparison.p_value}
          fmt="num"
          rating={sig ? 'pass' : 'warn'}
        />
        <MetricCard
          label="CI 是否含 0"
          value={ciExcludesZero ? '不含 0' : '含 0'}
          fmt="str"
          rating={ciExcludesZero ? 'pass' : 'fail'}
        />
        <MetricCard
          label="观察数"
          value={comparison.n_observations}
          fmt="str"
        />
      </div>

      <div style={{ marginTop: 14 }}>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 12,
          color: CHART.textSecondary,
          marginBottom: 6,
        }}>
          <span>Sharpe 差值 95% CI</span>
          <span style={{ color: headlineColor }}>
            {headlineText}
          </span>
        </div>
        <CIBar
          lower={comparison.ci_lower}
          upper={comparison.ci_upper}
          observed={comparison.sharpe_diff}
        />
      </div>

      {/* Side-by-side metrics table */}
      <div style={{ marginTop: 18 }}>
        <table style={{
          width: '100%',
          fontSize: 12,
          borderCollapse: 'collapse',
        }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${CHART.border}` }}>
              <th style={{ textAlign: 'left', padding: 6, color: CHART.textSecondary }}>指标</th>
              <th style={{ textAlign: 'right', padding: 6, color: CHART.text }}>当前</th>
              <th style={{ textAlign: 'right', padding: 6, color: CHART.textSecondary }}>基线</th>
              <th style={{ textAlign: 'right', padding: 6, color: CHART.textSecondary }}>差值</th>
            </tr>
          </thead>
          <tbody>
            {(['sharpe', 'ret', 'vol', 'dd'] as const).map((key) => {
              const t = comparison.treatment_metrics[key]
              const c = comparison.control_metrics[key]
              const d = typeof t === 'number' && typeof c === 'number' ? t - c : null
              const fmt = (v: number | undefined) =>
                typeof v === 'number' ? v.toFixed(3) : '—'
              const label = {
                sharpe: 'Sharpe',
                ret: '年化收益',
                vol: '年化波动',
                dd: '最大回撤',
              }[key]
              return (
                <tr key={key} style={{ borderBottom: `1px solid ${CHART.border}` }}>
                  <td style={{ padding: 6 }}>{label}</td>
                  <td style={{ padding: 6, textAlign: 'right', color: CHART.text }}>{fmt(t)}</td>
                  <td style={{ padding: 6, textAlign: 'right', color: CHART.textSecondary }}>{fmt(c)}</td>
                  <td style={{
                    padding: 6,
                    textAlign: 'right',
                    color: d === null ? CHART.textSecondary : d > 0 ? CHART.success : CHART.error,
                  }}>
                    {d === null ? '—' : (d > 0 ? '+' : '') + d.toFixed(3)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Section>
  )
}

// ============================================================
// Phase 2.1: Report export (markdown generation)
// ============================================================

/**
 * I-2: Escape user-controlled strings before interpolating into markdown.
 * Strips backticks (inline-code break), pipes (table break), and newlines
 * (section break). Keeps all other unicode intact.
 */
function mdSafe(s: string | null | undefined): string {
  if (!s) return ''
  return String(s).replace(/[`|\r\n]/g, '_')
}

function generateMarkdown(result: ValidationResult): string {
  const lines: string[] = []
  const now = new Date().toISOString()
  lines.push(`# 验证报告`, '')
  lines.push(`- 主策略 run: \`${mdSafe(result.run_id)}\``)
  if (result.baseline_run_id) {
    lines.push(`- 对比基线 run: \`${mdSafe(result.baseline_run_id)}\``)
  }
  lines.push(`- 生成时间: ${now}`, '')

  // Verdict
  const v = result.verdict
  lines.push(`## 综合裁决: ${VERDICT_LABEL[v.result]}`, '')
  lines.push(`${v.summary}`, '')
  lines.push(`- 通过: ${v.passed}/${v.total}`)
  lines.push(`- 警告: ${v.warned}`)
  lines.push(`- 不通过: ${v.failed}`, '')
  lines.push('### 检验明细', '')
  lines.push('| 检验项 | 状态 | 原因 |')
  lines.push('|---|---|---|')
  for (const c of v.checks) {
    const statusText =
      c.status === 'pass' ? '✓ 通过' :
      c.status === 'warn' ? '⚠ 警告' :
      '✗ 不通过'
    const reason = c.reason.replace(/\|/g, '\\|').replace(/\n/g, ' ')
    lines.push(`| ${c.name} | ${statusText} | ${reason} |`)
  }
  lines.push('')

  // Significance
  const s = result.significance
  lines.push(`## 统计显著性`, '')
  lines.push(`- 观察 Sharpe: **${s.observed_sharpe.toFixed(3)}**`)
  lines.push(`- Bootstrap 95% CI: [${s.ci_lower.toFixed(3)}, ${s.ci_upper.toFixed(3)}]`)
  lines.push(`- p-value: ${s.p_value.toFixed(4)}`)
  if (result.deflated) {
    lines.push(`- Deflated Sharpe: ${result.deflated.deflated_sharpe.toFixed(3)}`)
    lines.push(`- 偏度: ${result.deflated.skew.toFixed(3)}, 超额峰度: ${(result.deflated.excess_kurt ?? 0).toFixed(3)}`)
  }
  if (result.min_btl.min_btl_years !== null) {
    lines.push(`- 最小回测长度: 实际 ${result.min_btl.actual_years.toFixed(1)} 年 / 需要 ${result.min_btl.min_btl_years.toFixed(1)} 年`)
  }
  lines.push('')

  // Walk-Forward
  if (result.walk_forward) {
    const wf = result.walk_forward
    lines.push(`## Walk-Forward`, '')
    if (typeof wf.avg_is_sharpe === 'number') lines.push(`- 聚合 IS Sharpe: ${wf.avg_is_sharpe.toFixed(3)}`)
    if (typeof wf.oos_sharpe === 'number') lines.push(`- OOS Sharpe: ${wf.oos_sharpe.toFixed(3)}`)
    if (typeof wf.degradation === 'number') lines.push(`- 降解率: ${(wf.degradation * 100).toFixed(1)}%`)
    if (typeof wf.overfitting_score === 'number') lines.push(`- 过拟合分数: ${wf.overfitting_score.toFixed(3)}`)
    lines.push('')
  }

  // Annual
  if (result.annual.per_year.length > 0) {
    lines.push(`## 年度稳定性`, '')
    const a = result.annual
    const nProfitable = a.per_year.filter((y) => y.ret > 0).length
    lines.push(`- 盈利年份: ${nProfitable}/${a.per_year.length} (${((a.profitable_ratio) * 100).toFixed(0)}%)`)
    lines.push(`- 正 Sharpe 年份 (consistency): ${(a.consistency_score * 100).toFixed(0)}%`)
    if (a.best_year !== null) lines.push(`- 最好年份: ${a.best_year}`)
    if (a.worst_year !== null) lines.push(`- 最差年份: ${a.worst_year}`)
    lines.push('')
    lines.push('| 年份 | Sharpe | 收益 | 最大回撤 | 天数 |')
    lines.push('|---|---|---|---|---|')
    for (const y of a.per_year) {
      lines.push(`| ${y.year} | ${y.sharpe.toFixed(2)} | ${(y.ret * 100).toFixed(1)}% | ${(y.mdd * 100).toFixed(1)}% | ${y.n_days} |`)
    }
    lines.push('')
  }

  // Comparison
  if (result.comparison) {
    const cmp = result.comparison
    lines.push(`## 配对对比 (vs 基线 \`${mdSafe(cmp.control_run_id)}\`)`, '')
    if (cmp.status === 'error') {
      lines.push(`- 状态: 失败 — ${mdSafe(cmp.error)}`)
    } else {
      const diffPositive = cmp.sharpe_diff > 0
      const verdict = !cmp.is_significant
        ? '差异不显著'
        : diffPositive ? '显著优于基线' : '显著差于基线'
      lines.push(`- Sharpe 差值: ${cmp.sharpe_diff.toFixed(3)}`)
      lines.push(`- 95% CI: [${cmp.ci_lower.toFixed(3)}, ${cmp.ci_upper.toFixed(3)}] ${cmp.ci_excludes_zero ? '(不含 0)' : '(含 0)'}`)
      lines.push(`- p-value: ${cmp.p_value.toFixed(4)} (${verdict})`)
      lines.push(`- 观察数: ${cmp.n_observations}`)
    }
    lines.push('')
  }

  return lines.join('\n')
}

function ReportExportBar({ result }: { result: ValidationResult }) {
  const toast = useToast()
  const handleCopy = async () => {
    const md = generateMarkdown(result)
    try {
      await navigator.clipboard.writeText(md)
      toast.showToast('success', '报告已复制到剪贴板')
    } catch {
      toast.showToast('error', '复制失败: 浏览器不支持剪贴板或权限被拒')
    }
  }
  const handleDownload = () => {
    const md = generateMarkdown(result)
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const dateStr = new Date().toISOString().slice(0, 10)
    // S-7: sanitize run_id for filename (/ or special chars → _)
    const safeId = result.run_id.replace(/[^a-zA-Z0-9_-]/g, '_')
    a.download = `validation-${safeId}-${dateStr}.md`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    toast.showToast('success', '报告已下载')
  }
  return (
    <div style={{
      display: 'flex',
      gap: 8,
      justifyContent: 'flex-end',
      paddingTop: 10,
      borderTop: `1px solid ${CHART.border}`,
    }}>
      <button
        onClick={handleCopy}
        style={{
          padding: '6px 14px',
          backgroundColor: 'transparent',
          color: CHART.text,
          border: `1px solid ${CHART.border}`,
          borderRadius: 4,
          cursor: 'pointer',
          fontSize: 13,
        }}
      >
        复制报告
      </button>
      <button
        onClick={handleDownload}
        style={{
          padding: '6px 14px',
          backgroundColor: CHART.accent,
          color: '#fff',
          border: 'none',
          borderRadius: 4,
          cursor: 'pointer',
          fontSize: 13,
        }}
      >
        下载 .md
      </button>
    </div>
  )
}

// ============================================================
// Shared UI primitives
// ============================================================

function Section({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div style={{
        borderBottom: `1px solid ${CHART.border}`,
        paddingBottom: 6,
        marginBottom: 10,
      }}>
        <h4 style={{
          fontSize: 14,
          fontWeight: 600,
          color: CHART.text,
          margin: 0,
          display: 'flex',
          alignItems: 'baseline',
          gap: 8,
        }}>
          {title}
          {subtitle && (
            <span style={{ fontSize: 11, fontWeight: 400, color: CHART.textSecondary }}>
              {subtitle}
            </span>
          )}
        </h4>
      </div>
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
  // UX 修: 默认值文字 = 主色 (不着色); 仅 warn/fail 染色, pass 用小徽标不染色.
  // 这样大量 pass 指标时不会满屏都是颜色.
  const valueColor = rating === 'warn' || rating === 'fail'
    ? VERDICT_COLOR[rating]
    : CHART.text
  const showBadge = rating !== undefined
  const borderColor = rating === 'warn' || rating === 'fail'
    ? VERDICT_COLOR[rating]
    : CHART.border
  return (
    <div style={{
      padding: 10,
      backgroundColor: CHART.bg,
      border: `1px solid ${borderColor}`,
      borderLeft: rating
        ? `3px solid ${VERDICT_COLOR[rating]}`
        : `1px solid ${CHART.border}`,
      borderRadius: 4,
    }}>
      <div style={{
        fontSize: 11,
        color: CHART.textSecondary,
        marginBottom: 4,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <span>{label}</span>
        {showBadge && (
          <span style={{
            width: 12,
            height: 12,
            borderRadius: '50%',
            backgroundColor: VERDICT_COLOR[rating!],
            color: '#fff',
            fontSize: 8,
            fontWeight: 700,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            lineHeight: 1,
          }}>
            {VERDICT_ICON[rating!]}
          </span>
        )}
      </div>
      <div style={{ fontSize: 16, fontWeight: 600, color: valueColor }}>
        {display}
      </div>
    </div>
  )
}

