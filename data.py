"""Data layer: FRED loaders, caching and small series helpers (DESIGN.md sections 9.3 and 11 step 3).

Pages and panels never call the API directly; they go through load_series so a failed fetch degrades
to the last good copy with a notice instead of a traceback (DESIGN.md 5.4).
"""
from __future__ import annotations

import datetime as dt
import io
import json
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

import cape
import countries
import valuation

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


# ---------- Country index closes: yfinance, saved locally ----------

# One benchmark index per country (see countries.COUNTRIES). Same save-and-fall-back pattern as the stock prices.
# All indices are required: a download missing one is treated as broken and never saved over the file.
INDEX_FILE = Path(__file__).parent / "cache" / "country_index_close.parquet"
_index_lock = threading.Lock()
_index_state: dict = {"force": False, "failed_at": None}


def _index_current() -> bool:
    return INDEX_FILE.exists() and dt.datetime.now() - dt.datetime.fromtimestamp(INDEX_FILE.stat().st_mtime) <= PRICE_MAX_AGE


def _download_and_save_index() -> None:
    raw = yf.download(list(countries.TICKERS), period=f"{PRICE_YEARS}y", interval="1d", auto_adjust=False,
                      progress=False, threads=True)
    close = raw["Close"].dropna(axis=1, how="all").sort_index().rename_axis(columns=None) if not raw.empty else raw
    if missing := sorted(set(countries.TICKERS) - set(close.columns)):
        raise ValueError(f"Yahoo returned no prices for {', '.join(missing)}")
    frame = countries.drop_open_bars(countries.to_names(close))
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_FILE.with_suffix(".tmp")
    frame.to_parquet(tmp)
    tmp.replace(INDEX_FILE)


@st.cache_data
def _read_index(saved_at: float) -> pd.DataFrame:
    """Read the saved index closes; `saved_at` (the file's mtime) is part of the cache key."""
    return pd.read_parquet(INDEX_FILE)


def load_index_prices() -> tuple[pd.DataFrame, str | None]:
    """Daily index closes (dates x country names, PRICE_YEARS of history); never raises. Returns (prices, notice);
    notice is None unless a due download failed, then it says what is shown instead (DESIGN.md 5.4)."""
    ok = _ensure_saved(_index_state, _index_lock, _index_current, _download_and_save_index, "Country index",
                       "Downloading daily closes for the country indices (Yahoo Finance)…")
    try:
        prices = _read_index(INDEX_FILE.stat().st_mtime)
    except Exception as exc:  # no saved file yet, or unreadable
        log.warning("Saved country index prices unusable: %s", exc)
        return pd.DataFrame(), "Country index download failed; no saved prices available."
    if ok:
        return prices, None
    return prices, f"Country index download failed; showing saved prices (latest observation {prices.index[-1]:%d %b %Y})."


# ---------- Quarterly EPS for the forward P/E: yfinance, saved per ticker ----------

# Reported EPS, current estimates and report lag for each stock, saved to cache/eps/<ticker>.json and reused for a
# week. A single stock is fetched on demand (forward P/E history chart); the sector and S&P 500 comparison needs
# every constituent, so a bulk download fills all of them once, marked by cache/eps/_bulk.json (which stocks
# failed). A failed fetch falls back to the saved file, like the price loaders. Like share counts, "Refresh data"
# does not force EPS (the bulk download takes a few minutes); it refreshes weekly. Needs lxml.
EPS_DIR = Path(__file__).parent / "cache" / "eps"
EPS_BULK_FILE = EPS_DIR / "_bulk.json"
EPS_MAX_AGE = dt.timedelta(days=7)
_EPS_HISTORY = 48                      # quarters of reported EPS to request (12Y)
_EPS_ESTIMATE_KEYS = ("0q", "+1q", "0y", "+1y")
_EPS_WORKERS = 6                       # parallel Yahoo requests; more risks rate limiting
_MIN_EPS_TICKERS = 400                 # fewer successes than this is treated as a broken bulk download
_eps_lock = threading.Lock()
_eps_state: dict = {"force": False, "failed_at": None}


