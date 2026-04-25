# V2.8 Autonomous Research Agent — Design Spec

## Goal

Agent 自主探索策略空间 — 从自然语言目标出发，自动生成假设、写代码、批量实验、评估筛选、迭代优化。**Agent 驾驶，人类审核。**

## Architecture

全自治循环 + SSE 实时进度 + cancel 按钮。后台 `asyncio.create_task` 运行编排器，前端通过 SSE 观测进度，完成后查看报告。

任务级串行（同时只跑 1 个研究任务），迭代内顺序执行（瓶颈在 LLM 调用，回测已足够快）。

**不修改任何核心文件。** 全部新建模块 + 复用现有接口。

## Tech Stack

- Backend: Python 3.12, FastAPI, asyncio, DuckDB
- LLM: DeepSeek (P0) via existing OpenAICompatProvider
- Frontend: React 19, TypeScript, ECharts
- Existing infra: RunSpec, Runner, ResearchGate, run_batch, ExperimentStore, sandbox, tool framework

---

## Data Flow

```
用户表单 → ResearchGoal(goal, symbol, dates, budget)
    │
    ├─ 获取数据: data = get_chain().get_kline(symbol, market, period, start, end)
    │  (一次获取，所有迭代复用)
    │
    └─ Loop (LoopController.should_continue):
        │
        ├─ E1 Hypothesis Generator:
        │      await provider.achat(goal + 上轮分析)
        │      → ["RSI超卖反转...", "双均线交叉...", ...] (3-5个)
        │
        ├─ E2 Code Generator (for each hypothesis):
        │      await asyncio.to_thread(chat_sync, provider, [hypothesis])
        │      → sandbox 验证 → strategies/xxx.py
        │      → AST 提取 class_name
        │      失败重试 3 次，仍失败则跳过
        │
        ├─ E3 Batch Execution:
        │      specs = [RunSpec(strategy_name=name, ...) for name in names]
        │      result = await asyncio.to_thread(run_batch, specs, data, config, store)
        │      → pre-filter → full run → gate → rank → persist
        │
        ├─ E4 Analyzer:
        │      summary = summarize(result)  # ≤500 tokens 摘要
        │      analysis = await provider.achat(summary)
        │      → {direction: "收紧阈值", suggestions: [...]}
        │
        └─ E5 Loop Controller:
               state = controller.update(state, result)
               continue? = controller.should_continue(state)
               停止条件: 预算耗尽 / 连续3轮无新通过 / cancelled
    │
    └─ E6 Research Report:
           report = build_report(store, task_id)
           report.summary = await provider.achat(...)  # LLM总结，可选
```

---

## New Files

### Backend (ez/agent/)

| File | Module | Responsibility |
|------|--------|----------------|
| `hypothesis.py` | E1 | `generate_hypotheses(provider, goal) → list[str]` |
| `code_gen.py` | E2 | `generate_strategy_code(provider, hypothesis) → (filename, class_name, error)` |
| `analyzer.py` | E4 | `analyze_results(provider, batch_result, goal) → AnalysisResult` |
| `loop_controller.py` | E5 | `LoopController.should_continue(state) → (bool, reason)` |
| `research_report.py` | E6 | `build_report(store, task_id) → ResearchReport` |
| `research_store.py` | Persistence | `ResearchStore` — research_tasks + research_iterations tables |
| `research_runner.py` | Orchestrator | `run_research_task(goal, config) → task_id` — coordinates E1-E6 |

### API (ez/api/routes/)

| File | Endpoints |
|------|-----------|
| `research.py` | POST start, GET list, GET detail, POST cancel, GET stream (SSE) |

### Frontend (web/src/)

| File | Component |
|------|-----------|
| `components/ResearchPanel.tsx` | Goal form + task list + progress stream + report view |

---

## Module Interfaces

### E1: Hypothesis Generator (`hypothesis.py`)

