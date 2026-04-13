"""V2.17: strategy.state cross-restart persistence.

Known Limitation before V2.17: PaperTradingEngine's strategy instance
was lost on process restart. `_start_engine` re-instantiated a fresh
strategy via `_instantiate(spec)`, wiping:
- MLAlpha's trained sklearn model (expensive to retrain)
- StrategyEnsemble's hypothetical-return ledger
- Any `self.*` fields custom user strategies kept

This test pins the restore contract:
1. After a successful tick, `deployment_snapshots.strategy_state` holds
   a pickle blob of the strategy.
2. A subsequent `_start_engine` retrieves that blob and swaps the
   freshly-constructed strategy with the restored one.
3. If pickle fails (unpicklable attrs), NULL is stored and restart
   falls back silently to fresh construction — no crash.
4. If unpickle fails (class renamed, format drift), restart falls back
   silently.
5. Class-name mismatch between stored pickle and current spec is
   detected (prevents restoring an MLAlpha over a TopNRotation).
"""
from __future__ import annotations

import pickle
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ez.live.deployment_store import DeploymentStore
from ez.live.scheduler import (
    _pickle_strategy, _unpickle_strategy,
    _pickle_failure_warned, _unpickle_failure_warned,
)


# ---------------------------------------------------------------------------
# Picklable / unpicklable strategy fixtures
# ---------------------------------------------------------------------------

class StatefulStrategy:
    """Picklable strategy with mutable state — mimics MLAlpha's
    'trained model persists across ticks' pattern."""
    lookback_days = 5

    def __init__(self):
        self.tick_count = 0
        self.fitted_param = None

    def generate_weights(self, universe_data, date_, prev_weights, prev_returns):
        self.tick_count += 1
        self.fitted_param = f"after_tick_{self.tick_count}"
        return {}


class UnpicklableStrategy:
    """Strategy holding a lambda (unpicklable)."""
    lookback_days = 5

    def __init__(self):
        self.model = lambda x: x  # lambdas can't be pickled

    def generate_weights(self, universe_data, date_, prev_weights, prev_returns):
        return {}


# Module-level so pickle can find them by qualname (test fixtures for
# class-mismatch test below)
class _MismatchAlpha:
    lookback_days = 5


class _MismatchBeta:
    lookback_days = 5


# ---------------------------------------------------------------------------
# Store round-trip
# ---------------------------------------------------------------------------

def _tmp_store(tmp_path: Path) -> DeploymentStore:
    """DeploymentStore takes a DuckDB connection, not a path."""
    import duckdb
    conn = duckdb.connect(str(tmp_path / "test.db"))
    return DeploymentStore(conn=conn)


def test_save_and_read_strategy_state_roundtrip(tmp_path) -> None:
    """save_daily_snapshot accepts strategy_state bytes and
    get_latest_strategy_state returns them unchanged."""
    store = _tmp_store(tmp_path)
    blob = pickle.dumps(StatefulStrategy())
    store.save_daily_snapshot(
        deployment_id="dep-A",
        snapshot_date=date(2024, 1, 2),
        result={"equity": 100_000, "cash": 100_000, "holdings": {}, "weights": {}},
        strategy_state=blob,
    )
    back = store.get_latest_strategy_state("dep-A")
    assert back == blob
    restored = pickle.loads(back)
    assert isinstance(restored, StatefulStrategy)


def test_no_strategy_state_returns_none(tmp_path) -> None:
    """Pre-V2.17 snapshots (no state column written) or opt-out saves
    return None from the getter, letting _start_engine degrade to
    fresh strategy construction."""
    store = _tmp_store(tmp_path)
    store.save_daily_snapshot(
        deployment_id="dep-B",
        snapshot_date=date(2024, 1, 2),
        result={"equity": 100_000, "cash": 100_000, "holdings": {}, "weights": {}},
        # strategy_state omitted (defaults to None)
    )
    assert store.get_latest_strategy_state("dep-B") is None


def test_latest_state_is_most_recent_non_null(tmp_path) -> None:
    """Multi-day: getter returns the most recent non-null blob,
    skipping days where state wasn't saved (e.g., a failure)."""
    store = _tmp_store(tmp_path)
    early = pickle.dumps(("early", 1))
    late = pickle.dumps(("late", 99))
    # Day 1: state saved
    store.save_daily_snapshot(
        "dep-C", date(2024, 1, 1),
        {"equity": 1, "cash": 1, "holdings": {}, "weights": {}},
        strategy_state=early,
    )
    # Day 2: no state (e.g., pickle failed)
    store.save_daily_snapshot(
        "dep-C", date(2024, 1, 2),
        {"equity": 2, "cash": 2, "holdings": {}, "weights": {}},
    )
    # Day 3: state saved
    store.save_daily_snapshot(
        "dep-C", date(2024, 1, 3),
        {"equity": 3, "cash": 3, "holdings": {}, "weights": {}},
        strategy_state=late,
    )
    assert store.get_latest_strategy_state("dep-C") == late

    # Day 4: no state again — still returns late (latest non-null)
    store.save_daily_snapshot(
        "dep-C", date(2024, 1, 4),
        {"equity": 4, "cash": 4, "holdings": {}, "weights": {}},
    )
    assert store.get_latest_strategy_state("dep-C") == late