def _safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker)


def _eps_path(ticker: str) -> Path:
    """Added (non-S&P 500) tickers keep their EPS apart from the constituents', so the bulk files stay S&P-only."""
    return (CUSTOM_EPS_DIR if ticker in added_tickers() else EPS_DIR) / f"{_safe_name(ticker)}.json"


def _report_lag_days(ticker: yf.Ticker, report_dates: pd.DatetimeIndex) -> int:
    """Median days from fiscal quarter end to report date, from the recent quarters Yahoo lists statements for."""
    try:
        ends = pd.DatetimeIndex(ticker.quarterly_income_stmt.columns).normalize()
    except Exception:
        return 35
    lags = [(report_dates[report_dates >= e][0].normalize() - e).days for e in ends if (report_dates >= e).any()]
    lags = [lag for lag in lags if 10 <= lag <= 100]
    return int(pd.Series(lags).median()) if lags else 35


def _fetch_eps(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    dates = t.get_earnings_dates(limit=_EPS_HISTORY)
    if dates is None or dates.empty:
        raise ValueError("no earnings history")
    dates = dates.sort_index()
    dates.index = dates.index.tz_localize(None) if dates.index.tz is None else dates.index.tz_convert(None)
    reported = dates["Reported EPS"].dropna()
    est_table = t.earnings_estimate
    estimates = {}
    if est_table is not None and not est_table.empty:
        estimates = {k: float(est_table.loc[k, "avg"]) for k in _EPS_ESTIMATE_KEYS
                     if k in est_table.index and pd.notna(est_table.loc[k, "avg"])}
    return {
        "reported": {d.strftime("%Y-%m-%d"): float(v) for d, v in reported.items()},
        "estimates": estimates, "lag_days": _report_lag_days(t, dates.index),
        "fetched": dt.datetime.now().isoformat(timespec="seconds"),
    }


def _fetch_and_save_eps(ticker: str) -> dict:
    """Fetch one stock's EPS data (one retry, for Yahoo rate limits) and save it. Raises if both attempts fail."""
    for attempt in range(2):
        try:
            eps = _fetch_eps(ticker)
            break
        except Exception:
            if attempt == 1:
                raise
            time.sleep(1.5)
    path = _eps_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(eps), encoding="utf-8")
    for attempt in range(4):  # Windows can briefly lock the target (virus scanner, another reader)
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.3)
    return eps


