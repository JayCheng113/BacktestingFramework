"""Integration tests for the research runner orchestrator."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import numpy as np
import pandas as pd
import pytest

from ez.agent.research.hypothesis import ResearchGoal
from ez.agent.research.loop_controller import LoopConfig, LoopController
from ez.agent.research.runner import (
    run_research_task, cancel_task, get_task_events, _running_tasks,
    _emit, is_any_task_running, cleanup_finished_tasks, register_task,
    get_start_lock,
)
from ez.agent.research.store import ResearchStore
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


def _mock_batch(passed_count=0, executed=1, best_sharpe=0.0):
    batch = MagicMock()
    passed = [MagicMock(sharpe=best_sharpe - i * 0.1) for i in range(passed_count)]
    batch.passed = passed
    batch.executed = executed
    batch.candidates = []
    batch.ranked = passed
    return batch


@pytest.fixture(autouse=True)
def _cleanup():
    _running_tasks.clear()
    yield
    _running_tasks.clear()


# --- _emit tests ---

class TestEmit:
    def test_emit_appends_event(self):
        _running_tasks["t1"] = {"events": [], "done": False}
        _emit("t1", "test_event", {"key": "value"})
        assert len(_running_tasks["t1"]["events"]) == 1
        assert _running_tasks["t1"]["events"][0] == {"event": "test_event", "data": {"key": "value"}}

    def test_emit_missing_task_silent(self):
        _emit("nonexistent", "test", {})
        # No error raised

    def test_emit_multiple_events(self):
        _running_tasks["t1"] = {"events": [], "done": False}
        _emit("t1", "a", {"i": 1})
        _emit("t1", "b", {"i": 2})
        assert len(_running_tasks["t1"]["events"]) == 2


# --- is_any_task_running tests ---

class TestIsAnyTaskRunning:
    def test_no_tasks(self):
        assert is_any_task_running() is False

    def test_one_running(self):
        _running_tasks["t1"] = {"events": [], "done": False}
        assert is_any_task_running() is True

    def test_all_done(self):
        _running_tasks["t1"] = {"events": [], "done": True}
        _running_tasks["t2"] = {"events": [], "done": True}
        assert is_any_task_running() is False

    def test_mixed(self):
        _running_tasks["t1"] = {"events": [], "done": True}
        _running_tasks["t2"] = {"events": [], "done": False}
        assert is_any_task_running() is True


# --- cleanup_finished_tasks tests ---

class TestCleanupFinishedTasks:
    def test_cleanup_removes_old(self):
        for i in range(10):
            _running_tasks[f"t{i}"] = {"events": [], "done": True}
        cleanup_finished_tasks(keep=3)
        assert len(_running_tasks) == 3

    def test_cleanup_keeps_running(self):
        _running_tasks["running"] = {"events": [], "done": False}
        _running_tasks["done1"] = {"events": [], "done": True}
        _running_tasks["done2"] = {"events": [], "done": True}
        cleanup_finished_tasks(keep=0)
        # Running task untouched, finished tasks removed (keep=0 means remove all finished beyond 0)
        assert "running" in _running_tasks

    def test_cleanup_under_limit_noop(self):
        _running_tasks["t1"] = {"events": [], "done": True}
        cleanup_finished_tasks(keep=5)
        assert "t1" in _running_tasks

    def test_cleanup_keeps_newest_by_finished_at(self):
        """V2.8.1: cleanup sorts by finished_at, keeps newest."""
        from datetime import datetime, timedelta
        now = datetime.now()
        # oldest finished first
        _running_tasks["old"] = {"events": [], "done": True, "finished_at": now - timedelta(hours=3)}
        _running_tasks["mid"] = {"events": [], "done": True, "finished_at": now - timedelta(hours=1)}
        _running_tasks["new"] = {"events": [], "done": True, "finished_at": now}
        cleanup_finished_tasks(keep=1)
        assert "new" in _running_tasks
        assert "old" not in _running_tasks
        assert "mid" not in _running_tasks


# --- get_start_lock tests (V2.8.1) ---

class TestGetStartLock:
    def test_returns_asyncio_lock(self):
        lock = get_start_lock()
        assert isinstance(lock, asyncio.Lock)

    def test_returns_same_lock(self):
        assert get_start_lock() is get_start_lock()


# --- register_task tests (V2.8.1) ---

class TestRegisterTask:
    def test_register_has_created_at(self):
        from datetime import datetime
        register_task("test_register")
        assert "created_at" in _running_tasks["test_register"]
        assert isinstance(_running_tasks["test_register"]["created_at"], datetime)


# --- get_task_events tests ---

class TestGetTaskEvents:
    def test_existing(self):
        _running_tasks["t1"] = {"events": [{"event": "x", "data": {}}], "done": False}
        result = get_task_events("t1")
        assert result is not None
        assert len(result["events"]) == 1

    def test_nonexistent(self):
        assert get_task_events("nope") is None


# --- cancel tests ---

class TestCancel:
    def test_cancel_nonexistent(self):
        assert cancel_task("nope") is False

    def test_cancel_running(self):
        _running_tasks["t1"] = {"events": [], "done": False}
        assert cancel_task("t1") is True
        assert _running_tasks["t1"]["cancel"] is True

    def test_cancel_done(self):
        _running_tasks["t1"] = {"events": [], "done": True}
        assert cancel_task("t1") is False


# --- E2E pipeline tests ---

def _standard_patches(research_store, mock_provider, code_result=("test.py", "TestStrat", None),
                      batch=None):
    """Common patches for runner tests."""
    if batch is None:
        batch = _mock_batch()
    return [
        patch("ez.agent.research.runner.create_provider", return_value=mock_provider),
        patch("ez.agent.research.runner.get_research_store", return_value=research_store),
        patch("ez.agent.research.runner.get_experiment_store"),
        patch("ez.agent.research.runner._fetch_data", return_value=_make_test_data()),
        patch("ez.agent.research.runner.generate_strategy_code",
              new_callable=AsyncMock, return_value=code_result),
        patch("ez.agent.research.runner._run_batch_for_strategies",
              return_value=(batch, ["spec_abc"])),
    ]


class TestRunResearchTask:
    @pytest.mark.asyncio
    async def test_full_pipeline_single_iteration(self):
        """Single iteration pipeline with event verification."""
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["MA交叉策略"]'),
            LLMResponse(content='{"direction": "ok", "suggestions": []}'),
            LLMResponse(content="研究完成"),
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)
        goal = ResearchGoal(description="test", n_hypotheses=1)

        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=("test.py", "TestStrat", None)),
                            _run_batch_for_strategies=MagicMock(return_value=(_mock_batch(), ["spec1"]))):
            task_id = await run_research_task(goal, LoopConfig(max_iterations=1))

        # Verify task completed
        task = rs.get_task(task_id)
        assert task["status"] == "completed"

        # Verify events emitted in correct order
        events = get_task_events(task_id)
        assert events["done"] is True
        types = [e["event"] for e in events["events"]]
        assert types[0] == "iteration_start"
        assert "hypothesis" in types
        assert "code_success" in types
        assert "batch_start" in types
        assert "batch_complete" in types
        assert "analysis" in types
        assert "iteration_end" in types
        assert types[-1] == "task_complete"

        # Verify iteration persisted
        iters = rs.get_iterations(task_id)
        assert len(iters) == 1
        assert iters[0]["strategies_tried"] == 1
        conn.close()

    @pytest.mark.asyncio
    async def test_multi_iteration_convergence(self):
        """Multi-iteration: 2 iterations, no improvement, stops on no_improve_limit."""
        mock_provider = MagicMock()
        # 3 iterations: E1+E4 per iteration + E6 summary = 3*2 + 1 = 7 calls
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["策略A"]'),  # E1 iter 0
            LLMResponse(content='{"direction": "try B"}'),  # E4 iter 0
            LLMResponse(content='["策略B"]'),  # E1 iter 1
            LLMResponse(content='{"direction": "try C"}'),  # E4 iter 1
            LLMResponse(content='["策略C"]'),  # E1 iter 2
            LLMResponse(content='{"direction": "give up"}'),  # E4 iter 2
            LLMResponse(content="总结"),  # E6
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)
        goal = ResearchGoal(description="test", n_hypotheses=1)
        # no_improve_limit=3, all batches have 0 passed → should stop after 3 iterations
        loop_config = LoopConfig(max_iterations=10, no_improve_limit=3)

        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=("s.py", "S", None)),
                            _run_batch_for_strategies=MagicMock(
                                return_value=(_mock_batch(passed_count=0, executed=1), ["s1"]))):
            task_id = await run_research_task(goal, loop_config)

        task = rs.get_task(task_id)
        assert task["status"] == "completed"
        assert "无新通过" in task["stop_reason"]
        iters = rs.get_iterations(task_id)
        assert len(iters) == 3
        conn.close()

    @pytest.mark.asyncio
    async def test_budget_exhaustion_max_specs(self):
        """Stops when max_specs is exceeded."""
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["策略"]'),
            LLMResponse(content='{"direction": "继续"}'),
            LLMResponse(content="总结"),
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)
        goal = ResearchGoal(description="test", n_hypotheses=1)
        loop_config = LoopConfig(max_iterations=10, max_specs=1)

        # Batch executes 2 specs → exceeds max_specs=1
        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=("s.py", "S", None)),
                            _run_batch_for_strategies=MagicMock(
                                return_value=(_mock_batch(executed=2), ["s1"]))):
            task_id = await run_research_task(goal, loop_config)

        task = rs.get_task(task_id)
        assert "回测" in task["stop_reason"]
        conn.close()

    @pytest.mark.asyncio
    async def test_all_code_gen_fails(self):
        """All code gen fails → empty batch → no_improve increments."""
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["策略"]'),
            LLMResponse(content='{"direction": "继续"}'),
            LLMResponse(content="总结"),
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)
        goal = ResearchGoal(description="test", n_hypotheses=1)

        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=(None, None, "failed")),
                            _run_batch_for_strategies=MagicMock(
                                return_value=(_mock_batch(executed=0), []))):
            task_id = await run_research_task(goal, LoopConfig(max_iterations=1))

        events = get_task_events(task_id)
        types = [e["event"] for e in events["events"]]
        assert "code_failed" in types
        conn.close()

    @pytest.mark.asyncio
    async def test_data_fetch_failure(self):
        mock_provider = MagicMock()
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)

        with patch("ez.agent.research.runner.create_provider", return_value=mock_provider), \
             patch("ez.agent.research.runner.get_research_store", return_value=rs), \
             patch("ez.agent.research.runner._fetch_data", side_effect=ValueError("No data")):
            task_id = await run_research_task(ResearchGoal(description="test"), LoopConfig(max_iterations=1))

        task = rs.get_task(task_id)
        assert task["status"] == "failed"
        assert "No data" in task["error"]
        events = get_task_events(task_id)
        assert any(e["event"] == "task_failed" for e in events["events"])
        conn.close()

    @pytest.mark.asyncio
    async def test_task_id_passthrough(self):
        """Pre-generated task_id is used."""
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(return_value=LLMResponse(content='["s"]'))
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)

        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=(None, None, "err")),
                            _run_batch_for_strategies=MagicMock(
                                return_value=(_mock_batch(), []))):
            task_id = await run_research_task(
                ResearchGoal(description="test", n_hypotheses=1),
                LoopConfig(max_iterations=1),
                task_id="custom123",
            )

        assert task_id == "custom123"
        assert rs.get_task("custom123") is not None
        conn.close()

    @pytest.mark.asyncio
    async def test_event_data_types(self):
        """Verify event data contains correct types."""
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["策略"]'),
            LLMResponse(content='{"direction": "ok"}'),
            LLMResponse(content="总结"),
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)

        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=("s.py", "S", None)),
                            _run_batch_for_strategies=MagicMock(
                                return_value=(_mock_batch(passed_count=1, executed=1, best_sharpe=1.5), ["s1"]))):
            task_id = await run_research_task(
                ResearchGoal(description="test", n_hypotheses=1),
                LoopConfig(max_iterations=1),
            )

        events = get_task_events(task_id)
        for e in events["events"]:
            if e["event"] == "iteration_start":
                assert isinstance(e["data"]["iteration"], int)
                assert isinstance(e["data"]["max_iterations"], int)
            elif e["event"] == "batch_complete":
                assert isinstance(e["data"]["executed"], int)
                assert isinstance(e["data"]["passed"], int)
                assert isinstance(e["data"]["best_sharpe"], float)
            elif e["event"] == "task_complete":
                assert isinstance(e["data"]["total_passed"], int)
                assert "stop_reason" in e["data"]
        conn.close()

    @pytest.mark.asyncio
    async def test_init_failure_marks_done(self):
        """P0-1: If create_provider fails, task must still be marked done."""
        register_task("stuck_task")
        with patch("ez.agent.research.runner.create_provider", side_effect=RuntimeError("no provider")), \
             patch("ez.agent.research.runner.get_research_store") as mock_store:
            mock_store.return_value = MagicMock()
            mock_store.return_value.save_task = MagicMock(side_effect=RuntimeError("no store"))
            await run_research_task(ResearchGoal(description="test"), task_id="stuck_task")

        # Must be done=True (not stuck forever)
        assert _running_tasks["stuck_task"]["done"] is True
        # is_any_task_running should be False
        assert is_any_task_running() is False

    @pytest.mark.asyncio
    async def test_cancel_sets_cancelled_status(self):
        """P0-3: Cancelled task should have status='cancelled', not 'completed'."""
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["策略"]'),  # E1 iter 0
            LLMResponse(content='{"direction": "ok"}'),  # E4 iter 0
            LLMResponse(content='["策略2"]'),  # E1 iter 1 — cancel flag already set
            LLMResponse(content='{}'),  # E4 won't reach
            LLMResponse(content="总结"),  # E6
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)

        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=(None, None, "skip")),
                            _run_batch_for_strategies=MagicMock(
                                return_value=(_mock_batch(), []))):
            task_id = "cancel_test"
            register_task(task_id)
            # Set cancel after first iteration completes
            original_update = LoopController.update
            def cancel_on_first_update(self, state, batch, llm):
                new_state = original_update(self, state, batch, llm)
                # After first iteration, set cancel
                if new_state.iteration == 1:
                    cancel_task(task_id)
                return new_state
            with patch.object(LoopController, "update", cancel_on_first_update):
                await run_research_task(
                    ResearchGoal(description="test", n_hypotheses=1),
                    LoopConfig(max_iterations=100),
                    task_id=task_id,
                )

        task = rs.get_task(task_id)
        assert task["status"] == "cancelled"
        assert "取消" in task["stop_reason"]
        conn.close()

    @pytest.mark.asyncio
    async def test_spec_ids_persisted(self):
        """spec_ids should be persisted in iterations (not empty)."""
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["s"]'),
            LLMResponse(content='{"direction": "ok"}'),
            LLMResponse(content="总结"),
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)

        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=("s.py", "S", None)),
                            _run_batch_for_strategies=MagicMock(
                                return_value=(_mock_batch(), ["spec_abc_123"]))):
            task_id = await run_research_task(
                ResearchGoal(description="test", n_hypotheses=1),
                LoopConfig(max_iterations=1),
            )

        import json
        iters = rs.get_iterations(task_id)
        spec_ids = json.loads(iters[0]["spec_ids"])
        assert spec_ids == ["spec_abc_123"]
        conn.close()

    @pytest.mark.asyncio
    async def test_batch_timeout_breaks_loop(self):
        """Regression test for codex finding: research pipeline previously had
        no timeout on run_batch, so a stuck user strategy would hang the task
        indefinitely. Now wrapped in asyncio.wait_for with per-batch timeout.
        """
        import threading
        import time as _time
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["策略"]'),  # E1
            LLMResponse(content="总结"),  # E6 (skipped since not cancelled)
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)

        # Signal event so the stuck thread can be released when the test is done,
        # letting pytest exit cleanly (Python can't force-kill threads).
        release = threading.Event()
        def _stuck_batch(*args, **kwargs):
            # Wait on event with a hard upper bound so the thread eventually dies
            # even if the test forgets to set it.
            release.wait(timeout=10)
            return (_mock_batch(), [])

        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=("s.py", "Strat", None)),
                            _run_batch_for_strategies=MagicMock(side_effect=_stuck_batch),
                            # Shrink batch timeout to 1s for fast test
                            _batch_timeout_sec=MagicMock(return_value=1)):
            start = _time.time()
            task_id = await run_research_task(
                ResearchGoal(description="test", n_hypotheses=1),
                LoopConfig(max_iterations=1),
            )
            elapsed = _time.time() - start

        # Release the stuck thread so pytest can exit cleanly
        release.set()

        task = rs.get_task(task_id)
        assert "batch_timeout" in (task.get("stop_reason") or ""), (
            f"Expected batch_timeout in stop_reason, got: {task.get('stop_reason')}"
        )
        # The key invariant: the task returned in seconds, not minutes
        assert elapsed < 10, f"Task hung for {elapsed:.1f}s (expected <10s via batch_timeout=1)"
        conn.close()

    @pytest.mark.asyncio
    async def test_cancel_skips_e6_llm_summary(self):
        """Regression test for codex finding: cancelled tasks should skip the
        E6 LLM summary call to make cancel take effect promptly.

        When state.cancelled is True, build_report is called with provider=None
        which causes it to skip the LLM summary generation.
        """
        mock_provider = MagicMock()
        mock_provider.achat = AsyncMock(side_effect=[
            LLMResponse(content='["策略"]'),  # E1 iter 0
        ])
        conn = duckdb.connect(":memory:")
        rs = ResearchStore(conn)

        # Cancel the task immediately on first cancel check (before E2 starts)
        with patch.multiple("ez.agent.research.runner",
                            create_provider=MagicMock(return_value=mock_provider),
                            get_research_store=MagicMock(return_value=rs),
                            get_experiment_store=MagicMock(),
                            _fetch_data=MagicMock(return_value=_make_test_data()),
                            generate_strategy_code=AsyncMock(return_value=(None, None, "skip")),
                            _run_batch_for_strategies=MagicMock(return_value=(_mock_batch(), []))):
            task_id = "cancel_skip_e6"
            register_task(task_id)
            # Cancel right away
            _running_tasks[task_id]["cancel"] = True
            await run_research_task(
                ResearchGoal(description="test", n_hypotheses=1),
                LoopConfig(max_iterations=10),
                task_id=task_id,
            )

        # E6 LLM summary call count: should be 0 (only E1 consumed side_effect[0])
        # If E6 had been called with provider, it would consume another side_effect
        # and raise StopIteration. No StopIteration means E6 skipped LLM correctly.
        task = rs.get_task(task_id)
        assert task["status"] == "cancelled"
        # mock_provider.achat should have been called exactly 1 time (E1 only, E6 skipped)
        # Note: E1 may be called 0 times too, depending on how fast cancel propagates
        assert mock_provider.achat.call_count <= 1, (
            f"E6 LLM summary not skipped on cancel — achat called {mock_provider.achat.call_count} times"
        )
        conn.close()
