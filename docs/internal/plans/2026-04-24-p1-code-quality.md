# P1: 代码质量提升 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** 历史实施计划，主要工作已完成；保留用于追溯当时的任务拆分，不作为当前待办清单。

**Goal:** 将核心模块的 magic numbers、缺失 docstrings、JIT 变量名、超长方法提升到优秀开源项目标准。

**Architecture:** 纯重构，不改逻辑。逐文件添加命名常量、docstrings、映射注释，最后拆分 `_simulate()` 长方法。每步之后跑相关测试确认无回归。

**Tech Stack:** Python 3.12, numpy, pandas

**Spec reference:** `docs/internal/specs/2026-04-24-p1-code-quality-design.md`

---

## File Map

| File | Changes |
|------|---------|
| `ez/core/market_rules.py` | +1 常量定义，2 处替换 |
| `ez/factor/evaluator.py` | +4 常量定义，4 处替换 |
| `ez/backtest/walk_forward.py` | +2 常量定义，2 处替换 |
| `ez/backtest/engine.py` | +2 常量定义，5 处替换，+3 docstrings，+JIT 映射注释，_simulate() 拆分 |
| `ez/portfolio/engine.py` | +5 常量定义，10+ 处替换 |
| `ez/portfolio/optimizer.py` | +9 常量定义，10 处替换 |

---

### Task 1: ez/core/market_rules.py — 命名常量

**Files:**
- Modify: `ez/core/market_rules.py`

- [ ] **Step 1: Read file and locate magic numbers**

Run: `grep -n "1e-6" ez/core/market_rules.py`

Confirm two lines use bare `1e-6`.

- [ ] **Step 2: Add constant and replace usages**

在文件的 import 行之后（`from ez.core.matcher import FillResult, Matcher` 之后）添加：

```python

_PRICE_EPSILON = 1e-6  # 浮点价格比较容差，用于涨跌停判断
```

将文件中所有 `1e-6`（应该恰好 2 处）替换为 `_PRICE_EPSILON`。用 replace_all。

- [ ] **Step 3: Run tests**

