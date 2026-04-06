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
| `deployment_spec.py` | DeploymentSpec (不可变配置) + DeploymentRecord (运行时元数据) |
| `deploy_gate.py` | DeployGate — 硬门禁 (内部重算, 不可跳过) |
| `paper_engine.py` | PaperTradingEngine — 模拟执行引擎 |
| `scheduler.py` | Scheduler — 单进程幂等调度 + 业务日期 + 自动恢复 |
| `deployment_store.py` | DeploymentStore — DuckDB 持久化 (specs + records + snapshots) |
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

## 组件 1: DeploymentSpec + DeploymentRecord — 策略导出/序列化

### 问题

研究态的策略是 Python 对象 + 散落的参数。要部署运行需要：
- 固化全部参数 (策略参数 + 成本 + 优化器 + 风控)
- 可复现 (同一个 spec 永远产生同一个策略实例)
- 审批、启动、停止等**运行时元数据**和策略配置分离

### 设计: 两个对象

**DeploymentSpec** — 纯不可变策略配置 (参与哈希)：

```python
@dataclass
class DeploymentSpec:
    """纯策略配置 — 不含任何运行时/审批元数据。
    所有字段参与 spec_id 哈希。JSON 序列化后深冻结。"""

    # 策略配置 (和 PortfolioRunRequest 对齐)
    strategy_name: str          # "TopNRotation" / "StrategyEnsemble" / ...
    strategy_params: dict       # 含 sub_strategies (Ensemble) 或 factor/top_n
    symbols: tuple[str, ...]    # tuple 不可变 (不用 list)
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
    optimizer_params: tuple = ()      # tuple of (k,v) pairs, 不可变
    risk_control: bool = False
    risk_params: tuple = ()           # tuple of (k,v) pairs

    # 资金
    initial_cash: float = 1_000_000.0

    @property
    def spec_id(self) -> str:
        """内容哈希 — 所有字段参与, 无例外"""
        # SHA-256 of canonical JSON → [:16]

    def to_json(self) -> str:
        """序列化 (symbols/optimizer_params/risk_params → list/dict)"""
```

**DeploymentRecord** — 可变运行时记录 (不参与哈希)：

```python
@dataclass
class DeploymentRecord:
    """运行时元数据 — 审批/启动/停止/来源"""
    deployment_id: str              # UUID, 非内容哈希 (每次部署唯一)
    spec_id: str                    # 指向 DeploymentSpec
    name: str                       # 用户可读名称
    status: str = "pending"         # pending → approved → running → stopped/error
    stop_reason: str = ""

    # 来源追溯
    source_run_id: str | None = None
    code_commit: str | None = None
    gate_verdict: dict | None = None  # Deploy Gate 结果快照

    # 生命周期时间 (UTC aware)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: datetime | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
```

### 从回测结果创建

```python
def from_portfolio_run(run_id: str, name: str) -> tuple[DeploymentSpec, DeploymentRecord]:
    """从已完成的组合回测创建部署规格 + 记录"""
    run = portfolio_store.get_run(run_id)
    config = json.loads(run.config)
    spec = DeploymentSpec(
        strategy_name=run.strategy_name,
        strategy_params=json.loads(run.strategy_params),
        symbols=tuple(json.loads(run.symbols)),
        market=config.get("market", "cn_stock"),
        freq=run.freq,
        # ... 从 config 恢复所有参数
    )
    record = DeploymentRecord(
        deployment_id=str(uuid4()),
        spec_id=spec.spec_id,
        name=name,
        source_run_id=run_id,
        code_commit=_get_git_sha(),
    )
    return spec, record
```

### 关键区别: spec_id vs deployment_id

