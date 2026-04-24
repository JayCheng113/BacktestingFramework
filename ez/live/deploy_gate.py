"""V2.15 A3: DeployGate — non-bypassable 10-check hard deployment gate.

4-phase evaluation:
  Phase 0: Source run existence
  Phase 1: Research metrics (from portfolio_runs DB row)
  Phase 2: Walk-forward metrics (from portfolio_runs.wf_metrics, server-side)
  Phase 3: Deploy-specific (backtest days, symbols, concentration, WFO, freq)

All params are required. No Optional bypass. Reuses GateReason/GateVerdict
from ez/agent/gates.py for consistent verdict format.

V2.15.1 S1: WF metrics now read from DB (portfolio_runs.wf_metrics column),
eliminating the client trust boundary. The /walk-forward endpoint writes
WF metrics to the source run when source_run_id is provided.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ez.agent.gates import GateReason, GateVerdict


@dataclass
class DeployGateConfig:
    """Configurable thresholds for deployment gate (stricter than research gate)."""

    # Research gate thresholds (stricter defaults)
    min_sharpe: float = 0.5
    max_drawdown: float = 0.25
    min_trades: int = 20
    max_p_value: float = 0.05
    max_overfitting_score: float = 0.3
    # Deploy-specific
    min_backtest_days: int = 504
    require_wfo: bool = True
    min_symbols: int = 5
    max_concentration: float = 0.4


class DeployGate:
    """Non-bypassable deployment gate with 10 checks.

    Usage::

        gate = DeployGate()
        verdict = gate.evaluate(spec, source_run_id, portfolio_store)
        if not verdict.passed:
            for r in verdict.failed_reasons:
                print(r.rule, r.message)
    """

    def __init__(self, config: DeployGateConfig | None = None):
        self.config = config or DeployGateConfig()

    def evaluate(
        self,
        spec,
        source_run_id: str,
        portfolio_store,
    ) -> GateVerdict:
        """4-phase hard check. All params required. No Optional.

        V2.15.1 S1: WF metrics (p_value, overfitting_score) are now read from
        portfolio_runs.wf_metrics (server-side, written by /walk-forward endpoint).
        This eliminates the V2.15 trust boundary where clients could forge WF metrics.
        """
        reasons: list[GateReason] = []

        # ---------------------------------------------------------------
        # Phase 0: source run exists
        # ---------------------------------------------------------------
        run = portfolio_store.get_run(source_run_id)
        if not run:
            return GateVerdict(
                passed=False,
                reasons=[
                    GateReason(
                        rule="source_run_exists",
                        passed=False,
                        value=0,
                        threshold=1,
                        message=f"来源回测 {source_run_id} 不存在",
                    )
                ],
            )

        # ---------------------------------------------------------------
        # Parse metrics from the run (handle both dict and JSON string)
        # ---------------------------------------------------------------
        metrics = self._parse_json_field(run, "metrics", {})

        # ---------------------------------------------------------------
        # Phase 1: Research metrics (from DB)
        # ---------------------------------------------------------------
        sharpe = metrics.get("sharpe_ratio", 0)
        reasons.append(
            GateReason(
                rule="min_sharpe",
                passed=sharpe >= self.config.min_sharpe,
                value=sharpe,
                threshold=self.config.min_sharpe,
                message=f"夏普 {sharpe:.2f}",
            )
        )

        dd = abs(metrics.get("max_drawdown", -1.0))
        reasons.append(
            GateReason(
                rule="max_drawdown",
                passed=dd <= self.config.max_drawdown,
                value=dd,
                threshold=self.config.max_drawdown,
                message=f"最大回撤 {dd:.1%}",
            )
        )

        trades_count = (
            metrics.get("trade_count")
            if "trade_count" in metrics
            else metrics.get("total_trades", run.get("trade_count", 0))
        )
        reasons.append(
            GateReason(
                rule="min_trades",
                passed=trades_count >= self.config.min_trades,
                value=trades_count,
                threshold=self.config.min_trades,
                message=f"交易次数 {trades_count}",
            )
        )

        # ---------------------------------------------------------------
        # Phase 2: WF metrics (from portfolio_runs.wf_metrics, server-side)
        # ---------------------------------------------------------------
        wf_metrics = self._parse_json_field(run, "wf_metrics", {})
        p_value = wf_metrics.get("p_value", 1.0)
        reasons.append(
            GateReason(
                rule="max_p_value",
                passed=p_value <= self.config.max_p_value,
                value=p_value,
                threshold=self.config.max_p_value,
                message=f"显著性 p={p_value:.3f}",
            )
        )

        overfit = wf_metrics.get("overfitting_score", 1.0)
        reasons.append(
            GateReason(
                rule="max_overfitting_score",
                passed=overfit <= self.config.max_overfitting_score,
                value=overfit,
                threshold=self.config.max_overfitting_score,
                message=f"过拟合评分 {overfit:.2f}",
            )
        )

        # ---------------------------------------------------------------
        # Phase 3: Deploy-specific
        # ---------------------------------------------------------------

        # Backtest days from dates field
        dates_list = self._parse_json_field(run, "dates", [])
        n_days = len(dates_list)
        reasons.append(
            GateReason(
                rule="min_backtest_days",
                passed=n_days >= self.config.min_backtest_days,
                value=n_days,
                threshold=self.config.min_backtest_days,
                message=f"回测天数 {n_days}",
            )
        )

        # Symbol count from spec
        n_syms = len(spec.symbols)
        reasons.append(
            GateReason(
                rule="min_symbols",
                passed=n_syms >= self.config.min_symbols,
                value=n_syms,
                threshold=self.config.min_symbols,
                message=f"标的数 {n_syms}",
            )
        )

        # Concentration from rebalance_weights
        # Format: [{"date": "...", "weights": {"A": 0.3}}, ...] OR [{"A": 0.3}, ...]
        reb_list = self._parse_json_field(run, "rebalance_weights", [])
        max_w = self._compute_max_concentration(reb_list)
        reasons.append(
            GateReason(
                rule="max_concentration",
                passed=max_w <= self.config.max_concentration,
                value=max_w,
                threshold=self.config.max_concentration,
                message=f"全周期最大单股权重 {max_w:.1%}",
            )
        )

        # WFO required
        if self.config.require_wfo:
            has_wfo = p_value < 1.0 and overfit < 1.0
            reasons.append(
                GateReason(
                    rule="require_wfo",
                    passed=has_wfo,
                    value=int(has_wfo),
                    threshold=1,
                    message="前推验证" + ("已完成" if has_wfo else "未执行"),
                )
            )

        # Freq valid
        reasons.append(
            GateReason(
                rule="freq_valid",
                passed=spec.freq in ("daily", "weekly", "monthly", "quarterly"),
                value=0,
                threshold=0,
                message=f"调仓频率 {spec.freq}",
            )
        )

        return GateVerdict(
            passed=all(r.passed for r in reasons), reasons=reasons
        )

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _parse_json_field(run: dict, key: str, default):
        """Extract a field from run dict, parsing JSON string if needed."""
        raw = run.get(key)
        if raw is None:
            return default
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return default
        return raw

    @staticmethod
    def _compute_max_concentration(reb_list: list) -> float:
        """Find the maximum single-symbol weight across all rebalance entries."""
        max_w = 0.0
        for entry in reb_list:
            if not isinstance(entry, dict):
                continue
            # Support both {"date": ..., "weights": {...}} and plain {"A": 0.3} format
            w_dict = entry.get("weights", entry) if "weights" in entry else entry
            if not isinstance(w_dict, dict):
                continue
            vals = [v for v in w_dict.values() if isinstance(v, (int, float))]
            if vals:
                max_w = max(max_w, max(vals))
        # No rebalance data -> concentration is 1.0 (worst case)
        if not reb_list or max_w == 0.0:
            max_w = 1.0
        return max_w
