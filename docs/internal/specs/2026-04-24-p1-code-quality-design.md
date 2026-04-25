# P1: 代码质量提升设计

> 日期: 2026-04-24
> 状态: 历史设计，主要工作已实施
> 范围: ez/core, ez/backtest, ez/factor, ez/portfolio 核心模块
> 约束: 不改逻辑、不移文件、不改公开 API 签名

## 目标

将核心模块代码提升到 pandas/scikit-learn 级别的可读性标准。修复四类系统性问题：magic numbers、JIT 变量名、超长方法、缺失 docstrings。

---

## 1. Magic Numbers → 命名常量

**原则：** 常量定义在使用它的文件顶部（不新建 constants.py），遵循 `_UPPER_SNAKE_CASE` 命名。每个常量附一行注释说明含义。

> 注：以下行号基于 2026-04-24 版本，实施时需用 grep 验证实际位置。

### ez/core/market_rules.py（2 处）

```python
# 模块顶部新增
_PRICE_EPSILON = 1e-6  # 浮点价格比较容差，用于涨跌停判断
```

替换：
- Line 57: `1e-6` → `_PRICE_EPSILON`
- Line 118: `1e-6` → `_PRICE_EPSILON`

### ez/backtest/engine.py（5 处）

```python
_ZERO_THRESHOLD = 1e-10      # 通用零值判断阈值（股数、损益等）
_WEIGHT_CHANGE_MIN = 1e-3    # 触发调仓的最小权重变化（0.1%）
```

替换：
- Line 98: `1e-10` → `_ZERO_THRESHOLD`
- Line 234: `1e-10` → `_ZERO_THRESHOLD`
- Line 304: `1e-3` → `_WEIGHT_CHANGE_MIN`
- Line 324: `1e-10` → `_ZERO_THRESHOLD`
- Line 329: `1e-10` → `_ZERO_THRESHOLD`

### ez/backtest/walk_forward.py（2 处）

```python
_MIN_OOS_BARS = 10          # OOS 窗口最少交易日数（过少则结果无统计意义）
_ZERO_THRESHOLD = 1e-10     # 零值判断阈值
```

替换：
- Line 68: `10` → `_MIN_OOS_BARS`
- Line 150: `1e-10` → `_ZERO_THRESHOLD`

### ez/factor/evaluator.py（4 处）

```python
_IC_DECAY_PERIODS = [1, 5, 10, 20]   # IC 衰减预测期（天）
_IC_ROLLING_WINDOW = 30               # 滚动 IC 的窗口上限
_IC_ROLLING_DIVISOR = 3               # 滚动窗口 = min(_IC_ROLLING_WINDOW, len // _IC_ROLLING_DIVISOR)
_ZERO_THRESHOLD = 1e-10               # ICIR 分母零值保护
```

替换：
- Line 40: `[1, 5, 10, 20]` → `_IC_DECAY_PERIODS`
- Line 48: `30` → `_IC_ROLLING_WINDOW`，`3` → `_IC_ROLLING_DIVISOR`
- Line 65-66: `1e-10` → `_ZERO_THRESHOLD`

### ez/portfolio/engine.py（5+ 处）

```python
_TRADING_DAYS_PER_YEAR = 252  # 年化交易日数
_DEFAULT_RISK_FREE_RATE = 0.03  # 默认无风险利率（3%）
_PRICE_LIMIT_EPSILON = 1e-6   # 涨跌停浮点比较容差
_TURNOVER_EPSILON = 1e-6       # 换手率浮点比较容差
_ZERO_THRESHOLD = 1e-10        # 通用零值保护
```

替换：
- Line 90: 默认 `252` → `_TRADING_DAYS_PER_YEAR`
- Line 253: `1e-6` → `_PRICE_LIMIT_EPSILON`
- Line 397: `1e-6` → `_TURNOVER_EPSILON`
- Lines 555-606: `0.03` → `_DEFAULT_RISK_FREE_RATE`，所有 `252` → `_TRADING_DAYS_PER_YEAR`

