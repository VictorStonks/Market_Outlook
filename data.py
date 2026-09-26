"""Data layer: FRED loaders, caching and small series helpers (DESIGN.md sections 9.3 and 11 step 3).

Pages and panels never call the API directly; they go through load_series so a failed fetch degrades
to the last good copy with a notice instead of a traceback (DESIGN.md 5.4).
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from fredapi import Fred

log = logging.getLogger(__name__)

SGT = ZoneInfo("Asia/Singapore")  # refresh times are stamped in SGT; data as-of dates are observation dates

# FRED's `units` transform applied server-side before the data is returned.
# See https://api.stlouisfed.org/docs/fred/series_observations.html for the full list.
#   lin  - levels, no transform (default)
#   chg  - change from previous value
#   ch1  - change from a year ago
#   pch  - percent change from previous value
#   pc1  - percent change from a year ago
#   pca  - compounded annual rate of change
#   cch  - continuously compounded rate of change
#   cca  - continuously compounded annual rate of change
#   log  - natural log
FRED_UNITS = ("lin", "chg", "ch1", "pch", "pc1", "pca", "cch", "cca", "log")

# Plain-English names for the transforms above (Methodology page).
FRED_UNITS_LABEL = {
    "lin": "Level", "chg": "Change from previous value", "ch1": "Change from a year ago",
    "pch": "% change from previous value", "pc1": "% change from a year ago",
    "pca": "Compounded annual rate of change", "cch": "Continuously compounded rate of change",
    "cca": "Continuously compounded annual rate of change", "log": "Natural log",
}


def require_api_key() -> None:
    """Stop the app with a plain message (no traceback) when the FRED key is not configured."""
    try:
        st.secrets["FRED_API_KEY"]
    except (KeyError, FileNotFoundError):
        st.error("FRED API key not found. Add FRED_API_KEY to .streamlit/secrets.toml (see CLAUDE.md).")
        st.stop()


@st.cache_resource
def _fred() -> Fred:
    return Fred(api_key=st.secrets["FRED_API_KEY"])


@st.cache_data(ttl=3600)
def fred_data(series_id: str, years: int, units: str = "lin") -> pd.Series:
    if units not in FRED_UNITS:
        raise ValueError(f"units must be one of {FRED_UNITS}, got {units!r}")
    start = dt.datetime.now() - dt.timedelta(days=365 * years)
    return _fred().get_series(series_id, observation_start=start, units=units)


# Last successful fetch per request. Lives for the server process, so a transient FRED outage after
# the 1h cache expires still shows the previous data, clearly labelled, instead of an empty panel.
_LAST_GOOD: dict[tuple[str, int, str], pd.Series] = {}


def load_series(series_id: str, years: int, units: str = "lin") -> tuple[pd.Series, str | None]:
    """fred_data that never raises. Returns (series, notice); notice is None on success, otherwise a
    one-line message for st.warning naming the series and what is shown instead."""
    key = (series_id, years, units)
    try:
        s = fred_data(series_id, years, units)
        if s.dropna().empty:
            raise ValueError("no observations returned")
    except Exception as exc:  # network, bad series id, rate limit: all become a notice, never a traceback
        log.warning("FRED fetch failed for %s (%s): %s", series_id, units, exc)
        cached = _LAST_GOOD.get(key)
        if cached is None or cached.dropna().empty:
            return pd.Series(dtype=float), f"Fetch failed for {series_id}; no cached data available."
        return cached, (f"Fetch failed for {series_id}; showing cached data "
                        f"(latest observation {cached.dropna().index[-1]:%d %b %Y}).")
    _LAST_GOOD[key] = s
    return s, None


# ---------- Investment universe: S&P 500 constituents ----------

# TO CHANGE THE UNIVERSE SOURCE: point UNIVERSE_URL at any CSV that has these four columns (extra columns
# are ignored): Symbol, Security, GICS Sector, GICS Sub-Industry. Symbols may use dots (BRK.B); they are
# converted to Yahoo's dash form (BRK-B). Each successful download overwrites UNIVERSE_CACHE, which is the
# fallback used when the URL cannot be reached, so the app never depends on the online source being up.
# Current source: community dataset github.com/datasets/s-and-p-500-companies (not an official S&P list).
UNIVERSE_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
UNIVERSE_CACHE = Path(__file__).parent / "cache" / "sp500_constituents.csv"
_UNIVERSE_COLUMNS = {"Symbol": "ticker", "Security": "name", "GICS Sector": "sector",
                     "GICS Sub-Industry": "sub_industry"}
_MIN_UNIVERSE_SIZE = 400  # a download smaller than this is treated as broken and never saved over the fallback


def _tidy_universe(csv_text: str) -> pd.DataFrame:
    """Raw constituents CSV -> tidy table (ticker, name, sector, sub_industry, label) sorted by ticker."""
    raw = pd.read_csv(io.StringIO(csv_text), dtype=str)
    missing = [c for c in _UNIVERSE_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"universe file is missing columns: {', '.join(missing)}")
    df = raw[list(_UNIVERSE_COLUMNS)].rename(columns=_UNIVERSE_COLUMNS).apply(lambda c: c.str.strip())
    df = df.dropna(subset=["ticker", "name"]).drop_duplicates("ticker")
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    df["label"] = df["ticker"] + " — " + df["name"]  # what the search box shows and matches on
    if len(df) < _MIN_UNIVERSE_SIZE:
        raise ValueError(f"universe has only {len(df)} rows")
    return df.sort_values("ticker").reset_index(drop=True)


@st.cache_data(ttl=86400)
def _fetch_universe() -> pd.DataFrame:
    """Download and validate the constituents, then save the raw CSV as the offline fallback."""
    resp = requests.get(UNIVERSE_URL, timeout=10)
    resp.raise_for_status()
    df = _tidy_universe(resp.text)  # validate before saving so a bad download cannot replace a good copy
    UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_CACHE.write_text(resp.text, encoding="utf-8", newline="")
    return df


def load_universe() -> tuple[pd.DataFrame, str | None]:
    """The S&P 500 universe; never raises. Returns (table, notice); notice is None when the download worked,
    otherwise a one-line message for st.warning saying what is shown instead (DESIGN.md 5.4)."""
    try:
        return _fetch_universe(), None
    except Exception as exc:  # network, bad file, missing columns: all fall back to the saved copy
        log.warning("S&P 500 list fetch failed: %s", exc)
    try:
        df = _tidy_universe(UNIVERSE_CACHE.read_text(encoding="utf-8"))
    except Exception as exc:  # no saved copy yet, or it is unreadable
        log.warning("Saved S&P 500 list unusable: %s", exc)
        return pd.DataFrame(columns=[*_UNIVERSE_COLUMNS.values(), "label"]), \
            "Fetch failed for S&P 500 list; no saved copy available."
    saved = dt.datetime.fromtimestamp(UNIVERSE_CACHE.stat().st_mtime)
    return df, f"Fetch failed for S&P 500 list; showing saved copy from {saved:%d %b %Y}."


# ---------- Equity prices: yfinance, saved locally ----------

# Two price databases come from one Yahoo download (auto_adjust=False returns both fields), saved as
# parquet files of dates x tickers:
#   close      Yahoo "Close": split-adjusted, NOT dividend-adjusted. Matches quoted prices; use for price charts.
#   adj_close  Yahoo "Adj Close": split- and dividend-adjusted. Use for total-return analysis.
# Files older than PRICE_MAX_AGE are re-downloaded on the next load; the sidebar "Refresh data" button
# forces it. If Yahoo fails, the saved files are shown with a notice.
PRICE_YEARS = 10  # matches the longest lookback option in panels.LOOKBACK_OPTIONS
PRICE_FILES = {
    "close": Path(__file__).parent / "cache" / "sp500_close.parquet",
    "adj_close": Path(__file__).parent / "cache" / "sp500_adj_close.parquet",
}
PRICE_MAX_AGE = dt.timedelta(days=1)
_PRICE_FIELDS = {"close": "Close", "adj_close": "Adj Close"}  # kind -> Yahoo field
_PRICE_BATCH = 100  # tickers per yf.download call; smaller batches are gentler on Yahoo's rate limits
_MIN_PRICE_TICKERS = 400  # a download with fewer tickers is treated as broken and never saved over the files
_RETRY_AFTER = dt.timedelta(minutes=15)  # after a failed download, wait this long before trying again
US_EASTERN = ZoneInfo("America/New_York")  # the exchange clock that decides when a daily bar is final
_US_CLOSE = dt.time(16, 0)

# Process-level state (like _LAST_GOOD): survives Streamlit reruns. The lock stops two sessions downloading at once.
_price_lock = threading.Lock()
_price_state: dict = {"force": False, "failed_at": None}


def _prices_current() -> bool:
    if not all(p.exists() for p in PRICE_FILES.values()):
        return False
    saved = min(p.stat().st_mtime for p in PRICE_FILES.values())
    return dt.datetime.now() - dt.datetime.fromtimestamp(saved) <= PRICE_MAX_AGE


def _drop_open_bar(prices: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Drop today's row while the US market is still open. yfinance returns the running intraday price as
    that day's "Close", so keeping it would label an unfinished price as a close (early-close days are
    treated the same way, which only costs a few hours of freshness)."""
    now = now if now is not None else pd.Timestamp.now(tz=US_EASTERN)
    if now.time() >= _US_CLOSE:
        return prices
    return prices.loc[prices.index < now.normalize().tz_localize(None)]


