# V2.12 Design Spec: 组合优化 + 归因 + 风控 (v2 — 审查修复版)

## Goal

从"等权/简单加权持仓"升级到"约束优化 + 绩效归因 + 组合风控"。
不改 core 文件，全部在 `ez/portfolio/` 扩展。

## Architecture

```
PortfolioStrategy.generate_weights()      # 原始 alpha 信号权重
    ↓
Optimizer._optimize(alpha, Σ, constraints) # F4: 约束优化 (新接口, 非 Allocator)
    ↓
Engine: weight → shares → 执行             # 不改现有接口
    ↓ (每个交易日)
RiskManager.check_drawdown(equity)         # D4: 每日回撤检查
    ↓ (仅再平衡日)
RiskManager.check_turnover(w_new, w_old)   # D4: 换手率限制
    ↓
Attribution.analyze(result, universe_data) # F6: 事后归因
```

**关键设计决策**:
1. **Optimizer 不继承 Allocator** — Allocator.allocate(raw_weights) 不传 date，无法逐日更新协方差。Optimizer 用独立接口，Engine 通过 `set_context(date, universe_data)` + `optimize(alpha_weights)` 两步调用。
2. **RiskManager 回撤检查每日执行** — 不仅在再平衡日。Engine 主循环每天调 `check_drawdown()`，触发时执行紧急减仓。
3. **约束分工明确** — Optimizer 负责 max_weight + industry 约束（优化时满足）。RiskManager 只负责回撤熔断 + 换手率限制（优化后检查）。不重复。
4. **Alpha 信号 = 预期收益** — 策略的 raw_weights 被解释为预期超额收益的代理，不用历史均值替代。

---

## F4. Portfolio Optimizer (独立接口, 3 个实现)

### 新文件: `ez/portfolio/optimizer.py`

### 接口设计 (解决 P1: date 传递)

```python
@dataclass
class OptimizationConstraints:
    max_weight: float = 0.10          # 单股上限 (默认 10%)
    max_industry_weight: float = 0.30 # 行业上限 (默认 30%)
    industry_map: dict[str, str] = field(default_factory=dict)

class PortfolioOptimizer(ABC):
    """组合优化器: 从 alpha 信号 + 风险模型 → 最优权重。

    与 Allocator 不同: 接收 date context，每次再平衡重新估计协方差。
    """

    def __init__(self, constraints: OptimizationConstraints,
                 cov_lookback: int = 60):
        self._constraints = constraints
        self._cov_lookback = cov_lookback
        # Context: set by engine before each optimize() call
        self._current_date: date | None = None
        self._universe_data: dict[str, pd.DataFrame] | None = None

    def set_context(self, current_date: date,
                    universe_data: dict[str, pd.DataFrame]) -> None:
        """Called by engine before each rebalance. Provides date + data."""
        self._current_date = current_date
        self._universe_data = universe_data

    def optimize(self, alpha_weights: dict[str, float]) -> dict[str, float]:
        """Public entry: alpha signal → constrained optimal weights.

        Args:
            alpha_weights: Strategy's raw weights, interpreted as
                relative expected excess returns (higher = more bullish).

        Returns:
            Optimal weights: all >= 0, sum <= 1.0, respecting constraints.
        """
        symbols = [s for s, w in alpha_weights.items() if w > 0]
        if len(symbols) < 2:
            # Degenerate: single stock or empty → equal weight
            n = len(symbols)
            return {s: 1.0 / n for s in symbols} if n > 0 else {}

        # 1. Build alpha vector from raw_weights (normalized to sum=1)
        total_alpha = sum(alpha_weights[s] for s in symbols)
        alpha = np.array([alpha_weights[s] / total_alpha for s in symbols])

        # 2. Estimate covariance from historical data
        sigma = self._estimate_covariance(symbols)
        if sigma is None:
            return self._fallback(symbols)

        # 3. Delegate to subclass
        try:
            w = self._optimize(alpha, sigma, symbols)
        except Exception:
            return self._fallback(symbols)

        # 4. Enforce constraints (post-optimization clip)
        return self._apply_constraints(dict(zip(symbols, w)))

    @abstractmethod
    def _optimize(self, alpha: np.ndarray, sigma: np.ndarray,
                  symbols: list[str]) -> np.ndarray:
        """Subclass implements the specific optimization objective."""
        ...

    def _estimate_covariance(self, symbols: list[str]) -> np.ndarray | None:
        """Ledoit-Wolf 收缩协方差 (numpy 实现, 不依赖 sklearn)."""
        # 从 universe_data 提取最近 lookback 天的日收益率
        # 返回 N×N 矩阵, 或 None (数据不足)
        ...

    def _fallback(self, symbols: list[str]) -> dict[str, float]:
        """优化失败时回退: MaxWeight 等权."""
        max_w = self._constraints.max_weight
        n = len(symbols)
        w = min(1.0 / n, max_w)
        return {s: w for s in symbols}

    def _apply_constraints(self, weights: dict[str, float]) -> dict[str, float]:
        """Post-optimization: clip max_weight + industry limits + normalize."""
        ...
```

