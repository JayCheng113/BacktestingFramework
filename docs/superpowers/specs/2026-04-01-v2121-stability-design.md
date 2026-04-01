# V2.12.1 Stability Design Spec

## Goal

稳定化 V2.12 + 补完 F5 指数增强 + 清理全部剩余技术债。本版本做完，不遗留。

## Scope (7 项，全做)

| # | 项目 | 性质 |
|---|------|------|
| S1 | Batch kline 查询 | 技术债 |
| S2 | Gram-Schmidt 因子正交化 | 技术债 |
| S3 | weights_history 延迟加载 | 技术债 |
| S4 | 指数增强 F5 (完整版) | 功能 |
| S5 | PortfolioPanel 拆分 | UX/质量 |
| S6 | TypeScript types 补全 | UX/质量 |
| S7 | 边缘测试补全 | 健壮性 |

---

## S1. Batch Kline Query

### 问题
`_fetch_data()` 对 50 股循环 50 次 `chain.get_kline()`。每次命中 DuckDB 是独立 SQL。

### 设计

**DuckDB 层** (`ez/data/store.py`):
```python
def query_kline_batch(self, symbols: list[str], market: str, period: str,
                      start: date, end: date) -> dict[str, list[Bar]]:
    """Single SQL: WHERE symbol IN (...) AND trade_date BETWEEN ? AND ?
    Returns {symbol: [bars]}. Symbols without data → empty list."""
```

**DataProviderChain 层** (`ez/data/provider.py`):
```python
def get_kline_batch(self, symbols: list[str], market: str, period: str,
                    start: date, end: date) -> dict[str, list[Bar]]:
    """1. Batch query DuckDB cache
       2. 缺失的 symbols 逐个 fetch (现有逻辑, 无法批量因 API 限制)
       3. 存入 cache
       4. 返回全部"""
```

**API 层** (`ez/api/routes/portfolio.py`):
`_fetch_data()` 调 `chain.get_kline_batch()` 替代循环。

**收益**: 热缓存 50 股从 50 次 SQL → 1 次 SQL（~3x 加速）。冷启动无变化（API 限制）。

---

## S2. Gram-Schmidt 因子正交化

### 问题
AlphaCombiner 直接 z-score 加权，高相关因子会重复计入同一信号。

### 设计

**新文件**: `ez/portfolio/orthogonalization.py`

```python
def gram_schmidt_orthogonalize(factor_matrix: np.ndarray) -> np.ndarray:
    """对 N×K 因子矩阵做 Gram-Schmidt 正交化。

    Args:
        factor_matrix: N stocks × K factors (每列是一个因子的 raw scores)
    Returns:
        N×K 正交化后的因子矩阵 (列间两两正交)

    第一列保持不变 (基准因子)，后续列逐个去除与前面列的相关性。
    NaN 处理: NaN 行跳过，正交化后恢复 NaN。
    """
```

**集成到 AlphaCombiner** (`ez/portfolio/alpha_combiner.py`):
```python
class AlphaCombiner:
    def __init__(self, factors, weights=None, orthogonalize: bool = False):
        self._orthogonalize = orthogonalize

    def compute_raw(self, universe_data, date):
        # 收集各因子 raw scores → N×K matrix
        # if self._orthogonalize: matrix = gram_schmidt_orthogonalize(matrix)
        # z-score + 加权求和
```

**API**: `/run` 的 AlphaCombiner 参数中加 `orthogonalize: bool = false`。

**前端**: 多因子合成区加"因子正交化"checkbox。

---

## S3. weights_history 延迟加载

### 问题
`/run` 响应内联 last 20 权重快照。完整权重历史（如 36 期 × 50 股）无按需加载端点。

### 设计

**新端点**: `GET /api/portfolio/runs/{run_id}/weights`

```python
@router.get("/runs/{run_id}/weights")
def get_run_weights(run_id: str):
    """返回完整 rebalance_weights (从 DB 加载, 不在 /run 内联)。"""
    run = store.get_run(run_id)
    return {"rebalance_weights": run.get("rebalance_weights", [])}
```

**`/run` 响应瘦身**: 移除 `weights_history` (last 20)，只保留 `latest_weights`。前端需要权重历史时调新端点。

**前端**: 持仓变动表改为按需加载（点击"查看持仓变动"时 fetch）。

---

## S4. 指数增强 F5 (完整版)

### 数据层

**新文件**: `ez/portfolio/index_data.py`