def _read_eps(ticker: str) -> dict | None:
    try:
        return json.loads(_eps_path(ticker).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@st.cache_data(ttl=3600)
def _load_eps_cached(ticker: str) -> tuple[dict | None, str | None]:
    path = _eps_path(ticker)
    if path.exists() and dt.datetime.now() - dt.datetime.fromtimestamp(path.stat().st_mtime) <= EPS_MAX_AGE:
        return _read_eps(ticker), None
    try:
        return _fetch_and_save_eps(ticker), None
    except Exception as exc:
        log.warning("EPS fetch failed for %s: %s", ticker, exc)
    saved = _read_eps(ticker)
    if saved is None:
        return None, f"Fetch failed for {ticker} EPS; no saved data available."
    return saved, f"Fetch failed for {ticker} EPS; showing saved data from {saved['fetched'][:10]}."


def load_eps(ticker: str) -> tuple[pd.Series, dict[str, float], int, str | None]:
    """Reported quarterly EPS (indexed by report date), Yahoo's current estimates, the report lag in days and a
    notice; never raises. Empty series when nothing is available. Notice is None on success (DESIGN.md 5.4)."""
    eps, notice = _load_eps_cached(ticker)
    if not eps or not eps["reported"]:
        return pd.Series(dtype=float), {}, 35, notice or f"Yahoo has no reported EPS for {ticker}."
    reported = pd.Series(eps["reported"]).rename(lambda d: pd.Timestamp(d)).sort_index()
    return reported, eps["estimates"], int(eps["lag_days"]), notice


def _eps_bulk_current() -> bool:
    return EPS_BULK_FILE.exists() and \
        dt.datetime.now() - dt.datetime.fromtimestamp(EPS_BULK_FILE.stat().st_mtime) <= EPS_MAX_AGE


def _download_and_save_eps_all() -> None:
    universe, _ = load_universe()
    if universe.empty:
        raise ValueError("no S&P 500 list to download EPS for")

    def fetch(ticker: str) -> tuple[str, bool]:
        try:
            _fetch_and_save_eps(ticker)
            return ticker, True
        except Exception as exc:
            log.warning("EPS fetch failed for %s: %s", ticker, exc)
            return ticker, False

    with ThreadPoolExecutor(max_workers=_EPS_WORKERS) as pool:
        results = list(pool.map(fetch, universe["ticker"].tolist()))
    failed = sorted(t for t, ok in results if not ok)
    if len(results) - len(failed) < _MIN_EPS_TICKERS:
        raise ValueError(f"only {len(results) - len(failed)} tickers returned EPS")
    tmp = EPS_BULK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"failed": failed}), encoding="utf-8")
    tmp.replace(EPS_BULK_FILE)


@st.cache_data
def _build_ntm_eps(bulk_saved_at: float, price_saved_at: float) -> pd.DataFrame:
    """NTM EPS on every price date for every stock with saved EPS (dates x tickers). The two timestamps are the
    cache key, so a new bulk download or new prices rebuild it."""
    prices = _read_prices("close", price_saved_at)
    columns = {}
    for path in EPS_DIR.glob("*.json"):
        ticker = path.stem
        if path.name.startswith("_") or ticker not in prices.columns:
            continue
        raw = _read_eps(ticker)
        if not raw or not raw["reported"]:
            continue
        reported = pd.Series(raw["reported"]).rename(lambda d: pd.Timestamp(d)).sort_index()
        quarters = valuation.build_quarters(reported, raw["estimates"], int(raw["lag_days"]))
        if not quarters.empty:
            columns[ticker] = valuation.ntm_eps(quarters, prices.index)
    return pd.DataFrame(columns, index=prices.index)


def load_ntm_eps() -> tuple[pd.DataFrame, list[str], str | None]:
    """NTM EPS for every S&P 500 stock (dates x tickers), the tickers Yahoo had no EPS for, and a notice; never
    raises. Empty frame when no bulk download has ever succeeded (a partial set of saved stocks would make a
    sector average misleading). Notice is None unless a due download failed (DESIGN.md 5.4)."""
    ok = _ensure_saved(_eps_state, _eps_lock, _eps_bulk_current, _download_and_save_eps_all, "EPS",
                       "Downloading quarterly EPS for the S&P 500 (Yahoo Finance; the first run takes a few "
                       "minutes)…")
    try:
        marker = json.loads(EPS_BULK_FILE.read_text(encoding="utf-8"))
        eps = _build_ntm_eps(EPS_BULK_FILE.stat().st_mtime, PRICE_FILES["close"].stat().st_mtime)
    except Exception as exc:  # no bulk download yet, or unreadable
        log.warning("Saved EPS unusable: %s", exc)
        return pd.DataFrame(), [], "EPS download failed; no saved EPS available."
    if ok:
        return eps, marker["failed"], None
    saved = dt.datetime.fromtimestamp(EPS_BULK_FILE.stat().st_mtime)
    return eps, marker["failed"], f"EPS download failed; showing saved EPS from {saved:%d %b %Y}."


# ---------- Added tickers: any Yahoo ticker outside the S&P 500, saved locally ----------

