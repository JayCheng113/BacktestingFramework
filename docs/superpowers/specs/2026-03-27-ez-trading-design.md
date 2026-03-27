# ez-trading 设计规格书

## 项目愿景

**ez-trading 是世界上第一个 Agent-Native 量化平台。**

不是"一个有 AI 功能的回测框架"，而是"一个从底层为 AI agent 自主开发、评估、部署策略而设计的量化系统"。

**核心差异化（vs 竞品）：**

| 竞品 | 它做得好的 | 它做不到的 | ez-trading 的定位 |
|------|-----------|-----------|------------------|
| Qlib | AI/ML 研究最强（40+ 模型） | 不能交易，无看板，A 股数据差 | AI 能力 + 可交易 + 看板 |
| NautilusTrader | Rust 高性能，回测=实盘 | 学习曲线陡峭，无 AI，无看板 | C++ 性能 + 低门槛 + 看板 |
| FreqTrade | UX 最佳（Web+Telegram），FreqAI | 仅加密货币 | 多市场 + 更强 AI |
| VNPy | A 股/期货生态最强 | 代码质量差，测试弱，AI 初期 | 专业工程 + 强 AI |
| QuantConnect | 多资产，400TB 数据 | 依赖云平台，AI 弱 | 本地化 + AI 原生 |
| 所有平台 | 各自领域 | **无人为 agent 设计架构** | **Agent-Native** |

**Agent-Native 的含义：**
- Agent 创建策略文件 → contract test 自动验证 → 无需人类审核代码
- 因子 IC 评估告诉 agent 哪些因子值得用（数据驱动，非直觉）
- Walk-Forward 验证防止 agent 过拟合（行业第二大痛点，无竞品解决）
- 统计显著性检验告诉 agent 回测结果是否可信
- 人类通过 Web 看板监督 agent 的全部操作
- Core/Extension 架构保证 agent 不会意外破坏系统

## 核心原则

| 原则 | 含义 | 设计约束 |
|------|------|----------|
| **Agent-Native** | 系统为 AI agent 自主操作而设计 | 自动注册、contract test、因子评估闭环 |
| **高效** | 计算高效 + 开发高效 | C++ 热路径，Python 编排；无冗余抽象 |
| **精简** | 最少代码量达成目标 | 单进程架构，拒绝微服务；无 XML/样板代码 |
| **专业** | 金融级严谨性 | Walk-Forward 验证，统计显著性检验，防过拟合 |
| **现代化** | 技术栈领先 | C++20/23, Python 3.12+, React 19, DuckDB |

## 第一性原理分析

### 量化系统的本质

一条计算管线：**数据 -> 因子 -> 信号 -> 决策 -> 执行**

每一层的核心职责：

```
数据层    ：获取、清洗、存储、服务市场数据和另类数据
因子层    ：将原始数据转换为有预测力的特征
信号层    ：因子组合 + 模型推理 -> 交易信号
决策层    ：信号 -> 目标仓位（组合优化、风控约束）
执行层    ：目标仓位 -> 实际订单（撮合、滑点、成本优化）
```

### 对 jzhu-trading 的诊断

| 层级 | jzhu-trading 现状 | 诊断 |
|------|-------------------|------|
| 数据 | FMP 单源 + TimescaleDB 缓存 | 仅是 API 代理，非数据管理系统 |
| 因子 | 无 | 缺失 |
| 信号 | 无 | 缺失 |
| 决策 | 无 | 缺失 |
| 执行 | 无 | 缺失 |
| 回测 | 无 | 项目名含 Backtesting 但无回测引擎 |
| 架构 | Java 微服务 + Clean Architecture | 过度工程，agent 理解成本极高 |

**根因**：选择了错误的技术栈（Java/Spring Boot）来解决错误的问题（把量化系统当 Web 应用来搭建）。

### 顶级量化的共识（Two Sigma, Citadel, Jump Trading, Qlib, NautilusTrader）

1. **多语言分层**：C++/Rust 做计算核心，Python 做研究/AI/编排
2. **混合回测**：向量化快筛 + 事件驱动精验
3. **数据即竞争力**：专用列式存储（kdb+, ArcticDB, DuckDB）
4. **AI 原生**：因子挖掘、LLM 信号、RL 决策已成标配
5. **Hub-and-spoke**：共享基础设施 + 独立策略插件

### ez-trading 的定位

取 Qlib（AI 原生 + Python 编排）和 NautilusTrader（编译语言核心 + 高保真回测）的交集，走 **C++/Python 混合架构**：

- **80% Python**：数据管线、策略定义、ML/AI、API、分析 —— agent 高效生成
- **20% C++**：因子计算引擎、回测核心、时序操作原语 —— 性能关键路径
- **nanobind 桥接**：编译快 4x，体积小 5x（pybind11 作者新作）

v1 先用纯 Python 实现全链路逻辑，接口设计预留 C++ 替换点。

---

## 技术架构

### 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    web (React 19)                    │
│         K 线图 + 回测面板 + 结果展示                  │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP/REST
┌──────────────────────┴──────────────────────────────┐
│                  ez.api (FastAPI)                     │
│              统一 API 层，单进程                       │
├──────────────────────────────────────────────────────┤
│  ez.data      │  ez.factor   │  ez.backtest          │
│  数据获取/存储 │  因子计算     │  回测引擎             │
│               │              │                       │
│  Provider ABC │  Factor ABC  │  Engine (向量化)       │
│  FMP 实现     │  技术指标     │  Portfolio 跟踪       │
│  DuckDB 存储  │              │  Metrics 计算         │
│               │              │                       │
│  ── v2: 多源 ─│─ v2: C++ ────│─ v2: C++ + 事件驱动 ──│
├──────────────────────────────────────────────────────┤
│  ez.strategy  │  ez.model (v3)  │  ez.risk (v4)      │
│  策略框架     │  ML 模型框架     │  风控模块           │
│  Strategy ABC │                 │                     │
│  示例策略     │  ── v3: AI ─────│── v4: 风控 ──────── │
└──────────────────────────────────────────────────────┘
         │                              │
    ┌────┴────┐                   ┌─────┴─────┐
    │ DuckDB  │                   │  C++ Core  │
    │ Parquet │                   │  (v2+)     │
    └─────────┘                   └───────────┘