```python
class IndexDataProvider:
    """获取指数成分股 + 权重。优先 AKShare, 降级等权。"""

    # 支持: CSI300 (000300), CSI500 (000905), CSI1000 (000852)
    SUPPORTED_INDICES = {"000300": "沪深300", "000905": "中证500", "000852": "中证1000"}

    def get_constituents(self, index_code: str) -> list[str]:
        """返回成分股 symbol 列表。AKShare: ak.index_stock_cons_csindex_p()
        失败时返回空列表 + warning。"""

    def get_weights(self, index_code: str) -> dict[str, float]:
        """返回 {symbol: weight}。AKShare 尝试获取权重。
        无权重时: 成分股等权 1/N。"""

    # 24 小时内存缓存 (避免频繁 API 调用)
    _cache: dict[str, tuple[float, Any]] = {}
```

### 优化器集成

**不新建类**。给现有 `PortfolioOptimizer` 加 `benchmark_weights` 支持：

```python
class PortfolioOptimizer(ABC):
    def __init__(self, ..., benchmark_weights: dict[str, float] | None = None,
                 max_tracking_error: float | None = None):
        self._benchmark_weights = benchmark_weights
        self._max_te = max_tracking_error
```

**MeanVarianceOptimizer 扩展**:
```python
def _optimize(self, alpha, sigma, symbols):
    # 现有: min λw'Σw - w'α, s.t. Σw=1, w>=0, w<=max_w
    # 新增 TE 约束 (如果 benchmark_weights + max_te 都提供):
    #   (w - w_b_active)'Σ(w - w_b_active) <= TE_budget²
    #   TE_budget² = max_te² - missing_weight²
    #   missing_weight = Σ(w_b_i for i NOT in user universe)
    # 约束形式: {"type": "ineq", "fun": lambda w: TE_budget² - (w-w_b)'Σ(w-w_b)}
```

**TE 近似**: 只在用户 universe 内估计 TE（50×50 协方差），index 中不在 universe 的股票权重计入固定 missing_weight。这是业界标准的 "active universe TE" 近似。

### Attribution 集成

`compute_attribution()` 已支持 `custom_benchmark`。指数增强时：
```python
# benchmark_type="index", custom_benchmark = index_weights
attr = compute_attribution(result, data, industry_map,
                           benchmark_type="custom",
                           custom_benchmark=index_weights)
```

### Engine 集成

`run_portfolio_backtest()` 不改。优化器已有 `benchmark_weights` 参数。Benchmark curve 仍用现有 `benchmark_symbol`（如 `000300.SH` ETF）。

### API

```python
class PortfolioRunRequest(BaseModel):
    ...
    # V2.12.1: Index enhancement
    index_benchmark: str = ""  # "000300" / "000905" / "000852" / ""
    max_tracking_error: float = Field(default=0.05, gt=0, le=0.20)
```

路由逻辑:
```python
if req.index_benchmark:
    from ez.portfolio.index_data import IndexDataProvider
    idx = IndexDataProvider()
    index_weights = idx.get_weights(req.index_benchmark)
    if index_weights:
        # 传给 optimizer 的 benchmark_weights
        # 传给 attribution 的 custom_benchmark
```

### 前端

优化器面板内:
```
┌─ 指数增强 (仅优化方法 ≠ 不优化时显示) ──────┐
│ 基准指数: [不使用 ▼]                         │
│   不使用 / 沪深300 / 中证500 / 中证1000       │
│ 跟踪误差上限: [5] %                          │
└──────────────────────────────────────────────┘
```

结果区: 如果有 index benchmark，显示主动权重偏离表：
```
┌─ 主动权重偏离 ──────────────────────────────┐
│ 标的      组合权重  指数权重  主动偏离         │
│ 000001.SZ  15.2%    3.1%    +12.1%          │
│ 600519.SH   0.0%    4.2%    -4.2%           │
└──────────────────────────────────────────────┘
```

---

## S5. PortfolioPanel 拆分

### 设计

拆为 4 个文件:

| 文件 | 内容 | 估计行数 |
|------|------|---------|
| `PortfolioPanel.tsx` | Tab router + 共享 state + settings | ~250 |
| `PortfolioRunTab.tsx` | 回测运行 + 优化器 + 风控 + 指数增强 + 结果 + 归因 | ~550 |
| `PortfolioFactorTab.tsx` | 因子研究 + IC + 相关性 + WF + 数据质量 | ~350 |
| `PortfolioHistoryTab.tsx` | 历史记录 + 多回测对比 | ~200 |

**共享 state**: symbols, startDate, endDate, freq, settings 通过 props 传递。
**独立 state**: 每个 tab 内部的 loading/result/evalResult 等在各自组件内管理。

---

## S6. TypeScript Types 补全

### 设计

