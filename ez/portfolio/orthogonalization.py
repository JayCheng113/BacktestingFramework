"""V2.12.1 S2: Gram-Schmidt factor orthogonalization.

Removes linear dependencies between factors before combination.
Column order matters: first column unchanged, subsequent columns
progressively de-correlated from preceding ones.
"""
from __future__ import annotations

import numpy as np


def gram_schmidt_orthogonalize(factor_matrix: np.ndarray) -> np.ndarray:
    """Orthogonalize N×K factor matrix via sequential residualization.

    Column 0 unchanged. Column j = residual after regressing on columns 0..j-1.
    NaN handling: excluded from regression, preserved in output.

    Args:
        factor_matrix: N stocks × K factors.
    Returns:
        N×K orthogonalized matrix (columns pairwise uncorrelated on non-NaN rows).
    """
    if factor_matrix.ndim != 2:
        return factor_matrix.copy()
    _N, K = factor_matrix.shape
    if K <= 1:
        return factor_matrix.copy()

    result = factor_matrix.copy().astype(float)
    for j in range(1, K):
        col = result[:, j].copy()
        valid_j = ~np.isnan(col)
        for p in range(j):
            prev = result[:, p]
            both_valid = valid_j & ~np.isnan(prev)
            if both_valid.sum() < 2:
                continue
            x = prev[both_valid]
            y = col[both_valid]
            x_mean = x.mean()
            var_x = np.dot(x - x_mean, x - x_mean)
            if var_x < 1e-20:
                continue
            beta = np.dot(x - x_mean, y - y.mean()) / var_x
            col[both_valid] -= beta * prev[both_valid]
        result[:, j] = col
    return result
