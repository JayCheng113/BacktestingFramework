# OpenTrading

开源量化交易研究平台 — 从策略研究到模拟实盘，全流程 UI 驱动。

![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react)
![License](https://img.shields.io/badge/License-MIT-green)
![Tests](https://img.shields.io/badge/Tests-3000%2B-brightgreen)

<!-- 截图占位: Dashboard 全貌 -->
<!-- 请将截图放置在 docs/images/dashboard.png 并取消注释下面的图片标签 -->
<!-- ![Dashboard](docs/images/dashboard.png) -->

---

## 核心功能

- **策略回测** — 支持单股/组合两种模式，完整实现 A 股市场规则：T+1、涨跌停、印花税
- **因子研究** — 截面 IC/RankIC/ICIR/衰减分析/分组收益，内置相关性矩阵与多因子对比
- **组合优化** — 均值方差、风险平价、最小方差等多种优化器，配合 Brinson 归因分析
- **参数搜索** — Walk-forward 验证结合 Bootstrap 显著性检验，有效识别过拟合策略
- **AI 助手** — LLM 编程助手辅助对话式策略开发，支持自主研究任务
- **模拟实盘** — 完整的部署门控、调度器、OMS 事件溯源，原生对接 QMT 券商接口
- **MLAlpha** — 9 种估计器（Ridge/Lasso/RandomForest/XGBoost 等），自动过拟合诊断
- **全流程 UI** — 所有操作均可在浏览器中完成，无需本地编程环境

---

## 快速开始

### Docker（推荐）

```bash
git clone https://github.com/your-org/opentrading.git
cd opentrading
docker compose up
```

浏览器访问 `http://localhost:8000`

### 本地安装

```bash
git clone https://github.com/your-org/opentrading.git
cd opentrading
pip install -e ".[all]"
cd web && npm install && npm run build && cd ..
uvicorn ez.api.app:app --host 0.0.0.0 --port 8000
```

> **首次使用**：需要配置数据源（Tushare Token 等），详见 [docs/guide/installation.md](docs/guide/installation.md)

---

## 功能展示

### 策略编辑与回测

<!-- ![代码编辑器](docs/images/code-editor.png) -->
<!-- ![回测结果](docs/images/backtest-result.png) -->

在浏览器中直接编写策略代码，实时运行回测并查看净值曲线、Sharpe 比率、最大回撤等完整指标。支持 Walk-forward 验证和显著性检验，有效识别过拟合策略。

### 因子研究面板

<!-- ![因子研究面板](docs/images/factor-panel.png) -->

内置多种技术与基本面因子，支持截面 IC/RankIC/ICIR 计算、因子衰减分析、分组收益回测，以及多因子相关性矩阵分析。

### 组合回测

<!-- ![组合回测](docs/images/portfolio-panel.png) -->

支持多标的轮动回测，提供均值方差、风险平价等多种组合优化方式，配合 Brinson 归因精确拆解超额收益来源。

### AI 研究助手

<!-- ![AI研究助手](docs/images/ai-assistant.png) -->

基于 LLM 的对话式策略开发助手，支持自然语言描述策略需求，自动生成并验证代码；自主研究模式可端到端完成因子挖掘与回测任务。

### 模拟实盘

<!-- ![模拟实盘](docs/images/paper-trading.png) -->

完整的模拟实盘环境，包括部署门控、定时调度、OMS 事件溯源与回放，支持 QMT 券商实单接口（白名单小额实盘）。

---

## 技术架构

```
ez/types.py -> ez/data/ -> ez/factor/ -> ez/strategy/ -> ez/backtest/ -> ez/api/ -> web/
                            ^  ts_ops                     ^  matcher
                            └──────────── ez/core/ ───────┘

ez/llm/      仅依赖配置层
ez/agent/    消费 backtest / core / llm 接口
ez/live/     基于 portfolio + 部署基础设施
ez/research/ 可复用研究工作流层
```

| 模块 | 职责 |
|------|------|
| `ez/core/` | 计算原语：匹配器、时间序列操作、市场规则 |
| `ez/data/` | 数据摄取、验证、缓存、Parquet/DuckDB 存储、提供者链 |
| `ez/factor/` | 因子计算与评估，内置技术/基本面因子 |
| `ez/strategy/` | 单股策略框架与自动注册 |
| `ez/backtest/` | 单股引擎、指标计算、Walk-forward、显著性检验 |
| `ez/portfolio/` | 组合引擎、截面因子、优化器、风险管理、归因、MLAlpha |
| `ez/research/` | 可复用研究管线与步骤 |
| `ez/llm/` | LLM 提供商抽象层 |
| `ez/agent/` | 编程/研究智能体、沙箱、工具、安全守卫 |
| `ez/live/` | 模拟实盘部署生命周期、调度器、监控 |
| `ez/api/` | FastAPI 路由层 |
| `web/` | React 19 + ECharts 前端仪表盘 |

详细架构说明见 [docs/architecture/](docs/architecture/)

---

## 项目结构

```
opentrading/
├── ez/                    # 后端核心包
│   ├── core/              # 计算原语
│   ├── data/              # 数据层
│   ├── factor/            # 因子层
│   ├── strategy/          # 策略层
│   ├── backtest/          # 回测引擎
│   ├── portfolio/         # 组合管理
│   ├── research/          # 研究管线
│   ├── llm/               # LLM 集成
│   ├── agent/             # AI 智能体
│   ├── live/              # 模拟实盘
│   └── api/               # API 路由
├── web/                   # React 前端
├── strategies/            # 用户策略目录
├── factors/               # 用户因子目录
├── ml_alphas/             # MLAlpha 定义目录
├── docs/                  # 文档
├── tests/                 # 测试套件
├── scripts/               # 工具脚本
└── docker-compose.yml
```

---

## 环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `TUSHARE_TOKEN` | 是 | Tushare 数据接口 Token，用于获取 A 股行情与财务数据 |
| `DEEPSEEK_API_KEY` | 否 | DeepSeek LLM API Key，用于 AI 助手与自主研究功能 |
| `FMP_API_KEY` | 否 | Financial Modeling Prep API Key，用于部分基本面数据 |

---

## 开发指南

贡献代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，了解代码规范、测试要求与提交流程。

---

## 更新日志

完整版本历史见 [CHANGELOG.md](CHANGELOG.md)

---

## License

本项目以 [MIT 协议](LICENSE) 开源，欢迎自由使用与二次开发。
