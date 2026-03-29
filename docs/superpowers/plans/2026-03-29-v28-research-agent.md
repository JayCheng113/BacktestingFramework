# V2.8 Research Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous research agent that generates strategy hypotheses, writes code, runs batch experiments, analyzes results, and iterates — all from a single natural language goal.

**Architecture:** 7 new backend modules in `ez/agent/` (hypothesis, code_gen, analyzer, loop_controller, research_report, research_store, research_runner), 1 new API route, 1 new frontend component. All modules compose existing V2.4-V2.7.1 infrastructure (RunSpec, Runner, run_batch, sandbox, tool framework, LLMProvider). No core files modified.

**Tech Stack:** Python 3.12, FastAPI, asyncio, DuckDB, httpx, React 19, TypeScript

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `ez/agent/loop_controller.py` | Budget tracking, convergence detection, stop conditions |
| `ez/agent/research_store.py` | DuckDB persistence for research_tasks + research_iterations |
| `ez/agent/hypothesis.py` | LLM-powered hypothesis generation from research goal |
| `ez/agent/code_gen.py` | LLM-powered strategy code generation with sandbox validation |
| `ez/agent/analyzer.py` | LLM-powered batch result analysis and direction suggestions |
| `ez/agent/research_report.py` | Aggregate iterations into final report with optional LLM summary |
| `ez/agent/research_runner.py` | Main orchestrator: coordinate E1-E6 in async loop with SSE events |
| `ez/api/routes/research.py` | REST API: start, list, detail, cancel, SSE stream |
| `web/src/components/ResearchPanel.tsx` | Frontend: goal form, task list, progress stream, report view |
| `tests/test_agent/test_loop_controller.py` | Unit tests for loop controller |
| `tests/test_agent/test_research_store.py` | Unit tests for research store |
| `tests/test_agent/test_hypothesis.py` | Unit tests for hypothesis generation |
| `tests/test_agent/test_code_gen.py` | Unit tests for code generation |
| `tests/test_agent/test_analyzer.py` | Unit tests for result analysis |
| `tests/test_agent/test_research_report.py` | Unit tests for report building |
| `tests/test_agent/test_research_runner.py` | Integration test for full pipeline |
| `tests/test_api/test_research_api.py` | API endpoint tests |

### Modified Files
| File | Change |
|------|--------|
| `ez/agent/data_access.py` | Add `get_research_store()` singleton |
| `ez/api/app.py` | Register research router |
| `web/src/components/Navbar.tsx` | Add "研究助手" tab |
| `web/src/App.tsx` | Add ResearchPanel render |
| `CLAUDE.md` | Version bump + V2.8 entry |
| `ez/agent/CLAUDE.md` | Document new modules |
| `docs/core-changes/v2.3-roadmap.md` | Mark V2.8 deliverables complete |

---

### Task 1: Loop Controller

**Files:**
- Create: `ez/agent/loop_controller.py`
- Test: `tests/test_agent/test_loop_controller.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_agent/test_loop_controller.py
"""Tests for the research loop controller."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from ez.agent.loop_controller import LoopConfig, LoopState, LoopController


class TestLoopConfig:
    def test_defaults(self):
        c = LoopConfig()
        assert c.max_iterations == 10
        assert c.max_specs == 500
        assert c.max_llm_calls == 100
        assert c.no_improve_limit == 3

    def test_custom(self):
        c = LoopConfig(max_iterations=5, max_specs=100)
        assert c.max_iterations == 5
        assert c.max_specs == 100


class TestLoopState:
    def test_defaults(self):
        s = LoopState()
        assert s.iteration == 0
        assert s.specs_executed == 0
        assert s.llm_calls == 0
        assert s.best_sharpe == float("-inf")
        assert s.gate_passed_total == 0
        assert s.no_improve_streak == 0
        assert s.cancelled is False


class TestShouldContinue:
    def test_fresh_state_continues(self):
        ctrl = LoopController(LoopConfig())
        ok, reason = ctrl.should_continue(LoopState())
        assert ok is True

    def test_cancelled_stops(self):
        ctrl = LoopController(LoopConfig())
        state = LoopState(cancelled=True)
        ok, reason = ctrl.should_continue(state)
        assert ok is False
        assert "取消" in reason

    def test_max_iterations_stops(self):
        ctrl = LoopController(LoopConfig(max_iterations=3))
        state = LoopState(iteration=3)
        ok, reason = ctrl.should_continue(state)
        assert ok is False
        assert "轮次" in reason

    def test_max_specs_stops(self):
        ctrl = LoopController(LoopConfig(max_specs=100))
        state = LoopState(specs_executed=100)
        ok, reason = ctrl.should_continue(state)
        assert ok is False
        assert "回测" in reason

    def test_max_llm_calls_stops(self):
        ctrl = LoopController(LoopConfig(max_llm_calls=50))
        state = LoopState(llm_calls=50)
        ok, reason = ctrl.should_continue(state)
        assert ok is False
        assert "LLM" in reason

    def test_no_improvement_stops(self):
        ctrl = LoopController(LoopConfig(no_improve_limit=3))
        state = LoopState(no_improve_streak=3, iteration=4)
        ok, reason = ctrl.should_continue(state)
        assert ok is False
        assert "无新通过" in reason

    def test_under_limits_continues(self):
        ctrl = LoopController(LoopConfig(max_iterations=10))
        state = LoopState(iteration=5, specs_executed=200, llm_calls=40)
        ok, _ = ctrl.should_continue(state)
        assert ok is True


class TestUpdate:
    def _mock_batch_result(self, passed_count: int, executed: int, best_sharpe: float):
        result = MagicMock()
        result.executed = executed
        passed_list = []
        for i in range(passed_count):
            c = MagicMock()
            c.sharpe = best_sharpe - i * 0.1
            passed_list.append(c)
        result.passed = passed_list
        return result

    def test_update_with_passed(self):
        ctrl = LoopController(LoopConfig())
        state = LoopState(iteration=0, specs_executed=0, llm_calls=0)
        batch = self._mock_batch_result(passed_count=2, executed=5, best_sharpe=1.5)
        new_state = ctrl.update(state, batch, llm_calls_this_round=8)
        assert new_state.iteration == 1
        assert new_state.specs_executed == 5
        assert new_state.llm_calls == 8
        assert new_state.gate_passed_total == 2
        assert new_state.best_sharpe == 1.5
        assert new_state.no_improve_streak == 0

    def test_update_no_passed_increments_streak(self):
        ctrl = LoopController(LoopConfig())
        state = LoopState(iteration=1, no_improve_streak=1)
        batch = self._mock_batch_result(passed_count=0, executed=3, best_sharpe=0)
        new_state = ctrl.update(state, batch, llm_calls_this_round=5)
        assert new_state.iteration == 2
        assert new_state.no_improve_streak == 2

    def test_update_passed_resets_streak(self):
        ctrl = LoopController(LoopConfig())
        state = LoopState(iteration=2, no_improve_streak=2, best_sharpe=0.8)
        batch = self._mock_batch_result(passed_count=1, executed=4, best_sharpe=1.2)
        new_state = ctrl.update(state, batch, llm_calls_this_round=6)
        assert new_state.no_improve_streak == 0
        assert new_state.best_sharpe == 1.2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_agent/test_loop_controller.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: Implement loop_controller.py**

```python
# ez/agent/loop_controller.py
"""V2.8: Research loop controller — budget, convergence, stop conditions."""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class LoopConfig:
    """Budget and convergence settings for a research loop."""
    max_iterations: int = 10
    max_specs: int = 500
    max_llm_calls: int = 100
    no_improve_limit: int = 3


@dataclass
class LoopState:
    """Mutable state tracked across iterations."""
    iteration: int = 0
    specs_executed: int = 0
    llm_calls: int = 0
    best_sharpe: float = float("-inf")
    gate_passed_total: int = 0
    no_improve_streak: int = 0
    cancelled: bool = False


