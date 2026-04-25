# Portfolio / Rotation Strategy Design

> **状态**: 历史设计文档（未作为当前实现路线；已通过自审 + Codex 审计修正）
> **Codex 审计修正**: 接口冻结为 compute(data, date)→Series（#6）; 会计不变量 cash+pos=equity（#4）; PIT 动态成分股提升为必做（#3）; 交易日历模块（#8）; 防前瞻改为引擎切片（#2）
> **目标版本**: V2.9
> **依赖**: V2.5 (BatchRunner)。V2.6 (MarketRules) 可选集成。
> **前置条件**: 单股回测正确性已验证 (V2.3)，Agent Loop 已就位 (V2.4)

---

## 1. 问题陈述

当前系统只支持**单股票单策略**回测。实际量化研究需要：

- **股票池轮动**: 按因子排名选 top-N 股票，定期换仓
- **多股组合**: 同时持有多只股票，按权重分配资金
- **截面因子**: 在同一时间点跨股票计算因子（如动量排名、相对强弱）
- **组合级风控**: 总仓位、行业集中度、最大单股权重

**约束**: 不修改核心文件（薄核心规则）。现有单股策略/因子/引擎保持不变。

---

## 2. 架构方案

### 2.1 分阶段实施

| 阶段 | 内容 | 核心改动 | 复杂度 |
|------|------|---------|--------|
| **V2.9** | 组合引擎 + 截面因子 + 排名策略（最小可用组合） | 0 核心改动 | 中 |
| **Future** | 原生多股引擎（如需要） | 需 core-change 提案 | 高 |

**注意**: 轮动的本质是截面排名，截面因子和组合引擎必须同时交付。
不拆成独立 Phase——没有截面因子的组合层只能做固定权重，价值极低。

### 2.2 新模块: `ez/portfolio/`

```
ez/portfolio/
  __init__.py
  universe.py         — 股票池定义 + 数据获取
  cross_factor.py     — 截面因子 ABC + 内置实现
  portfolio_strategy.py — 组合策略 ABC（输入多股数据，输出权重向量）
  allocator.py        — 权重分配器（等权/风险平价/自定义）
  engine.py           — 组合回测引擎（聚合单股回测 + 换仓模拟）
  metrics.py          — 组合级指标（换手率/集中度/归因）
  rebalancer.py       — 换仓调度（日/周/月）
```

**依赖方向**: `ez/portfolio/` → `ez/backtest/`, `ez/data/`, `ez/factor/`, `ez/strategy/`
反方向禁止。符合薄核心规则。

---

## 3. 详细设计

### 3.1 Universe — 股票池

```python
# ez/portfolio/universe.py

@dataclass
class Universe:
    """A pool of symbols to trade."""
    symbols: list[str]
    market: str = "cn_stock"
    period: str = "daily"

    @classmethod
    def from_list(cls, symbols: list[str], market: str = "cn_stock") -> Universe:
        """手动指定股票列表。"""
        return cls(symbols=symbols, market=market)

    @classmethod
    def from_index(cls, index_code: str, market: str = "cn_stock") -> Universe:
        """从指数成分股生成（如沪深300）。需要数据源支持。"""
        # 调用 DataProviderChain 获取成分股列表
        ...


def fetch_universe_data(
    universe: Universe,
    start_date: date,
    end_date: date,
    chain: DataProviderChain,
) -> dict[str, pd.DataFrame]:
    """获取股票池所有股票的数据。

    Returns: {symbol: DataFrame(OHLCV)} — 按时间对齐，缺失日填 NaN。
    """
    data = {}
    for symbol in universe.symbols:
        bars = chain.get_kline(symbol, universe.market, universe.period,
                               start_date, end_date)
        if bars:
            df = pd.DataFrame([{
                "time": b.time, "open": b.open, "high": b.high,
                "low": b.low, "close": b.close, "adj_close": b.adj_close,
                "volume": b.volume,
            } for b in bars]).set_index("time")
            data[symbol] = df
    # 对齐时间轴
    if data:
        all_dates = sorted(set().union(*(df.index for df in data.values())))
        for sym in data:
            data[sym] = data[sym].reindex(all_dates)
    return data
```

### 3.2 CrossSectionalFactor — 截面因子

