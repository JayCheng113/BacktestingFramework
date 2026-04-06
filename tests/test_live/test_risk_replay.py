import pytest
from ez.portfolio.risk_manager import RiskManager, RiskConfig

class TestReplayEquity:
    def test_replay_restores_peak_and_breach_state(self):
        rm1 = RiskManager(RiskConfig(max_drawdown_threshold=0.1))
        curve = [100, 105, 110, 95, 90, 85]
        for eq in curve:
            rm1.check_drawdown(eq)
        assert rm1._is_breached is True
        assert rm1._peak_equity == 110

        rm2 = RiskManager(RiskConfig(max_drawdown_threshold=0.1))
        rm2.replay_equity(curve)
        assert rm2._is_breached == rm1._is_breached
        assert rm2._peak_equity == rm1._peak_equity

    def test_replay_empty_curve(self):
        rm = RiskManager(RiskConfig())
        rm.replay_equity([])
        assert rm._peak_equity == 0.0
        assert rm._is_breached is False

    def test_replay_recovery(self):
        rm = RiskManager(RiskConfig(max_drawdown_threshold=0.1, drawdown_recovery_ratio=0.02))
        curve = [100, 110, 95, 85, 90, 95, 100, 105, 110]  # breach then recover
        rm.replay_equity(curve)
        assert rm._is_breached is False  # recovered
        assert rm._peak_equity == 110
