# V2.12.1 Stability Design Spec (v2 — 审查修复版)

## Goal

稳定化 V2.12 + 补完 F5 指数增强 + 清理全部剩余技术债。本版本做完，不遗留。

## Scope (7 项，全做)

| # | 项目 | 性质 |
|---|------|------|
| S1 | Batch kline 查询 | 技术债 |
| S2 | Gram-Schmidt 因子正交化 | 技术债 |
| S3 | weights 完整历史端点 | 技术债 |
| S4 | 指数增强 F5 (完整版) | 功能 |
| S5 | PortfolioPanel 拆分 | UX/质量 |
| S6 | TypeScript types 补全 | UX/质量 |
| S7 | 边缘测试补全 | 健壮性 |

---

## S1. Batch Kline Query

### 问题
`_fetch_data()` 对 50 股循环 50 次 `chain.get_kline()`，每次独立 SQL 查询。

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
    """1. Batch query DuckDB cache (单次 SQL)
       2. 缺失的 symbols 逐个 fetch (现有逻辑, Tushare API 不支持多 symbol 批量)
       3. 存入 cache
       4. 返回全部"""
```

**API 层** (`ez/api/routes/portfolio.py`):
`_fetch_data()` 调 `chain.get_kline_batch()` 替代循环。

**收益**: 代码简化（消除循环），热缓存减少 DB 连接开销。用户可感知的提升有限（DB 查询本身很快），主要是代码质量改进。冷启动无变化（受 API 限制）。

---

## S2. Gram-Schmidt 因子正交化

### 问题
AlphaCombiner 直接 z-score 加权，高相关因子会重复计入同一信号。

### 设计

**新文件**: `ez/portfolio/orthogonalization.py`

```python
def gram_schmidt_orthogonalize(factor_matrix: np.ndarray) -> np.ndarray:
    """对 N×K 因子矩阵做 Gram-Schmidt 正交化 (残差法)。

    Args:
        factor_matrix: N stocks × K factors (每列是一个因子的 raw scores)
    Returns:
        N×K 正交化后的因子矩阵 (列间两两正交)

    算法:
        第一列保持不变 (基准因子)。
        第 j 列: 回归到前 j-1 列，取残差。
    NaN 处理:
        仅对非 NaN 行参与正交化计算。
        NaN 行在输出中保持 NaN。
    注意:
        因子顺序影响结果 (第一个因子不变, 后续因子逐步去相关)。
        调用方应按因子重要性排序 (最重要的放第一列)。
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
        # z-score + 加权求和 (现有逻辑)
```

**API**: `/run` 的 AlphaCombiner 参数中加 `orthogonalize: bool = false`。

**前端**: 多因子合成区加"因子正交化"checkbox。

---

## S3. weights 完整历史端点

### 问题
完整 rebalance_weights 存在 DB 但无按需加载端点。用户需要分析全部调仓历史时只能看 last 20。

### 设计

**新端点**: `GET /api/portfolio/runs/{run_id}/weights`

```python
@router.get("/runs/{run_id}/weights")
def get_run_weights(run_id: str):
    """返回完整 rebalance_weights (从 DB 加载)。"""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404)
    return {"rebalance_weights": run.get("rebalance_weights", [])}
```

**不破坏现有接口**: `/run` 响应保留 `weights_history` (last 20) + `latest_weights`，不移除任何字段。新端点是纯增量。

**前端**: 持仓变动表增加"加载完整历史"按钮，点击时 fetch 新端点。

---

## S4. 指数增强 F5 (完整版)

### 数据层

**新文件**: `ez/portfolio/index_data.py`

```python
class IndexDataProvider:
    """获取指数成分股 + 权重。优先 AKShare, 降级等权。"""

    SUPPORTED_INDICES = {
        "000300": "沪深300",
        "000905": "中证500",
        "000852": "中证1000",
    }

    def get_constituents(self, index_code: str) -> list[str]:
        """返回成分股 symbol 列表。
        尝试多个 AKShare API (index_stock_cons / index_stock_cons_sina 等)。
        全部失败 → 空列表 + warning log。"""

    def get_weights(self, index_code: str) -> dict[str, float]:
        """返回 {symbol: weight}。
        AKShare 有权重数据 → 直接用。
        无权重 → 成分股等权 1/N。"""

    # 24 小时内存缓存
    _cache: dict[str, tuple[float, Any]] = {}
    _CACHE_TTL = 86400  # seconds
```

**AKShare API fallback 策略**:
1. 尝试 `ak.index_stock_cons_csindex_p(symbol=index_code)`
2. 失败 → 尝试 `ak.index_stock_cons(index=index_code)`
3. 失败 → 尝试 `ak.stock_zh_index_spot()` 获取成分列表
4. 全部失败 → 返回空 + warning（前端显示"无法获取指数成分"）

### 优化器集成

**不新建类**。给 `PortfolioOptimizer` 加 benchmark_weights 支持：

```python
class PortfolioOptimizer(ABC):
    def __init__(self, ..., benchmark_weights: dict[str, float] | None = None,
                 max_tracking_error: float | None = None):
        self._benchmark_weights = benchmark_weights
        self._max_te = max_tracking_error
```

**MeanVarianceOptimizer TE 约束**:
```python
def _optimize(self, alpha, sigma, symbols):
    # 现有约束: Σw=1, w>=0, w<=max_w, industry
    # 新增 TE 约束 (当 benchmark_weights + max_te 都提供时):
    if self._benchmark_weights and self._max_te:
        # w_b_active: 用户 universe 中各股的指数权重 (未归一化)
        w_b = np.array([self._benchmark_weights.get(s, 0) for s in symbols])
        # Active-universe TE: (w - w_b)'Σ(w - w_b) <= max_te²
        # 注: 这是近似值, 忽略 universe 外股票的协方差贡献
        cons.append({
            "type": "ineq",
            "fun": lambda w, wb=w_b: float(
                self._max_te ** 2 - (w - wb) @ sigma @ (w - wb)
            ),
        })
```

**Active-universe TE 近似**: 只在用户 universe 内的股票上计算 TE。指数中不在 universe 的股票的权重贡献被忽略。这是业界标准的实用近似 — 实际 TE 会因缺失股票而高于约束值。文档注明此限制。

### 两种使用模式

| 条件 | 行为 |
|------|------|
| `optimizer != "none"` + `index_benchmark` | TE 约束优化 + 归因用指数权重 |
| `optimizer == "none"` + `index_benchmark` | 仅归因用指数权重（无 TE 优化） |
| `index_benchmark` 为空 | 现有行为不变 |

### Attribution 集成

`compute_attribution()` 已支持 `custom_benchmark`。指数增强时：
```python
if index_weights:
    attr = compute_attribution(result, data, industry_map,
                               benchmark_type="custom",
                               custom_benchmark=index_weights)
```

### API

```python
class PortfolioRunRequest(BaseModel):
    ...
    # V2.12.1: Index enhancement
    index_benchmark: str = Field(default="", pattern="^(|000300|000905|000852)$")
    max_tracking_error: float = Field(default=0.05, gt=0, le=0.20)
```

路由逻辑:
```python
index_weights = {}
if req.index_benchmark:
    from ez.portfolio.index_data import IndexDataProvider
    idx = IndexDataProvider()
    index_weights = idx.get_weights(req.index_benchmark)
    if not index_weights:
        fund_warnings.append(f"无法获取{req.index_benchmark}成分数据")

# 传给 optimizer (如果有)
if optimizer_instance and index_weights:
    optimizer_instance._benchmark_weights = index_weights
    optimizer_instance._max_te = req.max_tracking_error

# 传给 attribution (始终, 如果有 index_weights)
if index_weights:
    attribution = _compute_inline_attribution(
        result, universe_data, req.initial_cash,
        benchmark_type="custom", custom_benchmark=index_weights)
```

响应新增 `active_weights` 字段:
```python
if index_weights:
    response["active_weights"] = {
        s: {"portfolio": result_weights.get(s, 0),
            "benchmark": index_weights.get(s, 0),
            "active": result_weights.get(s, 0) - index_weights.get(s, 0)}
        for s in set(result_weights) | set(index_weights)
        if abs(result_weights.get(s, 0) - index_weights.get(s, 0)) > 1e-6
    }
```

### 前端

优化器面板内:
```
┌─ 指数增强 ──────────────────────────────┐
│ 基准指数: [不使用 ▼]                     │
│   不使用 / 沪深300 / 中证500 / 中证1000   │
│ 跟踪误差上限: [5] % (仅优化时生效)        │
└──────────────────────────────────────────┘
```

结果区: 如果有 active_weights，显示主动权重表：
```
┌─ 主动权重偏离 ──────────────────────────┐
│ 标的      组合权重  指数权重  主动偏离     │
│ 000001.SZ  15.2%    3.1%    +12.1%      │
│ 600519.SH   0.0%    4.2%    -4.2%       │
└──────────────────────────────────────────┘
```

---

## S5. PortfolioPanel 拆分

### 设计

共享输入区（symbols/dates/freq/settings）保留在 parent，只拆 **tab 内容**：

| 文件 | 内容 | 估计行数 |
|------|------|---------|
| `PortfolioPanel.tsx` | 共享输入区 + tab router + shared state | ~400 |
| `PortfolioRunContent.tsx` | 回测运行结果 + 优化器 + 风控 + 归因 + 指数增强 + 主动权重 | ~450 |
| `PortfolioFactorContent.tsx` | 因子研究 + IC/ICIR + 相关性 + WF + 数据质量 | ~300 |
| `PortfolioHistoryContent.tsx` | 历史记录 + 多回测对比 | ~200 |

**props 设计**: 子组件只接收 **该 tab 需要的数据和回调**，不接收输入 state 的 setter。输入区在 parent 管理。

```typescript
// PortfolioRunContent 接收:
interface RunContentProps {
  result: PortfolioRunResult | null
  loading: boolean
  onRun: () => void
  // 优化器/风控参数由 parent 管理, 通过 onRun 提交
  wfResult: any
  wfLoading: boolean
  onWalkForward: () => void
}
```

---

## S6. TypeScript Types 补全

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

interface ActiveWeight {
  portfolio: number
  benchmark: number
  active: number
}

// 扩展 PortfolioRunResult
interface PortfolioRunResult {
  ...existing fields...
  risk_events?: RiskEvent[]
  attribution?: AttributionResult
  active_weights?: Record<string, ActiveWeight>
}
```

去掉所有 `(result as any)` cast（约 10 处）。

---

## S7. 边缘测试补全

### 测试清单 (~20 个有效测试)

```python
# tests/test_portfolio/test_v2121_edge.py

class TestOptimizerEdgeCases:
    test_all_negative_alpha_returns_fallback()
    test_covariance_with_exactly_3_days()  # minimum viable
    test_optimizer_with_nan_in_adj_close()
    test_industry_constraint_infeasible_falls_back()
    test_te_constraint_with_benchmark_weights()

class TestRiskManagerEdgeCases:
    test_turnover_check_with_empty_prev_weights()  # first rebalance
    test_drawdown_recovery_at_exact_threshold()
    test_mixed_weights_sum_after_normalization()

class TestAttributionEdgeCases:
    test_empty_industry_map_all_in_other()
    test_single_period_carino_equals_arithmetic()
    test_zero_return_period_carino_k_is_one()
    test_attribution_with_index_benchmark_weights()

class TestFinalLiquidation:
    test_liquidation_trades_have_flag()
    test_liquidation_date_is_last_day_plus_one()
    test_turnover_excludes_liquidation()

class TestBatchKline:
    test_batch_returns_same_as_individual()
    test_batch_with_missing_symbols()

class TestGramSchmidt:
    test_orthogonalized_columns_are_orthogonal()
    test_nan_rows_preserved()
    test_single_factor_unchanged()

class TestIndexData:
    test_cache_prevents_repeated_calls()
    test_fallback_to_equal_weight_on_error()
```

---

## 文件变更清单

### 新建 (5)
| 文件 | 行数 | 职责 |
|------|------|------|
| `ez/portfolio/orthogonalization.py` | ~50 | Gram-Schmidt 正交化 |
| `ez/portfolio/index_data.py` | ~120 | 指数成分 + 权重 (AKShare + cache) |
| `web/src/components/PortfolioRunContent.tsx` | ~450 | 回测运行 tab 内容 |
| `web/src/components/PortfolioFactorContent.tsx` | ~300 | 因子研究 tab 内容 |
| `web/src/components/PortfolioHistoryContent.tsx` | ~200 | 历史记录 tab 内容 |

### 修改 (10)
| 文件 | 改动 |
|------|------|
| `ez/data/store.py` | +30: query_kline_batch() |
| `ez/data/provider.py` | +25: get_kline_batch() |
| `ez/portfolio/alpha_combiner.py` | +15: orthogonalize 参数 |
| `ez/portfolio/optimizer.py` | +30: benchmark_weights + TE 约束 |
| `ez/api/routes/portfolio.py` | +50: index_benchmark + weights 端点 + batch fetch |
| `web/src/components/PortfolioPanel.tsx` | 重构为 router + 输入区 (~400 行) |
| `web/src/types/index.ts` | +40: V2.12 types + ActiveWeight |
| `web/src/api/index.ts` | +5: weights endpoint |
| `CLAUDE.md` | 版本更新 |
| `ez/portfolio/CLAUDE.md` | 模块文档 |

### 新测试 (2)
| 文件 | 测试数 |
|------|--------|
| `tests/test_portfolio/test_v2121_edge.py` | ~20 |
| `tests/test_data/test_batch_kline.py` | ~3 |

---

## Exit Gate

1. batch kline: `get_kline_batch(50 symbols)` 返回结果与逐个查询一致
2. Gram-Schmidt: 正交化后因子列两两相关系数 < 0.05
3. weights 端点: `GET /runs/{id}/weights` 返回完整 rebalance_weights
4. 指数增强: CSI300 成分可获取 + TE 约束优化可运行 + 归因用指数权重 + 主动权重展示
5. PortfolioPanel: 主文件 < 500 行
6. TypeScript: 0 处 `as any` cast
7. 边缘测试全部通过
8. 测试 >= 1365 (当前 1339 + ~25 新)

---

## 审查修复追踪

| 问题 | 修复 |
|------|------|
| S1 收益夸大 | 改为"代码简化 + 减少 DB 连接开销"，不承诺用户可感知加速 |
| S3 破坏性变更 | 改为纯增量：不移除现有 weights_history，新增端点 |
| S4 TE 公式错误 | 改为 active-universe TE 近似，文档注明限制 |
| S4 未处理分支 | 明确: optimizer=none + index → 只做归因 |
| S4 API 未验证 | 加 4 级 fallback 策略 |
| S5 prop drilling | 改为只拆 tab 内容，shared state 留 parent |
| S7 测试重复 | 删除伪测试，保留 ~20 个 |