```python
# ez/portfolio/cross_factor.py

class CrossSectionalFactor(ABC):
    """跨股票因子：在同一时间点对所有股票计算因子值。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def warmup_period(self) -> int: ...

    @abstractmethod
    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """
        Input: {symbol: OHLCV DataFrame (已切片至 date-1)}, 当前调仓日
        Output: Series[symbol → factor score] (单日截面)
        NOTE: universe_data 由引擎切片，策略看不到 date 及之后的数据。
        NOTE: 全矩阵版本已废弃 — 改为逐日调用，与 roadmap 接口一致。
        """
        ...


class MomentumRank(CrossSectionalFactor):
    """N日收益率排名。"""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"momentum_rank_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        # universe_data 已切片至 date-1，取每只证券最后 _period 天的收益率
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period:
                continue
            scores[sym] = (df["adj_close"].iloc[-1] - df["adj_close"].iloc[-self._period]) / df["adj_close"].iloc[-self._period]
        return pd.Series(scores).rank(pct=True)


class VolumeRank(CrossSectionalFactor):
    """成交量排名。"""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"volume_rank_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period:
                continue
            scores[sym] = df["volume"].iloc[-self._period:].mean()
        return pd.Series(scores).rank(pct=True)


class ReverseVolatilityRank(CrossSectionalFactor):
    """波动率倒数排名（低波动 → 高分）。"""

    def __init__(self, period: int = 20):
        self._period = period

    @property
    def name(self) -> str:
        return f"reverse_vol_rank_{self._period}"

    @property
    def warmup_period(self) -> int:
        return self._period + 1

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        scores = {}
        for sym, df in universe_data.items():
            if len(df) < self._period + 1:
                continue
            vol = df["adj_close"].pct_change().iloc[-self._period:].std()
            scores[sym] = -vol  # 低波动 → 高分
        return pd.Series(scores).rank(pct=True)
```

### 3.3 PortfolioStrategy — 组合策略

```python
# ez/portfolio/portfolio_strategy.py

class PortfolioStrategy(ABC):
    """组合策略：输入多股数据，输出每日权重向量。

    与 Strategy 的区别:
    - Strategy: 单股 DataFrame → 单股 weight Series
    - PortfolioStrategy: {symbol: DataFrame} → {symbol: weight} DataFrame
    """

    @abstractmethod
    def required_cross_factors(self) -> list[CrossSectionalFactor]:
        """截面因子列表。"""
        ...

    @abstractmethod
    def generate_weights(
        self,
        universe_data: dict[str, pd.DataFrame],  # 已切片至 [date-lookback, date-1]
        date: datetime,                           # 当前调仓日
        prev_weights: dict[str, float],           # 上期实际权重（引擎回传）
        prev_returns: dict[str, float],           # 上期各资产收益（引擎回传）
    ) -> dict[str, float]:
        """
        返回目标权重 {symbol: weight}。
        - universe_data 由引擎切片，最后一行 ≤ date-1（策略看不到当日及未来数据）
        - self.state 可自由维护跨周期状态
        - 权重 >= 0（long-only），和 <= 1.0（剩余为现金）
        NOTE: 旧版全矩阵接口已废弃，改为逐调仓日调用（与 v2.3-roadmap.md 一致）。
        """
        ...


class TopNRotation(PortfolioStrategy):
    """按因子排名选 top-N 股票，等权持有。

    典型用法：动量轮动 — 每月选动量最强的 N 只股票。
    """

    def __init__(self, factor: CrossSectionalFactor, top_n: int = 10):
        super().__init__()
        self._factor = factor
        self._top_n = top_n

    def required_cross_factors(self) -> list[CrossSectionalFactor]:
        return [self._factor]

    def generate_weights(self, universe_data, date, prev_weights, prev_returns) -> dict[str, float]:
        scores = self._factor.compute(universe_data, date)
        valid = scores.dropna()
        if len(valid) < self._top_n:
            return {}
        top = valid.nlargest(self._top_n).index
        w = 1.0 / self._top_n
        return {sym: w for sym in top}
```

### 3.4 Allocator — 权重分配器

