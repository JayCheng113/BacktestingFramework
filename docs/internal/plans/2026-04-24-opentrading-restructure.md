# OpenTrading 开源项目重整 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 ez-trading 重整为优秀开源项目标准的 OpenTrading，不改代码逻辑。

**Architecture:** 纯文档/配置/结构变更。分三阶段：(1) 标准开源文件 + 配置更新，(2) 代码精简 + docs 重组，(3) README + 用户指南编写。每阶段末尾跑测试验证。

**Tech Stack:** Python 3.12, FastAPI, React 19, DuckDB, Git

**Spec reference:** `docs/superpowers/specs/2026-04-24-opentrading-restructure-design.md`

---

## Phase 1: 标准开源文件 + 配置

### Task 1: LICENSE + SECURITY.md

**Files:**
- Create: `LICENSE`
- Create: `SECURITY.md`

- [ ] **Step 1: Create LICENSE**

```
MIT License

Copyright (c) 2026 JayCheng113

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Create SECURITY.md**

```markdown
# 安全问题上报

如果你发现了安全漏洞，请**不要**在公开 Issue 中讨论。

请发送邮件至 ZHANC113003@gmail.com，我们会在 48 小时内响应。

## 上报内容

- 漏洞描述
- 复现步骤
- 影响范围评估

感谢你帮助我们保持项目安全。
```

- [ ] **Step 3: Commit**

```bash
git add LICENSE SECURITY.md
git commit -m "docs: add MIT LICENSE and SECURITY.md"
```

### Task 2: pyproject.toml 更新

**Files:**
- Modify: `pyproject.toml:6-8`

- [ ] **Step 1: Update pyproject.toml metadata**

在 `[project]` 块中，修改 `description` 并新增 `license`、`readme`、`urls`：

```toml
[project]
name = "ez-trading"
version = "0.3.3"
description = "OpenTrading — 开源量化交易研究平台，从策略研究到模拟实盘，全流程 UI 驱动"
requires-python = ">=3.12"
license = {text = "MIT"}
readme = "README.md"
```

在文件末尾（`[tool.ruff]` 之后）新增：

```toml
[project.urls]
Homepage = "https://github.com/JayCheng113/OpenTrading"
Repository = "https://github.com/JayCheng113/OpenTrading"
```

- [ ] **Step 2: Verify pyproject.toml still valid**

Run: `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: update pyproject.toml metadata for OpenTrading"
```

### Task 3: .gitignore 确认

**Files:**
- Modify: `.gitignore` (仅在缺失时补充)

- [ ] **Step 1: Verify .env rules exist**

Run: `grep -n "^\.env" .gitignore`

Expected: 应该能看到 `.env` 已在 gitignore 中（当前第 6 行）。

如果 `.env.local` 不在列表中，追加一行 `.env.local`。

当前 `.gitignore` 已包含 `*.zip`、`ez-trading.spec`、`.env`，无需额外修改。

- [ ] **Step 2: Verify .env not tracked**

Run: `git ls-files | grep "^\.env$"`
Expected: 无输出（空）

- [ ] **Step 3: Commit (if changed)**

```bash
git add .gitignore
git commit -m "chore: verify .gitignore safety rules"
```

---

## Phase 2: 代码精简 + docs 重组

### Task 4: 精简 builtin_strategies.py

**Files:**
- Modify: `ez/portfolio/builtin_strategies.py`

当前文件 799 行，包含 4 个策略类。只保留 `EtfMacdRotation`（lines 107-251），删除其余三个类及其专用 helper。

- [ ] **Step 1: Keep lines 1-251 (docstring + shared helpers + EtfMacdRotation), delete lines 252-799**

保留的内容：
- Lines 1-8: 模块 docstring（需要更新）
- Lines 9-50: imports + `_weekly_macd_signal` + `_get_close` + `_get_raw_close`
- Lines 52-100: `_remove_outliers_and_refit` — 删除（仅被 EtfSectorSwitch/EtfRotateCombo 使用）
- Lines 103-251: `EtfMacdRotation` — 保留

更新模块 docstring（第 1-8 行）为：

```python
"""Built-in portfolio strategy — strict 1:1 port from QMT script.

EtfMacdRotation — QMT "ETF指标MACD周线收益率5分钟回测" calc_rotate_signal
"""
```

删除 `_remove_outliers_and_refit` 函数（lines 52-100），删除 lines 253-799 的三个类。

- [ ] **Step 2: Verify file structure**

Run: `grep -n "^class " ez/portfolio/builtin_strategies.py`
Expected: 只有一行 `class EtfMacdRotation`

Run: `wc -l ez/portfolio/builtin_strategies.py`
Expected: 约 200 行左右

- [ ] **Step 3: Update ez/portfolio/__init__.py import**

当前第 3 行：
```python
from ez.portfolio.builtin_strategies import EtfMacdRotation, EtfSectorSwitch, EtfStockEnhance  # noqa: F401
```

改为：
```python
from ez.portfolio.builtin_strategies import EtfMacdRotation  # noqa: F401
```

- [ ] **Step 4: Update ez/api/routes/portfolio.py import**

当前第 16 行：
```python
from ez.portfolio.builtin_strategies import EtfMacdRotation, EtfSectorSwitch, EtfStockEnhance, EtfRotateCombo  # noqa: F401
```

改为：
```python
from ez.portfolio.builtin_strategies import EtfMacdRotation  # noqa: F401
```

- [ ] **Step 5: Clean up comments in ez/api/_portfolio_helpers.py**

`ez/api/_portfolio_helpers.py:470` 的注释：
```python
        # EtfRotateCombo has its own DEFAULT_SECTOR_ETFS and handles classification
