"""PairedBlockBootstrapStep: statistical significance of strategy differences.

Given two weight configurations (e.g., optimized vs baseline), this step
tests whether the Sharpe difference is statistically significant using
paired block bootstrap.

**Why block bootstrap?**
Daily returns are autocorrelated.  I.I.D. resampling destroys this serial
dependence and produces anticonservative CIs (too narrow).  Block bootstrap
preserves within-block autocorrelation.

**Why paired?**
Both portfolios are exposed to the same market conditions on each day.
Resampling the *same* block indices for both series preserves the
cross-sectional pairing, making the test more powerful.

Reads:
  - artifacts['returns']: pd.DataFrame[date × label]

Writes:
  - artifacts['bootstrap_results']: dict with CI, p-value, bootstrap distribution

V2.20.4: replaces validation/phase_q pattern.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from ..pipeline import ResearchStep
from ..context import PipelineContext
from .._metrics import compute_basic_metrics

logger = logging.getLogger(__name__)


def _sharpe_from_returns(returns: np.ndarray) -> float:
    """Compute annualized Sharpe from a 1-D returns array (ddof=1)."""
    if len(returns) < 2:
        return 0.0
    m = float(np.nanmean(returns))
    s = float(np.nanstd(returns, ddof=1))
    if s < 1e-12:
        return 0.0
    return m / s * np.sqrt(252)


def sample_block_indices(
    n: int,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Sample one block-bootstrap index array of length n.

    Public helper (no leading underscore) so `ez/api/routes/validation.py`
    and any future block-bootstrap code can share the same
    implementation. Guarantees preservation of local autocorrelation
    within each block while randomizing block positions.

    Parameters
    ----------
    n : int
        Desired length of the resampled series. **Precondition: n >= block_size.**
        Callers must validate this before invoking — violations raise
        ``ValueError`` from ``rng.integers(0, n - block_size + 1)``.
    block_size : int
        Length of each block. ``n_blocks = ceil(n / block_size)``
        blocks are drawn with replacement.
    rng : np.random.Generator
        Random number generator (typically ``np.random.default_rng(seed)``).

    Returns
    -------
    np.ndarray of shape (n,) — indices into the original series.
    """
    n_blocks = (n + block_size - 1) // block_size  # ceiling division
    block_starts = rng.integers(0, n - block_size + 1, size=n_blocks)
    idx = np.concatenate([
        np.arange(s, min(s + block_size, n)) for s in block_starts
    ])[:n]  # trim to original length
    return idx


def paired_block_bootstrap(
    returns_a: np.ndarray,
    returns_b: np.ndarray,
    n_bootstrap: int = 5000,
    block_size: int = 21,
    seed: int = 42,
    statistic: str = "sharpe_diff",
) -> dict[str, Any]:
    """Run paired block bootstrap on two return series.

    Parameters
    ----------
    returns_a, returns_b : np.ndarray
        Daily returns, same length, aligned by date.
    n_bootstrap : int
        Number of bootstrap replications.
    block_size : int
        Block size in trading days. Default 21 ≈ 1 month.
    seed : int
        Random seed for reproducibility.
    statistic : str
        What to compute: ``"sharpe_diff"`` (default).

    Returns
    -------
    dict with:
        observed : float — observed statistic on original data
        ci_lower, ci_upper : float — 95% percentile CI
        p_value : float — two-sided p-value (H0: statistic = 0)
        n_bootstrap : int
        block_size : int
        distribution : np.ndarray — bootstrap distribution (for diagnostics)
    """
    n = len(returns_a)
    if n != len(returns_b):
        raise ValueError(
            f"returns_a and returns_b must have same length, "
            f"got {n} and {len(returns_b)}"
        )
    if n < block_size:
        raise ValueError(
            f"Data length ({n}) must be >= block_size ({block_size})"
        )

    rng = np.random.default_rng(seed)

    # Observed statistic
    obs_a = _sharpe_from_returns(returns_a)
    obs_b = _sharpe_from_returns(returns_b)
    observed = obs_a - obs_b

    # Bootstrap — SAME block indices for both series preserves pairing
    boot_stats = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        idx = sample_block_indices(n, block_size, rng)
        boot_a = returns_a[idx]
        boot_b = returns_b[idx]
        boot_stats[i] = _sharpe_from_returns(boot_a) - _sharpe_from_returns(boot_b)

    # Percentile CI (95%)
    ci_lower = float(np.percentile(boot_stats, 2.5))
    ci_upper = float(np.percentile(boot_stats, 97.5))

    # Two-sided p-value under H0: sharpe_diff = 0.
    # Standard approach: center the bootstrap distribution at zero
    # (simulating H0), then ask how often the centered distribution
    # produces a value as extreme as the observed statistic.
    # The observed statistic is NOT centered — it comes from the real data.
    centered = boot_stats - np.mean(boot_stats)
    if abs(observed) < 1e-12:
        p_value = 1.0
    else:
        p_value = float(np.mean(np.abs(centered) >= abs(observed)))
        p_value = max(p_value, 1.0 / n_bootstrap)  # floor

    return {
        "observed": float(observed),
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "p_value": p_value,
        "n_bootstrap": n_bootstrap,
        "block_size": block_size,
        "distribution": boot_stats,
    }