```python
# ez/portfolio/allocator.py

class Allocator(ABC):
    """权重分配器：将原始信号转化为最终持仓权重。
    NOTE: 接口已从 DataFrame 改为 dict（逐调仓日调用，非全矩阵）。
    """

    @abstractmethod
    def allocate(self, raw_weights: dict[str, float]) -> dict[str, float]:
        """
        Input: raw_weights {symbol: weight} (单日)
        Output: final_weights, 满足约束（权重 >= 0, 和 <= 1.0）
        """
        ...


class EqualWeightAllocator(Allocator):
    """等权分配：所有正权重标的等权。"""
    def allocate(self, raw_weights: dict[str, float]) -> dict[str, float]:
        selected = {k: v for k, v in raw_weights.items() if v > 0}
        if not selected:
            return {}
        w = 1.0 / len(selected)
        return {k: w for k in selected}


class MaxWeightAllocator(Allocator):
    """限制单股最大权重（迭代裁剪+重分配）。"""

    def __init__(self, max_weight: float = 0.05):
        self._max_weight = max_weight

    def allocate(self, raw_weights: dict[str, float]) -> dict[str, float]:
        # 见 ez/portfolio/allocator.py 实现（迭代裁剪算法）
        ...


class RiskParityAllocator(Allocator):
    """风险平价：按波动率倒数分配权重。
    NOTE: 必须在调用 allocate 前通过 set_volatilities() 设置波动率。
    """

    def __init__(self):
        self._vols: dict[str, float] = {}

    def set_volatilities(self, vols: dict[str, float]) -> None:
        self._vols = vols

    def allocate(self, raw_weights: dict[str, float]) -> dict[str, float]:
        # 见 ez/portfolio/allocator.py 实现
        ...
```

### 3.5 PortfolioEngine — 组合回测引擎

```python
# ez/portfolio/engine.py

@dataclass
class RebalanceEvent:
    """单次换仓记录。"""
    date: datetime
    old_weights: dict[str, float]
    new_weights: dict[str, float]
    turnover: float                   # sum(|new - old|) / 2
    cost: float                       # 换仓交易成本


@dataclass
class PortfolioResult:
    """组合回测结果。"""
    equity_curve: pd.Series           # 组合净值
    benchmark_curve: pd.Series        # 基准（等权/指数）
    weights_history: pd.DataFrame     # dates × symbols 持仓权重
    per_symbol_returns: pd.DataFrame  # dates × symbols 个股贡献
    rebalance_events: list[RebalanceEvent]
    metrics: dict[str, float]         # 组合级指标
    per_symbol_metrics: dict[str, dict[str, float]]  # 个股级指标


class PortfolioEngine:
    """组合回测引擎 — 聚合单股回测 + 换仓模拟。

    Phase 1 实现: 不修改核心 Engine，用加权聚合实现。
    """

    def __init__(
        self,
        rebalance_freq: str = "monthly",  # daily / weekly / monthly
        commission_rate: float = 0.0003,
        min_commission: float = 5.0,
        slippage_rate: float = 0.0,
    ):
        self._rebalance_freq = rebalance_freq
        self._commission_rate = commission_rate
        self._min_commission = min_commission
        self._slippage_rate = slippage_rate

    def run(
        self,
        universe_data: dict[str, pd.DataFrame],
        weights: pd.DataFrame,
        initial_capital: float = 100_000.0,
        benchmark: pd.Series | None = None,
    ) -> PortfolioResult:
        """
        执行组合回测。

        Args:
            universe_data: {symbol: OHLCV DataFrame}
            weights: DataFrame(dates × symbols), 目标权重
            initial_capital: 初始资金
            benchmark: 可选基准净值（默认等权买入持有）

        算法 (逐日模拟，非简单加权聚合):

        rebalance_dates = Rebalancer(freq).get_rebalance_dates(dates)
        actual_weights = {sym: 0.0 for sym in symbols}  # 实际持仓权重
        portfolio_value = initial_capital

        for day in trading_days:
            # 1. 计算当日个股收益
            returns = {sym: data[sym].adj_close.pct_change()[day] for sym}

            # 2. 权重自然漂移（非换仓日的关键行为）
            for sym in symbols:
                actual_weights[sym] *= (1 + returns[sym])
            # 归一化（总权重可能 != 1 因为价格变动）
            total = sum(actual_weights.values())
            if total > 0:
                actual_weights = {s: w / total for s, w in actual_weights.items()}

            # 3. 换仓日：调整到目标权重
            if day in rebalance_dates:
                # 前瞻偏差防护：引擎切片 universe_data 至 [day-lookback, day-1]
                # 策略函数物理上看不到 day 及之后数据（不依赖 shift(1)）
                sliced = slice_data(universe_data, day, strategy.lookback_days)
                target = strategy.generate_weights(sliced, day, actual_weights, prev_returns)
                turnover = sum(|target[s] - actual_weights[s]|) / 2
                cost = turnover * portfolio_value * commission_rate
                actual_weights = target
                record RebalanceEvent(day, old, new, turnover, cost)

            # 4. 当日 PnL
            daily_return = sum(actual_weights[s] * returns[s]) - cost/portfolio_value
            portfolio_value *= (1 + daily_return)

        关键设计点:
        - 引擎切片 universe_data 至 day-1（防前瞻偏差，不依赖 shift(1)）
        - 非换仓日权重随价格漂移（涨的股占比自然增大）
        - 停牌股 NaN 收益 → 0（价格不变，权重保持）
        - 换仓时跳过无数据/停牌股票
        """
        ...
```

