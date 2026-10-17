"""Data download pages (Yahoo Finance and FRED): turn each ticker's raw Yahoo data into one CSV with three header rows (Ticker / Dataset /
Field) and one row per date.

Pure module (no Streamlit), so it is unit-testable like `valuation.py`. The network side is `data.load_download_raw`,
which fetches everything a ticker has once (daily history, earnings dates, valuation measures, EPS estimates); this
module only slices and reshapes it, so changing the interval, timeframe or adjustment never calls Yahoo again.

Datasets have different dates (trading days, ex-dates, report dates, month ends), so the combined table is sparse:
a cell is empty when that dataset has nothing on that date.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import valuation

PRICE, DIVIDENDS, SPLITS, EARNINGS, VALUATION = "Price & volume", "Dividends", "Splits", "Earnings", "Valuation"
DATASETS = (PRICE, DIVIDENDS, SPLITS, EARNINGS, VALUATION)

INTERVALS = {"Daily": None, "Weekly": "W", "Monthly": "M"}   # pandas period used to group daily bars
TIMEFRAMES = {"1M": pd.DateOffset(months=1), "3M": pd.DateOffset(months=3), "6M": pd.DateOffset(months=6),
              "YTD": None, "1Y": pd.DateOffset(years=1), "3Y": pd.DateOffset(years=3),
              "5Y": pd.DateOffset(years=5), "10Y": pd.DateOffset(years=10),
              "20Y": pd.DateOffset(years=20), "Max": None}
# A timeframe too short for the interval would give one or two rows, so it is not offered.
_TOO_SHORT = {"Daily": (), "Weekly": ("1M",), "Monthly": ("1M", "3M", "6M", "YTD")}

NTM_PE_FIELD = "Fwd 12M P/E (computed)"
NTM_EST_FIELD = "Fwd 12M EPS uses estimates"


def timeframes_for(interval: str) -> list[str]:
    return [t for t in TIMEFRAMES if t not in _TOO_SHORT[interval]]


def start_date(timeframe: str, latest: pd.Timestamp) -> pd.Timestamp | None:
    """First date kept: latest date minus the timeframe; 1 Jan of the latest year for YTD; None (all) for Max."""
    if timeframe == "Max":
        return None
    if timeframe == "YTD":
        return pd.Timestamp(year=latest.year, month=1, day=1)
    return (latest - TIMEFRAMES[timeframe]).normalize()


@dataclass
class Raw:
    """Everything fetched for one ticker. Frames are empty when Yahoo has nothing; `error` is set when the ticker
    could not be loaded at all."""
    ticker: str
    history: pd.DataFrame = field(default_factory=pd.DataFrame)    # daily, auto_adjust=False, tz-naive dates
    earnings: pd.DataFrame = field(default_factory=pd.DataFrame)   # EPS Estimate / Reported EPS / Surprise(%)
    measures: pd.DataFrame = field(default_factory=pd.DataFrame)   # valuation measures; rows = month-end dates, columns = measures
    estimates: dict = field(default_factory=dict)                  # "0q", "+1q", "0y", "+1y" average EPS
    lag_days: int = valuation.DEFAULT_LAG_DAYS
    gap_days: tuple[int, int] = valuation._GAP_DAYS
    name: str = ""
    kind: str = ""          # Yahoo instrument type: EQUITY, ETF, INDEX, CRYPTOCURRENCY, ...
    currency: str | None = None
    eps_currency: str | None = None
    major_currency: str | None = None  # GBP for prices quoted in pence (GBp); EPS is in the major unit
    units_per_major: int = 1           # 100 for pence
    error: str | None = None


def availability(raw: Raw) -> dict[str, bool]:
    h = raw.history
    has = lambda col: not h.empty and col in h and h[col].fillna(0).ne(0).any()  # noqa: E731
    return {
        PRICE: not h.empty,
        DIVIDENDS: has("Dividends"),
        SPLITS: has("Stock Splits"),
        EARNINGS: not raw.earnings.empty,
        VALUATION: not raw.measures.empty or not ntm_quarters(raw).empty,
    }


def _label(cur: str | None, adj: str) -> str:
    return f"{cur}, {adj}" if cur else adj


def price_table(raw: Raw, interval: str, adjusted: bool) -> pd.DataFrame:
    """OHLC, Adj Close and volume, labelled with currency and adjustment. Weekly and monthly bars are built from the
    daily bars (first open, highest high, lowest low, last close, summed volume) and dated on the period's last
    trading day. `adjusted`: Open/High/Low/Close are multiplied by Adj Close / Close, so they are adjusted for
    dividends as well as splits and Adj Close is dropped (it would equal Close)."""
    h = raw.history
    if h.empty:
        return pd.DataFrame()
    h = h[["Open", "High", "Low", "Close", "Adj Close", "Volume"]].copy()
    if adjusted:
        factor = (h["Adj Close"] / h["Close"]).where(h["Close"] != 0)
        h[["Open", "High", "Low", "Close"]] = h[["Open", "High", "Low", "Close"]].mul(factor, axis=0)
        h = h.drop(columns="Adj Close")
    period = INTERVALS[interval]
    if period:
        key = h.index.to_period(period)
        groups, last_day = h.groupby(key), h.index.to_series().groupby(key).max()
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        if not adjusted:
            agg["Adj Close"] = "last"
        h = groups.agg(agg)[list(h.columns)]
        h.index = last_day.reindex(h.index).to_numpy()
    adj = "split + div adj." if adjusted else "split-adj."
    names = {c: f"{c} ({_label(raw.currency, adj)})" for c in ("Open", "High", "Low", "Close")}
    names["Adj Close"] = f"Adj Close ({_label(raw.currency, 'split + div adj.')})"
    names["Volume"] = "Volume (shares, split-adj.)"
    return h.rename(columns=names)


def dividends_table(raw: Raw) -> pd.DataFrame:
    """Cash dividend per share on its ex-date (Yahoo adjusts it for later splits)."""
    d = raw.history.get("Dividends", pd.Series(dtype=float))
    d = d[d.fillna(0) != 0]
    return d.groupby(level=0).sum().to_frame(f"Dividend per share ({_label(raw.currency, 'ex-date, split-adj.')})")


def splits_table(raw: Raw) -> pd.DataFrame:
    s = raw.history.get("Stock Splits", pd.Series(dtype=float))
    s = s[s.fillna(0) != 0]
    return s.groupby(level=0).last().to_frame("Split ratio (new shares per old)")


def earnings_table(raw: Raw) -> pd.DataFrame:
    """One row per report date (the next scheduled report has an estimate and no reported value yet)."""
    e = raw.earnings
    if e.empty:
        return pd.DataFrame()
    cur = raw.eps_currency
    out = pd.DataFrame({
        f"EPS estimate ({_label(cur, 'consensus')})": e["EPS Estimate"],
        f"EPS reported ({_label(cur, 'as Yahoo reports')})": e["Reported EPS"],
        "EPS surprise (%)": e["Surprise(%)"],
    }, index=e.index)
    return out.groupby(level=0).last()


def ntm_quarters(raw: Raw) -> pd.DataFrame:
    if raw.earnings.empty:
        return pd.DataFrame(columns=["eps", "estimated"])
    reported = raw.earnings["Reported EPS"].dropna()
    return valuation.build_quarters(reported, raw.estimates, raw.lag_days, raw.gap_days)


def pe_currency_clash(raw: Raw) -> bool:
    return bool(raw.eps_currency and raw.major_currency and raw.eps_currency != raw.major_currency)


def ntm_table(raw: Raw, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Blended forward 12M EPS (valuation.ntm_eps, the same method as the Equities forward P/E panels) on each price
    row's date, the P/E it implies (close in the currency's major unit over that EPS, only where EPS is positive), and
    whether the 12-month window already reaches into quarters that are still estimates. P/E is left empty when Yahoo
    reports EPS in a different currency from the price (e.g. VOD.L: GBp price, EUR EPS), see `pe_currency_clash`."""
    quarters = ntm_quarters(raw)
    if quarters.empty or raw.history.empty or len(dates) == 0:
        return pd.DataFrame()
    eps = valuation.ntm_eps(quarters, dates)
    close = raw.history["Close"].reindex(dates) / raw.units_per_major
    pe = close / eps.where(eps > 0) if not pe_currency_clash(raw) else pd.Series(float("nan"), index=dates)
    est_from = valuation.estimates_from(quarters)
    uses_est = pd.Series(dates >= est_from if est_from is not None else False, index=dates)
    out = pd.DataFrame({f"Fwd 12M EPS ({_label(raw.eps_currency, 'blended, computed')})": eps, NTM_PE_FIELD: pe, NTM_EST_FIELD: uses_est.where(eps.notna())})
    return out.dropna(how="all", subset=out.columns[:2])