- **spec_id** (内容哈希): 同一策略配置永远相同 → 可做去重
- **deployment_id** (UUID): 每次部署唯一 → 同一配置可以部署多次

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
    # 研究门禁阈值 (内部重算, 不依赖外部传入)
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
    """部署门禁 — 硬门禁, 不可跳过。

    内部自行重算研究门禁 (从 source_run_id 拉回测结果),
    不接受外部传入的 GateVerdict。调用方无法通过不传参绕过。
    """

    def __init__(self, config: DeployGateConfig | None = None):
        self.config = config or DeployGateConfig()

    def evaluate(
        self,
        spec: DeploymentSpec,
        source_run_id: str,               # 必传 — 来源回测 run ID
        portfolio_store: PortfolioStore,   # 必传 — 读取回测结果
    ) -> GateVerdict:
        """
        三阶段硬检查 (全部必须通过):
        1. 从 source_run_id 拉回测结果 → 内部重算研究门禁
        2. 部署专属检查 (回测时长、标的数、集中度)
        3. 配置完整性检查

        所有参数都是必传的, 没有 Optional 可以绕过。
        """
        reasons: list[GateReason] = []

        # Phase 0: 来源回测必须存在
        run = portfolio_store.get_run(source_run_id)
        if not run:
            return GateVerdict(passed=False, reasons=[
                GateReason(rule="source_run_exists", passed=False,
                           value=0, threshold=1,
                           message=f"来源回测 {source_run_id} 不存在")
            ])

        metrics = json.loads(run.metrics) if run.metrics else {}

        # Phase 1: 内部重算研究门禁 (不接受外部传入)
        sharpe = metrics.get("sharpe_ratio", 0)
        reasons.append(GateReason(
            rule="min_sharpe", passed=sharpe >= self.config.min_sharpe,
            value=sharpe, threshold=self.config.min_sharpe,
            message=f"夏普 {sharpe:.2f}"))

        dd = abs(metrics.get("max_drawdown", 1.0))
        reasons.append(GateReason(
            rule="max_drawdown", passed=dd <= self.config.max_drawdown,
            value=dd, threshold=self.config.max_drawdown,
            message=f"最大回撤 {dd:.1%}"))

        trades = metrics.get("trade_count", 0)
        reasons.append(GateReason(
            rule="min_trades", passed=trades >= self.config.min_trades,
            value=trades, threshold=self.config.min_trades,
            message=f"交易次数 {trades}"))

        # Phase 2: 部署专属检查
        n_days = len(json.loads(run.dates)) if run.dates else 0
        reasons.append(GateReason(
            rule="min_backtest_days",
            passed=n_days >= self.config.min_backtest_days,
            value=n_days, threshold=self.config.min_backtest_days,
            message=f"回测天数 {n_days}"))

        n_syms = len(spec.symbols)
        reasons.append(GateReason(
            rule="min_symbols", passed=n_syms >= self.config.min_symbols,
            value=n_syms, threshold=self.config.min_symbols,
            message=f"标的数 {n_syms}"))

        # Phase 3: 配置完整性
        reasons.append(GateReason(
            rule="freq_valid",
            passed=spec.freq in ("daily", "weekly", "monthly"),
            value=0, threshold=0,
            message=f"调仓频率 {spec.freq}"))

        return GateVerdict(
            passed=all(r.passed for r in reasons),
            reasons=reasons,
        )
```

### 不可绕过性保证

- `source_run_id` 和 `portfolio_store` 都是必传参数 (非 Optional)
- 门禁从 DB 自己拉指标重算, 不接受调用方声称的 "研究已通过"
- API 层在 `/deploy` 端点内部构造 DeployGate + 传入 store, 前端无法跳过

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

1. **`execute_day()` 是幂等函数** — 同一 business_date 重复调用不产生新交易 (见 Scheduler 幂等键)
2. **数据获取复用 DataProviderChain** — 同一个数据源，只是 end_date = business_date
3. **成交逻辑从 portfolio engine 提取** — 不是复制粘贴，而是提取共享函数：

```python
# ez/portfolio/execution.py (新文件)
def execute_portfolio_trades(
    target_weights: dict[str, float],
    current_holdings: dict[str, int],
    equity: float,
    cash: float,
    # 行情上下文 — 完整, 不遗漏
    prices: dict[str, float],           # 今日收盘价 (adj_close)
    raw_closes: dict[str, float],       # 今日 raw close (涨跌停判断用)
    prev_raw_closes: dict[str, float],  # 昨日 raw close (涨跌停基准)
    has_bar_today: set[str],            # 今日有实际 bar 的标的 (无 bar 不交易)
    # 成交参数
    cost_model: CostModel,
    lot_size: int = 100,
    t_plus_1: bool = True,
    limit_pct: float = 0.1,
    sold_today: set[str] | None = None,
) -> tuple[list[TradeResult], dict[str, int], float]:
    """
    共享成交逻辑 — 回测引擎和 Paper Trading 引擎都调用。

    输入契约:
    - prices: 成交价基准 (adj_close + 滑点)
    - raw_closes + prev_raw_closes: 涨跌停判断 (raw, 非复权)
    - has_bar_today: 避免 stale price 成交 (停牌/无数据)
    - sold_today: T+1 约束 (当日已卖的不能买回)

    Returns: (trades, new_holdings, new_cash)
    """