### 3.6 Rebalancer — 换仓调度

```python
# ez/portfolio/rebalancer.py

class Rebalancer:
    """决定哪些日期需要换仓。"""

    def __init__(self, freq: str = "monthly"):
        """
        freq: "daily" | "weekly" | "monthly"
        daily — 每个交易日换仓
        weekly — 每周第一个交易日换仓
        monthly — 每月第一个交易日换仓
        """
        self._freq = freq

    def get_rebalance_dates(self, dates: pd.DatetimeIndex) -> list[datetime]:
        """返回需要换仓的日期列表。"""
        if self._freq == "daily":
            return list(dates)
        elif self._freq == "weekly":
            return list(dates.to_series().groupby(dates.isocalendar().week).first())
        elif self._freq == "monthly":
            return list(dates.to_series().groupby(dates.to_period("M")).first())
        raise ValueError(f"Unknown frequency: {self._freq}")
```

### 3.7 组合级指标

```python
# ez/portfolio/metrics.py

def compute_portfolio_metrics(result: PortfolioResult) -> dict[str, float]:
    """计算组合级指标。"""
    returns = result.equity_curve.pct_change().dropna()
    return {
        # 收益
        "total_return": ...,
        "annualized_return": ...,
        "annualized_volatility": ...,
        "sharpe_ratio": ...,
        "sortino_ratio": ...,
        "max_drawdown": ...,

        # 换手
        "avg_turnover": np.mean([e.turnover for e in result.rebalance_events]),
        "total_cost": sum(e.cost for e in result.rebalance_events),

        # 集中度
        "avg_holding_count": ...,
        "max_single_weight": result.weights_history.max().max(),
        "hhi": ...,                     # 赫芬达尔指数

        # 归因
        "top_contributor": ...,         # 贡献最大的股票
        "bottom_contributor": ...,      # 拖累最大的股票

        # vs 基准
        "excess_return": ...,
        "information_ratio": ...,
        "tracking_error": ...,
    }
```

---

## 4. 典型使用流程

### 4.1 动量轮动策略

```python
from ez.portfolio.universe import Universe, fetch_universe_data
from ez.portfolio.cross_factor import MomentumRank
from ez.portfolio.portfolio_strategy import TopNRotation
from ez.portfolio.allocator import EqualWeightAllocator
from ez.portfolio.engine import PortfolioEngine

# 1. 定义股票池
universe = Universe.from_list([
    "000001.SZ", "600519.SH", "601318.SH", "000333.SZ",
    "600036.SH", "300750.SZ", "000858.SZ", "601012.SH",
    "603288.SH", "002714.SZ", "600276.SH", "000568.SZ",
], market="cn_stock")

# 2. 获取数据
data = fetch_universe_data(universe, date(2020,1,1), date(2024,12,31), chain)

# 3. 策略: 每月选动量 top-5
strategy = TopNRotation(factor=MomentumRank(20), top_n=5)

# 4. 计算截面因子
factor_scores = {}
for factor in strategy.required_cross_factors():
    factor_scores[factor.name] = factor.compute(data)

# 5. 生成权重
raw_weights = strategy.generate_weights(data, factor_scores)

# 6. 分配器（可选）
allocator = EqualWeightAllocator()
weights = allocator.allocate(raw_weights)

# 7. 回测
engine = PortfolioEngine(rebalance_freq="monthly")
result = engine.run(data, weights, initial_capital=1_000_000)

# 8. 查看结果
print(f"总收益: {result.metrics['total_return']:.1%}")
print(f"年化Sharpe: {result.metrics['sharpe_ratio']:.2f}")
print(f"平均换手: {result.metrics['avg_turnover']:.1%}")
print(f"平均持股: {result.metrics['avg_holding_count']:.0f}")
```

