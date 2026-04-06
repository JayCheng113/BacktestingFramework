# V2.15 — Paper Trading Bridge: 研究到实盘的过渡层

## 定位

V2.15 不是完整实盘系统。它是研究平台 (V1-V2.14) 到未来实盘系统 (V3.0) 之间的**桥梁层**。

目标：让研究完成的策略能**以相同的信号/风控/成交逻辑**在真实行情上模拟运行，验证从回测到实际的gap，同时为 V3.0 的 OMS/Broker 奠定接口基础。

**不做**：真实 Broker 对接、tick 级执行、多账户、权限管理。

---

## 架构概览

```
研究态 (V2.14 已有)                    部署态 (V2.15 新增)
┌──────────────┐                    ┌──────────────────────┐
│ 回测 Engine   │                    │ Paper Trading Engine  │
│ (historical)  │                    │ (live bar-driven)     │
│              │                    │                      │
│ strategy.    │  ──── 同一个 ────→  │ strategy.            │
│ generate_    │     strategy 实例    │ generate_weights()   │
│ weights()    │                    │                      │
│              │                    │ + MarketRules        │
│ optimizer    │  ──── 同一个 ────→  │ + optimizer          │
│ risk_manager │     配置           │ + risk_manager       │
│ cost_model   │                    │ + cost_model         │
└──────────────┘                    └──────────────────────┘
       │                                    │
       ↓                                    ↓
┌──────────────┐                    ┌──────────────────────┐
│ portfolio_   │                    │ deployment_store     │
│ store        │                    │ (new DuckDB tables)  │
│ (研究记录)    │                    │ (部署+运行记录)       │
└──────────────┘                    └──────────────────────┘
                                           │
                                           ↓
                                    ┌──────────────────────┐
                                    │ Deploy Gate          │
                                    │ (研究门禁 → 部署审批) │
                                    └──────────────────────┘
```

---

## 模块设计

### 新增模块: `ez/live/`

| 文件 | 职责 |
|------|------|
| `deployment_spec.py` | DeploymentSpec — 不可变部署规格 |
| `deploy_gate.py` | DeployGate — 部署审批 (自动检查 + 手动确认) |
| `paper_engine.py` | PaperTradingEngine — 模拟执行引擎 |
| `paper_broker.py` | PaperBroker — 纸面成交撮合 |
| `scheduler.py` | Scheduler — cron-like 定时触发 |
| `deployment_store.py` | DeploymentStore — DuckDB 持久化 |
| `monitor.py` | Monitor — 最小监控集 |

### 依赖关系

```
ez/live/ 消费:
  - ez/portfolio/portfolio_strategy.py (PortfolioStrategy ABC)
  - ez/portfolio/engine.py (复用 _lot_round, CostModel, 交易逻辑)
  - ez/portfolio/optimizer.py (PortfolioOptimizer)
  - ez/portfolio/risk_manager.py (RiskManager)
  - ez/core/market_rules.py (MarketRulesMatcher)
  - ez/data/provider.py (DataProviderChain — 获取最新行情)
  - ez/agent/gates.py (GateConfig, GateVerdict — Deploy Gate 复用结构)

ez/live/ 不依赖:
  - ez/backtest/ (回测引擎是历史模拟，live 是前向执行)
  - ez/agent/ (Agent 是研究工具，不是部署工具)
  - ez/api/ (API 依赖 live，不是反过来)
```

---

## 组件 1: DeploymentSpec — 策略导出/序列化

### 问题

研究态的策略是 Python 对象 + 散落的参数。要部署运行需要：
- 固化全部参数 (策略参数 + 成本 + 优化器 + 风控)
- 记录版本信息 (代码提交、依赖版本)
- 绑定门禁结果 (通过了哪些 gate)
- 可复现 (同一个 spec 永远产生同一个策略实例)

### 设计

