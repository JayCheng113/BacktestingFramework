/** K 线 OHLCV 数据（单根 K 线） */
export interface KlineBar {
  /** 交易日期 (YYYY-MM-DD) */
  date: string; open: number; high: number; low: number;
  /** 收盘价（未复权） */
  close: number;
  /** 复权收盘价，因子层使用此字段 */
  adj_close: number;
  /** 成交量（股数） */
  volume: number;
}

/** 单股回测结果 */
export interface BacktestResult {
  /** 回测指标键值对（sharpe_ratio / max_drawdown 等） */
  metrics: Record<string, number>;
  /** 策略净值曲线（逐日） */
  equity_curve: number[];
  /** 基准净值曲线（逐日，与 equity_curve 等长） */
  benchmark_curve: number[];
  trades: TradeRecord[];
  /** Bootstrap 显著性检验结果 */
  significance: {
    sharpe_ci_lower: number;
    sharpe_ci_upper: number;
    /** 单侧 p 值 */
    p_value: number;
    is_significant: boolean;
  };
}

/** 单笔成交记录 */
export interface TradeRecord {
  entry_time: string; exit_time: string; entry_price: number;
  exit_price: number;
  /** 绝对盈亏（元） */
  pnl: number;
  /** 盈亏百分比（0.05 = 5%） */
  pnl_pct: number;
  /** 单边佣金（含印花税） */
  commission: number;
}

export type StrategyParamValue = number | string | boolean | string[]

export interface StrategyInfo {
  name: string; key: string; parameters: Record<string, StrategyParamValue>; description?: string;
}

/** Walk-forward 验证结果（单股） */
export interface WalkForwardResult {
  /** 样本外平均指标 */
  oos_metrics: Record<string, number>;
  /** 过拟合评分（0–1，越高越可疑） */
  overfitting_score: number;
  /** IS→OOS 夏普衰减幅度（正值表示退化） */
  is_vs_oos_degradation: number;
  /** 折数 */
  n_splits: number;
  /** 样本外拼接净值曲线 */
  oos_equity_curve: number[];
}

export interface SymbolInfo {
  symbol: string
  name: string
  area?: string
  industry?: string
}

/** 单股因子评估结果（IC / ICIR / 衰减 / 换手） */
export interface FactorResult {
  /** 均值 IC（Pearson 相关） */
  ic_mean: number;
  /** 均值 Rank IC（Spearman 相关） */
  rank_ic_mean: number;
  /** IC 信息比率（IC 均值 / IC 标准差） */
  icir: number;
  rank_icir: number;
  /** IC 衰减曲线（lag → IC 均值） */
  ic_decay: Record<string, number>;
  /** 因子换手率（相邻期信号变化均值） */
  turnover: number;
  /** 逐期 IC 序列 */
  ic_series: number[];
  rank_ic_series: number[];
}

/** 实验运行记录（单股回测历史表中的一行） */
export interface ExperimentRun {
  run_id: string
  /** 参数哈希（相同参数组合共享同一 spec_id） */
  spec_id: string
  /** 运行状态：ok / error / running */
  status: string
  created_at: string
  /** 实际耗时（毫秒） */
  duration_ms: number
  /** 策略代码版本 commit 摘要 */
  code_commit: string
  strategy_name: string
  symbol: string
  market: string
  sharpe_ratio: number | null
  total_return: number | null
  max_drawdown: number | null
  trade_count: number
  win_rate: number | null
  /** 总盈利 / 总亏损之比 */
  profit_factor: number | null
  /** Bootstrap 显著性 p 值 */
  p_value: number | null
  is_significant: boolean
  /** Walk-forward 样本外夏普 */
  oos_sharpe: number | null
  /** 过拟合评分（0–1） */
  overfitting_score: number | null
  /** 是否通过所有部署门控 */
  gate_passed: boolean
  gate_summary: string
  gate_reasons: string | GateReason[]
  error: string | null
  start_date?: string
  end_date?: string
}

export interface GateReason {
  rule: string
  passed: boolean
  value: number
  threshold: number
  message: string
}

/** 参数搜索中的单候选结果 */
export interface CandidateResult {
  /** 参数组合哈希 */
  spec_id: string
  /** 该候选对应的参数值字典 */
  params: Record<string, number>
  sharpe: number | null
  total_return: number | null
  max_drawdown: number | null
  trade_count: number
  /** 是否通过部署门控 */
  gate_passed: boolean
  run_id: string | null
  p_value?: number | null
  /** FDR 校正后的 p 值（多重检验校正） */
  fdr_adjusted_p?: number | null
  /** 经 FDR 校正后是否显著 */
  fdr_significant?: boolean
}