### Engine 集成 (取代 Allocator 位置)

```python
# engine.py rebalance 逻辑:
if optimizer:
    optimizer.set_context(day, sliced_tradeable)  # 传 date + 当前数据
    raw_weights = optimizer.optimize(raw_weights)   # alpha → optimal
elif allocator:
    raw_weights = allocator.allocate(raw_weights)   # 现有兼容
```

**不破坏兼容**: allocator 参数保留，optimizer 是新增可选参数。两者互斥。

### F4a. MeanVarianceOptimizer (解决 P2: alpha 信号)

```python
class MeanVarianceOptimizer(PortfolioOptimizer):
    """Markowitz: min λ·w'Σw - w'α, s.t. long-only + constraints.

    α = 策略 raw_weights (normalized), 不是历史均值收益。
    策略权重越大 = 预期超额收益越高。
    """

    def __init__(self, risk_aversion: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self.risk_aversion = risk_aversion

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)
        max_w = self._constraints.max_weight

        # Objective: minimize λ·w'Σw - w'α
        def objective(w):
            return self.risk_aversion * w @ sigma @ w - w @ alpha

        # Constraints
        cons = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},  # Σw = 1
        ]
        # Industry constraints
        for industry, idx_list in self._industry_groups(symbols).items():
            cons.append({
                "type": "ineq",
                "fun": lambda w, idx=idx_list: (
                    self._constraints.max_industry_weight - sum(w[i] for i in idx)
                ),
            })

        bounds = [(0.0, max_w)] * n
        w0 = np.full(n, 1.0 / n)  # equal-weight start

        result = scipy.optimize.minimize(
            objective, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
            options={"maxiter": 500, "ftol": 1e-10},
        )
        if not result.success:
            raise RuntimeError(result.message)
        return np.clip(result.x, 0, max_w)
```

### F4b. MinVarianceOptimizer (替代 MaxDiversification, 解决 P8: 非凸)

```python
class MinVarianceOptimizer(PortfolioOptimizer):
    """最小方差组合: min w'Σw, s.t. long-only + constraints.

    纯风险视角, 不用 alpha。适合保守配置。
    凸问题, 保证全局最优。
    """

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)
        max_w = self._constraints.max_weight

        def objective(w):
            return w @ sigma @ w

        cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        for industry, idx_list in self._industry_groups(symbols).items():
            cons.append({
                "type": "ineq",
                "fun": lambda w, idx=idx_list: (
                    self._constraints.max_industry_weight - sum(w[i] for i in idx)
                ),
            })

        bounds = [(0.0, max_w)] * n
        w0 = np.full(n, 1.0 / n)
        result = scipy.optimize.minimize(
            objective, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
        )
        if not result.success:
            raise RuntimeError(result.message)
        return np.clip(result.x, 0, max_w)
```

> **P8 决策**: 砍掉 MaxDiversification（非凸, 不稳定），改为 MinVariance（凸, 全局最优）。实用价值相当 — 两者都追求风险最小化。

### F4c. RiskParityOptimizer (解决 P11: 可行性)

