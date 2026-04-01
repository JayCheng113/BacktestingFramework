"""V2.11.1 F3: AlphaCombiner — multi-factor composite score.

Combines N sub-factors into a single cross-sectional score via z-score + weighted sum.

Methods:
  - equal: unweighted mean of z-scores
  - ic: weight by pre-computed IC (stronger factors get more weight)
  - icir: weight by pre-computed ICIR (stronger AND more stable factors get more weight)

Weights are pre-computed by the API layer using training data BEFORE the backtest start,
avoiding any lookahead bias. They are passed as fixed dict to the constructor.

Not auto-registered (requires sub-factors in constructor). Handled specially in API routes.
"""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from ez.portfolio.cross_factor import CrossSectionalFactor

logger = logging.getLogger(__name__)


class AlphaCombiner(CrossSectionalFactor):
    """Multi-factor composite. Z-score normalize + weighted sum of sub-factors."""

    def __init__(
        self,
        factors: list[CrossSectionalFactor],
        weights: dict[str, float] | None = None,
        orthogonalize: bool = False,
    ):
        """
        Args:
            factors: Sub-factor instances (each must implement compute_raw).
            weights: {factor.name: weight}. None → equal weight.
                     Pre-computed by API from training-period IC/ICIR.
            orthogonalize: If True, apply Gram-Schmidt to remove inter-factor correlation
                          before weighted combination. Factor order determines priority.
        """
        self._factors = factors
        self._weights = weights
        self._orthogonalize = orthogonalize

    @property
    def name(self) -> str:
        names = "+".join(f.name for f in self._factors)
        return f"alpha({names})"

    @property
    def description(self) -> str:
        return "多因子合成"

    @property
    def warmup_period(self) -> int:
        return max((f.warmup_period for f in self._factors), default=0)

    def compute_raw(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        """Composite raw score: z-score each sub-factor, then weighted sum.

        Handles:
        - C2: std < 1e-10 → z-score = 0 (avoids division by zero)
        - C3: missing factors per stock → re-normalize weights over available factors
        - E2: each sub-factor compute_raw() called exactly once
        """
        # Collect raw scores from each sub-factor (E2: one call each)
        raw_scores: dict[str, pd.Series] = {}
        for f in self._factors:
            # Detect legacy factors that don't override compute_raw() — they return ranks
            has_own_raw = type(f).compute_raw is not CrossSectionalFactor.compute_raw
            scores = f.compute_raw(universe_data, date)
            if len(scores) > 0:
                if not has_own_raw and len(scores) > 1:
                    # Legacy factor: compute_raw returns ranks [0,1].
                    # Log warning but still use — ranks can be z-scored for relative ordering.
                    logger.warning(
                        "Factor '%s' has no compute_raw() override — using ranked values in combination. "
                        "Results may be suboptimal. Implement compute_raw() for better accuracy.", f.name)
                raw_scores[f.name] = scores

        if not raw_scores:
            return pd.Series(dtype=float)

        # Z-score normalize each factor
        z_df = pd.DataFrame(raw_scores)
        for col in z_df.columns:
            s = z_df[col]
            std = s.std()
            if std > 1e-10:  # C2: division-by-zero guard
                z_df[col] = (s - s.mean()) / std
            else:
                z_df[col] = 0.0

        # V2.12.1: Gram-Schmidt orthogonalization (after z-score, before combination)
        if self._orthogonalize and len(z_df.columns) > 1:
            from ez.portfolio.orthogonalization import gram_schmidt_orthogonalize
            orth = gram_schmidt_orthogonalize(z_df.values)
            z_df = pd.DataFrame(orth, index=z_df.index, columns=z_df.columns)

        # Build weight vector (validate coverage)
        if self._weights:
            w = pd.Series({col: self._weights.get(col, 0.0) for col in z_df.columns})
            missing_w = [col for col in z_df.columns if col not in self._weights]
            if missing_w:
                logger.warning(
                    "AlphaCombiner weights missing for factors: %s — using weight=0", missing_w)
        else:
            w = pd.Series(1.0, index=z_df.columns)

        # C3: weighted sum with per-stock re-normalization for missing values
        # numerator = sum of (z * w) for available factors
        # denominator = sum of w for available factors
        weighted = z_df.mul(w)
        available_w = z_df.notna().astype(float).mul(w)

        numerator = weighted.sum(axis=1, skipna=True)
        denominator = available_w.sum(axis=1)
        denominator = denominator.replace(0, np.nan)

        combined = numerator / denominator
        return combined.dropna()

    def compute(self, universe_data: dict[str, pd.DataFrame], date: datetime) -> pd.Series:
        raw = self.compute_raw(universe_data, date)
        return raw.rank(pct=True) if len(raw) > 0 else raw


# Prevent auto-registration (AlphaCombiner can't be instantiated without args)
CrossSectionalFactor._registry.pop("AlphaCombiner", None)