# The default universe is the S&P 500. Any other Yahoo Finance ticker (D05.SI, 7203.T, ASML.AS) typed into an Equities
# search box is downloaded once, saved under cache/custom/ and offered in every stock box from then on. The registry
# (tickers.json) holds each ticker's name, trading currency, the currency of its financial statements, exchange time
# zone and shares outstanding (refreshed weekly). Prices are one parquet per ticker with `close` and `adj_close`, 10Y
# daily like the S&P files, in the listing's own currency, refreshed when over a day old ("Refresh data" forces it).
CUSTOM_DIR = Path(__file__).parent / "cache" / "custom"
CUSTOM_REGISTRY = CUSTOM_DIR / "tickers.json"
CUSTOM_PRICE_DIR = CUSTOM_DIR / "prices"
CUSTOM_EPS_DIR = CUSTOM_DIR / "eps"
CUSTOM_META_MAX_AGE = dt.timedelta(days=7)
_LOCAL_CLOSE = dt.time(18, 0)  # non-US exchanges: local time after which today's bar is final (all major ones close earlier)
_TICKER_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^&]{0,19}$")
_MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}  # quoted in cents
_custom_lock = threading.Lock()
_custom_state: dict = {"force": False, "failed_at": None}
_registry_memo: dict = {"mtime": None, "data": {}}


