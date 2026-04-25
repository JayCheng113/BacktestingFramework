# OpenTrading — Codex 开源项目优化 Review Prompt

## 项目概览

OpenTrading (包名 ez-trading) 是一个 A 股量化交易研究平台，支持从策略编写、回测、因子研究到模拟实盘的全流程。技术栈：Python 3.12 + FastAPI + DuckDB + React 19 + TypeScript + ECharts + C++ nanobind 扩展。

- **规模**：362 个 Python 文件、44 个 TypeScript 文件、176 个测试文件、3187 个测试全部通过
- **核心包**：`ez/`（13 个子模块）
- **前端**：`web/`（React 19 + Vite）
- **文档**：README.md（中文）、CONTRIBUTING.md、CHANGELOG.md、docs/guide/（4 篇用户指南）、docs/architecture/（架构文档）

## 已完成的优化

以下工作已经完成，**不需要重复做**：

1. **开源标准文件**：README.md、LICENSE (MIT)、CONTRIBUTING.md、CHANGELOG.md、SECURITY.md 已创建
2. **pyproject.toml**：已添加 description、license、authors、keywords、classifiers、urls
3. **文档重组**：docs/ 已分为 guide/（用户指南）、architecture/（架构文档）、internal/（内部开发文档）
4. **Magic numbers**：ez/core/、ez/backtest/、ez/factor/、ez/portfolio/ 的裸数字常量已提取为命名常量
5. **超长方法拆分**：ez/backtest/engine.py 的 _simulate() 已从 342 行拆分为 28 行编排层 + 3 个子方法
6. **Docstrings**：backtest engine 的 __init__/run() 已添加完整 docstring
7. **模块重组**：ez/portfolio/ml/、ez/agent/research/、ez/live/qmt/ 子目录已提取
8. **模块 docstrings**：所有 __init__.py 已添加模块 docstring
9. **字段文档**：ez/types.py dataclass 字段注释、ez/config.py Pydantic Field 描述已添加
10. **TypeScript JSDoc**：web/src/types/index.ts 的 12 个主要 interface 已添加 JSDoc
11. **品牌统一**：CLAUDE.md、CI workflow、Dockerfile、docker-compose.yml、web/package.json 已更新为 OpenTrading

## 你的任务

以优秀开源项目（参考 pandas、scikit-learn、FastAPI、zipline）和现代软件工程的标准，对这个项目进行全面审查和优化。分为以下几个维度：

---

### 维度 1：代码可读性与文档完整性

**目标**：任何一个中级 Python 开发者打开任意文件，都能在 30 秒内理解这个文件做什么、怎么用。

审查范围：
- `ez/data/` — 数据层的每个 .py 文件是否有清晰的模块 docstring 和公开函数/类 docstring
- `ez/research/` — 研究管线的每个 .py 文件
- `ez/llm/` — LLM 抽象层
- `ez/testing/` — 测试工具层
- `ez/agent/` 根目录文件（非 research/ 子目录）— runner.py、sandbox.py、tools.py、gates.py 等
- `ez/live/` 根目录文件（非 qmt/ 子目录）— scheduler.py、paper_engine.py、oms.py、events.py 等
- `ez/api/` — routes/ 下每个路由文件

具体检查：
1. 每个 .py 文件的模块级 docstring 是否存在且有用（不是空的或只有一个词）
2. 每个公开类的 class docstring 是否说明了：它做什么、怎么用、依赖什么
3. 每个公开方法/函数的 docstring 是否说明了：参数、返回值、副作用
4. 复杂算法处是否有"为什么"注释（不是"做什么"注释）
5. 变量命名是否自解释（没有单字母变量名、没有含义不明的缩写）

**约束**：
- 不改逻辑，只加文档
- 已经有良好 docstring 的文件不要重复添加
- ez/core/、ez/backtest/metrics.py、ez/backtest/significance.py、ez/strategy/base.py、ez/factor/base.py 已经达标，跳过

---

### 维度 2：代码结构与工程实践

**目标**：代码组织符合 Python 社区最佳实践，没有 code smell。

审查范围：全项目

具体检查：
1. **超长文件**：找出所有超过 500 行的 .py 文件，评估是否可以合理拆分（但不要为了拆而拆——只有当文件包含多个不相关职责时才拆）
2. **超长方法**：找出所有超过 100 行的方法/函数，评估是否可以提取子方法
3. **import 整洁度**：是否有 `import *`、循环 import、未使用的 import
4. **类型标注**：公开 API 的函数签名是否都有完整的类型标注（参数 + 返回值）
5. **错误处理**：是否有裸 `except:`、过于宽泛的 `except Exception:`、吞掉异常不记录的地方
6. **一致性**：同类文件的代码风格是否一致（比如所有 strategy 的 base 类模式、所有 store 的 CRUD 模式）