```python
@dataclass(frozen=True)
class DeploymentSpec:
    """Immutable deployment specification — the bridge from research to live."""

    # 身份
    deployment_id: str          # 内容哈希 (SHA-256[:16])
    name: str                   # 用户可读名称

    # 策略配置 (和 PortfolioRunRequest 对齐)
    strategy_name: str          # "TopNRotation" / "StrategyEnsemble" / ...
    strategy_params: dict       # 含 sub_strategies (Ensemble) 或 factor/top_n
    symbols: list[str]          # 标的池
    market: str                 # "cn_stock" / "us_stock" / "hk_stock"
    freq: str                   # "daily" / "weekly" / "monthly"

    # 成本模型
    buy_commission_rate: float = 0.0003
    sell_commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.0005
    slippage_rate: float = 0.001
    min_commission: float = 5.0
    lot_size: int = 100
    limit_pct: float = 0.1

    # 优化器 + 风控
    optimizer: str | None = None
    optimizer_params: dict = field(default_factory=dict)
    risk_control: bool = False
    risk_params: dict = field(default_factory=dict)

    # 资金
    initial_cash: float = 1_000_000.0

    # 来源追溯
    source_run_id: str | None = None     # 来自哪个回测 run
    gate_verdict: dict | None = None     # 门禁结果快照
    code_commit: str | None = None       # git SHA

    # 时间
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def deployment_id(self) -> str:
        """内容哈希 — 相同配置永远生成相同 ID"""
        # SHA-256 of canonical JSON (exclude created_at, name)
```

### 从回测结果创建

```python
# 用户在前端点 "部署到模拟盘"
def from_portfolio_run(run_id: str, name: str) -> DeploymentSpec:
    """从已完成的组合回测创建部署规格"""
    run = portfolio_store.get_run(run_id)
    config = json.loads(run.config)
    return DeploymentSpec(
        name=name,
        strategy_name=run.strategy_name,
        strategy_params=json.loads(run.strategy_params),
        symbols=json.loads(run.symbols),
        market=config.get("market", "cn_stock"),
        freq=run.freq,
        # ... 从 config 恢复所有参数
        source_run_id=run_id,
        code_commit=_get_git_sha(),
    )
```

---

## 组件 2: Deploy Gate — 部署审批

### 问题

不是所有通过 ResearchGate 的策略都适合部署。部署前需要额外检查：
- 回测是否足够长 (至少 2 年)
- WF 验证是否通过
- 标的池流动性是否充足
- 成本模型是否合理

### 设计

```python
@dataclass
class DeployGateConfig:
    """部署门禁阈值 — 比研究门禁更严格"""
    # 继承 ResearchGate 检查
    min_sharpe: float = 0.5
    max_drawdown: float = 0.25          # 比研究更严 (0.25 vs 0.30)
    min_trades: int = 20                # 比研究更严 (20 vs 10)
    max_p_value: float = 0.05
    max_overfitting_score: float = 0.3  # 比研究更严 (0.3 vs 0.5)

    # 部署专属检查
    min_backtest_days: int = 504        # 至少 2 年交易日
    require_wfo: bool = True            # 必须通过 WF
    min_symbols: int = 5                # 至少 5 个标的 (组合策略)
    max_concentration: float = 0.4      # 单股最大权重 40%


class DeployGate:
    """部署门禁 — ResearchGate 的超集"""

    def __init__(self, config: DeployGateConfig | None = None):
        self.config = config or DeployGateConfig()

    def evaluate(self, spec: DeploymentSpec,
                 research_verdict: GateVerdict | None = None,
                 backtest_metrics: dict | None = None) -> GateVerdict:
        """
        两阶段检查:
        1. 如果有 research_verdict, 验证研究门禁已通过
        2. 额外部署检查 (回测时长、标的数、集中度)
        返回 GateVerdict (和 ResearchGate 相同结构)
        """
        reasons = []

        # Phase 1: 研究门禁通过?
        if research_verdict and not research_verdict.passed:
            reasons.append(GateReason(
                rule="research_gate", passed=False,
                value=0, threshold=1,
                message="研究门禁未通过，不能部署"
            ))

        # Phase 2: 部署专属检查
        if backtest_metrics:
            # 回测时长
            bt_days = backtest_metrics.get("n_trading_days", 0)
            reasons.append(GateReason(
                rule="min_backtest_days",
                passed=bt_days >= self.config.min_backtest_days,
                value=bt_days, threshold=self.config.min_backtest_days,
                message=f"回测天数 {bt_days} {'>=':if passed else '<'} {self.config.min_backtest_days}"
            ))

            # 更严格的 sharpe/drawdown
            sharpe = backtest_metrics.get("sharpe_ratio", 0)
            reasons.append(GateReason(
                rule="min_sharpe", passed=sharpe >= self.config.min_sharpe,
                value=sharpe, threshold=self.config.min_sharpe,
                message=f"夏普 {sharpe:.2f}"
            ))
            # ... 其余规则

        # 标的池检查
        reasons.append(GateReason(
            rule="min_symbols",
            passed=len(spec.symbols) >= self.config.min_symbols,
            value=len(spec.symbols), threshold=self.config.min_symbols,
            message=f"标的数 {len(spec.symbols)}"
        ))

        return GateVerdict(
            passed=all(r.passed for r in reasons),
            reasons=reasons
        )
```

