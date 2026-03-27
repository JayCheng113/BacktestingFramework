# ez-trading 设计规格书

## 项目愿景

构建一个现代化量化回测交易系统，核心差异化：C++ 计算引擎 + AI 大规模接入。最终形态覆盖数据、因子、策略、模型、回测、交易全链路。全程 vibe coding 开发，代码结构优先服务 AI agent 的理解和操作能力。

## 核心原则

| 原则 | 含义 | 设计约束 |
|------|------|----------|
| **高效** | 计算高效 + 开发高效 | C++ 热路径，Python 编排；无冗余抽象 |
| **精简** | 最少代码量达成目标 | 单进程架构，拒绝微服务；无 XML/样板代码 |
| **专业** | 金融级严谨性 | 正确的回测指标，无前视偏差，专业级 K 线图 |
| **现代化** | 技术栈领先 | C++20/23, Python 3.12+, React 19, DuckDB |
| **Agent 友好** | AI agent 可高效导航和修改 | 扁平结构，小文件，显式 > 隐式，CLAUDE.md 导航 |

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
├── CLAUDE.md                      # Agent 导航文档
├── pyproject.toml                 # 统一 Python 配置（uv/pip）
├── .env.example                   # 环境变量模板
│
├── ez/                            # Python 主包
│   ├── __init__.py
│   ├── config.py                  # 配置加载（YAML + .env）
│   │
│   ├── data/                      # 数据层
│   │   ├── types.py               # Bar, TradeRecord 数据模型
│   │   ├── provider.py            # DataProvider 抽象基类
│   │   ├── fmp_provider.py        # FMP API 实现
│   │   └── store.py               # DuckDB 存储引擎
│   │
│   ├── factor/                    # 因子层
│   │   ├── base.py                # Factor 抽象基类
│   │   └── technical.py           # 技术指标：MA, EMA, RSI, MACD, BOLL
│   │
│   ├── strategy/                  # 策略层
│   │   ├── base.py                # Strategy 抽象基类
│   │   └── ma_cross.py            # 示例：均线交叉策略
│   │
│   ├── backtest/                  # 回测层
│   │   ├── engine.py              # 向量化回测引擎
│   │   ├── portfolio.py           # 组合状态跟踪
│   │   └── metrics.py             # 绩效指标计算
│   │
│   └── api/                       # API 层
│       ├── app.py                 # FastAPI 应用入口
│       ├── market_data.py         # /api/market-data 路由
│       └── backtest_routes.py     # /api/backtest 路由
│
├── web/                           # 前端
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
│       │   └── BacktestPanel.tsx  # 回测参数 + 结果展示
│       ├── pages/
│       │   └── Dashboard.tsx      # 主看板页面
│       └── styles/
│           └── global.css         # 深色主题基础样式
│
├── configs/
│   └── default.yaml               # 默认配置
│
├── scripts/
│   ├── start.sh                   # 一键启动
│   └── stop.sh                    # 一键停止
│
└── tests/
    ├── conftest.py
    ├── test_data/
    ├── test_factor/
    └── test_backtest/
```

---

## V1 功能范围（当前版本）

> 目标：跑通数据 -> 因子 -> 策略 -> 回测 -> 可视化的最小闭环

### 1. 数据层

**数据获取**
- FMP (Financial Modeling Prep) K 线数据（美股日 K/周 K/月 K）
- DataProvider 抽象基类，后续可接入 Tiingo, Polygon, Binance 等
- 查库优先：本地有数据直接返回，无则调 API -> 存库 -> 返回

**复权策略（关键决策）**
- v1 采用 FMP 提供的前复权价格（`adjClose`），简单直接
- 存储字段区分 `close`（原始收盘价）和 `adj_close`（前复权收盘价）
- 回测引擎默认使用 `adj_close` 计算收益，避免拆分/分红导致的虚假波动
- v2 切换为 LEAN 方案：存储原始价格 + factor 文件，运行时按需复权

**交易日历**
- 使用 `exchange_calendars` 库处理交易日历对齐
- 仅存储交易日数据行（非交易日无行），日历文件定义有效日期
- 跨时区统一存为 UTC，显示层转为交易所本地时间

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
    symbol: str
    market: str
    open: float
    high: float
    low: float
    close: float        # 原始收盘价
    adj_close: float    # 前复权收盘价（v1 回测用此字段）
    volume: int
```

### 2. 因子层

**技术指标（v1）**
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

### 3. 策略层

**策略接口**
```python
class Strategy(ABC):
    name: str

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

**执行时序规则（防止前视偏差）**
- 信号在 bar[i] 收盘价产生 → 在 bar[i+1] 开盘价执行（`signal.shift(1)`）
- 回测引擎在内部自动做 shift，策略代码无需关心
- 这是 LEAN 和 Zipline 的标准做法

**示例策略：均线交叉**
- 短期 MA 上穿长期 MA -> 目标仓位 1.0（满仓）
- 短期 MA 下穿长期 MA -> 目标仓位 0.0（空仓）
- 过渡期可输出 0.5（半仓）等中间值

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
    equity_curve: pd.Series       # 权益曲线（含基准线）
    benchmark_curve: pd.Series    # 基准权益曲线（Buy & Hold）
    trades: list[TradeRecord]     # 交易记录
    metrics: dict[str, float]     # 绩效指标
    signals: pd.Series            # 目标仓位权重序列
    daily_returns: pd.Series      # 日收益率序列
```

### 5. API 层

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/market-data/kline` | GET | 获取 K 线数据（symbol, market, period, startDate, endDate） |
| `/api/market-data/symbols` | GET | 搜索股票代码 |
| `/api/backtest/run` | POST | 运行回测（body: {symbol, market, period, strategy_name, strategy_params, start_date, end_date, initial_capital, commission_rate}） |
| `/api/backtest/strategies` | GET | 可用策略列表 |
| `/api/factors` | GET | 可用因子列表 |

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
- 策略选择（下拉）+ 参数配置
- 运行回测按钮
- 结果展示：
  - 权益曲线（折线图，叠加基准线）
  - 绩效指标卡片（Sharpe, 回撤, 胜率等）
  - 交易记录表格

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

## Agent 友好设计规范

1. **CLAUDE.md**：项目根目录必须有，描述模块职责、关键文件、运行方式
2. **文件大小**：单文件不超过 300 行，超过即拆分
3. **命名规范**：Python PEP8, TypeScript camelCase，文件名 snake_case
4. **显式 > 隐式**：不用 Spring 式注解魔法，所有依赖显式传入
5. **扁平结构**：最多 3 层目录嵌套
6. **类型标注**：Python 全量使用 type hints，TypeScript strict mode
7. **无全局状态**：依赖注入，可测试
8. **配置集中**：YAML + .env，不散落在代码中
