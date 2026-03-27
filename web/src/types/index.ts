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
  name: string; key: string; parameters: Record<string, any>;
}

export interface WalkForwardResult {
  oos_metrics: Record<string, number>;
  overfitting_score: number;
  is_vs_oos_degradation: number;
  n_splits: number;
  oos_equity_curve: number[];
}

export interface FactorResult {
  ic_mean: number; rank_ic_mean: number; icir: number;
  rank_icir: number; ic_decay: Record<string, number>;
  turnover: number; ic_series: number[]; rank_ic_series: number[];
}
