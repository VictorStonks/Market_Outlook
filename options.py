"""Listed options: open interest by strike, max pain and dealer gamma exposure (DESIGN.md 14, "Options open interest"),
and the risk-neutral distribution behind the analyst target probability (DESIGN.md 14, "Analyst target probability").

Pure module (no Streamlit), so it is unit-testable like `cape.py`. Input is one tidy chain frame with a row per
contract: expiry (Timestamp), type ("call" / "put"), strike, oi (contracts), iv (implied volatility, decimal), bid, ask.

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


# ---------- Risk-neutral distribution at one expiry (DESIGN.md 14, "Analyst target probability") ----------
# Black-76 on the forward, so dividends never need to be known: the forward comes from put-call parity. Implied vol is
# solved from each out-of-the-money option's bid/ask mid (Yahoo's own IV column is unreliable on long-dated options),
# fitted with a quadratic smile in log-moneyness, and the probability of ending above a price K is minus the slope of
# the undiscounted call price in K (Breeden-Litzenberger), which includes the smile's skew.

TARGET_HORIZON_DAYS = 365      # Yahoo analyst price targets are taken to be 12-month targets
MAX_REL_SPREAD = 0.5           # a quote whose (ask - bid) / mid is above this is too wide to price from
WIDE_SPREAD = 0.2              # median relative spread above which the footer calls the quotes wide
MIN_QUOTES_SIDE = 3            # usable out-of-the-money quotes needed below and above the forward
PARITY_STRIKES = 5             # strikes nearest the spot used for the put-call parity forward
CONE_QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)
_erf = np.vectorize(math.erf, otypes=[float])


def _ncdf(x) -> np.ndarray:
    return 0.5 * (1 + _erf(np.asarray(x, dtype=float) / math.sqrt(2)))


def _npdf(x) -> np.ndarray:
    return np.exp(-0.5 * np.asarray(x, dtype=float) ** 2) / math.sqrt(2 * math.pi)


def nearest_expiry(expiries: list[pd.Timestamp], today, days: int = TARGET_HORIZON_DAYS) -> pd.Timestamp | None:
    """The listed expiry closest to `days` calendar days after `today` (ties: the earlier one)."""
    return min(expiries, key=lambda e: (abs((e.date() - today).days - days), e)) if expiries else None


def black_price(fwd: float, strike: np.ndarray, years: float, vol: np.ndarray, is_call: np.ndarray) -> np.ndarray:
    """Undiscounted Black-76 price of calls (is_call) or puts on the forward."""
    vt = np.asarray(vol, dtype=float) * math.sqrt(years)
    d1 = (np.log(fwd / strike) + 0.5 * vt ** 2) / vt
    call = fwd * _ncdf(d1) - strike * _ncdf(d1 - vt)
    return np.where(is_call, call, call - fwd + strike)


def black_iv(price: np.ndarray, fwd: float, strike: np.ndarray, years: float, is_call: np.ndarray) -> np.ndarray:
    """Implied volatility by bisection on [IV_MIN, IV_MAX]; NaN where the price is outside the no-arbitrage bounds."""
    price, strike = np.asarray(price, dtype=float), np.asarray(strike, dtype=float)
    intrinsic = np.where(is_call, np.maximum(fwd - strike, 0), np.maximum(strike - fwd, 0))
    ok = (price > intrinsic) & (price < np.where(is_call, fwd, strike))
    lo, hi = np.full(price.shape, IV_MIN), np.full(price.shape, IV_MAX)
    for _ in range(60):
        mid = (lo + hi) / 2
        high = black_price(fwd, strike, years, mid, is_call) > price
        hi, lo = np.where(high, mid, hi), np.where(high, lo, mid)
    iv = (lo + hi) / 2
    return np.where(ok & (iv > IV_MIN * 1.01) & (iv < IV_MAX * 0.99), iv, np.nan)


def _two_sided(frame: pd.DataFrame) -> pd.DataFrame:
    q = frame[(frame["bid"] > 0) & (frame["ask"] > frame["bid"])].copy()
    q["mid"] = (q["bid"] + q["ask"]) / 2
    q["spread"] = (q["ask"] - q["bid"]) / q["mid"]
    return q


def parity_forward(chain: pd.DataFrame, spot: float, discount: float) -> float | None:
    """Median over the PARITY_STRIKES strikes nearest the spot of K + (call mid − put mid) / discount, using strikes with
    a two-sided call and a two-sided put quote; None when there is none."""
    both = _two_sided(chain).pivot_table(index="strike", columns="type", values="mid", aggfunc="first")
    if not {"call", "put"} <= set(both.columns):
        return None
    both = both[["call", "put"]].dropna()
    if both.empty:
        return None
    near = both.iloc[np.argsort(np.abs(both.index.to_numpy(dtype=float) - spot))[:PARITY_STRIKES]]
    return float(np.median(near.index.to_numpy(dtype=float) + (near["call"] - near["put"]).to_numpy() / discount))


def smile_vol(smile: np.ndarray, x_range: tuple[float, float], x: np.ndarray) -> np.ndarray:
    """Fitted IV at log-moneyness `x`: the quadratic inside the strikes priced, continued along its edge slope beyond
    them (smooth, so the distribution has no false lump at the last strike), floored at IV_MIN."""
    xc = np.clip(x, *x_range)
    return np.maximum(np.polyval(smile, xc) + np.polyval(np.polyder(smile), xc) * (x - xc), IV_MIN)


def _close(day: pd.Timestamp) -> pd.Timestamp:
    """16:00 New York on `day`."""
    return pd.Timestamp(day.date()).tz_localize("America/New_York") + pd.Timedelta(hours=16)


@dataclass(frozen=True)
class Implied:
    expiry: pd.Timestamp
    years: float                   # from now to 16:00 New York on the expiry date
    spot: float
    forward: float
    parity: bool                   # forward from put-call parity (else the spot grown at the rate, no dividends)
    quotes: pd.DataFrame           # quotes priced: strike, type, bid, ask, mid, spread, iv, iv_bid, iv_ask, fit_iv
    smile: np.ndarray              # quadratic coefficients of IV in ln(K / forward), highest power first
    strike_range: tuple[float, float]   # lowest and highest strike priced; the smile is extrapolated beyond them
    grid: np.ndarray               # prices at expiry, ascending
    survival: np.ndarray           # risk-neutral P(price at expiry >= grid)
    smoothed: float                # largest correction made to keep `survival` non-increasing (0 when none)

    def prob_above(self, price: float) -> float:
        return float(np.interp(price, self.grid, self.survival))

    def quantile(self, q: float) -> float:
        return float(np.interp(q, 1 - self.survival, self.grid))

    def extrapolated(self, price: float) -> bool:
        return not self.strike_range[0] <= price <= self.strike_range[1]

    def fitted_iv(self, strikes) -> np.ndarray:
        """The fitted smile's implied volatility (decimal) at each strike, extrapolated beyond the strikes priced."""
        x_range = tuple(np.log(np.array(self.strike_range) / self.forward))
        return smile_vol(self.smile, x_range, np.log(np.asarray(strikes, dtype=float) / self.forward))

    def fit_error(self) -> float:
        """Root-mean-square miss of the fitted smile against the quotes' implied volatilities (decimal)."""
        return float(np.sqrt(np.mean((self.quotes["iv"] - self.quotes["fit_iv"]) ** 2)))

    def cone(self, start: pd.Timestamp, qs: tuple[float, ...] = CONE_QUANTILES) -> pd.DataFrame:
        """Quantiles of the price on each business day from `start` (today, at the spot) to the expiry, plus a
        "forward" column: the forward grown smoothly from the spot, which is the distribution's mean (option prices
        pin the mean, not the median, to the forward). Only the expiry end comes from option prices; in between, the
        expiry's log-return quantiles are scaled by the square root of the fraction of time elapsed."""
        dates = pd.DatetimeIndex([start]).append(pd.bdate_range(start + pd.Timedelta(days=1), self.expiry))
        left = np.array([self.years] + [years_to_expiry(self.expiry, _close(d)) for d in dates[1:]])
        tau = np.clip(1 - left / self.years, 0, 1)
        tau[-1] = 1.0
        centre = self.spot * (self.forward / self.spot) ** tau
        out = pd.DataFrame({q: centre * (self.quantile(q) / self.forward) ** np.sqrt(tau) for q in qs}, index=dates)
        out["forward"] = centre
        return out


