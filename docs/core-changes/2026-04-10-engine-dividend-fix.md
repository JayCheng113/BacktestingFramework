# Core-Change Proposal: Engine Dividend Handling Fix (V2.18.1)

> **状态**: 提案
> **影响范围**: `ez/portfolio/engine.py` (~30 行修改)
> **向后兼容**: 是 — 默认行为保持一致, 修复的是 use_open_price=True 下的长期持有策略 bug

## 问题

`ez/portfolio/engine.py:197-207` 的估值逻辑对 `use_open_price=True` 模式使用 `raw_close`(未复权)价格做每日估值,但引擎**没有分红账务处理**。这导致跨越 ETF/股票分红日的长期持有策略被系统性低估。

### 具体 Bug 表现

以 `StaticLowVol` 策略 (100% 持有红利低波 100 ETF 512890) 为例, 回测 2020-2024:

**use_open_price=True (当前行为, 错)**:
- 年化收益: **-0.5%**
- Sharpe: 0.18
- Max Drawdown: **-58%**

**use_open_price=False (用 adj_close, 对)**:
- 年化收益: **+13.9%**
- Sharpe: 0.80
- Max Drawdown: -15.8%

差距: **14.4 个百分点年化**。根因是 2021-10-25 的分红除权日:
- raw_close: 1.639 → 0.801 (-51.13%,真实除权)
- adj_close: 0.8011 → 0.8010 (连续,正确反映总收益)
- 引擎用 raw_close 估值,当天持仓价值"突降 51%",但实际用户拿到了对应的现金分红,总资产没变。

### 影响范围

| 策略类型 | 影响 |
|---|---|
| 高换手动量 (EtfRotateCombo / EtfMacdRotation / EtfSectorSwitch QMT 移植策略) | **小** — 持仓通常不跨分红日,5 年回测可能踩到 1-2 个分红日 |
| 中等换手轮动 (月度调仓) | **中** — 偶尔踩到分红日 |
| 长期持有 (RiskParityAllWeather, StaticLowVol, StrategyEnsemble 组合) | **严重** — 每个分红日都被算成亏损 |
| benchmark 曲线 (benchmark_symbol) | **严重** — 基准曲线也被 raw_close 跟踪,导致 alpha/beta 计算偏 |
| V2.15 模拟盘 | **严重** — 长期持仓策略回测与实盘背离 |

### 代码位置

`ez/portfolio/engine.py:183-212`:
```python
        exec_prices: dict[str, float] = {}  # open prices for trade execution only
        for sym in holdings.keys() | tradeable:
            if sym not in _sym_data:
                continue
            sdates, adj_arr, raw_arr, open_arr, date_set = _sym_data[sym]
            idx = bisect.bisect_right(sdates, day) - 1
            if idx >= 0:
                adj_val = adj_arr[idx]
                raw_val = raw_arr[idx]
                # prices = close for equity tracking
                # QMT compat (use_open_price): use raw close, not adj_close
                if use_open_price:
                    if not np.isnan(raw_val):
                        prices[sym] = raw_val   # ← BUG: 未处理分红
                    elif not np.isnan(adj_val):
                        prices[sym] = adj_val
                else:
                    if not np.isnan(adj_val):
                        prices[sym] = adj_val
                    elif not np.isnan(raw_val):
                        prices[sym] = raw_val
                # exec_prices = open (for trade execution only, when use_open_price)
                if use_open_price and day in date_set:
                    open_val = open_arr[idx]
                    if not np.isnan(open_val):
                        exec_prices[sym] = open_val   # ← BUG: raw_open 和 adj_close 单位不同
```

benchmark 曲线类似问题, `engine.py:500+`。

## 设计方案

### 方案 A (推荐): 统一 adj 单位系统

**核心原则**: 估值**永远**用 `adj_close` (已复权),`use_open_price=True` 时交易价格用 `adj_open = open × (adj_close / close)` (复权 open)。

这样所有价格都在同一个 "复权单位" 系统里,分红通过 adj_close 的连续性自动处理,无需显式分红账务。

### 改动清单

| 文件 | 改动 | 类型 |
|---|---|---|
| `ez/portfolio/engine.py` | 估值逻辑 + 交易价格逻辑 + benchmark (~30 行) | Core |
| `tests/test_portfolio/test_engine_dividend_handling.py` | 新建测试文件 | Test |
| `CLAUDE.md` | V2.18.1 post-release 条目 | Doc |
| `ez/portfolio/CLAUDE.md` | 记录设计决策变更 | Doc |

### 代码改动

**1. 估值逻辑 (engine.py:197-207)**