```python
class RiskParityOptimizer(PortfolioOptimizer):
    """风险平价: min Σ(RC_i - 1/N)², 其中 RC_i = w_i·(Σw)_i / w'Σw.

    纯风险视角, 不用 alpha。
    Fallback: 如果有约束导致无法收敛, 降级为 inverse-vol 近似。
    """

    def _optimize(self, alpha, sigma, symbols):
        n = len(symbols)

        def risk_contribution_obj(w):
            port_var = w @ sigma @ w
            if port_var < 1e-20:
                return 0.0
            marginal = sigma @ w
            rc = w * marginal / port_var  # risk contribution
            target = 1.0 / n
            return float(np.sum((rc - target) ** 2))

        # Start from inverse-vol (good initialization)
        vols = np.sqrt(np.diag(sigma))
        vols = np.maximum(vols, 1e-8)
        w0 = (1.0 / vols) / np.sum(1.0 / vols)

        bounds = [(1e-6, self._constraints.max_weight)] * n  # strictly positive
        cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

        result = scipy.optimize.minimize(
            risk_contribution_obj, w0, method="SLSQP",
            bounds=bounds, constraints=cons,
            options={"maxiter": 1000},
        )
        if not result.success:
            # P11 fallback: inverse-vol when constrained risk parity fails
            return w0
        return result.x
```

### 协方差估计: Ledoit-Wolf (numpy, 解决 P9: 不加 sklearn)

```python
def ledoit_wolf_shrinkage(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage estimator (numpy implementation).

    Oracle Approximating Shrinkage (OAS) variant.
    Reference: Chen, Wiesel, Eldar, Hero (2010).

    Args:
        returns: T×N matrix of daily returns (T observations, N assets)
    Returns:
        N×N shrunk covariance matrix, guaranteed positive semi-definite.
    """
    T, N = returns.shape
    if T < 2:
        return np.eye(N) * 0.04  # fallback: 20% vol identity

    S = np.cov(returns, rowvar=False, ddof=1)  # sample covariance
    trace_S = np.trace(S)
    trace_S2 = np.sum(S ** 2)

    # Shrinkage target: scaled identity (constant correlation model)
    mu = trace_S / N
    F = mu * np.eye(N)

    # Optimal shrinkage intensity (OAS formula)
    rho_num = (1 - 2.0 / N) * trace_S2 + trace_S ** 2
    rho_den = (T + 1 - 2.0 / N) * (trace_S2 - trace_S ** 2 / N)
    rho = min(1.0, max(0.0, rho_num / rho_den)) if rho_den > 1e-20 else 1.0

    # Shrunk covariance
    sigma = (1 - rho) * S + rho * F

    # Ensure positive definite: add small ridge
    sigma += 1e-8 * np.eye(N)
    return sigma
```

---

## D4. Risk Manager (解决 P3: 每日回撤 + P5: 约束分工)

### 新文件: `ez/portfolio/risk_manager.py`

### 职责边界 (P5 修复: 不与 Optimizer 重复)

| 检查项 | Optimizer 负责 | RiskManager 负责 |
|--------|---------------|-----------------|
| max_weight (单股) | 优化约束 | **不检查** |
| max_industry_weight | 优化约束 | **不检查** |
| max_drawdown | - | 每日检查 |
| max_turnover | - | 再平衡日检查 |

