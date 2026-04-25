# OpenTrading 开源项目重整设计

> 日期: 2026-04-24
> 状态: 待实施
> 目标: 将 ez-trading 项目重整为符合优秀开源项目标准的 OpenTrading

## 背景

ez-trading 是一个技术成熟的量化交易研究平台（3000+ 测试、13 模块、Docker/CI），但缺少开源项目的标准门面工程。本次重整不涉及代码逻辑改动，聚焦于目录结构、文档、品牌、标准文件。

## 目标受众

- **主要**: A 股量化研究者/交易者 — 通过 UI 直接使用平台
- **次要**: 作品集展示 — 展示工程能力

## 设计约束

- 文档语言: 中文为主
- 不改动 `ez/`、`web/`、`tests/`、`scripts/` 的内部代码逻辑
- 不拆分后端/前端大文件（独立重构任务）
- 不泄漏任何 API key（`.env` 不入 git）

---

## 1. 品牌与命名

- 对外品牌名: **OpenTrading**（README 标题、GitHub 仓库名）
- 包名保持: `ez-trading`（pyproject.toml `name` 字段不改，避免 `pip install opentrading` 却 `import ez` 的困惑）
- Python 导入名: `ez`（不改）
- License: MIT
- pyproject.toml 更新: description、license、urls、readme（name 不动）

## 2. 顶层目录结构

### 重整后

```
OpenTrading/
├── README.md                    # 新建 — 项目门面（中文）
├── LICENSE                      # 新建 — MIT
├── CONTRIBUTING.md              # 新建 — 贡献指南（轻量）
├── CHANGELOG.md                 # 新建 — 版本历史
├── SECURITY.md                  # 新建 — 安全上报
│
├── .github/                     # 保留
├── .githooks/                   # 保留
│
├── ez/                          # 保留 — 核心包
├── web/                         # 保留 — React 前端
├── tests/                       # 保留 — 测试
├── scripts/                     # 保留 — 工具脚本
│
├── strategies/                  # 保留原位 — 用户策略目录（清理至仅 mom20.py）
├── portfolio_strategies/        # 保留原位 — 用户组合策略目录
├── factors/                     # 保留原位 — 用户因子目录
├── cross_factors/               # 保留原位 — 用户截面因子目录
├─��� ml_alphas/                   # 保留���位 — 用户 ML Alpha 目录
│
├── docs/                        # 重组 — 见第 4 节
├── configs/                     # 保留
├── data/                        # 保留（.gitignore）
│
├── pyproject.toml               # 更新元数据
├── CMakeLists.txt               # 保留（C++ nanobind 构建仍需要）
├── Dockerfile                   # 保留
├── docker-compose.yml           # 保留
├── launcher.py                  # 保留
├── .env.example                 # 保留
├── .gitignore                   # 更新
├── .dockerignore                # 保留
└── CLAUDE.md                    # 保留 — agent 开发用
```

### 删除/移动的文件

| 操作 | 文件 | 原因 |
|------|------|------|
| 无需操作 | `ez-trading-mac-arm64.zip` | 已确认不在 git 跟踪中（本地文件） |
| 无需操作 | `ez-trading.spec` | 已确认不在 git 跟踪中（本地文件） |
| 删除 | `strategies/advanced_macd.py` | 精简，仅保留 mom20.py |
| 删除 | `strategies/boll_macd_breakout.py` | 精简 |
| 删除 | `strategies/low_vol_momentum.py` | 精简 |
| 删除 | `strategies/rsi_momentum_reversal.py` | 精简 |
| 删除 | `portfolio_strategies/etf_momentum_rotation.py` | 精简，builtin 里已有 EtfMacdRotation |

### 路径说明

`strategies/`、`factors/`、`portfolio_strategies/`、`cross_factors/`、`ml_alphas/` 保持原位。这些目录是框架的运行时工作目录（loader 扫描 + UI CodeEditor 保存路径），不是示例目录。保持原位避免了以下位置的路径更新风险：

- `configs/default.yaml` (`strategy.scan_dirs`)
- `ez/config.py:44` (硬编码默认值)
- `ez/strategy/loader.py` (硬编码 `factors/`)
- `ez/portfolio/loader.py:31,36,46` (硬编码 `portfolio_strategies/`、`cross_factors/`、`ml_alphas/`)
- `Dockerfile:38-42` (5 条 COPY)
- `docker-compose.yml:8-12` (4 条 volume)
- `ez/agent/` (代码保存路径)
- `CLAUDE.md` 及各模块 `CLAUDE.md`