### 4.2 低波动轮动

```python
from ez.portfolio.cross_factor import ReverseVolatilityRank

strategy = TopNRotation(factor=ReverseVolatilityRank(20), top_n=10)
# ... 同上流程
```

### 4.3 多因子组合

```python
class MultiFactorRotation(PortfolioStrategy):
    """多因子综合打分轮动。"""

    def __init__(self, top_n: int = 10):
        self._top_n = top_n

    def required_cross_factors(self):
        return [MomentumRank(20), ReverseVolatilityRank(20), VolumeRank(20)]

    def generate_weights(self, universe_data, factor_scores):
        # 综合打分 = 等权平均
        combined = sum(factor_scores.values()) / len(factor_scores)
        weights = pd.DataFrame(0.0, index=combined.index, columns=combined.columns)
        for date in combined.index:
            row = combined.loc[date].dropna()
            if len(row) >= self._top_n:
                top = row.nlargest(self._top_n).index
                weights.loc[date, top] = 1.0 / self._top_n
        return weights
```

---

## 5. API 端点

```
POST /api/portfolio/run           — 执行组合回测
GET  /api/portfolio/{id}          — 获取组合回测结果
GET  /api/portfolio/list          — 列表
POST /api/portfolio/search        — 批量搜索（类似 candidates/search）
```

**PortfolioRunRequest:**
```json
{
    "symbols": ["000001.SZ", "600519.SH", "601318.SH", ...],
    "market": "cn_stock",
    "period": "daily",
    "start_date": "2020-01-01",
    "end_date": "2024-12-31",
    "strategy_name": "TopNRotation",
    "strategy_params": {"top_n": 5, "factor": "momentum_rank_20"},
    "rebalance_freq": "monthly",
    "allocator": "equal_weight",
    "initial_capital": 1000000
}
```

---

## 6. 前端页面

在 Navbar 新增 **Portfolio** 标签：

| 区域 | 内容 |
|------|------|
| 股票池选择 | 手动输入 / 指数成分股 / 自定义筛选 |
| 策略配置 | 选组合策略 + 因子 + 换仓频率 + 分配器 |
| 回测结果 | 组合净值曲线 + 基准对比 + 换仓记录 |
| 持仓分析 | 权重时序图 + 集中度 + 行业分布 |
| 归因分析 | 个股贡献排名 + 换手成本分析 |

---

## 7. 与现有系统的关系

### 7.1 不修改核心文件

| 核心文件 | 影响 |
|---------|------|
| Strategy ABC | 不改。PortfolioStrategy 是独立 ABC |
| Factor ABC | 不改。CrossSectionalFactor 是独立 ABC |
| Engine | 不改。Phase 1 通过加权聚合实现 |
| types.py | 仅追加新类型（PortfolioResult 等） |
| Runner | 不改。PortfolioRunner 是新类 |

### 7.2 复用现有能力

| 已有能力 | 组合层如何复用 |
|---------|---------------|
| DataProviderChain | fetch_universe_data 循环调用 |
| 单股回测 Engine | Phase 1 可选用于计算个股指标 |
| ResearchGate | 可扩展支持组合级 Gate 规则 |
| ExperimentStore | PortfolioResult 存储（新表） |
| BatchRunner | 多组合参数搜索 |

### 7.3 架构层级

```
Layer 7: Web (Portfolio 页面)
Layer 6: API (portfolio routes)
Layer 5: ez/portfolio/ (新模块)  ← 与 ez/agent/ 同层
Layer 4: ez/backtest/ (复用)
Layer 3: ez/strategy/, ez/factor/ (复用)
Layer 2: ez/data/ (复用)
Layer 1: ez/core/ (不动)
```

---

## 8. 测试策略

| 测试类别 | 内容 |
|---------|------|
| Contract test | CrossSectionalFactor / PortfolioStrategy ABC 合规 |
| 不变量 | 组合权重和 <= 1.0，现金 >= 0 |
| 换仓正确性 | 换仓日权重变化，非换仓日权重漂移 |
| 成本计算 | 换仓成本 = 交易额 × 费率 |
| 边界情况 | 空股票池、全部停牌、单股退市 |
| 归因一致性 | sum(个股贡献) == 组合收益 |
| vs 单股 | 单股组合结果 ≈ 单股回测结果 |

