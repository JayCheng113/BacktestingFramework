"""Tests for V2.12 risk manager — drawdown + turnover."""
import pytest


class TestDrawdownStateMachine:
    def test_no_event_when_no_drawdown(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20))
        scale, event = rm.check_drawdown(1_000_000)
        assert scale == 1.0
        assert event is None

    def test_breach_on_large_drawdown(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20, drawdown_reduce_ratio=0.50))
        rm.check_drawdown(1_000_000)
        scale, event = rm.check_drawdown(750_000)  # 25% drawdown > 20%
        assert scale == 0.50
        assert event is not None
        assert "减仓" in event

    def test_stays_breached_until_recovery(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20,
                                    drawdown_reduce_ratio=0.50,
                                    drawdown_recovery_ratio=0.10))
        rm.check_drawdown(1_000_000)
        rm.check_drawdown(750_000)  # breach
        scale, event = rm.check_drawdown(800_000)  # 20% dd, still breached
        assert scale == 0.50
        assert event is None  # no repeat event

    def test_recovery_unbreaches(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20,
                                    drawdown_reduce_ratio=0.50,
                                    drawdown_recovery_ratio=0.10))
        rm.check_drawdown(1_000_000)
        rm.check_drawdown(750_000)  # breach
        scale, event = rm.check_drawdown(950_000)  # 5% dd < 10% recovery
        assert scale == 1.0
        assert event is not None
        assert "解除" in event

    def test_re_breach_after_recovery(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.20,
                                    drawdown_reduce_ratio=0.50,
                                    drawdown_recovery_ratio=0.10))
        rm.check_drawdown(1_000_000)
        rm.check_drawdown(750_000)  # breach
        rm.check_drawdown(950_000)  # recover
        # New peak is still 1M; drop again
        scale, event = rm.check_drawdown(700_000)  # 30% dd
        assert scale == 0.50
        assert "减仓" in event


class TestTurnoverMixing:
    def test_within_limit_no_change(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_turnover=0.50))
        new_w = {"A": 0.4, "B": 0.3, "C": 0.3}
        prev_w = {"A": 0.3, "B": 0.3, "C": 0.4}
        result, event = rm.check_turnover(new_w, prev_w)
        assert result == new_w
        assert event is None

    def test_exceeds_limit_mixes(self):
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_turnover=0.30))
        new_w = {"A": 0.8, "B": 0.2}
        prev_w = {"A": 0.2, "B": 0.8}
        # Single-sided turnover: buy_side=0.6, sell_side=0.6, max=0.6 > 0.3
        result, event = rm.check_turnover(new_w, prev_w)
        assert event is not None
        assert "混合" in event
        assert 0.2 < result["A"] < 0.8
        assert 0.2 < result["B"] < 0.8

    def test_new_symbol_entry(self):
        """Turnover mixing handles new symbols not in prev_weights."""
        from ez.portfolio.risk_manager import RiskManager, RiskConfig
        rm = RiskManager(RiskConfig(max_turnover=0.10))
        new_w = {"A": 0.5, "B": 0.3, "C": 0.2}
        prev_w = {"A": 0.7, "B": 0.3}  # C is new
        # Single-sided: buy_side=max(0,0.5-0.7)+max(0,0)+max(0,0.2-0)=0.2
        #               sell_side=max(0,0.7-0.5)+max(0,0)+max(0,0)=0.2
        #               turnover=0.2 > 0.10
        result, event = rm.check_turnover(new_w, prev_w)
        assert event is not None
        assert "C" in result
