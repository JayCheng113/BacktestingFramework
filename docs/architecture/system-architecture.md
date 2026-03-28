# ez-trading 系统架构设计

## 定位

ez-trading 是 **Agent-Native 量化交易系统**。

"Agent-Native" 不是"只给 Agent 用"，而是：
- **人类和 Agent 都是一等公民研究者**：人类写策略/因子 + Agent 自主探索，殊途同归
- 人类通过 Web/CLI 创建因子、编写策略、提交实验、查看结果
- Agent 通过 API/Tool Calling 做同样的事，但可以 24 小时不间断批量迭代
- 两者提交的实验走同一条管线（候选 → Research Gate → Deploy Gate）
- 所有操作都有审计日志，Agent 和人类的操作同等可追溯
- 系统自动验证和反馈（Gate 机制），不管是谁提交的都自动检验

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

## 策略生命周期：双状态机

策略有两个维度的状态，解耦研究和部署：

### StrategyVersionState（研究侧，按策略版本）

一个策略版本从诞生到验证通过的生命周期。与账户无关。

```
CANDIDATE → BACKTESTED → RESEARCH_PASSED
     ↓           ↓
  REJECTED   REJECTED
```

| 状态 | 含义 | 谁触发 |
|------|------|--------|
| CANDIDATE | 候选池，等待回测 | Agent / 参数搜索 |
| BACKTESTED | 回测完成，等待 Gate | Runner |
| RESEARCH_PASSED | 通过全部研究检验 | Gate 自动 |
| REJECTED | 未通过 | Gate 自动 |

### DeploymentState（部署侧，按 account × market 实例化）

一个 RESEARCH_PASSED 的策略版本可以在多个账户/市场独立部署，各自有自己的状态。

```
PENDING_DEPLOY → PAPER_LIVE → LIMITED → ACTIVE
      ↓              ↓           ↓         ↓
  NOT_DEPLOYABLE  SUSPENDED  SUSPENDED  RETIRED
```

| 状态 | 含义 | 谁触发 | 需人工 |
|------|------|--------|--------|
| PENDING_DEPLOY | 等待 Deploy Gate | 研究通过后自动 | — |
| NOT_DEPLOYABLE | 不适合此账户 | Deploy Gate | — |
| PAPER_LIVE | 仿真运行 | 人工审批 | **是** |
| LIMITED | 小规模资金 | 人工审批 | **是** |
| ACTIVE | 全量运行 | 人工审批 | **是** |
| SUSPENDED | 风控暂停 | 自动或手动 | 恢复需审批 |
| RETIRED | 永久下线 | 人工 | **是** |

**多账户示例**：策略 MACross-v3 通过 Research → 在 Account-A(A股) 部署为 PAPER_LIVE，在 Account-B(美股) 仍为 NOT_DEPLOYABLE（碎股不支持）。

### 禁止转移

- CANDIDATE 不可直接到 PENDING_DEPLOY（必须经 Research Gate）
- PENDING_DEPLOY 不可直接到 ACTIVE（必须经 Deploy Gate + 人工审批）
- Agent **没有** Deploy/Live 操作权限（见下方权限模型）

### 回滚

- ACTIVE/LIMITED 触发风控 → 自动 SUSPENDED + 通知
- SUSPENDED 恢复需人工审批 + 重新 Runtime Gate

---

## 不可变发布工件 (Release Artifact)

**策略从 Research 到 Live 的唯一通道是发布工件，Live 面不执行工作区代码。**

```
Research 通过 → 打包发布工件 → Deploy Gate 验证工件 → Live 执行工件
```

发布工件包含（全部不可变，append-only）：

| 字段 | 说明 |
|------|------|
| artifact_id | 唯一标识 (UUID) |
| strategy_version | 策略版本号 |
| code_commit | git SHA（代码精确版本） |
| dependency_lock | requirements.txt / pyproject.toml 的 hash |
| config_hash | 策略参数 + 交易成本配置的 hash |
| data_snapshot_id | 训练数据的 hash（可回放） |
| gate_results | Research Gate 每层检验结果 |
| created_at | 打包时间 |