```

### 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 数据库 | DuckDB + Parquet | 进程内 OLAP，零部署，Arrow 原生（未来零拷贝到 C++） |
| API 框架 | FastAPI | 异步，类型推导自动生成文档，代码极简 |
| 前端图表 | ECharts 5 | K 线/成交量/指标叠加成熟，中文生态好 |
| 前端框架 | React 19 + Vite 7 + TailwindCSS | 现代，agent 友好（样式内联，无上下文切换） |
| C++/Python 桥接 | nanobind（v2） | pybind11 继任者，性能更优 |
| 配置 | YAML + .env | 无 XML，agent 可读 |
| 架构模式 | 单进程 + 模块分包 | 拒绝微服务，减少 agent 上下文负担 |

### 目录结构

```
ez-trading/
├── CLAUDE.md                      # Agent 强制入口（每次会话必读）
├── pyproject.toml                 # 统一 Python 配置（uv/pip）
├── .env.example                   # 环境变量模板
│
├── ez/                            # Python 主包
│   ├── __init__.py
│   ├── config.py                  # 配置加载（YAML + .env）
│   │
│   ├── errors.py                  # [CORE] 统一错误类型
│   │
│   ├── data/                      # 数据层
│   │   ├── CLAUDE.md              # 模块文档（接口、依赖、状态）
│   │   ├── types.py               # [CORE] Bar, TradeRecord 数据模型
│   │   ├── provider.py            # [CORE] DataProvider ABC + DataProviderChain
│   │   ├── validator.py           # [CORE] DataValidator 验证规则
│   │   ├── store.py               # [CORE] DuckDB 存储引擎
│   │   └── providers/             # [EXTENSION] 数据源实现
│   │       ├── tushare_provider.py    # Tushare Pro（A 股主源）
│   │       ├── tencent_provider.py    # 腾讯财经 API（备用源）
│   │       └── fmp_provider.py        # FMP（美股主源）
│   │
│   ├── factor/                    # 因子层
│   │   ├── CLAUDE.md              # 模块文档
│   │   ├── base.py                # [CORE] Factor ABC（含 warmup_period）
│   │   ├── evaluator.py           # [CORE] FactorEvaluator + FactorAnalysis
│   │   └── builtin/               # [EXTENSION] 内置因子
│   │       └── technical.py       # MA, EMA, RSI, MACD, BOLL
│   │
│   ├── strategy/                  # 策略层
│   │   ├── CLAUDE.md              # 模块文档
│   │   ├── base.py                # [CORE] Strategy ABC（自动注册）
│   │   ├── loader.py              # [CORE] 策略目录扫描与加载
│   │   └── builtin/               # [EXTENSION] 内置策略
│   │       └── ma_cross.py        # 均线交叉策略
│   │
│   ├── backtest/                  # 回测层
│   │   ├── CLAUDE.md              # 模块文档
│   │   ├── engine.py              # [CORE] BacktestEngine ABC + 向量化实现
│   │   ├── portfolio.py           # [CORE] 组合状态跟踪
│   │   ├── metrics.py             # [CORE] 绩效指标计算
│   │   ├── walk_forward.py        # [CORE] Walk-Forward 滚动验证
│   │   └── significance.py        # [CORE] 统计显著性检验（Bootstrap + Monte Carlo）
│   │
│   └── api/                       # API 层
│       ├── CLAUDE.md              # 模块文档
│       ├── app.py                 # FastAPI 应用入口
│       └── routes/                # [EXTENSION] API 路由
│           ├── market_data.py     # /api/market-data
│           ├── backtest.py        # /api/backtest
│           └── factors.py         # /api/factors
│
├── web/                           # 前端
│   ├── CLAUDE.md                  # 模块文档
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/index.ts           # Axios 实例
│       ├── types/index.ts         # TypeScript 类型
│       ├── components/
│       │   ├── Navbar.tsx
│       │   ├── SearchBar.tsx
│       │   ├── StockTabs.tsx
│       │   ├── KlineChart.tsx     # K 线 + 成交量 + 指标叠加
│       │   ├── BacktestPanel.tsx  # 回测参数 + 结果展示
│       │   └── FactorPanel.tsx   # 因子分析面板（IC/分组收益图表）
│       ├── pages/
│       │   └── Dashboard.tsx      # 主看板页面
│       └── styles/
│           └── global.css         # 深色主题基础样式
│
├── strategies/                    # 用户自定义策略目录（agent 放入 .py 即自动注册）
│   └── .gitkeep
│
├── configs/
│   └── default.yaml               # 默认配置
│
├── scripts/
│   ├── start.sh                   # 一键启动
│   └── stop.sh                    # 一键停止
│
└── tests/
    ├── conftest.py                # 共享 fixtures: sample_data, mock_provider
    ├── fixtures/
    │   └── sample_kline.parquet   # 固定测试数据（确定性，零网络依赖）
    ├── mocks/
    │   └── mock_provider.py       # MockDataProvider（读本地 parquet）
    ├── test_smoke.py              # 冒烟测试（每次修改后必跑）
    ├── test_architecture.py       # 架构适应度测试（Core/Extension 边界）
    ├── test_data/
    │   └── test_provider_contract.py  # DataProvider 契约测试
    ├── test_factor/
    │   ├── test_factor_contract.py    # Factor 契约测试
    │   └── test_technical.py          # 技术指标计算正确性
    ├── test_strategy/
    │   └── test_strategy_contract.py  # Strategy 契约测试
    ├── test_backtest/
    │   ├── test_engine.py             # 引擎核心逻辑
    │   └── test_metrics.py            # 指标计算正确性
    └── test_integration/
        └── test_pipeline.py           # 全流程集成测试
