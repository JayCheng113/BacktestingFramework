/**
 * SleeveOptimizationPanel — V2.24 multi-sleeve weight optimization UI
 *
 * 用户流: 从历史 run 列表勾选 2-5 个作为 sleeve → 配置目标/模式 → 跑优化
 * → 看每个 objective 的最优权重、IS/OOS 指标、年度分解.
 *
 * 区分于 ValidationPanel: 后者验证"单个" run; 本 Panel 是"多 run" 权重组合优化.
 *
 * 位置: PortfolioPanel 新增 sub-tab "组合优化".
 */
import { useEffect, useRef, useState } from 'react'
import { AxiosError } from 'axios'
import { listPortfolioRuns, optimizeWeights } from '../api'
import type {
  HistoryRun,
  OptimizeMode,
  ObjectiveName,
  OptimizeWeightsResponse,
  OptimizerCandidate,
  NestedOOSResults,
  WalkForwardResults,
} from '../types'
import { CHART } from './shared/chartTheme'
import { useToast } from './shared/Toast'

const OBJECTIVES: { key: ObjectiveName; label: string; desc: string }[] = [
  { key: 'MaxSharpe', label: 'Max Sharpe', desc: '最大化 Sharpe 比率' },
  { key: 'MaxCalmar', label: 'Max Calmar', desc: '最大化 年化收益 / 最大回撤' },
  { key: 'MaxSortino', label: 'Max Sortino', desc: '最大化 Sortino (下行风险调整)' },
  { key: 'MinCVaR', label: 'Min CVaR', desc: '最小化 5% 尾部损失' },
]

const STATUS_COLOR: Record<OptimizerCandidate['status'], string> = {
  converged: '#3b82f6',
  max_iter: '#f59e0b',
  infeasible: '#ef4444',
}

const STATUS_LABEL: Record<OptimizerCandidate['status'], string> = {
  converged: '已收敛',
  max_iter: '达到迭代上限',
  infeasible: '不可行',
}

