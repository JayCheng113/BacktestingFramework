# Deep Audit & Bug Fix — Design Spec

**Date:** 2026-04-06
**Scope:** Bug-only fixes + regression tests. No architecture changes, no new features.
**Goal:** 使项目达到团队可用的可靠性标准。

---

## Methodology

五层并行审查 → 交叉验证 → 剔除误报 → 按优先级分组。
以下所有 bug 均经代码验证确认，标注了具体文件和行号。

---

## P0 — 数据/计算正确性 (静默产生错误结果)

### BUG-01: `_known_sparse_symbols` 永久缓存，不过期
**File:** `ez/data/provider.py:87, 192`
**Problem:** 一旦某 symbol 被标记为 sparse（bar 数 < 预期 75%），永远不会被重新 fetch。新上市股、复牌股、新 ETF 会永久拿到空数据。`set` 类型，无 TTL、无 max-size、无 session 重置。
**Impact:** 团队成员如果在某只股票 IPO 初期查过一次（数据不足），后续该股永远缺失。
**Fix:** 加 TTL 机制 — `dict[key, float]` 替代 `set`，记录标记时间，超过 24h 的 key 允许重试。在 `get_kline()` 查询前检查过期。
**Test:** 模拟 sparse 标记 → 等待过期 → 验证重新 fetch。

### BUG-02: AKShare raw fetch 失败时 qfq 被当作 raw close
**File:** `ez/data/providers/akshare_provider.py:88-104, 143-146`
**Problem:** raw 价格 fetch 抛异常时，`df_raw = None`，后续 `open/high/low/close` 全部用 qfq 调整价填充。但 `close` 字段应为未调整价格，因为涨跌停判定 (`market_rules.py`) 用 `raw_close` 做 `(today - prev) / prev` 计算。用 qfq 值会导致：
- 除权日涨跌停误判（前复权价 vs 原始价差异可达 50%+）
- 整手判断的价格基准偏移
**Impact:** AKShare 作为免费 fallback，触发率不低。涨跌停判断失效 = 回测在不可交易的价格上成交。
**Fix:** raw fetch 失败时，对 `close` 字段标记一个 `_raw_fallback` flag，让 `MarketRulesMatcher` 在该 bar 跳过涨跌停检查（而不是用错误价格做判断）。或者更简单：raw 失败时整条 bar 的 limit 检查 disable + 发 warning。
**Test:** Mock raw fetch 异常 → 验证 bar 的 limit 检查被跳过 + warning 记录。

### BUG-03: MLDiagnostics OOS IC 可能跨 retrain 混合模型
**File:** `ez/portfolio/ml_diagnostics.py:359-362`
**Problem:** OOS IC 计算使用 retrain 后的固定窗口 `min(max(retrain_freq, 21), 42)` 天内的 factor scores vs forward returns。但如果在这个窗口内发生了又一次 retrain，OOS IC 混合了两个不同模型的预测。`retrain_count` 标记只在 factor 内部维护，diagnostics 无法区分哪些预测来自哪个模型。
**Impact:** 过拟合评分 `overfitting_score = max(0, (IS-OOS)/|IS|)` 基于不纯的 OOS IC，可能低估过拟合。
**Fix:** 在 MLAlpha 的 `_predict` 中记录 `self._retrain_count` 到输出 metadata（或新增 `_prediction_model_id` 属性），让 diagnostics 可以按 model version 分割 OOS 预测。
**Test:** 创建 retrain_freq=21 的 MLAlpha，运行 diagnostics，验证 OOS IC 只用同一模型版本的预测。

---

## P1 — 隐匿逻辑 Bug (日常使用中会触发)

### BUG-04: 会计 invariant 用 `assert` 而非显式检查
**File:** `ez/portfolio/engine.py:460-464`
**Problem:** `assert cash >= -EPS_FUND` 和 `assert equity > 0` 在 `python -O` (optimized mode) 下被完全跳过。某些打包工具（PyInstaller、Nuitka）默认启用 `-O`。如果会计不变量被违反，程序不 raise，继续运行产出错误结果。
**Impact:** exe 打包发布时有真实风险。
**Fix:** 替换为 `if cash < -EPS_FUND: raise AccountingError(...)` 显式检查。
**Test:** 已有 invariant 测试覆盖，改为 explicit 后行为不变。添加一个测试确认非 assert。

