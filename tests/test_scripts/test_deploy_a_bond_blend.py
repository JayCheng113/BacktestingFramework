from __future__ import annotations

import pytest
from types import SimpleNamespace

from ez.portfolio.walk_forward import portfolio_significance
from scripts.deploy_a_bond_blend import (
    _build_run_metrics,
    _build_truthful_wf_metrics,
    _validate_deploy_gate_compatibility,
)


def test_build_truthful_wf_metrics_uses_real_walk_forward_outputs() -> None:
    oos_equity_curve = [
        1_000_000.0,
        1_002_500.0,
        999_000.0,
        1_006_000.0,
        1_004_500.0,
        1_010_000.0,
        1_012_000.0,
        1_009_500.0,
    ]
    wf_result = SimpleNamespace(
        n_splits=3,
        is_sharpes=[1.2, 0.9, 1.5],
        oos_equity_curve=oos_equity_curve,
        oos_metrics={"sharpe_ratio": 0.37, "total_return": 0.012},
        overfitting_score=0.18,
        degradation=0.18,
    )

    metrics = _build_truthful_wf_metrics(wf_result)

    sig = portfolio_significance(oos_equity_curve, seed=42)
    assert metrics["is_sharpe"] == 1.2
    assert metrics["oos_sharpe"] == 0.37
    assert metrics["overfitting_score"] == 0.18
    assert metrics["degradation"] == 0.18
    assert metrics["n_splits"] == 3
    assert metrics["p_value"] == sig.monte_carlo_p_value
    assert metrics["oos_return"] == 0.012


def test_build_run_metrics_writes_canonical_and_legacy_trade_keys() -> None:
    metrics = _build_run_metrics(
        {"sharpe": 1.1, "ann_ret": 0.18, "max_drawdown": -0.12},
        trades=[{"id": 1}, {"id": 2}, {"id": 3}],
    )

    assert metrics["sharpe_ratio"] == 1.1
    assert metrics["annualized_return"] == 0.18
    assert metrics["max_drawdown"] == -0.12
    assert metrics["trade_count"] == 3
    assert metrics["total_trades"] == 3


def test_validate_deploy_gate_compatibility_allows_gate_compatible_weight() -> None:
    _validate_deploy_gate_compatibility(bond_weight=0.4, skip_gate=False)


def test_validate_deploy_gate_compatibility_allows_skip_gate_override() -> None:
    _validate_deploy_gate_compatibility(bond_weight=0.5, skip_gate=True)


def test_validate_deploy_gate_compatibility_rejects_unapprovable_default() -> None:
    with pytest.raises(ValueError, match="max_concentration"):
        _validate_deploy_gate_compatibility(bond_weight=0.5, skip_gate=False)
