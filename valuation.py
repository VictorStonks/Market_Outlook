"""Forward (NTM) P/E from scratch: daily price over a daily time-weighted next-twelve-months EPS.

Pure module (no Streamlit), so it is unit-testable like `sectors.py`. Yahoo keeps no history of what analysts
expected on past dates, so the denominator is built from reported quarterly EPS plus today's estimates for the
quarters not yet reported (DESIGN.md 14, "Forward P/E"). The past is therefore a perfect-foresight NTM P/E.

Method: put each quarter's EPS on its fiscal period end, accumulate it, and read NTM EPS on any date d as the
growth of that cumulative line over the next 365 days, C(d + 365) - C(d). EPS is assumed to accrue evenly within
a quarter, so each quarter counts in proportion to the share of it that falls inside the window. That makes the
denominator roll forward daily instead of stepping when a quarter is reported.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

NTM_DAYS = 365
QUARTER_DAYS = 91                     # length assumed for the first quarter, whose start date is not in the data
_GAP_DAYS = (60, 125)                 # spacing between consecutive quarter ends outside this = a missing quarter
_EXTRAPOLATED_QUARTERS = 6            # estimates run to +6 quarters so today's 365-day window is fully covered
_GROWTH_BOUNDS = (0.5, 2.0)           # cap on the FY2/FY1 growth used to roll estimates forward
DEFAULT_LAG_DAYS = 35                 # typical days from fiscal quarter end to report date


def _growth(estimates: Mapping[str, float]) -> float:
    """Next fiscal year's estimate over this one's; 1.0 (flat) when either is missing or not positive."""
    fy1, fy2 = estimates.get("0y"), estimates.get("+1y")
    if fy1 is None or fy2 is None or not (fy1 > 0 and fy2 > 0):
        return 1.0
    return float(np.clip(fy2 / fy1, *_GROWTH_BOUNDS))


def build_quarters(reported: pd.Series, estimates: Mapping[str, float], lag_days: int = DEFAULT_LAG_DAYS
                   ) -> pd.DataFrame:
    """Quarterly EPS on fiscal period ends: columns `eps` and `estimated`. Empty when there is too little history.

    `reported`: actual EPS indexed by report date. Period end = report date - `lag_days`.
    `estimates`: Yahoo's current consensus keyed "0q", "+1q", "0y", "+1y". "0q" and "+1q" are the next two
    unreported quarters. Later quarters repeat the EPS of the same quarter a year earlier, times the FY2/FY1
    growth, which keeps seasonality without inventing a quarterly consensus that Yahoo does not publish.
    """
    reported = reported.dropna().sort_index()
    if len(reported) < 5:
        return pd.DataFrame(columns=["eps", "estimated"])
    ends = pd.DatetimeIndex(reported.index.normalize() - pd.Timedelta(days=lag_days))
    gaps = np.diff(ends.to_numpy()).astype("timedelta64[D]").astype(int)
    bad = np.flatnonzero((gaps < _GAP_DAYS[0]) | (gaps > _GAP_DAYS[1]))
    start = 0 if bad.size == 0 else int(bad[-1]) + 1     # keep only the unbroken run that ends today
    ends, eps = ends[start:], reported.to_numpy(dtype=float)[start:]
    if len(eps) < 5:
        return pd.DataFrame(columns=["eps", "estimated"])
    last_end, values, flags = ends[-1], list(eps), [False] * len(eps)
    q1, q2 = estimates.get("0q"), estimates.get("+1q")
    if q1 is not None and q2 is not None:
        g = _growth(estimates)
        new_ends = [last_end + pd.DateOffset(months=3 * j) for j in range(1, _EXTRAPOLATED_QUARTERS + 1)]
        for j in range(_EXTRAPOLATED_QUARTERS):
            values.append(q1 if j == 0 else q2 if j == 1 else values[-4] * g)
            flags.append(True)
        ends = ends.append(pd.DatetimeIndex(new_ends))
    return pd.DataFrame({"eps": values, "estimated": flags}, index=ends.rename("period_end"))


def _days(index) -> np.ndarray:
    return pd.DatetimeIndex(index).normalize().to_numpy().astype("datetime64[D]").astype(float)


def ntm_eps(quarters: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.Series:
    """Time-weighted next-12-month EPS on each date; NaN where the 365-day window is not fully covered."""
    if quarters.empty:
        return pd.Series(np.nan, index=dates, dtype=float)
    x = np.concatenate([[_days(quarters.index[:1])[0] - QUARTER_DAYS], _days(quarters.index)])
    cum = np.concatenate([[0.0], quarters["eps"].to_numpy(dtype=float).cumsum()])
    d = _days(dates)
    now, ahead = (np.interp(v, x, cum, left=np.nan, right=np.nan) for v in (d, d + NTM_DAYS))
    return pd.Series(ahead - now, index=dates, dtype=float)


def ntm_pe(prices: pd.Series, eps: pd.Series) -> pd.Series:
    """Daily close over NTM EPS. Dates without a positive NTM EPS are dropped: a negative or zero denominator
    makes the multiple meaningless."""
    prices = prices.dropna()
    e = eps.reindex(prices.index)
    return (prices / e.where(e > 0)).dropna().rename("NTM P/E")


def estimates_from(quarters: pd.DataFrame) -> pd.Timestamp | None:
    """First date whose 365-day window includes an estimated quarter (None when nothing is estimated)."""
    if quarters.empty or not quarters["estimated"].any():
        return None
    return quarters.index[~quarters["estimated"].to_numpy(dtype=bool)][-1] - pd.Timedelta(days=NTM_DAYS)
