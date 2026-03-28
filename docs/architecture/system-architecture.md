# ez-trading 系统架构设计

## 定位

ez-trading 是 **Agent-Native 量化交易系统**。

"Agent-Native" 不是"只给 Agent 用"，而是：
- Agent 和人类操作员共用同一个系统
- Agent 通过 API 提交实验/策略，人类通过 Web 看板监督和审批
- 所有操作都有审计日志，Agent 和人类的操作同等可追溯
- 系统自动验证和反馈（Gate 机制），减少人工逐步操作

当前状态：**研究/回测阶段**（Layer 0-3 部分完成）。

---

## 两个平面（控制面 / 交易面隔离）

```
┌─────────────────────────────────────────────────────────────────┐
│           Research Control Plane（研究控制面）                    │
│           异步 · 可重试 · 允许耗时 · 无资金风险                    │
│                                                                  │
│  数据平台 → 特征工程 → 候选生成 →[Research Gate]→ 策略注册表      │
│                                                                  │
│  特点：                                                          │
│  - 回测/WFO/显著性可以跑几分钟                                   │
│  - 失败可重试，不影响任何实盘                                     │
│  - Agent 主要在这个面工作                                         │
│  - 人类在 Web 看板审查结果                                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│           Live Data Plane（交易数据面）                           │
│           低延迟 · 强约束 · 不可阻塞 · 资金在线                    │
│                                                                  │
│  策略注册表 →[Deploy Gate]→[Runtime Gate]→ 执行 → 监控            │
│                                                                  │
│  特点：                                                          │
│  - 不依赖 Research 面（研究挂了不影响交易）                       │
│  - 严格风控：PreTradeRiskEngine 是执行前最后闸门                 │
│  - 最小事件总线（bar/order/fill），非高频事件引擎                 │
│  - 人类可通过 Web 手动干预（暂停/撤单/平仓）                     │
└─────────────────────────────────────────────────────────────────┘
```

**隔离原则**：Research 面的任何故障（回测崩溃、数据刷新超时、Agent 提交异常请求）
不会影响 Live 面已在执行的策略。两个面共享数据但独立运行。

---

## 统一策略生命周期状态机

**唯一真相源**。策略在任何模块、任何时刻只有一个状态。

```
CANDIDATE → BACKTESTED → RESEARCH_PASSED → DEPLOYABLE → PAPER_LIVE → LIMITED → ACTIVE
     ↓           ↓              ↓              ↓            ↓          ↓         ↓
  REJECTED   REJECTED      NOT_DEPLOYABLE   SUSPENDED    SUSPENDED  SUSPENDED  RETIRED
```

| 状态 | 含义 | 谁触发转移 | 在哪个面 |
|------|------|-----------|---------|
| CANDIDATE | 候选池，等待回测 | Agent 提交 / 参数搜索生成 | Research |
| BACKTESTED | 回测完成，等待 Research Gate | Runner 自动 | Research |
| RESEARCH_PASSED | 通过 Research Gate 全部检验 | Gate 自动判定 | Research |
| NOT_DEPLOYABLE | 研究通过但不适合当前账户 | Deploy Gate 自动 | Research |
| DEPLOYABLE | 通过 Deploy Gate，**需人工审批** | 人类在 Web 确认 | 跨面 |
| PAPER_LIVE | 仿真实盘运行中 | 人工启动 | Live |
| LIMITED | 小规模真实资金 | 人工审批 + 系统检查 | Live |
| ACTIVE | 全量运行 | 人工审批 | Live |
| SUSPENDED | 暂停（风控触发 / 人工干预） | 自动或手动 | Live |
| RETIRED | 永久下线 | 人工决定 | 任意 |
| REJECTED | 未通过验证 | Gate 自动 | Research |

**禁止转移**：
- CANDIDATE 不可直接到 DEPLOYABLE（必须经过 Research Gate）
- RESEARCH_PASSED 不可直接到 ACTIVE（必须经过 Deploy Gate + 人工审批）
- SUSPENDED 可回到 PAPER_LIVE/LIMITED/ACTIVE（人工审批），但不可跳级

**回滚动作**：
- 任何 ACTIVE/LIMITED 策略触发风控 → 自动 SUSPENDED + 通知
- SUSPENDED 恢复需人工审批 + 重新通过 Runtime Gate

---

## 系统分层架构

### Layer 0: 数据平台