```python
@dataclass
class ResearchGoal:
    description: str          # "探索A股动量策略，Sharpe>1，回撤<20%"
    market: str = "cn_stock"
    symbol: str = "000001.SZ"
    period: str = "daily"
    start_date: date = ...    # 默认3年前
    end_date: date = ...      # 默认今天
    n_hypotheses: int = 5

async def generate_hypotheses(
    provider: LLMProvider,
    goal: ResearchGoal,
    previous_analysis: str = "",  # E4上轮输出，引导方向
) -> list[str]:
    """调 provider.achat() 生成 N 个策略假设。

    System prompt: 量化研究员角色，已知因子列表 (MA/EMA/RSI/MACD/BOLL/ATR/OBV/VWAP)，
    输出格式为 JSON array of strings。

    Returns: ["RSI<25买入...", "MACD金叉+成交量放大...", ...]
    """
```

### E2: Code Generator (`code_gen.py`)

```python
async def generate_strategy_code(
    provider: LLMProvider,
    hypothesis: str,
    max_retries: int = 3,
) -> tuple[str | None, str | None, str | None]:
    """生成策略代码并验证。

    Returns: (filename, class_name, error)
    - 成功: ("rsi_reversal.py", "RSIReversal", None)
    - 失败: (None, None, "contract test failed: ...")

    实现:
    1. 构建 messages = [system_prompt, user: hypothesis]
    2. await asyncio.to_thread(chat_sync, provider, messages)
       chat_sync 会自动: LLM写代码 → 调 create_strategy tool → sandbox验证
    3. 检查 tool result 是否 success
    4. 失败: 追加错误消息，递归重试 (≤max_retries)
    5. 成功: AST 解析文件提取 Strategy 子类名 (复用 list_user_strategies 逻辑)
    """
```

### E4: Analyzer (`analyzer.py`)

```python
@dataclass
class AnalysisResult:
    direction: str        # "下轮方向建议"
    suggestions: list[str]  # 具体改进建议
    passed_count: int
    failed_count: int
    best_sharpe: float
    key_failure_reasons: list[str]  # 主要失败原因

async def analyze_results(
    provider: LLMProvider,
    batch_result: BatchResult,
    goal: ResearchGoal,
    hypothesis_texts: list[str],
) -> AnalysisResult:
    """分析本轮结果，提出下轮方向。

    发给 LLM 的是摘要 (≤500 tokens)，不是全量 BatchResult:
    - N 个假设，M 个通过 gate
    - Top 3 Sharpe 值
    - 采样 5 条 gate_reason (最常见的失败原因)
    - 当前目标回顾

    LLM 输出 JSON: {direction, suggestions}
    """
```

### E5: Loop Controller (`loop_controller.py`)

```python
@dataclass
class LoopConfig:
    max_iterations: int = 10
    max_specs: int = 500          # 总回测次数上限
    max_llm_calls: int = 100      # LLM 调用次数上限
    no_improve_limit: int = 3     # 连续 N 轮无新通过策略则停止

@dataclass
class LoopState:
    iteration: int = 0
    specs_executed: int = 0
    llm_calls: int = 0
    best_sharpe: float = -inf
    gate_passed_total: int = 0
    no_improve_streak: int = 0    # 连续无新通过轮数
    cancelled: bool = False       # cancel endpoint 设置

class LoopController:
    def __init__(self, config: LoopConfig): ...

    def should_continue(self, state: LoopState) -> tuple[bool, str]:
        """返回 (是否继续, 原因)。
        停止条件 (任一触发):
        - cancelled → "用户取消"
        - iteration >= max_iterations → "达到最大轮次"
        - specs_executed >= max_specs → "达到回测预算上限"
        - llm_calls >= max_llm_calls → "达到LLM调用上限"
        - no_improve_streak >= no_improve_limit → "连续N轮无新通过策略"
        """

    def update(self, state: LoopState, batch_result: BatchResult,
               llm_calls_this_round: int) -> LoopState:
        """更新状态。
        - specs_executed += batch_result.executed
        - llm_calls += llm_calls_this_round
        - 本轮 passed = len(batch_result.passed)
        - 有新 passed → no_improve_streak = 0, 更新 best_sharpe
        - 无新 passed → no_improve_streak += 1
        """
```