# ---------------------------------------------------------------------------
# _pickle_strategy / _unpickle_strategy helpers
# ---------------------------------------------------------------------------

def test_pickle_strategy_success() -> None:
    s = StatefulStrategy()
    s.tick_count = 42
    blob = _pickle_strategy(s, "dep-ok")
    assert blob is not None
    restored = pickle.loads(blob)
    assert restored.tick_count == 42


def test_pickle_strategy_unpicklable_returns_none_and_warns_once(caplog) -> None:
    _pickle_failure_warned.clear()  # fresh state per test
    s = UnpicklableStrategy()
    with caplog.at_level("WARNING"):
        b1 = _pickle_strategy(s, "dep-unpicklable")
    assert b1 is None
    assert "unpicklable" in caplog.text
    # Second attempt: still None, NO second log
    caplog.clear()
    with caplog.at_level("WARNING"):
        b2 = _pickle_strategy(s, "dep-unpicklable")
    assert b2 is None
    assert caplog.text == ""  # silenced


def test_unpickle_strategy_success() -> None:
    s = StatefulStrategy()
    s.tick_count = 7
    blob = pickle.dumps(s)
    restored = _unpickle_strategy(blob, "dep-x")
    assert restored.tick_count == 7


def test_unpickle_strategy_bad_blob_returns_none_and_warns(caplog) -> None:
    _unpickle_failure_warned.clear()
    with caplog.at_level("WARNING"):
        out = _unpickle_strategy(b"not a valid pickle", "dep-bad")
    assert out is None
    assert "failed to restore" in caplog.text


def test_stateful_strategy_restore_preserves_internal_state() -> None:
    """End-to-end logical check: pickle a strategy with non-trivial
    state, round-trip through _pickle_strategy / _unpickle_strategy,
    verify state reconstruction is bit-exact.
    """
    s = StatefulStrategy()
    # Simulate 3 tick() calls
    for _ in range(3):
        s.generate_weights({}, None, {}, {})
    assert s.tick_count == 3
    assert s.fitted_param == "after_tick_3"

    blob = _pickle_strategy(s, "dep-round")
    assert blob is not None
    restored = _unpickle_strategy(blob, "dep-round")
    assert restored is not None
    assert restored.tick_count == 3
    assert restored.fitted_param == "after_tick_3"
    # And restored strategy continues ticking from where it left off
    restored.generate_weights({}, None, {}, {})
    assert restored.tick_count == 4


# ---------------------------------------------------------------------------
# Scheduler integration: class-name guard
# ---------------------------------------------------------------------------

def test_start_engine_ignores_mismatched_class_pickle(tmp_path, monkeypatch) -> None:
    """If the stored pickle is from a DIFFERENT strategy class than
    what the current spec says, _start_engine must NOT swap it in.
    Prevents: spec changed strategy_name from MLAlpha to TopNRotation,
    DB still has MLAlpha pickle — restoring MLAlpha over TopNRotation
    is silently wrong."""
    from ez.live.scheduler import Scheduler

    # Store has a _MismatchAlpha pickle, but _instantiate returns _MismatchBeta
    store = _tmp_store(tmp_path)
    store.save_daily_snapshot(
        "dep-mismatch", date(2024, 1, 1),
        {"equity": 1, "cash": 1, "holdings": {}, "weights": {}},
        strategy_state=pickle.dumps(_MismatchAlpha()),
    )

    sched = Scheduler(store=store, data_chain=MagicMock())

    # Stub out record + spec lookup + _instantiate to return Beta
    beta = _MismatchBeta()
    spec = MagicMock(
        symbols=(), market="cn_stock", freq="daily",
        initial_cash=1000.0, stamp_tax_rate=0.0, lot_size=1,
        price_limit_pct=0.0, t_plus_1=False,
    )
    record = MagicMock(deployment_id="dep-mismatch", spec_id="sp")
    monkeypatch.setattr(store, "get_record", lambda _id: record)
    monkeypatch.setattr(store, "get_spec", lambda _id: spec)
    monkeypatch.setattr(
        sched, "_instantiate",
        lambda _spec: (beta, None, None),
    )
    monkeypatch.setattr(sched, "_restore_full_state", lambda _e, _d: None)

    import asyncio
    asyncio.run(sched._start_engine("dep-mismatch"))

    # Engine strategy should be the FRESH beta, not restored Alpha.
    # Class-name mismatch blocked the swap.
    eng = sched._engines["dep-mismatch"]
    assert type(eng.strategy).__name__ == "_MismatchBeta"
