"""Rolling correlation of two stocks' daily returns, and a correlation matrix of stocks and country indices
(DESIGN.md 14, "Rolling correlation" and "Correlation matrix").

Pure module (no Streamlit), so it is unit-testable like `sectors.py`. Returns are daily % changes of the split-
and dividend-adjusted close (total return), taken only on days both stocks have a close, so a trading halt in one
stock never pairs its multi-day move with a single day of the other. A window of N trading days means N such
paired returns; a value is shown only once the window is full.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOWS = {"1M": 21, "3M": 63, "6M": 126, "12M": 252}  # label -> trading days in the rolling window
DEFAULT_WINDOW = "3M"


# Correlation matrix: calendar timeframe back from the latest close, and the fewest paired returns for a value.
MATRIX_TIMEFRAMES = {
    "1M": pd.DateOffset(months=1), "3M": pd.DateOffset(months=3), "6M": pd.DateOffset(months=6),
    "12M": pd.DateOffset(months=12), "3Y": pd.DateOffset(years=3), "5Y": pd.DateOffset(years=5),
}
DEFAULT_MATRIX_TIMEFRAME = "12M"
MIN_PAIRED = 15


def paired_returns(a: pd.Series, b: pd.Series) -> pd.DataFrame:
    """Daily returns of two price series on the dates both have a close, columns `a` and `b`."""
    prices = pd.concat({"a": a, "b": b}, axis=1).dropna()
    return prices.pct_change().dropna()


def rolling_correlation(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """Pearson correlation of the two stocks' daily returns over the last `window` paired trading days, on every
    date the window is full. Empty when the shared history is shorter than the window."""
    r = paired_returns(a, b)
    return r["a"].rolling(window, min_periods=window).corr(r["b"]).dropna().rename("correlation")


def matrix(prices: pd.DataFrame, timeframe: str, min_obs: int = MIN_PAIRED) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pairwise correlation of daily returns for every pair of columns over a timeframe: (correlation, paired
    returns counted). Each pair uses only the dates both have a close, as `rolling_correlation`, and the returns
    dated after (latest date in `prices` - timeframe). A pair with fewer than `min_obs` returns is NaN."""
    cols = list(prices.columns)
    corr = pd.DataFrame(np.nan, index=cols, columns=cols)
    counts = pd.DataFrame(0, index=cols, columns=cols)
    filled = prices.dropna(how="all")
    if filled.empty:
        return corr, counts
    start = filled.index[-1] - MATRIX_TIMEFRAMES[timeframe]
    for i, a in enumerate(cols):
        for b in cols[i:]:
            r = paired_returns(prices[a], prices[b])
            r = r.loc[r.index > start]
            counts.loc[a, b] = counts.loc[b, a] = len(r)
            if len(r) >= min_obs:
                corr.loc[a, b] = corr.loc[b, a] = 1.0 if a == b else r["a"].corr(r["b"])
    return corr, counts