### 审批流程

```
用户点 "部署到模拟盘"
    ↓
DeployGate.evaluate() — 自动检查
    ↓ (全通过)
显示检查报告 → 用户确认 "开始模拟"
    ↓ (用户点确认)
创建 Deployment + 启动 PaperTradingJob
```

门禁不通过时显示原因，用户不能跳过 (硬门禁)。

---

## 组件 3: Paper Trading Engine

### 问题

回测引擎在历史数据上全量迭代。Paper Trading 需要：
- 每天（或每周/每月调仓日）获取最新行情
- 调用同一个 `strategy.generate_weights()`
- 用同一套成交逻辑执行
- 持续跟踪持仓、权益、风控状态

### 设计

```python
class PaperTradingEngine:
    """前向执行引擎 — 复用回测的策略/优化/风控/成交逻辑"""

    def __init__(
        self,
        deployment: DeploymentSpec,
        strategy: PortfolioStrategy,
        data_chain: DataProviderChain,
        optimizer: PortfolioOptimizer | None = None,
        risk_manager: RiskManager | None = None,
    ):
        self.deployment = deployment
        self.strategy = strategy
        self.data_chain = data_chain
        self.optimizer = optimizer
        self.risk_manager = risk_manager

        # 运行状态
        self.cash = deployment.initial_cash
        self.holdings: dict[str, int] = {}       # symbol → shares
        self.equity_curve: list[float] = []
        self.dates: list[date] = []
        self.trades: list[dict] = []
        self.prev_weights: dict[str, float] = {}
        self.prev_returns: dict[str, float] = {}
        self.risk_events: list[dict] = []

    def execute_day(self, today: date) -> dict:
        """
        单日执行 — 由 Scheduler 调用

        Returns: {
            "date": today,
            "equity": float,
            "trades": [...],
            "risk_events": [...],
            "rebalanced": bool,
        }
        """
        # 1. 获取最新行情 (today 的收盘价)
        universe_data = self._fetch_latest(today)

        # 2. Mark-to-market
        equity = self._mark_to_market(universe_data, today)

        # 3. 风控检查
        if self.risk_manager:
            event = self.risk_manager.check_drawdown(self.equity_curve + [equity])
            if event:
                self.risk_events.append({"date": str(today), **event})

        # 4. 是否调仓日?
        if self._is_rebalance_day(today):
            # 切片历史数据 [today-lookback, today-1] — 和回测完全相同
            sliced = self._slice_history(universe_data, today)

            # 调用策略 — 完全相同的接口
            target_weights = self.strategy.generate_weights(
                sliced, today, self.prev_weights, self.prev_returns
            )

            # 优化器
            if self.optimizer:
                self.optimizer.set_context(today, sliced)
                target_weights = self.optimizer.optimize(target_weights)

            # 执行交易 — 复用回测的成交逻辑
            day_trades = self._execute_trades(target_weights, universe_data, today)
            self.trades.extend(day_trades)

        # 5. 记录
        self.equity_curve.append(equity)
        self.dates.append(today)

        return {
            "date": str(today),
            "equity": equity,
            "trades": day_trades if self._is_rebalance_day(today) else [],
            "risk_events": [e for e in self.risk_events if e["date"] == str(today)],
            "rebalanced": self._is_rebalance_day(today),
        }

    def _fetch_latest(self, today: date) -> dict[str, pd.DataFrame]:
        """获取到 today 为止的历史数据 (含 today 收盘)"""
        lookback_start = today - timedelta(days=self.strategy.lookback_days + 30)
        data = {}
        for sym in self.deployment.symbols:
            bars = self.data_chain.get_kline(sym, self.deployment.market, "daily",
                                              lookback_start, today)
            if bars:
                df = pd.DataFrame([b.__dict__ for b in bars]).set_index("date")
                data[sym] = df
        return data

    def _execute_trades(self, target_weights, universe_data, today):
        """复用回测引擎的 weight→shares→trade 逻辑"""
        # 和 ez/portfolio/engine.py lines 300-406 相同的:
        # - weight → amount → shares (lot round)
        # - sell first, buy second
        # - T+1, limit price, lot size 检查
        # - commission + stamp tax + slippage
        ...
```