### ez/portfolio/optimizer.py（10 处）

```python
_DEFAULT_MAX_WEIGHT = 0.10         # 默认单票权重上限（10%）
_FALLBACK_VARIANCE = 0.04           # Ledoit-Wolf 退化时的对角协方差（假设 20% 年化波动率）
_COV_REGULARIZATION = 1e-8         # 协方差矩阵正定化修正项
_LEDOIT_WOLF_DENOM_MIN = 1e-20    # Ledoit-Wolf 分母最小值
_WEIGHT_FLOOR = 1e-10              # 权重零值判断阈值
_OPTIMIZER_FTOL = 1e-10            # scipy 优化器收敛容差
_PORTFOLIO_VAR_FLOOR = 1e-20       # 组合方差零值保护（风险平价）
_VOL_FLOOR = 1e-8                  # 波动率下限
_WEIGHT_LOWER_BOUND = 1e-6         # 优化器权重下界
```

替换 10 处裸数字（lines 23, 40, 44, 54, 57, 201, 247, 307, 315, 319）。

注：原代码 `np.eye(N) * 0.04` 直接替换为 `np.eye(N) * _FALLBACK_VARIANCE`。

---

## 2. JIT 变量名映射注释

**ez/backtest/engine.py lines 207-214**

在解包代码前加结构化注释：

```python
# ── C++ / Numba JIT 快速路径输出结构 ──
# [0]  equity_curve   : 逐日净值 (n_bars,)
# [1]  daily_returns   : 逐日收益率 (n_bars,)
# [2]  trade_entry_bars: 成交入场 bar 索引 (n_trades,)
# [3]  trade_exit_bars : 成交出场 bar 索引 (n_trades,)
# [4]  trade_entry_prices: 入场价格 (n_trades,)
# [5]  trade_exit_prices : 出场价格 (n_trades,)
# [6]  trade_pnl       : 成交盈亏 (n_trades,)
# [7]  trade_commissions: 成交手续费 (n_trades,)
# [8]  trade_weights    : 成交权重 (n_trades,)
# [9]  trade_count      : 成交笔数 (scalar)
# [10] final_shares     : 期末持仓股数 (scalar)
# [11] (reserved)
# [12] final_entry_bar  : 期末持仓入场 bar (scalar)
# [13] final_entry_price: 期末持仓入场价 (scalar)
# [14] final_entry_comm : 期末持仓入场手续费 (scalar)
```

短变量名保持不变（它们在后续 30 行内立即被消费，改长名反而降低局部可读性），但有了映射注释后读者能对照理解。

---

## 3. 超长方法拆分：_simulate()

**当前：** `VectorizedBacktestEngine._simulate()` 342 行（lines 140-481）。

**拆分为 4 个私有方法：**

### _compute_adj_open(df) → pd.Series
- 提取 lines 146-176
- 职责：根据复权因子计算调整后的开盘价
- 处理缺失 open 列的 fallback 逻辑

### _build_trades_from_jit(jit_out, df, capital, signals, weights) → list[dict]
- 提取 lines 208-255
- 职责：将 JIT 输出数组转为 TradeRecord 列表
- 包含期末强平逻辑

### _execute_python_loop(df, signals, weights, capital, matcher) → tuple[np.ndarray, np.ndarray, list[dict]]
- 提取 lines 257-410
- 职责：逐 bar Python 执行循环
- 返回 (equity_curve, daily_returns, trades)

### _terminal_liquidation(shares, entry_bar, entry_price, entry_comm, df, cash, bar_idx, cycle_state) → dict | None
- 提取 lines 412-477
- 职责：期末清仓，生成最终 TradeRecord
- 返回 None 如果无持仓

**拆分后 `_simulate()` 缩减为约 40 行的编排层（概念结构，实际方法名以实现为准）：**