/** 参数搜索完整结果汇总 */
export interface SearchResult {
  /** 参数空间总候选数 */
  total_specs: number
  /** 预过滤后剩余数（去重+缓存命中减少实际运行量） */
  prefiltered: number
  /** 实际执行回测数 */
  executed: number
  /** 跳过的重复 spec 数 */
  duplicates: number
  /** 通过门控的候选数 */
  passed_count: number
  /** 按 sharpe 降序排列的候选列表 */
  ranked: CandidateResult[]
}

// V2.12+ Portfolio types

/** 组合回测指标（对应 PortfolioEngine 计算输出） */
export interface PortfolioMetrics {
  total_return?: number; annualized_return?: number; sharpe_ratio?: number
  sortino_ratio?: number; max_drawdown?: number; max_drawdown_duration?: number
  benchmark_return?: number; alpha?: number; beta?: number
  trade_count?: number; turnover_per_rebalance?: number
  annualized_volatility?: number; n_rebalances?: number
  concentration_hhi?: number
}

export interface RiskEvent { date: string; event: string }

export interface BrinsonPeriod {
  start: string; end: string
  allocation: number; selection: number; interaction: number; total_excess: number
}

export interface AttributionResult {
  cumulative: { allocation: number; selection: number; interaction: number; total_excess: number } | null
  cost_drag: number
  by_industry: Record<string, { allocation: number; selection: number; interaction: number }>
  periods: BrinsonPeriod[]
}

export interface ActiveWeight { portfolio: number; benchmark: number; active: number }

export interface PortfolioTrade {
  date: string
  symbol: string
  side: 'buy' | 'sell' | string
  shares: number
  price: number
  cost: number
}

/** 组合回测完整结果（含净值曲线、交易记录、权重历史、归因） */
export interface PortfolioRunResult {
  run_id: string; metrics: PortfolioMetrics; equity_curve: number[]
  benchmark_curve: number[]; dates: string[]; trades: PortfolioTrade[]; rebalance_dates: string[]
  /** 成功拉取数据的股票数量 */
  symbols_fetched?: number;
  /** 因数据缺失而跳过的股票列表 */
  symbols_skipped?: string[]
  weights_history?: { date: string; weights: Record<string, number> }[]
  /** 最新一次调仓的目标权重（或末日持仓快照） */
  latest_weights?: Record<string, number>
  // V2.12.2 codex: when true, the backtest ended with final liquidation
  // (all positions sold). `latest_weights` in this case is the last
  // pre-liquidation daily snapshot (drift-adjusted actual holdings on
  // the last trading day before the T+1 force close), NOT a rebalance
  // target. UI labels the pie chart accordingly to avoid misleading
  // users about the nature of the data shown.
  terminal_liquidated?: boolean
  warnings?: string[] | null
  risk_events?: RiskEvent[]
  attribution?: AttributionResult
  active_weights?: Record<string, ActiveWeight>
}

export interface HistoryRun {
  run_id: string; strategy_name: string; start_date: string; end_date: string
  freq: string; metrics: PortfolioMetrics; trade_count: number; created_at: string
  // V2.12.2 codex: config summary + warning count from list_runs so the
  // history page surfaces optimizer/risk/market/warning-count without a
  // per-row drill into detail endpoint.
  config_summary?: {
    market?: string
    optimizer?: string
    risk_control?: boolean
    index_benchmark?: string
  }
  warning_count?: number
}

export interface ParamSchema {
  type: string; default: number | string | boolean; min?: number; max?: number; label?: string
  step?: number; options?: string[]
}

// V2.13.2 Phase 6: ML Alpha Diagnostics types
export interface MLDiagnosticsRequest {
  ml_alpha_name: string
  symbols: string[]
  market?: string
  start_date?: string
  end_date?: string
  eval_freq?: string
  forward_horizon?: number
  severe_overfit_threshold?: number
  mild_overfit_threshold?: number
  high_turnover_threshold?: number
  top_n_for_turnover?: number
}

export interface DiagnosticsResult {
  feature_importance: Record<string, (number | null)[]>
  feature_importance_cv: Record<string, number | null>
  ic_series: Array<{ retrain_date: string; train_ic: number | null; oos_ic: number | null }>
  mean_train_ic: number | null
  mean_oos_ic: number | null
  overfitting_score: number | null
  turnover_series: Array<{ date: string; retention_rate: number }>
  avg_turnover: number
  retrain_dates: string[]
  retrain_count: number
  expected_retrain_freq: number
  actual_avg_gap_days: number
  verdict: 'healthy' | 'mild_overfit' | 'severe_overfit' | 'unstable' | 'insufficient_data'
  warnings: string[]
}


// V2.22 — Unified OOS Validation
export interface ValidationRequest {
  run_id: string
  baseline_run_id?: string | null
  n_bootstrap?: number
  block_size?: number
  n_trials?: number
  seed?: number
}