```python
@dataclass
class RiskConfig:
    max_drawdown_threshold: float = 0.20  # 回撤熔断线
    drawdown_reduce_ratio: float = 0.50   # 熔断时仓位缩减比例
    drawdown_recovery_ratio: float = 0.10 # 回撤恢复到此比例时解除熔断
    max_turnover: float = 0.50            # 单边换手率上限

class RiskManager:
    def __init__(self, config: RiskConfig):
        self._config = config
        self._peak_equity = 0.0
        self._is_breached = False  # 熔断状态

    def check_drawdown(self, equity: float) -> tuple[float, str | None]:
        """每日调用。返回 (仓位缩放系数, 事件描述 | None)。

        状态机:
          NORMAL → 回撤 > threshold → BREACHED (scale = reduce_ratio)
          BREACHED → 回撤 < recovery_ratio → NORMAL (scale = 1.0)
        """
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity <= 0:
            return 1.0, None
        drawdown = (self._peak_equity - equity) / self._peak_equity

        if not self._is_breached:
            if drawdown > self._config.max_drawdown_threshold:
                self._is_breached = True
                return (self._config.drawdown_reduce_ratio,
                        f"回撤{drawdown:.1%}超阈值{self._config.max_drawdown_threshold:.0%}→减仓")
        else:
            if drawdown < self._config.drawdown_recovery_ratio:
                self._is_breached = False
                return 1.0, f"回撤恢复至{drawdown:.1%}→解除熔断"
            return self._config.drawdown_reduce_ratio, None  # 维持减仓, 不重复报事件

        return 1.0, None

    def check_turnover(self, new_weights: dict[str, float],
                       prev_weights: dict[str, float]
                       ) -> tuple[dict[str, float], str | None]:
        """再平衡日调用。如果换手率超限, 按比例混合 new/old。

        公式: w_final = α·w_new + (1-α)·w_old
        其中 α = min(1, max_turnover / actual_turnover)
        """
        all_syms = set(new_weights) | set(prev_weights)
        actual_turnover = sum(
            abs(new_weights.get(s, 0) - prev_weights.get(s, 0))
            for s in all_syms
        )
        if actual_turnover <= self._config.max_turnover:
            return new_weights, None

        # P10 修复: 明确混合公式
        alpha = self._config.max_turnover / actual_turnover
        mixed = {}
        for s in all_syms:
            w = alpha * new_weights.get(s, 0) + (1 - alpha) * prev_weights.get(s, 0)
            if w > 1e-10:
                mixed[s] = w
        return mixed, f"换手率{actual_turnover:.1%}超限{self._config.max_turnover:.0%}→混合α={alpha:.2f}"
```

### Engine 集成 (P3 修复: 每日回撤检查)

```python
def run_portfolio_backtest(
    ...,
    optimizer: PortfolioOptimizer | None = None,  # 新增
    risk_manager: RiskManager | None = None,        # 新增
) -> PortfolioResult:

    risk_events: list[dict] = []

    for day in trading_days:
        # ═══ 每日回撤检查 (P3: 不仅在再平衡日) ═══
        if risk_manager:
            equity_now = cash + sum(holdings.get(s, 0) * prices.get(s, 0) for s in holdings)
            scale, dd_event = risk_manager.check_drawdown(equity_now)
            if dd_event:
                risk_events.append({"date": day.isoformat(), "event": dd_event})
            # 如果 scale < 1 且非再平衡日, 执行紧急减仓
            if scale < 1.0 and day not in rebalance_date_set:
                for sym in list(holdings.keys()):
                    target = int(holdings[sym] * scale / lot_size) * lot_size
                    # ... 卖出 holdings[sym] - target 股
                    # (复用现有卖出逻辑)

        if day in rebalance_date_set:
            # 调用策略
            raw_weights = strategy.generate_weights(...)

            # 优化器 (P1 修复: set_context 传 date)
            if optimizer:
                optimizer.set_context(day, sliced_tradeable)
                raw_weights = optimizer.optimize(raw_weights)
            elif allocator:
                raw_weights = allocator.allocate(raw_weights)

            # 风控: 换手率限制 (P5: 只做换手率, 不重复约束检查)
            if risk_manager:
                raw_weights, to_event = risk_manager.check_turnover(raw_weights, prev_weights)
                if to_event:
                    risk_events.append({"date": day.isoformat(), "event": to_event})

            # ... 现有 clip + weight→shares + 执行逻辑不变
```

Engine 改动量: ~25 行（比原估计 15 行多，因为加了每日回撤检查和紧急减仓）。

### PortfolioResult 扩展

```python
@dataclass
class PortfolioResult:
    ...  # 现有字段不变
    risk_events: list[dict] = field(default_factory=list)
    # 每条: {"date": "2024-03-15", "event": "回撤21.3%超阈值20%→减仓"}
```

---

## F6. Attribution Analysis (解决 P6, P7)

### 新文件: `ez/portfolio/attribution.py`

### 接口 (P6 修复: 从 universe_data 计算收益, 不依赖存储)