```
改为：
```python
        # Strategies with DEFAULT_SECTOR_ETFS handle classification
```

`ez/api/_portfolio_helpers.py:481` 的注释：
```python
        # Auto-inject rotate_symbols for EtfRotateCombo
```
改为：
```python
        # Auto-inject rotate_symbols for strategies with DEFAULT_ROTATE_SYMBOLS
```

注意：这两处的实际逻辑代码是 generic 的（使用 `hasattr`/`getattr`），不需要改动，只更新注释。

- [ ] **Step 6: Clean up comment in ez/live/paper_engine.py**

`ez/live/paper_engine.py:184` 的注释：
```python
            # weekly-signaled one (EtfRotateCombo, ARotateBondBlend) would
```
改为：
```python
            # weekly-signaled one (e.g. strategies returning None) would
```

- [ ] **Step 7: Commit**

```bash
git add ez/portfolio/builtin_strategies.py ez/portfolio/__init__.py ez/api/routes/portfolio.py ez/api/_portfolio_helpers.py ez/live/paper_engine.py
git commit -m "refactor: keep only EtfMacdRotation in builtin portfolio strategies"
```

### Task 5: 清理相关测试

**Files:**
- Modify: `tests/test_portfolio/test_v291_regression.py`
- Modify: `tests/test_live/test_paper_engine_none_signal.py`

- [ ] **Step 1: Delete TestEtfSectorSwitch and TestEtfStockEnhance test classes**

删除 `tests/test_portfolio/test_v291_regression.py` 中的：
- `class TestEtfSectorSwitch:` (lines 233-273) — 整个类
- `class TestEtfStockEnhance:` (lines 276-327) — 整个类

- [ ] **Step 2: Update registry test**

`tests/test_portfolio/test_v291_regression.py` 中 `class TestBuiltinRegistration:`

Line 382 的 docstring 改为：
```python
    """All 3 builtin strategies must be in registry."""
```

Lines 386-388 改为：
```python
        expected = ["TopNRotation", "MultiFactorRotation",
                    "EtfMacdRotation"]
```

Lines 393-394 改为：
```python
        builtins = ["TopNRotation", "MultiFactorRotation",
                    "EtfMacdRotation"]