```
┌─────────────────────────────────────────────────────┐
│  数据源（config 驱动 failover）                       │
│                                                      │
│  A 股: Tushare (primary, 2000分) + Tencent (backup)   │
│  美股: FMP (primary)                                  │
│  [未来: 中国券商实时行情, 东方财富, 同花顺, 万得]     │
│  [未来: 新闻/舆情 (中文NLP, 新浪/东方财富), 宏观]     │
│                                                      │
│  存储（逻辑分库）                                     │
│  ├── research.db: K线/因子/回测结果（Research 面用）   │
│  ├── trading.db: 订单/成交/持仓（Live 面用）[未来]    │
│  └── Parquet cache: 高频读取场景 [未来]               │
│                                                      │
│  当前实现: DuckDB 单库, 3 数据源, DataValidator       │
└─────────────────────────────────────────────────────┘
```

### Layer 1: 特征工程

```
┌─────────────────────────────────────────────────────┐
│  因子计算 (ts_ops, C++ 加速)                          │
│  ├── 技术: MA/EMA/RSI/MACD/BOLL/Momentum (已有)      │
│  ├── [未来] 量价: VWAP, OBV, 资金流                   │
│  ├── [未来] 基本面: PE/PB/ROE (Tushare daily_basic)   │
│  ├── [未来] 舆情: 中文新闻情绪 (A股特色)              │
│  └── [未来] 公式化 alpha 批量生成                     │
│                                                      │
│  因子评估 (FactorEvaluator)                           │
│  └── IC/ICIR/IC Decay/Turnover                        │
│                                                      │
│  当前实现: 6 因子 + IC 评估 + C++ 加速                │
└─────────────────────────────────────────────────────┘
```

### Layer 2: 候选生成

```
┌─────────────────────────────────────────────────────┐
│  策略注册表                                           │
│  ├── 内置: MACross/Momentum/BollReversion (已有)      │
│  ├── Agent 生成: contract test 自动验证 (已有)        │
│  └── [未来] 外部导入 + 参数网格搜索 + 批量预筛       │
│                                                      │
│  Pre-filter (快速淘汰)                                │
│  └── Sharpe/MaxDD/trades 门槛 [未来, V2.4 Gate]      │
│                                                      │
│  当前实现: 3 策略 + 自动注册 + contract test          │
└─────────────────────────────────────────────────────┘
```

### Layer 3: Research Gate

```
┌─────────────────────────────────────────────────────┐
│  多层检验（CANDIDATE → BACKTESTED → RESEARCH_PASSED）│
│                                                      │
│  第1层: 回测 (VectorizedBacktestEngine)        [已有] │
│  第2层: Walk-Forward 验证 (防过拟合)           [已有] │
│  第3层: 统计显著性 (Bootstrap CI + Monte Carlo) [已有] │
│  第4层: 成本压力 (2x 交易成本仍盈利)          [未来] │
│  第5层: 鲁棒性 (不同市场状态下的表现)         [未来] │
│                                                      │
│  多重检验控制 [未来]                                  │
│  └── 当候选数 > 阈值时，应用 FDR 或 Bonferroni 校正  │
│      或采用"外层留出集 + 内层调参"两层验证            │
│      防止参数搜索的数据挖掘偏差                       │
│                                                      │
│  Gate 判定: 全部通过 → RESEARCH_PASSED                │
│            任一失败 → REJECTED + reject_reason_codes  │
│                                                      │
│  当前实现: 前3层引擎已有, Gate 框架未实现 (V2.4)     │
└─────────────────────────────────────────────────────┘
```

### Layer 4: Deploy Gate

```
┌─────────────────────────────────────────────────────┐
│  RESEARCH_PASSED → DEPLOYABLE（需人工确认）           │
│                                                      │
│  自动检查                                             │
│  ├── 账户适配: 资金/碎股/最小下单额/交易频率          │
│  ├── 流动性: 策略所需 vs 标的实际                     │
│  └── 容量: 最大持仓数 vs 资金约束                     │
│                                                      │
│  人工审批节点                                         │
│  ├── Web 看板展示 Gate 结果 + 策略详情                │
│  └── 人类点击"批准部署" → DEPLOYABLE                 │
│                                                      │
│  当前实现: ❌ 未实现                                  │
└─────────────────────────────────────────────────────┘
```

### Layer 5: Runtime Gate + 仓位分配