```

---

## V1 功能范围（当前版本）

> 目标：跑通数据 -> 因子 -> 策略 -> 回测 -> 可视化的最小闭环

### 1. 数据层

**数据源架构（多源 + 故障转移）**

| 市场 | 主数据源 | 备用数据源 | 兜底数据源 |
|------|----------|------------|------------|
| A 股 | Tushare Pro（需注册 token，免费 120 积分层可用日 K） | 腾讯财经 API（无需注册，非官方） | AKShare（开源聚合库） |
| 美股 | FMP（免费 250 次/天） | 腾讯财经 API（`usAAPL` 格式） | — |
| 港股 | Tushare Pro（`hk_daily`） | 腾讯财经 API（`hk00700` 格式） | — |

**故障转移链（DataProviderChain）：**
```
1. 查询本地 DuckDB 缓存 → 有数据则直接返回
2. 调用主数据源 → 成功则存入 DuckDB 并返回
3. 主数据源失败（超时/限流/HTTP 错误）→ 调用备用数据源
4. 备用数据源失败 → 调用兜底数据源
5. 全部失败 → 返回缓存中的陈旧数据（如有）+ 警告，或报错
```
- 每个 Provider 独立配置超时时间（默认 10s）
- 指数退避重试（1s, 2s, 4s），单源最多重试 2 次
- 日志记录每次源切换，便于排查数据问题

**数据验证规则（每次入库前执行）：**
- OHLC 一致性：`low <= open <= high` 且 `low <= close <= high`
- 成交量非负：`volume >= 0`
- 无重复行：`(symbol, market, time)` 唯一
- 价格跳空检查：如果 open 相对前一日 close 偏离 > 20% 且非涨跌停，标记为可疑
- 成交量单位统一：Tushare 返回"手"（100 股），腾讯返回"股"，统一存储为"股"

**Tushare 注意事项：**
- 免费 120 积分：仅日 K 线（`daily`），50 次/分钟，8000 次/天
- 周 K/月 K 需 2000+ 积分（付费或社区贡献）
- 复权价通过 `pro_bar(adj='qfq')` 获取前复权，或 `adj_factor` API 获取复权因子
- 股票代码格式：`000001.SZ`（深圳），`600000.SH`（上海）

**腾讯 API 注意事项：**
- 非官方 API，无 SLA，可随时变更
- 端点：`https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`
- 响应列序非标准：`[date, open, CLOSE, high, low, volume]`（close 在第 2 位）
- 前复权参数：`qfqa`，后复权：`qfq`
- 建议限流 60 次/分钟以防被封
- 仅作为备用源，不作为主源

**复权策略（关键决策）**
- v1 采用数据源提供的前复权价格（Tushare `adj='qfq'` / 腾讯 `qfqa`）
- 存储字段区分 `close`（原始收盘价）和 `adj_close`（前复权收盘价）
- 回测引擎默认使用 `adj_close` 计算收益，避免拆分/分红导致的虚假波动
- v2 切换为 LEAN 方案：存储原始价格 + factor 文件，运行时按需复权

**交易日历**
- 使用 `exchange_calendars` 库处理交易日历对齐（SSE/SZSE/HKEX/NYSE）
- 仅存储交易日数据行（非交易日无行），日历文件定义有效日期
- A 股时间存为 Asia/Shanghai，美股存为 America/New_York

**数据存储**
- DuckDB 本地文件数据库（`data/ez_trading.db`）
- 表结构：`kline_{period}(time, symbol, market, open, high, low, close, adj_close, volume)`
- Parquet 导出能力（为未来 C++ Arrow 集成预留）
- **DuckDB 已知限制**：单写者并发、无原生时序索引、无复制。v1 单进程架构下可接受，v2+ 视需求评估 ArcticDB

**数据模型**
```python
@dataclass
class Bar:
    time: datetime
    symbol: str         # 标准化代码：000001.SZ / AAPL
    market: str         # cn_stock / us_stock / hk_stock
    open: float
    high: float
    low: float
    close: float        # 原始收盘价
    adj_close: float    # 前复权收盘价（v1 回测用此字段）
    volume: int         # 统一为"股"，非"手"
```

### 2. 因子层

**技术指标（v1 内置）**
- MA (Simple Moving Average) — 5, 10, 20, 60 日
- EMA (Exponential Moving Average)
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- BOLL (Bollinger Bands)

**因子接口**
```python
class Factor(ABC):
    name: str
    params: dict

    @property
    @abstractmethod
    def warmup_period(self) -> int:
        """因子需要的最小历史数据条数（如 60 日 MA 返回 60）。
        回测引擎用此值决定向前加载多少额外数据做预热。"""
        ...

    @abstractmethod
    def compute(self, data: pd.DataFrame) -> pd.DataFrame:
        """输入 OHLCV DataFrame，返回附加因子列的 DataFrame。
        前 warmup_period 行的因子值可能为 NaN，引擎会自动裁剪。"""
        ...
```

**因子评估框架（IC 分析）**

因子的价值不在于计算出来，而在于能否预测未来收益。v1 提供完整的因子评估工具：

| 指标 | 计算方式 | 含义 |
|------|----------|------|
| **IC** (Pearson) | `corr(factor_values, N日forward_returns)` | 因子值与未来收益的线性相关性 |
| **Rank IC** (Spearman) | `spearmanr(factor_ranks, return_ranks)` | 因子排名与收益排名的单调相关性（首选，对异常值鲁棒） |
| **ICIR** | `IC_mean / IC_std` | IC 的稳定性，> 0.5 为可投资信号 |
| **IC 衰减** | 分别计算 1d/5d/10d/20d 的 IC | 因子的信息时效，衰减慢 = 更可持续 |
| **因子换手率** | 因子排名的自相关系数 | 换手率高 = 交易成本高，侵蚀 alpha |
| **分组收益** | 按因子值分 5 组（quintile），计算各组平均收益 | 单调递增/递减 = 因子有效 |

**因子评估接口**
```python
@dataclass
class FactorAnalysis:
    ic_series: pd.Series          # 每日 IC 时间序列
    rank_ic_series: pd.Series     # 每日 Rank IC 时间序列
    ic_mean: float                # IC 均值
    rank_ic_mean: float           # Rank IC 均值
    icir: float                   # ICIR
    rank_icir: float              # Rank ICIR
    ic_decay: dict[int, float]    # {1: 0.05, 5: 0.04, 10: 0.03, 20: 0.02}
    turnover: float               # 因子换手率
    quintile_returns: pd.DataFrame  # 5 组平均收益