---

## 9. 实施计划

### V2.9 交付（8 个任务，不拆 Phase）

- P1. Universe + fetch_universe_data + 时间对齐 + NaN 处理规则
- P2. CrossSectionalFactor ABC + MomentumRank, VolumeRank, ReverseVolatilityRank
- P3. PortfolioStrategy ABC + TopNRotation（接收因子实例）+ MultiFactorRotation
- P4. Allocator — EqualWeight, MaxWeight, RiskParity
- P5. PortfolioEngine — 逐日模拟（权重漂移 + 引擎切片防前瞻 + 离散股数记账 + 换仓成本）
- P6. 组合级指标（换手率 / 集中度 / 归因 / vs 基准）
- P7. 组合 API (`/api/portfolio/`) + 持久化（新 DuckDB 表）
- P8. 前端 Portfolio 页面（股票池 + 策略配置 + 净值曲线 + 持仓分析）

### Exit Gate

- [ ] 12 只股票的动量轮动跑通（月度换仓，2020-2024）
- [ ] 前瞻偏差验证：shuffle 权重后收益不优于随机
- [ ] 组合净值曲线 + 基准对比正确
- [ ] 换仓成本计算正确（交易额 × 费率）
- [ ] 权重和不变量 <= 1.0，现金 >= 0
- [ ] 非换仓日权重自然漂移（不是每天强制调回目标）
- [ ] 停牌股 NaN 处理正确（收益→0，换仓时跳过）
- [ ] 截面因子 contract test 通过
- [ ] 单股组合结果 ≈ 单股回测结果（退化验证）
- [ ] 前端可视化可用

---

## 10. 已知局限（V2.9 范围内不解决）

> **注意**：以下局限表已根据 Codex 审计修正更新。V2.9 roadmap 已将 PIT 动态成分股和离散股数记账提升为硬门槛，不再后置。

| 局限 | 说明 | 状态 |
|------|------|------|
| ~~静态股票池~~ | ~~不支持动态成分股~~ | **V2.9 必做**（Codex #3: PIT Universe + 动态成分 + 退市/IPO，见 roadmap P1） |
| **停牌处理** | 停牌股 NaN 收益 → 0（假设价格不变）。换仓时跳过无数据股票。 | V2.9 内实现 |
| **涨跌停重分配** | 涨停买不进 → 权重重分配给其他标的 | **V2.9 必做**（见 roadmap P5） |
| ~~无离散股数~~ | ~~按连续权重分配~~ | **V2.9 必做**（Codex #4: 离散股数记账 + 会计不变量，见 roadmap P5） |
| **无行业/风格约束** | 不限制行业集中度。需要行业分类数据源。 | V2.11 |

---

## 11. 优先级较低（不是不做，时机未到）

- **多空组合**: long top-N + short bottom-N（需要融券支持，A股限制大）
- **行业约束**: 行业权重上限（需要行业分类数据）
- **优化器**: 均值-方差 / Black-Litterman（需要协方差矩阵估计）
- **交易型组合**: 逐笔撮合（需要 V3.0 OMS 支持）
- **跨市场组合**: A股+港股+美股（需要汇率+时区对齐）

---

## 12. 自审修复记录

| # | 问题 | 严重度 | 修复 |
|---|------|--------|------|
| 1 | generate_weights 接收全量数据，无前瞻偏差防护 | P0 | 引擎切片 universe_data 至 date-1（Codex 审计 #2 修正，不依赖 shift(1)） |
| 2 | Phase 1 没有截面因子无法做轮动 | P1 | 合并 Phase 1+2 为单次交付 |
| 3 | "加权聚合"描述模糊，缺逐日模拟伪代码 | P1 | 补充完整伪代码，区分目标权重/实际权重/漂移 |
| 4 | 静态股票池 + NaN 处理未定义 | P1 | 新增"已知局限"章节，定义 NaN→0 规则 |
| 5 | MarketRules 依赖声明但未设计接口 | P2 | 改为可选依赖，PortfolioEngine 预留 market_rules 参数 |
| 6 | TopNRotation.required_cross_factors 和 factor_name 脱耦 | P2 | 改为接收因子实例而非名称字符串 |
