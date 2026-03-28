# ez-trading 系统架构设计

## 定位

ez-trading 的目标不是"一个回测框架"，而是**一个完整的 Agent-Native 量化交易系统**。

当前状态：研究/回测阶段。本文档定义整个系统的最终形态，明确我们已完成的部分、
正在做的部分、和未来的部分。

---

## 高层链路

```
数据平台 → 特征工程 → 候选生成 → Research Gate → Deploy Gate → Runtime Gate → 执行 → 监控
    ↑                                                                              |
    └──────────────────────── 调度层（循环驱动所有层）────────────────────────────────┘
```

### 关键约束（不可违背的设计原则）

| 约束 | 含义 | 为什么 |
|------|------|--------|
| **候选 → Research 不产生 live 指令** | 策略在候选池里只做回测评估，绝不可能触发真实下单 | 防止未验证策略造成资金损失 |
| **Research 通过 ≠ 直接部署** | 通过 Research Gate 只意味着"研究合格"，部署需要独立的 Deploy Gate 和人工审批 | 研究环境和生产环境不同（账户限制、资金规模、流动性） |
| **可部署 ≠ 今天给仓位** | 通过 Deploy Gate 的策略进入策略池，但每天的 Runtime Gate 决定今天是否分配资金 | 市场状态变化（波动率、流动性、风险事件）需要动态判断 |
| **Runtime → 执行反馈驱动在线学习** | 每笔成交回报（Fill）触发在线模型更新，但不触发全量重训练 | 在线适应市场变化；全量重训练走 Research 循环 |

---

## 系统分层架构

### Layer 0: 数据平台 (Data Platform)

**职责**：获取、清洗、存储、服务市场数据和另类数据。

```
┌─────────────────────────────────────────────────────┐
│                    数据源                             │
│  Tushare (A股, 2000分)  FMP (美股)  Tencent (备用)    │
│  [未来: IBKR实时, GDELT新闻, FRED宏观, SEC基本面]     │
└──────────────────────┬──────────────────────────────┘
                       ↓
┌──────────────────────┴──────────────────────────────┐
│               DuckDB 主数据仓                        │
│  kline_daily (OHLCV + adj_close)                     │
│  symbols (股票/ETF 元数据)                            │
│  [未来: features_daily, parquet cache]                │
└──────────────────────┬──────────────────────────────┘
                       ↓
              DataProviderChain (failover)
              DataValidator (质量检查)
```

**ez-trading 当前状态**: ✅ 已实现
- 3 个数据源 (Tushare/FMP/Tencent) + config 驱动 failover
- DuckDB 存储 + 数据校验
- 缺失: Parquet cache, 多频率 (仅 daily), 另类数据源

---

### Layer 1: 特征工程 (Feature Engineering)

**职责**：将原始数据转换为有预测力的特征（因子）。

```
┌─────────────────────────────────────────────────────┐
│                  因子计算引擎                         │
│                                                      │
│  技术指标 (ts_ops, C++ 加速)                          │
│  ├── MA, EMA, RSI, MACD, BOLL, Momentum              │
│  └── [未来: 更多公式化 alpha, Barra 因子]             │
│                                                      │
│  因子评估 (FactorEvaluator)                           │
│  ├── IC, Rank IC, ICIR, IC Decay                      │
│  └── Agent 用此决定哪些因子值得用                     │
│                                                      │
│  [未来层]                                             │
│  ├── 情绪因子 (新闻 NLP)                              │
│  ├── 另类因子 (宏观/基本面)                           │
│  └── 因子组合优化                                     │
└─────────────────────────────────────────────────────┘
```

**ez-trading 当前状态**: ⚠️ 部分实现
- 6 个技术因子 + C++ 加速 (最高 7.9x)
- IC 评估框架 (时序 IC, 非截面)
- 缺失: 更多因子种类, 截面 IC, 因子组合

---

### Layer 2: 候选生成 (Candidate Generation)

**职责**：批量生成策略候选，快速预筛，淘汰明显无效的。

