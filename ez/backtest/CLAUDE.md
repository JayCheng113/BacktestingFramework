# ez/backtest -- Backtest Layer

## Responsibility
Run vectorized backtests, compute metrics, validate via Walk-Forward, test statistical significance.

## Public Interfaces
- `VectorizedBacktestEngine` -- [CORE] run(data, strategy, capital) -> BacktestResult
- `MetricsCalculator` -- [CORE] compute(equity, benchmark) -> dict
- `WalkForwardValidator` -- [CORE] validate(data, strategy, n_splits) -> WalkForwardResult
- `compute_significance()` -- [CORE] Bootstrap CI + Monte Carlo permutation test

## Files
| File | Role | Core/Extension |
|------|------|---------------|
| engine.py | VectorizedBacktestEngine | CORE |
| portfolio.py | PortfolioState (V2, reserved) | CORE |
| metrics.py | MetricsCalculator | CORE |
| walk_forward.py | WalkForwardValidator | CORE |
| significance.py | Statistical significance | CORE |

## Critical Notes
- The engine shifts signals by 1 bar (T+1 execution) to prevent look-ahead bias
- Minimum commission: a floor is applied per trade so that very small trades still incur realistic costs
- Walk-Forward: `n_splits >= 2`, `0 < train_ratio < 1` (enforced both in WalkForwardValidator constructor and API Pydantic validation)
- NaN price guard: engine skips trading on NaN open/close, equity carried forward
- Known: engine does NOT force-close at end of period — last open trade is not settled in trade_count/win_rate

## Status
- Implemented: Full backtest engine, Walk-Forward (fixed-param), significance testing
- V2.0: Matcher extraction — engine delegates to Matcher.fill_buy/fill_sell
- V2.2: SlippageMatcher — user-configurable slippage via API/frontend
- V2.10: WalkForward constructor validates n_splits >= 2 and 0 < train_ratio < 1 (raises ValueError)
- Engine uses fill.fill_price (not exec_price) for entry/exit/PnL — supports slippage
- V2.12.1 post-release (codex 6 轮 + reviewer 8 轮):
  - **engine.py profit_factor**: 改用标准 `gross_profit / gross_loss` 公式 (原 `avg_win_pct / avg_loss_pct` 忽略 position sizing, 几次大赢+多次小亏会完全扭曲)
  - **walk_forward.py 每折 deepcopy strategy**: copy.deepcopy(strategy) 防 IS→OOS 状态污染 (原版复用同一实例)
  - **walk_forward.py oos_metrics 重算**: 用 MetricsCalculator 基于拼接 oos_equity_curve 算, 不是每折 sharpe 平均 (折长不一时偏)
  - **walk_forward.py _sharpe / significance.py _sharpe ddof=1**: 3 个 helpers 全部改 ddof=1, 匹配 metrics.py. 短 OOS (30-60d) 偏差最大 2.7%, CI 和显示 Sharpe 一致
  - **metrics.py degenerate input 防护**: _nan_safe() helper + rolling_corr 常数窗口 fast-path (1e-12 tolerance), evaluator.py 全部输出点 sanitize
- V2.12.2 post-release:
  - **walk_forward.py 尾部丢弃修复**: 原 `window_size = n // n_splits` 静默丢弃 `n % n_splits` 行 (n=510, k=7 丢 6 行). 改用整数区间 `i*n//n_splits .. (i+1)*n//n_splits`, 最后一折吸收余数. 校验用 `min_window = n // n_splits` 作为最小折的保守下界.