### E6: Research Report (`research_report.py`)

```python
@dataclass
class ResearchReport:
    task_id: str
    goal: str
    config: dict              # LoopConfig 序列化
    status: str               # completed / cancelled / failed
    iterations: list[dict]    # [{hypothesis_texts, tried, passed, best_sharpe, analysis}]
    best_strategies: list[dict]  # Top 5: {strategy_name, sharpe, max_dd, gate_reasons}
    total_specs: int
    total_passed: int
    summary: str              # LLM 生成总结 (可选，失败则为空)
    duration_sec: float
    stop_reason: str          # "预算耗尽" / "收敛" / "取消"

async def build_report(
    provider: LLMProvider | None,  # None = 跳过 LLM 总结
    store: ResearchStore,
    exp_store: ExperimentStore,
    task_id: str,
) -> ResearchReport:
    """从 DB 聚合所有迭代数据，生成报告。
    - 查 research_iterations 表获取每轮数据
    - 用 spec_ids JOIN experiment_runs 获取 Top 5 策略详情
    - 可选: 调 provider.achat() 生成自然语言总结
    """
```

### Research Store (`research_store.py`)

```python
class ResearchStore:
    """研究任务持久化。共享 ExperimentStore 的 DuckDB 连接。"""

    def __init__(self, conn: duckdb.DuckDBPyConnection): ...
    # 创建 2 张表:
    #
    # research_tasks:
    #   task_id TEXT PRIMARY KEY,
    #   goal TEXT,
    #   config TEXT (JSON: LoopConfig),
    #   status TEXT (pending/running/completed/cancelled/failed),
    #   created_at TIMESTAMP,
    #   completed_at TIMESTAMP,
    #   stop_reason TEXT,
    #   summary TEXT,
    #   error TEXT
    #
    # research_iterations:
    #   task_id TEXT,
    #   iteration INTEGER,
    #   hypotheses TEXT (JSON array of strings),
    #   strategies_tried INTEGER,
    #   strategies_passed INTEGER,
    #   best_sharpe DOUBLE,
    #   analysis TEXT (JSON: AnalysisResult),
    #   spec_ids TEXT (JSON array of spec_id strings),
    #   created_at TIMESTAMP,
    #   PRIMARY KEY (task_id, iteration)

    def save_task(self, task: dict) -> None: ...
    def update_task_status(self, task_id: str, status: str,
                           stop_reason: str = "", summary: str = "",
                           error: str = "") -> None: ...
    def save_iteration(self, iteration: dict) -> None: ...
    def get_task(self, task_id: str) -> dict | None: ...
    def list_tasks(self, limit: int = 50, offset: int = 0) -> list[dict]: ...
    def get_iterations(self, task_id: str) -> list[dict]: ...
```

### Research Runner — Orchestrator (`research_runner.py`)