```
┌─────────────────────────────────────────────────────┐
│                  候选生成                             │
│                                                      │
│  策略注册表 (Strategy Registry)                       │
│  ├── 内置策略 (MACross, Momentum, BollReversion)      │
│  ├── Agent 生成策略 (contract test 自动验证)           │
│  └── 外部策略蓝图导入                                 │
│                                                      │
│  参数网格搜索                                         │
│  ├── 因子组合 × 参数空间                              │
│  └── 向量化快速预筛 (vectorbt 或引擎批量模式)         │
│                                                      │
│  Pre-filter 门槛 (快速淘汰)                           │
│  ├── Sharpe ≥ 阈值                                    │
│  ├── MaxDD ≥ 阈值                                     │
│  └── trades ≥ 最低交易次数                            │
│                                                      │
│  输出: CandidateSpec (strategy + params + data range)  │
└─────────────────────────────────────────────────────┘
```

**ez-trading 当前状态**: ⚠️ 基础已有
- 3 个内置策略 + 自动注册 + contract test
- 缺失: 参数网格搜索, 批量预筛, CandidateSpec 标准化

---

### Layer 3: Research Gate (研究门禁)

**职责**：对候选策略进行多层严格验证。通过 ≠ 可部署，只意味着"研究合格"。

```
┌─────────────────────────────────────────────────────┐
│              Research Gate (多层检验)                  │
│                                                      │
│  第1层: 基础回测 (Backtest)                           │
│  └── VectorizedBacktestEngine + 15 项指标             │
│                                                      │
│  第2层: Walk-Forward 验证 (防过拟合)                  │
│  └── IS vs OOS 衰减, overfitting_score                │
│                                                      │
│  第3层: 统计显著性 (Monte Carlo + Bootstrap)           │
│  └── Sharpe CI, p-value, is_significant               │
│                                                      │
│  第4层: 成本压力测试 [未来]                            │
│  └── 双倍交易成本下是否仍盈利                         │
│                                                      │
│  第5层: 鲁棒性/压力测试 [未来]                        │
│  └── 不同市场状态(牛/熊/震荡)下的表现                 │
│                                                      │
│  输出:                                                │
│  ├── RESEARCH_PASSED + reject_reason_codes[]          │
│  └── research_status: BACKTESTED → RESEARCH_PASSED    │
│       deploy_status: NOT_DEPLOYABLE (需 Deploy Gate)   │
└─────────────────────────────────────────────────────┘
```

**ez-trading 当前状态**: ⚠️ 前3层已实现
- 回测引擎 + WFO + 显著性检验
- Agent Gate 规则（V2.4 roadmap）
- 缺失: Gate 框架（自动判定 PASS/FAIL）, 成本压力, 鲁棒性测试

---

### Layer 4: Deploy Gate (部署门禁)

**职责**：检查策略是否适合在特定账户/环境中部署。Research 通过不意味着可部署。

```
┌─────────────────────────────────────────────────────┐
│              Deploy Gate                              │
│                                                      │
│  账户适配检查                                         │
│  ├── live_enabled (账户是否开通实盘)                   │
│  ├── min_order_notional (最小下单金额)                 │
│  ├── fractional_shares (是否支持碎股)                  │
│  └── investable_capital (可投资金)                     │
│                                                      │
│  执行可行性分析                                       │
│  ├── 策略所需流动性 vs 标的实际流动性                  │
│  ├── 最大持仓数 vs 资金约束                           │
│  └── 交易频率 vs 账户限制                             │
│                                                      │
│  人工审批节点                                         │
│  └── 系统建议 DEPLOYABLE/NOT_DEPLOYABLE，人确认        │
│                                                      │
│  输出:                                                │
│  └── promotion_stage: RESEARCH_PASSED → DEPLOYABLE    │
└─────────────────────────────────────────────────────┘
```

**ez-trading 当前状态**: ❌ 未实现

---

### Layer 5: Runtime Gate (运行时门禁)

**职责**：每天/每个交易周期决定"今天给不给这个策略分配仓位"。可部署 ≠ 今天给仓位。