class FactorEvaluator:
    def evaluate(self, factor_values: pd.Series,
                 forward_returns: pd.Series,
                 periods: list[int] = [1, 5, 10, 20]) -> FactorAnalysis:
        """评估单个因子的预测能力"""
        ...
```

**前端因子分析可视化（v1）：**
- IC 时间序列折线图（含均值线）
- IC 分布直方图
- 分组收益柱状图（quintile returns）
- IC 衰减曲线图

### 3. 策略层

**策略接口（自动注册）**

使用 `__init_subclass__` 自动注册机制：任何继承 `Strategy` 的非抽象类自动被注册，agent 只需创建 .py 文件即可，无需修改核心代码。

```python
class Strategy(ABC):
    """策略基类。所有子类自动注册到 _registry 字典中。"""
    _registry: dict[str, type] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not inspect.isabstract(cls):
            cls._registry[cls.__name__] = cls

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        """返回策略可配置参数的 schema，供前端渲染参数表单。
        格式：{"param_name": {"type": "int", "default": 20, "min": 5, "max": 200, "label": "短期均线"}}"""
        return {}

    @abstractmethod
    def required_factors(self) -> list[Factor]:
        """声明策略依赖的因子列表，引擎自动计算并注入 data 中"""
        ...

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """输入含因子列的 DataFrame，返回目标仓位权重序列。
        值域：0.0 = 空仓, 1.0 = 满仓, 0.5 = 半仓。
        信号基于当前 bar 收盘价计算，引擎在下一根 bar 开盘价执行。"""
        ...