export interface SignificanceResult {
  observed_sharpe: number
  ci_lower: number
  ci_upper: number
  p_value: number
  n_bootstrap: number
  block_size: number
}

export interface DeflatedResult {
  sharpe: number
  deflated_sharpe: number
  expected_max_sr: number
  skew: number
  kurt: number
  excess_kurt?: number
  warning?: string
}

export interface MinBtlResult {
  actual_years: number
  min_btl_years: number | null
}

export interface AnnualYear {
  year: number
  sharpe: number
  ret: number
  mdd: number
  n_days: number
}

export interface AnnualResult {
  per_year: AnnualYear[]
  worst_year: number | null
  best_year: number | null
  profitable_ratio: number
  consistency_score: number
}

export interface WalkForwardAggregate {
  degradation?: number
  oos_sharpe?: number
  avg_is_sharpe?: number
  overfitting_score?: number
  [key: string]: unknown
}

/**
 * V2.23.2: Discriminated union for comparison results.
 * Backend always returns a comparison object when baseline_run_id is
 * provided — either a success payload OR an explicit error. Frontend
 * picks the render path via `status`.
 */
export interface ComparisonSuccess {
  status: 'success'
  treatment_run_id: string
  control_run_id: string
  sharpe_diff: number
  ci_lower: number
  ci_upper: number
  p_value: number
  is_significant: boolean
  ci_excludes_zero: boolean
  treatment_metrics: Record<string, number>
  control_metrics: Record<string, number>
  n_observations: number
}

export interface ComparisonError {
  status: 'error'
  treatment_run_id: string
  control_run_id: string
  error: string
}

export type ComparisonResult = ComparisonSuccess | ComparisonError

export interface VerdictCheck {
  name: string
  status: 'pass' | 'warn' | 'fail'
  reason: string
  value: unknown
}

export interface VerdictResult {
  result: 'pass' | 'warn' | 'fail'
  passed: number
  warned: number
  failed: number
  total: number
  checks: VerdictCheck[]
  summary: string
  badge_color?: string  // V2.23.2: 'green' | 'amber' | 'red' (separate from summary)
  badge_emoji?: string  // V2.23.2: 🟢 / 🟡 / 🔴 (optional, frontend can ignore)
}

export interface ValidationResult {
  run_id: string
  baseline_run_id: string | null
  significance: SignificanceResult
  deflated: DeflatedResult | null
  min_btl: MinBtlResult
  annual: AnnualResult
  walk_forward: WalkForwardAggregate | null
  comparison: ComparisonResult | null
  verdict: VerdictResult
}


// V2.24: Multi-sleeve weight optimization
export type OptimizeMode = 'nested' | 'walk_forward'

export type ObjectiveName = 'MaxSharpe' | 'MaxCalmar' | 'MaxSortino' | 'MinCVaR'

export interface OptimizeWeightsRequest {
  run_ids: string[]
  labels?: string[] | null
  mode: OptimizeMode
  is_window?: [string, string] | null
  oos_window?: [string, string] | null
  n_splits?: number
  train_ratio?: number
  objectives: ObjectiveName[]
  baseline_weights?: Record<string, number> | null
  cvar_alpha?: number
  seed?: number
  max_iter?: number
}

export interface OptimizerCandidate {
  objective: string
  weights: Record<string, number>
  is_metrics: Record<string, number>
  oos_metrics: Record<string, number>
  status: 'converged' | 'max_iter' | 'infeasible'
}

export interface NestedOOSResults {
  is_window: [string, string]
  oos_window: [string, string]
  candidates: OptimizerCandidate[]
  baseline_is: Record<string, number> | null
  baseline_oos: Record<string, number> | null
}

export interface WalkForwardFold {
  fold: number
  is_window: [string, string]
  oos_window: [string, string]
  candidates: OptimizerCandidate[]
  baseline_is: Record<string, number> | null
  baseline_oos: Record<string, number> | null
}

export interface WalkForwardResults {
  n_splits: number
  train_ratio: number
  n_folds_completed: number
  folds: WalkForwardFold[]
  aggregate: {
    oos_sharpe?: number
    oos_return?: number
    oos_vol?: number
    oos_mdd?: number
    avg_is_sharpe?: number
    is_sharpe?: number
    degradation?: number
    baseline_oos_sharpe?: number
    baseline_oos_return?: number
  }
}

// Review S9: discriminated union via `mode`. TS can narrow access without
// non-null assertions. Adding a third mode in future = add a variant.
interface OptimizeBase {
  labels: string[]
  n_observations: number
  date_range: [string, string]
}

export type OptimizeWeightsResponse =
  | (OptimizeBase & { mode: 'nested'; nested_oos_results: NestedOOSResults })
  | (OptimizeBase & { mode: 'walk_forward'; walk_forward_results: WalkForwardResults })