```python
@dataclass
class BrinsonAttribution:
    """单期 Brinson 归因"""
    period_start: str
    period_end: str
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_excess: float

@dataclass
class AttributionResult:
    periods: list[BrinsonAttribution]
    cumulative: BrinsonAttribution
    cost_drag: float                     # Σ(trade costs) / initial_cash
    by_industry: dict[str, dict]         # industry → {allocation, selection, interaction}

def compute_attribution(
    result: PortfolioResult,
    universe_data: dict[str, pd.DataFrame],
    industry_map: dict[str, str],
    benchmark_type: str = "equal",  # "equal" or "custom"
    custom_benchmark: dict[str, float] | None = None,
) -> AttributionResult:
    """从回测结果 + 原始数据计算 Brinson 归因。

    P6 修复: 逐股收益直接从 universe_data 计算, 不需要 PortfolioStore 存储。
    P7 修复: 等权基准按每个周期的实际成分动态计算 1/N。
    """
```

### 归因计算逻辑

```python
def compute_attribution(...) -> AttributionResult:
    periods = []
    rebalance_dates = result.rebalance_dates  # list[date]
    weights_history = result.weights_history    # list[dict[str, float]]

    for i in range(len(rebalance_dates) - 1):
        t_start = rebalance_dates[i]
        t_end = rebalance_dates[i + 1]

        # 组合权重 (该周期开始时)
        w_p = weights_history[i]

        # P7 修复: 基准权重动态计算
        if benchmark_type == "equal":
            active_syms = [s for s in universe_data if _has_data(s, t_start, t_end)]
            n = len(active_syms)
            w_b = {s: 1.0 / n for s in active_syms} if n > 0 else {}
        else:
            w_b = custom_benchmark or {}

        # 从 universe_data 计算每只股票该周期收益
        r_p, r_b = {}, {}  # 个股收益
        for sym in set(w_p) | set(w_b):
            r = _period_return(universe_data.get(sym), t_start, t_end)
            r_p[sym] = r
            r_b[sym] = r  # 同一支股票, 组合和基准看到的收益相同

        # Brinson 分解 (行业维度)
        alloc, select, interact = 0.0, 0.0, 0.0
        for industry in set(industry_map.values()):
            syms_in_ind = [s for s in set(w_p) | set(w_b) if industry_map.get(s) == industry]
            w_p_j = sum(w_p.get(s, 0) for s in syms_in_ind)
            w_b_j = sum(w_b.get(s, 0) for s in syms_in_ind)
            r_p_j = _weighted_return(syms_in_ind, w_p, r_p) if w_p_j > 0 else 0
            r_b_j = _weighted_return(syms_in_ind, w_b, r_b) if w_b_j > 0 else 0
            r_b_total = sum(w_b.get(s, 0) * r_b.get(s, 0) for s in w_b)

            alloc += (w_p_j - w_b_j) * r_b_j
            select += w_b_j * (r_p_j - r_b_j)
            interact += (w_p_j - w_b_j) * (r_p_j - r_b_j)

        total_excess = alloc + select + interact
        periods.append(BrinsonAttribution(
            period_start=t_start.isoformat(),
            period_end=t_end.isoformat(),
            allocation_effect=alloc,
            selection_effect=select,
            interaction_effect=interact,
            total_excess=total_excess,
        ))

    # 累计: 简单求和 (逐期加总)
    cumulative = BrinsonAttribution(
        period_start=periods[0].period_start if periods else "",
        period_end=periods[-1].period_end if periods else "",
        allocation_effect=sum(p.allocation_effect for p in periods),
        selection_effect=sum(p.selection_effect for p in periods),
        interaction_effect=sum(p.interaction_effect for p in periods),
        total_excess=sum(p.total_excess for p in periods),
    )

    cost_drag = sum(float(t.get("cost", 0)) for t in result.trades) / initial_cash

    return AttributionResult(
        periods=periods,
        cumulative=cumulative,
        cost_drag=cost_drag,
        by_industry=_aggregate_by_industry(periods, industry_map),
    )
```

---

## API 设计 (P4 修复: 消除矛盾)

### 方案: 扩展现有 `/api/portfolio/run` + 新增 `/api/portfolio/attribution`

**不新建 `/optimize-run`**。只扩展现有 `/run` 加可选参数。

#### `/api/portfolio/run` 扩展参数