```

**自定义策略目录**
- 内置策略：`ez/strategy/builtin/`（ma_cross.py, rsi_reversal.py 等）
- 用户策略：`strategies/`（项目根目录，agent 直接放入 .py 文件即可）
- 启动时自动扫描两个目录（`pkgutil.iter_modules`），导入所有模块触发注册
- API 端点 `GET /api/backtest/strategies` 返回所有已注册策略的名称 + 参数 schema

**执行时序规则（防止前视偏差）**
- 信号在 bar[i] 收盘价产生 → 在 bar[i+1] 开盘价执行（`signal.shift(1)`）
- 回测引擎在内部自动做 shift，策略代码无需关心
- 这是 LEAN 和 Zipline 的标准做法

**示例策略：均线交叉**
```python
class MACrossStrategy(Strategy):
    def __init__(self, short_period: int = 5, long_period: int = 20):
        self.short_period = short_period
        self.long_period = long_period

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "short_period": {"type": "int", "default": 5, "min": 2, "max": 60, "label": "短期均线"},
            "long_period": {"type": "int", "default": 20, "min": 5, "max": 250, "label": "长期均线"},
        }

    def required_factors(self) -> list[Factor]:
        return [MA(period=self.short_period), MA(period=self.long_period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        short_ma = data[f"ma_{self.short_period}"]
        long_ma = data[f"ma_{self.long_period}"]
        return (short_ma > long_ma).astype(float)  # 1.0 满仓 / 0.0 空仓
```

### 4. 回测层

**向量化回测引擎**
- 输入：历史数据 + 策略 + 初始资金 + 手续费率
- 流程：
  1. 根据策略声明的因子计算所需预热期（取所有因子 `warmup_period` 最大值）
  2. 加载数据（含预热期额外数据）
  3. 计算因子 → 生成信号（目标仓位权重）
  4. 将信号 shift(1)：bar[i] 信号在 bar[i+1] 执行（防止前视偏差）
  5. 裁剪预热期数据行
  6. 按权重变化模拟交易（仅在权重变化时产生交易）
  7. 计算绩效指标 + 基准对比
- 使用 `adj_close` 计算收益，`open` 作为执行价格
- 手续费率可配置（默认万三），支持最低手续费
- 单标的，支持灵活仓位（0.0-1.0 权重）

**前视偏差防护（架构级）**
- 信号 shift(1) 由引擎强制执行，策略无法绕过
- 因子预热期自动裁剪，预热期内不产生交易
- 数据层仅返回交易日数据（通过 exchange_calendars 过滤）

**绩效指标**
- 总收益率 / 年化收益率
- Sharpe Ratio / Sortino Ratio（无风险利率可配）
- 最大回撤 / 最大回撤持续期
- 胜率 / 盈亏比 / 利润因子
- 交易次数 / 平均持仓天数
- 基准对比（Buy & Hold 同期收益）
- 年化波动率

**Walk-Forward 验证（核心差异化功能 #1）**

> 行业第二大痛点：过拟合太容易。没有任何开源平台内置强制 walk-forward 验证。ez-trading 将此作为 v1 核心功能。

```
数据: |----训练----|--验证--|--测试--|
                 |----训练----|--验证--|--测试--|
                              |----训练----|--验证--|--测试--|
      ←────────── 滚动窗口 ──────────→
```

- 将历史数据切分为多个滚动窗口（训练 + 样本外测试）
- 每个窗口：在训练期拟合策略参数 → 在测试期验证
- 汇总所有样本外测试期的绩效 = 真实的策略表现
- 参数：`train_ratio`（默认 0.7）、`n_splits`（默认 5）、`min_train_size`

```python
class WalkForwardValidator:
    def validate(self, data: pd.DataFrame, strategy: Strategy,
                 n_splits: int = 5, train_ratio: float = 0.7) -> WalkForwardResult:
        """滚动窗口验证，返回每个窗口的回测结果 + 汇总指标"""
        ...

@dataclass
class WalkForwardResult:
    splits: list[BacktestResult]       # 每个窗口的回测结果
    oos_equity_curve: pd.Series        # 拼接所有样本外权益曲线
    oos_metrics: dict[str, float]      # 样本外汇总指标
    is_vs_oos_degradation: float       # 样本内 vs 样本外 Sharpe 衰减比
    overfitting_score: float           # 过拟合评分（衰减比越高越过拟合）
```

**统计显著性检验（核心差异化功能 #2）**

> 回测 Sharpe > 0 不代表策略有效。可能只是随机波动。ez-trading 用统计检验告诉 agent（和人类）结果是否可信。

- **Bootstrap 置信区间**：对日收益率重采样 1000 次，计算 Sharpe 的 95% 置信区间
- **Monte Carlo 排列检验**：随机打乱信号 1000 次，统计有多少次 Sharpe > 真实值 → p-value
- 如果 p-value > 0.05，回测结果标记为"统计不显著"，前端显示警告

```python
@dataclass
class SignificanceTest:
    sharpe_ci_lower: float       # Sharpe 95% 置信区间下界
    sharpe_ci_upper: float       # Sharpe 95% 置信区间上界
    monte_carlo_p_value: float   # Monte Carlo 排列检验 p-value
    is_significant: bool         # p_value < 0.05
```

**回测结果数据**
```python
@dataclass
class TradeRecord:
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    weight: float             # 仓位权重
    pnl: float                # 盈亏金额
    pnl_pct: float            # 盈亏百分比
    commission: float         # 手续费

@dataclass
class BacktestResult:
    equity_curve: pd.Series       # 权益曲线
    benchmark_curve: pd.Series    # 基准权益曲线（Buy & Hold）
    trades: list[TradeRecord]     # 交易记录
    metrics: dict[str, float]     # 绩效指标
    signals: pd.Series            # 目标仓位权重序列
    daily_returns: pd.Series      # 日收益率序列
    significance: SignificanceTest # 统计显著性检验结果
```

### 5. API 层

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/market-data/kline` | GET | 获取 K 线数据（symbol, market, period, startDate, endDate） |
| `/api/market-data/symbols` | GET | 搜索股票代码 |
| `/api/backtest/run` | POST | 单次回测（body: {symbol, market, period, strategy_name, strategy_params, start_date, end_date, initial_capital, commission_rate}），返回含统计显著性 |
| `/api/backtest/walk-forward` | POST | Walk-Forward 验证（额外参数: n_splits, train_ratio），返回各窗口结果 + 过拟合评分 |
| `/api/backtest/strategies` | GET | 可用策略列表（含参数 schema，供前端渲染表单） |
| `/api/factors` | GET | 可用因子列表 |
| `/api/factors/evaluate` | POST | 因子评估（body: {symbol, factor_name, factor_params, start_date, end_date, periods}），返回 IC/ICIR/分组收益 |

### 6. 前端看板

**深色主题配色**（延续 jzhu-trading 风格）
- 背景: #0d1117 / 面板: #161b22 / 边框: #30363d
- 涨: #ef4444 (红) / 跌: #22c55e (绿) — 中国习惯
- 强调: #2563eb (蓝)

**K 线看板**
- 搜索栏：股票代码 + 市场 + 周期 + 日期范围 + 查询按钮
- 股票标签栏：多标签切换，可关闭
- K 线图（ECharts）：
  - 主图：K 线蜡烛图 + 可叠加 MA/BOLL 等指标线
  - 副图：成交量柱（涨红跌绿）
  - dataZoom：底部滑动条 + 鼠标滚轮缩放
  - tooltip：日期、开/高/低/收、成交量

**回测面板**
- 策略选择（下拉，动态加载已注册策略）+ 参数配置（根据 schema 自动渲染表单）
- 运行模式切换：单次回测 / Walk-Forward 验证
- 结果展示：
  - 权益曲线（折线图，叠加基准线 Buy & Hold）
  - 绩效指标卡片（Sharpe, Sortino, 回撤, 胜率等）
  - **统计显著性徽章**：显著（绿）/ 不显著（红）+ p-value + 95% 置信区间
  - **Walk-Forward 结果**：样本内 vs 样本外 Sharpe 对比、过拟合评分、各窗口权益曲线
  - 交易记录表格

**因子分析面板**
- 因子选择（下拉）+ 参数配置
- 分析按钮
- 结果展示：
  - IC 时间序列折线图（含均值线、±1 标准差带）
  - IC 分布直方图
  - 分组收益柱状图（quintile returns，检验单调性）
  - IC 衰减曲线图（1d/5d/10d/20d）
  - ICIR 等核心指标卡片

---

## 版本路线图

### V2 — C++ 计算核心

> 目标：性能飞跃，引入 C++ 加速关键路径

**C++ 引擎（nanobind 桥接 Python）**
- 因子计算引擎：CRTP 静态多态（6x 快于虚函数），Eigen 矩阵运算
- 向量化时序操作：滚动窗口、截面运算、SIMD 加速
- 事件驱动回测引擎：tick 级仿真，部分成交、滑点模拟
- 订单撮合模拟器：限价单、市价单、止损单

**数据层升级**
- 原始价格 + factor 文件方案（参考 LEAN）：存储未复权价格，运行时按需复权
- 支持多种复权模式：前复权、后复权、不复权
- 企业行为处理：拆分、分红、退市、更名
- 数据质量检查：缺失值、异常值、跳空检测
- 评估 ArcticDB 替代 DuckDB（版本化、增量更新）

**增强功能**
- 多标的回测 + 截面策略支持
- 多策略并行回测
- 更多数据源：Tiingo, Polygon, Binance
- 性能对比面板：C++ vs Python 引擎切换

### V3 — AI 引擎

> 目标：AI 原生的因子发现和策略生成

**因子挖掘**
- LLM 驱动因子生成（参考 AlphaAgent / RD-Agent 架构）
- 遗传编程因子搜索
- 因子评估框架：IC, IR, 换手率, 衰减分析

**ML 模型框架**
- 模型基类：train, predict, evaluate
- 内置模型：LightGBM, XGBoost, LSTM, Transformer
- Feature Store：因子版本管理、训练/推理一致性
- Walk-Forward 训练：滚动窗口避免过拟合

**LLM 集成**
- 新闻/财报情绪分析 -> 情绪因子
- 自然语言策略描述 -> 策略代码生成
- Agent 辅助回测分析和报告生成

**MLOps**
- 实验追踪（模型版本、回测结果绑定）
- Concept Drift 检测与自动重训
- 模型 A/B 测试（Paper Trading 对比）

### V4 — 组合与风控

> 目标：从单标的到多资产组合管理

**组合优化**
- Mean-Variance 优化
- Risk Parity
- Black-Litterman
- 约束：行业限制、个股上限、换手率限制

**风险管理**
- VaR / CVaR 计算
- 压力测试（历史情景 + 假设情景）
- 实时风险监控面板
- 止损/止盈规则引擎

**扩展**
- 多市场支持（A 股、港股、加密货币）
- 多时间框架策略
- 另类数据管线（社交媒体、卫星图像、链上数据）

### V5 — 实盘交易

> 目标：从回测到实盘的完整闭环

**交易连接**
- Broker API 适配器（Interactive Brokers, Alpaca, Binance）
- 统一订单管理系统 (OMS)
- FIX 协议支持

**执行优化**
- TWAP / VWAP 算法
- 智能订单路由
- 交易成本分析 (TCA)

**运维**
- 实时监控仪表盘（Grafana 集成）
- 异常报警（持仓偏离、策略失效、系统故障）
- 审计日志

---

## V1 技术栈汇总

| 层级 | 技术 | 版本 |
|------|------|------|
| 语言 (后端) | Python | 3.12+ |
| 语言 (前端) | TypeScript | 5.9 |
| API 框架 | FastAPI | latest |
| 数据库 | DuckDB | latest |
| HTTP 客户端 | httpx | latest |
| A 股数据 | tushare | latest |
| 数据聚合 | akshare | latest |
| 交易日历 | exchange_calendars | latest |
| 数据处理 | pandas + numpy | latest |
| 前端框架 | React | 19 |
| 构建工具 | Vite | 7 |
| CSS 框架 | TailwindCSS | 4 |
| 图表库 | ECharts | 5 |
| HTTP 客户端 (前端) | Axios | latest |
| 包管理 (Python) | uv | latest |
| 包管理 (前端) | npm | latest |

---

## V1 不做的事（YAGNI）

- 事件驱动回测（向量化足够）
- 多标的同时回测（单标的先跑通）
- 实时数据推送 / WebSocket（HTTP 轮询够用）
- 用户认证 / 多用户（单用户系统）
- C++ 编译（纯 Python，接口预留）
- Docker / K8s 部署（本地运行）
- Paper Trading（v3）
- 组合优化（v4）
- 原始价格 + factor 文件复权（v2，v1 使用数据源提供的前复权价格）
- 做空交易（v1 仅支持 0.0-1.0 多头仓位）
- 分笔/Tick 级别数据（v1 仅日 K/周 K/月 K）

## V1 已知限制

| 限制 | 原因 | 解决版本 |
|------|------|----------|
| 使用数据源前复权价格，非自主计算 | v1 精简原则，FMP 提供 adj_close | V2（raw + factor 文件） |
| DuckDB 单写者并发 | 单进程架构下无影响 | V2（评估 ArcticDB） |
| 无拆分/分红事件通知 | 依赖前复权价格已隐含处理 | V2（企业行为事件系统） |
| 无存活偏差修正 | FMP 免费版不含退市股票 | V2（专业数据源） |
| 仅支持多头仓位 | 简化 v1 回测逻辑 | V2（支持做空） |

---

## 架构治理（V1 最关键的交付物）

> **V1 的核心产出不是功能，而是架构。功能可以迭代，架构定型后改动成本指数级增长。**

---

### 一、核心不可变架构

将代码严格划分为 **Core（核心）** 和 **Extension（扩展）** 两类。Core 在 v1 定型后**未经充分论证不得修改**，所有功能迭代通过 Extension 实现。

**Core = 接口定义 + 数据模型 + 引擎骨架。Extension = 具体实现。**

```
依赖方向：Extension → Core（单向，不可反转）
核心承诺：Core 文件的公开接口不变，Extension 可自由增删
```

**Core 文件清单（不可变）：**

| 文件 | 职责 | 修改条件 |
|------|------|----------|
| `ez/data/types.py` | Bar, TradeRecord, BacktestResult, FactorAnalysis 数据模型 | 仅允许追加字段（带默认值），不允许删除或改名 |
| `ez/data/provider.py` | DataProvider ABC + DataProviderChain | 接口签名冻结 |
| `ez/data/validator.py` | DataValidator 验证规则引擎 | 仅允许追加规则 |
| `ez/data/store.py` | DataStore ABC | 接口签名冻结 |
| `ez/factor/base.py` | Factor ABC（含 warmup_period） | 接口签名冻结 |
| `ez/factor/evaluator.py` | FactorEvaluator + FactorAnalysis | 仅允许追加指标 |
| `ez/strategy/base.py` | Strategy ABC + 自动注册 + 参数 schema | 接口签名冻结 |
| `ez/backtest/engine.py` | BacktestEngine ABC + 引擎核心循环 | 循环步骤不变，仅允许追加 hook 点 |
| `ez/backtest/metrics.py` | MetricsCalculator | 仅允许追加指标 |
| `ez/backtest/walk_forward.py` | WalkForwardValidator + WalkForwardResult | 接口签名冻结 |
| `ez/backtest/significance.py` | SignificanceTest + Bootstrap/Monte Carlo | 接口签名冻结 |
| `ez/config.py` | 配置加载和验证 | 仅允许追加配置项 |

**Extension 文件（自由增删）：**

| 类型 | 目录 | 添加方式 |
|------|------|----------|
| 数据源 | `ez/data/providers/` | 新建 `xxx_provider.py`，继承 DataProvider |
| 因子 | `ez/factor/builtin/` | 新建 `xxx.py`，继承 Factor |
| 策略（内置） | `ez/strategy/builtin/` | 新建 `xxx.py`，继承 Strategy |
| 策略（用户） | `strategies/` | 新建 `xxx.py`，继承 Strategy |
| API 路由 | `ez/api/routes/` | 新建 `xxx.py`，注册到 FastAPI router |
| 前端组件 | `web/src/components/` | 新建 `Xxx.tsx` |
| 前端页面 | `web/src/pages/` | 新建 `XxxPage.tsx` |

**Core 修改审批流程：**
1. 在 `docs/core-changes/` 创建变更提案文档：为什么要改、影响范围、向后兼容方案
2. 评估是否可以通过追加（而非修改）解决
3. 如果必须修改：同步更新所有依赖的 Extension + 更新所有 CLAUDE.md + 更新所有 contract tests

**依赖边界强制（架构适应度测试）：**
```python
# tests/test_architecture.py — 每次 CI 运行
def test_extension_does_not_import_extension():
    """Extension 之间不允许互相导入，只能导入 Core"""
    ...

def test_core_does_not_import_extension():
    """Core 不允许导入任何 Extension"""
    ...
```

---

### 二、Agent 开发工作流

本系统为 agent 写代码而设计。agent 添加功能的标准流程：

**场景：agent 被要求"添加一个 RSI 反转策略"**

```
1. 读取 CLAUDE.md → 了解项目结构
2. 读取 ez/strategy/CLAUDE.md → 了解 Strategy ABC 和注册机制
3. 读取 ez/strategy/builtin/ma_cross.py → 作为参考模板
4. 创建 strategies/rsi_reversal.py → 继承 Strategy，实现接口
5. 运行 pytest tests/test_strategy/ → contract tests 自动验证新策略
6. 更新 strategies/ 目录的文档（如有 CLAUDE.md）
7. 提交
```

**关键设计：agent 全程不接触任何 Core 文件。**

**每种 Extension 类型的标准三件套：**
1. **ABC 接口**（Core）— 定义契约
2. **参考实现**（Extension）— agent 的模板
3. **Contract Test**（Tests）— 自动验证任何新实现是否符合契约

| Extension 类型 | ABC | 参考实现 | Contract Test |
|----------------|-----|----------|---------------|
| 数据源 | `DataProvider` | `tushare_provider.py` | `tests/test_data/test_provider_contract.py` |
| 因子 | `Factor` | `technical.py` (MA) | `tests/test_factor/test_factor_contract.py` |
| 策略 | `Strategy` | `builtin/ma_cross.py` | `tests/test_strategy/test_strategy_contract.py` |

---

### 三、测试体系

> **测试是 agent 的安全网。没有测试，agent 每次修改都是盲飞。**

**测试金字塔：**

```
                  ┌─────────┐
                  │  Smoke  │  ← 每次修改后运行（< 5s）
                 ┌┴─────────┴┐
                 │ Contract  │  ← 每次新增 Extension 时运行（< 10s）
                ┌┴───────────┴┐
                │ Integration │  ← 每个模块完成后运行（< 30s）
               ┌┴─────────────┴┐
               │     Unit      │  ← 每个函数完成后运行（< 1s each）
               └───────────────┘
```

**1. Smoke Tests（冒烟测试）— 全局健康检查**

```python
# tests/test_smoke.py — 任何修改后必须全部通过
def test_all_imports():
    """所有模块可正常导入，无语法错误"""
    import ez.data, ez.factor, ez.strategy, ez.backtest, ez.api

def test_all_strategies_registered():
    """所有策略正确注册"""
    assert len(Strategy._registry) > 0

def test_all_factors_registered():
    """所有因子可实例化"""
    assert MA(period=5).warmup_period == 5

def test_api_starts():
    """API 可正常启动"""
    from ez.api.app import app
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200

def test_simple_backtest_completes():
    """一个简单回测可以跑完，不崩溃"""
    result = run_backtest(MockData, MACrossStrategy(), initial_capital=100000)
    assert result.equity_curve is not None
    assert len(result.trades) >= 0
```

**2. Contract Tests（契约测试）— 自动验证所有 Extension**

```python
# tests/test_strategy/test_strategy_contract.py
import pytest
from ez.strategy.base import Strategy

def discover_strategies():
    """自动发现所有已注册的 Strategy 子类"""
    # 触发 auto-discovery
    import ez.strategy.loader
    return list(Strategy._registry.values())

@pytest.fixture(params=discover_strategies(), ids=lambda s: s.__name__)
def strategy_cls(request):
    return request.param

class TestStrategyContract:
    def test_has_required_factors(self, strategy_cls):
        """必须声明依赖因子"""
        instance = strategy_cls(**self._default_params(strategy_cls))
        factors = instance.required_factors()
        assert isinstance(factors, list)

    def test_generate_signals_returns_series(self, strategy_cls, sample_data):
        """generate_signals 必须返回 pd.Series"""
        instance = strategy_cls(**self._default_params(strategy_cls))
        signals = instance.generate_signals(sample_data)
        assert isinstance(signals, pd.Series)
        assert len(signals) == len(sample_data)

    def test_signals_in_valid_range(self, strategy_cls, sample_data):
        """信号值必须在 [0.0, 1.0] 范围内"""
        instance = strategy_cls(**self._default_params(strategy_cls))
        signals = instance.generate_signals(sample_data)
        assert signals.between(0.0, 1.0).all()

    def test_parameters_schema_valid(self, strategy_cls):
        """参数 schema 格式正确"""
        schema = strategy_cls.get_parameters_schema()
        for name, spec in schema.items():
            assert "type" in spec
            assert "default" in spec

    def _default_params(self, cls):
        return {k: v["default"] for k, v in cls.get_parameters_schema().items()}
```

同理为 DataProvider 和 Factor 编写 contract tests。**新增 Extension 无需编写新测试——contract tests 自动覆盖。**

**3. Integration Tests（集成测试）— 数据流贯通**

```python
# tests/test_integration/test_pipeline.py
def test_full_pipeline_with_mock_data():
    """数据 → 因子 → 策略 → 回测 → 指标 全流程"""
    data = MockDataProvider().get_kline("TEST", "cn_stock", "daily", start, end)
    assert len(data) > 0

    strategy = MACrossStrategy(short_period=5, long_period=20)
    result = BacktestEngine().run(data, strategy, initial_capital=100000)

    assert result.metrics["sharpe_ratio"] is not None
    assert result.metrics["max_drawdown"] <= 0  # 回撤为负数
    assert len(result.equity_curve) == len(data)
    assert result.equity_curve.iloc[-1] > 0     # 权益不为负
```

**4. Unit Tests（单元测试）— 核心计算正确性**

```python
# tests/test_backtest/test_metrics.py
def test_sharpe_ratio_known_values():
    """Sharpe ratio 对已知数据的计算结果正确"""
    returns = pd.Series([0.01, 0.02, -0.01, 0.03, -0.005])
    sharpe = calculate_sharpe(returns, risk_free_rate=0.0)
    assert abs(sharpe - expected_value) < 1e-6

# tests/test_factor/test_technical.py
def test_ma_computation():
    """MA(5) 对 [1,2,3,4,5,6,7] 的计算结果正确"""
    data = pd.DataFrame({"adj_close": [1,2,3,4,5,6,7]})
    result = MA(period=5).compute(data)
    assert result["ma_5"].iloc[4] == 3.0  # (1+2+3+4+5)/5
    assert pd.isna(result["ma_5"].iloc[3])  # 预热期为 NaN
```

**5. 架构适应度测试 — 守护核心边界**

```python
# tests/test_architecture.py
import ast, pathlib

CORE_FILES = { ... }  # 核心文件路径集合
EXTENSION_DIRS = { ... }  # 扩展目录集合

def test_core_does_not_import_extension():
    """Core 文件不得导入 Extension 模块"""
    for core_file in CORE_FILES:
        tree = ast.parse(core_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = node.module or node.names[0].name
                assert not any(ext in module for ext in EXTENSION_DIRS)

def test_no_circular_dependencies():
    """模块间无循环依赖"""
    ...
```

**测试基础设施：**

| 文件 | 职责 |
|------|------|
| `tests/conftest.py` | 共享 fixtures：sample_data, mock_provider |
| `tests/fixtures/sample_kline.parquet` | 固定测试数据集（确定性，不依赖外部 API） |
| `tests/mocks/mock_provider.py` | MockDataProvider（读取本地 parquet，零网络调用） |

**测试运行命令（写入 CLAUDE.md）：**
```bash
pytest tests/test_smoke.py          # 冒烟测试（每次修改后）
pytest tests/test_strategy/         # 策略契约测试
pytest tests/test_factor/           # 因子契约测试
pytest tests/                       # 全量测试
```

---

### 四、文档驱动开发

> **每次新对话可能是新 agent。文档 = 跨会话记忆。代码变更但文档未更新 = 不完整提交。**

**CLAUDE.md 分层体系（根文件 ≤ 150 行，模块文件 ≤ 80 行）：**

```
ez-trading/
├── CLAUDE.md                      # 根入口（每次会话自动加载）
├── ez/
│   ├── data/CLAUDE.md             # 数据层
│   ├── factor/CLAUDE.md           # 因子层
│   ├── strategy/CLAUDE.md         # 策略层
│   ├── backtest/CLAUDE.md         # 回测层
│   └── api/CLAUDE.md              # API 层
└── web/CLAUDE.md                  # 前端
```

**根 CLAUDE.md 必须包含（≤ 150 行）：**
1. 项目一句话描述 + 技术栈
2. 模块地图（每个模块一行 + CLAUDE.md 路径）
3. 依赖关系图（`data → factor → strategy → backtest → api → web`）
4. Core 文件清单（标注**不可修改**）
5. Extension 添加指南（每种类型：放哪个目录、继承什么基类、运行什么测试）
6. 快速命令（启动、停止、测试）
7. 开发状态表格（模块 × 状态 × 最后更新）

**模块 CLAUDE.md 必须包含（≤ 80 行）：**
1. 职责（做什么、不做什么）
2. 公开接口（类名 + 方法签名，无实现细节）
3. 文件清单（文件名 + 职责 + Core/Extension 标记）
4. 上游/下游依赖
5. 添加新 Extension 的步骤（3-5 步）
6. 当前状态（已实现/未实现/已知问题）

**开发工作流（每次变更强制执行）：**
```
开始 → 读 CLAUDE.md → 读目标模块 CLAUDE.md → 编码 → 运行测试 → 更新 CLAUDE.md → 同一 commit 提交
```

---

### 五、错误处理策略

**统一错误类型（Core 定义）：**

```python
# ez/errors.py (Core)
class EzTradingError(Exception): ...
class DataError(EzTradingError): ...         # 数据获取/验证失败
class ProviderError(DataError): ...          # 数据源连接/限流
class ValidationError(DataError): ...        # 数据验证不通过
class FactorError(EzTradingError): ...       # 因子计算失败
class BacktestError(EzTradingError): ...     # 回测引擎错误
class ConfigError(EzTradingError): ...       # 配置错误
```

- API 层捕获所有 `EzTradingError` 并转换为 HTTP 响应
- 错误消息必须包含上下文（哪个数据源、哪个股票、什么时间段）
- agent 读到错误消息应能判断问题原因和修复方向

---

### 六、代码规范

1. **Core 不可变**：Core 文件的公开接口冻结，功能通过 Extension 扩展
2. **Add, Never Modify**：新功能 = 新文件，不改已有文件（Core 尤其如此）
3. **文件 ≤ 300 行**：超过即拆分
4. **类型标注**：Python 全量 type hints，TypeScript strict mode
5. **显式 > 隐式**：无全局状态，依赖注入，所有 import 显式
6. **扁平 ≤ 3 层**：最多 3 层目录嵌套
7. **配置集中**：YAML + .env，不散落在代码中
8. **文档同步**：代码和文档在同一 commit
9. **测试覆盖**：新 Extension 必须通过 contract test，Core 修改必须通过全量测试
10. **命名规范**：Python PEP8，TypeScript camelCase，文件 snake_case
