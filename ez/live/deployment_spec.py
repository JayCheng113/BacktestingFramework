"""V2.15 A2: DeploymentSpec (immutable, content-hashed) + DeploymentRecord (mutable runtime).

DeploymentSpec captures the full strategy configuration as a frozen, content-addressed
object. Two specs with the same logical configuration (even if symbols are in different
order) produce the same spec_id. This is the foundation for idempotent deployments.

DeploymentRecord tracks the mutable lifecycle of a single deployment (pending -> approved
-> running -> stopped/paused/error).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sort_keys_recursive(obj):
    """Recursively sort dict keys for canonical JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sort_keys_recursive(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_sort_keys_recursive(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# DeploymentSpec — immutable, content-hashed
# ---------------------------------------------------------------------------

class DeploymentSpec:
    """Immutable strategy deployment configuration.

    All fields are frozen after construction via __slots__ + __setattr__ guard.
    The spec_id is a SHA-256[:16] hash of the canonical JSON representation
    of ALL fields, so two specs with the same logical content always produce
    the same spec_id regardless of symbol ordering or dict key order.
    """

    __slots__ = (
        "_strategy_name",
        "_strategy_params_json",  # canonical JSON string (sorted keys)
        "_symbols",               # sorted tuple
        "_market",
        "_freq",
        "_t_plus_1",
        "_price_limit_pct",
        "_lot_size",
        "_buy_commission_rate",
        "_sell_commission_rate",
        "_stamp_tax_rate",
        "_slippage_rate",
        "_min_commission",
        "_optimizer",
        "_optimizer_params_json",  # canonical JSON string
        "_risk_control",
        "_risk_params_json",       # canonical JSON string
        "_initial_cash",
        "_spec_id",
    )

    def __init__(
        self,
        *,
        strategy_name: str,
        strategy_params: dict,
        symbols: tuple[str, ...] | list[str],
        market: str,
        freq: str,
        t_plus_1: bool = True,
        price_limit_pct: float = 0.1,
        lot_size: int = 100,
        buy_commission_rate: float = 0.00008,
        sell_commission_rate: float = 0.00008,
        stamp_tax_rate: float = 0.0005,
        slippage_rate: float = 0.001,
        min_commission: float = 0.0,
        optimizer: str = "",
        optimizer_params: dict | None = None,
        risk_control: bool = False,
        risk_params: dict | None = None,
        initial_cash: float = 1_000_000.0,
    ):
        # Use object.__setattr__ to bypass our guard during __init__
        _set = object.__setattr__
        _set(self, "_strategy_name", strategy_name)
        _set(self, "_strategy_params_json", json.dumps(
            _sort_keys_recursive(strategy_params), sort_keys=True, ensure_ascii=False,
        ))
        _set(self, "_symbols", tuple(sorted(symbols)))
        _set(self, "_market", market)
        _set(self, "_freq", freq)
        _set(self, "_t_plus_1", bool(t_plus_1))
        _set(self, "_price_limit_pct", float(price_limit_pct))
        _set(self, "_lot_size", int(lot_size))
        _set(self, "_buy_commission_rate", float(buy_commission_rate))
        _set(self, "_sell_commission_rate", float(sell_commission_rate))
        _set(self, "_stamp_tax_rate", float(stamp_tax_rate))
        _set(self, "_slippage_rate", float(slippage_rate))
        _set(self, "_min_commission", float(min_commission))
        _set(self, "_optimizer", optimizer or "")
        _set(self, "_optimizer_params_json", json.dumps(
            _sort_keys_recursive(optimizer_params or {}), sort_keys=True, ensure_ascii=False,
        ))
        _set(self, "_risk_control", bool(risk_control))
        _set(self, "_risk_params_json", json.dumps(
            _sort_keys_recursive(risk_params or {}), sort_keys=True, ensure_ascii=False,
        ))
        _set(self, "_initial_cash", float(initial_cash))

        # Compute content hash
        canonical = self._canonical_dict()
        canonical_json = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
        _set(self, "_spec_id", hashlib.sha256(canonical_json.encode()).hexdigest()[:16])

    def __setattr__(self, name, value):
        raise AttributeError(f"DeploymentSpec is immutable: cannot set '{name}'")

    def __delattr__(self, name):
        raise AttributeError(f"DeploymentSpec is immutable: cannot delete '{name}'")

    # -- Properties (read-only access) ------------------------------------

    @property
    def spec_id(self) -> str:
        return self._spec_id

    @property
    def strategy_name(self) -> str:
        return self._strategy_name

    @property
    def strategy_params(self) -> dict:
        """Returns a fresh dict copy each time (caller cannot mutate internal state)."""
        return json.loads(self._strategy_params_json)

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def market(self) -> str:
        return self._market

    @property
    def freq(self) -> str:
        return self._freq

    @property
    def t_plus_1(self) -> bool:
        return self._t_plus_1

    @property
    def price_limit_pct(self) -> float:
        return self._price_limit_pct

    @property
    def lot_size(self) -> int:
        return self._lot_size

    @property
    def buy_commission_rate(self) -> float:
        return self._buy_commission_rate

    @property
    def sell_commission_rate(self) -> float:
        return self._sell_commission_rate

    @property
    def stamp_tax_rate(self) -> float:
        return self._stamp_tax_rate

    @property
    def slippage_rate(self) -> float:
        return self._slippage_rate

    @property
    def min_commission(self) -> float:
        return self._min_commission

    @property
    def optimizer(self) -> str:
        return self._optimizer

    @property
    def optimizer_params(self) -> dict:
        return json.loads(self._optimizer_params_json)

    @property
    def risk_control(self) -> bool:
        return self._risk_control

    @property
    def risk_params(self) -> dict:
        return json.loads(self._risk_params_json)

    @property
    def initial_cash(self) -> float:
        return self._initial_cash

    # -- Serialization ----------------------------------------------------

    def _canonical_dict(self) -> dict:
        """All fields explicitly enumerated for hash stability."""
        return {
            "strategy_name": self._strategy_name,
            "strategy_params": self._strategy_params_json,
            "symbols": list(self._symbols),
            "market": self._market,
            "freq": self._freq,
            "t_plus_1": self._t_plus_1,
            "price_limit_pct": self._price_limit_pct,
            "lot_size": self._lot_size,
            "buy_commission_rate": self._buy_commission_rate,
            "sell_commission_rate": self._sell_commission_rate,
            "stamp_tax_rate": self._stamp_tax_rate,
            "slippage_rate": self._slippage_rate,
            "min_commission": self._min_commission,
            "optimizer": self._optimizer,
            "optimizer_params": self._optimizer_params_json,
            "risk_control": self._risk_control,
            "risk_params": self._risk_params_json,
            "initial_cash": self._initial_cash,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        d = self._canonical_dict()
        d["spec_id"] = self._spec_id
        return json.dumps(d, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> DeploymentSpec:
        """Deserialize from JSON string produced by to_json()."""
        d = json.loads(json_str)
        # strategy_params and optimizer_params/risk_params are stored as
        # JSON strings in the canonical dict; parse them back to dicts.
        strategy_params = json.loads(d["strategy_params"]) if isinstance(d["strategy_params"], str) else d["strategy_params"]
        optimizer_params = json.loads(d["optimizer_params"]) if isinstance(d["optimizer_params"], str) else d.get("optimizer_params", {})
        risk_params = json.loads(d["risk_params"]) if isinstance(d["risk_params"], str) else d.get("risk_params", {})
        return cls(
            strategy_name=d["strategy_name"],
            strategy_params=strategy_params,
            symbols=tuple(d["symbols"]),
            market=d["market"],
            freq=d["freq"],
            t_plus_1=d.get("t_plus_1", True),
            price_limit_pct=d.get("price_limit_pct", 0.1),
            lot_size=d.get("lot_size", 100),
            buy_commission_rate=d.get("buy_commission_rate", 0.00008),
            sell_commission_rate=d.get("sell_commission_rate", 0.00008),
            stamp_tax_rate=d.get("stamp_tax_rate", 0.0005),
            slippage_rate=d.get("slippage_rate", 0.001),
            min_commission=d.get("min_commission", 0.0),
            optimizer=d.get("optimizer", ""),
            optimizer_params=optimizer_params,
            risk_control=d.get("risk_control", False),
            risk_params=risk_params,
            initial_cash=d.get("initial_cash", 1_000_000.0),
        )

    def __repr__(self) -> str:
        return (
            f"DeploymentSpec(spec_id={self._spec_id!r}, "
            f"strategy={self._strategy_name!r}, market={self._market!r})"
        )


# ---------------------------------------------------------------------------
# DeploymentRecord — mutable runtime lifecycle
# ---------------------------------------------------------------------------

@dataclass
class DeploymentRecord:
    """Mutable deployment lifecycle record.

    State machine: pending -> approved -> running -> stopped | paused | error
    """
    spec_id: str
    name: str
    deployment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "pending"
    stop_reason: str = ""
    source_run_id: str | None = None
    code_commit: str | None = None
    gate_verdict: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: datetime | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
