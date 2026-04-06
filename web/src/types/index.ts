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
  type: string; default: any; min?: number; max?: number; label?: string
  options?: string[]
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