### 关键设计决策

1. **`execute_day()` 是无状态函数** — 所有状态在 `self` 上，Scheduler 只需要每天调一次
2. **数据获取复用 DataProviderChain** — 同一个数据源，只是 end_date = today
3. **成交逻辑从 portfolio engine 提取** — 不是复制粘贴，而是提取共享函数：

```python
# ez/portfolio/engine.py (新增导出)
def execute_portfolio_trades(
    target_weights: dict[str, float],
    current_holdings: dict[str, int],
    prices: dict[str, float],
    cash: float,
    cost_model: CostModel,
    market_rules: MarketRulesMatcher | None = None,
    sold_today: set[str] | None = None,
) -> tuple[list[dict], dict[str, int], float]:
    """
    共享成交函数 — 回测引擎和 Paper Trading 引擎都调用
    Returns: (trades, new_holdings, new_cash)
    """
```

这是 V2.15 最关键的重构：把回测引擎的交易执行逻辑提取为独立函数。

---

## 组件 4: Scheduler — 定时触发

### 设计

```python
class Scheduler:
    """最小调度器 — 管理 paper trading job 的生命周期"""

    def __init__(self, store: DeploymentStore):
        self.store = store
        self._jobs: dict[str, PaperTradingEngine] = {}  # deployment_id → engine
        self._lock = asyncio.Lock()

    async def start_deployment(self, deployment_id: str) -> None:
        """启动一个部署的 paper trading"""
        async with self._lock:
            if deployment_id in self._jobs:
                raise ValueError("已在运行")
            spec = self.store.get_deployment(deployment_id)
            strategy, optimizer, risk = self._instantiate(spec)
            engine = PaperTradingEngine(spec, strategy, ...)

            # 恢复历史状态 (如果有)
            self._restore_state(engine, deployment_id)

            self._jobs[deployment_id] = engine
            self.store.update_status(deployment_id, "running")

    async def stop_deployment(self, deployment_id: str, reason: str = "user_stop") -> None:
        """停止一个部署"""
        async with self._lock:
            if deployment_id in self._jobs:
                del self._jobs[deployment_id]
            self.store.update_status(deployment_id, "stopped", stop_reason=reason)

    async def tick(self, today: date) -> list[dict]:
        """
        每日触发 — 由外部 cron 或 API 调用
        对所有 running 的 deployment 执行当日逻辑
        """
        results = []
        for dep_id, engine in list(self._jobs.items()):
            try:
                result = engine.execute_day(today)
                self.store.save_daily_snapshot(dep_id, result)
                results.append({"deployment_id": dep_id, **result})
            except Exception as e:
                self.store.save_error(dep_id, today, str(e))
                results.append({"deployment_id": dep_id, "error": str(e)})
        return results

    def _restore_state(self, engine: PaperTradingEngine, deployment_id: str):
        """从 DB 恢复最后的持仓/现金状态 (crash recovery)"""
        latest = self.store.get_latest_snapshot(deployment_id)
        if latest:
            engine.cash = latest["cash"]
            engine.holdings = latest["holdings"]
            engine.equity_curve = latest["equity_curve"]
            engine.dates = latest["dates"]
            engine.prev_weights = latest["prev_weights"]
```

### 触发方式

V2.15 用最简方案：**API 触发** (不做真 cron)

```python
# POST /api/live/tick
# 由用户手动调用 (开发/测试)
# 或由外部 cron job 每日收盘后调用 (生产)
@router.post("/tick")
async def trigger_daily_tick():
    today = date.today()
    results = await scheduler.tick(today)
    return {"date": str(today), "results": results}
```

V3.0 时替换为内置 APScheduler 或 Celery。

---

## 组件 5: 监控最小集

### 设计