```
┌─────────────────────────────────────────────────────┐
│  每日/每周期决策：今天给不给仓位？                     │
│                                                      │
│  信号生成: 策略产出目标权重                            │
│  运行时检查:                                          │
│  ├── 策略健康度 (近期 Sharpe, 回撤状态)               │
│  ├── 市场状态 (波动率, 流动性)                        │
│  ├── 仓位限制 (单票/总仓/杠杆)                       │
│  └── 成本比率 (预期收益 vs 执行成本)                  │
│                                                      │
│  仓位分配: 多策略资本分配 (等权/风险预算/Kelly)       │
│                                                      │
│  输出:                                                │
│  ├── selected → AllocationResult (权重, 金额)         │
│  └── rejected → 原因码 (TAIL_RISK, COST_RATIO, etc.) │
│                                                      │
│  当前实现: ❌ 未实现                                  │
└─────────────────────────────────────────────────────┘
```

### Layer 6: 执行层

```
┌─────────────────────────────────────────────────────┐
│  AllocationResult → 实际订单                          │
│                                                      │
│  ┌──────────────────────────────────────┐             │
│  │ PreTradeRiskEngine（硬风控，最后闸门）│             │
│  │  ├── notional 限额（单笔/日累计）    │             │
│  │  ├── position 限额（单票/总仓）      │             │
│  │  ├── leverage 上限                   │             │
│  │  ├── kill-switch（紧急全面停单）      │             │
│  │  └── 拒单 → 回写订单流 + 通知        │             │
│  └──────────────────────────────────────┘             │
│                                                      │
│  OMS (订单管理)                                       │
│  ├── 状态机: NEW→SUBMITTED→PARTIAL→FILLED/CANCELED    │
│  ├── 持久化（重启恢复）                               │
│  └── 超时撤单                                         │
│                                                      │
│  最小事件总线（非高频事件引擎）                       │
│  ├── bar_received → 策略计算                          │
│  ├── order_submitted → 等待回报                       │
│  ├── fill_received → 更新持仓/资金                    │
│  └── 日线级别足够，不做 tick 事件引擎                 │
│                                                      │
│  Broker 适配                                          │
│  ├── PaperBroker（仿真，优先实现）                    │
│  ├── [未来] 中国券商 API（通达信/恒生/CTP 期货）      │
│  └── 幂等下单, 重试, 撤单一致性                      │
│                                                      │
│  持仓对账: 本地 tracker vs 券商实际                    │
│                                                      │
│  当前实现: ❌ 未实现                                  │
│  (Matcher 是回测撮合模拟，不是 OMS)                   │
└─────────────────────────────────────────────────────┘
```

### Layer 7: 调度 + 监控

```
┌─────────────────────────────────────────────────────┐
│  调度 (end_of_day_scheduler)                          │
│  ├── daily: 数据更新 → 信号 → 执行                   │
│  ├── weekly: 持续回测 → 健康检查                      │
│  └── monthly: 自动研究循环 → 新候选                   │
│                                                      │
│  监控 (4级降级)                                       │
│  ┌──────────────────────────────────────────┐         │
│  │ L1 正常:  全功能运行                      │         │
│  │ L2 警告:  数据延迟>30min / 失败率>5%      │         │
│  │          → 通知, 不影响交易                │         │
│  │ L3 降级:  数据缺失>1日 / 失败率>20%       │         │
│  │          → 自动降杠杆, 暂停新开仓         │         │
│  │ L4 紧急:  风控触发 / 系统异常              │         │
│  │          → kill-switch 停单, 仅允许平仓    │         │
│  │          → 立即通知人类                    │         │
│  └──────────────────────────────────────────┘         │
│                                                      │
│  通知: 微信/钉钉/Discord（中国团队优先微信）          │
│  审计: 每笔决策日志, 每次 run 可复现                  │
│                                                      │
│  当前实现: ❌ 未实现                                  │
└─────────────────────────────────────────────────────┘
```

---

## 可复现性定义

每次 Research Run 必须记录：

| 字段 | 含义 |
|------|------|
| run_id | 唯一标识 |
| code_commit | git SHA，确保代码可回溯 |
| data_snapshot_hash | 输入数据的 hash，检测数据变更 |
| strategy_id + params | 策略标识和参数 |
| fee_model | 手续费率/最低手续费/滑点率 |
| calendar + timezone | 交易日历和时区 |
| random_seed | 所有随机源的种子 |
| result_metrics | 输出指标 |
| gate_verdict | PASS/FAIL + 原因 |

**重放验证**：同一 RunSpec 重跑，结果差异超过阈值即告警。

---

## 在线学习约束

**原则**：实盘中，Fill 反馈**不直接修改决策参数**。

```
Fill 回报 → 更新统计缓冲区（候选参数/特征统计/滑点估计）
                    ↓
          受控发布窗口（如每周日收盘后）
                    ↓
          人工/自动审批 → 更新决策参数
                    ↓
          审计日志记录参数变更
```