### BUG-05: `app.py` lifespan `aclose()` 异常阻断 `close_resources()`
**File:** `ez/api/app.py:40-47`
**Problem:**
```python
provider = get_cached_provider()
if provider is not None:
    await provider.aclose()  # 如果这里抛异常...
close_resources()  # ...这行永远不会执行
```
LLM provider 网络异常时，DuckDB store 不会关闭。
**Fix:** `try/finally` 包裹。
**Test:** Mock `aclose()` raise → 验证 `close_resources()` 仍被调用。

### BUG-06: 前端 ML Diagnostics 无 race token
**File:** `web/src/components/PortfolioFactorContent.tsx:319-336`
**Problem:** `runDiagnostics()` 是 async 函数，无 `useRef` token 保护。用户快速切换 ML Alpha 或改变日期时，旧请求可能后返回覆盖新结果。项目其他 async handler (evalTokenRef, fundaTokenRef, compareTokenRef) 都有 token 保护，唯独 diagnostics 遗漏。
**Fix:** 添加 `diagTokenRef = useRef(0)`，请求前 `++diagTokenRef.current`，响应后比对。
**Test:** 手动验证 / 现有 pattern 一致性。

### BUG-07: `fullWeights` 输入参数变化时不清空
**File:** `web/src/components/PortfolioRunContent.tsx:130-135, 178`
**Problem:** `fullWeights` 仅在 `result?.run_id` 变化时清空。但用户可以切换 symbols/dates/strategy 而不运行新回测，此时 `fullWeights` 还是上一个 run 的数据。`weightsToShow = fullWeights || result?.weights_history` 会显示旧股票的持仓权重。
**Fix:** 在 `useEffect` 中监听输入参数变化，清空 `fullWeights`。
**Test:** 手动验证切换后无残留。

### BUG-08: Index benchmark 不按 market 过滤
**File:** `web/src/components/PortfolioRunContent.tsx:296-302`
**Problem:** 指数增强的 benchmark 下拉总是显示 CSI300/CSI500/CSI1000（A 股指数），不管 market 是 `us_stock` 还是 `hk_stock`。用户可以设定美股追踪沪深 300 的 tracking error 约束。
**Fix:** 根据 `market` prop 过滤 benchmark options。非 `cn_stock` 时隐藏 A 股指数（或显示 placeholder 提示暂不支持）。
**Test:** 手动验证切换 market 后 benchmark 列表变化。

### BUG-09: CandidateSearch NaN 参数静默过滤
**File:** `web/src/components/CandidateSearch.tsx:466-470`
**Problem:** 用户输入 `1.5, 2.x, 3` 时，`parseFloat("2.x")` 返回 `2`（JavaScript parseFloat 停在第一个非数字字符处）... 实际上 `parseFloat("2.x")` = `2`，所以不会 NaN。但 `parseFloat("abc")` = NaN，会被 `.filter(v => !isNaN(v))` 静默移除。用户以为设了 3 个值实际只用了 2 个。
**Fix:** 过滤后如果有 NaN 被移除，显示 warning 提示。
**Test:** 手动验证。

### BUG-10: DataValidator 不检查负价格
**File:** `ez/data/validator.py:43-53`
**Problem:** `_check_bar()` 验证 OHLC 一致性（high >= low 等）但不检查 `price < 0`。数据源返回负价格（如 API 错误、数据损坏）会通过验证，导致因子计算产出 NaN/inf。
**Fix:** 在 `_check_bar()` 开头加 `if any(v < 0 for v in [o, h, l, c]): return False`。
**Test:** 负价格 bar → 验证被 reject。

---

## P2 — 边缘情况 / 健壮性