def _download_prices(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """PRICE_YEARS of daily closes for `tickers`, as {kind: dates x tickers}. Tickers Yahoo returns no
    prices for are left out (see price_gaps). Today's unfinished bar is dropped (see _drop_open_bar)."""
    parts: dict[str, list[pd.DataFrame]] = {kind: [] for kind in _PRICE_FIELDS}
    for i in range(0, len(tickers), _PRICE_BATCH):
        raw = yf.download(tickers[i:i + _PRICE_BATCH], period=f"{PRICE_YEARS}y", interval="1d",
                          auto_adjust=False, progress=False, threads=True)
        if raw.empty:
            continue
        for kind, field in _PRICE_FIELDS.items():
            parts[kind].append(raw[field])
    if not parts["close"]:
        raise ValueError("Yahoo returned no prices")
    frames = {kind: _drop_open_bar(pd.concat(p, axis=1).dropna(axis=1, how="all").sort_index()
                                   .rename_axis(columns=None))
              for kind, p in parts.items()}
    keep = frames["close"].columns.intersection(frames["adj_close"].columns)  # same tickers in both files
    return {kind: df[keep] for kind, df in frames.items()}


def _download_and_save() -> None:
    universe, _ = load_universe()
    if universe.empty:
        raise ValueError("no S&P 500 list to download prices for")
    frames = _download_prices(universe["ticker"].tolist())
    if frames["close"].shape[1] < _MIN_PRICE_TICKERS:
        raise ValueError(f"only {frames['close'].shape[1]} tickers returned prices")
    PRICE_FILES["close"].parent.mkdir(parents=True, exist_ok=True)
    tmp = {kind: path.with_suffix(".tmp") for kind, path in PRICE_FILES.items()}
    for kind, df in frames.items():  # write both before replacing either, so the two files stay in step
        df.to_parquet(tmp[kind])
    for kind, path in PRICE_FILES.items():
        tmp[kind].replace(path)


def _ensure_saved(state: dict, lock: threading.Lock, is_current: Callable[[], bool],
                  download: Callable[[], None], what: str, spinner: str) -> bool:
    """Run `download` when the saved files are due (or a refresh was forced). True if they are current afterwards;
    False if a download was due but failed (the caller then shows whatever is saved, with a notice)."""
    with lock:  # a second session waits here, then finds the files current
        force, failed_at = state["force"], state["failed_at"]
        if not force and is_current():
            return True
        if not force and failed_at and dt.datetime.now() - failed_at < _RETRY_AFTER:
            return False  # recent failure: do not block every rerun on a slow retry
        try:
            with st.spinner(spinner):
                download()
        except Exception as exc:  # network, rate limit, too few tickers: fall back to the saved files
            log.warning("%s download failed: %s", what, exc)
            state.update(force=False, failed_at=dt.datetime.now())
            return False
        state.update(force=False, failed_at=None)
        return True


def _ensure_prices() -> bool:
    return _ensure_saved(_price_state, _price_lock, _prices_current, _download_and_save, "Price",
                         f"Downloading {PRICE_YEARS}Y of daily prices for the S&P 500 "
                         "(Yahoo Finance; the first run takes a minute or two)…")


@st.cache_data
def _read_prices(kind: str, saved_at: float) -> pd.DataFrame:
    """Read one saved price file; `saved_at` (its mtime) is part of the cache key so a re-download is picked up."""
    return pd.read_parquet(PRICE_FILES[kind])


def load_prices(kind: str = "close") -> tuple[pd.DataFrame, str | None]:
    """Daily prices (dates x tickers, PRICE_YEARS of history) for the S&P 500; never raises. `kind` is
    "close" (split-adjusted) or "adj_close" (split- and dividend-adjusted). Returns (prices, notice); notice
    is None unless a due download failed, then it says what is shown instead (DESIGN.md 5.4)."""
    if kind not in PRICE_FILES:
        raise ValueError(f"kind must be one of {tuple(PRICE_FILES)}, got {kind!r}")
    ok = _ensure_prices()
    path = PRICE_FILES[kind]
    try:
        prices = _read_prices(kind, path.stat().st_mtime)
    except Exception as exc:  # no saved file yet, or unreadable
        log.warning("Saved prices unusable (%s): %s", kind, exc)
        return pd.DataFrame(), "Price download failed; no saved prices available."
    if ok:
        return prices, None
    return prices, ("Price download failed; showing saved prices "
                    f"(latest observation {prices.index[-1]:%d %b %Y}).")


def prices_window(prices: pd.DataFrame, years: int) -> pd.DataFrame:
    """Slice prices to the lookback window, the same way fred_data does (`years` x 365 days back from now)."""
    if prices.empty:  # load_prices returns an empty frame when nothing is saved
        return prices
    return prices.loc[prices.index >= pd.Timestamp.now() - pd.Timedelta(days=365 * years)]


def price_gaps(prices: pd.DataFrame, universe: pd.DataFrame) -> list[str]:
    """Universe tickers Yahoo returned no prices for, for the panel caveat."""
    return sorted(set(universe["ticker"]) - set(prices.columns))


# ---------- Equity share counts: yfinance, saved locally ----------

# Market-cap weights need shares outstanding, per share class. Do NOT use yfinance's `marketCap`: for a
# dual-class company (Alphabet GOOGL/GOOG, Fox, News Corp) each class reports the WHOLE company's cap, so
# summing them double-counts. `sharesOutstanding` is per class. Counts change slowly, so they refresh weekly;
# the "Refresh data" button does not force them (the download takes a few minutes).
SHARES_FILE = Path(__file__).parent / "cache" / "sp500_shares.csv"
SHARES_MAX_AGE = dt.timedelta(days=7)
_SHARES_WORKERS = 6  # parallel Yahoo requests; more risks rate limiting
_MIN_SHARES_TICKERS = 400  # fewer than this is treated as a broken download and never saved over the file
_shares_lock = threading.Lock()
_shares_state: dict = {"force": False, "failed_at": None}


def _shares_current() -> bool:
    return SHARES_FILE.exists() and dt.datetime.now() - dt.datetime.fromtimestamp(SHARES_FILE.stat().st_mtime) <= SHARES_MAX_AGE


def _fetch_shares(ticker: str) -> float | None:
    """Shares outstanding for one ticker; None when Yahoo has none. One retry after an error (rate limit)."""
    for attempt in range(2):
        try:
            n = yf.Ticker(ticker).info.get("sharesOutstanding")
            return float(n) if n and n > 0 else None
        except Exception:
            if attempt == 0:
                time.sleep(1.5)
    return None


def _download_and_save_shares() -> None:
    universe, _ = load_universe()
    if universe.empty:
        raise ValueError("no S&P 500 list to download share counts for")
    tickers = universe["ticker"].tolist()
    with ThreadPoolExecutor(max_workers=_SHARES_WORKERS) as pool:
        counts = list(pool.map(_fetch_shares, tickers))
    shares = pd.Series(counts, index=pd.Index(tickers, name="ticker"), name="shares", dtype=float).dropna()
    if len(shares) < _MIN_SHARES_TICKERS:
        raise ValueError(f"only {len(shares)} tickers returned share counts")
    SHARES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHARES_FILE.with_suffix(".tmp")
    shares.to_csv(tmp)
    tmp.replace(SHARES_FILE)


@st.cache_data
def _read_shares(saved_at: float) -> pd.Series:
    """Read the saved share counts; `saved_at` (the file's mtime) is part of the cache key."""
    return pd.read_csv(SHARES_FILE, index_col="ticker")["shares"]


def load_shares() -> tuple[pd.Series, str | None]:
    """Shares outstanding by ticker (per share class); never raises. Returns (shares, notice); notice is None
    unless a due download failed, then it says what is shown instead (DESIGN.md 5.4)."""
    ok = _ensure_saved(_shares_state, _shares_lock, _shares_current, _download_and_save_shares, "Share count",
                       "Downloading share counts for the S&P 500 (Yahoo Finance; the first run takes a few minutes)…")
    try:
        shares = _read_shares(SHARES_FILE.stat().st_mtime)
    except Exception as exc:  # no saved file yet, or unreadable
        log.warning("Saved share counts unusable: %s", exc)
        return pd.Series(dtype=float), "Share count download failed; no saved share counts available."
    if ok:
        return shares, None
    saved = dt.datetime.fromtimestamp(SHARES_FILE.stat().st_mtime)
    return shares, f"Share count download failed; showing saved share counts from {saved:%d %b %Y}."


@st.cache_data(ttl=3600)
def refreshed_at() -> pd.Timestamp:
    """SGT wall-clock time this cache generation was created; resets when the cache is cleared."""
    return pd.Timestamp.now(tz=SGT)


def refresh() -> None:
    st.cache_data.clear()
    _price_state["force"] = True  # the next price load re-downloads, whatever the age of the saved files


def moving_average(data: pd.Series, window: int) -> pd.Series:
    """Rolling mean over the last `window` valid observations of the series'
    own frequency (e.g. window=3 is 3 months for a monthly series, 3 quarters
    for a quarterly one).

    Gaps in the source data are skipped rather than propagated: pandas'
    .rolling() requires all `window` periods to be non-null by default, so a
    single missing month (FRED series do have these, e.g. a delayed release)
    would otherwise poison every window that includes it and leave the MA
    stuck at NaN for the next `window` periods even once fresh data arrives."""
    return data.dropna().rolling(window=window).mean()


def with_moving_average(label: str, data: pd.Series, window: int | None) -> dict:
    """Build a {label: series} dict for line_fig, adding a
    '<label> (N-period MA)' series alongside the raw one when a window is set."""
    series = {label: data}
    if window:
        series[f"{label} ({window}-period MA)"] = moving_average(data, window)
    return series


# Maturity (in years) for each tenor used to build the yield curve. Fed funds
# is treated as ~overnight (0Y). Keys must match the series labels in specs.TREASURY_SERIES.
YIELD_CURVE_TENORS = [
    ("Fed funds", 0.0),
    ("Treasury 2Y", 2.0),
    ("Treasury 10Y", 10.0),
    ("Treasury 30Y", 30.0),
]


def maturity_from_range_label(label: str) -> float:
    """Extract a maturity in years from a range label by taking its last number,
    e.g. '1-3 Years' -> 3.0, '15+ Years' -> 15.0."""
    digits = re.findall(r"\d+", label)
    if not digits:
        raise ValueError(f"no year number found in range label {label!r}")
    return float(digits[-1])


def yield_curve_as_of(
    curve_series: dict, as_of: pd.Timestamp, tenors: list[tuple[str, float]] = YIELD_CURVE_TENORS
) -> pd.Series:
    """For each tenor present in curve_series, pick the most recent observation
    at or before `as_of`. Returns a Series indexed by maturity in years."""
    values = {}
    for label, maturity in tenors:
        s = curve_series.get(label)
        if s is None or s.empty:
            continue
        val = s.sort_index().asof(as_of)
        if pd.notna(val):
            values[maturity] = val
    return pd.Series(values, dtype=float)