```

- [ ] **Step 3: Clean comment in test_paper_engine_none_signal.py**

`tests/test_live/test_paper_engine_none_signal.py:6` 的注释：
```
when the wrapped strategy (ARotateBondBlend -> EtfRotateCombo) returned
```
改为：
```
when the wrapped strategy returned
```

- [ ] **Step 4: Run tests to verify**

Run: `scripts/run_pytest_safe.sh tests/test_portfolio/test_v291_regression.py -v`
Expected: All tests PASS, no references to deleted strategies

Run: `scripts/run_pytest_safe.sh tests/test_live/test_paper_engine_none_signal.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_portfolio/test_v291_regression.py tests/test_live/test_paper_engine_none_signal.py
git commit -m "test: remove tests for deleted builtin strategies"
```

### Task 6: Phase 2 验证 — 全量测试

- [ ] **Step 1: Run full backend test suite**

Run: `scripts/run_pytest_safe.sh tests/ -x -q`
Expected: 3000+ tests pass, 0 failures

如果有失败，检查是否还有其他地方引用了删除的策略：
```bash
git grep "EtfSectorSwitch\|EtfRotateCombo\|EtfStockEnhance" -- '*.py'
```

- [ ] **Step 2: Run frontend tests**

Run: `cd web && npm test -- --run`
Expected: 96 tests pass

- [ ] **Step 3: Fix any remaining references if needed**

如果 Step 1-2 发现失败，修复后重新验证，然后 commit。

### Task 7: docs/ 目录重组

**Files:**
- Move: `docs/core-changes/*` → `docs/internal/core-changes/`
- Move: `docs/audit/*` → `docs/internal/audit/`
- Move: `docs/superpowers/specs/*` → `docs/internal/specs/`
- Move: `docs/superpowers/plans/*` → `docs/internal/plans/`
- Move: `docs/v2.12-code-audit.md` → `docs/internal/audit/`
- Create: `docs/images/.gitkeep`
- Create: `docs/guide/.gitkeep`

- [ ] **Step 1: Create target directories and move files**

```bash
mkdir -p docs/internal/core-changes docs/internal/audit docs/internal/specs docs/internal/plans docs/images docs/guide

git mv docs/core-changes/* docs/internal/core-changes/
git mv docs/audit/* docs/internal/audit/
git mv docs/v2.12-code-audit.md docs/internal/audit/
git mv docs/superpowers/specs/* docs/internal/specs/
git mv docs/superpowers/plans/* docs/internal/plans/

# Clean up empty directories
rmdir docs/core-changes docs/audit docs/superpowers/specs docs/superpowers/plans docs/superpowers

# Create placeholder files
touch docs/images/.gitkeep docs/guide/.gitkeep
```

- [ ] **Step 2: Verify structure**

Run: `ls docs/`
Expected: `architecture/  guide/  images/  internal/`

Run: `ls docs/internal/`
Expected: `audit/  core-changes/  plans/  specs/`

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: reorganize docs/ — internal docs moved to docs/internal/"
```

### Task 8: 修复 docs 移动后的交叉引用

**Files:**
- Modify: `CLAUDE.md`
- Modify: `ez/portfolio/ml_alpha.py`
- Modify: `ez/portfolio/engine.py`
- Modify: `tests/test_portfolio/test_v213_readiness.py`
- Modify: docs/internal/ 中的文件（内部交叉引用）

- [ ] **Step 1: Update CLAUDE.md**

Line 17: `docs/core-changes/v2.3-roadmap.md` → `docs/internal/core-changes/v2.3-roadmap.md`

Line 67 (大约): `docs/core-changes/` → `docs/internal/core-changes/`

同时更新 "Read First" 部分的列表，添加注释说明 docs 结构：

```markdown
## Read First

Read these before major architectural changes:

- `docs/architecture/system-architecture.md`
- `docs/architecture/governance.md`
- `docs/internal/core-changes/v2.3-roadmap.md`
```

- [ ] **Step 2: Update source code references**

`ez/portfolio/ml_alpha.py:40`:
```python
See ``docs/superpowers/plans/2026-04-06-v213-ml-alpha.md`` for design
```
→
```python
See ``docs/internal/plans/2026-04-06-v213-ml-alpha.md`` for design
```

`ez/portfolio/engine.py:201`:
```python
                # See docs/core-changes/2026-04-10-engine-dividend-fix.md
```
→
```python
                # See docs/internal/core-changes/2026-04-10-engine-dividend-fix.md
```

`tests/test_portfolio/test_v213_readiness.py:21`:
```python
See `docs/audit/v2.13-readiness-audit.md` for the full readiness
```
→
```python
See `docs/internal/audit/v2.13-readiness-audit.md` for the full readiness
```

- [ ] **Step 3: Batch-update internal doc cross-references**

内部文档互相引用很多，用 sed 批量替换（仅在 `docs/internal/` 内执行）：

```bash
find docs/internal -name "*.md" -exec sed -i '' \
  -e 's|docs/core-changes/|docs/internal/core-changes/|g' \
  -e 's|docs/audit/|docs/internal/audit/|g' \
  -e 's|docs/superpowers/specs/|docs/internal/specs/|g' \
  -e 's|docs/superpowers/plans/|docs/internal/plans/|g' \
  {} +
```

- [ ] **Step 4: Verify no stale references remain**

Run: `git grep "docs/core-changes\|docs/audit\|docs/superpowers" -- '*.py' '*.md' ':!docs/internal/'`
Expected: 无输出（只有 docs/internal/ 内的文件自身引用）

如果仍有残留，逐个修复。

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md ez/portfolio/ml_alpha.py ez/portfolio/engine.py tests/test_portfolio/test_v213_readiness.py docs/internal/
git commit -m "docs: fix cross-references after docs/ reorganization"
```

### Task 9: 删除用户示例策略

**Files:**
- Delete: `strategies/advanced_macd.py`, `strategies/boll_macd_breakout.py`, `strategies/low_vol_momentum.py`, `strategies/rsi_momentum_reversal.py`
- Delete: `portfolio_strategies/etf_momentum_rotation.py`

注意：这些文件在 `.gitignore` 中（`strategies/*.py` 等），因此不在 git 跟踪中。这是**本地文件清理**，不会产生 git commit。

- [ ] **Step 1: Verify files are not tracked**

Run: `git ls-files strategies/ portfolio_strategies/ | grep "\.py$" | grep -v __init__`
Expected: 无输出

- [ ] **Step 2: Delete local files**

```bash
rm -f strategies/advanced_macd.py strategies/boll_macd_breakout.py strategies/low_vol_momentum.py strategies/rsi_momentum_reversal.py
rm -f portfolio_strategies/etf_momentum_rotation.py
```

- [ ] **Step 3: Verify mom20.py preserved**

Run: `ls strategies/`
Expected: `__init__.py  .gitkeep  mom20.py`

（`mom20.py` 是本地文件，不在 git 中，但保留供用户参考）

---

## Phase 3: README + 用户文档

### Task 10: README.md

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create README.md**

```markdown
# OpenTrading

开源量化交易研究平台 — 从策略研究到模拟实盘，全流程 UI 驱动。

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![React](https://img.shields.io/badge/React-19-61dafb)
![License](https://img.shields.io/badge/License-MIT-green)
![Tests](https://img.shields.io/badge/Tests-3000+-brightgreen)

<!-- 截图占位: Dashboard 全貌 -->
<!-- 请替换为 docs/images/dashboard.png -->

## 核心功能

- **策略回测** — 单股/组合回测，A 股规则内置（T+1、涨跌停、印花税、整手交易）
- **因子研究** — 截面 IC/RankIC/ICIR/衰减分析/分组收益，因子相关性矩阵
- **组合优化** — 均值方差/风险平价/最小方差优化器，Brinson 归因分析
- **参数搜索** — Walk-forward 验证 + Bootstrap 显著性检验，防过拟合
- **AI 助手** — 内置 LLM 编程助手，对话式策略开发与自动研究
- **模拟实盘** — 部署门控、调度器、OMS 事件溯源、QMT 券商对接
- **MLAlpha** — 机器学习因子框架，支持 9 种估计器，自动过拟合诊断
- **全流程 UI** — 浏览器中完成所有操作，无需编写脚本

## 快速开始

### 方式一：Docker（推荐）

```bash
git clone https://github.com/JayCheng113/OpenTrading.git
cd OpenTrading
docker compose up
```

打开浏览器访问 http://localhost:8000

### 方式二：本地安装

```bash
git clone https://github.com/JayCheng113/OpenTrading.git
cd OpenTrading
pip install -e ".[all]"
cd web && npm install && npm run build && cd ..
python -m uvicorn ez.api.app:app --host 0.0.0.0 --port 8000
```

打开浏览器访问 http://localhost:8000

> 首次使用需要配置数据源，详见 [安装指南](docs/guide/installation.md)。

## 功能展示

### 策略编辑与回测

<!-- 截图占位: 代码编辑器 + 回测结果 -->
<!-- 请替换为 docs/images/code-editor.png 和 docs/images/backtest-result.png -->

在浏览器中直接编写策略代码，一键回测。查看 K 线叠加买卖点、收益曲线、完整绩效指标（Sharpe、Sortino、最大回撤、胜率等）。

### 因子研究面板

<!-- 截图占位: 因子评估面板 -->
<!-- 请替换为 docs/images/factor-panel.png -->

截面因子评估：IC 时序图、分组收益分析、IC 衰减曲线。支持因子中性化、多因子合成、Alpha Combiner。

### 组合回测

<!-- 截图占位: 组合回测面板 -->
<!-- 请替换为 docs/images/portfolio-panel.png -->

多股组合回测，内置轮动、多因子等策略模板。支持优化器（均值方差/风险平价）、风控（回撤熔断/换手限制）、Brinson 归因分析。

### AI 研究助手

<!-- 截图占位: Chat 面板 -->
<!-- 请替换为 docs/images/ai-assistant.png -->

对话式策略开发：描述你的想法，AI 自动编写策略代码、运行回测、评估结果。支持自主研究模式，AI 自动迭代优化。

### 模拟实盘

<!-- 截图占位: Paper Trading 面板 -->
<!-- 请替换为 docs/images/paper-trading.png -->

策略部署到模拟实盘：部署门控验证、每日自动调仓、实时监控仪表盘、事件日志追溯。支持 QMT 券商对接。

## 技术架构

```
核心依赖流:
types → data → factor → strategy → backtest → api → web
                ↑ ts_ops              ↑ matcher
                └──── core ───────────┘
```

| 模块 | 职责 |
|------|------|
| `ez/core/` | 计算原语：撮合器、时序运算、市场规则 |
| `ez/data/` | 数据层：Tushare/AKShare 接入、Parquet 缓存、DuckDB 存储 |
| `ez/factor/` | 因子计算、评估、内置技术/基本面因子 |
| `ez/strategy/` | 单股策略框架，自动注册 |
| `ez/backtest/` | 单股回测引擎、绩效指标、Walk-forward、显著性检验 |
| `ez/portfolio/` | 组合引擎、优化器、风控、归因、MLAlpha、Ensemble |
| `ez/research/` | 可复用研究管线（Nested OOS、Bootstrap 等） |
| `ez/llm/` | LLM 提供商抽象层 |
| `ez/agent/` | AI 编程助手、沙箱、自主研究 |
| `ez/live/` | 模拟实盘：部署、调度、OMS、QMT 券商 |
| `ez/api/` | FastAPI REST API |
| `web/` | React 19 + TypeScript + ECharts 前端 |

详细架构文档见 [docs/architecture/](docs/architecture/)。

## 项目结构

```
OpenTrading/
├── ez/                     # 核心 Python 包（13 模块）
├── web/                    # React 前端
├── tests/                  # 测试（3000+）
├── scripts/                # 工具脚本
├── strategies/             # 用户策略目录
├── factors/                # 用户因子目录
├── portfolio_strategies/   # 用户组合策略目录
├── cross_factors/          # 用户截面因子目录
├── ml_alphas/              # 用户 ML Alpha 目录
├── configs/                # 配置文件
├── docs/                   # 文档
│   ├── guide/              # 用户指南
│   ├── architecture/       # 架构文档
│   └── internal/           # 内部开发文档
├── pyproject.toml          # Python 项目配置
├── Dockerfile              # 多阶段容器构建
└── docker-compose.yml      # 容器编排
```

## 环境变量

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `TUSHARE_TOKEN` | 是 | A 股数据源（[tushare.pro](https://tushare.pro) 免费注册） |
| `DEEPSEEK_API_KEY` | 否 | AI 助手（推荐 DeepSeek） |
| `FMP_API_KEY` | 否 | 美股数据（可选） |

## 开发指南

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## License

[MIT](LICENSE)
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add comprehensive README.md for OpenTrading"
```

### Task 11: CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Create CONTRIBUTING.md**

```markdown
# 贡献指南

感谢你对 OpenTrading 的关注！

## 环境搭建

### 依赖

- Python 3.12+
- Node.js 22+
- Git

### 安装

```bash
# 克隆仓库
git clone https://github.com/JayCheng113/OpenTrading.git
cd OpenTrading

# 安装 Python 依赖
pip install -e ".[all,dev]"

# 安装前端依赖
cd web && npm install && cd ..
```

### 配置

复制环境变量模板并填写数据源 token：

```bash
cp .env.example .env
# 编辑 .env，至少填写 TUSHARE_TOKEN
```

## 运行测试

### 后端测试

```bash
# macOS 推荐（避免 readline 段错误）
scripts/run_pytest_safe.sh tests/ -x -q

# Linux / 其他系统
pytest tests/ -x -q
```

### 前端测试

```bash
cd web && npm test -- --run
```

## 项目结构

详见 [docs/architecture/system-architecture.md](docs/architecture/system-architecture.md)。

核心模块在 `ez/` 下，每个子模块有独立的 `CLAUDE.md` 文档说明职责和接口。

## 代码风格

- 遵循现有代码风格
- Python: 行宽 120，Python 3.12+ 语法
- TypeScript: 现有 ESLint 配置
- 无强制 formatter，但推荐保持一致性

## 提交 PR

1. Fork 仓库
2. 创建功能分支：`git checkout -b feature/your-feature`
3. 确保所有测试通过
4. 提交 PR，描述清楚改动内容和原因

## 高风险文件

以下核心文件修改需要特别审慎：

- `ez/types.py`, `ez/errors.py`, `ez/config.py`
- `ez/core/matcher.py`, `ez/core/ts_ops.py`
- `ez/backtest/engine.py`, `ez/portfolio/engine.py`

修改行为性变更时，请在 `docs/internal/core-changes/` 中记录变更提案。
```

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md"
```

### Task 12: CHANGELOG.md

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Create CHANGELOG.md**

从 git history 提炼关键版本，采用 Keep a Changelog 格式：

```markdown
# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 格式。

## [0.3.3] - 2026-04-16

### 新增
- QMT 实盘对接：影子券商、实盘小白名单提交、回调驱动执行同步
- 资金策略框架：阶段式资金梯度（read_only → paper_sim → small_whitelist → expanded → full）
- 混合市场 auto-tick 按市场本地交易日分批
- 四路对账闭环：账户/委托/持仓/成交独立对账
- QMT 宿主服务独立进程管理

### 修复
- 历史非 CN 部署使用 CN 规则的沉默 bug，现在 fail-closed
- 非正数 auto-tick 间隔拒绝

## [0.3.x] - V3 执行架构

### 新增
- Paper trading OMS：事件溯源、幂等重放、快照检查点
- PaperBroker 抽象：统一券商接口
- 部署门控（DeployGate）：比研究门控更严格
- 调度器：单进程编排、每日 tick、暂停/恢复/停止
- 监控仪表盘 + 可选 Webhook 告警
- 策略状态跨重启持久化
- 预交易风控引擎：kill-switch、最大仓位、集中度、换手限制
- 运行时分配器：等权/比例/风险预算/约束优化
- QMT 影子券商：只读对账、回调消费、会话管理

## [0.2.x] - 研究与优化

### 新增
- 组合优化器：均值方差/最小方差/风险平价（Ledoit-Wolf 协方差）
- 风控管理：回撤熔断 + 换手限制
- Brinson 归因分析（Carino 几何链接）
- MLAlpha 机器学习因子框架（9 种估计器 + 过拟合诊断）
- StrategyEnsemble 多策略组合
- 可复用研究管线（Nested OOS、Walk-Forward、Paired Block Bootstrap）
- 因子中性化、多因子合成（AlphaCombiner）
- 基本面数据层 + 内置基本面截面因子
- 本地 Parquet 数据缓存
- AI 自主研究模式

### 修复
- 组合 vs 单股指标公式统一（Sharpe/Sortino/Alpha/Beta）
- 引擎分红处理统一 adj 单位系统
- Walk-forward 尾部交易日丢弃问题

## [0.1.x] - 初始平台

### 新增
- 单股回测引擎，A 股规则内置（T+1、涨跌停、印花税、整手交易）
- 因子计算与评估框架
- 策略框架（自动注册 + 参数 schema）
- Walk-forward 验证 + 显著性检验
- 截面因子研究：IC/RankIC/ICIR/衰减/分组收益/相关性
- FastAPI REST API
- React 19 + TypeScript + ECharts 前端
- LLM 编程助手（对话式策略开发）
- 沙箱安全执行（危险导入拦截 + 路径保护）
- Docker 多阶段构建 + CI/CD
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md with version history"
```

### Task 13: 用户指南 — installation.md

**Files:**
- Create: `docs/guide/installation.md`

- [ ] **Step 1: Create installation.md**

```markdown
# 安装指南

## Docker 安装（推荐）

最简单的方式，无需手动配置 Python/Node 环境。

### 前置条件

- [Docker](https://docs.docker.com/get-docker/) 和 Docker Compose

### 步骤

```bash
git clone https://github.com/JayCheng113/OpenTrading.git
cd OpenTrading
cp .env.example .env
# 编辑 .env，填写 TUSHARE_TOKEN（必填）
docker compose up
```

打开 http://localhost:8000 即可使用。

### 数据持久化

Docker Compose 已配置 volume 映射，以下目录的数据会持久化到宿主机：

- `data/` — 数据库和缓存
- `strategies/` — 用户策略
- `factors/` — 用户因子
- `configs/` — 配置覆盖

## 本地安装

### 前置条件

- Python 3.12+
- Node.js 22+
- Git

### 步骤

```bash
# 1. 克隆仓库
git clone https://github.com/JayCheng113/OpenTrading.git
cd OpenTrading

# 2. 安装 Python 依赖
pip install -e ".[all]"

# 3. 构建前端
cd web && npm install && npm run build && cd ..

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填写 TUSHARE_TOKEN

# 5. 启动服务
python -m uvicorn ez.api.app:app --host 0.0.0.0 --port 8000
# 或使用快捷脚本：
# ./scripts/start.sh
```

### C++ 扩展（可选）

项目包含 nanobind C++ 加速模块，通常 `pip install -e .` 会自动编译。如果编译失败：

```bash
# 安装编译依赖
pip install scikit-build-core nanobind

# 手动构建
pip install -e . --no-build-isolation
```

C++ 扩展不是必须的，框架会自动 fallback 到纯 Python 实现。

## 环境变量说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `TUSHARE_TOKEN` | 是 | A 股数据源 token，[tushare.pro](https://tushare.pro) 免费注册获取 |
| `FMP_API_KEY` | 否 | 美股数据源 |
| `DEEPSEEK_API_KEY` | 否 | AI 助手（推荐 [DeepSeek](https://platform.deepseek.com)） |
| `OPENAI_API_KEY` | 否 | OpenAI 兼容接口 |
| `OPENAI_BASE_URL` | 否 | 自定义 API 地址（搭配 OPENAI_API_KEY 使用） |

也可以在 UI 的设置面板中配置 LLM 和数据源。

## 数据缓存

首次使用时，平台会从 Tushare 拉取数据并缓存到本地。仓库已内置少量 ETF 种子数据（`data/cache/cn_stock_*.parquet`），可以直接体验基本功能。

如需构建完整 A 股数据缓存：

```bash
python scripts/build_data_cache.py
```

注意：完整缓存较大，构建时间取决于 Tushare 积分和网络状况。
```

- [ ] **Step 2: Commit**

```bash
git add docs/guide/installation.md
git commit -m "docs: add installation guide"
```

### Task 14: 用户指南 — quick-start.md

**Files:**
- Create: `docs/guide/quick-start.md`

- [ ] **Step 1: Create quick-start.md**

```markdown
# 快速上手

本指南帮助你在 5 分钟内完成第一次策略回测。

## 前提

- 已完成 [安装](installation.md) 并启动服务
- 浏览器打开 http://localhost:8000

## 第一步：编写策略

1. 点击顶部导航栏的「代码编辑器」
2. 点击「新建策略」
3. 在编辑器中编写你的策略，或使用 AI 助手帮你生成

<!-- 截图占位: 代码编辑器界面 -->
<!-- 请替换为 docs/images/code-editor.png -->

一个最简单的动量策略示例：

```python
from ez.strategy.base import Strategy
from ez.factor.builtin.technical import MomentumFactor

class MyMomentum(Strategy):
    def required_factors(self):
        return [MomentumFactor(20)]

    def generate_signals(self, df):
        mom = df["momentum_20"]
        return (mom > 0.02).astype(float)

    @classmethod
    def get_parameters_schema(cls):
        return {"threshold": {"type": "float", "default": 0.02}}
```

4. 点击「保存」，系统会自动验证代码安全性

## 第二步：运行回测

1. 点击顶部导航栏的「回测」
2. 在下拉菜单中选择刚保存的策略
3. 输入股票代码（如 `000001.SZ`）
4. 设置回测区间
5. 点击「运行回测」

<!-- 截图占位: 回测结果 -->
<!-- 请替换为 docs/images/backtest-result.png -->

## 第三步：查看结果

回测完成后，你会看到：

- **K 线图** — 叠加买卖信号标记
- **收益曲线** — 策略 vs 基准对比
- **绩效指标** — Sharpe、Sortino、最大回撤、胜率、盈亏比等

## 下一步

- [功能总览](features.md) — 了解平台所有功能
- [常见问题](faq.md) — 遇到问题先看这里
```

- [ ] **Step 2: Commit**

```bash
git add docs/guide/quick-start.md
git commit -m "docs: add quick-start guide"
```

### Task 15: 用户指南 — features.md

**Files:**
- Create: `docs/guide/features.md`

- [ ] **Step 1: Create features.md**

```markdown
# 功能总览

OpenTrading 的所有功能都可以在浏览器 UI 中直接使用。

## 策略回测

支持单股策略回测，内置 A 股市场规则：

- T+1 交易限制
- 涨跌停板（10%/20%）
- 印花税（卖出 0.05%）
- 整手交易（100 股）
- Walk-forward 验证
- 显著性检验（Bootstrap CI + Monte Carlo）

<!-- 截图占位: 回测面板 -->

## 因子研究

截面因子评估体系：

- IC / RankIC / ICIR 时序分析
- IC 衰减曲线
- 分组收益（五分位/十分位）
- 因子相关性矩阵
- 因子中性化（行业中性）
- 多因子合成（AlphaCombiner）

内置技术因子和基本面因子，支持自定义因子开发。

<!-- 截图占位: 因子面板 -->

## 组合回测

多股组合回测引擎：

- 内置策略：轮动、多因子排名、ETF MACD 轮动
- 优化器：均值方差、最小方差、风险平价（Ledoit-Wolf 协方差估计）
- 风控：最大回撤熔断、换手率限制、紧急减仓
- Brinson 归因分析（Carino 几何链接）
- 批量参数搜索

<!-- 截图占位: 组合面板 -->

## MLAlpha

机器学习因子框架：

- 支持 9 种估计器：Ridge、Lasso、LinearRegression、ElasticNet、DecisionTreeRegressor、RandomForestRegressor、GradientBoostingRegressor、LGBMRegressor、XGBRegressor
- 自动 Walk-forward 训练，Purge + Embargo 防前视偏差
- 过拟合诊断：特征重要性稳定性、IS/OOS IC 衰减、换手率

## AI 助手

内置 LLM 编程助手：

- 对话式策略开发
- 代码自动生成、回测、评估
- 自主研究模式：AI 自动迭代优化策略
- 沙箱安全执行

支持 DeepSeek、OpenAI 兼容接口。

<!-- 截图占位: AI 面板 -->

## 模拟实盘

策略部署到模拟实盘：

- 部署门控（比研究更严格的准入检查）
- 每日自动调仓调度器
- OMS 事件溯源 + 幂等重放
- 策略状态跨重启持久化
- 实时监控仪表盘
- 预交易风控引擎
- QMT 券商对接（影子模式 + 小白名单实盘）

<!-- 截图占位: Paper Trading 面板 -->

## 数据层

- 主数据源：Tushare（A 股）
- 备用数据源：AKShare（ETF / 原始数据）
- 本地 Parquet 缓存
- DuckDB 持久化
```

- [ ] **Step 2: Commit**

```bash
git add docs/guide/features.md
git commit -m "docs: add features overview"
```

### Task 16: 用户指南 — faq.md

**Files:**
- Create: `docs/guide/faq.md`

- [ ] **Step 1: Create faq.md**

```markdown
# 常见问题

## 数据源

### Tushare token 怎么获取？

1. 注册 [tushare.pro](https://tushare.pro)
2. 在个人主页获取 token
3. 填入 `.env` 文件的 `TUSHARE_TOKEN` 字段，或在 UI 设置面板中配置

### 需要 Tushare 积分吗？

部分接口需要一定积分。基础行情数据免费可用。如需更多数据（如基本面数据），建议积累积分或购买。

### 没有 Tushare token 能用吗？

仓库内置了少量 ETF 种子数据，可以体验基本的回测功能。完整使用需要配置数据源。

## A 股规则

### 为什么回测结果和其他平台不一样？

OpenTrading 严格模拟 A 股交易规则，包括：

- T+1：当日买入的股票次日才能卖出
- 涨跌停：不能以涨停价买入或跌停价卖出
- 整手交易：每次交易必须是 100 股的整数倍
- 印花税：卖出时收取 0.05%
- 最低佣金：可配置

许多回测平台会简化或忽略这些规则，导致结果偏乐观。

### 支持美股/港股吗？

支持，但市场规则（T+0、无涨跌停、无整手限制）会自动适配。A 股规则不会错误地应用到非 A 股市场。

## 安装问题

### Docker 启动失败

确认 Docker 和 Docker Compose 已安装且正在运行：

```bash
docker --version
docker compose version
```

### C++ 扩展编译失败

C++ 扩展是可选的性能优化，不是必须的。如果编译失败，框架会自动使用纯 Python 实现。

### macOS 上 pytest 段错误

macOS 系统自带的 readline 扩展可能导致 pytest 段错误。使用项目提供的安全脚本：

```bash
scripts/run_pytest_safe.sh tests/ -x -q
```

## 其他

### 为什么包名是 ez-trading 而不是 opentrading？

OpenTrading 是项目品牌名。Python 包名 `ez-trading`、导入名 `import ez` 是历史原因保留的，未来版本可能统一。
```

- [ ] **Step 2: Commit**

```bash
git add docs/guide/faq.md
git commit -m "docs: add FAQ"
```

### Task 17: 最终验证

- [ ] **Step 1: Run full backend test suite**

Run: `scripts/run_pytest_safe.sh tests/ -x -q`
Expected: 3000+ tests pass, 0 failures

- [ ] **Step 2: Run frontend tests**

Run: `cd web && npm test -- --run`
Expected: 96 tests pass

- [ ] **Step 3: Verify no stale doc references**

Run: `git grep "docs/core-changes\|docs/audit\|docs/superpowers" -- '*.py' '*.md' ':!docs/internal/'`
Expected: 无输出

- [ ] **Step 4: Verify no references to deleted strategies in tracked files**

Run: `git grep "EtfSectorSwitch\|EtfRotateCombo\|EtfStockEnhance" -- '*.py'`
Expected: 无输出

- [ ] **Step 5: Verify .env not tracked**

Run: `git ls-files | grep "^\.env$"`
Expected: 无输出

- [ ] **Step 6: Verify all new files exist**

Run: `ls README.md LICENSE CONTRIBUTING.md CHANGELOG.md SECURITY.md docs/guide/installation.md docs/guide/quick-start.md docs/guide/features.md docs/guide/faq.md`
Expected: 所有文件列出，无 "No such file"

- [ ] **Step 7: Verify builtin_strategies.py only has EtfMacdRotation**

Run: `grep "^class " ez/portfolio/builtin_strategies.py`
Expected: 仅一行 `class EtfMacdRotation(PortfolioStrategy):`

- [ ] **Step 8: Final commit if any fixes were needed**

如果前面步骤发现问题并修复，统一提交：

```bash
git add -A
git commit -m "fix: final verification fixes for OpenTrading restructure"
```