def valuation_table(raw: Raw, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Yahoo's valuation measures (month ends, plus today's values dated on the fetch date) joined with the
    computed forward 12M EPS and P/E on the price rows."""
    v = raw.measures.rename(columns=lambda m: f"{m} (Yahoo)")
    if not v.empty:
        cap_cols = [c for c in v.columns if c.startswith(("Market Cap", "Enterprise Value ("))]
        v = v.rename(columns={c: c.replace(" (Yahoo)", f" ({_label(raw.currency, 'Yahoo')})") for c in cap_cols})
    return v.join(ntm_table(raw, dates), how="outer") if not v.empty else ntm_table(raw, dates)


def ticker_tables(raw: Raw, datasets: list[str], interval: str, adjusted: bool,
                  start: pd.Timestamp | None) -> dict[str, pd.DataFrame]:
    """{dataset: table} for one ticker, cut to dates on or after `start`; datasets the ticker lacks are left out."""
    prices = price_table(raw, interval, adjusted)
    builders = {
        PRICE: lambda: prices,
        DIVIDENDS: lambda: dividends_table(raw),
        SPLITS: lambda: splits_table(raw),
        EARNINGS: lambda: earnings_table(raw),
        VALUATION: lambda: valuation_table(raw, pd.DatetimeIndex(prices.index)),
    }
    out = {}
    for name in datasets:
        t = builders[name]()
        if start is not None and not t.empty:
            t = t[t.index >= start]
        if not t.empty:
            out[name] = t
    return out


def combine(tables: dict[str, dict[str, pd.DataFrame]],
            names: tuple[str, str, str] = ("Ticker", "Dataset", "Field")) -> pd.DataFrame:
    """{ticker: {dataset: table}} -> one frame, oldest first, columns = MultiIndex `names` (Ticker, Dataset, Field;
    the FRED page uses Series ID, Title, Field)."""
    parts = {(t, d): table for t, by_ds in tables.items() for d, table in by_ds.items()}
    if not parts:
        return pd.DataFrame()
    frames = []
    for (ticker, dataset), table in parts.items():
        table = table.copy()
        table.columns = pd.MultiIndex.from_tuples([(ticker, dataset, f) for f in table.columns],
                                                  names=list(names))
        frames.append(table)
    out = pd.concat(frames, axis=1, join="outer").sort_index()
    out.index = pd.DatetimeIndex(out.index).rename("Date")
    return out


def to_csv(df: pd.DataFrame) -> bytes:
    """Three header rows (Ticker, Dataset, Field), ISO dates, full precision, no thousands separators.
    Read back with pd.read_csv(f, header=[0, 1, 2], index_col=0, parse_dates=True)."""
    return df.to_csv(date_format="%Y-%m-%d").encode("utf-8")


def flat_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Same table with one-line headers ("AAPL · Price & volume · Close (USD, split-adj.)") for the on-page preview."""
    out = df.copy()
    out.columns = [" · ".join(c) for c in df.columns]
    return out


