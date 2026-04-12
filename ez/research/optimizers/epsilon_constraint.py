"""EpsilonConstraint objective: optimize one metric subject to a constraint
on another, with safe-eval support for ``"0.9*baseline_ret"``-style strings.

The phase O / phase Q research workflows depend heavily on this pattern:

  - Min |MDD|  s.t. Ret >= 0.9 * baseline_ret  (preserve 90% of return)
  - Min |MDD|  s.t. Ret >= 0.8 * baseline_ret  (preserve 80% of return)
  - Max Ret    s.t. |MDD| <= 0.8 * baseline_mdd_abs (drop drawdown to 80%)
  - Max Ret    s.t. |MDD| <= 0.6 * baseline_mdd_abs (drop drawdown to 60%)

V2.20.1 commit 3 supports a string DSL for the constraint value
(``"0.9*baseline_ret"``) backed by a custom AST visitor that allows ONLY:
  - Float / int constants
  - References to ``baseline_<key>`` where key is a known metric
  - Binary operators ``*`` and ``/`` (and parentheses for grouping)

Anything else (function calls, attribute access, subscripts, names that
don't start with ``baseline_``, unary operators) → ``ValueError`` at
construction time. We construct the AST once at __init__ and walk it on
every evaluate() call to interpolate baseline values.
"""
from __future__ import annotations
import ast
import math
from typing import Optional, Union, Callable

import pandas as pd

from .base import Objective
from .._metrics import compute_basic_metrics


# Allowed metrics that can be referenced as `baseline_<metric>`
_ALLOWED_BASELINE_KEYS = frozenset({
    "ret", "sharpe", "sortino", "vol", "dd", "mdd_abs", "calmar",
})

# Built-in objective expressions (target → callable taking a metrics dict
# and returning the scalar to minimize)
_OBJECTIVE_FNS: dict[str, Callable[[dict], float]] = {
    "min_mdd": lambda m: m["mdd_abs"],
    "max_ret": lambda m: -m["ret"],
    "min_vol": lambda m: m["vol"],
    "min_dd": lambda m: m["mdd_abs"],  # alias of min_mdd
}

# Built-in constraint metrics (the LHS of the constraint comparison)
_CONSTRAINT_METRICS = frozenset({
    "ret", "sharpe", "sortino", "vol", "mdd_abs", "calmar",
})

# Allowed comparison ops
_OPS = frozenset({">=", "<=", ">", "<", "=="})


def _safe_eval(expr: Union[str, float, int, Callable], baseline: Optional[dict[str, float]]) -> float:
    """Safely evaluate a constraint value expression.

    Accepts:
      - int / float (returned as float)
      - callable (passed baseline dict, returns float) — V2.20.1 codex round-6 P2
      - string with literal numbers, * /, parentheses, and references
        to ``baseline_<metric>``

    Raises ``ValueError`` for any unsafe construct (function calls,
    attribute access, names other than baseline_*, unary minus, etc).
    """
    if isinstance(expr, (int, float)):
        return float(expr)
    if callable(expr) and not isinstance(expr, str):
        # Codex round-6 P2: callable constraint value support.
        # Design draft said "support both string DSL and callable".
        if baseline is None:
            raise ValueError("Callable constraint value requires baseline_metrics")
        result = expr(baseline)
        if not isinstance(result, (int, float)):
            raise TypeError(
                f"Callable constraint value must return a number, got {type(result).__name__}"
            )
        return float(result)
    if not isinstance(expr, str):
        raise TypeError(f"Constraint value must be number, str, or callable, got {type(expr).__name__}")

    expr = expr.strip()
    if not expr:
        raise ValueError("Empty constraint expression")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid constraint expression syntax: {expr!r}: {e}")

    return _walk(tree.body, baseline, expr)


