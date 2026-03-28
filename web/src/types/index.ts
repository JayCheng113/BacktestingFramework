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