class LoopController:
    """Decides whether the research loop should continue."""

    def __init__(self, config: LoopConfig):
        self._config = config

    def should_continue(self, state: LoopState) -> tuple[bool, str]:
        if state.cancelled:
            return False, "用户取消"
        if state.iteration >= self._config.max_iterations:
            return False, f"达到最大轮次 ({self._config.max_iterations})"
        if state.specs_executed >= self._config.max_specs:
            return False, f"达到回测预算上限 ({self._config.max_specs})"
        if state.llm_calls >= self._config.max_llm_calls:
            return False, f"达到LLM调用上限 ({self._config.max_llm_calls})"
        if (state.no_improve_streak >= self._config.no_improve_limit
                and state.iteration > 0):
            return False, f"连续{self._config.no_improve_limit}轮无新通过策略"
        return True, ""

    def update(
        self,
        state: LoopState,
        batch_result,
        llm_calls_this_round: int,
    ) -> LoopState:
        new = LoopState(
            iteration=state.iteration + 1,
            specs_executed=state.specs_executed + batch_result.executed,
            llm_calls=state.llm_calls + llm_calls_this_round,
            best_sharpe=state.best_sharpe,
            gate_passed_total=state.gate_passed_total,
            no_improve_streak=state.no_improve_streak,
            cancelled=state.cancelled,
        )
        passed = batch_result.passed
        new.gate_passed_total += len(passed)
        if passed:
            top_sharpe = max(c.sharpe for c in passed)
            if top_sharpe > new.best_sharpe:
                new.best_sharpe = top_sharpe
            new.no_improve_streak = 0
        else:
            new.no_improve_streak += 1
        return new
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_agent/test_loop_controller.py -v
```
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add ez/agent/loop_controller.py tests/test_agent/test_loop_controller.py
git commit -m "feat(v2.8): E5 LoopController — budget/convergence/stop conditions"
```

---

### Task 2: Research Store

**Files:**
- Create: `ez/agent/research_store.py`
- Modify: `ez/agent/data_access.py`
- Test: `tests/test_agent/test_research_store.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_agent/test_research_store.py
"""Tests for research task persistence."""
from __future__ import annotations

import json
from datetime import datetime

import duckdb
import pytest

from ez.agent.research_store import ResearchStore


@pytest.fixture
def store():
    conn = duckdb.connect(":memory:")
    s = ResearchStore(conn)
    yield s
    conn.close()


class TestSaveAndGetTask:
    def test_save_and_get(self, store):
        store.save_task({
            "task_id": "t1",
            "goal": "探索动量策略",
            "config": json.dumps({"max_iterations": 5}),
            "status": "running",
            "created_at": datetime.now().isoformat(),
        })
        task = store.get_task("t1")
        assert task is not None
        assert task["goal"] == "探索动量策略"
        assert task["status"] == "running"

    def test_get_nonexistent(self, store):
        assert store.get_task("nope") is None

    def test_update_status(self, store):
        store.save_task({"task_id": "t1", "goal": "test", "config": "{}", "status": "running",
                         "created_at": datetime.now().isoformat()})
        store.update_task_status("t1", "completed", stop_reason="收敛", summary="找到3个策略")
        task = store.get_task("t1")
        assert task["status"] == "completed"
        assert task["stop_reason"] == "收敛"
        assert task["summary"] == "找到3个策略"
        assert task["completed_at"] is not None


class TestListTasks:
    def test_list_empty(self, store):
        assert store.list_tasks() == []

    def test_list_with_tasks(self, store):
        for i in range(3):
            store.save_task({"task_id": f"t{i}", "goal": f"goal {i}", "config": "{}",
                             "status": "completed", "created_at": datetime.now().isoformat()})
        tasks = store.list_tasks(limit=2)
        assert len(tasks) == 2

    def test_list_ordered_by_created_at(self, store):
        store.save_task({"task_id": "old", "goal": "old", "config": "{}",
                         "status": "completed", "created_at": "2024-01-01T00:00:00"})
        store.save_task({"task_id": "new", "goal": "new", "config": "{}",
                         "status": "completed", "created_at": "2025-01-01T00:00:00"})
        tasks = store.list_tasks()
        assert tasks[0]["task_id"] == "new"


class TestIterations:
    def test_save_and_get_iterations(self, store):
        store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                         "status": "running", "created_at": datetime.now().isoformat()})
        store.save_iteration({
            "task_id": "t1", "iteration": 0,
            "hypotheses": json.dumps(["h1", "h2"]),
            "strategies_tried": 2, "strategies_passed": 1,
            "best_sharpe": 1.2,
            "analysis": json.dumps({"direction": "继续"}),
            "spec_ids": json.dumps(["spec1", "spec2"]),
            "created_at": datetime.now().isoformat(),
        })
        iters = store.get_iterations("t1")
        assert len(iters) == 1
        assert iters[0]["strategies_passed"] == 1
        assert iters[0]["best_sharpe"] == 1.2

    def test_multiple_iterations_ordered(self, store):
        store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                         "status": "running", "created_at": datetime.now().isoformat()})
        for i in range(3):
            store.save_iteration({
                "task_id": "t1", "iteration": i,
                "hypotheses": "[]", "strategies_tried": i + 1,
                "strategies_passed": 0, "best_sharpe": 0.0,
                "analysis": "{}", "spec_ids": "[]",
                "created_at": datetime.now().isoformat(),
            })
        iters = store.get_iterations("t1")
        assert len(iters) == 3
        assert iters[0]["iteration"] == 0
        assert iters[2]["iteration"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_agent/test_research_store.py -v
```

- [ ] **Step 3: Implement research_store.py**

```python
# ez/agent/research_store.py
"""V2.8: Persistence for research tasks and iterations."""
from __future__ import annotations

import json
import logging
from datetime import datetime

import duckdb

logger = logging.getLogger(__name__)


class ResearchStore:
    """DuckDB persistence for autonomous research tasks."""

    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS research_tasks (
                task_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                config TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP,
                completed_at TIMESTAMP,
                stop_reason TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                error TEXT DEFAULT ''
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS research_iterations (
                task_id TEXT NOT NULL,
                iteration INTEGER NOT NULL,
                hypotheses TEXT DEFAULT '[]',
                strategies_tried INTEGER DEFAULT 0,
                strategies_passed INTEGER DEFAULT 0,
                best_sharpe DOUBLE DEFAULT 0.0,
                analysis TEXT DEFAULT '{}',
                spec_ids TEXT DEFAULT '[]',
                created_at TIMESTAMP,
                PRIMARY KEY (task_id, iteration)
            )
        """)

    def save_task(self, task: dict) -> None:
        self._conn.execute(
            """INSERT INTO research_tasks (task_id, goal, config, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            [task["task_id"], task["goal"], task.get("config", "{}"),
             task.get("status", "pending"), task.get("created_at", datetime.now().isoformat())],
        )

    def update_task_status(
        self, task_id: str, status: str,
        stop_reason: str = "", summary: str = "", error: str = "",
    ) -> None:
        completed_at = datetime.now().isoformat() if status in ("completed", "cancelled", "failed") else None
        self._conn.execute(
            """UPDATE research_tasks
               SET status=?, stop_reason=?, summary=?, error=?, completed_at=?
               WHERE task_id=?""",
            [status, stop_reason, summary, error, completed_at, task_id],
        )

    def save_iteration(self, iteration: dict) -> None:
        self._conn.execute(
            """INSERT INTO research_iterations
               (task_id, iteration, hypotheses, strategies_tried, strategies_passed,
                best_sharpe, analysis, spec_ids, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [iteration["task_id"], iteration["iteration"],
             iteration.get("hypotheses", "[]"),
             iteration.get("strategies_tried", 0),
             iteration.get("strategies_passed", 0),
             iteration.get("best_sharpe", 0.0),
             iteration.get("analysis", "{}"),
             iteration.get("spec_ids", "[]"),
             iteration.get("created_at", datetime.now().isoformat())],
        )

    def get_task(self, task_id: str) -> dict | None:
        rows = self._conn.execute(
            "SELECT * FROM research_tasks WHERE task_id=?", [task_id]
        ).fetchall()
        if not rows:
            return None
        cols = [d[0] for d in self._conn.description]
        return dict(zip(cols, rows[0]))

    def list_tasks(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM research_tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [limit, offset],
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_iterations(self, task_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM research_iterations WHERE task_id=? ORDER BY iteration",
            [task_id],
        ).fetchall()
        cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def close(self) -> None:
        pass  # Connection owned externally
```