```python
class PortfolioRunRequest(BaseModel):
    # === 现有参数 (不变) ===
    strategy: str
    params: dict = {}
    symbols: list[str]
    market: str = "cn_stock"
    start_date: str
    end_date: str
    freq: str = "monthly"
    initial_cash: float = 1_000_000
    buy_commission: float = 0.0003
    sell_commission: float = 0.0003
    stamp_tax: float = 0.0005
    slippage: float = 0.001
    lot_size: int = 100
    limit_pct: float = 0.10
    benchmark_symbol: str = ""

    # === V2.12 新增: 优化器 ===
    optimizer: str = "none"           # none / mean_variance / min_variance / risk_parity
    risk_aversion: float = 1.0        # 仅 mean_variance
    max_weight: float = 0.10          # 优化器约束
    max_industry_weight: float = 0.30
    cov_lookback: int = 60

    # === V2.12 新增: 风控 ===
    risk_control: bool = False
    max_drawdown: float = 0.20
    drawdown_reduce: float = 0.50
    max_turnover: float = 0.50
```

API 路由逻辑:
```python
if request.optimizer != "none":
    optimizer = _create_optimizer(request)
    # 构建时传 constraints (max_weight, industry)
else:
    optimizer = None

if request.risk_control:
    risk_manager = RiskManager(RiskConfig(
        max_drawdown_threshold=request.max_drawdown,
        drawdown_reduce_ratio=request.drawdown_reduce,
        max_turnover=request.max_turnover,
    ))
else:
    risk_manager = None

result = run_portfolio_backtest(
    ..., optimizer=optimizer, risk_manager=risk_manager,
)
```

#### 新增 `POST /api/portfolio/attribution`

```python
class AttributionRequest(BaseModel):
    run_id: str
    benchmark_type: str = "equal"

# 实现:
# 1. 从 PortfolioStore 加载 run (包含 weights_history, trades, rebalance_dates)
# 2. 从 DataProviderChain 重新获取 universe_data (P6: 不依赖存储的逐股收益)
# 3. 从 FundamentalStore 获取 industry_map
# 4. 调用 compute_attribution()
# 5. 返回 AttributionResult
```

---

## 前端设计

### PortfolioPanel 改动 (现有"组合回测"tab 内扩展)

#### 1. 优化器参数区 (折叠面板, 默认收起)

```
┌─ 组合优化 ─────────────────────────────┐
│ 优化方法: [不优化 ▼]                     │
│   不优化 / 均值-方差 / 最小方差 / 风险平价  │
│ ┌─ (仅 mean_variance 时展开) ──────────┐ │
│ │ 风险厌恶系数 λ: [1.0]               │ │
│ └──────────────────────────────────────┘ │
│ 协方差回看期: [60] 天                     │
│ 单股上限: [10] %                         │
│ 行业上限: [30] %                         │
└──────────────────────────────────────────┘
```

#### 2. 风控参数区 (折叠面板, 默认收起)

```
┌─ 风险控制 ─────────────────────────────┐
│ [x] 启用风控                            │
│ 最大回撤阈值: [20] %                     │
│ 回撤减仓比例: [50] %                     │
│ 换手率上限: [50] %                       │
└──────────────────────────────────────────┘
```

#### 3. 归因分析 (回测完成后, 结果区折叠面板)

```
┌─ 归因分析 ────────────── [请求归因] ────┐
│ 累计超额: +6.5%                         │
│   配置效应: +2.3%  ████████             │
│   选股效应: +4.5%  ███████████████      │
│   交互效应: -0.3%  ▌                    │
│   交易成本: -0.8%  ██                   │
│                                        │
│ [展开逐期明细]                           │
└──────────────────────────────────────────┘
```

归因是**按需加载** — 用户点击"请求归因"才调 `/attribution`，不自动触发。

#### 4. 风控事件日志 (回测结果中)

```
┌─ 风控事件 (3) ──────────────────────────┐
│ 2024-03-15  回撤21.3%超阈值20%→减仓       │
│ 2024-03-22  换手率65%超限50%→混合α=0.77   │
│ 2024-06-01  回撤恢复至8%→解除熔断          │
└──────────────────────────────────────────┘
```

---

## 文件变更清单

### 新建文件 (4)

