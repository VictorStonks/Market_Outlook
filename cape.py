"""Shiller CAPE and its relationship with the next 10 years' real US equity return (DESIGN.md 14, "CAPE").

Pure module (no Streamlit), so it is unit-testable like `valuation.py`. Source: Robert J. Shiller's "Irrational
Exuberance" data (shillerdata.com), saved by the owner as cache/shiller_ie_data.csv and replaced by hand every month
or two (Shiller updates it monthly). Columns used, by position, because the file has an 8-row multi-line header:
date (YYYY.MM, where 1871.1 is October), P (S&P composite, monthly average of daily closes), E (trailing 12-month
earnings), CPI (CPI-U, not seasonally adjusted), CAPE (real price over the 10-year average of real earnings) and the
annualized real 10-year forward stock return (total return, dividends reinvested, after inflation).

Live CAPE: the file's latest month fixes the 10-year average of real earnings. Earnings per unit of CPI is held
constant (the average is in real terms), so on a later day CAPE = latest S&P 500 close / (that average x latest CPI).
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import pandas as pd

_COLUMNS = {0: "date", 1: "price", 3: "earnings", 4: "cpi", 12: "cape", 19: "fwd10_real"}
SAMPLES = {"Full history": None, "Since 1950": 1950, "Since 2000": 2000}   # pill label -> first year (None = all)
DEFAULT_SAMPLE = "Full history"
MODELS = {"Linear": "linear", "Earnings yield (1/CAPE)": "inverse"}         # pill label -> model
DEFAULT_MODEL = "Linear"


def _month(token: str) -> pd.Timestamp:
    """Shiller's "YYYY.MM" as a float-looking string: 1871.1 is October, 1871.01 January."""
    year, _, frac = token.partition(".")
    month = int(frac.ljust(2, "0")) if frac else 1
    return pd.Timestamp(int(year), month, 1)


def parse(csv_text: str) -> pd.DataFrame:
    """Shiller's ie_data CSV -> monthly frame indexed by month start: price, earnings, cpi, cape, fwd10_real (% a
    year). Missing values are NaN (the last ~10 years have no forward return; recent months have no earnings yet)."""
    raw = pd.read_csv(io.StringIO(csv_text), header=None, dtype=str, skip_blank_lines=False)
    raw = raw[raw[0].str.strip().str.fullmatch(r"\d{4}\.\d{1,2}", na=False)]
    if raw.empty or raw.shape[1] <= max(_COLUMNS):
        raise ValueError("not a Shiller ie_data file (no monthly rows, or too few columns)")
    df = raw[list(_COLUMNS)].rename(columns=_COLUMNS)
    df.index = pd.DatetimeIndex([_month(d.strip()) for d in df.pop("date")], name="month")
    df = df.apply(lambda c: pd.to_numeric(c.str.strip().str.rstrip("%").str.replace(",", ""), errors="coerce"))
    if df.index.duplicated().any():
        raise ValueError("duplicate months in the file")
    return df.sort_index()


def live_cape(shiller: pd.DataFrame, price: float, cpi: float) -> float:
    """CAPE on a later date from the file's latest month: price / (10-year real earnings average x cpi), with that
    average expressed per unit of CPI so it stays constant in real terms."""
    last = shiller.dropna(subset=["cape", "price", "cpi"]).iloc[-1]
    earnings_per_cpi = last["price"] / last["cape"] / last["cpi"]
    return float(price / (earnings_per_cpi * cpi))


def sample(shiller: pd.DataFrame, name: str) -> pd.DataFrame:
    """Rows from the sample's first year on (all rows for "Full history")."""
    first = SAMPLES[name]
    return shiller if first is None else shiller.loc[shiller.index >= pd.Timestamp(first, 1, 1)]


@dataclass(frozen=True)
class Fit:
    model: str                 # "linear" (y = a + b·CAPE) or "inverse" (y = a + b/CAPE)
    a: float
    b: float
    r2: float
    resid_sd: float            # standard deviation of the residuals, % a year: the typical miss of the line
    n: int                     # monthly starting points
    start: pd.Timestamp        # first and last starting month in the fit
    end: pd.Timestamp
    cape_max: float            # highest CAPE among the starting points

    def predict(self, cape: float | np.ndarray) -> float | np.ndarray:
        x = np.asarray(cape, dtype=float)
        return self.a + self.b * (x if self.model == "linear" else 1.0 / x)

    def equation(self) -> str:
        sign = "−" if self.b < 0 else "+"
        term = "CAPE" if self.model == "linear" else "(1 / CAPE)"
        return f"Real 10Y return (% a year) = {self.a:.2f} {sign} {abs(self.b):.3f} × {term}".replace("-", "−")


def fit(rows: pd.DataFrame, model: str) -> Fit | None:
    """Ordinary least squares of the forward 10-year real return on CAPE (or 1/CAPE) over the starting months that
    have both. None with fewer than 24 points. The monthly points share overlapping 10-year windows, so they are not
    independent: R² describes the fit but no significance test is valid without correcting for the overlap."""
    d = rows.dropna(subset=["cape", "fwd10_real"])
    d = d[d["cape"] > 0]
    if len(d) < 24:
        return None
    x = d["cape"].to_numpy() if model == "linear" else 1.0 / d["cape"].to_numpy()
    y = d["fwd10_real"].to_numpy()
    b, a = np.polyfit(x, y, 1)
    resid = y - (a + b * x)
    r2 = 1.0 - float(resid @ resid) / float(((y - y.mean()) ** 2).sum())
    return Fit(model, float(a), float(b), r2, float(resid.std(ddof=2)), len(d), d.index[0], d.index[-1],
               float(d["cape"].max()))


def nominal(real_pct: float, inflation_pct: float) -> float:
    """Fisher: (1 + real)(1 + inflation) − 1, all in % a year."""
    return ((1 + real_pct / 100) * (1 + inflation_pct / 100) - 1) * 100