```python
# 内存事件队列 (SSE 进度推送)
_running_tasks: dict[str, dict] = {}
# task_id → {"events": list[dict], "done": bool, "state": LoopState}

async def run_research_task(
    goal: ResearchGoal,
    loop_config: LoopConfig = LoopConfig(),
    gate_config: GateConfig = GateConfig(),
) -> str:
    """主编排器。返回 task_id。

    在 asyncio.create_task 中后台运行:
    1. 初始化: task_id, ResearchStore, ExperimentStore, LoopController
    2. 获取数据: await asyncio.to_thread(get_chain().get_kline, ...)
    3. Loop:
       a. E1: hypotheses = await generate_hypotheses(provider, goal, prev_analysis)
       b. E2: for h in hypotheses:
              (filename, class_name, err) = await generate_strategy_code(provider, h)
       c. E3: specs → await asyncio.to_thread(run_batch, specs, data, ...)
       d. E4: analysis = await analyze_results(provider, batch_result, goal, hypotheses)
       e. E5: state = controller.update(state, batch_result, llm_calls)
       f. 持久化: save_iteration(...)
       g. 推送 SSE events
       h. if not controller.should_continue(state): break
    4. E6: report = await build_report(provider, store, exp_store, task_id)
    5. 标记完成
    """

def cancel_task(task_id: str) -> bool:
    """设置 cancelled flag，loop 下轮检查时退出。"""

def get_task_events(task_id: str) -> dict | None:
    """返回内存中的事件队列 (SSE endpoint 用)。"""
```

---

## API Endpoints (`ez/api/routes/research.py`)

```
POST /api/research/start
  Body: {goal, symbol?, market?, period?, start_date?, end_date?,
         max_iterations?, max_specs?, max_llm_calls?,
         gate_min_sharpe?, gate_max_drawdown?}
  Returns: {task_id, status: "started"}

GET /api/research/tasks
  Query: limit=50, offset=0
  Returns: [{task_id, goal, status, created_at, total_passed, best_sharpe}]

GET /api/research/tasks/{task_id}
  Returns: {task_id, goal, config, status, iterations: [...],
            best_strategies: [...], summary, duration_sec, stop_reason}

POST /api/research/tasks/{task_id}/cancel
  Returns: {status: "cancelled"} | 404

GET /api/research/tasks/{task_id}/stream
  Returns: SSE stream
```

### SSE Event Types

```
iteration_start   {iteration: 1, max_iterations: 10}
hypothesis        {index: 0, total: 5, text: "RSI超卖反转..."}
code_success      {index: 0, filename: "rsi_reversal.py", class_name: "RSIReversal"}
code_failed       {index: 1, hypothesis: "...", error: "contract test failed", retries: 3}
batch_start       {total_specs: 4}
batch_complete    {executed: 4, passed: 2, best_sharpe: 1.2}
analysis          {direction: "收紧RSI阈值", passed: 2, failed: 2}
iteration_end     {iteration: 1, cumulative_passed: 2, cumulative_specs: 4}
task_complete     {total_passed: 8, best_sharpe: 1.5, stop_reason: "收敛"}
task_failed       {error: "..."}
task_cancelled    {}
```

---

## Frontend (`ResearchPanel.tsx`)

### Layout

1. **目标输入表单** (顶部):
   - 目标描述 textarea
   - 股票代码 input (默认 000001.SZ)
   - 日期范围 DatePicker (默认近3年)
   - 折叠面板: 预算 (max_iterations, max_specs) + Gate (min_sharpe, max_drawdown)
   - "开始研究" 按钮

2. **任务列表** (左侧/主区域):
   - 表格: 任务ID | 目标摘要 | 状态 | 通过数 | 最佳Sharpe | 时间
   - 状态徽章: 运行中(蓝) / 已完成(绿) / 已取消(灰) / 失败(红)

3. **运行中任务** (展开/详情):
   - 进度条: iteration / max_iterations
   - SSE 实时日志 (参照 ChatPanel 模式)
   - 每轮卡片: 假设列表 + 通过/失败计数 + 分析方向
   - "取消" 按钮

4. **已完成任务报告** (展开/详情):
   - 总结区: LLM 总结 + 关键指标 (总测试/通过/Sharpe)
   - 迭代时间线: 每轮通过数折线图
   - Top 5 策略表: 策略名 | Sharpe | MaxDD | 交易数 | Gate结果
   - 停止原因

---

## Persistence Model

