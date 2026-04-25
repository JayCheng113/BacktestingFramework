# 贡献指南

感谢你对 ez-trading 的关注。本文档说明如何在本地搭建开发环境、运行测试以及提交贡献。

---

## 环境搭建

**运行环境要求**

- Python 3.12+
- Node.js 22+
- Git

**安装依赖**

```bash
# 后端（含所有可选依赖和开发工具）
pip install -e ".[all,dev]"

# 前端
cd web && npm install
```

---

## 配置

```bash
cp .env.example .env
```

打开 `.env`，填写必要配置项，最低限度需要：

```
TUSHARE_TOKEN=你的token
```

其余配置项参考 `.env.example` 中的注释说明。

---

## 运行测试

**后端**

macOS 下请使用封装脚本，避免系统 `readline` 扩展在 pytest 启动时触发段错误：

```bash
scripts/run_pytest_safe.sh tests/ -x -q
```

其他平台：

```bash
pytest tests/ -x -q
```

**前端**

```bash
cd web && npm test -- --run
```

提交 PR 前，请确保所有测试通过。

---

## 项目结构

详细架构说明见 [`docs/architecture/system-architecture.md`](docs/architecture/system-architecture.md)。

每个子模块根目录下都有独立的 `CLAUDE.md`，记录该模块的设计约定、扩展点和注意事项，修改前请先阅读。

主要模块一览：

| 模块 | 职责 |
|---|---|
| `ez/core/` | 计算原语（匹配器、时序运算、市场规则） |
| `ez/data/` | 数据摄取、校验、缓存、Parquet/DuckDB 存储 |
| `ez/factor/` | 因子计算与评估 |
| `ez/strategy/` | 单股策略框架与自动注册 |
| `ez/backtest/` | 单股回测引擎、指标、WF验证 |
| `ez/portfolio/` | 组合引擎、优化器、风控、归因、MLAlpha |
| `ez/research/` | 可复用研究管线 |
| `ez/llm/` | LLM 提供商抽象 |
| `ez/agent/` | 自主研究 Agent、沙箱、工具 |
| `ez/live/` | 实盘/模拟交易部署、调度器、监控 |
| `ez/api/` | FastAPI 路由 |
| `web/` | React 19 前端 |

---

## 代码风格

- 遵循各模块现有代码风格
- Python 行长度限制为 **120** 字符
- 不强制使用统一 formatter，但新代码应与周围代码保持一致
- 类型注解优先，公共接口必须有注解

---

## 提交 PR

1. Fork 本仓库
2. 从 `main` 创建功能分支：`git checkout -b feat/your-feature`
3. 在本地完成开发并确保测试全部通过
4. 提交 PR 至 `main`，描述清楚改动目的和影响范围

---

## 高风险文件

以下文件是平台核心语义文件，修改需慎重。如需变更，请先在 `docs/internal/core-changes/` 下提交变更提案并说明原因，经过评审后再实施：

- `ez/types.py`
- `ez/errors.py`
- `ez/config.py`
- `ez/core/matcher.py`
- `ez/core/ts_ops.py`
- `ez/backtest/engine.py`
- `ez/portfolio/engine.py`

对这些文件的改动即使看起来很小，也可能引发跨引擎行为不一致或破坏历史可比性。