在 `web/src/types/index.ts` 新增:

```typescript
interface RiskEvent {
  date: string
  event: string
}

interface BrinsonPeriod {
  start: string
  end: string
  allocation: number
  selection: number
  interaction: number
  total_excess: number
}

interface AttributionResult {
  cumulative: {
    allocation: number
    selection: number
    interaction: number
    total_excess: number
  } | null
  cost_drag: number
  by_industry: Record<string, {allocation: number; selection: number; interaction: number}>
  periods: BrinsonPeriod[]
}

// 扩展 PortfolioRunResult
interface PortfolioRunResult {
  ...existing fields...
  risk_events?: RiskEvent[]
  attribution?: AttributionResult
  active_weights?: Record<string, {portfolio: number; benchmark: number; active: number}>
}
```

去掉所有 `(result as any)` cast（约 10 处）。

---

## S7. 边缘测试补全

### 测试清单

```python
# tests/test_portfolio/test_v2121_edge.py

class TestOptimizerEdgeCases:
    test_all_zero_alpha_returns_fallback()
    test_all_negative_alpha_returns_fallback()
    test_covariance_with_2_days_data()  # minimum viable
    test_optimizer_with_nan_in_prices()
    test_industry_constraint_5_iterations_convergence()

class TestRiskManagerEdgeCases:
    test_breach_on_first_trading_day()
    test_recovery_ratio_equals_threshold_minus_epsilon()
    test_turnover_check_with_empty_prev_weights()  # first rebalance

class TestAttributionEdgeCases:
    test_empty_industry_map_all_in_other()
    test_single_period_carino_equals_arithmetic()
    test_zero_return_period_carino_k_is_one()

class TestFinalLiquidation:
    test_liquidation_trades_have_liquidation_flag()
    test_liquidation_date_is_last_day_plus_one()
    test_turnover_excludes_liquidation()
    test_cost_drag_excludes_liquidation()

class TestBatchKline:
    test_batch_returns_same_as_individual()
    test_batch_with_missing_symbols()

class TestGramSchmidt:
    test_orthogonalized_columns_are_orthogonal()
    test_nan_rows_preserved()
    test_single_factor_unchanged()

class TestIndexData:
    test_get_constituents_returns_list()
    test_get_weights_sum_approximately_one()
    test_cache_prevents_repeated_api_calls()
    test_fallback_to_equal_weight_on_error()
```

---

## 文件变更清单

### 新建 (5)
| 文件 | 行数 | 职责 |
|------|------|------|
| `ez/portfolio/orthogonalization.py` | ~50 | Gram-Schmidt 正交化 |
| `ez/portfolio/index_data.py` | ~100 | 指数成分 + 权重获取 (AKShare) |
| `web/src/components/PortfolioRunTab.tsx` | ~550 | 回测运行子组件 |
| `web/src/components/PortfolioFactorTab.tsx` | ~350 | 因子研究子组件 |
| `web/src/components/PortfolioHistoryTab.tsx` | ~200 | 历史记录子组件 |

### 修改 (10)
| 文件 | 改动 |
|------|------|
| `ez/data/store.py` | +30: query_kline_batch() |
| `ez/data/provider.py` | +25: get_kline_batch() |
| `ez/portfolio/alpha_combiner.py` | +15: orthogonalize 参数 |
| `ez/portfolio/optimizer.py` | +30: benchmark_weights + TE 约束 |
| `ez/api/routes/portfolio.py` | +40: index_benchmark + weights 端点 + batch fetch |
| `web/src/components/PortfolioPanel.tsx` | 重构为 router (~250 行) |
| `web/src/types/index.ts` | +30: V2.12 types |
| `web/src/api/index.ts` | +5: weights endpoint + index API |
| `CLAUDE.md` | 版本更新 |
| `ez/portfolio/CLAUDE.md` | 模块文档 |

### 新测试 (2)
| 文件 | 测试数 |
|------|--------|
| `tests/test_portfolio/test_v2121_edge.py` | ~20 |
| `tests/test_data/test_batch_kline.py` | ~5 |

---

## Exit Gate

1. batch kline: 50 股热缓存查询 < 1s (单次 SQL)
2. Gram-Schmidt: 正交化后因子列两两相关系数 < 0.01
3. weights 延迟加载: /run 响应不含 weights_history, 新端点可获取
4. 指数增强: CSI300 成分可获取 + TE 约束优化可运行 + 归因用指数权重
5. PortfolioPanel: 主文件 < 300 行
6. TypeScript: 0 处 `as any` cast
7. 边缘测试全部通过
8. 测试 >= 1370 (当前 1339 + ~30 新)