def added_tickers() -> dict[str, dict]:
    """Registry of added tickers: {ticker: metadata}. Read-only; memoised on the file's mtime. Not st.cache_data,
    because the EPS bulk download calls it from worker threads."""
    try:
        mtime = CUSTOM_REGISTRY.stat().st_mtime
    except OSError:
        return {}
    if _registry_memo["mtime"] != mtime:
        try:
            _registry_memo.update(mtime=mtime, data=json.loads(CUSTOM_REGISTRY.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            log.warning("Added-ticker registry unreadable: %s", exc)
            return {}
    return _registry_memo["data"]


def _write_atomic(path: Path, write: Callable[[Path], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    write(tmp)
    for attempt in range(4):  # Windows can briefly lock the target (virus scanner, another reader)
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.3)


def _write_registry(registry: dict[str, dict]) -> None:
    _write_atomic(CUSTOM_REGISTRY, lambda p: p.write_text(json.dumps(registry, indent=1, sort_keys=True),
                                                          encoding="utf-8"))


def ticker_currency(ticker: str) -> str:
    """Trading currency: USD for S&P 500 stocks, Yahoo's quote currency for added ones ("?" when unknown)."""
    meta = added_tickers().get(ticker)
    return (meta.get("currency") or "?") if meta else "USD"


def major_currency(currency: str | None) -> tuple[str | None, int]:
    """(major currency, quote units per major unit): ("GBP", 100) for prices quoted in pence (GBp), else (currency, 1)."""
    return _MINOR_UNITS.get(currency, (currency, 1)) if currency else (None, 1)


def ticker_tz(ticker: str) -> str:
    """Exchange time zone: New York for S&P 500 stocks, Yahoo's exchange time zone for added ones."""
    return (added_tickers().get(ticker) or {}).get("tz") or "America/New_York"


def normalise_ticker(text: str, known: set[str] = frozenset()) -> str | None:
    """Typed text -> Yahoo ticker, or None when it cannot be one. Upper-cases, takes the ticker from a pasted
    "AAPL — Apple Inc." label, and maps BRK.B to the S&P list's BRK-B form when that is a constituent."""
    t = str(text).split(" — ")[0].strip().upper()
    if not _TICKER_RE.match(t):
        return None
    dashed = t.replace(".", "-")
    return dashed if dashed != t and dashed in known else t


def _fetch_meta(ticker: str) -> dict:
    """Name, currencies, time zone and share count from Yahoo; falls back to fast_info when `info` fails."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        log.warning("Yahoo info failed for %s: %s", ticker, exc)
        info = {}
    if not info.get("currency"):
        try:
            fast = yf.Ticker(ticker).fast_info
            info = {"currency": fast.get("currency"), "exchangeTimezoneName": fast.get("timezone"), **info}
        except Exception as exc:
            log.warning("Yahoo fast_info failed for %s: %s", ticker, exc)
    shares = info.get("sharesOutstanding")
    return {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "currency": info.get("currency"), "financial_currency": info.get("financialCurrency"),
        "tz": info.get("exchangeTimezoneName"), "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "shares": float(shares) if shares and shares > 0 else None,
        "meta_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def _drop_open_local(frame: pd.DataFrame, tz: str | None, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """_drop_open_bar on the listing's own exchange clock: US listings final at 16:00 New York, others at 18:00 local."""
    tz = tz or "America/New_York"
    local = (now if now is not None else pd.Timestamp.now(tz="UTC")).tz_convert(ZoneInfo(tz))
    if local.time() >= (_US_CLOSE if tz.startswith("America/") else _LOCAL_CLOSE):
        return frame
    return frame.loc[frame.index < local.normalize().tz_localize(None)]


def _download_added_prices(tz_of: dict[str, str | None]) -> dict[str, pd.DataFrame]:
    """{ticker: dates x (close, adj_close)} for the tickers Yahoo returned prices for; one Yahoo call for all."""
    raw = yf.download(list(tz_of), period=f"{PRICE_YEARS}y", interval="1d", auto_adjust=False, progress=False,
                      threads=True)
    if raw.empty:
        return {}
    if raw.index.tz is not None:
        raw.index = raw.index.tz_localize(None)
    out = {}
    for t, tz in tz_of.items():
        try:
            frame = pd.DataFrame({kind: raw[field][t] for kind, field in _PRICE_FIELDS.items()})
        except KeyError:
            continue
        frame = _drop_open_local(frame.dropna(how="all").sort_index(), tz)
        if not frame["close"].dropna().empty:
            out[t] = frame
    return out


def _added_price_path(ticker: str) -> Path:
    return CUSTOM_PRICE_DIR / f"{_safe_name(ticker)}.parquet"


def _save_added_prices(frames: dict[str, pd.DataFrame]) -> None:
    for t, frame in frames.items():
        _write_atomic(_added_price_path(t), frame.to_parquet)


def add_ticker(text: str) -> tuple[str | None, str | None]:
    """Resolve a typed ticker; never raises. An S&P 500 or already-added ticker returns at once, with no download.
    Otherwise its metadata and 10Y of prices are downloaded and saved. Returns (ticker, None) or (None, message)."""
    universe, _ = load_universe()
    known = set(universe["ticker"])
    t = normalise_ticker(text, known)
    if t is None:
        return None, (f"“{str(text).strip()}” is not a Yahoo Finance ticker. Non-US listings need the exchange "
                      "suffix, e.g. D05.SI (SGX), 7203.T (Tokyo), 0700.HK (Hong Kong), ASML.AS (Amsterdam).")
    if t in known or t in added_tickers():
        return t, None
    with _custom_lock:
        try:
            meta = _fetch_meta(t)
            frames = _download_added_prices({t: meta["tz"]})
        except Exception as exc:  # network, rate limit, unknown ticker
            log.warning("Adding %s failed: %s", t, exc)
            frames = {}
        if t not in frames:
            return None, (f"Yahoo Finance has no prices for {t}. Check the ticker and its exchange suffix "
                          "(e.g. D05.SI for DBS on SGX).")
        _save_added_prices(frames)
        _write_registry({**added_tickers(), t: meta})
    return t, None


def _added_due(force: bool) -> list[str]:
    """Added tickers whose saved prices are missing, over a day old, or all of them when a refresh is forced."""
    due = []
    for t in added_tickers():
        path = _added_price_path(t)
        if force or not path.exists() or \
                dt.datetime.now() - dt.datetime.fromtimestamp(path.stat().st_mtime) > PRICE_MAX_AGE:
            due.append(t)
    return due


def _refresh_added() -> None:
    """Re-download due prices (and week-old metadata). Saves whatever succeeded, then raises if any ticker failed,
    so _ensure_saved records the failure and the panels show the saved prices with a notice."""
    registry = dict(added_tickers())
    due = _added_due(_custom_state["force"])
    for t in due:
        if dt.datetime.now() - dt.datetime.fromisoformat(registry[t]["meta_at"]) > CUSTOM_META_MAX_AGE:
            fresh = _fetch_meta(t)
            registry[t] = fresh if fresh["currency"] else {**registry[t], "meta_at": fresh["meta_at"]}
    frames = _download_added_prices({t: registry[t].get("tz") for t in due})
    _save_added_prices(frames)
    _write_registry(registry)
    if failed := sorted(set(due) - set(frames)):
        raise ValueError(f"no prices for {', '.join(failed)}")


def _added_current() -> bool:
    return not _added_due(False)


@st.cache_data
def _read_added(kind: str, stamps: tuple[tuple[str, float], ...]) -> pd.DataFrame:
    """Saved prices of every added ticker (dates x tickers); `stamps` (ticker, mtime) is the cache key."""
    cols = {t: pd.read_parquet(_added_price_path(t))[kind] for t, _ in stamps}
    return pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()


def load_added_prices(kind: str = "close") -> tuple[pd.DataFrame, str | None]:
    """Daily prices of the added tickers (dates x tickers, local currency); never raises. Empty when none were added.
    Notice is None unless a due download failed, then it says what is shown instead (DESIGN.md 5.4)."""
    if kind not in _PRICE_FIELDS:
        raise ValueError(f"kind must be one of {tuple(_PRICE_FIELDS)}, got {kind!r}")
    if not added_tickers():
        return pd.DataFrame(), None
    ok = _ensure_saved(_custom_state, _custom_lock, _added_current, _refresh_added, "Added-ticker price",
                       "Updating prices for the added tickers (Yahoo Finance)…")
    stamps = tuple((t, p.stat().st_mtime) for t in sorted(added_tickers()) if (p := _added_price_path(t)).exists())
    try:
        prices = _read_added(kind, stamps)
    except Exception as exc:
        log.warning("Saved added-ticker prices unusable: %s", exc)
        return pd.DataFrame(), "Price download failed for the added tickers; no saved prices available."
    if ok:
        return prices, None
    return prices, "Price update failed for some added tickers; showing their saved prices."


def load_stock_prices(kind: str = "close") -> tuple[pd.DataFrame, list[str]]:
    """S&P 500 prices joined with the added tickers' (dates x tickers), and any fallback notices. The date index is
    the union of every market's trading days, so take a stock's own series with .dropna(). S&P-wide aggregates
    (sector and index P/E) must keep using load_prices, whose rows are all US trading days."""
    sp, sp_notice = load_prices(kind)
    added, added_notice = load_added_prices(kind)
    notices = [n for n in (sp_notice, added_notice) if n]
    added = added.drop(columns=added.columns.intersection(sp.columns))  # a ticker that has since joined the S&P 500
    if added.empty:
        return sp, notices
    return pd.concat([sp, added], axis=1).sort_index(), notices


@st.cache_data(ttl=86400)
def fx_to_usd(currency: str | None) -> float | None:
    """USD per one unit of `currency` (cents-quoted currencies such as GBp included), from Yahoo's latest FX close.
    None when the rate cannot be fetched."""
    if not currency or currency == "?":
        return None
    base, divisor = major_currency(currency)
    base = base.upper()
    if base == "USD":
        return 1.0 / divisor
    try:
        raw = yf.download(f"{base}USD=X", period="10d", interval="1d", auto_adjust=False, progress=False)
        rate = float(raw["Close"].dropna().iloc[-1].squeeze())
    except Exception as exc:
        log.warning("FX fetch failed for %s: %s", currency, exc)
        return None
    return rate / divisor if rate > 0 else None


# ---------- Listed options: Yahoo option chains, fetched on demand ----------

# A snapshot only: Yahoo keeps no history of open interest, and it is not saved to disk (owner's choice). The last
# good chain per request is kept for the server process, as for FRED series, so an outage still shows labelled data.
_OPTION_WORKERS = 4            # parallel chain requests for "All expiries"; more risks rate limiting
_LAST_GOOD_OPTIONS: dict[tuple[str, str], tuple[pd.DataFrame, dict]] = {}


@st.cache_data(ttl=3600)
def option_expiries(ticker: str) -> list[pd.Timestamp]:
    """Listed expiries for `ticker`, nearest first; empty when Yahoo has none (non-US listings) or the fetch fails."""
    try:
        return [pd.Timestamp(e) for e in yf.Ticker(ticker).options]
    except Exception as exc:
        log.warning("Option expiries fetch failed for %s: %s", ticker, exc)
        return []


def _fetch_option_chain(ticker: str, expiry: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """One expiry's chain as tidy rows (expiry, type, strike, oi, iv) and the underlying's quote (spot, currency,
    quote time). Raises on failure."""
    chain = yf.Ticker(ticker).option_chain(f"{expiry:%Y-%m-%d}")
    rows = []
    for kind, frame in (("call", chain.calls), ("put", chain.puts)):
        rows.append(pd.DataFrame({
            "expiry": expiry, "type": kind, "strike": frame["strike"].astype(float),
            "oi": pd.to_numeric(frame["openInterest"], errors="coerce").fillna(0.0),
            "iv": pd.to_numeric(frame["impliedVolatility"], errors="coerce"),
            "bid": pd.to_numeric(frame.get("bid"), errors="coerce"),
            "ask": pd.to_numeric(frame.get("ask"), errors="coerce"),
        }))
    quote = chain.underlying if isinstance(chain.underlying, dict) else {}
    stamp = quote.get("regularMarketTime")
    info = {"spot": quote.get("regularMarketPrice"), "currency": quote.get("currency") or ticker_currency(ticker),
            "quote_time": pd.Timestamp(stamp, unit="s", tz="UTC") if stamp else pd.Timestamp.now(tz="UTC")}
    return pd.concat(rows, ignore_index=True), info


@st.cache_data(ttl=3600)
def _fetch_options(ticker: str, expiries: tuple[pd.Timestamp, ...]) -> tuple[pd.DataFrame, dict]:
    """Chains for `expiries`, fetched in parallel and stacked, with the newest underlying quote. Raises on failure."""
    with ThreadPoolExecutor(max_workers=_OPTION_WORKERS) as pool:
        parts = list(pool.map(lambda e: _fetch_option_chain(ticker, e), expiries))
    info = max((i for _, i in parts), key=lambda i: i["quote_time"])
    if info["spot"] is None:
        raise ValueError("no underlying price in the option chain")
    return pd.concat([frame for frame, _ in parts], ignore_index=True), info


def load_options(ticker: str, expiries: list[pd.Timestamp]) -> tuple[pd.DataFrame, dict, str | None]:
    """Chains for `expiries` stacked into one frame, the underlying's latest quote and a notice; never raises. Falls
    back to the last good copy of the same request with a notice (DESIGN.md 5.4); empty frame when there is none."""
    key = (ticker, ",".join(f"{e:%Y%m%d}" for e in expiries))
    try:
        chain, info = _fetch_options(ticker, tuple(expiries))
        _LAST_GOOD_OPTIONS[key] = (chain, info)
        return chain, info, None
    except Exception as exc:
        log.warning("Option chain fetch failed for %s: %s", ticker, exc)
    if key in _LAST_GOOD_OPTIONS:
        chain, info = _LAST_GOOD_OPTIONS[key]
        return chain, info, (f"Fetch failed for {ticker} options; showing the chain fetched "
                             f"{info['quote_time'].tz_convert(SGT):%d %b %Y %H:%M} SGT.")
    return pd.DataFrame(), {}, f"Fetch failed for {ticker} options; no earlier copy is available."


# Analyst price targets: Yahoo's consensus (mean, median, low, high) and analyst count, one `info` call per stock, on
# demand. Yahoo gives no date per analyst, so some targets inside the consensus may be months old.
_LAST_GOOD_TARGETS: dict[str, dict] = {}
TARGET_FIELDS = {"mean": "targetMeanPrice", "median": "targetMedianPrice", "low": "targetLowPrice",
                 "high": "targetHighPrice"}


@st.cache_data(ttl=6 * 3600)
def _fetch_targets(ticker: str) -> dict:
    """{mean, median, low, high} (only the positive ones), analysts, currency; raises on a failed fetch."""
    info = yf.Ticker(ticker).info or {}
    targets = {k: float(info[f]) for k, f in TARGET_FIELDS.items() if isinstance(info.get(f), (int, float))
               and info[f] > 0}
    return {"targets": targets, "analysts": info.get("numberOfAnalystOpinions"), "currency": info.get("currency")}


def load_price_targets(ticker: str) -> tuple[dict, str | None]:
    """Analyst targets for `ticker` and a notice; never raises. "targets" is empty when Yahoo lists none (ETFs,
    indices, uncovered stocks). Falls back to the last good copy with a notice (DESIGN.md 5.4)."""
    try:
        out = _fetch_targets(ticker)
        _LAST_GOOD_TARGETS[ticker] = out
        return out, None
    except Exception as exc:
        log.warning("Analyst target fetch failed for %s: %s", ticker, exc)
    if ticker in _LAST_GOOD_TARGETS:
        return _LAST_GOOD_TARGETS[ticker], f"Fetch failed for {ticker} analyst targets; showing an earlier copy."
    return {"targets": {}, "analysts": None, "currency": None}, \
        f"Fetch failed for {ticker} analyst targets; the distribution is shown without them."


# ---------- Shiller CAPE data: saved by hand ----------

# Robert J. Shiller's "Irrational Exuberance" monthly data (shillerdata.com). Shiller updates it monthly and his
# download link changes, so it is not fetched: the owner replaces this file every month or two. Parsed by cape.parse.
SHILLER_FILE = Path(__file__).parent / "cache" / "shiller_ie_data.csv"


@st.cache_data
def _read_shiller(saved_at: float) -> pd.DataFrame:
    """Parse the saved Shiller file; `saved_at` (its mtime) is part of the cache key."""
    return cape.parse(SHILLER_FILE.read_text(encoding="utf-8-sig"))


def load_shiller() -> tuple[pd.DataFrame, str | None]:
    """Shiller's monthly data (see cape.parse); never raises. Empty frame and a notice when the file is missing or
    unreadable (DESIGN.md 5.4)."""
    try:
        return _read_shiller(SHILLER_FILE.stat().st_mtime), None
    except FileNotFoundError:
        return pd.DataFrame(), f"Shiller data not found: save ie_data.csv from shillerdata.com as {SHILLER_FILE.name} in cache/."
    except Exception as exc:
        log.warning("Shiller file unreadable: %s", exc)
        return pd.DataFrame(), f"Shiller data in cache/{SHILLER_FILE.name} could not be read ({exc})."


@st.cache_data(ttl=3600)
def refreshed_at() -> pd.Timestamp:
    """SGT wall-clock time this cache generation was created; resets when the cache is cleared."""
    return pd.Timestamp.now(tz=SGT)


def refresh() -> None:
    st.cache_data.clear()
    _price_state["force"] = True  # the next price load re-downloads, whatever the age of the saved files
    _index_state["force"] = True
    _custom_state["force"] = True


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