| 文件 | 行数估计 | 职责 |
|------|---------|------|
| `ez/portfolio/optimizer.py` | ~280 | PortfolioOptimizer ABC + MeanVariance + MinVariance + RiskParity + ledoit_wolf_shrinkage() |
| `ez/portfolio/risk_manager.py` | ~100 | RiskConfig + RiskManager (回撤状态机 + 换手率混合) |
| `ez/portfolio/attribution.py` | ~200 | BrinsonAttribution + compute_attribution() + 行业归因 |
| `tests/test_portfolio/test_v212.py` | ~250 | 优化器约束 + 回撤熔断 + 换手率 + 归因一致性 + fallback |

### 修改文件 (5)

| 文件 | 改动量 | 内容 |
|------|--------|------|
| `ez/portfolio/engine.py` | +25 行 | optimizer + risk_manager 参数, 每日回撤检查, risk_events |
| `ez/api/routes/portfolio.py` | +90 行 | /run 扩展优化/风控参数 + /attribution 端点 |
| `web/src/components/PortfolioPanel.tsx` | +160 行 | 优化器/风控折叠面板 + 归因按需加载 + 风控事件 |
| `ez/portfolio/CLAUDE.md` | +25 行 | 模块文档 |
| `CLAUDE.md` | +5 行 | 版本进度 |

### 不改文件

- `ez/portfolio/allocator.py` — ABC 不变, 现有 Allocator 保持兼容
- 所有 Core 文件 — 不动

---

## 依赖

- `scipy.optimize.minimize` — 已有 (scipy>=1.14)
- **不加 sklearn** — Ledoit-Wolf 用 numpy 自实现 (~25 行)
- 无新依赖

## 砍掉/延迟

| 功能 | 处理 | 原因 |
|------|------|------|
| MaxDiversification | **替换为 MinVariance** | 非凸问题不稳定, MinVariance 凸且实用价值等同 |
| F5 指数增强 | **V2.12.1** | 需要 index_weight 付费 API |
| U5 报告导出 | **V2.13** | 需新依赖 |

## Exit Gate

1. 优化器输出权重: 全部 >= 0、Σ = 1.0±1e-6、单股 <= max_weight
2. Brinson 归因: 配置 + 选股 + 交互 ≈ 总超额 (误差 < 1bp)
3. 风控熔断: 注入 20% 回撤 → 减仓触发 → 会计不变量成立
4. 每日回撤检查: 非再平衡日也能触发紧急减仓
5. 换手率限制: 超限时混合系数 α 正确
6. 优化不收敛时 fallback → 等权
7. Ledoit-Wolf 协方差: N > T 时也能返回正定矩阵
8. 测试 >= 1330

---

## 实施顺序

1. **optimizer.py** — Ledoit-Wolf + 3 优化器 + fallback + 测试
2. **risk_manager.py** — 回撤状态机 + 换手率混合 + 测试
3. **engine.py** — optimizer/risk_manager 参数 + 每日回撤 + 紧急减仓
4. **attribution.py** — Brinson 归因 + 行业聚合 + 测试
5. **API** — /run 扩展 + /attribution
6. **前端** — 折叠面板 + 归因 + 风控事件
7. **文档**

---

## 审查问题修复追踪

| ID | 级别 | 问题 | 修复方案 |
|----|------|------|---------|
| P1 | Critical | Allocator 不传 date | Optimizer 独立接口 + set_context(date, data) |
| P2 | High | alpha 信号被历史均值替代 | raw_weights 直接作为 alpha vector |
| P3 | High | 回撤只在再平衡日检查 | Engine 主循环每日调 check_drawdown() |
| P4 | High | API 新建/扩展矛盾 | 统一: 扩展 /run + 新增 /attribution |
| P5 | High | Optimizer/RiskManager 约束重复 | 分工: Optimizer 管权重约束, RiskManager 只管回撤+换手 |
| P6 | Medium | 归因需逐股收益但未存储 | 从 universe_data 实时计算, 不依赖存储 |
| P7 | Medium | 基准权重静态 | 等权基准每周期动态算 1/N(active_syms) |
| P8 | Medium | MaxDiversification 非凸 | 替换为 MinVariance (凸, 全局最优) |
| P9 | Medium | sklearn 依赖未决 | numpy 自实现 Ledoit-Wolf, 不加依赖 |
| P10 | Low | 换手率混合公式未指定 | α = min(1, max_turnover / actual_turnover) |
| P11 | Low | 风险平价+约束可行性 | inverse-vol fallback 兜底 |