**为什么**：
- 直接更新 → 参数漂移 → 难以审计和回放
- 受控发布 → 可比较"更新前 vs 更新后"的影响
- 全量重训练 → 走 Research 循环，不在 Live 面做

---

## 部署模型：个人 + 团队双模式

```
┌─────────────────────────────────────────────────────────────┐
│  个人模式（研究员本地）                                       │
│  python -m ez 或 ./scripts/start.sh                          │
│  ├── 本地 DuckDB，本地前端                                   │
│  ├── 个人回测/因子研究/策略开发                               │
│  ├── Agent 在本地跑 Research 循环                            │
│  └── 成果（策略+实验结果）推送到团队服务器                   │
└──────────────────────┬──────────────────────────────────────┘
                       ↓ 推送策略/实验结果
┌──────────────────────┴──────────────────────────────────────┐
│  团队模式（中心服务器）                                       │
│  ├── 统一数据仓（全市场数据，定时更新）                      │
│  ├── 策略注册表（所有团队成员 + Agent 的策略汇总）           │
│  ├── 因子库（共享因子，避免重复计算）                        │
│  ├── 实验台账（所有 Research Run 的结果，可比较/审计）        │
│  ├── Gate 管理（Research/Deploy Gate 集中判定）              │
│  ├── 调度器（daily 数据更新/信号/执行）                      │
│  ├── Live 交易管理（OMS/风控/券商对接）                      │
│  └── 权限控制（研究员/PM/风控 不同角色）                     │
└─────────────────────────────────────────────────────────────┘

客户端:
├── Web 看板 (React, 已有基础) — 回测/监控/审批
├── [未来] CLI (ez-cli) — 研究员快速操作
├── [未来] 移动端 (微信小程序 / App) — PM 审批 + 告警推送
└── Agent (API + Tool Calling) — 自主研究循环
```

### Web 页面

| 页面 | 服务对象 | 功能 |
|------|---------|------|
| 行情看板 | 所有人 | K线 + 指标 + 买卖点标记（已有） |
| 回测面板 | 研究员 | 策略 + 参数 + 交易成本配置（已有） |
| 因子评估 | 研究员 | IC 分析 + 衰减曲线（已有） |
| **实验管理** | 研究员/PM | Agent 和人工提交的实验 + 比较（V2.4） |
| **策略审批** | PM | Deploy Gate 审批 + 生命周期操作（V2.6） |
| **实盘监控** | PM/风控 | 持仓/订单/PnL + 风控状态（V3+） |
| **系统健康** | 运维 | 数据新鲜度/降级状态/告警（V3+） |

---

## Agent 能力设计（High-Level）

> **注：此处水很深。以下为架构级设计，具体实现需逐步探索。**

### 核心理念

Agent 不是"调 API 的脚本"，而是具备以下能力的自主研究体：

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent 能力栈                               │
│                                                              │
│  Layer 4: 自主研究循环                                        │
│  ├── 目标：发现有效因子 → 构建策略 → 通过 Gate              │
│  ├── 基于上一轮 Gate 反馈调整下一轮方向                      │
│  └── 人类设定约束（市场/风格/风险偏好），Agent 自主执行      │
│                                                              │
│  Layer 3: 推理与规划                                          │
│  ├── 分析 Gate 拒绝原因 → 决定改因子还是改参数还是换策略    │
│  ├── 因子 IC 反馈 → 决定保留/丢弃/组合哪些因子              │
│  └── 多步规划：先探索 → 再精调 → 最后验证                   │
│                                                              │
│  Layer 2: 记忆与上下文                                        │
│  ├── 跨会话记忆：记住哪些因子组合试过了、效果如何            │
│  ├── 实验台账查询：从 DB 拉历史实验对比                      │
│  ├── 代码库理解：CLAUDE.md 作为 agent 对系统的理解           │
│  └── 避免重复实验（查询台账 → 已做过 → 跳过）              │
│                                                              │
│  Layer 1: 工具调用 (Tool Calling)                             │
│  ├── run_backtest(spec) → BacktestResult                     │
│  ├── evaluate_factor(factor, data) → FactorAnalysis          │
│  ├── submit_strategy(code) → contract test → 注册            │
│  ├── check_gate(strategy_id) → PASS/FAIL + reasons           │
│  ├── query_experiments(filters) → 历史实验列表               │
│  ├── generate_factor(hypothesis) → Factor 代码               │
│  └── compare_runs(run_ids) → 对比报告                        │
│                                                              │
│  Layer 0: LLM 基础                                            │
│  └── Claude API (tool_use + system prompt + context window)   │
└─────────────────────────────────────────────────────────────┘
```

### Agent 与 Research 面的交互

```
人类设定目标: "在 A 股日线上寻找月度轮动策略，Sharpe > 1，MaxDD < 20%"
                                    ↓
