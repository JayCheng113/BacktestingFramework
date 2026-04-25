# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范。

---

## [0.3.3] - 2026-04-16

### 新增

- **QMT实盘对接**：影子券商 + 小白名单真实提交 + 回调驱动执行同步；`XtQuantShadowClient` 提供锁保护同步路径，回调新鲜度阈值兜底，重连退避上限60s
- **四路对账闭环**：`account + order + position + trade` 四路独立对账事件，`/broker-state` 新增 `latest_position_reconcile` / `latest_trade_reconcile`，任一漂移均 fail-closed
- **QMT宿主服务**：`QMTHostService` 独立管理长连接会话与回调消费者，调度器可来去自如而不终止会话；`ensure_ready_or_raise()` 在宿主健康状态非 `READY` 时 fail-close 所有提交/撤单
- **资金策略框架**：`CapitalPolicyEngine` 支持阶段式梯度（`read_only → paper_sim → small_whitelist → expanded → full`），每阶段设有每日最大资金/单标的最大持仓/最大总敞口限制；阶段跃升需满足最小无漂移天数和最小订单成功率；环境变量 `EZ_LIVE_QMT_KILL_SWITCH` 可即时降级至 `paper_sim`
- **资金策略接入**：`PaperOMS.execute_rebalance` 接入 `CapitalPolicyEngine`，spec 配置 `{"capital_policy": {"enabled": true, "stage": "small_whitelist"}}` 可最小化激活
- **混合市场 auto-tick 按本地日分批**：multi-market 场景下按市场本地业务日批量触发，拒绝非正间隔

### 修复

- 存量非CN部署在旧 `_build_spec_from_run` bug 窗口内构建的历史记录，启动/恢复时改为 fail-closed，不再静默使用CN规则运行

---

## [0.3.x] - V3 执行架构

### 新增

- **Paper Trading OMS**：事件溯源 + 幂等重放；`LiveLedger.replay()` 对重复 `event_id` / broker执行 / 撤单请求去重
- **PaperBroker 抽象**：经纪商状态机正向推进（不允许从终态回退），订单状态持久化
- **部署门控**：`DeployGate` 严于研究门控；`DeploymentSpec` 内容寻址，spec_id 折叠 `broker_type` / `shadow_broker_type`
- **调度器**：幂等、单进程；`asyncio.Lock` 串行化每个部署的 `tick / cancel / pause / resume / pump_broker_state`；tick 以单次 DuckDB 事务写入事件+快照+broker订单关联
- **监控与告警**：SSE 实时推送；可选 webhook 告警（环境变量开关）
- **策略状态持久化**：跨重启保持策略状态
- **预交易风控引擎**：`PreTradeRiskEngine` 在所有规则前评估资金策略
- **运行时分配器**：支持 optimizer 驱动的仓位分配
- **QMT影子券商**：影子/对账模式与小白名单真实提交双轨并行

---

## [0.2.x] - 研究与优化

### 新增

- **组合优化器**：均值方差、最小方差、风险平价，协方差矩阵支持 Ledoit-Wolf 收缩
- **风控模块**：回撤熔断、换手率限制
- **Brinson 归因**：Carino 几何链接法
- **MLAlpha**：支持9种估计器（Ridge / Lasso / ElasticNet / LinearRegression / DecisionTreeRegressor / RandomForestRegressor / GradientBoostingRegressor / LGBMRegressor / XGBRegressor），含特征重要性稳定性、IS/OOS IC衰减、换手率和过拟合裁决诊断
- **StrategyEnsemble**：多策略集成
- **研究管线**：Nested OOS、Walk-Forward、Paired Block Bootstrap 可复用步骤
- **因子中性化与合成**：截面因子中性化与加权合成
- **基本面数据层**：内置基本面截面因子
- **Parquet 本地缓存**：本地市场数据 Parquet 缓存路径支持
- **AI 自主研究**：LLM 驱动的自主策略研究 Agent（代码沙箱 + 守护检查）

### 修复

- 组合与单股指标公式统一（`sharpe_ratio` / `sortino_ratio` / `alpha` / `beta` / `profit_factor`），存量 V2.12.2 之前的回测结果与新结果不可直接比较
- 引擎分红处理统一：`open` 缺失时回落至 raw `close` 再乘调整系数，避免除权日双重调整
- Walk-Forward 尾部丢弃修复

---

## [0.1.x] - 初始平台

### 新增

- **单股回测引擎**：完整 A 股规则（T+1、手数限制、涨跌停板、印花税）
- **因子计算与评估**：IC / RankIC / 衰减 / 相关性分析
- **策略框架**：自动注册 + 参数 schema 声明
- **Walk-Forward 验证与显著性检验**：Deflated Sharpe、最小回测长度、年度拆解
- **截面因子研究**：跨标的因子建模与分析
- **FastAPI REST API**：完整后端路由
- **React 19 + TypeScript + ECharts 前端**：交互式研究仪表盘
- **LLM 编程助手**：自然语言辅助策略开发
- **沙箱安全执行**：用户代码守护（前瞻性、NaN/Inf、权重合规、确定性检查）
- **Docker + CI/CD**：容器化部署与持续集成