```
┌─────────────────────────────────────────┐
│ research_tasks (新)                      │
│ - task_id, goal, config, status          │
│ - created_at, completed_at               │
│ - stop_reason, summary, error            │
└───────────────┬─────────────────────────┘
                │ 1:N
┌───────────────▼─────────────────────────┐
│ research_iterations (新)                 │
│ - task_id + iteration (PK)              │
│ - hypotheses, strategies_tried/passed    │
│ - best_sharpe, analysis                  │
│ - spec_ids (JSON array) ────────────────┼──┐
└─────────────────────────────────────────┘  │
                                              │ spec_id JOIN
┌─────────────────────────────────────────┐  │
│ experiment_specs (已有)                   │◄─┘
│ experiment_runs (已有)                    │
│ completed_specs (已有)                    │
└─────────────────────────────────────────┘
```

---

## Budget Control

| 参数 | 默认值 | 含义 |
|------|--------|------|
| max_iterations | 10 | 最大迭代轮数 |
| max_specs | 500 | 总回测次数上限 |
| max_llm_calls | 100 | LLM API 调用次数上限 |
| no_improve_limit | 3 | 连续 N 轮无新 gate-passed 策略则停止 |

"改善" = 本轮有 ≥1 个新的 gate-passed 策略。

---

## Error Handling

| 场景 | 处理 |
|------|------|
| LLM 超时/网络错误 | 重试 1 次，仍失败则跳过当前假设 |
| Contract test 失败 | LLM 修复重试 (≤3次)，仍失败则跳过 |
| 全部假设都失败 (0 策略) | 记录为空轮次，Analyzer 调整方向，no_improve +1 |
| 数据获取失败 | 任务标记 failed，返回错误 |
| 进程中断 | 任务标记 failed (V2.8.0 不支持恢复) |
| LLM 返回格式错误 | 尝试宽松解析 (正则提取)，失败则重试 1 次 |
| Report LLM 总结失败 | summary 设为空字符串，报告仍然有效 |

---

## Testing Strategy

### Unit Tests (mock LLM)
- `test_hypothesis.py`: 解析 JSON/markdown 格式的假设列表
- `test_code_gen.py`: 成功/失败/重试逻辑，AST 类名提取
- `test_analyzer.py`: 摘要构建，LLM 响应解析
- `test_loop_controller.py`: 预算检查，收敛检测，所有停止条件
- `test_research_store.py`: CRUD，JSON 字段，边界情况
- `test_research_report.py`: 聚合逻辑，Top N 排序

### Integration Tests (mock LLM + real sandbox + real batch_runner)
- `test_research_runner.py`: 完整 pipeline 端到端
  - 目标 → 假设 → 代码 → 回测 → 分析 → 报告
  - 预算耗尽停止
  - cancel 中断
  - 幂等性 (相同目标不重复回测)

### API Tests
- `test_research_api.py`: start/list/detail/cancel/stream endpoints

---

## Exit Gate

- [ ] 从自然语言目标到可运行策略的全自主链路 (E2E test)
- [ ] 多轮迭代有可观测的策略质量提升 (Analyzer 输出 + iteration sharpe trend)
- [ ] 预算控制生效 (max_iterations + max_specs + max_llm_calls + no_improve_limit)
- [ ] 研究报告包含完整审计轨迹 (每轮假设/结果/分析)
- [ ] Tests ≥ 921

---

## Dependencies

- **Upstream (只读使用)**: RunSpec, Runner, ResearchGate, run_batch, ExperimentStore, sandbox, tool framework, LLMProvider, DataProviderChain
- **Downstream**: ez/api/routes/research.py, web/ResearchPanel.tsx
- **不修改核心文件**: 全部新建模块

## Scope Exclusions (V2.8.0 不做)

- 多股票/组合回测 → V2.9
- 进程恢复 (crash recovery) → V2.8.1
- 参数化网格搜索 (由 Analyzer 自然语言指导替代)
- 暂停/恢复 UI → V2.8.1
- 策略间对比可视化 → V2.8.1
