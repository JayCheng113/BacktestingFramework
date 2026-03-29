"""V2.7: False Discovery Rate correction for batch parameter search.

When testing N parameter combinations, the probability of finding at least
one "significant" result by chance increases. FDR correction adjusts for this.

Methods:
  - Bonferroni: conservative, controls family-wise error rate (FWER)
  - Benjamini-Hochberg (BH): less conservative, controls false discovery rate (FDR)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class FDRResult:
    """Result of FDR correction for a single test."""

    spec_id: str
    raw_p_value: float
    adjusted_p_value: float
    is_significant: bool  # adjusted_p < alpha


def bonferroni(
    p_values: list[tuple[str, float]],
    alpha: float = 0.05,
) -> list[FDRResult]:
    """Bonferroni correction: multiply each p-value by N.

    Args:
        p_values: list of (spec_id, raw_p_value) tuples.
        alpha: significance threshold (default 0.05).
    """
    n = len(p_values)
    if n == 0:
        return []
    results = []
    for spec_id, p in p_values:
        if math.isnan(p) or math.isinf(p):
            results.append(FDRResult(spec_id=spec_id, raw_p_value=p, adjusted_p_value=float("nan"), is_significant=False))
            continue
        adj = min(p * n, 1.0)
        results.append(FDRResult(spec_id=spec_id, raw_p_value=p, adjusted_p_value=adj, is_significant=adj < alpha))
    return results


def benjamini_hochberg(
    p_values: list[tuple[str, float]],
    alpha: float = 0.05,
) -> list[FDRResult]:
    """Benjamini-Hochberg procedure: controls FDR.

    Steps:
    1. Sort p-values ascending
    2. For rank i (1-based): adjusted_p = p * N / i
    3. Enforce monotonicity (step-up): adjusted_p[i] = min(adjusted_p[i], adjusted_p[i+1])
    4. Reject if adjusted_p < alpha

    Args:
        p_values: list of (spec_id, raw_p_value) tuples.
        alpha: significance threshold (default 0.05).
    """
    n = len(p_values)
    if n == 0:
        return []

    # Separate NaN p-values
    valid = [(sid, p) for sid, p in p_values if not (math.isnan(p) or math.isinf(p))]
    nan_ids = {sid for sid, p in p_values if math.isnan(p) or math.isinf(p)}

    if not valid:
        return [FDRResult(spec_id=sid, raw_p_value=p, adjusted_p_value=float("nan"), is_significant=False) for sid, p in p_values]

    # Sort by p-value
    sorted_valid = sorted(valid, key=lambda x: x[1])
    m = len(sorted_valid)

    # Compute adjusted p-values
    adjusted: list[float] = []
    for rank, (_, p) in enumerate(sorted_valid, 1):
        adj = p * m / rank
        adjusted.append(min(adj, 1.0))

    # Enforce monotonicity (step-up from the end)
    for i in range(m - 2, -1, -1):
        adjusted[i] = min(adjusted[i], adjusted[i + 1])

    # Build result map
    result_map: dict[str, FDRResult] = {}
    for i, (spec_id, raw_p) in enumerate(sorted_valid):
        result_map[spec_id] = FDRResult(
            spec_id=spec_id,
            raw_p_value=raw_p,
            adjusted_p_value=adjusted[i],
            is_significant=adjusted[i] < alpha,
        )

    # Preserve original order, add NaN entries
    results = []
    for spec_id, p in p_values:
        if spec_id in nan_ids:
            results.append(FDRResult(spec_id=spec_id, raw_p_value=p, adjusted_p_value=float("nan"), is_significant=False))
        else:
            results.append(result_map[spec_id])
    return results


def apply_fdr(
    ranked_results: list[dict],
    method: str = "bh",
    alpha: float = 0.05,
) -> list[dict]:
    """Apply FDR correction to batch search results.

    Modifies results in-place, adding:
      - fdr_adjusted_p: adjusted p-value
      - fdr_significant: whether still significant after correction
      - fdr_method: correction method used

    Args:
        ranked_results: list of dicts with 'spec_id' and 'p_value' keys.
        method: 'bh' (Benjamini-Hochberg) or 'bonferroni'.
        alpha: significance threshold.
    """
    p_values = [
        (r.get("spec_id", ""), r.get("p_value", float("nan")))
        for r in ranked_results
    ]

    if method == "bonferroni":
        fdr_results = bonferroni(p_values, alpha)
    else:
        fdr_results = benjamini_hochberg(p_values, alpha)

    for r, fdr in zip(ranked_results, fdr_results):
        r["fdr_adjusted_p"] = fdr.adjusted_p_value
        r["fdr_significant"] = fdr.is_significant
        r["fdr_method"] = method

    return ranked_results