Agent 规划: "先评估动量/价值/波动率因子的 IC，找最强因子组合"
                                    ↓
Agent 调用 tools:
  1. evaluate_factor(Momentum(20), 沪深300成分股)  → IC=0.03, ICIR=0.8
  2. evaluate_factor(RSI(14), 同上)                → IC=0.01, ICIR=0.3
  3. Agent 推理: "Momentum IC 更高，RSI 弱，丢弃 RSI"
                                    ↓
  4. generate_factor("低波动+高动量交叉")         → 生成代码
  5. submit_strategy(代码)                         → contract test 通过
  6. run_backtest(策略, 参数网格)                  → 批量结果
  7. check_gate(最佳参数)                          → FAIL: OOS Sharpe < 0
                                    ↓
Agent 记忆: "动量+低波动在近期市场不行，可能是趋势弱化"
Agent 调整: "尝试均值回归型策略"
                                    ↓
  ... 下一轮循环 ...
                                    ↓
最终: 某策略通过 Research Gate → 人类在 Web 审批 → 部署
```

### 关键设计决策（待定，逐步探索）

| 决策点 | 选项 | 当前倾向 | 待验证 |
|--------|------|---------|--------|
| Agent 框架 | 自建 vs LangChain vs CrewAI | **自建**（设计文档已分析，LangChain 增加复杂度无对应收益） | V2.4 POC |
| LLM | Claude API vs 本地模型 | **Claude API**（Agent 需要强推理能力） | — |
| 记忆 | CLAUDE.md + DB 台账 vs 向量数据库 | **CLAUDE.md + DB**（结构化数据用 SQL 查比向量搜索更精确） | V2.4 |
| 因子代码生成 | LLM 直接写 vs 模板填充 | **LLM 写 + contract test 验证**（Agent-Native 核心差异化） | V2.5 |
| 搜索策略 | 随机搜索 vs 贝叶斯优化 vs LLM 引导 | **LLM 引导**（利用推理能力，不是盲搜） | V2.5 |

> **注**：以上每个决策点都需要 POC 验证。V2.4 先做最小 Agent 闭环（RunSpec + Runner + Gate），
> 验证 tool calling 基础能力。更深层的记忆/推理/代码生成能力逐步迭代。

---

## 当前位置总览

```
Layer 0: 数据平台          ██████████ 100%  3数据源 + DuckDB + 校验
Layer 1: 特征工程          ████░░░░░░  40%  6技术因子 + IC评估 + C++加速
Layer 2: 候选生成          ███░░░░░░░  30%  策略注册 + contract test
Layer 3: Research Gate     ██████░░░░  60%  回测+WFO+显著性, 缺Gate框架
Layer 4: Deploy Gate       ░░░░░░░░░░   0%
Layer 5: Runtime Gate      ░░░░░░░░░░   0%
Layer 6: 执行层            ░░░░░░░░░░   0%
Layer 7: 调度+监控         ░░░░░░░░░░   0%

Research 面: ████████░░ ~50% (数据→特征→回测链路可用)
Live 面:     ░░░░░░░░░░  0%  (无 OMS/Broker/风控)
```

---

## 实施路线

### 近期: 把 Research 面做完（V2.3-V2.4）
1. V2.3: 正确性封顶（对账不变量 + C++/Python 对拍）
2. V2.4: Agent 闭环（RunSpec + Runner + Gate + Report + 实验持久化）

### 中期: 候选生成 + Deploy Gate（V2.5-V2.6）
3. V2.5: 参数搜索 + 批量预筛 + 更多因子
4. V2.6: Deploy Gate + 策略审批 Web 页面

### 远期: Live 面（V3+）
5. V3.0: PaperBroker + 基础 OMS + 最小事件总线
6. V3.1: PreTradeRiskEngine + Runtime Gate
7. V3.2: 调度层 + 监控 + 生命周期状态机
8. V3.3: 中国券商 API 适配（通达信/恒生/CTP）

### 不做
- 黎曼流形几何特征 (SPD)
- 高频事件引擎 (无 tick 数据)
- CRTP/Eigen/SIMD 显式优化
- Kyle λ / Glosten-Milgrom 微观结构