```python
@dataclass
class DeploymentHealth:
    """单个部署的健康状态"""
    deployment_id: str
    name: str
    status: str                  # running / stopped / error

    # 绩效
    cumulative_return: float
    max_drawdown: float
    sharpe_ratio: float | None

    # 今日
    today_pnl: float
    today_trades: int

    # 风控
    risk_events_today: int
    total_risk_events: int
    consecutive_loss_days: int

    # 系统
    last_execution_date: date
    last_execution_duration_ms: float
    days_since_last_trade: int
    error_count: int


class Monitor:
    """最小监控 — 聚合所有部署的健康状态"""

    def __init__(self, store: DeploymentStore):
        self.store = store

    def get_dashboard(self) -> list[DeploymentHealth]:
        """返回所有活跃部署的健康摘要"""
        ...

    def check_alerts(self) -> list[dict]:
        """
        检查告警条件:
        - 连续亏损 > 5 天
        - 最大回撤 > 阈值
        - 执行延迟 > 60s
        - 连续错误 > 3 次
        - 距上次交易 > 30 天
        """
        ...
```

### 前端

在 Navbar 添加 "模拟盘" tab，页面包含：

| 区域 | 内容 |
|------|------|
| 部署列表 | 状态灯 (绿/黄/红) + 策略名 + 累计收益 + 今日 PnL |
| 详情面板 | 净值曲线 + 持仓饼图 + 交易记录 + 风控事件 |
| 操作 | 暂停/恢复/停止 + 手动触发 tick |

---

## 持久化: DeploymentStore

```sql
-- 部署规格 (不可变)
CREATE TABLE deployments (
    deployment_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    spec TEXT NOT NULL,              -- JSON: 完整 DeploymentSpec
    status VARCHAR DEFAULT 'pending', -- pending/approved/running/stopped/error
    stop_reason VARCHAR DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,           -- Deploy Gate 通过时间
    started_at TIMESTAMP,            -- 开始运行时间
    stopped_at TIMESTAMP,
    source_run_id VARCHAR,           -- 来源回测 ID
    gate_verdict TEXT                -- JSON: Deploy Gate 结果
);

-- 每日快照 (追加写入)
CREATE TABLE deployment_snapshots (
    deployment_id VARCHAR NOT NULL,
    snapshot_date DATE NOT NULL,
    equity DOUBLE NOT NULL,
    cash DOUBLE NOT NULL,
    holdings TEXT NOT NULL,           -- JSON: {symbol: shares}
    weights TEXT NOT NULL,            -- JSON: {symbol: weight}
    trades TEXT DEFAULT '[]',         -- JSON: 当日交易
    risk_events TEXT DEFAULT '[]',    -- JSON: 当日风控事件
    rebalanced BOOLEAN DEFAULT FALSE,
    execution_ms DOUBLE,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (deployment_id, snapshot_date)
);
```

---

## API 设计

### 新增路由: `ez/api/routes/live.py`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/live/deploy | 从回测 run 创建 DeploymentSpec |
| GET | /api/live/deployments | 列出所有部署 |
| GET | /api/live/deployments/{id} | 部署详情 + 最新快照 |
| POST | /api/live/deployments/{id}/approve | Deploy Gate 审批 |
| POST | /api/live/deployments/{id}/start | 启动 paper trading |
| POST | /api/live/deployments/{id}/stop | 停止 |
| POST | /api/live/deployments/{id}/pause | 暂停 |
| POST | /api/live/tick | 触发每日执行 (所有 running 部署) |
| GET | /api/live/dashboard | 监控仪表板 |
| GET | /api/live/deployments/{id}/snapshots | 历史快照 (净值曲线数据) |
| GET | /api/live/deployments/{id}/trades | 交易记录 |
| GET | /api/live/deployments/{id}/stream | SSE 实时状态流 |

---

## 前端设计

### 新增页面: PaperTradingPage

**入口**: Navbar 新增 "模拟盘" tab

**布局**:

```
┌─────────────────────────────────────────────────────────┐
│ 模拟盘监控                                               │
├──────────────────┬──────────────────────────────────────┤
│                  │ 净值曲线 (ECharts)                     │
│ 部署列表          │                                      │
│ ┌──────────────┐ ├──────────────────────────────────────┤
│ │ 策略A ● 运行  │ │ 指标卡片: 累计收益 | 夏普 | 回撤 | PnL │
│ │ +2.3% 今日   │ ├──────────────────────────────────────┤
│ ├──────────────┤ │ 持仓饼图 | 当日交易 | 风控事件          │
│ │ 策略B ● 停止  │ │                                      │
│ │ -0.5% 今日   │ │                                      │
│ └──────────────┘ │                                      │
│                  │ [暂停] [停止] [手动 Tick]              │
│ [+ 新建部署]     │                                      │
└──────────────────┴──────────────────────────────────────┘
```