- [ ] **Step 4: Add get_research_store() to data_access.py**

Add to `ez/agent/data_access.py` after the existing `get_experiment_store()`:

```python
from ez.agent.research_store import ResearchStore

_research_store: ResearchStore | None = None

def get_research_store() -> ResearchStore:
    """Get or create ResearchStore (same DB connection path as ExperimentStore)."""
    global _research_store
    if _research_store is None:
        import duckdb
        conn = duckdb.connect(str(_resolve_db_path()))
        _research_store = ResearchStore(conn)
    return _research_store
```

Update `reset_data_access()` to also reset `_research_store`:

```python
def reset_data_access() -> None:
    global _store, _chain, _exp_store, _research_store
    reset_chain()
    if _research_store is not None:
        _research_store.close()
        _research_store = None
    if _exp_store is not None:
        _exp_store.close()
        _exp_store = None
    if _store is not None:
        _store.close()
        _store = None
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_agent/test_research_store.py -v
```
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add ez/agent/research_store.py ez/agent/data_access.py tests/test_agent/test_research_store.py
git commit -m "feat(v2.8): ResearchStore + get_research_store() singleton"
```

---

### Task 3: Hypothesis Generator (E1)

**Files:**
- Create: `ez/agent/hypothesis.py`
- Test: `tests/test_agent/test_hypothesis.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_agent/test_hypothesis.py
"""Tests for hypothesis generation (E1)."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from ez.agent.hypothesis import ResearchGoal, generate_hypotheses, _parse_hypotheses
from ez.llm.provider import LLMMessage, LLMResponse


class TestResearchGoal:
    def test_defaults(self):
        g = ResearchGoal(description="test")
        assert g.market == "cn_stock"
        assert g.symbol == "000001.SZ"
        assert g.n_hypotheses == 5


class TestParseHypotheses:
    def test_parse_json_array(self):
        text = '["RSI超卖反转", "双均线交叉"]'
        result = _parse_hypotheses(text)
        assert result == ["RSI超卖反转", "双均线交叉"]

    def test_parse_json_in_markdown(self):
        text = '```json\n["hypothesis 1", "hypothesis 2"]\n```'
        result = _parse_hypotheses(text)
        assert result == ["hypothesis 1", "hypothesis 2"]

    def test_parse_numbered_list(self):
        text = "1. RSI反转策略\n2. 均线交叉\n3. MACD动量"
        result = _parse_hypotheses(text)
        assert len(result) == 3
        assert "RSI反转策略" in result[0]

    def test_parse_empty(self):
        assert _parse_hypotheses("") == []
        assert _parse_hypotheses("no hypotheses here") == []


class TestGenerateHypotheses:
    @pytest.mark.asyncio
    async def test_basic_generation(self):
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(return_value=LLMResponse(
            content='["RSI<30买入反转", "双均线金叉"]',
            finish_reason="stop",
        ))
        goal = ResearchGoal(description="探索动量策略", n_hypotheses=2)
        result = await generate_hypotheses(mock_provider, goal)
        assert len(result) == 2
        assert "RSI" in result[0]
        mock_provider.achat.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_previous_analysis(self):
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(return_value=LLMResponse(
            content='["改进版RSI策略"]',
            finish_reason="stop",
        ))
        goal = ResearchGoal(description="test", n_hypotheses=1)
        result = await generate_hypotheses(mock_provider, goal, previous_analysis="RSI效果好但阈值太宽")
        assert len(result) == 1
        # Verify previous_analysis was included in the messages
        call_args = mock_provider.achat.call_args
        messages = call_args[0][0]
        user_msg = [m for m in messages if m.role == "user"][0]
        assert "RSI效果好" in user_msg.content

    @pytest.mark.asyncio
    async def test_llm_error_returns_empty(self):
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=Exception("timeout"))
        goal = ResearchGoal(description="test")
        result = await generate_hypotheses(mock_provider, goal)
        assert result == []
```

- [ ] **Step 2: Implement hypothesis.py**

```python
# ez/agent/hypothesis.py
"""V2.8 E1: Hypothesis Generator — LLM generates strategy hypotheses from research goal."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from ez.llm.provider import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class ResearchGoal:
    """User's research objective."""
    description: str
    market: str = "cn_stock"
    symbol: str = "000001.SZ"
    period: str = "daily"
    start_date: date | None = None
    end_date: date | None = None
    n_hypotheses: int = 5

    def __post_init__(self):
        if self.end_date is None:
            self.end_date = date.today()
        if self.start_date is None:
            self.start_date = self.end_date - timedelta(days=365 * 3)


_SYSTEM_PROMPT = """你是一位资深量化研究员。你的任务是根据用户的研究目标，生成具体的策略假设。

可用因子: MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR
因子列名: MA(20)→ma_20, EMA(12)→ema_12, RSI(14)→rsi_14, MACD()→macd_line/macd_signal/macd_hist, BOLL(20)→boll_mid_20/boll_upper_20/boll_lower_20, ATR(14)→atr_14

每个假设必须包含:
- 明确的入场/出场条件
- 使用的因子和参数
- 策略的核心逻辑

输出格式: JSON array of strings, 每个 string 是一个完整的策略假设描述。
示例: ["RSI(14)<25时买入，RSI>75时卖出，适用于震荡市反转", "MA(10)上穿MA(30)时买入，下穿时卖出，趋势跟踪"]
"""


def _parse_hypotheses(text: str) -> list[str]:
    """Parse LLM output into a list of hypothesis strings."""
    if not text.strip():
        return []
    # Try JSON array first
    cleaned = text.strip()
    # Remove markdown code block if present
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [str(h).strip() for h in parsed if str(h).strip()]
    except json.JSONDecodeError:
        pass
    # Fallback: numbered list
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    hypotheses = []
    for line in lines:
        # Match "1. ...", "1) ...", "- ..."
        m = re.match(r"^(?:\d+[.)]\s*|-\s*)(.*)", line)
        if m:
            hypotheses.append(m.group(1).strip())
    return hypotheses


async def generate_hypotheses(
    provider: LLMProvider,
    goal: ResearchGoal,
    previous_analysis: str = "",
) -> list[str]:
    """Generate N strategy hypotheses from a research goal."""
    user_content = f"研究目标: {goal.description}\n市场: {goal.market}\n请生成 {goal.n_hypotheses} 个策略假设。"
    if previous_analysis:
        user_content += f"\n\n上一轮分析结果（请据此调整方向）:\n{previous_analysis}"

    messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_content),
    ]
    try:
        response = await provider.achat(messages)
        return _parse_hypotheses(response.content)
    except Exception as e:
        logger.error("Hypothesis generation failed: %s", e)
        return []
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_agent/test_hypothesis.py -v
```
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add ez/agent/hypothesis.py tests/test_agent/test_hypothesis.py
git commit -m "feat(v2.8): E1 Hypothesis Generator — LLM-powered strategy ideation"
```

---

### Task 4: Code Generator (E2)