**数据快照物理落地**：
- 近期（90天）：Parquet 文件保留完整数据
- 远期：只保留 hash + 元数据；需要重放时从数据源重新拉取并验证 hash 一致

---

## 权限模型 (RBAC)

Agent 默认仅有 Research 权限。Deploy/Live 操作必须人类签名。

| 角色 | Research 面 | Deploy/Live 面 | 审批 |
|------|------------|---------------|------|
| **Agent** | 提交策略、运行回测、查询实验 | **只读**（查看状态） | 无 |
| **研究员** | 创建因子/策略 + 运行回测 + 提交实验 + 查询历史 | 只读 | 无 |
| **PM** | 只读 | 查看 + 审批部署 + 调整仓位 | **有** |
| **风控** | 只读 | 查看 + kill-switch + 强制平仓 | **有** |
| **管理员** | 全部 | 全部 | **有** |

**审计**：所有 Deploy/Live 操作记录操作人(或 Agent ID)、时间、审批链。

---

## 交易制度约束 (MarketRules)

**Backtest / Runtime / Execution 共用同一套 MarketRules，保证回测和实盘行为一致。**

| 规则 | A 股 (cn_stock) | 美股 (us_stock) | 说明 |
|------|----------------|----------------|------|
| T+N | T+1 | T+0 | A 股今日买入不可今日卖出 |
| 涨跌停 | ±10%（ST ±5%，科创/创业 ±20%） | 无 | 涨停价不可买入，跌停价不可卖出 |
| 最小交易单位 | 100 股（1 手） | 1 股（支持碎股看券商） | 下单必须是整手 |
| 停牌 | 有 | 有 | 停牌期间不可交易 |
| 集合竞价 | 9:15-9:25 | — | 日线回测用开盘价已隐含 |
| 印花税 | 卖出 0.05% | — | A 股特有卖出税 |
| 交易时间 | 9:30-11:30, 13:00-15:00 | 9:30-16:00 ET | — |

**当前实现**：❌ 未实现。引擎假设可随时买卖任意数量。V2.5 实现 MarketRules 模块。

**依赖关系**：OMS (V3.0) 消费 MarketRules (V2.5) 做订单校验。MarketRules 必须在 OMS 之前完成。

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
│  策略/因子来源（管线对提交者无感）                    │
│  ├── 人类研究员: Web/CLI 创建因子、编写策略           │
│  ├── Agent: API/Tool Calling 自主生成                 │
│  ├── 内置: MACross/Momentum/BollReversion (已有)      │
│  └── 所有来源走同一条 contract test → Gate 管线       │
│                                                      │
│  策略注册表                                           │
│  ├── 人类手写 + Agent 生成 + 内置，统一注册           │
│  └── [未来] 参数网格搜索 + 批量预筛                  │
│                                                      │
│  因子自定义                                           │
│  ├── 人类: 继承 Factor ABC, 实现 compute()            │
│  ├── Agent: 生成因子代码 + contract test 验证         │
│  └── [未来] Web 可视化因子组合器                      │
│                                                      │
│  Pre-filter (快速淘汰)                                │
│  └── Sharpe/MaxDD/trades 门槛 [未来, V2.4 Gate]      │
│                                                      │
│  当前实现: 3 策略 + 6 因子 + 自动注册 + contract test │
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