class PairedBlockBootstrapStep(ResearchStep):
    """Test whether two portfolios have significantly different Sharpe ratios.

    Computes the paired block bootstrap CI and p-value for the Sharpe
    difference between a "treatment" portfolio (defined by
    ``treatment_weights``) and a "control" portfolio (defined by
    ``control_weights``).

    Typical use: compare optimized weights (from NestedOOSStep or
    WalkForwardStep) against a baseline allocation.
    """

    name = "paired_bootstrap"
    writes = ("bootstrap_results",)

    def __init__(
        self,
        treatment_weights: dict[str, float],
        control_weights: dict[str, float],
        treatment_label: str = "Treatment",
        control_label: str = "Control",
        n_bootstrap: int = 5000,
        block_size: int = 21,
        seed: int = 42,
        window: tuple[str, str] | None = None,
        store_distribution: bool = False,
    ):
        """
        Parameters
        ----------
        treatment_weights : dict[str, float]
            Weights for the "treatment" portfolio (e.g., optimized).
        control_weights : dict[str, float]
            Weights for the "control" portfolio (e.g., baseline P1).
        treatment_label, control_label : str
            Labels for the report.
        n_bootstrap : int
            Number of bootstrap replications. Default 5000.
        block_size : int
            Block size in trading days. Default 21 (~1 month).
        seed : int
            Random seed.
        window : (start, end), optional
            Date window to slice returns before bootstrapping.
            If None, uses the full returns DataFrame.
        store_distribution : bool
            If True, store the full bootstrap distribution array in
            ``bootstrap_results['distribution']`` (for histograms / diagnostics).
            Default False to save memory.
        """
        if not treatment_weights:
            raise ValueError("treatment_weights must not be empty")
        if not control_weights:
            raise ValueError("control_weights must not be empty")
        if n_bootstrap < 100:
            raise ValueError(f"n_bootstrap must be >= 100, got {n_bootstrap}")
        if block_size < 1:
            raise ValueError(f"block_size must be >= 1, got {block_size}")

        self.treatment_weights = dict(treatment_weights)
        self.control_weights = dict(control_weights)
        self.treatment_label = treatment_label
        self.control_label = control_label
        self.n_bootstrap = n_bootstrap
        self.block_size = block_size
        self.seed = seed
        self.window = window
        self.store_distribution = store_distribution

    def _weighted_returns(
        self, returns: pd.DataFrame, weights: dict[str, float]
    ) -> pd.Series:
        port = pd.Series(0.0, index=returns.index)
        for label, w in weights.items():
            if label in returns.columns:
                col = returns[label]
                nan_frac = col.isna().mean()
                if nan_frac > 0.5:
                    logger.warning(
                        "PairedBlockBootstrapStep: column '%s' has %.0f%% NaN "
                        "(fillna(0) may distort results)", label, nan_frac * 100,
                    )
                port = port + col.fillna(0.0) * w
        return port

    def _slice(self, returns: pd.DataFrame) -> pd.DataFrame:
        if self.window is None:
            return returns
        start = pd.Timestamp(self.window[0])
        end = pd.Timestamp(self.window[1])
        return returns.loc[(returns.index >= start) & (returns.index <= end)]

    def run(self, context: PipelineContext) -> PipelineContext:
        returns = context.require("returns")

        if not isinstance(returns, pd.DataFrame):
            raise TypeError(
                f"PairedBlockBootstrapStep: 'returns' must be pd.DataFrame, "
                f"got {type(returns).__name__}"
            )

        sliced = self._slice(returns).dropna(how="all")
        if len(sliced) < self.block_size * 2:
            raise RuntimeError(
                f"PairedBlockBootstrapStep: insufficient data ({len(sliced)} rows) "
                f"for block_size={self.block_size}. Need at least {self.block_size * 2}."
            )

        # Compute portfolio returns
        treatment_rets = self._weighted_returns(sliced, self.treatment_weights)
        control_rets = self._weighted_returns(sliced, self.control_weights)

        # Align and drop NaN
        combined = pd.DataFrame({
            "treatment": treatment_rets,
            "control": control_rets,
        }).dropna()

        if len(combined) < self.block_size * 2:
            raise RuntimeError(
                f"PairedBlockBootstrapStep: after NaN alignment, only "
                f"{len(combined)} rows — need at least {self.block_size * 2}."
            )

        # Clear stale artifact
        context.artifacts.pop("bootstrap_results", None)

        # Run bootstrap
        result = paired_block_bootstrap(
            returns_a=combined["treatment"].values,
            returns_b=combined["control"].values,
            n_bootstrap=self.n_bootstrap,
            block_size=self.block_size,
            seed=self.seed,
        )

        # Compute standalone metrics for each portfolio
        treatment_metrics = compute_basic_metrics(combined["treatment"]) or {}
        control_metrics = compute_basic_metrics(combined["control"]) or {}

        # Determine significance
        is_significant = result["p_value"] < 0.05
        ci_excludes_zero = (result["ci_lower"] > 0) or (result["ci_upper"] < 0)

        context.artifacts["bootstrap_results"] = {
            "treatment_label": self.treatment_label,
            "control_label": self.control_label,
            "treatment_weights": self.treatment_weights,
            "control_weights": self.control_weights,
            "treatment_metrics": treatment_metrics,
            "control_metrics": control_metrics,
            "sharpe_diff": result["observed"],
            "ci_lower": result["ci_lower"],
            "ci_upper": result["ci_upper"],
            "p_value": result["p_value"],
            "is_significant": is_significant,
            "ci_excludes_zero": ci_excludes_zero,
            "n_bootstrap": result["n_bootstrap"],
            "block_size": result["block_size"],
            "n_observations": len(combined),
            "window": (
                (str(sliced.index[0].date()), str(sliced.index[-1].date()))
                if self.window is None
                else self.window
            ),
        }
        if self.store_distribution:
            context.artifacts["bootstrap_results"]["distribution"] = (
                result["distribution"].tolist()  # JSON-safe list
            )
        return context