## 3. README.md

### 结构

```
# OpenTrading

一句话介绍 + badge 行（Python、React、License、Tests）

## 平台截图
  hero 截图（Dashboard 全貌）

## 核心功能
  8 项功能列表（策略回测、因子研究、组合优化、参数搜索、AI 助手、模拟实盘、MLAlpha、全流程 UI）

## 快速开始
  ### Docker（推荐）
    3 行命令
  ### 本地安装
    5 行命令

## 功能展示（截图驱动）
  ### 策略编辑与回测
    截图 + 一段描述
  ### 因子研究面板
    截图 + 一段描述
  ### 组合回测
    截图 + 一段描述
  ### AI 研究助手
    截图 + 一段描述
  ### 模拟实盘
    截图 + 一段描述

## 技术架构
  依赖流文字图 + 模块表格

## 项目结构
  简化目录树

## 环境变量配置
  指向 .env.example 说明

## 开发指南
  指向 CONTRIBUTING.md

## 更新日志
  指向 CHANGELOG.md

## License
  MIT
```

### 截图规划

所有截图存放于 `docs/images/`，README 通过相对路径引用。

| # | 截图内容 | 文件名 |
|---|---------|--------|
| 1 | Dashboard 首页全貌 | `docs/images/dashboard.png` |
| 2 | 代码编辑器 + 策略编写 | `docs/images/code-editor.png` |
| 3 | 单股回测结果（K 线 + 收益曲线） | `docs/images/backtest-result.png` |
| 4 | 因子评估面板（IC 图表） | `docs/images/factor-panel.png` |
| 5 | 组合回测面板 | `docs/images/portfolio-panel.png` |
| 6 | AI Chat 对话面板 | `docs/images/ai-assistant.png` |
| 7 | Paper Trading 监控面板 | `docs/images/paper-trading.png` |
| 8 | 参数搜索 / Walk-Forward | `docs/images/walk-forward.png` |

初始版本用 `<!-- 截图占位: xxx -->` 占位，用户后续替换。

## 4. docs/ 目录重组

### 重组后结构

```
docs/
├── images/                          # 新建 — 截图资源
│   └── (8 张截图占位)
│
├── guide/                           # 新建 — 用户指南
│   ├── installation.md              # 安装配置详解
│   ├── quick-start.md               # 快速上手教程（UI 驱动）
│   ├── features.md                  # 功能总览
│   └── faq.md                       # 常见问题
│
├── architecture/                    # 保留 — 架构文档
│   ├── system-architecture.md
│   └── governance.md
│
├── internal/                        # 重组 — 内部开发文档
│   ├── core-changes/                # ← 原 docs/core-changes/
│   ├── audit/                       # ← 原 docs/audit/
│   ├── specs/                       # ← 原 docs/superpowers/specs/
│   └── plans/                       # ← 原 docs/superpowers/plans/
```

### 移动映射

| 原路径 | 新路径 |
|-------|--------|
| `docs/core-changes/*` | `docs/internal/core-changes/*` |
| `docs/audit/*` | `docs/internal/audit/*` |
| `docs/superpowers/specs/*` | `docs/internal/specs/*` |
| `docs/superpowers/plans/*` | `docs/internal/plans/*` |
| `docs/v2.12-code-audit.md` | `docs/internal/audit/v2.12-code-audit.md` |

### 用户指南内容规划

#### installation.md
- Docker 方式（推荐）
- 本地安装（Python 3.12+、Node 22）
- C++ 扩展编译（可选）
- 环境变量配置（Tushare token 申请、LLM key）
- 数据缓存构建

#### quick-start.md
- 启动服务
- 打开浏览器
- UI 操作流程：写策略 → 跑回测 → 看结果
- 截图引导

#### features.md
- 对应 UI 面板逐一介绍
- 策略回测、因子研究、组合优化、参数搜索、AI 助手、模拟实盘、MLAlpha
- 每个功能附截图占位

#### faq.md
- 数据源：Tushare 注册、积分要求
- A 股规则：T+1、涨跌停、印花税
- 常见报错处理
- Docker vs 本地安装选择

## 5. 标准开源文件

### LICENSE

MIT License，署名 JayCheng113，年份 2026。

### CONTRIBUTING.md

轻量版，覆盖：