Run: `scripts/run_pytest_safe.sh tests/test_core/test_market_rules.py -v 2>&1 | tail -10`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add ez/core/market_rules.py
git commit -m "refactor(core): extract _PRICE_EPSILON constant in market_rules.py"
```

---

### Task 2: ez/factor/evaluator.py — 命名常量

**Files:**
- Modify: `ez/factor/evaluator.py`

- [ ] **Step 1: Read file header and locate magic numbers**

Read first 80 lines of `ez/factor/evaluator.py`. Locate:
- `[1, 5, 10, 20]` (IC decay periods)
- `30` and `3` in `window = min(30, len(fv) // 3)` 
- `1e-10` in ICIR zero-guard (two uses)

- [ ] **Step 2: Add constants after imports**

在文件的 import 块之后、第一个 class/function 之前添加：

```python

_IC_DECAY_PERIODS = [1, 5, 10, 20]  # IC 衰减预测期（天）
_IC_ROLLING_WINDOW = 30              # 滚动 IC 的窗口上限
_IC_ROLLING_DIVISOR = 3              # 滚动窗口 = min(_IC_ROLLING_WINDOW, len // _IC_ROLLING_DIVISOR)
_ZERO_THRESHOLD = 1e-10              # ICIR 分母零值保护
```

- [ ] **Step 3: Replace usages**

搜索并替换：
- `[1, 5, 10, 20]` → `_IC_DECAY_PERIODS`（用于 `periods = ...` 的行）
- `min(30, len(fv) // 3)` → `min(_IC_ROLLING_WINDOW, len(fv) // _IC_ROLLING_DIVISOR)`
- ICIR 计算中的两个 `1e-10` → `_ZERO_THRESHOLD`（形如 `if xxx_std > 1e-10 else 0.0` 的行）

注意：文件中可能有其他 `1e-10` 出现在不同上下文（如 `_CONST_TOL = 1e-12` 已经是命名常量），只替换 ICIR 分母保护处的。

- [ ] **Step 4: Run tests**

Run: `scripts/run_pytest_safe.sh tests/test_factor/ -v 2>&1 | tail -10`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add ez/factor/evaluator.py
git commit -m "refactor(factor): extract named constants in evaluator.py"
```

---

### Task 3: ez/backtest/walk_forward.py — 命名常量

**Files:**
- Modify: `ez/backtest/walk_forward.py`

- [ ] **Step 1: Read and locate magic numbers**

Run: `grep -n "= 10\|1e-10" ez/backtest/walk_forward.py`

Confirm: `min_tradeable = 10` 和 `1e-10` 在降级计算处。

- [ ] **Step 2: Add constants after imports**

在 import 块之后添加：

```python

_MIN_OOS_BARS = 10       # OOS 窗口最少交易日数（过少则结果无统计意义）
_ZERO_THRESHOLD = 1e-10  # 零值判断阈值
```

- [ ] **Step 3: Replace usages**

- `min_tradeable = 10` → `min_tradeable = _MIN_OOS_BARS`
- 降级计算中 `abs(is_mean) > 1e-10` → `abs(is_mean) > _ZERO_THRESHOLD`

- [ ] **Step 4: Run tests**

Run: `scripts/run_pytest_safe.sh tests/test_backtest/test_walk_forward.py -v 2>&1 | tail -10`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add ez/backtest/walk_forward.py
git commit -m "refactor(backtest): extract named constants in walk_forward.py"
```

---

### Task 4: ez/portfolio/optimizer.py — 命名常量

**Files:**
- Modify: `ez/portfolio/optimizer.py`

- [ ] **Step 1: Read file and map all magic numbers**

Read the full file. Map every bare numeric constant:
- `0.10` (default max weight)
- `0.04` (fallback covariance diagonal)
- `1e-8` (regularization, appears 2x)
- `1e-20` (Ledoit-Wolf denominator minimum, appears 2x)
- `1e-10` (weight floor + optimizer ftol, appears 2x)
- `1e-6` (weight lower bound)

- [ ] **Step 2: Add constants after imports**

在 import 块之后（`from scipy import optimize` 之后）添加：

```python

# ── 优化器常量 ──
_DEFAULT_MAX_WEIGHT = 0.10          # 默认单票权重上限（10%）
_FALLBACK_VARIANCE = 0.04           # Ledoit-Wolf 退化时的对角协方差（假设 20% 年化波动率）
_COV_REGULARIZATION = 1e-8          # 协方差矩阵正定化修正项
_LEDOIT_WOLF_DENOM_MIN = 1e-20     # Ledoit-Wolf 收缩系数分母最小值
_WEIGHT_FLOOR = 1e-10               # 权重零值判断阈值
_OPTIMIZER_FTOL = 1e-10             # scipy 优化器收敛容差
_PORTFOLIO_VAR_FLOOR = 1e-20        # 组合方差零值保护（风险平价）
_VOL_FLOOR = 1e-8                   # 波动率估计下限
_WEIGHT_LOWER_BOUND = 1e-6          # 优化器权重下界
```

- [ ] **Step 3: Replace all usages**

逐一替换，每个确认上下文正确（跳过注释中的数字，如 line 101 的 `# If max_weight=0.10`）：
- `max_weight: float = 0.10`（line 23，代码）→ `max_weight: float = _DEFAULT_MAX_WEIGHT`
- `np.eye(N) * 0.04` → `np.eye(N) * _FALLBACK_VARIANCE`
- 两处 `1e-8`（正定化修正）→ `_COV_REGULARIZATION`
- 两处 `1e-20`（Ledoit-Wolf 分母 + 组合方差）→ `_LEDOIT_WOLF_DENOM_MIN` 和 `_PORTFOLIO_VAR_FLOOR`（注意区分上下文）
- `1e-10` 在权重阈值处 → `_WEIGHT_FLOOR`
- `"ftol": 1e-10` → `"ftol": _OPTIMIZER_FTOL`
- `np.maximum(vols, 1e-8)` → `np.maximum(vols, _VOL_FLOOR)`
- `bounds = [(1e-6, max_w)]` → `bounds = [(_WEIGHT_LOWER_BOUND, max_w)]`

- [ ] **Step 4: Run tests**

Run: `scripts/run_pytest_safe.sh tests/test_portfolio/test_optimizer.py -v 2>&1 | tail -15`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add ez/portfolio/optimizer.py
git commit -m "refactor(portfolio): extract 9 named constants in optimizer.py"
```

---

### Task 5: ez/portfolio/engine.py — 命名常量

**Files:**
- Modify: `ez/portfolio/engine.py`

- [ ] **Step 1: Read file and map all magic numbers**

Read full file. Find all `252`, `0.03`, `1e-6`, `1e-10` usages.

已知位置：
- Line ~90: 默认 lookback `252`
- Line ~253: 涨跌停 `1e-6`
- Line ~397: 换手率 `1e-6`
- Lines ~542-606: 年化计算中的 `252`（约 5 处）和 `0.03`（1 处）
- Lines ~559, 582, 598: 零值保护 `1e-10`（Sharpe 分母、Sortino 分母、benchmark std）

- [ ] **Step 2: Add constants after imports**

在 import 块之后添加：

```python

# ── 组合引擎常量 ──
_TRADING_DAYS_PER_YEAR = 252   # 年化交易日数
_DEFAULT_RISK_FREE_RATE = 0.03  # 默认无风险利率（3%）
_PRICE_LIMIT_EPSILON = 1e-6    # 涨跌停浮点比较容差
_TURNOVER_EPSILON = 1e-6        # 换手率浮点比较容差
_ZERO_THRESHOLD = 1e-10         # 通用零值保护（Sharpe/Sortino 分母等）
```

- [ ] **Step 3: Replace all usages**

逐一搜索替换：
- 所有独立的 `252` → `_TRADING_DAYS_PER_YEAR`（注意不要替换注释中的 252）
- `RF_ANNUAL = 0.03` → `RF_ANNUAL = _DEFAULT_RISK_FREE_RATE`
- 涨跌停处的 `1e-6` → `_PRICE_LIMIT_EPSILON`
- 换手率处的 `1e-6` → `_TURNOVER_EPSILON`
- 所有 3 处 `1e-10`（lines 559, 582, 598）→ `_ZERO_THRESHOLD`
- `np.sqrt(252)` → `np.sqrt(_TRADING_DAYS_PER_YEAR)`（注意保留 np.sqrt 调用）
- `/ 252` 和 `* 252` → `/ _TRADING_DAYS_PER_YEAR` 和 `* _TRADING_DAYS_PER_YEAR`

- [ ] **Step 4: Run tests**

Run: `scripts/run_pytest_safe.sh tests/test_portfolio/ -x -q 2>&1 | tail -10`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add ez/portfolio/engine.py
git commit -m "refactor(portfolio): extract named constants in engine.py"
```

---

### Task 6: ez/backtest/engine.py — 常量 + Docstrings + JIT 注释

**Files:**
- Modify: `ez/backtest/engine.py`

- [ ] **Step 1: Add constants after imports**

在 import 块之后（`from ez.types import BacktestResult, TradeRecord` 后面的空行之后）添加：

```python

# ── 回测引擎常量 ──
_ZERO_THRESHOLD = 1e-10    # 通用零值判断阈值（股数、损益、权益等）
_WEIGHT_CHANGE_MIN = 1e-3  # 触发调仓的最小权重变化（0.1%）
```

- [ ] **Step 2: Replace 5 处 magic numbers**

替换：
- Line 98 `gross_loss > 1e-10` → `gross_loss > _ZERO_THRESHOLD`
- Line 234 `f_shares > 1e-10` → `f_shares > _ZERO_THRESHOLD`
- Line 304 `> 1e-3` → `> _WEIGHT_CHANGE_MIN`
- Line 324 `shares < 1e-10` → `shares < _ZERO_THRESHOLD`
- Line 329 `shares < 1e-10` → `shares < _ZERO_THRESHOLD`

- [ ] **Step 3: Add docstring to __init__**

当前 `__init__` 无 docstring。在 `def __init__(self, commission_rate, min_commission, risk_free_rate, matcher):` 之后添加：

```python
        """初始化回测引擎。

        Args:
            commission_rate: 佣金费率，默认万 0.8。当 matcher 为 None 时用于构建 SimpleMatcher。
            min_commission: 最低佣金，默认 0（免五）。
            risk_free_rate: 无风险利率，用于 Sharpe/Sortino/Alpha 计算，默认 3%。
            matcher: 撮合器实例。传入则忽略 commission_rate/min_commission。
                支持 SimpleMatcher、SlippageMatcher、MarketRulesMatcher 装饰器链。
        """
```

- [ ] **Step 4: Add docstring to run()**

当前 `run()` 无 docstring。在方法签名之后添加：

```python
        """运行单股回测。

        流程：
        1. 计算策略所需因子
        2. 生成交易信号（自动右移 1 日，避免前视偏差）
        3. 裁剪因子预热期
        4. 执行模拟交易（JIT 快速路径或 Python 循环）
        5. 计算绩效指标 + 可选显著性检验

        Args:
            data: OHLCV DataFrame，需包含 close 和 adj_close 列。
            strategy: 策略实例，需实现 required_factors() 和 generate_signals()。
            initial_capital: 初始资金，默认 100 万。
            skip_significance: 跳过 Bootstrap/Monte Carlo 显著性检验（加速）。

        Returns:
            BacktestResult: 包含 equity_curve、trades、metrics、daily_returns、significance。
        """
```

- [ ] **Step 5: Add JIT mapping comment**

在 `_jit_out = _sim_fn(...)` 调用返回之后、`eq, dr = _jit_out[0], _jit_out[1]` 之前（约 line 208 前）添加：

```python
            # ── JIT 快速路径输出结构 ──
            # [0]  equity_curve      逐日净值 (n_bars,)
            # [1]  daily_returns     逐日收益率 (n_bars,)
            # [2]  trade_entry_bars  成交入场 bar 索引 (n_trades,)
            # [3]  trade_exit_bars   成交出场 bar 索引 (n_trades,)
            # [4]  trade_entry_prices 入场价格 (n_trades,)
            # [5]  trade_exit_prices  出场价格 (n_trades,)
            # [6]  trade_pnl         成交盈亏 (n_trades,)
            # [7]  trade_commissions  成交手续费 (n_trades,)
            # [8]  trade_weights      成交权重 (n_trades,)
            # [9]  trade_count        成交笔数 (scalar)
            # [10] final_shares       期末持仓股数 (scalar)
            # [11] (reserved)
            # [12] final_entry_bar    期末持仓入场 bar (scalar)
            # [13] final_entry_price  期末持仓入场价 (scalar)
            # [14] final_entry_comm   期末持仓入场手续费 (scalar)
```

- [ ] **Step 6: Run tests**

Run: `scripts/run_pytest_safe.sh tests/test_backtest/ -v 2>&1 | tail -15`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add ez/backtest/engine.py
git commit -m "refactor(backtest): named constants, docstrings, JIT mapping in engine.py"
```

---

### Task 7: ez/backtest/engine.py — _simulate() 拆分

**Files:**
- Modify: `ez/backtest/engine.py`

这是最复杂的 Task。将 342 行的 `_simulate()` 拆分为编排层 + 3 个子方法。

- [ ] **Step 1: Read full _simulate() method**

Read `ez/backtest/engine.py` lines 140-481。理解三个逻辑段：
1. adj_open 计算（~30 行）
2. JIT 快速路径（~72 行）
3. Python 循环 + 期末清仓（~220 行）

- [ ] **Step 2: Extract _compute_adj_open()**

在 `_simulate()` 之前创建新方法：

```python
    def _compute_adj_open(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """计算复权调整后的开盘价。

        处理三种情况：
        - open 列存在：adj_open = open * (adj_close / close)
        - open 缺失但 close 存在：fallback 到 close（避免复权因子重复应用）
        - 两者都缺失：直接用 adj_close

        Returns:
            (open_prices, raw_close): 调整后开盘价数组和原始收盘价数组。
        """
        prices = df["adj_close"].values
        if "open" in df.columns:
            _raw_open = df["open"].values
        elif "close" in df.columns:
            _raw_open = df["close"].values
        else:
            _raw_open = prices
        raw_close = df["close"].values if "close" in df.columns else prices
        with np.errstate(divide="ignore", invalid="ignore"):
            _ratio = np.where(
                (raw_close > 0) & np.isfinite(raw_close) & np.isfinite(prices),
                prices / np.where(raw_close > 0, raw_close, 1.0),
                1.0,
            )
        open_prices = _raw_open * _ratio
        return open_prices, raw_close
```

然后在 `_simulate()` 中将 lines 146-173 替换为：

```python
        open_prices, raw_close = self._compute_adj_open(df)
        prices = df["adj_close"].values
```

- [ ] **Step 3: Extract _simulate_jit()**

将 JIT 快速路径（当前 lines 184-255）提取为独立方法：

```python
    def _simulate_jit(
        self,
        prices: np.ndarray,
        open_prices: np.ndarray,
        weights: np.ndarray,
        capital: float,
        df: pd.DataFrame,
    ) -> tuple[pd.Series, list[TradeRecord], pd.Series] | None:
        """尝试 JIT 快速路径执行。条件不满足时返回 None。

        条件：SimpleMatcher 或 SlippageMatcher（无 on_bar）+ 二值信号（0/1）。
        """
```

方法体包含当前的 JIT 条件判断、`_sim_fn` 调用、输出解包（含映射注释）、trade 构建、期末强平。如果条件不满足返回 `None`。

在 `_simulate()` 中替换为：

```python
        jit_result = self._simulate_jit(prices, open_prices, weights, capital, df)
        if jit_result is not None:
            return jit_result
```

- [ ] **Step 4: Extract _simulate_python()**

将 Python 循环 + 期末清仓（当前 lines 257-481）提取为独立方法：

```python
    def _simulate_python(
        self,
        prices: np.ndarray,
        open_prices: np.ndarray,
        raw_close: np.ndarray,
        weights: np.ndarray,
        capital: float,
        df: pd.DataFrame,
    ) -> tuple[pd.Series, list[TradeRecord], pd.Series]:
        """Python 通用路径：支持任意 Matcher 链和连续权重信号。

        包含逐 bar 交易循环和期末持仓虚拟清仓。
        """
```

方法体是当前 lines 257-481 的全部内容。

- [ ] **Step 5: Add docstring to orchestrator _simulate()**

拆分后 `_simulate()` 变成约 30 行的编排层：

```python
    def _simulate(
        self,
        df: pd.DataFrame,
        signals: pd.Series,
        capital: float,
    ) -> tuple[pd.Series, list[TradeRecord], pd.Series]:
        """执行交易模拟，返回净值曲线、成交记录和日收益率。

        两条执行路径：
        - JIT 快速路径：SimpleMatcher/SlippageMatcher + 二值信号 → C++/Numba 向量化
        - Python 通用路径：支持任意 Matcher 链（MarketRulesMatcher 等）和连续权重信号
        """
        open_prices, raw_close = self._compute_adj_open(df)
        prices = df["adj_close"].values
        weights = signals.values
        n = len(prices)

        if n == 0:
            return (
                pd.Series([capital], dtype=float),
                [],
                pd.Series([0.0], dtype=float),
            )

        # 尝试 JIT 快速路径（条件不满足时返回 None）
        jit_result = self._simulate_jit(prices, open_prices, weights, capital, df)
        if jit_result is not None:
            return jit_result

        # Python 通用路径
        return self._simulate_python(
            prices, open_prices, raw_close, weights, capital, df
        )
```

- [ ] **Step 6: Verify method count and line count**

Run: `grep -n "def " ez/backtest/engine.py`

确认：
- `_compute_adj_open` 存在
- `_simulate_jit` 存在
- `_simulate_python` 存在
- `_simulate` 变短

Run: `awk '/def _simulate\(/{start=NR} start && /^    def [^_]/{print NR-start; start=0}' ez/backtest/engine.py`
或手动检查 `_simulate()` 行数不超过 35 行。

- [ ] **Step 7: Run full backtest tests**

Run: `scripts/run_pytest_safe.sh tests/test_backtest/ -v 2>&1 | tail -20`
Expected: All PASS

Run: `scripts/run_pytest_safe.sh tests/test_core/ -v 2>&1 | tail -10`
Expected: All PASS（确认 matcher 集成未受影响）

- [ ] **Step 8: Commit**

```bash
git add ez/backtest/engine.py
git commit -m "refactor(backtest): split _simulate() into _compute_adj_open + _simulate_jit + _simulate_python"
```

---

### Task 8: 最终验证

- [ ] **Step 1: Run full backend test suite**

Run: `scripts/run_pytest_safe.sh tests/ -x -q --deselect tests/test_architecture/test_gates.py::TestCoreStability::test_core_package_no_unlisted_python_files 2>&1 | tail -5`
Expected: 3100+ passed, 0 failed

- [ ] **Step 2: Verify magic numbers replaced**

Run: `grep -rn "1e-10\|1e-6\|1e-3\|1e-8\|1e-20" ez/core/market_rules.py ez/backtest/engine.py ez/backtest/walk_forward.py ez/factor/evaluator.py ez/portfolio/engine.py ez/portfolio/optimizer.py | grep -v "^.*_[A-Z].*=" | grep -v "^.*#"`

每个匹配行应该是常量定义行（`_XXX = 1e-xx`）或注释行，不是逻辑代码中的裸字面量。

- [ ] **Step 3: Verify _simulate() is an orchestrator**

Run: `grep -c "def " ez/backtest/engine.py`
Expected: 至少 6 个方法定义（__init__, run, _simulate, _compute_adj_open, _simulate_jit, _simulate_python）

- [ ] **Step 4: Verify docstrings exist**

Run: `python -c "from ez.backtest.engine import VectorizedBacktestEngine; print(VectorizedBacktestEngine.__init__.__doc__[:20]); print(VectorizedBacktestEngine.run.__doc__[:20])"`
Expected: 两行非空输出

- [ ] **Step 5: Run frontend tests (regression)**

Run: `cd web && npm test -- --run 2>&1 | tail -5`
Expected: 96 tests pass（确认无后端 API 回归影响前端）
