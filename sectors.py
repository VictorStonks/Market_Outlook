"""Sector returns: market-cap weighted, from saved close prices and share counts (DESIGN.md 4, Equities).

Pure functions, no Streamlit. The weight of a stock is its market cap at the START of the timeframe
(today's shares outstanding x the split-adjusted close on the start date), so a sector's return is
sum(weight x stock return) = the change in the sector's total market cap: a buy-and-hold basket with no
look-ahead. Price return only (no dividends). Weights use total shares, not the S&P's float-adjusted
shares, and today's share counts, so results differ slightly from the official S&P sector indices.
"""
from __future__ import annotations

import pandas as pd

TIMEFRAMES = ("1D", "1W", "1M", "3M", "6M", "12M")
LINE_TIMEFRAMES = ("1M", "3M", "6M", "12M")  # 1D and 1W have too few daily points for a line chart
BENCHMARK = "S&P 500"                         # all stocks with data, cap-weighted the same way

# Calendar offsets back from the latest close. The start is the last close on or before the anchor date.
_OFFSETS = {
    "1D": pd.DateOffset(days=1), "1W": pd.DateOffset(weeks=1), "1M": pd.DateOffset(months=1),
    "3M": pd.DateOffset(months=3), "6M": pd.DateOffset(months=6), "12M": pd.DateOffset(months=12),
}


def base_date(index: pd.DatetimeIndex, timeframe: str) -> pd.Timestamp | None:
    """Start date of a timeframe: the last close on or before (latest close - timeframe); None if the
    history does not reach back that far."""
    i = index.searchsorted(index[-1] - _OFFSETS[timeframe], side="right") - 1
    return None if i < 0 else index[i]


def _basket(prices: pd.DataFrame, shares: pd.Series, sector_of: pd.Series, base: pd.Timestamp) -> pd.Index:
    """Tickers with a share count, a sector and a close on both the start date and the latest date."""
    priced = prices.loc[base].notna() & prices.iloc[-1].notna()
    return priced.index[priced].intersection(shares.index).intersection(sector_of.index)


def returns_table(prices: pd.DataFrame, shares: pd.Series, sector_of: pd.Series,
                  timeframes: tuple[str, ...] = TIMEFRAMES) -> pd.DataFrame:
    """% return per sector (rows, plus BENCHMARK) and timeframe (columns). NaN where a timeframe cannot be
    computed. `prices` is dates x tickers, `shares` and `sector_of` are indexed by ticker."""
    if prices.empty:
        return pd.DataFrame(columns=list(timeframes), dtype=float)
    columns = {}
    for tf in timeframes:
        base = base_date(prices.index, tf)
        if base is None:
            columns[tf] = pd.Series(dtype=float)
            continue
        tickers = _basket(prices, shares, sector_of, base)
        start = prices.loc[base, tickers] * shares[tickers]
        end = prices.iloc[-1][tickers] * shares[tickers]
        sector = sector_of[tickers]
        r = end.groupby(sector).sum() / start.groupby(sector).sum() - 1
        r[BENCHMARK] = end.sum() / start.sum() - 1
        columns[tf] = r * 100
    return pd.DataFrame(columns)


def paths(prices: pd.DataFrame, shares: pd.Series, sector_of: pd.Series, timeframe: str) -> pd.DataFrame:
    """Cumulative % return since the timeframe's start date, one row per trading day, one column per sector
    plus BENCHMARK. Every column starts at 0. Empty when the timeframe cannot be computed."""
    base = None if prices.empty else base_date(prices.index, timeframe)
    if base is None:
        return pd.DataFrame()
    tickers = _basket(prices, shares, sector_of, base)
    cap = prices.loc[base:, tickers].ffill() * shares[tickers]  # ffill: a trading halt must not drop a stock
    by_sector = cap.T.groupby(sector_of[tickers].to_numpy()).sum().T
    by_sector[BENCHMARK] = cap.sum(axis=1)
    return (by_sector / by_sector.iloc[0] - 1) * 100


def market_caps(prices: pd.DataFrame, shares: pd.Series, sector_of: pd.Series) -> pd.Series:
    """Latest total market cap per sector (USD), largest first. Used to order rows and choose defaults."""
    if prices.empty:
        return pd.Series(dtype=float)
    last = prices.iloc[-1].dropna()
    tickers = last.index.intersection(shares.index).intersection(sector_of.index)
    return (last[tickers] * shares[tickers]).groupby(sector_of[tickers]).sum().sort_values(ascending=False)