**从回测部署流程**:

```
组合回测结果页 → 点 "部署到模拟盘"
    ↓
弹出 DeploymentSpec 预览 (参数、成本、标的)
    ↓ 用户确认
Deploy Gate 自动检查
    ↓ (通过)
显示门禁报告 → 用户点 "开始模拟"
    ↓
跳转到模拟盘页面
```

---

## 核心重构: 提取共享成交函数

这是 V2.15 的前置条件。从 `ez/portfolio/engine.py` 提取：

```python
# ez/portfolio/execution.py (新文件)

@dataclass
class TradeOrder:
    symbol: str
    side: Literal["buy", "sell"]
    target_shares: int
    target_weight: float

@dataclass
class TradeResult:
    symbol: str
    side: str
    shares: int
    price: float
    amount: float
    commission: float
    stamp_tax: float
    slippage: float

def execute_portfolio_trades(
    target_weights: dict[str, float],
    current_holdings: dict[str, int],
    prices: dict[str, float],
    prev_closes: dict[str, float],
    cash: float,
    equity: float,
    cost_model: CostModel,
    lot_size: int = 100,
    t_plus_1: bool = True,
    limit_pct: float = 0.1,
    sold_today: set[str] | None = None,
) -> tuple[list[TradeResult], dict[str, int], float]:
    """
    共享成交逻辑:
    1. 计算 target shares (weight → amount → shares → lot round)
    2. Sell first, buy second
    3. Apply market rules (T+1, limit price, lot size)
    4. Deduct costs (commission + stamp tax + slippage)

    Returns: (trades, new_holdings, new_cash)

    回测引擎和 Paper Trading 引擎都调用这个函数。
    """
```

回测引擎的 `run_portfolio_backtest` 改为调用这个函数。Paper Trading 引擎也调用它。**Same code, two contexts.**

---

## 测试策略

| 层级 | 测试 | 验证 |
|------|------|------|
| 单元 | DeploymentSpec 序列化 / 哈希 | 相同配置 → 相同 deployment_id |
| 单元 | DeployGate 各规则 | 阈值判断正确 |
| 单元 | execute_portfolio_trades | 和原回测引擎产出一致 |
| 集成 | PaperTradingEngine.execute_day | 单日完整流程 |
| 集成 | Scheduler.tick | 多部署批量执行 |
| 集成 | crash recovery | stop → start → 恢复持仓 |
| 回归 | 回测引擎不受影响 | 提取共享函数后全量测试通过 |
| E2E | 回测 → 部署 → 模拟 → 监控 | 完整链路 |

---

## 实施顺序

```
Phase A: 基础设施 (可独立发布)
  A1. execute_portfolio_trades 提取 + 回测引擎重构
  A2. DeploymentSpec + DeploymentStore
  A3. DeployGate
  A4. PaperTradingEngine (核心循环)

Phase B: 调度与监控
  B1. Scheduler (API 触发)
  B2. Monitor (最小监控集)
  B3. crash recovery (状态恢复)

Phase C: 前端 + API
  C1. API routes (live.py)
  C2. PaperTradingPage (部署列表 + 净值 + 持仓)
  C3. 回测结果 → "部署到模拟盘" 按钮

Phase D: 文档 + 测试
  D1. DocsPage Ch15 模拟盘
  D2. CLAUDE.md V2.15
  D3. 全量测试 + code review
```

---

## 不做 (V3.0 scope)

| 推迟到 V3.0 | 原因 |
|---|---|
| 真实 Broker 对接 | 需要券商 API (通达信/恒生/CTP) |
| Tick 级执行 | 当前是日线级，够用 |
| 多账户 / RBAC | V2.15 单用户 |
| Kill Switch | V2.15 手动暂停够用 |
| OMS 完整状态机 | Paper Trading 不需要订单路由 |
| 自动调度 (APScheduler) | V2.15 用 API trigger / 外部 cron |
| 告警推送 (微信/钉钉) | V2.15 只在页面显示 |
