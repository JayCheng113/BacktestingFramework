# OpenTrading 工程治理规则

## 1. 薄核心规则

稳定能力放核心，新功能默认放边缘模块。

| 类别 | 包路径 | 修改约束 |
|------|--------|---------|
| **核心** | ez/core/, ez/backtest/, ez/data/, ez/factor/base.py, ez/strategy/base.py, ez/types.py, ez/errors.py, ez/config.py | 需要 core-change 提案 |
| **扩展** | ez/factor/builtin/, ez/strategy/builtin/, ez/data/providers/ | 自由添加，contract test 自动验证 |
| **Agent** | ez/agent/ (V2.4+) | 新模块，不依赖核心的内部实现 |
| **Live** | ez/live/ (V2.6+) | 新模块，消费核心接口但不修改 |
| **Ops** | ez/ops/ (V3.2+) | 新模块，运维/调度/监控 |
| **API/Web** | ez/api/, web/ | 展示层，依赖上游 |

**规则**：ez/agent/, ez/live/, ez/ops/ 可以 import ez/core, ez/backtest, ez/data 的公开接口。
反方向 import 禁止。跨层 import 新增必须为 0。

---

## 2. 功能生命周期

每个新模块/API 必须标注状态：

| 状态 | 含义 | 行动 |
|------|------|------|
| **experimental** | 接口可能变，不保证向后兼容 | 文档标注，不进核心 |
| **beta** | 接口基本稳定，需更多验证 | 可进入 CLAUDE.md |
| **stable** | 接口冻结，向后兼容保证 | 列入 Core Files |
| **deprecated** | 计划移除，给出替代方案 | 标注 sunset_version |

**规则**：两个大版本未从 experimental 晋升 beta 的功能，自动 deprecated。

---

## 3. 版本纪律

### 每版必须包含
- **Entry Checklist**: 前一版 exit gate 通过；无遗留 Critical/Important
- **清理清单**: 至少 1 项（删旧代码/合并重复/统一接口）
- **Exit Gate**: 明确的完成标准（见各版本定义）

### 发版规则
- Exit gate 全部通过 → 正式版 (v0.X.0)
- Exit gate 部分未通过 → 只发 -rc (v0.X.0-rc1)，不打正式 tag

### 稳定性版本
- 每两个功能版本后，做一个稳定性版本（只修 bug / 重构 / 补测试，不加大功能）
- V2.3 (功能) → V2.4 (功能) → V2.4.1 (稳定性) → V2.5 (功能) → V2.6 (功能) → V2.6.1 (稳定性)

---

## 4. 架构门禁测试

`tests/test_architecture/` — 自动检查架构约束，CI 必须通过。

| 测试 | 检查内容 |
|------|---------|
| test_layer_dependencies | ez/agent 不被 ez/core import；依赖方向正确 |
| test_no_circular_imports | 无循环依赖 |
| test_core_stability | Core files 列表与实际一致 |
| test_extension_contract | 所有 Factor/Strategy/DataProvider 子类通过 contract test |

---

## 5. 市场优先级

| 市场 | 优先级 | 理由 |
|------|--------|------|
| A 股 (cn_stock) | **P0** | 团队主要研究市场，Tushare 数据已接入 |
| 港股 (hk_stock) | P2 | 需求较低，暂缓 |
| 美股 (us_stock) | P2 | FMP 已接入但非重点 |
| 加密货币 | P3 | 未来可能 |