```
## 环境搭建
  Python 3.12+、Node 22、依赖安装

## 运行测试
  scripts/run_pytest_safe.sh（macOS 特殊处理说明）
  cd web && npm test

## 项目结构
  简述 + 指向 docs/architecture/

## 代码风格
  遵循现有风格，无额外 linter 强制要求

## 提交 PR
  fork → branch → test → PR
```

### CHANGELOG.md

Keep a Changelog 格式，从 git history 提炼：

```
## [0.3.3] - 2026-04-16
  QMT 实盘对接、资金策略框架、混合市场 auto-tick

## [0.3.x] - V3 执行架构
  Paper trading OMS、事件溯源、QMT broker stack、部署门控

## [0.2.x] - 研究与优化
  组合优化器、风控、Brinson 归因、MLAlpha、研究框架、Walk-forward

## [0.1.x] - 初始平台
  单股回测引擎、因子计算、策略框架、Web UI、AI 助手
```

### SECURITY.md

```
## 安全问题上报
  联系邮箱（不公开讨论安全漏洞）
  响应时间承诺
```

## 6. 配置文件更新

### pyproject.toml

仅新增/更新以下字段，`name = "ez-trading"` 保持不变：

```toml
[project]
description = "开源量化交易研究平台 — 从策略研究到模拟实盘，全流程 UI 驱动"
license = {text = "MIT"}
readme = "README.md"

[project.urls]
Homepage = "https://github.com/JayCheng113/OpenTrading"
Repository = "https://github.com/JayCheng113/OpenTrading"
```

### .gitignore 补充

确认以下规则存在（已有的不重复添加）：

```gitignore
# 确保 .env 不泄漏
.env
.env.local
```

注意：`*.zip` 和 `*.spec` 不需要加 — 这些文件已确认不在 git 跟踪中，且过于宽泛的通配符可能误伤合法文件。

### configs/default.yaml 及代码路径

**不需要修改。** 用户工作目录保持原位，所有 loader 扫描路径、Dockerfile COPY、docker-compose volume 映射均无需改动。

## 7. 组合策略精简

`ez/portfolio/builtin_strategies.py` 当前包含 4 个 QMT 移植策略：

- `EtfMacdRotation` — **保留**（最简单，周线 MACD 轮动）
- `EtfSectorSwitch` — 删除
- `EtfRotateCombo` — 删除
- `EtfStockEnhance` — 删除

同步更新：
- 文件顶部 docstring
- `__init__.py` 的导出（如有）
- 相关测试中对删除策略的引用

## 8. 不做的事情

明确排除，避免范围蔓延：

- **不拆分大文件**: `scheduler.py`（3583 行）、`qmt_session_owner.py`（2449 行）等保持现状
- **不拆分大组件**: `ValidationPanel.tsx`（1316 行）等保持现状
- **不改代码逻辑**: 只改文档、标准文件、builtin 策略精简
- **不移动用户工作目录**: `strategies/`、`factors/` 等保持根目录原位
- **不删除 `tools/pytest_shim/`**: `run_pytest_safe.sh` 依赖
- **不加 linter/formatter**: 不引入 ruff、black、mypy 等新工具链
- **不建文档站**: 不搭 MkDocs/VitePress（后续可做）
- **不改 UI 语言**: 前端保持中文

## 实施风险

| 风险 | 缓解 |
|------|------|
| 删除 builtin 策略后测试失败 | 同步清理相关测试用例 |
| docs/ 内部文档移动后有交叉引用断裂 | `git grep` 搜索旧路径前缀，修复引用 |
| `tools/pytest_shim/` 被误删 | 明确保留（`run_pytest_safe.sh` 依赖） |

注意：用户工作目录不移动，消除了路径断裂和 Docker 构建失败的主要风险。

## 验收标准

1. `scripts/run_pytest_safe.sh` 全量测试通过
2. `cd web && npm test` 前端测试通过
3. README.md 结构完整（截图占位）
4. LICENSE、CONTRIBUTING.md、CHANGELOG.md、SECURITY.md 存在且内容正确
5. docs/ 重组后无悬空引用（`git grep "docs/core-changes\|docs/audit\|docs/superpowers"` 无残留）
6. `.env` 确认不在 git 跟踪中
7. `ez/portfolio/builtin_strategies.py` 仅包含 `EtfMacdRotation`
8. `strategies/` 仅包含 `mom20.py`