**实验流程（V2.4，连接 Layer 2 和 Layer 3）**：
Agent/研究员提交 RunSpec → Runner 调用 Layer 2 候选生成 + Layer 3 各层检验 →
结果持久化到实验台账 (DuckDB runs 表) → Gate 自动判定 PASS/FAIL →
人类在 Web "实验管理"页面查看/比较/审批。
API: `POST /experiments/submit`, `GET /experiments/{id}`, `GET /experiments`。
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
│  ├── 事件日志: append-only（不可删改）                │
│  ├── 幂等键: client_order_id（防重复下单）            │
│  ├── 重启恢复: 重放事件日志重建状态                   │
│  │   - NEW/SUBMITTED → 查询券商确认状态               │
│  │   - PARTIAL → 查询已成交量，继续等待或撤单         │
│  │   - FILLED/CANCELED → 已终态，无需操作             │
│  └── 超时撤单（可配置超时阈值）                       │
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
│  ┌────────────────────────────────────────────────┐   │
│  │ 级别 │ 触发条件               │ 自动动作       │   │
│  │──────│────────────────────────│────────────────│   │
│  │ L1   │ 全部正常               │ 无             │   │
│  │ L2   │ 数据延迟>30min(5min窗) │ 通知           │   │
│  │      │ 或 订单失败率>5%(20单) │ 不影响交易     │   │
│  │ L3   │ 数据缺失>1交易日       │ 暂停新开仓     │   │
│  │      │ 或 订单失败率>20%(20单)│ 降杠杆50%      │   │
│  │ L4   │ 风控触发 / 系统崩溃    │ kill-switch    │   │
│  │      │ 或 持仓对账drift>5%   │ 仅允许平仓     │   │
│  │──────│────────────────────────│────────────────│   │
│  │ 恢复 │ L4→L3: 人工确认+对账通过                │   │
│  │      │ L3→L2: 数据恢复+失败率下降              │   │
│  │      │ L2→L1: 自动（指标回正常）               │   │
│  └────────────────────────────────────────────────┘   │
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
| 回测面板 | 研究员 | 策略选择 + 参数调整 + 交易成本 + 运行回测（已有） |
| 因子评估 | 研究员 | IC 分析 + 衰减曲线 + 因子选择（已有） |
| **因子/策略创建** | 研究员 | 上传自定义因子/策略代码 + 自动 contract test（V2.5） |
| **实验管理** | 研究员/PM | 人工和Agent提交的实验统一查看 + 对比 + Gate结果（V2.4） |
| **策略审批** | PM | Deploy Gate 审批 + 生命周期操作（V2.6） |
| **实盘监控** | PM/风控 | 持仓/订单/PnL + 风控状态（V3+） |
| **系统健康** | 运维 | 数据新鲜度/降级状态/告警（V3+） |

### 看板能力演进

当前看板仅支持日线K线和单次回测。目标对标专业量化看板：

| 能力 | 当前 | 目标版本 |
|------|------|---------|
| K 线周期 | 仅日线 | V2.5: daily/weekly/monthly 可选 |
| K 线指标 | MA5/10/20/60 + BOLL | V2.5: 更多可选指标 (VWAP, OBV, RSI叠加) |
| 多标的对比 | 无 | V2.5: 多股同屏对比 |
| 因子/策略创建 | 无（需手写代码） | V2.5: Web 上传 + 自动验证 |
| 实验管理 | 无 | V2.4: 列表+对比+Gate结果 |
| 策略生命周期 | 无 | V2.6: 状态机可视化+审批 |
| 实盘持仓/PnL | 无 | V3+: 实时更新 |

---

## Agent 能力设计（High-Level）

> **注：此处水很深。以下为架构级设计，具体实现需逐步探索。**

### 核心理念

Agent 是研究方式之一（另一种是人类研究员手动操作）。
Agent 的优势在于可以 24 小时不间断批量迭代，而人类的优势在于直觉、创意和领域洞察。
两者互补，走同一条管线。

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
3. V2.5: 参数搜索 + 批量预筛 + 更多因子 + 多重检验控制(FDR) + MarketRules
4. V2.6: Deploy Gate + 不可变发布工件 + 策略审批页面

### 远期: Live 面（V3+）
5. V3.0: PaperBroker + OMS(事件日志+幂等键+恢复) + 日线级事件总线
6. V3.1: PreTradeRiskEngine + Runtime Gate
7. V3.2: 调度层 + 监控(SLO/SLA) + 双状态机实现
8. V3.3: 中国券商 API 适配（通达信/恒生/CTP）

---