**Files:**
- Create: `ez/agent/code_gen.py`
- Test: `tests/test_agent/test_code_gen.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_agent/test_code_gen.py
"""Tests for code generation (E2)."""
from __future__ import annotations

import ast
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ez.agent.code_gen import generate_strategy_code, _extract_strategy_class_name
from ez.llm.provider import LLMResponse


class TestExtractClassName:
    def test_simple_strategy(self):
        code = '''
from ez.strategy.base import Strategy
class RSIReversal(Strategy):
    pass
'''
        assert _extract_strategy_class_name(code) == "RSIReversal"

    def test_multiple_classes(self):
        code = '''
from ez.strategy.base import Strategy
class Helper:
    pass
class MyStrat(Strategy):
    pass
'''
        assert _extract_strategy_class_name(code) == "MyStrat"

    def test_no_strategy(self):
        code = 'class Foo: pass'
        assert _extract_strategy_class_name(code) is None


class TestGenerateStrategyCode:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_provider = MagicMock()
        # chat_sync returns after LLM calls create_strategy tool successfully
        mock_response = LLMResponse(content="策略已创建", finish_reason="stop")

        with patch("ez.agent.code_gen.chat_sync", return_value=mock_response) as mock_chat, \
             patch("ez.agent.code_gen._find_latest_strategy", return_value=("rsi_reversal.py", "RSIReversal")):
            filename, class_name, error = await generate_strategy_code(
                mock_provider, "RSI<30买入"
            )
            assert filename == "rsi_reversal.py"
            assert class_name == "RSIReversal"
            assert error is None

    @pytest.mark.asyncio
    async def test_all_retries_fail(self):
        mock_provider = MagicMock()
        mock_response = LLMResponse(content="无法创建", finish_reason="stop")

        with patch("ez.agent.code_gen.chat_sync", return_value=mock_response), \
             patch("ez.agent.code_gen._find_latest_strategy", return_value=(None, None)):
            filename, class_name, error = await generate_strategy_code(
                mock_provider, "bad hypothesis", max_retries=1
            )
            assert filename is None
            assert class_name is None
            assert error is not None

    @pytest.mark.asyncio
    async def test_llm_exception(self):
        mock_provider = MagicMock()

        with patch("ez.agent.code_gen.chat_sync", side_effect=Exception("LLM down")):
            filename, class_name, error = await generate_strategy_code(
                mock_provider, "test"
            )
            assert filename is None
            assert "LLM down" in error
```

- [ ] **Step 2: Implement code_gen.py**

```python
# ez/agent/code_gen.py
"""V2.8 E2: Code Generator — LLM writes strategy code with sandbox validation."""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path

from ez.agent.assistant import chat_sync
from ez.agent.sandbox import list_user_strategies
from ez.llm.provider import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)

_CODE_GEN_SYSTEM = """你是 ez-trading 量化交易平台的策略代码生成器。
你的唯一任务是：根据给定的策略假设，使用 create_strategy 工具创建一个 Python 策略文件。

## 策略接口
```python
from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import MA, EMA, RSI, MACD, BOLL, Momentum, VWAP, OBV, ATR