### BUG-11: `get_daily_basic_at()` 注释说 5 天实际循环 6 次
**File:** `ez/data/fundamental.py:388`
**Problem:** `for _ in range(6)` 注释说 "up to 5 days back" 但循环 6 次。
**Fix:** 改注释为 "up to 6 calendar days" 或改 `range(5)`。实际影响极小。

### BUG-12: ChatPanel localStorage 激进裁剪
**File:** `web/src/components/ChatPanel.tsx:50-65`
**Problem:** quota 异常时直接裁到 10 条对话 + 每条只留最后 20 消息，无用户提示。
**Fix:** 裁剪前显示 toast 提示 "存储空间不足，已清理旧对话"。

### BUG-13: ExperimentPanel 硬编码结束日期
**File:** `web/src/components/ExperimentPanel.tsx:25-26`
**Problem:** `new Date(2024, 11, 31)` 固定到 2024-12-31。2025 年后用户看到的默认日期范围终止于过去。
**Fix:** `new Date()` 取当前日期。

### BUG-14: Chat SSE JSON.parse 错误被吞
**File:** `web/src/components/ChatPanel.tsx:371`
**Problem:** `catch {}` 完全吞掉解析错误，丢失工具结果无任何提示。
**Fix:** 在 catch 中 `console.warn` 记录。

### BUG-15: Navbar fetch 无 AbortController
**File:** `web/src/components/Navbar.tsx:22-29`
**Problem:** 组件卸载时 inflight fetch 回调更新已卸载组件 state（React warning）。
**Fix:** useEffect cleanup 中 abort。

### BUG-16: Sortino ratio ddof=0 与 Sharpe ddof=1 不一致
**File:** `ez/backtest/metrics.py:54-57`
**Problem:** Sharpe 用 `std(ddof=1)`，Sortino 用 `mean()` of squared downside（等效 ddof=0）。同一回测结果中两个比率的分母统计口径不同。
**Note:** 这是公式选择问题而非明确 bug。Sortino 的下行偏差传统上用 population 定义。记录为已知不一致，不在本轮修复（避免改变指标语义）。仅添加注释说明。

### BUG-17: Ensemble correlation warnings 在有 optimizer 时失真
**File:** `ez/portfolio/ensemble.py:331-397`
**Problem:** 相关性基于 hypothetical returns（忽略成本/优化器/风控），有 optimizer 时 realized ≠ hypothetical。
**Note:** 设计局限。添加 docstring 说明。不修代码。

---

## Out of Scope (本轮不修)

| Issue | Reason |
|-------|--------|
| API 认证 | 当前本地 exe 运行，部署前再加 |
| 沙箱 `exec_module` | 本地执行无攻击面 |
| `.env` 原子写入 | 本地场景风险低 |
| LLM 错误泄露 API key | 本地运行无第三方 |
| Symbol 参数注入 | 数据层用 `?` 参数化，风险低 |
| Chat message 长度限制 | 本地 DoS 无意义 |
| Strategy 单字典注册表 | 架构变更，超出 bug-fix 范围 |
| DuckDB 连接管理 | lifespan 已正确关闭 |
| Fundamental cache 竞态 | 已文档化，benign |

---

## Implementation Plan

### Phase 1: Backend P0 (3 fixes + 3 tests)
- BUG-01: sparse cache TTL
- BUG-02: AKShare raw fallback guard
- BUG-03: MLDiagnostics model version isolation

### Phase 2: Backend P1 (3 fixes + 3 tests)
- BUG-04: assert → explicit check
- BUG-05: lifespan try/finally
- BUG-10: negative price validation

### Phase 3: Frontend (6 fixes)
- BUG-06: ML diagnostics race token
- BUG-07: fullWeights invalidation
- BUG-08: benchmark market filter
- BUG-09: NaN param warning
- BUG-13: hardcoded date
- BUG-14: SSE parse error logging

### Phase 4: Cleanup (4 minor fixes)
- BUG-11: comment fix
- BUG-12: localStorage toast
- BUG-15: Navbar AbortController
- BUG-16 & BUG-17: docstring/comment only

### Verification
每个 Phase 完成后运行 `pytest tests/` 确保无回归。前端改动手动验证。
