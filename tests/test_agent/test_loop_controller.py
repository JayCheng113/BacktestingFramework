"""Tests for the research loop controller."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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
        assert s.best_sharpe == float("-inf")
        assert s.cancelled is False


class TestShouldContinue:
    def test_fresh_state_continues(self):
        ok, _ = LoopController(LoopConfig()).should_continue(LoopState())
        assert ok is True

    def test_cancelled_stops(self):
        ok, reason = LoopController(LoopConfig()).should_continue(LoopState(cancelled=True))
        assert ok is False
        assert "取消" in reason

    def test_max_iterations_stops(self):
        ok, reason = LoopController(LoopConfig(max_iterations=3)).should_continue(LoopState(iteration=3))
        assert ok is False
        assert "轮次" in reason

    def test_max_specs_stops(self):
        ok, reason = LoopController(LoopConfig(max_specs=100)).should_continue(LoopState(specs_executed=100))
        assert ok is False
        assert "回测" in reason

    def test_max_llm_calls_stops(self):
        ok, reason = LoopController(LoopConfig(max_llm_calls=50)).should_continue(LoopState(llm_calls=50))
        assert ok is False
        assert "LLM" in reason

    def test_no_improvement_stops(self):
        ok, reason = LoopController(LoopConfig(no_improve_limit=3)).should_continue(
            LoopState(no_improve_streak=3, iteration=4))
        assert ok is False
        assert "无新通过" in reason

    def test_under_limits_continues(self):
        ok, _ = LoopController(LoopConfig(max_iterations=10)).should_continue(
            LoopState(iteration=5, specs_executed=200, llm_calls=40))
        assert ok is True


class TestUpdate:
    def _mock_batch(self, passed_count, executed, best_sharpe=1.0):
        result = MagicMock()
        result.executed = executed
        passed_list = [MagicMock(sharpe=best_sharpe - i * 0.1) for i in range(passed_count)]
        result.passed = passed_list
        return result

    def test_update_with_passed(self):
        ctrl = LoopController(LoopConfig())
        state = LoopState()
        new = ctrl.update(state, self._mock_batch(2, 5, 1.5), llm_calls_this_round=8)
        assert new.iteration == 1
        assert new.specs_executed == 5
        assert new.llm_calls == 8
        assert new.gate_passed_total == 2
        assert new.best_sharpe == 1.5
        assert new.no_improve_streak == 0

    def test_update_no_passed_increments_streak(self):
        ctrl = LoopController(LoopConfig())
        state = LoopState(iteration=1, no_improve_streak=1)
        new = ctrl.update(state, self._mock_batch(0, 3), llm_calls_this_round=5)
        assert new.iteration == 2
        assert new.no_improve_streak == 2

    def test_update_passed_resets_streak(self):
        ctrl = LoopController(LoopConfig())
        state = LoopState(iteration=2, no_improve_streak=2, best_sharpe=0.8)
        new = ctrl.update(state, self._mock_batch(1, 4, 1.2), llm_calls_this_round=6)
        assert new.no_improve_streak == 0
        assert new.best_sharpe == 1.2

    def test_update_preserves_cancelled(self):
        ctrl = LoopController(LoopConfig())
        state = LoopState(cancelled=True)
        new = ctrl.update(state, self._mock_batch(0, 1), llm_calls_this_round=1)
        assert new.cancelled is True

    def test_update_accumulates_across_iterations(self):
        ctrl = LoopController(LoopConfig())
        s = LoopState()
        s = ctrl.update(s, self._mock_batch(1, 5, 1.0), llm_calls_this_round=3)
        s = ctrl.update(s, self._mock_batch(2, 3, 1.5), llm_calls_this_round=4)
        assert s.iteration == 2
        assert s.specs_executed == 8
        assert s.llm_calls == 7
        assert s.gate_passed_total == 3
        assert s.best_sharpe == 1.5


class TestStopPrecedence:
    """Cancel takes precedence over all other stop conditions."""

    def test_cancel_over_budget(self):
        ctrl = LoopController(LoopConfig(max_iterations=1))
        state = LoopState(cancelled=True, iteration=5)
        ok, reason = ctrl.should_continue(state)
        assert "取消" in reason

    def test_boundary_just_under_limit(self):
        ctrl = LoopController(LoopConfig(max_iterations=10))
        ok, _ = ctrl.should_continue(LoopState(iteration=9))
        assert ok is True

    def test_boundary_at_limit(self):
        ctrl = LoopController(LoopConfig(max_iterations=10))
        ok, _ = ctrl.should_continue(LoopState(iteration=10))
        assert ok is False