```
┌─────────────────────────────────────────────────────┐
│              Runtime Gate                             │
│                                                      │
│  信号生成                                             │
│  └── 策略基于最新数据产出信号                         │
│                                                      │
│  运行时风控检查                                       │
│  ├── 策略健康度 (近期表现, 回撤状态)                   │
│  ├── 市场状态 (波动率, 流动性)                        │
│  ├── 仓位限制 (单票/总仓/杠杆)                       │
│  ├── 日损阈值                                         │
│  └── 成本比率 (预期收益 vs 执行成本)                  │
│                                                      │
│  仓位分配                                             │
│  ├── 多策略资本分配                                   │
│  └── Kelly / 风险预算 / 等权                          │
│                                                      │
│  输出:                                                │
│  ├── selected → AllocationResult (target_weight, $)   │
│  └── rejected → 原因 (TAIL_RISK, COST_RATIO, etc.)   │
└─────────────────────────────────────────────────────┘
```

**ez-trading 当前状态**: ❌ 未实现

---

### Layer 6: 执行层 (Execution)

**职责**：将 AllocationResult 转换为实际订单，管理订单生命周期。

```
┌─────────────────────────────────────────────────────┐
│              执行层                                    │
│                                                      │
│  订单管理 (OMS)                                       │
│  ├── 状态机: NEW→PARTIAL→FILLED / CANCELED / REJECTED │
│  ├── 超时撤单                                         │
│  └── 持久化 (重启恢复)                                │
│                                                      │
│  Broker 适配                                          │
│  ├── PaperBroker (仿真)                               │
│  ├── [未来: IBKR, 券商 API]                           │
│  └── 幂等下单, 重试, 撤单一致性                      │
│                                                      │
│  持仓对账                                             │
│  ├── 本地 tracker vs 券商实际持仓                     │
│  └── drift_ratio → 风控告警                           │
│                                                      │
│  学习闭环                                             │
│  └── Fill → OnlineLearner.learn_one()                 │
│                                                      │
│  审计日志                                             │
│  └── 每笔决策可追溯                                   │
└─────────────────────────────────────────────────────┘
```

**ez-trading 当前状态**: ❌ 未实现
- 有 Matcher 抽象（SimpleMatcher/SlippageMatcher），但这是回测层的撮合模拟，不是真实 OMS

---

### Layer 7: 调度 + 监控 (Scheduling & Monitoring)

**职责**：驱动整个系统的循环运转，监控健康状态。

```
┌─────────────────────────────────────────────────────┐
│              调度层                                    │
│                                                      │
│  end_of_day_scheduler (统一入口)                      │
│  ├── daily: 数据更新 → 信号生成 → 执行               │
│  ├── weekly: 持续回测 → 策略健康检查                  │
│  └── monthly: 自动研究循环 → 新候选筛选              │
│                                                      │
│  策略生命周期状态机                                    │
│  └── VALIDATED → PAPER_LIVE → LIMITED_CAPITAL → ACTIVE │
│                                                      │
│              监控层                                    │
│                                                      │
│  健康检查 (4 级降级)                                   │
│  ├── 数据新鲜度监控                                   │
│  ├── 订单失败率采集                                   │
│  └── 策略偏离 vs 容量约束区分                         │
│                                                      │
│  通知 (Discord/Slack/邮件)                             │
│  审计落盘 (每次 run 可复现)                            │
└─────────────────────────────────────────────────────┘
```

**ez-trading 当前状态**: ❌ 未实现
- 有 start.sh/stop.sh 手动启停
- 无自动调度, 无监控, 无生命周期管理

---

## 当前位置总览

```
Layer 0: 数据平台          ██████████ 100%  ← 3数据源 + DuckDB + 校验
Layer 1: 特征工程          ████░░░░░░  40%  ← 6技术因子 + IC评估, 缺更多种类
Layer 2: 候选生成          ███░░░░░░░  30%  ← 策略注册 + contract test, 缺预筛
Layer 3: Research Gate     ██████░░░░  60%  ← 回测+WFO+显著性, 缺Gate框架
Layer 4: Deploy Gate       ░░░░░░░░░░   0%
Layer 5: Runtime Gate      ░░░░░░░░░░   0%
Layer 6: 执行层            ░░░░░░░░░░   0%  (Matcher是回测撮合, 非OMS)
Layer 7: 调度+监控         ░░░░░░░░░░   0%
```