export function SleeveOptimizationPanel() {
  const toast = useToast()
  const [runList, setRunList] = useState<HistoryRun[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [labels, setLabels] = useState<Record<string, string>>({})

  const [mode, setMode] = useState<OptimizeMode>('walk_forward')
  const [objectives, setObjectives] = useState<ObjectiveName[]>(['MaxSharpe', 'MaxCalmar'])
  const [nSplits, setNSplits] = useState(5)
  const [trainRatio, setTrainRatio] = useState(0.8)
  const [isStart, setIsStart] = useState('')
  const [isEnd, setIsEnd] = useState('')
  const [oosStart, setOosStart] = useState('')
  const [oosEnd, setOosEnd] = useState('')
  const [useBaseline, setUseBaseline] = useState(false)
  const [baselineWeights, setBaselineWeights] = useState<Record<string, number>>({})

  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<OptimizeWeightsResponse | null>(null)
  const tokenRef = useRef(0)

  // Load historical runs for sleeve selection
  useEffect(() => {
    listPortfolioRuns(50, 0)
      .then((r) => setRunList(r.data as HistoryRun[]))
      .catch((e) => {
        console.warn('SleeveOpt: listPortfolioRuns failed', e)
        toast.showToast('warning', '无法加载历史 run 列表')
      })
  }, [toast])

  const toggleSleeve = (runId: string) => {
    setResult(null)
    if (selectedIds.includes(runId)) {
      setSelectedIds(selectedIds.filter((id) => id !== runId))
      const { [runId]: _, ...rest } = labels  // eslint-disable-line @typescript-eslint/no-unused-vars
      setLabels(rest)
    } else {
      if (selectedIds.length >= 10) {
        toast.showToast('warning', '最多选择 10 个 sleeve')
        return
      }
      setSelectedIds([...selectedIds, runId])
      const run = runList.find((r) => r.run_id === runId)
      if (run) setLabels({ ...labels, [runId]: run.strategy_name })
    }
  }

  const toggleObjective = (obj: ObjectiveName) => {
    setResult(null)
    if (objectives.includes(obj)) {
      if (objectives.length === 1) {
        toast.showToast('warning', '至少保留 1 个优化目标')
        return
      }
      setObjectives(objectives.filter((o) => o !== obj))
    } else {
      setObjectives([...objectives, obj])
    }
  }

  const resolvedLabelsList = selectedIds.map((id) => labels[id] || id.slice(0, 8))

  const handleRun = async () => {
    if (selectedIds.length < 2) {
      toast.showToast('warning', '至少选择 2 个 sleeve')
      return
    }
    if (mode === 'nested') {
      if (!isStart || !isEnd || !oosStart || !oosEnd) {
        toast.showToast('warning', 'Nested 模式需要填写完整的 IS/OOS 日期范围')
        return
      }
      if (isStart >= isEnd || oosStart >= oosEnd) {
        toast.showToast('warning', '日期范围无效 (开始必须早于结束)')
        return
      }
      if (oosStart < isEnd) {
        toast.showToast('warning', 'OOS 开始日期必须 >= IS 结束日期')
        return
      }
    }
    // Baseline weights sum check
    if (useBaseline) {
      const sum = resolvedLabelsList.reduce(
        (s, lbl) => s + (baselineWeights[lbl] ?? 0),
        0,
      )
      if (Math.abs(sum - 1.0) > 0.05) {
        toast.showToast('warning', `基线权重之和 ${sum.toFixed(2)} 应该 ≈ 1.0`)
        return
      }
    }

    const token = ++tokenRef.current
    setLoading(true)
    setResult(null)
    try {
      const resp = await optimizeWeights({
        run_ids: selectedIds,
        labels: resolvedLabelsList,
        mode,
        is_window: mode === 'nested' ? [isStart, isEnd] : undefined,
        oos_window: mode === 'nested' ? [oosStart, oosEnd] : undefined,
        n_splits: mode === 'walk_forward' ? nSplits : undefined,
        train_ratio: mode === 'walk_forward' ? trainRatio : undefined,
        objectives,
        baseline_weights: useBaseline
          ? Object.fromEntries(
              resolvedLabelsList.map((lbl) => [lbl, baselineWeights[lbl] ?? 0]),
            )
          : undefined,
        seed: 42,
        max_iter: 150,
      })
      if (tokenRef.current !== token) return
      setResult(resp.data)
      toast.showToast('success', '组合优化完成')
    } catch (e: unknown) {
      if (tokenRef.current !== token) return
      let msg = '优化失败'
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

  // Invalidate result on any config change
  useEffect(() => {
    setResult(null)
    tokenRef.current += 1
  }, [mode, objectives.length, nSplits, trainRatio, isStart, isEnd, oosStart, oosEnd])

  return (
    <div style={{ padding: 16 }}>
      {/* ======= Header ======= */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <h3 style={{ fontSize: 17, fontWeight: 600, margin: 0, color: CHART.text }}>
            组合权重优化
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
            V2.24
          </span>
        </div>
        <p style={{
          fontSize: 12,
          color: CHART.textSecondary,
          margin: '4px 0 0 0',
          lineHeight: 1.5,
        }}>
          勾选 2-5 个历史 run 作为 sleeve → 选目标 → 选模式 (单次 IS/OOS 或 滚动 WF) →
          运行优化。回答: <strong style={{ color: CHART.text }}>这几个策略怎么组合权重最优?</strong>
        </p>
      </div>

      {/* ======= Sleeve 选择器 ======= */}
      <Section title="① 选择 Sleeve" subtitle={`已选 ${selectedIds.length} / 最少 2, 最多 10`}>
        {runList.length === 0 ? (
          <div style={{ fontSize: 12, color: CHART.textSecondary, padding: 12 }}>
            无历史 run。请先在"组合回测" tab 跑几个策略。
          </div>
        ) : (
          <div style={{
            maxHeight: 220,
            overflowY: 'auto',
            border: `1px solid ${CHART.border}`,
            borderRadius: 4,
          }}>
            {runList.map((r) => {
              const selected = selectedIds.includes(r.run_id)
              const sharpe = r.metrics?.sharpe_ratio
              const ret = r.metrics?.total_return
              return (
                <div
                  key={r.run_id}
                  onClick={() => toggleSleeve(r.run_id)}
                  style={{
                    padding: '8px 12px',
                    borderBottom: `1px solid ${CHART.border}`,
                    cursor: 'pointer',
                    backgroundColor: selected ? 'rgba(59,130,246,0.08)' : 'transparent',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    fontSize: 12,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() => {}}
                    style={{ pointerEvents: 'none' }}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ color: CHART.text, fontWeight: 500 }}>
                      {r.strategy_name}
                    </div>
                    <div style={{ color: CHART.textSecondary, fontSize: 11 }}>
                      {r.start_date} → {r.end_date} · {r.freq}
                    </div>
                  </div>
                  <div style={{
                    fontSize: 11,
                    color: CHART.textSecondary,
                    textAlign: 'right',
                    minWidth: 120,
                  }}>
                    <div>Sharpe <strong style={{ color: CHART.text }}>
                      {typeof sharpe === 'number' ? sharpe.toFixed(2) : '—'}
                    </strong></div>
                    <div>收益 <strong style={{ color: CHART.text }}>
                      {typeof ret === 'number' ? (ret * 100).toFixed(1) + '%' : '—'}
                    </strong></div>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {selectedIds.length > 0 && (
          <div style={{ marginTop: 10, fontSize: 11, color: CHART.textSecondary }}>
            自定义标签 (显示在结果里, 留空用策略名):
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
              {selectedIds.map((id) => {
                const r = runList.find((x) => x.run_id === id)
                return (
                  <input
                    key={id}
                    value={labels[id] ?? ''}
                    onChange={(e) => setLabels({ ...labels, [id]: e.target.value })}
                    placeholder={r?.strategy_name || id.slice(0, 8)}
                    style={{
                      padding: '3px 8px',
                      backgroundColor: CHART.bg,
                      color: CHART.text,
                      border: `1px solid ${CHART.border}`,
                      borderRadius: 3,
                      fontSize: 11,
                      width: 120,
                    }}
                  />
                )
              })}
            </div>
          </div>
        )}
      </Section>

      {/* ======= 目标选择 ======= */}
      <Section title="② 优化目标" subtitle="可多选, 每个 objective 独立输出一组最优权重">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {OBJECTIVES.map((obj) => {
            const selected = objectives.includes(obj.key)
            return (
              <button
                key={obj.key}
                onClick={() => toggleObjective(obj.key)}
                title={obj.desc}
                style={{
                  padding: '6px 12px',
                  fontSize: 12,
                  backgroundColor: selected ? 'rgba(59,130,246,0.15)' : CHART.bg,
                  color: selected ? '#60a5fa' : CHART.textSecondary,
                  border: `1px solid ${selected ? '#60a5fa' : CHART.border}`,
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                {obj.label}
              </button>
            )
          })}
        </div>
      </Section>

      {/* ======= 模式选择 ======= */}
      <Section title="③ 验证模式">
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          {([
            { val: 'walk_forward' as const, label: '滚动 Walk-Forward', desc: 'N 折滚动重新优化权重, 验证跨时段稳定性' },
            { val: 'nested' as const, label: '单次 IS/OOS', desc: '一次训练/测试分割, 简单直接' },
          ]).map((m) => (
            <button
              key={m.val}
              onClick={() => setMode(m.val)}
              title={m.desc}
              style={{
                padding: '8px 14px',
                fontSize: 12,
                backgroundColor: mode === m.val ? 'rgba(59,130,246,0.15)' : CHART.bg,
                color: mode === m.val ? '#60a5fa' : CHART.textSecondary,
                border: `1px solid ${mode === m.val ? '#60a5fa' : CHART.border}`,
                borderRadius: 4,
                cursor: 'pointer',
              }}
            >
              {m.label}
            </button>
          ))}
        </div>

        {mode === 'walk_forward' ? (
          <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
            <label style={{ fontSize: 12, color: CHART.textSecondary }}>
              折数
              <input
                type="number"
                min={2}
                max={20}
                value={nSplits}
                onChange={(e) => setNSplits(Number(e.target.value))}
                style={{ ...inputStyle, width: 60, marginLeft: 6 }}
              />
            </label>
            <label style={{ fontSize: 12, color: CHART.textSecondary }}>
              训练比例
              <input
                type="number"
                min={0.1}
                max={0.95}
                step={0.05}
                value={trainRatio}
                onChange={(e) => setTrainRatio(Number(e.target.value))}
                style={{ ...inputStyle, width: 70, marginLeft: 6 }}
              />
            </label>
            <span style={{ fontSize: 11, color: CHART.textSecondary }}>
              每折 {(trainRatio * 100).toFixed(0)}% 训练 + {((1 - trainRatio) * 100).toFixed(0)}% 测试
            </span>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
            <div>
              <div style={{ fontSize: 11, color: CHART.textSecondary, marginBottom: 4 }}>
                IS (样本内, 用于优化)
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <input
                  type="date"
                  value={isStart}
                  onChange={(e) => setIsStart(e.target.value)}
                  style={{ ...inputStyle, flex: 1 }}
                />
                <input
                  type="date"
                  value={isEnd}
                  onChange={(e) => setIsEnd(e.target.value)}
                  style={{ ...inputStyle, flex: 1 }}
                />
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: CHART.textSecondary, marginBottom: 4 }}>
                OOS (样本外, 用于验证)
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <input
                  type="date"
                  value={oosStart}
                  onChange={(e) => setOosStart(e.target.value)}
                  style={{ ...inputStyle, flex: 1 }}
                />
                <input
                  type="date"
                  value={oosEnd}
                  onChange={(e) => setOosEnd(e.target.value)}
                  style={{ ...inputStyle, flex: 1 }}
                />
              </div>
            </div>
          </div>
        )}
      </Section>

      {/* ======= 基线 (可选) ======= */}
      <Section title="④ 基线权重 (可选)" subtitle="和优化结果对比, 比如等权或手动设定的 benchmark">
        <label style={{ fontSize: 12, color: CHART.textSecondary, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={useBaseline}
            onChange={(e) => setUseBaseline(e.target.checked)}
          />
          启用基线对比
        </label>
        {useBaseline && (
          <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {resolvedLabelsList.map((lbl) => (
              <label key={lbl} style={{ fontSize: 11, color: CHART.textSecondary }}>
                {lbl}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={baselineWeights[lbl] ?? 0}
                  onChange={(e) => setBaselineWeights({
                    ...baselineWeights,
                    [lbl]: Number(e.target.value),
                  })}
                  style={{ ...inputStyle, width: 70, marginLeft: 6 }}
                />
              </label>
            ))}
          </div>
        )}
      </Section>

      {/* ======= 运行按钮 ======= */}
      <div style={{ marginTop: 14, textAlign: 'right' }}>
        <button
          onClick={handleRun}
          disabled={loading || selectedIds.length < 2}
          style={{
            padding: '8px 20px',
            backgroundColor: loading || selectedIds.length < 2
              ? CHART.border
              : '#3b82f6',
            color: '#fff',
            border: 'none',
            borderRadius: 4,
            cursor: loading || selectedIds.length < 2 ? 'not-allowed' : 'pointer',
            fontSize: 13,
            fontWeight: 500,
          }}
        >
          {loading ? '优化中...' : '运行优化'}
        </button>
      </div>

      {loading && (
        <div style={{ textAlign: 'center', padding: 20, color: CHART.textSecondary, fontSize: 12 }}>
          {mode === 'walk_forward'
            ? `正在跑 ${nSplits} 折滚动优化, 每折 ${objectives.length} 个 objective ...`
            : `正在跑单次优化, ${objectives.length} 个 objective ...`}
        </div>
      )}

      {/* ======= 结果 ======= */}
      {result && <OptimizeResultView result={result} />}
    </div>
  )
}


// ============================================================
// 结果展示
// ============================================================

function OptimizeResultView({ result }: { result: OptimizeWeightsResponse }) {
  return (
    <div style={{ marginTop: 20 }}>
      <div style={{
        padding: 10,
        backgroundColor: CHART.bg,
        border: `1px solid ${CHART.border}`,
        borderRadius: 4,
        fontSize: 12,
        color: CHART.textSecondary,
        marginBottom: 14,
      }}>
        {result.labels.join(' + ')}
        <span style={{ margin: '0 8px', color: CHART.border }}>|</span>
        {result.n_observations} 天对齐数据
        <span style={{ margin: '0 8px', color: CHART.border }}>|</span>
        {result.date_range[0]} → {result.date_range[1]}
      </div>

      {result.mode === 'nested' && result.nested_oos_results && (
        <NestedResultsTable nested={result.nested_oos_results} labels={result.labels} />
      )}
      {result.mode === 'walk_forward' && result.walk_forward_results && (
        <WalkForwardResultsView wf={result.walk_forward_results} labels={result.labels} />
      )}
    </div>
  )
}

function NestedResultsTable({
  nested,
  labels,
}: {
  nested: NestedOOSResults
  labels: string[]
}) {
  return (
    <div>
      <div style={{ fontSize: 12, color: CHART.textSecondary, marginBottom: 10 }}>
        IS: {nested.is_window[0]} → {nested.is_window[1]}
        <span style={{ margin: '0 8px', color: CHART.border }}>|</span>
        OOS: {nested.oos_window[0]} → {nested.oos_window[1]}
      </div>
      <CandidatesTable candidates={nested.candidates} labels={labels} />
      {(nested.baseline_is || nested.baseline_oos) && (
        <BaselineMetricsRow
          baselineIs={nested.baseline_is}
          baselineOos={nested.baseline_oos}
        />
      )}
    </div>
  )
}

function WalkForwardResultsView({
  wf,
  labels,
}: {
  wf: WalkForwardResults
  labels: string[]
}) {
  const agg = wf.aggregate
  return (
    <div>
      <div style={{
        padding: 12,
        backgroundColor: 'rgba(59,130,246,0.05)',
        border: '1px solid rgba(59,130,246,0.2)',
        borderRadius: 4,
        marginBottom: 14,
      }}>
        <div style={{ fontSize: 11, color: CHART.textSecondary, marginBottom: 6 }}>
          {wf.n_folds_completed} / {wf.n_splits} 折完成 · 训练比例 {wf.train_ratio}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
          <MetricTile label="聚合 OOS Sharpe" value={agg.oos_sharpe} fmt="num" />
          <MetricTile label="聚合 IS Sharpe" value={agg.avg_is_sharpe ?? agg.is_sharpe} fmt="num" />
          <MetricTile label="降解率" value={agg.degradation} fmt="pct" />
          <MetricTile label="OOS 最大回撤" value={agg.oos_mdd} fmt="pct" />
        </div>
        {typeof agg.baseline_oos_sharpe === 'number' && (
          <div style={{
            marginTop: 10,
            paddingTop: 10,
            borderTop: `1px dashed ${CHART.border}`,
            fontSize: 11,
            color: CHART.textSecondary,
          }}>
            基线 OOS Sharpe: <strong style={{ color: CHART.text }}>
              {agg.baseline_oos_sharpe.toFixed(3)}
            </strong>
            {typeof agg.oos_sharpe === 'number' && (
              <span style={{ marginLeft: 12 }}>
                优化 vs 基线: <strong style={{
                  color: agg.oos_sharpe > agg.baseline_oos_sharpe ? '#3b82f6' : CHART.warn,
                }}>
                  {agg.oos_sharpe > agg.baseline_oos_sharpe ? '+' : ''}
                  {(agg.oos_sharpe - agg.baseline_oos_sharpe).toFixed(3)}
                </strong>
              </span>
            )}
          </div>
        )}
      </div>

      <div style={{ fontSize: 12, color: CHART.textSecondary, marginBottom: 8 }}>
        每折结果 (展开查看):
      </div>
      {wf.folds.map((f) => (
        <details
          key={f.fold}
          style={{
            marginBottom: 8,
            backgroundColor: CHART.bg,
            border: `1px solid ${CHART.border}`,
            borderRadius: 4,
          }}
        >
          <summary style={{
            padding: '8px 12px',
            cursor: 'pointer',
            fontSize: 12,
            color: CHART.text,
          }}>
            <strong>Fold {f.fold + 1}</strong>
            <span style={{ marginLeft: 10, color: CHART.textSecondary, fontSize: 11 }}>
              IS {f.is_window[0]} → {f.is_window[1]} · OOS {f.oos_window[0]} → {f.oos_window[1]}
            </span>
          </summary>
          <div style={{ padding: 10, borderTop: `1px solid ${CHART.border}` }}>
            <CandidatesTable candidates={f.candidates} labels={labels} />
          </div>
        </details>
      ))}
    </div>
  )
}

function CandidatesTable({
  candidates,
  labels,
}: {
  candidates: OptimizerCandidate[]
  labels: string[]
}) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{
        width: '100%',
        fontSize: 11,
        borderCollapse: 'collapse',
      }}>
        <thead>
          <tr style={{ borderBottom: `2px solid ${CHART.border}`, color: CHART.textSecondary }}>
            <th style={{ textAlign: 'left', padding: 6 }}>Objective</th>
            <th style={{ textAlign: 'center', padding: 6 }}>状态</th>
            {labels.map((lbl) => (
              <th key={lbl} style={{ textAlign: 'right', padding: 6 }}>{lbl}</th>
            ))}
            <th style={{ textAlign: 'right', padding: 6 }}>IS Sharpe</th>
            <th style={{ textAlign: 'right', padding: 6 }}>OOS Sharpe</th>
            <th style={{ textAlign: 'right', padding: 6 }}>OOS MDD</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c, i) => {
            const cashWeight = 1 - labels.reduce((s, lbl) => s + (c.weights[lbl] ?? 0), 0)
            return (
              <tr key={i} style={{ borderBottom: `1px solid ${CHART.border}` }}>
                <td style={{ padding: 6, color: CHART.text, fontWeight: 500 }}>
                  {c.objective}
                  {cashWeight > 0.01 && (
                    <span style={{
                      marginLeft: 6,
                      fontSize: 10,
                      color: CHART.textSecondary,
                    }}>
                      (现金 {(cashWeight * 100).toFixed(0)}%)
                    </span>
                  )}
                </td>
                <td style={{ padding: 6, textAlign: 'center' }}>
                  <span style={{
                    padding: '1px 6px',
                    fontSize: 10,
                    backgroundColor: STATUS_COLOR[c.status] + '22',
                    color: STATUS_COLOR[c.status],
                    borderRadius: 2,
                  }}>
                    {STATUS_LABEL[c.status]}
                  </span>
                </td>
                {labels.map((lbl) => {
                  const w = c.weights[lbl] ?? 0
                  return (
                    <td
                      key={lbl}
                      style={{
                        padding: 6,
                        textAlign: 'right',
                        color: w > 0.01 ? CHART.text : CHART.textSecondary,
                        fontWeight: w > 0.3 ? 600 : 400,
                      }}
                    >
                      {(w * 100).toFixed(1)}%
                    </td>
                  )
                })}
                <td style={{ padding: 6, textAlign: 'right', color: CHART.text }}>
                  {fmtNum(c.is_metrics?.sharpe)}
                </td>
                <td style={{ padding: 6, textAlign: 'right', color: CHART.text, fontWeight: 500 }}>
                  {fmtNum(c.oos_metrics?.sharpe)}
                </td>
                <td style={{ padding: 6, textAlign: 'right', color: CHART.textSecondary }}>
                  {fmtPct(c.oos_metrics?.dd)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function BaselineMetricsRow({
  baselineIs,
  baselineOos,
}: {
  baselineIs: Record<string, number> | null
  baselineOos: Record<string, number> | null
}) {
  return (
    <div style={{
      marginTop: 12,
      padding: 10,
      backgroundColor: CHART.bg,
      border: `1px dashed ${CHART.border}`,
      borderRadius: 4,
      fontSize: 11,
      color: CHART.textSecondary,
    }}>
      <strong style={{ color: CHART.text }}>基线</strong> —
      IS Sharpe: {fmtNum(baselineIs?.sharpe)} ·
      OOS Sharpe: <strong style={{ color: CHART.text }}>{fmtNum(baselineOos?.sharpe)}</strong> ·
      OOS Return: {fmtPct(baselineOos?.ret)}
    </div>
  )
}

function MetricTile({
  label,
  value,
  fmt,
}: {
  label: string
  value: number | undefined
  fmt: 'num' | 'pct'
}) {
  return (
    <div style={{
      padding: 10,
      backgroundColor: CHART.bg,
      border: `1px solid ${CHART.border}`,
      borderRadius: 4,
    }}>
      <div style={{ fontSize: 10, color: CHART.textSecondary, marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ fontSize: 15, fontWeight: 600, color: CHART.text }}>
        {fmt === 'pct' ? fmtPct(value) : fmtNum(value)}
      </div>
    </div>
  )
}

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
    <div style={{ marginBottom: 16 }}>
      <div style={{
        display: 'flex',
        alignItems: 'baseline',
        gap: 10,
        marginBottom: 6,
        paddingBottom: 4,
        borderBottom: `1px solid ${CHART.border}`,
      }}>
        <h4 style={{ fontSize: 13, fontWeight: 600, color: CHART.text, margin: 0 }}>
          {title}
        </h4>
        {subtitle && (
          <span style={{ fontSize: 11, color: CHART.textSecondary }}>
            {subtitle}
          </span>
        )}
      </div>
      {children}
    </div>
  )
}

function fmtNum(v: number | null | undefined): string {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  return v.toFixed(3)
}

function fmtPct(v: number | null | undefined): string {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  return `${(v * 100).toFixed(1)}%`
}

const inputStyle: React.CSSProperties = {
  padding: '3px 6px',
  backgroundColor: CHART.bg,
  color: CHART.text,
  border: `1px solid ${CHART.border}`,
  borderRadius: 3,
  fontSize: 12,
}