```python
# 概念结构 — 实际实现需保留当前的 JIT 判断逻辑和参数传递
def _simulate(self, df, signals, weights, capital):
    """单股回测模拟。JIT 快速路径可用时走 C++/Numba，否则走 Python 循环。"""
    adj_open = self._compute_adj_open(df)
    
    # JIT 快速路径判断（当前是 isinstance(matcher, SimpleMatcher) + 二值信号检查）
    if <jit_conditions_met>:
        jit_out = <call_cpp_or_numba>(...)
        return self._build_trades_from_jit(jit_out, df, capital, signals, weights)
    
    # Python 通用路径
    equity, daily_ret, trades = self._execute_python_loop(
        df, signals, weights, capital, self._matcher, adj_open
    )
    return equity, daily_ret, trades
```

---

## 4. 缺失 Docstrings

### VectorizedBacktestEngine.__init__()

```python
def __init__(self, strategy, matcher=None, benchmark_symbol=None):
    """初始化回测引擎。

    Args:
        strategy: 策略实例，需实现 required_factors() 和 generate_signals()。
        matcher: 撮合器实例。默认 SimpleMatcher(commission_rate=0.0003)。
            支持 SlippageMatcher、MarketRulesMatcher 等装饰器链。
        benchmark_symbol: 基准标的代码（如 '000300.SH'），用于计算 Alpha/Beta。
            None 则不计算相对指标。
    """
```

### VectorizedBacktestEngine.run()

```python
def run(self, symbol, df, initial_capital=1_000_000):
    """运行单股回测。

    流程：
    1. 计算策略所需因子
    2. 生成交易信号（自动右移 1 日，避免前视偏差）
    3. 裁剪因子预热期
    4. 执行模拟交易（JIT 快速路径或 Python 循环）
    5. 计算绩效指标

    Args:
        symbol: 股票代码。
        df: OHLCV DataFrame，需包含 close/adj_close 列。
        initial_capital: 初始资金，默认 100 万。

    Returns:
        BacktestResult: 包含 equity_curve、trades、metrics、daily_returns。
    """
```

### _simulate() 概览注释

```python
def _simulate(self, ...):
    """执行交易模拟，返回净值曲线、日收益率和成交记录。

    两条执行路径：
    - JIT 快速路径：SimpleMatcher + 二值信号 → C++/Numba 向量化
    - Python 循环：通用路径，支持任意 Matcher 链和连续权重信号

    关键状态：
    - cycle_net_invested / cycle_peak_invested: 跟踪当前持仓周期的投入资金，
      用于计算单笔交易的真实收益率（非简单 entry/exit 价差）。
    """
```

---

## 5. 不做的事情

- **不改公开 API 签名** — 函数参数、返回值类型不变
- **不改逻辑** — 所有常量提取保持原值
- **不移文件** — P2/P3/P4 的工作
- **不给已达标文件加注释** — matcher.py、ts_ops.py、significance.py 等已经很好
- **不改前端代码** — 本轮只管 Python
- **不改 live/、agent/ 代码** — 等 P2/P3/P4

## 验收标准

1. `scripts/run_pytest_safe.sh tests/ -x -q` 全量通过（不含已知的 `_jit_fill.py` 架构测试）
2. 裸数字验证：在目标文件的逻辑代码中（非常量定义行、非注释行），不再出现 `1e-10`、`1e-6`、`1e-3`、`1e-8`、`1e-20`、`1e-12` 等裸字面量。验证方式：`git grep` 输出中每个匹配行都是 `_XXX = 1e-xx` 的定义形式，不是 `if x > 1e-10` 的使用形式。
3. `ez/backtest/engine.py` 中 `_simulate()` 不超过 60 行
4. `VectorizedBacktestEngine.run()` 和 `__init__()` 有完整 docstring
5. JIT 解包处有结构化映射注释
