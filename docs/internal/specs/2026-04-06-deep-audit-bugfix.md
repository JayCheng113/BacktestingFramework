# Deep Audit & Bug Fix — Design Spec

**Date:** 2026-04-06
**Scope:** Bug-only fixes + regression tests. No architecture changes, no new features.
**Goal:** 使项目达到团队可用的可靠性标准。

---

## Methodology

五层并行审查（引擎/组合/数据/API/前端） → 交叉验证 → 剔除误报 → 用户逐项审查。
以下 8 个 bug 均经**代码验证 + 人工复审**确认为当前 tree 中真实存在的问题。

---

## Confirmed Bugs (8 items)

### BUG-01: `_known_sparse_symbols` 永久在线缓存 — 状态泄漏
**File:** `ez/data/provider.py:87, 192`
**Priority:** P0 — 数据正确性
**Problem:** class-level `set` 没有 TTL，没有按市场/窗口刷新。一旦标记为 sparse，长跑进程里后续永远跳过密度检查。新上市股、复牌股、新 ETF 会永久拿到空数据。这不是缓存优化问题，是持续污染数据质量判断的状态泄漏。
**Fix:** `dict[key, float]` 替代 `set`，记录标记时间戳，超过 24h 允许重试。`get_kline()` 查询前检查过期。
**Test:** 模拟 sparse 标记 → mock time 推进 > 24h → 验证重新 fetch 被触发。

### BUG-02: AKShare raw fetch 失败后 qfq close 代替 raw close
**File:** `ez/data/providers/akshare_provider.py:88-104, 143-146`
**Priority:** P0 — 数据正确性
**Problem:** raw fetch 失败后 `df_raw = None`，close 退化成 qfq close。这直接改变回测成交语义：涨跌停判定用 `(raw_close_today - prev_raw_close) / prev_raw_close`，除权日前复权价与原始价差异可达 50%+，导致在不可交易的价格上成交。
**Fix:** raw fetch 失败时，在 bar 上标记 `_raw_unavailable = True`（或将 `close` 设为 NaN），让下游 `MarketRulesMatcher` 在该 bar 跳过涨跌停检查 + 发 warning。不用错误数据做判断。
**Test:** Mock raw fetch 异常 → 验证 limit 检查被跳过 + warning 记录。

### BUG-04: 会计 invariant 用 `assert` 保护
**File:** `ez/portfolio/engine.py:460-464`
**Priority:** P0 — 引擎正确性
**Problem:** `assert cash >= -EPS_FUND` 和 `assert equity > 0` 在 `python -O` 下被剥掉。PyInstaller/Nuitka 默认 `-O`。防线消失后，违反不变量的回测会继续运行产出错误结果，无任何报错。
**Fix:** 替换为 `if cash < -EPS_FUND: raise AccountingError(f"cash={cash} violated invariant")`。
**Test:** 现有 invariant 测试覆盖。新增一个测试 grep 源码确认无 `assert cash` / `assert equity` pattern。

### BUG-05: `aclose()` 异常阻断 `close_resources()`
**File:** `ez/api/app.py:40-47`
**Priority:** P0 — 资源清理
**Problem:** shutdown 路径没有 `try/finally`。`provider.aclose()` 抛异常时 `close_resources()` 不执行，DuckDB store 不关闭。
**Fix:** `try/finally` 包裹 `aclose()` 和 `close_resources()`。
**Test:** Mock `aclose()` raise → 验证 `close_resources()` 仍被调用。

### BUG-06: MLDiagnosticsPanel 缺 race token
**File:** `web/src/components/PortfolioFactorContent.tsx:319-336`
**Priority:** P1 — 前端状态竞态
**Problem:** 诊断请求无 token guard。用户切市场/标的后，晚到的旧响应写回当前面板，显示"看起来是当前输入"的旧结果。项目其他 async handler 都有 token 保护，唯独此处遗漏。
**Fix:** 添加 `diagTokenRef = useRef(0)`，请求前 `++diagTokenRef.current`，响应后比对 token 一致才 setState。
**Test:** 手动验证 / pattern 一致性审查。

### BUG-07: `fullWeights` 旧持仓残留
**File:** `web/src/components/PortfolioRunContent.tsx:130-135, 178`
**Priority:** P1 — 前端状态残留
**Problem:** `fullWeights` 仅在 `result?.run_id` 变化时清空。用户切换 symbols/dates/strategy 但尚未新跑时，`weightsToShow = fullWeights || result?.weights_history` 显示上一轮 run 的持仓。不是每次复现，但一旦复现会误导用户。
**Fix:** 在适当的 `useEffect` 中监听输入参数变化时也清空 `fullWeights`。
**Test:** 手动验证切换输入后持仓表清空。

### BUG-08: Benchmark 不按 market 过滤
**File:** `web/src/components/PortfolioRunContent.tsx:296-302`
**Priority:** P1 — 结果口径错位
**Problem:** benchmark 下拉硬编码 A 股指数（CSI300/500/1000），市场切换后不变。美股/港股回测绑 A 股 benchmark，tracking error 约束和 alpha/beta 语义全部错位。
**Fix:** 根据 `market` prop 过滤 benchmark options。非 `cn_stock` 时清空选择或显示提示。
**Test:** 手动验证切换 market 后列表变化。

### BUG-10: DataValidator 负价格未校验
**File:** `ez/data/validator.py:43-53`
**Priority:** P1 — 数据入口防御
**Problem:** `_check_bar()` 检查 OHLC 相对关系和 volume，但不拦截负价格。负价一旦进入，指标、收益、成交规则全部被污染。
**Fix:** `_check_bar()` 开头加 `if any(v < 0 for v in [o, h, l, c]): return False`。
**Test:** 构造负价格 bar → 验证被 reject + 不入库。

---

## Low-Priority Polish (不阻塞发布，顺手可修)

| Item | File | Description |
|------|------|-------------|
| 注释 mismatch | `ez/data/fundamental.py:388` | range(6) vs "5 days" 注释不一致 |
| localStorage trim | `web/src/components/ChatPanel.tsx:50-65` | quota 裁剪无用户提示 |
| 默认日期过期 | `web/src/components/ExperimentPanel.tsx:25-26` | 硬编码 2024-12-31 |
| SSE parse 吞错误 | `web/src/components/ChatPanel.tsx:371` | `catch {}` 无日志 |
| Navbar cleanup | `web/src/components/Navbar.tsx:22-29` | fetch 无 AbortController |

---

## Removed from Spec (审查剔除)

| Original ID | Reason |
|-------------|--------|
| BUG-03 | 当前代码已按 retrain 版本过滤 OOS 预测，不再成立 |
| BUG-09 | 描述基于旧代码路径，当前解析逻辑已不同 |
| BUG-16 | Sortino ddof 是公式选择，不是实现错误 |
| BUG-17 | Ensemble correlation 是明确的近似设计 |

---

## Implementation Plan

### Phase 1: Backend P0 (4 fixes + 4 regression tests)
- BUG-01: sparse cache TTL
- BUG-02: AKShare raw fallback guard
- BUG-04: assert → explicit check
- BUG-05: lifespan try/finally

### Phase 2: Backend P1 (1 fix + 1 test)
- BUG-10: negative price validation

### Phase 3: Frontend (3 fixes)
- BUG-06: ML diagnostics race token
- BUG-07: fullWeights invalidation
- BUG-08: benchmark market filter

### Phase 4: Polish (optional, 顺手修)
- 注释 mismatch、默认日期、SSE logging、Navbar cleanup、localStorage 提示

### Verification
每个 Phase 完成后运行 `pytest tests/` 确保无回归。前端改动手动验证。