**约束**：
- 拆分文件时要同步更新所有 import 和测试
- 不改公开 API 签名
- 每个改动后运行 `scripts/run_pytest_safe.sh tests/ -x -q` 验证

---

### 维度 3：测试质量

**目标**：测试不只是"有"，而是"有用"——覆盖关键路径、边界情况和回归场景。

审查范围：`tests/` 目录

具体检查：
1. **覆盖率盲区**：哪些模块的测试覆盖明显不足？（对比 `ez/` 下的文件数和 `tests/` 下的测试文件数）
2. **测试命名**：测试函数名是否描述了被测行为（`test_sharpe_returns_zero_for_flat_equity` 好于 `test_sharpe`）
3. **测试隔离**：是否有测试依赖执行顺序或共享可变状态
4. **断言质量**：是否有只检查"不报错"但不检查结果正确性的测试
5. **边界测试**：关键函数（引擎、优化器、风控）的边界情况（空数据、单条数据、全 NaN、极端值）是否有测试

**约束**：
- 新增测试放在对应的 `tests/test_<module>/` 目录
- 使用 `scripts/run_pytest_safe.sh` 运行测试（macOS readline 兼容）
- 不修改被测代码来迁就测试

---

### 维度 4：前端代码质量

**目标**：React 组件可维护、类型安全、无明显 code smell。

审查范围：`web/src/`

具体检查：
1. **超大组件**：ValidationPanel.tsx (1316行)、PortfolioRunContent.tsx (1163行)、PortfolioPanel.tsx (971行)、SleeveOptimizationPanel.tsx (853行)、CodeEditor.tsx (734行) — 这些是否应该拆分？如果拆，怎么拆？
2. **类型安全**：是否有 `any` 类型、`as` 类型断言、缺失的 prop 类型
3. **状态管理**：是否有 prop drilling 过深（>3 层）、是否该用 Context
4. **副作用清理**：useEffect 是否都有正确的 cleanup
5. **可访问性**：基本的 aria 属性、键盘导航

**约束**：
- 拆分组件时保持现有功能不变
- 运行 `cd web && npm test -- --run` 验证
- 不改 UI 外观和交互逻辑

---

### 维度 5：安全与配置

**目标**：没有安全隐患、没有硬编码的敏感信息。

审查范围：全项目

具体检查：
1. **敏感信息**：grep 整个仓库查找硬编码的 API key、token、密码、IP 地址
2. **.gitignore 完整性**：是否有应该忽略但被跟踪的文件
3. **依赖安全**：pyproject.toml 的依赖版本是否有已知漏洞（大版本范围是否合理）
4. **CORS 配置**：ez/api/app.py 的 CORS 设置是否过于宽松
5. **沙箱安全**：ez/agent/sandbox.py 的代码执行沙箱是否有逃逸风险

**约束**：
- 发现敏感信息立即清除并加入 .gitignore
- 不改运行时行为

---

## 工作方式

1. **先审后改**：每个维度先完整审查，列出所有发现，按严重度排序，然后再逐一修复
2. **小步提交**：每个独立的修复一个 commit，commit message 用 conventional commits 格式
3. **测试验证**：每次改动后跑相关测试，确认无回归
4. **不做过度工程**：不加不需要的抽象、不引入新依赖、不搭文档站、不加 pre-commit hooks
5. **保持中文**：所有注释和 docstring 用中文（技术术语保持英文）

## 关键文件指引

- `CLAUDE.md` — 项目架构总览（最重要的上下文文件）
- `ez/*/CLAUDE.md` — 每个模块的详细文档
- `docs/architecture/system-architecture.md` — 系统架构
- `docs/architecture/governance.md` — 架构治理规则
- `pyproject.toml` — 项目配置
- `configs/default.yaml` — 运行时默认配置

## 测试命令

```bash
# 后端全量测试（macOS 安全模式）
scripts/run_pytest_safe.sh tests/ -x -q --deselect tests/test_architecture/test_gates.py::TestCoreStability::test_core_package_no_unlisted_python_files

# 前端测试
cd web && npm test -- --run

# 单模块测试
scripts/run_pytest_safe.sh tests/test_backtest/ -v
scripts/run_pytest_safe.sh tests/test_portfolio/ -v
scripts/run_pytest_safe.sh tests/test_live/ -v
```

## 预期产出

1. 每个维度的审查报告（发现列表 + 严重度）
2. 所有修复的 commit（每个 commit 对应一个独立修复）
3. 全量测试通过的确认