```

回测引擎的内循环 (lines 320-406) 改为调用这个函数。Paper Trading 引擎也调用它。
**删除 `paper_broker.py`** — 成交逻辑全部在 `execution.py`, 没有独立 broker 模块。

---

## 组件 4: Scheduler — 定时触发

### 约束声明

- **单进程**: V2.15 Scheduler 是进程内单例。不支持多 worker。文档和启动日志明确声明。
- **业务日期**: tick 接收 `business_date: date` 而非 `date.today()`。调用方负责确定当日是否交易日。
- **幂等**: 每个 deployment 持久化 `last_processed_date`。同日重复 tick 跳过 (返回缓存结果)。
- **交易日历**: Scheduler 依赖 `TradingCalendar`。非交易日 tick 是 no-op (不跳过, 记录为 "非交易日")。

### 设计

```python
class Scheduler:
    """单进程调度器 — 管理 paper trading job 的生命周期。

    ⚠️ 单进程约束: 不支持多 worker / 多实例部署。
    进程重启时通过 resume_all() 从 DB 恢复所有 running 部署。
    """

    def __init__(self, store: DeploymentStore, data_chain: DataProviderChain):
        self.store = store
        self.data_chain = data_chain
        self._engines: dict[str, PaperTradingEngine] = {}
        self._lock = asyncio.Lock()

    async def resume_all(self) -> int:
        """进程启动时自动恢复所有 status='running' 的部署。
        由 app lifespan 调用。返回恢复数量。"""
        running = self.store.list_deployments(status="running")
        count = 0
        for record in running:
            try:
                await self._start_engine(record.deployment_id)
                count += 1
            except Exception as e:
                self.store.save_error(record.deployment_id, date.today(), f"恢复失败: {e}")
        return count

    async def start_deployment(self, deployment_id: str) -> None:
        async with self._lock:
            if deployment_id in self._engines:
                raise ValueError("已在运行")
            await self._start_engine(deployment_id)
            self.store.update_status(deployment_id, "running")

    async def stop_deployment(self, deployment_id: str, reason: str = "user_stop") -> None:
        async with self._lock:
            self._engines.pop(deployment_id, None)
            self.store.update_status(deployment_id, "stopped", stop_reason=reason)

    async def tick(self, business_date: date, calendar: TradingCalendar) -> list[dict]:
        """
        每日触发。

        幂等: 同一 deployment × 同一 business_date 只执行一次。
        非交易日: 跳过执行, 记录 "非交易日" 状态。
        """
        if not calendar.is_trading_day(business_date):
            return [{"date": str(business_date), "skipped": "非交易日"}]

        results = []
        for dep_id, engine in list(self._engines.items()):
            # 幂等检查
            last = self.store.get_last_processed_date(dep_id)
            if last and last >= business_date:
                results.append({"deployment_id": dep_id, "skipped": "已执行"})
                continue

            try:
                result = engine.execute_day(business_date)
                self.store.save_daily_snapshot(dep_id, business_date, result)
                results.append({"deployment_id": dep_id, **result})
            except Exception as e:
                self.store.save_error(dep_id, business_date, str(e))
                results.append({"deployment_id": dep_id, "error": str(e)})
        return results

    async def _start_engine(self, deployment_id: str) -> None:
        """内部: 实例化 engine + 完整状态恢复"""
        record = self.store.get_record(deployment_id)
        spec = self.store.get_spec(record.spec_id)
        strategy, optimizer, risk = self._instantiate(spec)
        engine = PaperTradingEngine(spec, strategy, self.data_chain, optimizer, risk)
        self._restore_full_state(engine, deployment_id)
        self._engines[deployment_id] = engine

    def _restore_full_state(self, engine: PaperTradingEngine, deployment_id: str):
        """完整状态恢复 — 所有影响策略行为的字段"""
        snapshots = self.store.get_all_snapshots(deployment_id)
        if not snapshots:
            return
        latest = snapshots[-1]
        engine.cash = latest["cash"]
        engine.holdings = latest["holdings"]
        engine.prev_weights = latest["weights"]
        engine.prev_returns = latest.get("prev_returns", {})
        # 恢复完整历史 (策略可能需要)
        engine.equity_curve = [s["equity"] for s in snapshots]
        engine.dates = [date.fromisoformat(s["snapshot_date"]) for s in snapshots]
        engine.trades = []
        engine.risk_events = []
        for s in snapshots:
            engine.trades.extend(json.loads(s.get("trades", "[]")))
            engine.risk_events.extend(json.loads(s.get("risk_events", "[]")))