## 语言分工与演进策略

### 当前分工

| 层 | 语言 | 理由 |
|----|------|------|
| Research / Gate / API / 编排 | Python | Agent 友好，开发效率高 |
| 数值热路径 (ts_ops) | C++ (nanobind) | 已证实 4-8x 加速 |
| Live 核心 (OMS/风控/执行) | **Python（V3.0 起步）** | 先跑通逻辑，验证正确性 |

### Rust 引入触发条件（硬门槛，未达到前不引入）

**只有同时满足以下条件时才考虑将 Live 组件迁移到 Rust：**

| 维度 | 触发阈值 | 测量方式 |
|------|---------|---------|
| 性能 | 某模块长期占总 CPU > 30%，Python 优化后仍不达标 | profiling |
| 稳定性 | Python Paper OMS 出现无法通过代码修复的崩溃/并发问题 | 事故日志 |
| 延迟 | p99 延迟超过交易时间窗口要求（日线: 数秒；分钟线: 数百毫秒） | 监控 |
| 恢复 | 重启恢复一致性无法在 Python 中保证 | 回放测试 |

**当前判断：日线级别交易，Python OMS 完全足够。触发条件预计在引入分钟线/高频时才会达到。**

### 如果引入 Rust 的路径

```
V3.0: Python Paper OMS 跑通 → 验证逻辑正确性
V3.x: 评估是否触发门槛
  - 未触发 → 继续 Python，优化热点
  - 触发 → Rust 重写 OMS + Risk + Gateway
         → 进程隔离（gRPC/HTTP），不是 FFI
         → Python 回测 vs Rust 实盘回放一致性测试
```

---

## 跨语言契约（不论未来用什么语言都需要）

### 统一事件模型

```python
# 所有模块共用这些事件定义（Python dataclass / Rust struct / C++ struct）
@dataclass
class OrderEvent:
    client_order_id: str    # 幂等键
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: float | None
    status: Literal["NEW", "SUBMITTED", "PARTIAL", "FILLED", "CANCELED", "REJECTED"]
    timestamp: datetime

# 注意：FillEvent (Live 面) 与 FillResult (回测面, ez/core/matcher.py) 是
# 不同抽象层级的类型。FillResult 用于回测撮合模拟（无 order_id/timestamp），
# FillEvent 用于实盘订单回报。两者不需要合并。
@dataclass
class FillEvent:
    client_order_id: str
    fill_price: float
    fill_quantity: float
    commission: float
    timestamp: datetime

@dataclass
class RiskDecision:
    order: OrderEvent
    approved: bool
    reject_reason: str | None    # NOTIONAL_LIMIT, POSITION_LIMIT, LEVERAGE, KILL_SWITCH
```

### 错误码（固定，跨语言通用）

| 码 | 含义 | 重试 |
|----|------|------|
| OK | 成功 | — |
| REJECTED_RISK | 风控拒绝 | 否 |
| REJECTED_CAPACITY | 资金/仓位不足 | 否 |
| BROKER_TIMEOUT | 券商超时 | 是（指数退避） |
| BROKER_ERROR | 券商报错 | 视错误码 |
| DUPLICATE_ORDER | 重复下单（幂等拦截） | 否 |

### 工程化成本预估

| 项目 | Python-only | + Rust (未来) |
|------|------------|--------------|
| CI/CD | 单语言构建，简单 | 多语言构建 + 交叉测试 |
| 发布 | pip install | pip + cargo/binary |
| 监控 | Python metrics | 统一 metrics 格式 |
| Oncall | Python 调试 | 需要 Rust 调试能力 |

---

### 优先级较低（不是不做，时机未到）
- 高频事件引擎（当前无 tick 数据，日线级事件总线优先，高频后续可扩展）
- Rust Live 核心（Python Paper OMS 先跑通，有瓶颈再迁移）
- 黎曼流形几何特征 (SPD)
- CRTP/Eigen/SIMD 显式优化
- Kyle λ / Glosten-Milgrom 微观结构