# --- FRED download page --------------------------------------------------------------------------------------------
# FRED converts a series only to a coarser frequency (daily -> monthly, never monthly -> daily), so the frequency
# picker offers only targets at least as coarse as the coarsest selected series.
FRED_FREQS = {"As published": None, "Weekly": "w", "Monthly": "m", "Quarterly": "q", "Annual": "a"}
FRED_AGGS = {"Average": "avg", "Sum": "sum", "End of period": "eop"}
_FREQ_RANK = {"D": 0, "W": 1, "BW": 2, "M": 3, "Q": 4, "SA": 5, "A": 6}
_TARGET_RANK = {"Weekly": 1, "Monthly": 3, "Quarterly": 4, "Annual": 6}
_MONTHLY_SHORT = ("1M", "3M", "6M", "YTD")
_TOO_SHORT_BY_RANK = {0: (), 1: ("1M",), 2: ("1M", "3M"), 3: _MONTHLY_SHORT, 4: (*_MONTHLY_SHORT, "1Y"),
                      5: (*_MONTHLY_SHORT, "1Y"), 6: (*_MONTHLY_SHORT, "1Y", "3Y")}


def freq_rank(frequency_short: str) -> int:
    """FRED's frequency_short ("D", "W", "BW", "M", "Q", "SA", "A") -> 0 (finest) to 6 (coarsest)."""
    return _FREQ_RANK.get(str(frequency_short).strip().upper(), 0)


def fred_frequencies(native: list[str]) -> list[str]:
    coarsest = max((freq_rank(f) for f in native), default=0)
    return ["As published", *(f for f, r in _TARGET_RANK.items() if r >= coarsest)]


def fred_timeframes(native: list[str], target: str) -> list[str]:
    rank = max([*(freq_rank(f) for f in native), _TARGET_RANK.get(target, 0)])
    return [t for t in TIMEFRAMES if t not in _TOO_SHORT_BY_RANK[rank]]


def fred_field(transform_label: str, units: str, transform: str, frequency: str, aggregation: str) -> str:
    """Column name stating the transform, the unit it is in and any frequency conversion, e.g.
    "Level (Percent)", "% change from a year ago", "Level (Percent; monthly, end of period)"."""
    parts = [units] if transform in ("lin", "chg", "ch1") and units else []
    if frequency != "As published":
        parts.append(f"{frequency.lower()}, {aggregation.lower()}")
    return f"{transform_label} ({'; '.join(parts)})" if parts else transform_label


def fred_tables(series: dict[tuple[str, str], pd.Series], titles: dict[str, str], fields: dict[tuple[str, str], str],
                start: pd.Timestamp | None) -> dict[str, dict[str, pd.DataFrame]]:
    """{(series id, transform): observations} -> {series id: {title: table of one column per transform}} for
    `combine`, cut to dates on or after `start`; `fields` names each (series id, transform) column. Empty
    observations are left out."""
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for (sid, transform), s in series.items():
        s = s.dropna()
        if start is not None:
            s = s[s.index >= start]
        if s.empty:
            continue
        by_title = out.setdefault(sid, {})
        table = by_title.get(titles[sid], pd.DataFrame())
        by_title[titles[sid]] = table.join(s.rename(fields[sid, transform]), how="outer") if not table.empty \
            else s.rename(fields[sid, transform]).to_frame()
    return out
