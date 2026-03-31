"""V2.11.1 F2: Industry neutralization — remove industry bias from factor scores.

Usage:
    raw_scores = factor.compute_raw(universe_data, date)
    neutralized, warnings = neutralize_by_industry(raw_scores, industry_map)
    ranked = neutralized.rank(pct=True)
"""
from __future__ import annotations

import pandas as pd


def neutralize_by_industry(
    raw_scores: pd.Series,
    industry_map: dict[str, str],
    min_coverage: float = 0.5,
) -> tuple[pd.Series, list[str]]:
    """Neutralize factor scores by subtracting industry mean.

    Args:
        raw_scores: Raw factor values from compute_raw() (NOT ranked).
        industry_map: {symbol: industry_name} mapping.
        min_coverage: Minimum fraction of stocks with industry labels.
                      Below this threshold, neutralization is skipped.

    Returns:
        (neutralized_scores, warnings):
        - neutralized_scores: pd.Series with industry bias removed.
          Stocks in single-stock industries are dropped (no reference group).
          Stocks without industry labels keep their original values.
        - warnings: List of warning messages (empty if all OK).
    """
    warnings: list[str] = []

    if len(raw_scores) == 0:
        return raw_scores, warnings

    # 1. Check coverage
    labeled = sum(1 for s in raw_scores.index if industry_map.get(s))
    coverage = labeled / len(raw_scores)
    if coverage < min_coverage:
        warnings.append(f"行业覆盖率仅 {coverage:.0%}，跳过中性化（需 ≥ {min_coverage:.0%}）")
        return raw_scores, warnings

    # 2. Build industry series
    industries = pd.Series(
        {s: industry_map.get(s, '') for s in raw_scores.index},
        dtype='object',
    )

    # 3. Compute grouped stats
    df = pd.DataFrame({'score': raw_scores, 'industry': industries})
    df.loc[df['industry'] == '', 'industry'] = pd.NA

    industry_mean = df.groupby('industry')['score'].transform('mean')
    industry_count = df.groupby('industry')['score'].transform('count')

    # 4. Neutralize: score - industry_mean
    neutralized = df['score'] - industry_mean

    # 5. Single-stock industries → drop (no meaningful reference)
    neutralized[industry_count <= 1] = pd.NA

    # 6. Stocks without industry → keep original value
    no_industry = df['industry'].isna()
    neutralized[no_industry] = df.loc[no_industry, 'score']
    if no_industry.sum() > 0:
        warnings.append(f"{no_industry.sum()} 只标的无行业标签，保留原始值")

    return neutralized.dropna(), warnings
