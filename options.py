"""Listed options: open interest by strike, max pain and dealer gamma exposure (DESIGN.md 14, "Options open interest").

Pure module (no Streamlit), so it is unit-testable like `cape.py`. Input is one tidy chain frame with a row per
contract: expiry (Timestamp), type ("call" / "put"), strike, oi (contracts), iv (implied volatility, decimal).

- Max pain: the settlement price, among the expiry's strikes, at which the options outstanding would pay their holders
  the least in total: sum over calls of OI x max(P - K, 0) plus sum over puts of OI x max(K - P, 0), x contract size.
- Dealer gamma exposure (GEX), the SqueezeMetrics convention: open interest says nothing about who is long, so dealers
  are ASSUMED long every call (investors sell calls against stock) and short every put (investors buy puts as
  protection). Call gamma counts positive, put gamma negative. Per contract, in USD per 1% move of the stock:
  gamma x contract size x spot^2 x 1%. Gamma is Black-Scholes from Yahoo's implied volatility, no dividends.
- Gamma flip: the stock price at which total dealer GEX, recomputed at that price, changes sign.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

CONTRACT_SIZE = 100            # shares per US equity option contract
STRIKE_RANGE = 0.25            # strikes shown: within ±25% of the last price (or the price history, if wider)
IV_MIN, IV_MAX = 0.01, 5.0     # Yahoo IVs outside this are placeholders on illiquid strikes; no gamma is computed
MIN_YEARS = 1 / (365 * 24)     # floor on time to expiry (one hour), so an option expiring today has finite gamma
HISTORY = {"1M": 1, "3M": 3, "6M": 6}   # price-history pill -> months
DEFAULT_HISTORY = "3M"
ALL_EXPIRIES = "All expiries"


def standard_monthly(expiries: list[pd.Timestamp]) -> pd.Timestamp | None:
    """The nearest standard monthly expiry (third Friday, or the Thursday before when that Friday is a holiday);
    the first listed expiry when none is listed."""
    for e in sorted(expiries):
        if (e.weekday() == 4 and 15 <= e.day <= 21) or (e.weekday() == 3 and 14 <= e.day <= 20
                                                         and e + pd.Timedelta(days=1) not in expiries):
            return e
    return min(expiries) if expiries else None


def by_strike(chain: pd.DataFrame) -> pd.DataFrame:
    """Open interest per strike, summed across the chain's expiries: columns calls, puts (contracts)."""
    oi = chain.pivot_table(index="strike", columns="type", values="oi", aggfunc="sum", fill_value=0.0)
    return oi.reindex(columns=["call", "put"], fill_value=0.0).rename(columns={"call": "calls", "put": "puts"})


def holder_payout(oi: pd.DataFrame, prices: np.ndarray) -> np.ndarray:
    """Total value paid to option holders at expiry if the stock settles at each of `prices` (per-strike OI frame from
    `by_strike`), in the stock's currency."""
    k = oi.index.to_numpy(dtype=float)[None, :]
    p = np.asarray(prices, dtype=float)[:, None]
    calls = (np.maximum(p - k, 0) * oi["calls"].to_numpy(dtype=float)).sum(axis=1)
    puts = (np.maximum(k - p, 0) * oi["puts"].to_numpy(dtype=float)).sum(axis=1)
    return (calls + puts) * CONTRACT_SIZE


def max_pain(oi: pd.DataFrame) -> float | None:
    """Strike with the smallest total holder payout (ties: the lowest strike). None without open interest."""
    if oi.empty or float(oi.to_numpy().sum()) <= 0:
        return None
    strikes = oi.index.to_numpy(dtype=float)
    return float(strikes[int(np.argmin(holder_payout(oi, strikes)))])