def implied_distribution(chain: pd.DataFrame, spot: float, now: pd.Timestamp,
                         rate: float) -> tuple[Implied | None, str | None]:
    """Risk-neutral distribution of the price at one expiry from the chain's bid/ask quotes (section comment above).
    `chain` holds one expiry with columns expiry, type, strike, bid, ask; `rate` is a decimal. Returns (None, reason)
    when the quotes cannot support a distribution."""
    expiry = chain["expiry"].iloc[0]
    years = years_to_expiry(expiry, now)
    discount = math.exp(-rate * years)
    fwd = parity_forward(chain, spot, discount)
    parity = fwd is not None and fwd > 0
    if not parity:
        fwd = spot / discount
    q = _two_sided(chain)
    q = q[((q["type"] == "put") & (q["strike"] < fwd)) | ((q["type"] == "call") & (q["strike"] >= fwd))]
    q = q[q["spread"] <= MAX_REL_SPREAD].sort_values("strike")
    q = q.assign(iv=black_iv(q["mid"].to_numpy(dtype=float) / discount, fwd, q["strike"].to_numpy(dtype=float),
                             years, (q["type"] == "call").to_numpy())).dropna(subset=["iv"])
    below, above = int((q["strike"] < fwd).sum()), int((q["strike"] >= fwd).sum())
    if below < MIN_QUOTES_SIDE or above < MIN_QUOTES_SIDE:
        return None, (f"{below} strikes below and {above} above the forward price have a usable two-sided quote "
                      f"(spread under {MAX_REL_SPREAD:.0%} of the mid); {MIN_QUOTES_SIDE} are needed on each side")

    k = q["strike"].to_numpy(dtype=float)
    calls = (q["type"] == "call").to_numpy()
    q = q.assign(iv_bid=black_iv(q["bid"].to_numpy(dtype=float) / discount, fwd, k, years, calls),
                 iv_ask=black_iv(q["ask"].to_numpy(dtype=float) / discount, fwd, k, years, calls))
    x = np.log(k / fwd)
    vt = q["iv"].to_numpy() * math.sqrt(years)
    vega = _npdf((-x + 0.5 * vt ** 2) / vt)          # Black vega up to a constant: weights the fit to liquid strikes
    smile = np.polyfit(x, q["iv"].to_numpy(), 2, w=np.sqrt(vega / vega.max()))
    x_lo, x_hi = float(x.min()), float(x.max())
    q = q.assign(fit_iv=np.maximum(np.polyval(smile, x), IV_MIN))

    vol_at = lambda x: smile_vol(smile, (x_lo, x_hi), x)  # noqa: E731
    atm = float(vol_at(np.array([0.0]))[0]) * math.sqrt(years)
    grid = fwd * np.exp(np.linspace(-6 * atm, 6 * atm, 1201))
    g = np.log(grid / fwd)
    vol = vol_at(g)
    slope = np.where(vol > IV_MIN, np.polyval(np.polyder(smile), np.clip(g, x_lo, x_hi)), 0.0)   # d vol / d ln K
    gt = vol * math.sqrt(years)
    d1 = (-g + 0.5 * gt ** 2) / gt
    # P(S_T > K) = -dC/dK with C = Black(F, K, vol(K)): N(d2) - F phi(d1) sqrt(T) dvol/dK, and dvol/dK = slope / K
    raw = np.clip(_ncdf(d1 - gt) - fwd * _npdf(d1) * math.sqrt(years) * slope / grid, 0, 1)
    survival = np.minimum.accumulate(raw)
    return Implied(expiry, years, spot, float(fwd), parity, q.reset_index(drop=True), smile,
                   (float(k.min()), float(k.max())), grid, survival, float(np.max(raw - survival))), None
