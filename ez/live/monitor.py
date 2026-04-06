"""V2.15 B2: Monitor — deployment health dashboard and alert checks.

Provides:
- DeploymentHealth dataclass: per-deployment health summary
- Monitor class: get_dashboard() + check_alerts()

Metric computation mirrors ez/backtest/metrics.py conventions (ddof=1, rf=0.03).
No network or strategy calls; pure DB reads + arithmetic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from ez.live.deployment_store import DeploymentStore

# Risk-free rate used for Sharpe (annualised); matches MetricsCalculator default
_RF = 0.03
_TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# DeploymentHealth
# ---------------------------------------------------------------------------

@dataclass
class DeploymentHealth:
    deployment_id: str
    name: str
    status: str  # running / paused / stopped / error

    # Performance
    cumulative_return: float
    max_drawdown: float
    sharpe_ratio: float | None

    # Today
    today_pnl: float
    today_trades: int

    # Risk
    risk_events_today: int
    total_risk_events: int
    consecutive_loss_days: int

    # System
    last_execution_date: date | None
    last_execution_duration_ms: float
    days_since_last_trade: int
    error_count: int


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class Monitor:
    """Read-only view over DeploymentStore — health dashboard + alert checks."""

    # Default alert thresholds (can be overridden per-instance)
    DEFAULT_MAX_DRAWDOWN_THRESHOLD: float = 0.25
    DEFAULT_MAX_EXECUTION_DURATION_MS: float = 60_000.0  # 60 s
    DEFAULT_MAX_CONSECUTIVE_ERRORS: int = 3
    DEFAULT_MAX_CONSECUTIVE_LOSS_DAYS: int = 5
    DEFAULT_MAX_DAYS_SINCE_LAST_TRADE: int = 30

    def __init__(self, store: DeploymentStore):
        self.store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_dashboard(self) -> list[DeploymentHealth]:
        """Return health summary for all active deployments (running + paused + error)."""
        active_statuses = ("running", "paused", "error")
        records = [
            r for r in self.store.list_deployments()
            if r.status in active_statuses
        ]
        return [self._build_health(r) for r in records]

    def check_alerts(self) -> list[dict[str, Any]]:
        """Check 5 alert conditions across all active deployments.

        Alert types:
        1. consecutive_loss_days  — streak > DEFAULT_MAX_CONSECUTIVE_LOSS_DAYS
        2. high_drawdown          — max_drawdown < -threshold (i.e. worse than threshold)
        3. slow_execution         — last_execution_duration_ms > DEFAULT_MAX_EXECUTION_DURATION_MS
        4. consecutive_errors     — error_count > DEFAULT_MAX_CONSECUTIVE_ERRORS
        5. inactivity             — days_since_last_trade > DEFAULT_MAX_DAYS_SINCE_LAST_TRADE

        Returns a list of {deployment_id, alert_type, message} dicts.
        """
        dashboard = self.get_dashboard()
        alerts: list[dict[str, Any]] = []

        for h in dashboard:
            # 1. Consecutive loss days
            if h.consecutive_loss_days > self.DEFAULT_MAX_CONSECUTIVE_LOSS_DAYS:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "consecutive_loss_days",
                    "message": (
                        f"{h.name}: {h.consecutive_loss_days} consecutive loss days "
                        f"(threshold {self.DEFAULT_MAX_CONSECUTIVE_LOSS_DAYS})"
                    ),
                })

            # 2. High drawdown (max_drawdown is negative, e.g. -0.30)
            if h.max_drawdown < -self.DEFAULT_MAX_DRAWDOWN_THRESHOLD:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "high_drawdown",
                    "message": (
                        f"{h.name}: max drawdown {h.max_drawdown:.1%} exceeds "
                        f"threshold -{self.DEFAULT_MAX_DRAWDOWN_THRESHOLD:.0%}"
                    ),
                })

            # 3. Slow execution
            if h.last_execution_duration_ms > self.DEFAULT_MAX_EXECUTION_DURATION_MS:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "slow_execution",
                    "message": (
                        f"{h.name}: last execution took "
                        f"{h.last_execution_duration_ms / 1000:.1f}s "
                        f"(threshold {self.DEFAULT_MAX_EXECUTION_DURATION_MS / 1000:.0f}s)"
                    ),
                })

            # 4. Consecutive errors
            if h.error_count > self.DEFAULT_MAX_CONSECUTIVE_ERRORS:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "consecutive_errors",
                    "message": (
                        f"{h.name}: {h.error_count} consecutive errors "
                        f"(threshold {self.DEFAULT_MAX_CONSECUTIVE_ERRORS})"
                    ),
                })

            # 5. Inactivity
            if h.days_since_last_trade > self.DEFAULT_MAX_DAYS_SINCE_LAST_TRADE:
                alerts.append({
                    "deployment_id": h.deployment_id,
                    "alert_type": "inactivity",
                    "message": (
                        f"{h.name}: {h.days_since_last_trade} days since last trade "
                        f"(threshold {self.DEFAULT_MAX_DAYS_SINCE_LAST_TRADE})"
                    ),
                })

        return alerts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_health(self, record) -> DeploymentHealth:
        """Build a DeploymentHealth from a DeploymentRecord + its snapshots."""
        dep_id = record.deployment_id
        snapshots = self.store.get_all_snapshots(dep_id)

        # --- Equity curve ---
        equity_curve = [float(s["equity"]) for s in snapshots if s.get("equity") is not None]

        # --- Cumulative return ---
        if len(equity_curve) >= 2 and equity_curve[0] > 0:
            cumulative_return = (equity_curve[-1] / equity_curve[0]) - 1.0
        elif len(equity_curve) == 1:
            cumulative_return = 0.0
        else:
            cumulative_return = 0.0

        # --- Max drawdown ---
        max_drawdown = _compute_max_drawdown(equity_curve)

        # --- Sharpe ratio ---
        sharpe_ratio = _compute_sharpe(equity_curve, rf=_RF, trading_days=_TRADING_DAYS)

        # --- Today's PnL (last equity - second-to-last equity) ---
        if len(equity_curve) >= 2:
            today_pnl = equity_curve[-1] - equity_curve[-2]
        else:
            today_pnl = 0.0

        # --- Today's trade count (last snapshot) ---
        today_trades = 0
        if snapshots:
            last_snap = snapshots[-1]
            trades = last_snap.get("trades") or []
            today_trades = len(trades)

        # --- Risk events ---
        risk_events_today = 0
        total_risk_events = 0
        if snapshots:
            for snap in snapshots:
                evts = snap.get("risk_events") or []
                total_risk_events += len(evts)
            last_snap = snapshots[-1]
            risk_events_today = len(last_snap.get("risk_events") or [])

        # --- Consecutive loss days ---
        consecutive_loss_days = _count_consecutive_loss_days(equity_curve)

        # --- Last execution date ---
        last_execution_date: date | None = None
        if snapshots:
            raw_date = snapshots[-1].get("snapshot_date")
            if raw_date is not None:
                if isinstance(raw_date, date):
                    last_execution_date = raw_date
                else:
                    try:
                        last_execution_date = date.fromisoformat(str(raw_date))
                    except (ValueError, TypeError):
                        last_execution_date = None

        # --- Last execution duration ---
        last_execution_duration_ms = 0.0
        if snapshots:
            raw_ms = snapshots[-1].get("execution_ms")
            if raw_ms is not None and not (isinstance(raw_ms, float) and math.isnan(raw_ms)):
                last_execution_duration_ms = float(raw_ms)

        # --- Days since last trade ---
        days_since_last_trade = _compute_days_since_last_trade(snapshots)

        # --- Consecutive errors ---
        error_count = self.store.get_error_count(dep_id)

        return DeploymentHealth(
            deployment_id=dep_id,
            name=record.name,
            status=record.status,
            cumulative_return=cumulative_return,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            today_pnl=today_pnl,
            today_trades=today_trades,
            risk_events_today=risk_events_today,
            total_risk_events=total_risk_events,
            consecutive_loss_days=consecutive_loss_days,
            last_execution_date=last_execution_date,
            last_execution_duration_ms=last_execution_duration_ms,
            days_since_last_trade=days_since_last_trade,
            error_count=error_count,
        )


# ---------------------------------------------------------------------------
# Pure-function metric helpers (no store I/O)
# ---------------------------------------------------------------------------

def _compute_max_drawdown(equity_curve: list[float]) -> float:
    """Return max drawdown as a non-positive float (e.g. -0.20 = -20%).

    Returns 0.0 when there are fewer than 2 points.
    """
    if len(equity_curve) < 2:
        return 0.0
    arr = np.array(equity_curve, dtype=float)
    running_max = np.maximum.accumulate(arr)
    # Avoid divide-by-zero on zero peaks
    mask = running_max > 0
    drawdown = np.where(mask, (arr - running_max) / running_max, 0.0)
    return float(drawdown.min())


def _compute_sharpe(
    equity_curve: list[float],
    rf: float = _RF,
    trading_days: int = _TRADING_DAYS,
) -> float | None:
    """Annualised Sharpe ratio (excess returns, ddof=1).

    Returns None if fewer than 3 points (need at least 2 daily returns
    to compute std with ddof=1 without returning 0).
    Returns None on degenerate inputs (zero std, NaN).
    """
    if len(equity_curve) < 3:
        return None
    arr = np.array(equity_curve, dtype=float)
    daily_returns = np.diff(arr) / arr[:-1]
    daily_rf = rf / trading_days
    excess = daily_returns - daily_rf
    n = len(excess)
    if n < 2:
        return None
    std = float(np.std(excess, ddof=1))
    if std < 1e-10 or math.isnan(std):
        return None
    sharpe = float(np.mean(excess) / std * np.sqrt(trading_days))
    if math.isnan(sharpe) or math.isinf(sharpe):
        return None
    return sharpe


def _count_consecutive_loss_days(equity_curve: list[float]) -> int:
    """Count consecutive loss days from the END of the equity curve.

    A loss day is one where equity declined vs the previous point.
    Returns 0 when fewer than 2 points.
    """
    if len(equity_curve) < 2:
        return 0
    count = 0
    for i in range(len(equity_curve) - 1, 0, -1):
        if equity_curve[i] < equity_curve[i - 1]:
            count += 1
        else:
            break
    return count


def _compute_days_since_last_trade(snapshots: list[dict]) -> int:
    """Return the number of snapshots (trading days) since the last trade.

    Looks backwards from the most recent snapshot. If no trade has ever
    occurred, returns len(snapshots) as a conservative estimate.
    """
    if not snapshots:
        return 0
    for offset, snap in enumerate(reversed(snapshots)):
        trades = snap.get("trades") or []
        if len(trades) > 0:
            return offset
    # No trade found in any snapshot
    return len(snapshots)