def years_to_expiry(expiry: pd.Timestamp, now: pd.Timestamp) -> float:
    """Calendar years from `now` (tz-aware) to 16:00 New York time on the expiry date, floored at one hour."""
    close = pd.Timestamp(expiry.date()).tz_localize("America/New_York") + pd.Timedelta(hours=16)
    return max((close - now).total_seconds() / (365 * 86400), MIN_YEARS)


def bs_gamma(spot: np.ndarray | float, strike: np.ndarray, years: np.ndarray, iv: np.ndarray,
             rate: float) -> np.ndarray:
    """Black-Scholes gamma (same for calls and puts), no dividends. Broadcasts over spot and the contract arrays."""
    s = np.asarray(spot, dtype=float)
    vol_t = iv * np.sqrt(years)
    d1 = (np.log(s / strike) + (rate + 0.5 * iv ** 2) * years) / vol_t
    return np.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi) / (s * vol_t)


@dataclass(frozen=True)
class Gex:
    by_strike: pd.DataFrame        # calls (+), puts (−), net: dealer GEX per strike, USD per 1% move
    total: float                   # sum of net
    flip: float | None             # price where total GEX changes sign nearest the spot; None if it does not
    excluded: int                  # contracts with open interest but no usable IV (left out)


def _contract_gex(spot: np.ndarray | float, c: dict, rate: float) -> np.ndarray:
    s = np.asarray(spot, dtype=float)
    return c["sign"] * bs_gamma(s, c["strike"], c["years"], c["iv"], rate) * c["oi"] * CONTRACT_SIZE * s ** 2 * 0.01


def dealer_gex(chain: pd.DataFrame, spot: float, now: pd.Timestamp, rate: float,
               flip_range: tuple[float, float]) -> Gex:
    """Dealer GEX under the SqueezeMetrics convention (module docstring). `rate` is the risk-free rate as a decimal;
    the gamma flip is searched for on a grid of 400 prices across `flip_range`."""
    live = chain[chain["oi"] > 0]
    usable = live[live["iv"].between(IV_MIN, IV_MAX)]
    c = {
        "sign": np.where(usable["type"].to_numpy() == "call", 1.0, -1.0),
        "strike": usable["strike"].to_numpy(dtype=float),
        "years": np.array([years_to_expiry(e, now) for e in usable["expiry"]], dtype=float),
        "iv": usable["iv"].to_numpy(dtype=float),
        "oi": usable["oi"].to_numpy(dtype=float),
    }
    each = _contract_gex(spot, c, rate)
    frame = pd.DataFrame({"strike": c["strike"], "side": np.where(c["sign"] > 0, "calls", "puts"), "gex": each})
    per = frame.pivot_table(index="strike", columns="side", values="gex", aggfunc="sum", fill_value=0.0)
    per = per.reindex(columns=["calls", "puts"], fill_value=0.0)
    per["net"] = per["calls"] + per["puts"]

    flip = None
    if len(each):
        grid = np.linspace(*flip_range, 400)
        profile = _contract_gex(grid[:, None], {k: v[None, :] for k, v in c.items()}, rate).sum(axis=1)
        crossings = np.flatnonzero(np.sign(profile[:-1]) * np.sign(profile[1:]) < 0)
        if len(crossings):
            # linear interpolation between the two grid points either side of each crossing; keep the nearest to spot
            roots = [grid[i] - profile[i] * (grid[i + 1] - grid[i]) / (profile[i + 1] - profile[i]) for i in crossings]
            flip = float(min(roots, key=lambda r: abs(r - spot)))
    return Gex(per, float(per["net"].sum()), flip, int(len(live) - len(usable)))


def strike_window(spot: float, history: pd.Series) -> tuple[float, float]:
    """Price range shown: ±STRIKE_RANGE around the last price, widened to the price history's range."""
    lo, hi = spot * (1 - STRIKE_RANGE), spot * (1 + STRIKE_RANGE)
    if not history.empty:
        lo, hi = min(lo, float(history.min())), max(hi, float(history.max()))
    return lo, hi