**结论**: 我们完成了数据到研究的前半段（Layer 0-3 部分），后半段（部署到执行）尚未开始。这与 Codex 的判断一致："研究/回测框架，不是完整交易系统"。

---

## 实施优先级

### 近期 (V2.3-V2.4): 把 Research 做到位

先不碰 Deploy/Runtime/Execution，而是把 Layer 0-3 做到"Agent 可以完全自主运转"的程度：

1. **V2.3**: 正确性封顶（对账不变量 + C++/Python 对拍）
2. **V2.4**: Agent 闭环（RunSpec + Runner + Gate + Report + 实验持久化）

完成后，系统可以做到：Agent 提交策略 → 自动评估 → 结构化结果 → 自动晋级/拒绝。

### 中期 (V2.5-V2.6): 候选生成 + Deploy Gate

3. **V2.5**: 参数网格搜索 + 批量预筛 + 更多因子
4. **V2.6**: Deploy Gate（账户适配 + 人工审批节点）

### 远期 (V3+): Runtime + Execution + Monitoring

5. **V3.0**: Paper Broker + 基础 OMS
6. **V3.1**: Runtime Gate + 仓位分配
7. **V3.2**: 调度层 + 监控层 + 生命周期状态机
8. **V3.3**: 真实券商适配（IBKR）

---

## 与参考架构的对应关系

| 参考架构模块 | ez-trading 对应 | 状态 |
|-------------|----------------|------|
| 数据源 (yfinance/Alpha Vantage/IBKR) | ez/data/providers/ (Tushare/FMP/Tencent) | ✅ |
| PostgreSQL 主仓 | DuckDB (ez/data/store.py) | ✅ 轻量替代 |
| Parquet Cache | — | ❌ |
| DuckDB 研究引擎 | 同上 (同一个 DuckDB) | ✅ |
| technical.py (34→41D) | ez/factor/builtin/technical.py (6 指标) | ⚠️ 数量少 |
| geometry.py (黎曼流形) | — | ❌ 不做 |
| sentiment.py (VADER) | — | ❌ 未来可加 |
| factors/ (barra, formula_alphas) | — | ❌ 未来可加 |
| vectorbt 预筛 | — | ❌ V2.5 |
| Pre-filter 门槛 | — | ❌ V2.4 Gate |
| BaseSignalModel (LightGBM) | — | ❌ V3+ AI 引擎 |
| MetaLabeling | — | ❌ V3+ |
| Research Gate 5层 | 前3层已有 (回测/WFO/显著性) | ⚠️ 缺 Gate 框架 |
| Deploy Gate | — | ❌ V2.6 |
| Runtime Gate | — | ❌ V3.1 |
| StrategyAllocator | — | ❌ V3.1 |
| OMS (order_manager) | — | ❌ V3.0 |
| Broker adapter (ib_client) | — | ❌ V3.3 |
| position_reconciler | — | ❌ V3.2 |
| end_of_day_scheduler | — | ❌ V3.2 |
| lifecycle.py (状态机) | — | ❌ V3.2 |
| health_check (4级降级) | — | ❌ V3.2 |
| decision_audit_logger | — | ❌ V3.2 |
| Dash 看板 | web/ (React, 非 Dash) | ✅ 不同技术栈但同功能 |

---

## 参考架构中我们不做的部分

| 模块 | 原因 |
|------|------|
| geometry.py (黎曼流形 SPD 特征) | 用户明确要求不做 |
| transformer_regime.py (MHA) | 团队当前不做 ML 模型 |
| lstm_regime.py | 同上 |
| Kyle λ / Glosten-Milgrom 微观结构 | 无 tick 数据 |
| GARCH 波动率缩放 | 日线级别不需要 |
| BigQuery 审计落盘 | DuckDB + 本地日志足够 |