def _walk(node: ast.AST, baseline: Optional[dict[str, float]], expr: str) -> float:
    """Recursive AST walker. Only allowed node types pass through."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ValueError(f"Constraint expression {expr!r}: only numeric constants allowed")

    if isinstance(node, ast.Name):
        name = node.id
        if not name.startswith("baseline_"):
            raise ValueError(
                f"Constraint expression {expr!r}: name {name!r} not allowed. "
                f"Only baseline_<metric> references are permitted."
            )
        key = name[len("baseline_"):]
        if key not in _ALLOWED_BASELINE_KEYS:
            raise ValueError(
                f"Constraint expression {expr!r}: unknown baseline metric "
                f"{key!r}. Allowed: {sorted(_ALLOWED_BASELINE_KEYS)}"
            )
        if baseline is None:
            raise ValueError(
                f"Constraint references baseline_{key} but no baseline_metrics provided"
            )
        if key not in baseline:
            raise ValueError(
                f"baseline_metrics missing key {key!r}. Available: {sorted(baseline.keys())}"
            )
        return float(baseline[key])

    if isinstance(node, ast.BinOp):
        left = _walk(node.left, baseline, expr)
        right = _walk(node.right, baseline, expr)
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError(f"Constraint expression {expr!r}: division by zero")
            return left / right
        raise ValueError(
            f"Constraint expression {expr!r}: only * and / are allowed, "
            f"got {type(node.op).__name__}"
        )

    if isinstance(node, ast.UnaryOp):
        # Unary minus is the only one we tolerate (e.g. "-0.5 * baseline_ret")
        if isinstance(node.op, ast.USub):
            return -_walk(node.operand, baseline, expr)
        raise ValueError(
            f"Constraint expression {expr!r}: only unary minus allowed"
        )

    raise ValueError(
        f"Constraint expression {expr!r}: unsupported node type "
        f"{type(node).__name__}"
    )


class EpsilonConstraint(Objective):
    """Optimize one metric subject to a constraint on another.

    Parameters
    ----------
    objective : str
        One of {"min_mdd", "max_ret", "min_vol", "min_dd"}. The metric
        the optimizer should drive toward its minimum (after sign flip
        for "max_*" cases).
    constraint_metric : str
        One of the keys in compute_basic_metrics output minus "dd"
        (use "mdd_abs" for absolute drawdown). The metric to constrain.
    constraint_op : str
        One of {">=", "<=", ">", "<", "=="}. The comparison sense.
    constraint_value : float | int | str
        The constraint threshold. Strings are evaluated via _safe_eval
        with baseline metrics interpolation.

    Examples
    --------
    >>> # Min |MDD| subject to Ret >= 0.9 * baseline_ret
    >>> obj = EpsilonConstraint("min_mdd", "ret", ">=", "0.9*baseline_ret")
    >>> # Max Ret subject to |MDD| <= 8%
    >>> obj = EpsilonConstraint("max_ret", "mdd_abs", "<=", 0.08)
    >>> # Max Ret subject to |MDD| <= 0.6 * baseline_mdd_abs
    >>> obj = EpsilonConstraint("max_ret", "mdd_abs", "<=", "0.6*baseline_mdd_abs")
    """

    def __init__(
        self,
        objective: str,
        constraint_metric: str,
        constraint_op: str,
        constraint_value: Union[str, float, int],
    ):
        if objective not in _OBJECTIVE_FNS:
            raise ValueError(
                f"Unknown objective: {objective!r}. "
                f"Allowed: {sorted(_OBJECTIVE_FNS.keys())}"
            )
        if constraint_metric not in _CONSTRAINT_METRICS:
            raise ValueError(
                f"Unknown constraint_metric: {constraint_metric!r}. "
                f"Allowed: {sorted(_CONSTRAINT_METRICS)}"
            )
        if constraint_op not in _OPS:
            raise ValueError(
                f"Unknown constraint_op: {constraint_op!r}. "
                f"Allowed: {sorted(_OPS)}"
            )

        self.objective = objective
        self.constraint_metric = constraint_metric
        self.constraint_op = constraint_op
        self.constraint_value = constraint_value

        # Validate the value expression syntax at construction time
        # using a stub baseline. The real evaluation happens per-call
        # because baseline depends on the IS window.
        if isinstance(constraint_value, str):
            stub = {k: 0.0 for k in _ALLOWED_BASELINE_KEYS}
            try:
                _safe_eval(constraint_value, stub)
            except ValueError as e:
                raise ValueError(
                    f"EpsilonConstraint constraint_value {constraint_value!r} "
                    f"failed validation: {e}"
                ) from e
        elif callable(constraint_value):
            # Codex round-6 P2: validate callable at construction time
            # by calling it with a stub baseline. If it crashes, fail fast.
            stub = {k: 0.0 for k in _ALLOWED_BASELINE_KEYS}
            try:
                result = constraint_value(stub)
                if not isinstance(result, (int, float)):
                    raise TypeError(
                        f"Callable must return a number, got {type(result).__name__}"
                    )
            except (TypeError, KeyError, ValueError) as e:
                raise ValueError(
                    f"EpsilonConstraint callable constraint_value "
                    f"failed validation: {e}"
                ) from e

        # Friendly name
        self.name = (
            f"{objective} | {constraint_metric}{constraint_op}{constraint_value}"
        )

    def evaluate(
        self,
        port_returns: pd.Series,
        baseline_metrics: Optional[dict[str, float]] = None,
    ) -> float:
        m = compute_basic_metrics(port_returns)
        if m is None:
            return math.inf

        # Resolve constraint threshold
        try:
            threshold = _safe_eval(self.constraint_value, baseline_metrics)
        except ValueError:
            # Baseline reference missing or unresolvable — treat as infeasible
            return math.inf

        actual = m.get(self.constraint_metric)
        if actual is None:
            return math.inf

        if not self._compare(float(actual), self.constraint_op, threshold):
            return math.inf  # constraint violated

        return _OBJECTIVE_FNS[self.objective](m)

    @staticmethod
    def _compare(actual: float, op: str, threshold: float) -> bool:
        if op == ">=":
            return actual >= threshold
        if op == "<=":
            return actual <= threshold
        if op == ">":
            return actual > threshold
        if op == "<":
            return actual < threshold
        if op == "==":
            return abs(actual - threshold) < 1e-12
        return False