```

### 触发方式

```python
# POST /api/live/tick
# 接收业务日期 (非 date.today()), 由调用方确定
@router.post("/tick")
async def trigger_daily_tick(business_date: date | None = None):
    cal = TradingCalendar.from_market(scheduler.default_market)
    bd = business_date or cal.previous_trading_day(date.today())
    results = await scheduler.tick(bd, cal)
    return {"business_date": str(bd), "results": results}
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
-- 策略配置 (不可变, spec_id 是内容哈希)
CREATE TABLE deployment_specs (
    spec_id VARCHAR PRIMARY KEY,
    spec_json TEXT NOT NULL,            -- JSON: 完整 DeploymentSpec
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 部署记录 (可变, deployment_id 是 UUID)
CREATE TABLE deployment_records (
    deployment_id VARCHAR PRIMARY KEY,
    spec_id VARCHAR NOT NULL REFERENCES deployment_specs(spec_id),
    name VARCHAR NOT NULL,
    status VARCHAR DEFAULT 'pending',   -- pending/approved/running/stopped/error
    stop_reason VARCHAR DEFAULT '',
    source_run_id VARCHAR,              -- 来源回测 ID
    code_commit VARCHAR,
    gate_verdict TEXT,                  -- JSON: Deploy Gate 结果
    last_processed_date DATE,           -- 幂等键: 最后执行的业务日期
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ
);

-- 每日快照 (追加写入, 幂等: deployment_id + snapshot_date 唯一)
CREATE TABLE deployment_snapshots (
    deployment_id VARCHAR NOT NULL,
    snapshot_date DATE NOT NULL,
    equity DOUBLE NOT NULL,
    cash DOUBLE NOT NULL,
    holdings TEXT NOT NULL,             -- JSON: {symbol: shares}
    weights TEXT NOT NULL,              -- JSON: {symbol: weight}
    prev_returns TEXT DEFAULT '{}',     -- JSON: {symbol: float} 策略依赖
    trades TEXT DEFAULT '[]',           -- JSON: 当日交易
    risk_events TEXT DEFAULT '[]',      -- JSON: 当日风控事件
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

V2.15 前置条件。从 `ez/portfolio/engine.py` lines 300-406 提取到 `ez/portfolio/execution.py`。

完整函数签名见组件 3 的 `execute_portfolio_trades()`。关键点：
- **输入完整性**: prices + raw_closes + prev_raw_closes + has_bar_today，覆盖现有回测的所有市场规则上下文
- **回测引擎重构**: `run_portfolio_backtest` 内循环改为调用 `execute_portfolio_trades()`，不改变外部行为
- **Paper Trading 复用**: `PaperTradingEngine.execute_day()` 调用同一函数
- **paper_broker.py 不存在**: 成交逻辑全部在 `execution.py`，没有独立 broker 模块

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
