export interface KlineBar {
  date: string; open: number; high: number; low: number;
  close: number; adj_close: number; volume: number;
}

export interface BacktestResult {
  metrics: Record<string, number>;
  equity_curve: number[];
  benchmark_curve: number[];
  trades: TradeRecord[];
  significance: {
    sharpe_ci_lower: number;
    sharpe_ci_upper: number;
    p_value: number;
    is_significant: boolean;
  };
}

export interface TradeRecord {
  entry_time: string; exit_time: string; entry_price: number;
  exit_price: number; pnl: number; pnl_pct: number; commission: number;
}

export interface StrategyInfo {
  name: string; key: string; parameters: Record<string, any>; description?: string;
}

export interface WalkForwardResult {
  oos_metrics: Record<string, number>;
  overfitting_score: number;
  is_vs_oos_degradation: number;
  n_splits: number;
  oos_equity_curve: number[];
}

export interface SymbolInfo {
  symbol: string
  name: string
  area?: string
  industry?: string
}

export interface FactorResult {
  ic_mean: number; rank_ic_mean: number; icir: number;
  rank_icir: number; ic_decay: Record<string, number>;
  turnover: number; ic_series: number[]; rank_ic_series: number[];
}

export interface ExperimentRun {
  run_id: string
  spec_id: string
  status: string
  created_at: string
  duration_ms: number
  code_commit: string
  strategy_name: string
  symbol: string
  market: string
  sharpe_ratio: number | null
  total_return: number | null
  max_drawdown: number | null
  trade_count: number
  win_rate: number | null
  profit_factor: number | null
  p_value: number | null
  is_significant: boolean
  oos_sharpe: number | null
  overfitting_score: number | null
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

export interface CandidateResult {
  spec_id: string
  params: Record<string, number>
  sharpe: number | null
  total_return: number | null
  max_drawdown: number | null
  trade_count: number
  gate_passed: boolean
  run_id: string | null
  p_value?: number | null
  fdr_adjusted_p?: number | null
  fdr_significant?: boolean
}

export interface SearchResult {
  total_specs: number
  prefiltered: number
  executed: number
  duplicates: number
  passed_count: number
  ranked: CandidateResult[]
}

// V2.12+ Portfolio types
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

export interface PortfolioRunResult {
  run_id: string; metrics: PortfolioMetrics; equity_curve: number[]
  benchmark_curve: number[]; dates: string[]; trades: any[]; rebalance_dates: string[]
  symbols_fetched?: number; symbols_skipped?: string[]
  weights_history?: { date: string; weights: Record<string, number> }[]
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

export interface OptimizeWeightsResponse {
  mode: OptimizeMode
  labels: string[]
  n_observations: number
  date_range: [string, string]
  nested_oos_results?: NestedOOSResults | null
  walk_forward_results?: WalkForwardResults | null
}