```python
        if idx >= 0:
            adj_val = adj_arr[idx]
            raw_val = raw_arr[idx]
            open_val = open_arr[idx] if day in date_set else float("nan")

            # V2.18.1: 统一用 adj_close 估值. 消除 use_open_price+raw_close
            # 对长期持有策略的分红日低估问题.
            if not np.isnan(adj_val):
                prices[sym] = adj_val
            elif not np.isnan(raw_val):
                prices[sym] = raw_val

            # V2.18.1: use_open_price 时交易价用 adj_open = open × (adj_close / close)
            # 这样交易价格和估值价格在同一复权单位. 分红通过 adj_close 连续性自动处理.
            if use_open_price and day in date_set and not np.isnan(open_val):
                if not np.isnan(raw_val) and raw_val > 0 and not np.isnan(adj_val):
                    adj_open = open_val * (adj_val / raw_val)
                    exec_prices[sym] = adj_open
                else:
                    exec_prices[sym] = open_val  # fallback
```

**2. raw_close_today 处理 (engine.py:216-217)**

raw_close_today 用于涨跌停判定,保持用 raw_close (市场规则必须用真实价格),不改:
```python
if not np.isnan(raw_val):
    raw_close_today[sym] = raw_val  # 保持 raw 用于 limit price 判定
```

**3. benchmark 曲线 (engine.py:500+)**

benchmark 跟踪应当永远用 adj_close (代表 buy-and-hold 总回报):
```python
bench_dates, bench_adj, bench_raw, _bench_open, _ = _sym_data[benchmark_symbol]
# V2.18.1: benchmark 用 adj_close 跟踪 buy-and-hold 总回报
for d in result.dates:
    bidx = bisect.bisect_right(bench_dates, d) - 1
    if bidx >= 0 and not np.isnan(bench_adj[bidx]):
        bench_curve.append(bench_adj[bidx])  # ← 改为 adj_close
    ...
```

### 兼容性分析

**向后兼容性**:

1. **默认参数行为 (use_open_price=False) 完全不变** — 这是之前正常工作的模式
2. **use_open_price=True 的高换手策略 (QMT 移植策略)** 行为会有**小幅变化**:
   - 非分红日: adj_open / adj_close 的比例 = raw_open / raw_close 的比例,所以 daily return 不变
   - 分红日: 之前被低估,现在正确处理,**数字会更好** (不是更差)
   - 预期影响: Sharpe/Calmar 略微上升 (几个 basis point),MDD 略微下降

3. **现有测试兼容性**:
   - `tests/test_portfolio/` 的所有测试默认 `use_open_price=False`,不受影响
   - 少数明确测试 `use_open_price=True` 的测试 (V2.17 加的),可能需要更新期望值

4. **历史回测数据库 (portfolio_runs)**:
   - V2.18 之前存入的 run 用旧行为,V2.18.1 之后用新行为
   - 不做迁移,类似 V2.12.2 的指标公式变更处理
   - 已知限制加一条记录

## 测试计划

**新增测试** (`tests/test_portfolio/test_engine_dividend_handling.py`):

1. **test_dividend_day_equity_continuity**: 构造含分红日的模拟数据,100% 持有策略 equity 在分红日连续 (差异 < 1%)
2. **test_use_open_price_matches_close_price_no_dividend**: 非分红日 use_open_price=True 和 False 的结果一致 (差异 < 0.5%)
3. **test_adj_open_calculation_correct**: 验证 adj_open = open × (adj_close / close)
4. **test_benchmark_tracks_adj_close**: benchmark 曲线等于 adj_close buy-and-hold 的收益率
5. **test_long_hold_strategy_not_underestimated**: 长期持有策略跨 3+ 分红日,equity 总收益率和 adj_close 总收益率一致

**现有测试回归**:
- `pytest tests/test_portfolio/ -v` 必须全部通过
- `pytest tests/test_backtest/ -v` 必须全部通过
- V2.17 的 QMT 策略集成测试数字可能有小变化,手动验证新数字合理

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 少数 V2.17 测试的期望值变化 | 手动逐项验证新数字, 更新测试期望值 |
| 外部用户依赖 use_open_price=True 的旧行为 | 通过 CLAUDE.md 已知限制明确告知 |
| benchmark 曲线 shape 变化 | 变化符合预期 (更准),不是 regression |
| 分红日的 lot_rounding 可能有小偏差 | adj_open 的小数精度足够 |

## 下一步

1. 实现 `ez/portfolio/engine.py` 的改动 (~30 行)
2. 新建 `tests/test_portfolio/test_engine_dividend_handling.py` (5 个测试)
3. 跑完整测试集 `pytest tests/ -v` 确认无 regression
4. 对比 V2.17 的 3 个 QMT 策略 (EtfMacdRotation/EtfSectorSwitch/EtfRotateCombo) 修复前后的 Sharpe/MDD/Calmar
5. 更新 CLAUDE.md 和 ez/portfolio/CLAUDE.md
