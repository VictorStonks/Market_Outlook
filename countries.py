"""Country index returns: local-currency price return of one benchmark index per country (DESIGN.md 4, Equities).

Pure functions, no Streamlit. Each country is one Yahoo Finance index. The indices trade on different calendars,
so they are put on one shared calendar with the last close carried forward over a holiday; a timeframe starts on
the last shared date on or before (latest date - timeframe), as in `sectors`. Price return only, in each
index's own currency: FX moves are not included.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

import sectors


@dataclass(frozen=True)
class Country:
    name: str        # what the user sees
    ticker: str      # Yahoo Finance symbol
    index: str       # official index name, for the disclaimer
    tz: str          # exchange time zone
    close: dt.time   # local time after which today's daily bar is final (closing auction included)


# Order = order of the picker, chart legend fallback and table rows.
COUNTRIES = (
    Country("US", "^GSPC", "S&P 500", "America/New_York", dt.time(16, 0)),
    Country("Europe", "^STOXX", "STOXX Europe 600", "Europe/Berlin", dt.time(17, 30)),
    Country("UK", "^FTSE", "FTSE 100", "Europe/London", dt.time(16, 30)),
    Country("Japan", "^N225", "Nikkei 225", "Asia/Tokyo", dt.time(15, 30)),
    Country("Hong Kong", "^HSI", "Hang Seng Index", "Asia/Hong_Kong", dt.time(16, 10)),
    Country("Singapore", "^STI", "Straits Times Index", "Asia/Singapore", dt.time(17, 20)),
    Country("Malaysia", "^KLSE", "FTSE Bursa Malaysia KLCI", "Asia/Kuala_Lumpur", dt.time(17, 0)),
)
NAMES = tuple(c.name for c in COUNTRIES)
TICKERS = tuple(c.ticker for c in COUNTRIES)
DEFAULT_SELECTION = ("US", "Europe", "Singapore")
REBASE_TO = 100.0


def disclaimer() -> str:
    """Which index stands in for each country, for the panel footers."""
    used = "; ".join(f"{c.name} = {c.index} ({c.ticker})" for c in COUNTRIES)
    return (f"Indices used: {used}. Price return only (no dividends), in each index's local currency "
            "(exchange-rate moves excluded). Europe is a pan-European index that includes UK stocks, so it "
            "overlaps with UK. Markets close on different days; a holiday carries the last close forward.")


def drop_open_bars(prices: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Blank today's value for any index whose market has not closed yet: yfinance reports the running
    intraday level as that day's "Close". Rows left with no value at all are dropped."""
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    prices = prices.copy()
    for c in COUNTRIES:
        if c.ticker not in prices.columns:
            continue
        local = now.tz_convert(ZoneInfo(c.tz))
        if local.time() < c.close:
            prices.loc[prices.index == local.normalize().tz_localize(None), c.ticker] = float("nan")
    return prices.dropna(how="all")


def to_names(prices: pd.DataFrame) -> pd.DataFrame:
    """Rename ticker columns to country names, in COUNTRIES order, keeping only the indices present."""
    named = prices.rename(columns={c.ticker: c.name for c in COUNTRIES})
    return named[[n for n in NAMES if n in named.columns]]


def _shared(prices: pd.DataFrame) -> pd.DataFrame:
    """One row per date any index traded, last close carried forward over holidays."""
    return prices.sort_index().ffill()


def returns_table(prices: pd.DataFrame, timeframes: tuple[str, ...] = sectors.TIMEFRAMES) -> pd.DataFrame:
    """% return per country (rows, COUNTRIES order) and timeframe (columns). NaN where it cannot be computed.
    `prices` is dates x country names."""
    if prices.empty:
        return pd.DataFrame(columns=list(timeframes), dtype=float)
    shared = _shared(prices)
    columns = {}
    for tf in timeframes:
        base = sectors.base_date(shared.index, tf)
        columns[tf] = pd.Series(dtype=float) if base is None else (shared.iloc[-1] / shared.loc[base] - 1) * 100
    return pd.DataFrame(columns).reindex(list(prices.columns))


def paths(prices: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Each index rebased to REBASE_TO on the timeframe's start date, one row per shared date. Empty when the
    timeframe cannot be computed."""
    shared = _shared(prices) if not prices.empty else prices
    base = None if shared.empty else sectors.base_date(shared.index, timeframe)
    if base is None:
        return pd.DataFrame()
    window = shared.loc[base:]
    return window / window.iloc[0] * REBASE_TO