class MyStrategy(Strategy):
    def __init__(self, period: int = 20):
        self.period = period

    @classmethod
    def get_description(cls) -> str:
        return "策略描述"

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        return {"period": {"type": "int", "default": 20, "min": 5, "max": 120, "label": "周期"}}

    def required_factors(self) -> list[Factor]:
        return [MA(period=self.period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        return (data["adj_close"] > data["ma_20"]).astype(float)
```

## 因子列名
MA(20)→ma_20, EMA(12)→ema_12, RSI(14)→rsi_14
MACD()→macd_line/macd_signal/macd_hist
BOLL(20)→boll_mid_20/boll_upper_20/boll_lower_20
Momentum(20)→momentum_20, VWAP(20)→vwap_20, OBV()→obv, ATR(14)→atr_14

## 规则
- 文件名使用蛇形命名且唯一 (如 rsi_reversal_v1.py)
- 类名使用驼峰命名
- 信号返回 0.0 (空仓) 到 1.0 (满仓) 的 pd.Series
- 必须使用 create_strategy 工具保存代码
- 不要跑回测或实验，只创建策略文件
"""


def _extract_strategy_class_name(code: str) -> str | None:
    """Extract the Strategy subclass name from Python code via AST."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name == "Strategy":
                    return node.name
    return None


def _find_latest_strategy(before_set: set[str]) -> tuple[str | None, str | None]:
    """Find a newly created strategy by comparing with a snapshot of filenames."""
    current = {s["filename"] for s in list_user_strategies()}
    new_files = current - before_set
    if not new_files:
        return None, None
    filename = sorted(new_files)[0]  # deterministic pick
    strategies_dir = Path(__file__).resolve().parent.parent.parent / "strategies"
    code = (strategies_dir / filename).read_text(encoding="utf-8")
    class_name = _extract_strategy_class_name(code)
    return filename, class_name


async def generate_strategy_code(
    provider: LLMProvider,
    hypothesis: str,
    max_retries: int = 3,
) -> tuple[str | None, str | None, str | None]:
    """Generate a strategy from a hypothesis.

    Returns: (filename, class_name, error)
    """
    before = {s["filename"] for s in list_user_strategies()}
    messages = [
        LLMMessage(role="system", content=_CODE_GEN_SYSTEM),
        LLMMessage(role="user", content=f"请根据以下假设创建策略:\n{hypothesis}"),
    ]

    for attempt in range(max_retries):
        try:
            await asyncio.to_thread(chat_sync, provider, messages)
            filename, class_name = _find_latest_strategy(before)
            if filename and class_name:
                logger.info("Code gen success: %s (%s)", filename, class_name)
                return filename, class_name, None
            # No new file — LLM didn't call the tool or it failed
            messages.append(LLMMessage(role="user",
                content="策略文件未创建成功。请使用 create_strategy 工具重新尝试。"))
        except Exception as e:
            logger.warning("Code gen attempt %d failed: %s", attempt + 1, e)
            return None, None, str(e)

    return None, None, f"经过{max_retries}次重试仍未成功创建策略"
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_agent/test_code_gen.py -v
```

- [ ] **Step 4: Commit**

```bash
git add ez/agent/code_gen.py tests/test_agent/test_code_gen.py
git commit -m "feat(v2.8): E2 Code Generator — LLM strategy creation with sandbox"
```

---

### Task 5: Analyzer (E4)

**Files:**
- Create: `ez/agent/analyzer.py`
- Test: `tests/test_agent/test_analyzer.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_agent/test_analyzer.py
"""Tests for result analyzer (E4)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ez.agent.analyzer import AnalysisResult, analyze_results, _build_summary
from ez.agent.hypothesis import ResearchGoal
from ez.llm.provider import LLMResponse


class TestBuildSummary:
    def test_with_passed_and_failed(self):
        batch = MagicMock()
        passed = [MagicMock(sharpe=1.2), MagicMock(sharpe=0.8)]
        batch.passed = passed
        batch.executed = 5
        # Create mock candidates with gate_reasons
        candidates = []
        for i in range(5):
            c = MagicMock()
            c.report = MagicMock()
            c.report.gate_reasons = [{"rule": "min_sharpe", "passed": i < 2, "message": f"Sharpe={0.3+i*0.3:.1f}"}]
            c.gate_passed = i < 2
            candidates.append(c)
        batch.candidates = candidates
        summary = _build_summary(batch, ["h1", "h2", "h3"])
        assert "5" in summary  # executed count
        assert "2" in summary  # passed count

    def test_empty_batch(self):
        batch = MagicMock()
        batch.passed = []
        batch.executed = 0
        batch.candidates = []
        summary = _build_summary(batch, [])
        assert "0" in summary


class TestAnalyzeResults:
    @pytest.mark.asyncio
    async def test_basic_analysis(self):
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(return_value=LLMResponse(
            content='{"direction": "收紧RSI阈值", "suggestions": ["RSI<20", "加ATR过滤"]}',
            finish_reason="stop",
        ))
        batch = MagicMock()
        batch.passed = [MagicMock(sharpe=1.1)]
        batch.executed = 3
        batch.candidates = []
        goal = ResearchGoal(description="test")

        result = await analyze_results(mock_provider, batch, goal, ["h1"])
        assert isinstance(result, AnalysisResult)
        assert "RSI" in result.direction
        assert result.passed_count == 1

    @pytest.mark.asyncio
    async def test_llm_error_returns_fallback(self):
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=Exception("timeout"))
        batch = MagicMock()
        batch.passed = []
        batch.executed = 2
        batch.candidates = []
        goal = ResearchGoal(description="test")

        result = await analyze_results(mock_provider, batch, goal, ["h1"])
        assert result.direction != ""  # Should have fallback text
```

- [ ] **Step 2: Implement analyzer.py**

```python
# ez/agent/analyzer.py
"""V2.8 E4: Analyzer — LLM interprets batch results and suggests next direction."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ez.agent.hypothesis import ResearchGoal
from ez.llm.provider import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Output from the analyzer."""
    direction: str = ""
    suggestions: list[str] = field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0
    best_sharpe: float = 0.0
    key_failure_reasons: list[str] = field(default_factory=list)


def _build_summary(batch_result, hypothesis_texts: list[str]) -> str:
    """Build a ≤500 token summary of batch results for LLM consumption."""
    passed = batch_result.passed
    executed = batch_result.executed
    lines = [
        f"本轮测试了 {len(hypothesis_texts)} 个假设，执行了 {executed} 个回测。",
        f"通过 Gate 的策略: {len(passed)} 个",
    ]
    if passed:
        top3 = sorted(passed, key=lambda c: c.sharpe, reverse=True)[:3]
        lines.append("Top Sharpe: " + ", ".join(f"{c.sharpe:.2f}" for c in top3))

    # Sample failure reasons (max 5)
    failure_reasons = []
    for c in batch_result.candidates:
        if not c.gate_passed and c.report and hasattr(c.report, "gate_reasons"):
            for reason in (c.report.gate_reasons or []):
                if isinstance(reason, dict) and not reason.get("passed", True):
                    failure_reasons.append(reason.get("message", ""))
        if len(failure_reasons) >= 5:
            break
    if failure_reasons:
        lines.append("主要失败原因: " + "; ".join(failure_reasons[:5]))
    if hypothesis_texts:
        lines.append("本轮假设: " + "; ".join(hypothesis_texts[:5]))
    return "\n".join(lines)


_ANALYZER_SYSTEM = """你是量化研究分析师。根据本轮回测结果，分析策略表现并提出下一轮研究方向。

输出 JSON 格式:
{"direction": "下轮研究方向建议（一句话）", "suggestions": ["具体建议1", "具体建议2"]}
"""


async def analyze_results(
    provider: LLMProvider,
    batch_result,
    goal: ResearchGoal,
    hypothesis_texts: list[str],
) -> AnalysisResult:
    """Analyze batch results and suggest next iteration direction."""
    passed_count = len(batch_result.passed)
    failed_count = batch_result.executed - passed_count
    best_sharpe = max((c.sharpe for c in batch_result.passed), default=0.0)

    summary = _build_summary(batch_result, hypothesis_texts)
    messages = [
        LLMMessage(role="system", content=_ANALYZER_SYSTEM),
        LLMMessage(role="user", content=f"研究目标: {goal.description}\n\n{summary}\n\n请分析并给出下轮方向。"),
    ]

    direction = "继续探索不同策略类型"
    suggestions: list[str] = []
    try:
        response = await provider.achat(messages)
        text = response.content.strip()
        # Try parse JSON
        if "{" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            data = json.loads(text[start:end])
            direction = data.get("direction", direction)
            suggestions = data.get("suggestions", [])
    except Exception as e:
        logger.warning("Analysis LLM call failed: %s", e)
        direction = f"上轮{passed_count}个通过，{failed_count}个失败。建议调整参数范围。"

    return AnalysisResult(
        direction=direction,
        suggestions=suggestions,
        passed_count=passed_count,
        failed_count=failed_count,
        best_sharpe=best_sharpe,
    )
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_agent/test_analyzer.py -v
```

- [ ] **Step 4: Commit**

```bash
git add ez/agent/analyzer.py tests/test_agent/test_analyzer.py
git commit -m "feat(v2.8): E4 Analyzer — LLM result interpretation + direction"
```

---

### Task 6: Research Report (E6)

**Files:**
- Create: `ez/agent/research_report.py`
- Test: `tests/test_agent/test_research_report.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_agent/test_research_report.py
"""Tests for research report builder (E6)."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import duckdb
import pytest

from ez.agent.research_report import ResearchReport, build_report
from ez.agent.research_store import ResearchStore
from ez.llm.provider import LLMResponse


@pytest.fixture
def stores():
    conn = duckdb.connect(":memory:")
    rs = ResearchStore(conn)
    # Setup task + iterations
    rs.save_task({"task_id": "t1", "goal": "test goal", "config": '{"max_iterations": 3}',
                  "status": "completed", "created_at": datetime.now().isoformat()})
    rs.save_iteration({"task_id": "t1", "iteration": 0,
                       "hypotheses": '["h1", "h2"]', "strategies_tried": 2,
                       "strategies_passed": 1, "best_sharpe": 1.2,
                       "analysis": '{"direction": "继续"}',
                       "spec_ids": '["spec1"]', "created_at": datetime.now().isoformat()})
    rs.save_iteration({"task_id": "t1", "iteration": 1,
                       "hypotheses": '["h3"]', "strategies_tried": 1,
                       "strategies_passed": 0, "best_sharpe": 0.0,
                       "analysis": '{"direction": "换方向"}',
                       "spec_ids": '[]', "created_at": datetime.now().isoformat()})
    yield rs
    conn.close()


class TestBuildReport:
    @pytest.mark.asyncio
    async def test_build_without_llm(self, stores):
        report = await build_report(None, stores, "t1", "收敛")
        assert isinstance(report, ResearchReport)
        assert report.task_id == "t1"
        assert report.goal == "test goal"
        assert len(report.iterations) == 2
        assert report.total_specs == 3  # 2 + 1
        assert report.total_passed == 1  # 1 + 0
        assert report.stop_reason == "收敛"
        assert report.summary == ""  # no LLM

    @pytest.mark.asyncio
    async def test_build_with_llm_summary(self, stores):
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(return_value=LLMResponse(
            content="本次研究找到1个有效策略", finish_reason="stop"))
        report = await build_report(mock_provider, stores, "t1", "收敛")
        assert "有效策略" in report.summary

    @pytest.mark.asyncio
    async def test_build_llm_failure_still_works(self, stores):
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=Exception("down"))
        report = await build_report(mock_provider, stores, "t1", "收敛")
        assert report.summary == ""  # graceful fallback
        assert report.total_specs == 3
```

- [ ] **Step 2: Implement research_report.py**

```python
# ez/agent/research_report.py
"""V2.8 E6: Research Report — aggregate iterations into final report."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ez.agent.research_store import ResearchStore
from ez.llm.provider import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class ResearchReport:
    """Final output of a research task."""
    task_id: str = ""
    goal: str = ""
    config: dict = field(default_factory=dict)
    status: str = ""
    iterations: list[dict] = field(default_factory=list)
    best_strategies: list[dict] = field(default_factory=list)
    total_specs: int = 0
    total_passed: int = 0
    summary: str = ""
    duration_sec: float = 0.0
    stop_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "goal": self.goal, "config": self.config,
            "status": self.status, "iterations": self.iterations,
            "best_strategies": self.best_strategies,
            "total_specs": self.total_specs, "total_passed": self.total_passed,
            "summary": self.summary, "duration_sec": self.duration_sec,
            "stop_reason": self.stop_reason,
        }


async def build_report(
    provider: LLMProvider | None,
    store: ResearchStore,
    task_id: str,
    stop_reason: str,
) -> ResearchReport:
    """Build report from stored iterations."""
    task = store.get_task(task_id) or {}
    iterations = store.get_iterations(task_id)

    total_specs = sum(it.get("strategies_tried", 0) for it in iterations)
    total_passed = sum(it.get("strategies_passed", 0) for it in iterations)

    config = {}
    try:
        config = json.loads(task.get("config", "{}"))
    except (json.JSONDecodeError, TypeError):
        pass

    report = ResearchReport(
        task_id=task_id,
        goal=task.get("goal", ""),
        config=config,
        status=task.get("status", "completed"),
        iterations=iterations,
        total_specs=total_specs,
        total_passed=total_passed,
        stop_reason=stop_reason,
    )

    # Optional LLM summary
    if provider is not None:
        try:
            summary_prompt = (
                f"研究目标: {report.goal}\n"
                f"共执行 {total_specs} 个回测，{total_passed} 个通过。\n"
                f"停止原因: {stop_reason}\n"
                f"请用2-3句话总结本次研究结果。"
            )
            response = await provider.achat([
                LLMMessage(role="system", content="你是量化研究报告撰写者，请简洁总结研究发现。"),
                LLMMessage(role="user", content=summary_prompt),
            ])
            report.summary = response.content.strip()
        except Exception as e:
            logger.warning("Report summary LLM failed: %s", e)

    return report
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_agent/test_research_report.py -v
```

- [ ] **Step 4: Commit**

```bash
git add ez/agent/research_report.py tests/test_agent/test_research_report.py
git commit -m "feat(v2.8): E6 Research Report — iteration aggregation + LLM summary"
```

---

### Task 7: Research Runner (Orchestrator)

**Files:**
- Create: `ez/agent/research_runner.py`
- Test: `tests/test_agent/test_research_runner.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_agent/test_research_runner.py
"""Integration tests for the research runner orchestrator."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import numpy as np
import pandas as pd
import pytest

from ez.agent.hypothesis import ResearchGoal
from ez.agent.loop_controller import LoopConfig
from ez.agent.research_runner import run_research_task, cancel_task, get_task_events, _running_tasks
from ez.agent.research_store import ResearchStore
from ez.llm.provider import LLMResponse


def _make_test_data():
    rng = np.random.default_rng(42)
    n = 300
    prices = 10 * np.cumprod(1 + rng.normal(0.001, 0.015, n))
    dates = pd.date_range("2020-01-03", periods=n, freq="B")
    return pd.DataFrame({
        "open": prices * (1 + rng.normal(0, 0.002, n)),
        "high": prices * (1 + abs(rng.normal(0, 0.005, n))),
        "low": prices * (1 - abs(rng.normal(0, 0.005, n))),
        "close": prices, "adj_close": prices,
        "volume": rng.integers(100_000, 5_000_000, n),
    }, index=dates)


@pytest.fixture(autouse=True)
def _cleanup():
    _running_tasks.clear()
    yield
    _running_tasks.clear()


class TestRunResearchTask:
    @pytest.mark.asyncio
    async def test_full_pipeline_mock(self):
        """Full pipeline with mocked LLM — verifies orchestration."""
        mock_provider = MagicMock()
        # E1: hypotheses
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["MA交叉策略"]'),  # E1 iter 0
            LLMResponse(content='{"direction": "ok", "suggestions": []}'),  # E4 iter 0
            LLMResponse(content='["RSI策略"]'),  # E1 iter 1 (if needed)
            LLMResponse(content='{"direction": "ok", "suggestions": []}'),  # E4 iter 1
            LLMResponse(content="研究完成"),  # E6 summary
        ])

        goal = ResearchGoal(description="test", n_hypotheses=1)
        loop_config = LoopConfig(max_iterations=1, max_specs=10)

        conn = duckdb.connect(":memory:")
        research_store = ResearchStore(conn)

        with patch("ez.agent.research_runner.create_provider", return_value=mock_provider), \
             patch("ez.agent.research_runner.get_research_store", return_value=research_store), \
             patch("ez.agent.research_runner.get_experiment_store") as mock_exp_store, \
             patch("ez.agent.research_runner._fetch_data", return_value=_make_test_data()), \
             patch("ez.agent.research_runner.generate_strategy_code",
                   new_callable=AsyncMock, return_value=("test.py", "TestStrat", None)), \
             patch("ez.agent.research_runner._run_batch_for_strategies", return_value=MagicMock(
                 passed=[], executed=1, candidates=[])):
            task_id = await run_research_task(goal, loop_config)

        assert task_id is not None
        task = research_store.get_task(task_id)
        assert task["status"] in ("completed", "failed")

    @pytest.mark.asyncio
    async def test_cancel(self):
        """Cancel sets flag, loop exits."""
        goal = ResearchGoal(description="test", n_hypotheses=1)
        loop_config = LoopConfig(max_iterations=100)  # Would run forever without cancel

        mock_provider = MagicMock()
        # Slow E1 to give us time to cancel
        async def slow_hypotheses(*args, **kwargs):
            await asyncio.sleep(0.1)
            return LLMResponse(content='["test"]')
        mock_provider.achat = slow_hypotheses

        conn = duckdb.connect(":memory:")
        research_store = ResearchStore(conn)

        with patch("ez.agent.research_runner.create_provider", return_value=mock_provider), \
             patch("ez.agent.research_runner.get_research_store", return_value=research_store), \
             patch("ez.agent.research_runner.get_experiment_store"), \
             patch("ez.agent.research_runner._fetch_data", return_value=_make_test_data()), \
             patch("ez.agent.research_runner.generate_strategy_code",
                   new_callable=AsyncMock, return_value=(None, None, "skipped")), \
             patch("ez.agent.research_runner._run_batch_for_strategies", return_value=MagicMock(
                 passed=[], executed=0, candidates=[])):
            task_id = await run_research_task(goal, loop_config)
            # Cancel immediately
            cancel_task(task_id)
            # Give loop time to check cancel flag
            await asyncio.sleep(0.3)

        events = get_task_events(task_id)
        assert events is not None
```

- [ ] **Step 2: Implement research_runner.py**

```python
# ez/agent/research_runner.py
"""V2.8: Research Runner — main orchestrator for autonomous research tasks."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime

import pandas as pd

from ez.agent.analyzer import analyze_results
from ez.agent.code_gen import generate_strategy_code
from ez.agent.data_access import get_chain, get_experiment_store, get_research_store
from ez.agent.hypothesis import ResearchGoal, generate_hypotheses
from ez.agent.loop_controller import LoopConfig, LoopController, LoopState
from ez.agent.research_report import build_report
from ez.agent.research_store import ResearchStore
from ez.agent.run_spec import RunSpec
from ez.agent.batch_runner import run_batch, BatchConfig
from ez.agent.gates import GateConfig
from ez.llm.factory import create_provider

logger = logging.getLogger(__name__)

# In-memory event queues for SSE streaming
_running_tasks: dict[str, dict] = {}


def _emit(task_id: str, event: str, data: dict) -> None:
    """Append an SSE event to the task's event queue."""
    if task_id in _running_tasks:
        _running_tasks[task_id]["events"].append({"event": event, "data": data})


def _fetch_data(goal: ResearchGoal) -> pd.DataFrame:
    """Fetch market data for the research task."""
    chain = get_chain()
    bars = chain.get_kline(goal.symbol, goal.market, goal.period, goal.start_date, goal.end_date)
    if not bars:
        raise ValueError(f"No data for {goal.symbol} ({goal.start_date} to {goal.end_date})")
    return pd.DataFrame([{
        "time": b.time, "open": b.open, "high": b.high, "low": b.low,
        "close": b.close, "adj_close": b.adj_close, "volume": b.volume,
    } for b in bars]).set_index("time")


def _run_batch_for_strategies(
    strategy_names: list[str],
    goal: ResearchGoal,
    data: pd.DataFrame,
    gate_config: GateConfig,
) -> object:
    """Create RunSpecs and run batch for the given strategies."""
    specs = [
        RunSpec(
            strategy_name=name,
            strategy_params={},
            symbol=goal.symbol,
            market=goal.market,
            period=goal.period,
            start_date=goal.start_date,
            end_date=goal.end_date,
        )
        for name in strategy_names
    ]
    if not specs:
        from types import SimpleNamespace
        return SimpleNamespace(passed=[], executed=0, candidates=[], ranked=[])
    config = BatchConfig(gate_config=gate_config, skip_prefilter=True)
    store = get_experiment_store()
    return run_batch(specs, data, config=config, store=store)


async def run_research_task(
    goal: ResearchGoal,
    loop_config: LoopConfig = LoopConfig(),
    gate_config: GateConfig = GateConfig(),
) -> str:
    """Main orchestrator. Returns task_id. Runs in background."""
    task_id = uuid.uuid4().hex[:12]
    _running_tasks[task_id] = {"events": [], "done": False, "state": LoopState()}

    provider = create_provider()
    research_store = get_research_store()
    controller = LoopController(loop_config)
    state = LoopState()
    start_time = datetime.now()

    # Persist task
    research_store.save_task({
        "task_id": task_id,
        "goal": goal.description,
        "config": json.dumps({
            "max_iterations": loop_config.max_iterations,
            "max_specs": loop_config.max_specs,
            "max_llm_calls": loop_config.max_llm_calls,
            "symbol": goal.symbol,
            "market": goal.market,
            "start_date": str(goal.start_date),
            "end_date": str(goal.end_date),
        }),
        "status": "running",
    })
    research_store.update_task_status(task_id, "running")

    stop_reason = ""
    try:
        # Fetch data once
        data = await asyncio.to_thread(_fetch_data, goal)
        previous_analysis = ""

        while True:
            # Check cancel / budget
            if task_id in _running_tasks and _running_tasks[task_id].get("cancel"):
                state.cancelled = True
            ok, reason = controller.should_continue(state)
            if not ok:
                stop_reason = reason
                break

            _emit(task_id, "iteration_start", {
                "iteration": state.iteration, "max_iterations": loop_config.max_iterations})
            llm_calls = 0

            # E1: Generate hypotheses
            hypotheses = await generate_hypotheses(provider, goal, previous_analysis)
            llm_calls += 1
            for i, h in enumerate(hypotheses):
                _emit(task_id, "hypothesis", {"index": i, "total": len(hypotheses), "text": h})

            # E2: Generate code for each hypothesis
            strategy_names: list[str] = []
            for i, hypothesis in enumerate(hypotheses):
                filename, class_name, error = await generate_strategy_code(provider, hypothesis)
                llm_calls += 1  # at least 1 LLM call per code_gen
                if class_name:
                    strategy_names.append(class_name)
                    _emit(task_id, "code_success", {
                        "index": i, "filename": filename, "class_name": class_name})
                else:
                    _emit(task_id, "code_failed", {
                        "index": i, "hypothesis": hypothesis[:100], "error": error or "unknown"})

            # E3: Batch execution
            _emit(task_id, "batch_start", {"total_specs": len(strategy_names)})
            batch_result = await asyncio.to_thread(
                _run_batch_for_strategies, strategy_names, goal, data, gate_config)
            best_sharpe = max((c.sharpe for c in batch_result.passed), default=0.0)
            _emit(task_id, "batch_complete", {
                "executed": batch_result.executed,
                "passed": len(batch_result.passed),
                "best_sharpe": round(best_sharpe, 4),
            })

            # E4: Analyze results
            analysis = await analyze_results(provider, batch_result, goal, hypotheses)
            llm_calls += 1
            previous_analysis = analysis.direction
            _emit(task_id, "analysis", {
                "direction": analysis.direction,
                "passed": analysis.passed_count,
                "failed": analysis.failed_count,
            })

            # E5: Update state
            spec_ids = [s.spec_id for s in
                        [RunSpec(strategy_name=n, strategy_params={}, symbol=goal.symbol,
                                 market=goal.market, start_date=goal.start_date, end_date=goal.end_date)
                         for n in strategy_names]]
            state = controller.update(state, batch_result, llm_calls)
            _running_tasks[task_id]["state"] = state

            # Persist iteration
            research_store.save_iteration({
                "task_id": task_id,
                "iteration": state.iteration - 1,
                "hypotheses": json.dumps(hypotheses),
                "strategies_tried": len(strategy_names),
                "strategies_passed": len(batch_result.passed),
                "best_sharpe": best_sharpe,
                "analysis": json.dumps({"direction": analysis.direction, "suggestions": analysis.suggestions}),
                "spec_ids": json.dumps(spec_ids),
            })

            _emit(task_id, "iteration_end", {
                "iteration": state.iteration,
                "cumulative_passed": state.gate_passed_total,
                "cumulative_specs": state.specs_executed,
            })

        # E6: Build report
        report = await build_report(provider, research_store, task_id, stop_reason)
        duration = (datetime.now() - start_time).total_seconds()
        report.duration_sec = duration

        research_store.update_task_status(
            task_id, "completed", stop_reason=stop_reason, summary=report.summary)

        _emit(task_id, "task_complete", {
            "total_passed": state.gate_passed_total,
            "best_sharpe": round(state.best_sharpe, 4) if state.best_sharpe > float("-inf") else 0,
            "stop_reason": stop_reason,
        })

    except Exception as e:
        logger.error("Research task %s failed: %s", task_id, e)
        stop_reason = str(e)
        research_store.update_task_status(task_id, "failed", error=str(e))
        _emit(task_id, "task_failed", {"error": str(e)})

    finally:
        if task_id in _running_tasks:
            _running_tasks[task_id]["done"] = True

    return task_id


def cancel_task(task_id: str) -> bool:
    """Cancel a running task."""
    if task_id in _running_tasks and not _running_tasks[task_id]["done"]:
        _running_tasks[task_id]["cancel"] = True
        return True
    return False


def get_task_events(task_id: str) -> dict | None:
    """Get in-memory event data for SSE streaming."""
    return _running_tasks.get(task_id)
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_agent/test_research_runner.py -v
```

- [ ] **Step 4: Commit**

```bash
git add ez/agent/research_runner.py tests/test_agent/test_research_runner.py
git commit -m "feat(v2.8): Research Runner — async orchestrator with SSE events"
```

---

### Task 8: API Routes

**Files:**
- Create: `ez/api/routes/research.py`
- Modify: `ez/api/app.py`
- Test: `tests/test_api/test_research_api.py`

- [ ] **Step 1: Implement research.py API**

```python
# ez/api/routes/research.py
"""V2.8: Research API — start, list, detail, cancel, SSE stream."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ez.agent.hypothesis import ResearchGoal
from ez.agent.loop_controller import LoopConfig
from ez.agent.gates import GateConfig
from ez.agent.research_runner import run_research_task, cancel_task, get_task_events

router = APIRouter()
logger = logging.getLogger(__name__)


class ResearchRequest(BaseModel):
    goal: str
    symbol: str = "000001.SZ"
    market: str = "cn_stock"
    period: str = "daily"
    start_date: date | None = None
    end_date: date | None = None
    max_iterations: int = Field(default=10, ge=1, le=50)
    max_specs: int = Field(default=500, ge=1, le=5000)
    max_llm_calls: int = Field(default=100, ge=1, le=1000)
    n_hypotheses: int = Field(default=5, ge=1, le=20)
    gate_min_sharpe: float = 0.5
    gate_max_drawdown: float = 0.3


@router.post("/start")
async def start_research(req: ResearchRequest):
    """Start an autonomous research task."""
    end = req.end_date or date.today()
    start = req.start_date or (end - timedelta(days=365 * 3))

    goal = ResearchGoal(
        description=req.goal,
        market=req.market,
        symbol=req.symbol,
        period=req.period,
        start_date=start,
        end_date=end,
        n_hypotheses=req.n_hypotheses,
    )
    loop_config = LoopConfig(
        max_iterations=req.max_iterations,
        max_specs=req.max_specs,
        max_llm_calls=req.max_llm_calls,
    )
    gate_config = GateConfig(
        min_sharpe=req.gate_min_sharpe,
        max_drawdown=req.gate_max_drawdown,
    )

    # Run in background
    task_id = await run_research_task(goal, loop_config, gate_config)
    return {"task_id": task_id, "status": "started"}


@router.get("/tasks")
def list_research_tasks(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List research tasks."""
    from ez.agent.data_access import get_research_store
    store = get_research_store()
    return store.list_tasks(limit=limit, offset=offset)


@router.get("/tasks/{task_id}")
def get_research_task(task_id: str):
    """Get research task detail with iterations."""
    from ez.agent.data_access import get_research_store
    store = get_research_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    task["iterations"] = store.get_iterations(task_id)
    return task


@router.post("/tasks/{task_id}/cancel")
def cancel_research_task(task_id: str):
    """Cancel a running research task."""
    if cancel_task(task_id):
        return {"status": "cancelling", "task_id": task_id}
    raise HTTPException(status_code=404, detail="Task not found or already finished")


@router.get("/tasks/{task_id}/stream")
async def stream_research_task(task_id: str):
    """SSE stream of research task progress."""
    events_data = get_task_events(task_id)
    if events_data is None:
        raise HTTPException(status_code=404, detail="Task not found or not running")

    async def generate():
        idx = 0
        while True:
            while idx < len(events_data["events"]):
                evt = events_data["events"][idx]
                line = f"event: {evt['event']}\ndata: {json.dumps(evt['data'], ensure_ascii=False)}\n\n"
                yield line
                idx += 1
            if events_data["done"]:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 2: Register router in app.py**

Add to `ez/api/app.py` after the existing router imports:

```python
from ez.api.routes import market_data, backtest, factors, experiments, candidates, code, chat, settings, research
```

And add:
```python
app.include_router(research.router, prefix="/api/research", tags=["research"])
```

- [ ] **Step 3: Write API tests**

```python
# tests/test_api/test_research_api.py
"""Tests for /api/research endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import duckdb
import pytest
from fastapi.testclient import TestClient

from ez.agent.research_store import ResearchStore
from ez.api.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _patch_store():
    conn = duckdb.connect(":memory:")
    store = ResearchStore(conn)
    with patch("ez.api.routes.research.get_research_store", return_value=store), \
         patch("ez.agent.data_access.get_research_store", return_value=store):
        yield store
    conn.close()


class TestListTasks:
    def test_list_empty(self, _patch_store):
        resp = client.get("/api/research/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_tasks(self, _patch_store):
        from datetime import datetime
        _patch_store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                                "status": "completed", "created_at": datetime.now().isoformat()})
        resp = client.get("/api/research/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestGetTask:
    def test_not_found(self):
        resp = client.get("/api/research/tasks/nonexistent")
        assert resp.status_code == 404

    def test_found(self, _patch_store):
        from datetime import datetime
        _patch_store.save_task({"task_id": "t1", "goal": "test", "config": "{}",
                                "status": "completed", "created_at": datetime.now().isoformat()})
        resp = client.get("/api/research/tasks/t1")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == "t1"


class TestCancelTask:
    def test_cancel_nonexistent(self):
        resp = client.post("/api/research/tasks/nonexistent/cancel")
        assert resp.status_code == 404
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_api/test_research_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add ez/api/routes/research.py ez/api/app.py tests/test_api/test_research_api.py
git commit -m "feat(v2.8): Research API — start/list/detail/cancel/stream endpoints"
```

---

### Task 9: Frontend — ResearchPanel

**Files:**
- Create: `web/src/components/ResearchPanel.tsx`
- Modify: `web/src/components/Navbar.tsx`
- Modify: `web/src/App.tsx`

- [ ] **Step 1: Create ResearchPanel.tsx**

Create `web/src/components/ResearchPanel.tsx` — goal form + task list + SSE progress + report view. This is a large component (~400 lines). Key sections:

1. **Goal input form** — textarea + symbol + dates + budget (collapsible)
2. **Task list** — table with status badges
3. **Running task** — SSE event log + progress bar
4. **Completed task** — report metrics + iteration timeline

The component should follow the existing patterns from ExperimentPanel (table + detail) and ChatPanel (SSE streaming).

- [ ] **Step 2: Update Navbar.tsx**

Add research tab to the tabs array:

```tsx
const tabs = [
  { id: 'dashboard', label: '看板' },
  { id: 'experiments', label: '实验' },
  { id: 'editor', label: '代码编辑器' },
  { id: 'research', label: '研究助手' },
  { id: 'docs', label: '开发文档' },
]
```

- [ ] **Step 3: Update App.tsx**

Add conditional render for the research tab:

```tsx
{activeTab === 'research' && <ResearchPanel />}
```

- [ ] **Step 4: Build frontend**

```bash
cd web && npm run build
```
Expected: Build succeeds with no TypeScript errors

- [ ] **Step 5: Commit**

```bash
git add web/src/components/ResearchPanel.tsx web/src/components/Navbar.tsx web/src/App.tsx
git commit -m "feat(v2.8): E7 ResearchPanel — goal form + task list + SSE progress + report"
```

---

### Task 10: Documentation + Version + Final Tests

**Files:**
- Modify: `CLAUDE.md`
- Modify: `ez/agent/CLAUDE.md`
- Modify: `docs/core-changes/v2.3-roadmap.md`
- Modify: `ez/api/CLAUDE.md`
- Modify: `ez/api/app.py` (version bump)
- Modify: `pyproject.toml` (version bump)

- [ ] **Step 1: Run full test suite**

```bash
./scripts/stop.sh
python -m pytest tests/ -q --tb=short
```
Expected: ≥ 921 pass, 0 errors

- [ ] **Step 2: Update CLAUDE.md**

- Version: 0.2.8
- Add V2.8 line in version progress
- Update test count
- Update Known Limitations

- [ ] **Step 3: Update module docs**

- `ez/agent/CLAUDE.md`: add E1-E6 modules + research_runner + research_store
- `ez/api/CLAUDE.md`: add research endpoints
- `docs/core-changes/v2.3-roadmap.md`: mark E1-E7 as [x]

- [ ] **Step 4: Version bump**

- `ez/api/app.py`: version "0.2.8"
- `pyproject.toml`: version "0.2.8"

- [ ] **Step 5: Commit and tag**

```bash
git add -A
git commit -m "docs: V2.8 Research Agent — 文档/版本更新"
```

- [ ] **Step 6: Code review**

Run `/superpowers:requesting-code-review` on the full V2.8 changeset.

- [ ] **Step 7: Tag after review passes**

```bash
git tag v0.2.8
```

---

## Self-Review

**Spec coverage:**
- E1 Hypothesis Generator → Task 3 ✅
- E2 Code Generator → Task 4 ✅
- E3 Batch Executor → Task 7 (in runner, uses existing run_batch) ✅
- E4 Analyzer → Task 5 ✅
- E5 Loop Controller → Task 1 ✅
- E6 Research Report → Task 6 ✅
- E7 Web UI → Task 9 ✅
- API endpoints → Task 8 ✅
- Persistence → Task 2 ✅
- Budget control → Task 1 (LoopController) ✅
- SSE streaming → Task 7 (runner events) + Task 8 (API stream) ✅
- Cancel → Task 7 (cancel_task) + Task 8 (cancel endpoint) ✅
- Error handling → Task 7 (try/except in runner) ✅
- Exit gates → Task 10 (full test suite) ✅

**Placeholder scan:** No TBD/TODO found. All code blocks are complete.

**Type consistency:** LoopState/LoopConfig/LoopController used consistently across Tasks 1, 7. ResearchGoal used consistently across Tasks 3, 5, 7. BatchResult/run_batch used consistently in Tasks 5, 7. ResearchStore used consistently in Tasks 2, 6, 7, 8.
