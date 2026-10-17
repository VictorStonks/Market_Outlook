"""Config-driven panels (DESIGN.md sections 5.2 and 10): a panel is a PanelSpec, a page is a list of them.

Every panel renders the same anatomy: header (title, unit + frequency, generated takeaway), panel-local
controls, chart, footer (source, frequency, as-of, STALE, caveat), collapsed raw data.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
import streamlit as st

import cape
import correlation
import countries
import data
import options
import sectors
import theme
import valuation
from takeaways import (MAX_AGE_DAYS, ReturnsSummary, Summary, TakeawaySpec, summarise, summarise_cape_fit,
                       summarise_corr_matrix, summarise_options, summarise_smile, summarise_target_prob,
                       summarise_country_returns,
                       summarise_pe_comparison, summarise_pe_screen, summarise_returns, summarise_sector_returns,
                       summarise_sector_table)

FREQ_NAMES = {"D": "Daily", "W": "Weekly", "M": "Monthly", "Q": "Quarterly"}
NO_SUMMARY = "Not enough history in the loaded window for a summary."

# Change window quoted in the takeaway, per frequency. Monthly uses 28 days so the comparison date always
# lands on the previous month's observation (30 days can skip a month around March).
_CHANGE_WINDOW = {"D": (30, "1M"), "W": (7, "1W"), "M": (28, "1M"), "Q": (90, "1Q")}

CURVE_COMPARE = {
    "1 week ago": dt.timedelta(weeks=1),
    "1 month ago": dt.timedelta(days=30),
    "3 months ago": dt.timedelta(days=90),
    "6 months ago": dt.timedelta(days=182),
    "12 months ago": dt.timedelta(days=365),
}
CURVE_COMPARE_DEFAULT = ["1 week ago"]

LOOKBACK_OPTIONS = (1, 3, 5, 10)
MA_OPTIONS = ("Off", 3, 6, 12)


@dataclass(frozen=True)
class PanelSpec:
    key: str                                   # unique widget/chart key
    title: str                                 # sentence case
    subtitle: str                              # unit + frequency + transform: "% YoY · monthly"
    series: dict[str, str]                     # label -> FRED series id
    fred_units: str = "lin"                    # FRED server-side transform (pc1, chg, ...)
    unit: str = "%"                            # axis / value unit: "%", " pp", "k", ""
    decimals: int = 2
    freq: str = "M"                            # D | W | M | Q: frequency of the lead series
    freq_label: str | None = None              # footer override for mixed-frequency panels
    series_freq: dict[str, str] = field(default_factory=dict)   # per-series frequency where it differs
    ordered: bool = False                      # ordered family (credit quality, maturity) -> blue ramp
    selectable: bool = False                   # show a series multiselect (line) / selectbox (band)
    select_label: str = "Series"
    kind: str = "line"                         # line | band | curve
    lead: str | None = None                    # series the takeaway describes; default = first selected
    tenors: tuple[tuple[str, float], ...] = ()  # curve panels: series label -> maturity in years
    recessions: bool = False                   # shade NBER recessions (macro history charts)
    caveat: str | None = None
    takeaway: TakeawaySpec | None = None       # label may contain {series}; freq and change window are derived


# ---------- Page scaffolding and global controls ----------

def page_header(title: str) -> None:
    st.title(title, anchor=False)
    st.caption(f"Refreshed {data.refreshed_at():%d %b %Y %H:%M} SGT")


def _keep_selection(key: str, default) -> None:
    """Segmented controls allow deselecting; a global control must always hold a value."""
    if st.session_state.get(key) is None:
        st.session_state[key] = default


def global_controls() -> None:
    """Sidebar controls that apply to every page (DESIGN.md 4). Call once, in the entrypoint."""
    st.session_state.setdefault("lookback_years", 5)
    st.session_state.setdefault("ma_window", "Off")
    st.segmented_control(
        "Lookback window", LOOKBACK_OPTIONS, key="lookback_years", format_func=lambda y: f"{y}Y",
        on_change=_keep_selection, args=("lookback_years", 5),
    )
    st.segmented_control(
        "Moving average (periods)", MA_OPTIONS, key="ma_window", on_change=_keep_selection,
        args=("ma_window", "Off"),
        help="Drawn on single-series line charts. A period is one observation of that series' own frequency.",
    )
    theme.display_controls()
    if st.button("Refresh data", icon=":material/refresh:", width="stretch"):
        data.refresh()


def global_settings() -> tuple[int, int | None]:
    """(lookback in years, moving-average window or None) from the sidebar controls."""
    ma = st.session_state.get("ma_window", "Off")
    return st.session_state.get("lookback_years", 5), None if ma == "Off" else ma


# ---------- Shared pieces ----------

def _load(spec: PanelSpec, labels: list[str], lookback_years: int) -> tuple[dict[str, pd.Series], list[str]]:
    series, notices = {}, []
    with st.spinner(f"Loading {', '.join(spec.series[label] for label in labels)}…"):
        for label in labels:
            s, notice = data.load_series(spec.series[label], lookback_years, spec.fred_units)
            if notice:
                notices.append(notice)
            if not s.dropna().empty:
                series[label] = s
    return series, notices


def summarise_lead(spec: PanelSpec, series: dict[str, pd.Series]) -> tuple[Summary | None, str | None]:
    """Takeaway summary of the panel's lead series (DESIGN.md 8). Returns (summary, lead label)."""
    if not series:
        return None, None
    lead = spec.lead if spec.lead in series else next(iter(series))
    if not spec.takeaway:
        return None, lead
    freq = spec.series_freq.get(lead, spec.freq)
    days, window = _CHANGE_WINDOW[freq]
    ts = replace(spec.takeaway, label=spec.takeaway.label.format(series=lead), freq=freq,
                 change_days=days, change_label=window)
    return summarise(series[lead], ts), lead


def _asof(sm: Summary | ReturnsSummary | None, series: dict[str, pd.Series]) -> pd.Timestamp | None:
    if sm:
        return sm.asof
    return max((s.dropna().index[-1] for s in series.values()), default=None)


def _takeaway_text(spec: PanelSpec, sm: Summary | None) -> str:
    if not spec.takeaway:
        return ""
    return sm.text if sm else NO_SUMMARY


def _footer(spec: PanelSpec, sm: Summary | None, series: dict[str, pd.Series], caveat: str | None = None) -> None:
    theme.panel_footer(
        "FRED", spec.freq_label or FREQ_NAMES[spec.freq], _asof(sm, series),
        caveat=" ".join(filter(None, [spec.caveat, caveat])) or None, stale=bool(sm and sm.stale),
    )


def _raw_table(df: pd.DataFrame, unit: str, decimals: int):
    """Latest first, dates as 19 Sep 2026, house number format, missing as an em dash."""
    df = df.sort_index(ascending=False)
    df.index = df.index.strftime("%d %b %Y")
    df.index.name = "Date"
    tag = f" ({unit.strip()})" if unit.strip() else ""
    df.columns = [f"{c}{tag}" for c in df.columns]
    return df.style.format(lambda v: theme.fmt_num(v, decimals), na_rep=theme.MISSING)


def _shade_recessions(fig, series: dict[str, pd.Series], lookback_years: int) -> str | None:
    """Add NBER recession shading. Returns a footer note when it could not be drawn."""
    rec, _ = data.load_series("USREC", lookback_years)
    if rec.dropna().empty:
        return "Recession shading unavailable (USREC could not be loaded)."
    index = [s.dropna().index for s in series.values()]
    theme.add_recessions(fig, rec, min(i[0] for i in index), max(i[-1] for i in index))
    return None


def _warn_all(notices: list[str]) -> None:
    for notice in notices:
        st.warning(notice)


# ---------- Panel bodies ----------

def _line_body(spec: PanelSpec, head, lookback_years: int, ma_window: int | None) -> None:
    labels = list(spec.series)
    if spec.selectable:
        labels = st.multiselect(spec.select_label, labels, default=labels, key=f"{spec.key}_sel")
    if not labels:
        with head:
            theme.panel_header(spec.title, spec.subtitle)
        st.info("Select at least one series to plot.")
        return

    series, notices = _load(spec, labels, lookback_years)
    sm, _ = summarise_lead(spec, series)
    with head:
        theme.panel_header(spec.title, spec.subtitle, _takeaway_text(spec, sm))
    _warn_all(notices)
    if not series:
        _footer(spec, sm, series)
        return

    plot, dashed = dict(series), []
    if ma_window and len(series) == 1:
        (label, s), = series.items()
        plot = data.with_moving_average(label, s, ma_window)
        dashed = [name for name in plot if name != label]
    fig = theme.line_fig(plot, ordered=spec.ordered, dashed=dashed, unit=spec.unit, decimals=spec.decimals)
    note = _shade_recessions(fig, series, lookback_years) if spec.recessions else None
    theme.render(fig, key=spec.key)
    _footer(spec, sm, series, note)
    with st.expander("Raw data"):
        st.dataframe(_raw_table(pd.concat(series, axis=1), spec.unit, spec.decimals), width="stretch")


def _band_body(spec: PanelSpec, head, lookback_years: int, ma_window: int | None) -> None:
    label = st.selectbox(spec.select_label, list(spec.series), key=f"{spec.key}_sel")
    series, notices = _load(spec, [label], lookback_years)
    sm, _ = summarise_lead(spec, series)
    with head:
        theme.panel_header(spec.title, spec.subtitle, _takeaway_text(spec, sm))
    _warn_all(notices)
    if not series:
        _footer(spec, sm, series)
        return

    theme.render(theme.band_fig(label, series[label], unit=spec.unit, decimals=spec.decimals), key=spec.key)
    _footer(spec, sm, series)
    with st.expander("Raw data"):
        st.dataframe(_raw_table(series[label].to_frame(label), spec.unit, spec.decimals), width="stretch")


def _curve_body(spec: PanelSpec, head, lookback_years: int, ma_window: int | None) -> None:
    compare = st.multiselect("Compare latest curve to", list(CURVE_COMPARE), default=CURVE_COMPARE_DEFAULT,
                             key=f"{spec.key}_cmp")
    series, notices = _load(spec, list(spec.series), lookback_years)
    sm, _ = summarise_lead(spec, series)
    with head:
        theme.panel_header(spec.title, spec.subtitle, _takeaway_text(spec, sm))
    _warn_all(notices)

    tenors, now = list(spec.tenors), pd.Timestamp.now()
    curves = {"Latest": data.yield_curve_as_of(series, now, tenors)}
    for name in compare:
        curves[name] = data.yield_curve_as_of(series, now - CURVE_COMPARE[name], tenors)
    if curves["Latest"].empty:
        st.info("No curve data available.")
        _footer(spec, sm, series)
        return

    theme.render(theme.curve_fig(curves, unit=spec.unit, decimals=spec.decimals), key=f"{spec.key}_curve")
    missing = [name for name in compare if curves[name].empty]
    if missing:
        st.caption(f"Not enough history in the loaded window for: {', '.join(missing)}. Increase the lookback.")

    change = pd.DataFrame({name: curves["Latest"] - c for name, c in curves.items() if name != "Latest" and not c.empty})
    if change.empty:
        st.info("Select a comparison curve to see the change in yield.")
    else:
        change.index = [f"{m:g}" for m in change.index]  # 2.0 -> "2" for the category axis
        st.caption("Change in yield, latest minus comparison · pp")
        theme.render(theme.bar_fig(change, unit=" pp", decimals=spec.decimals, height=220,
                                   x_title="Maturity (years)"), key=f"{spec.key}_change")
    _footer(spec, sm, series)
    with st.expander("Raw data"):
        if not change.empty:
            st.caption("Change in yield vs latest (pp), by maturity in years")
            st.dataframe(change.rename_axis("Maturity (years)").style.format(
                lambda v: theme.fmt_num(v, spec.decimals), na_rep=theme.MISSING), width="stretch")
        st.caption("Observations by series")
        st.dataframe(_raw_table(pd.concat(series, axis=1), spec.unit, spec.decimals), width="stretch")


_BODIES = {"line": _line_body, "band": _band_body, "curve": _curve_body}


@st.fragment
def render_panel(spec: PanelSpec, lookback_years: int, ma_window: int | None) -> None:
    """One bordered panel. A fragment, so its own controls rerun only this panel (DESIGN.md 9.3)."""
    with st.container(border=True):
        head = st.container()  # filled after the controls so the takeaway can use the loaded data
        _BODIES[spec.kind](spec, head, lookback_years, ma_window)


def render_grid(specs: list[PanelSpec], lookback_years: int, ma_window: int | None) -> None:
    """Two-column grid of panels (DESIGN.md 4)."""
    for i in range(0, len(specs), 2):
        for col, spec in zip(st.columns(2), specs[i:i + 2]):
            with col:
                render_panel(spec, lookback_years, ma_window)


# ---------- Equities: stock panels ----------

MAX_STOCKS = 6                         # categorical palette limit (DESIGN.md 3.2)
DEFAULT_STOCKS = ("AAPL", "MSFT", "NVDA")
STOCK_SOURCE = "Yahoo Finance via yfinance"
STOCK_TAKEAWAY = TakeawaySpec("", unit=" USD", decimals=2, freq="D", change_days=30, change_label="1M")
STOCK_CAVEAT = ("Dividends are not included in the price. Default universe: today's S&P 500 constituents; other "
                "Yahoo tickers can be typed in and are priced in their listing's currency.")
REBASE_TO = 100.0                      # every rebased line starts at this value on the base date
UNIVERSE_NOTE = (":material/info: Default universe: S&P 500 constituents. To add any other stock, type its Yahoo "
                 "Finance ticker into a stock search box (e.g. D05.SI, 7203.T, 0700.HK, ASML.AS); it is saved and "
                 "offered in every stock box on this page. Forward P/E comparisons use S&P 500 averages, so a stock "
                 "outside the S&P 500 is compared with the S&P 500 average.")
SEARCH_HELP = ("Type a ticker or company name. Any other Yahoo Finance ticker can be typed in and added, with its "
               "exchange suffix (e.g. D05.SI for DBS on SGX).")


def _ticker_labels(universe: pd.DataFrame, prices: pd.DataFrame) -> dict[str, str]:
    """{search-box label: ticker} for every S&P 500 or added stock we hold prices for, sorted by ticker."""
    listed = universe[universe["ticker"].isin(prices.columns)]
    ticker_of = dict(zip(listed["label"], listed["ticker"]))
    for t, meta in data.added_tickers().items():
        if t in prices.columns and t not in ticker_of.values():
            ticker_of[f"{t} — {meta.get('name') or t}"] = t
    return dict(sorted(ticker_of.items(), key=lambda kv: kv[1]))


def _stock_options(kind: str, boxes: list[tuple[str, bool]]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str], bool]:
    """Load the S&P 500 list and prices joined with the added tickers (warning on any fallback), and resolve text
    typed into the search boxes `boxes` [(session key, multi-select?)] before they are drawn: a new Yahoo ticker is
    downloaded and saved, and the box's value is rewritten to its label; a failure shows a warning and drops the
    text (a single box goes back to its last stock). Returns (universe, prices, {label: ticker}, ticker added?)."""
    universe, universe_notice = data.load_universe()
    prices, notices = data.load_stock_prices(kind)
    _warn_all([n for n in (universe_notice, *notices) if n])
    ticker_of = _ticker_labels(universe, prices)

    typed: dict[str, str | None] = {}  # typed text -> ticker (None when it failed)
    for key, multi in boxes:
        value = st.session_state.get(key)
        for text in (value or []) if multi else [value]:
            if text is not None and text not in ticker_of and text not in typed:
                with st.spinner(f"Loading {text} from Yahoo Finance…"):
                    typed[text], error = data.add_ticker(text)
                if error:
                    st.warning(error)
    if not typed:
        return universe, prices, ticker_of, False

    added = any(t and t not in ticker_of.values() for t in typed.values())
    if added:
        prices, _ = data.load_stock_prices(kind)
        ticker_of = _ticker_labels(universe, prices)
    label_of = {t: label for label, t in ticker_of.items()}
    for key, multi in boxes:
        value = st.session_state.get(key)
        if multi and value:
            labels = [text if text in ticker_of else label_of.get(typed.get(text)) for text in value]
            st.session_state[key] = list(dict.fromkeys(label for label in labels if label))  # drop failures, repeats
        elif not multi and value is not None and value not in ticker_of:
            st.session_state[key] = label_of.get(typed.get(value)) or st.session_state.get(f"_{key}_good")
    return universe, prices, ticker_of, added


def _window_series(prices: pd.DataFrame, tickers: list[str], lookback_years: int) -> dict[str, pd.Series]:
    """Selected stocks' closes inside the lookback window; stocks with no price in it are left out."""
    window = data.prices_window(prices, lookback_years)
    return {t: s for t in tickers if not (s := window[t].dropna()).empty}


def _stock_lines(prices: pd.DataFrame, series: dict[str, pd.Series], ma_window: int | None) -> tuple[dict, list]:
    """Lines to plot: one per stock, plus a dashed moving average when exactly one stock is shown. The average
    is computed on the full saved history, then cut to the series' start so it begins at the left edge."""
    if not (ma_window and len(series) == 1):
        return dict(series), []
    (t, s), = series.items()
    lines = {name: line.loc[line.index >= s.index[0]]
             for name, line in data.with_moving_average(t, prices[t].dropna(), ma_window).items()}
    return lines, [name for name in lines if name != t]


def _stock_empty_state(available: bool, tickers: list[str], series: dict[str, pd.Series]) -> None:
    if not series:
        st.info("No stock prices available." if not available
                else "Select at least one stock to plot." if not tickers
                else "No prices for the selected stocks in this window.")
    if missing := [t for t in tickers if t not in series]:
        st.caption(f"No prices in this window for: {', '.join(missing)}.")


def _stock_footer(universe: pd.DataFrame, prices: pd.DataFrame, asof, stale: bool, *notes: str) -> None:
    gaps = data.price_gaps(prices, universe) if not prices.empty else []
    yahoo_gaps = f"Yahoo returned no prices for: {', '.join(gaps)}." if gaps else None
    theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, stale=stale,
                       caveat=" ".join(filter(None, [*notes, STOCK_CAVEAT, yahoo_gaps])))


def _stock_multiselect(key: str, ticker_of: dict[str, str], on_change=None) -> list[str]:
    """Multi-stock search box over the S&P 500 and added tickers; any other Yahoo ticker can be typed in (resolved
    by _stock_options on the next run). Returns the chosen tickers."""
    picked = st.multiselect(
        "Search stocks", list(ticker_of), max_selections=MAX_STOCKS, key=key, on_change=on_change,
        accept_new_options=True, placeholder="Ticker or company name", help=f"{SEARCH_HELP} Up to {MAX_STOCKS} stocks.",
    )
    return [ticker_of[label] for label in picked if label in ticker_of]


def _mark_norm_custom() -> None:
    st.session_state["stock_norm_custom"] = True


def _reset_norm() -> None:
    st.session_state["stock_norm_custom"] = False


@st.fragment
def render_stock_row(lookback_years: int, ma_window: int | None) -> None:
    """Stock price (left) and normalised price (right), side by side. One fragment, so the normalised panel follows
    the stocks chosen on the left until the user picks its own, as the forward P/E pair does."""
    universe, prices, ticker_of, added = _stock_options("close", [("stock_price_sel", True), ("stock_norm_sel", True)])
    label_of = {t: label for label, t in ticker_of.items()}
    st.session_state.setdefault("stock_price_sel", [label_of[t] for t in DEFAULT_STOCKS if t in label_of])
    left, right = st.columns(2)  # two-column grid, as on Rates & Credit (DESIGN.md 4)
    with left:
        picked = render_stock_price_panel(lookback_years, ma_window, universe, prices, ticker_of)
    with right:
        render_normalised_panel(lookback_years, ma_window, universe, prices, ticker_of, picked)
    if added:  # a new ticker: rerun the page so every other stock box offers it too
        st.rerun(scope="app")


# ---------- Equities: options open interest ----------

OPT_DEFAULT = "AAPL"
OPT_RATE = "DGS3MO"                    # FRED 3-month Treasury bill: the risk-free rate in the gamma calculation
OPT_MAX_PAIN, OPT_FLIP = "Max pain", "Gamma flip"
OPT_HOW_OI = (
    "- **Left: price.** The stock's daily close over the chosen period. **Right: open interest.** For each strike, the "
    "number of call (amber) and put (blue) contracts still open, side by side. Both parts share one price axis, so a "
    "bar sits at its strike's price next to the price line. Strikes within ±25% of the last price are shown (wider "
    "if the price history is wider).\n"
    "- **Open interest is not direction.** Every open contract has a buyer and a seller, and the data does not say "
    "which side is customers or dealers. A tall call bar means many contracts are open at that strike, not that "
    "people are bullish.\n"
    "- **Expiry.** One expiry, or all listed expiries added together. The default is the nearest standard monthly "
    "expiry (third Friday), usually the one with the most open interest.\n"
    f"- **{OPT_MAX_PAIN}** (pill, single expiry only): the settlement price, among the listed strikes, at which all "
    "the expiry's calls and puts together would pay their holders the least at expiry. Calculation: for each strike "
    "as a test price, add up call open interest × max(price − strike, 0) plus put open interest × max(strike − "
    "price, 0), × 100 shares, and take the lowest. Assumptions: every contract is held to expiry, open interest "
    "stays as it is today, and every strike in the chain counts (not only those shown).\n"
    "- **How to use max pain.** Read it as a reference level: where today's open options would be worth least in "
    "total. Some traders watch for prices settling near it in the last days before expiry; the evidence for that is "
    "mixed, and this panel does not claim it. It moves as open interest changes, and it means little for an expiry "
    "with little open interest.\n"
    "- **Dotted line:** the last price reported with the option chain."
)
OPT_SOURCES = "Cboe delayed quotes (Yahoo Finance as fallback)"   # footer when no chain loaded, so no source is known
OPT_HOW_GEX = (
    "- **What it shows.** With the toggle on, bars show dealer gamma exposure (GEX) per strike in USD per 1% move "
    "of the stock: how many dollars of stock dealers would need to buy or sell to stay hedged when the stock moves "
    "1%, if the assumption below holds.\n"
    "- **The assumption (SqueezeMetrics convention).** Open interest does not say who is long. The convention "
    "assumes dealers (market makers) are **long every call**, because investors mostly sell calls (covered calls, "
    "overwriting), and **short every put**, because investors mostly buy puts as protection. So call gamma counts "
    "as positive (amber, right of zero) and put gamma as negative (blue, left of zero). The diamond is the net per "
    "strike. When the real positioning differs (e.g. investors buying calls in a rally), the sign and size are wrong.\n"
    "- **Calculation.** Per contract: Black-Scholes gamma × open interest × 100 shares × price² × 1%. Gamma uses "
    f"the data source's implied volatility for each contract (Cboe's, or Yahoo's on fallback), the 3-month T-bill "
    f"rate (FRED {OPT_RATE}) and no dividends. Contracts whose implied volatility is near zero or above 500% (no "
    "usable quote) are left out and "
    "counted in the footer.\n"
    f"- **{OPT_FLIP}** (pill): the price at which total dealer GEX, recomputed at that price, changes sign. Above it "
    "net dealer gamma is positive, below it negative (nearest crossing to the last price within the range shown).\n"
    "- **How to read it.** The reasoning behind the convention: with positive net gamma, dealers hedging would sell "
    "into rises and buy into falls, which leans against price moves; with negative net gamma, hedging goes with the "
    "move. The total in the takeaway is the sum across all strikes and the chosen expiries. Treat it as a model of "
    "positioning under an assumption, not an observation, and not a signal on its own."
)
OPT_CAVEAT = ("Open interest is a daily snapshot that the exchanges publish after each session, so it describes "
              "positions at the previous close; no free source keeps its history. Quotes are delayed 15 minutes. "
              "Options are listed only for US stocks and ETFs. One contract = 100 shares.")


def _opt_expiry_label(e: pd.Timestamp, today: dt.date) -> str:
    days = (e.date() - today).days
    return f"{e:%d %b %Y} · {'today' if days == 0 else f'{days} day' + ('s' if days != 1 else '')}"


def _opt_rate(notes: list[str], series: str = OPT_RATE) -> float:
    """Risk-free rate (decimal): latest FRED Treasury yield `series` (default the 3-month bill, used for gamma), else
    0 with a note."""
    rate, notice = data.load_series(series, 1)
    _warn_all([notice] if notice else [])
    rate = rate.dropna()
    if rate.empty:
        notes.append(f"FRED {series} unavailable: a 0% risk-free rate is used.")
        return 0.0
    return float(rate.iloc[-1]) / 100


def _opt_raw(oi: pd.DataFrame, chain: pd.DataFrame, single: bool, gex: options.Gex | None, unit: str) -> pd.DataFrame:
    raw = oi.rename(columns={"calls": "Call OI", "puts": "Put OI"})
    if single:
        iv = chain.pivot_table(index="strike", columns="type", values="iv", aggfunc="mean") * 100
        raw["Call IV (%)"] = iv.get("call")
        raw["Put IV (%)"] = iv.get("put")
    if gex is not None:
        g = gex.by_strike.reindex(raw.index)
        cur = unit.strip()
        raw[f"Call GEX ({cur} per 1%)"], raw[f"Put GEX ({cur} per 1%)"], raw[f"Net GEX ({cur} per 1%)"] = \
            g["calls"], g["puts"], g["net"]
    raw.index.name = f"Strike ({unit.strip()})"
    return raw.reset_index()


@st.fragment
def render_options_row() -> None:
    """Options open interest, then the analyst target probability and the implied volatility smile below it. One
    fragment, so the target panel follows the stock chosen in the open interest panel until the user picks its own,
    as the stock price pair does; the smile panel draws the target panel's fit (same stock and expiry)."""
    _, prices, ticker_of, added = _stock_options("close", [("opt_sel", False), ("tp_sel", False)])
    ticker = _options_oi_panel(prices, ticker_of)
    _smile_panel(_target_prob_panel(prices, ticker_of, ticker))
    if added:  # a new ticker: rerun the page so every other stock box offers it too
        st.rerun(scope="app")


def _options_oi_panel(prices: pd.DataFrame, ticker_of: dict[str, str]) -> str | None:
    """Open interest by strike for one stock beside its price, on one shared price axis, with max pain and, behind a
    toggle, dealer gamma exposure under the SqueezeMetrics convention (DESIGN.md 14, "Options open interest").
    Returns the chosen ticker."""
    title = "Options open interest"
    with st.container(border=True):
        head = st.container()  # filled after the controls so the takeaway can use the loaded data
        col_s, col_e = st.columns(2)
        with col_s:
            ticker = _pe_select("opt_sel", ticker_of, default=OPT_DEFAULT)
        expiries, exp_notice = data.option_expiries(ticker) if ticker else ([], None)
        today = pd.Timestamp.now(tz=data.US_EASTERN).date()
        with col_e:
            choices = [*expiries, options.ALL_EXPIRIES]
            monthly = options.standard_monthly(expiries)
            picked = st.selectbox(
                "Expiry", choices, key=f"opt_exp_{ticker}", disabled=not expiries,
                index=choices.index(monthly) if monthly is not None else 0,
                format_func=lambda e: e if isinstance(e, str) else _opt_expiry_label(e, today),
                help="Default: the nearest standard monthly expiry (third Friday). All expiries adds every listed "
                     "expiry together; max pain is then not shown, because it is defined for one expiry.")
        single = picked != options.ALL_EXPIRIES
        st.session_state.setdefault("opt_history", options.DEFAULT_HISTORY)
        col_h, col_g, col_l = st.columns([1, 1, 1])
        with col_h:
            st.segmented_control("Price history", tuple(options.HISTORY), key="opt_history",
                                 on_change=_keep_selection, args=("opt_history", options.DEFAULT_HISTORY))
        with col_g:
            gex_on = st.toggle("Dealer gamma (GEX convention)", key="opt_gex",
                               help="Off: open interest as reported. On: assumes dealers are long calls and short "
                                    "puts (SqueezeMetrics) and shows their gamma exposure per strike. See 'Dealer "
                                    "gamma' below the chart.")
        with col_l:
            line_options = [o for o, show in ((OPT_MAX_PAIN, single), (OPT_FLIP, gex_on)) if show]
            lines = st.pills("Lines", line_options, selection_mode="multi", default=line_options,
                             key=f"opt_lines_{int(single)}{int(gex_on)}",
                             help="Max pain needs a single expiry; gamma flip needs the GEX toggle.") \
                if line_options else []

        def empty(message: str, notes: tuple[str, ...] = (), failed: bool = False) -> str | None:
            with head:
                theme.panel_header(title, "Contracts by strike · daily snapshot")
            (st.warning if failed else st.info)(message)
            theme.panel_footer(OPT_SOURCES, "Daily snapshot", None, caveat=" ".join([*notes, OPT_CAVEAT]))
            return ticker

        if not ticker:
            return empty("No stock prices available." if not ticker_of else "Select a stock.")
        if not expiries:
            return empty(exp_notice, failed=True) if exp_notice else \
                empty(f"No listed options found for {ticker}. Option chains are available for US-listed stocks and "
                      "ETFs only.")
        if exp_notice:
            st.warning(exp_notice)
        chain, info, notice = data.load_options(ticker, expiries if not single else [picked])
        if notice:
            st.warning(notice)
        if chain.empty or float(chain["oi"].sum()) <= 0:
            return empty(f"No open interest reported for {ticker} for this expiry." if not chain.empty
                         else f"No option data for {ticker}.")

        spot, unit = float(info["spot"]), f" {info['currency']}"
        asof = pd.Timestamp(info["quote_time"].tz_convert(data.US_EASTERN).date())
        close = prices[ticker].dropna() if ticker in prices else pd.Series(dtype=float)
        history = close.loc[close.index > close.index[-1] - pd.DateOffset(months=options.HISTORY[
            st.session_state["opt_history"]])] if not close.empty else close
        lo, hi = options.strike_window(spot, history)
        oi = options.by_strike(chain)
        shown = oi.loc[(oi.index >= lo) & (oi.index <= hi)]

        notes: list[str] = []
        mp = options.max_pain(oi) if single else None
        gex = None
        if gex_on:
            gex = options.dealer_gex(chain, spot, pd.Timestamp.now(tz="UTC"), _opt_rate(notes), (lo, hi))
            if gex.excluded:
                notes.append(f"{gex.excluded:,} contracts with open interest have no usable implied volatility and "
                             "are left out of GEX.")
        scope = f"expiring {picked:%d %b %Y}" if single else f"all {len(expiries)} expiries"
        sm = summarise_options(ticker, scope, float(oi["calls"].sum()), float(oi["puts"].sum()),
                               float(oi["calls"].idxmax()) if oi["calls"].max() > 0 else None,
                               float(oi["puts"].idxmax()) if oi["puts"].max() > 0 else None,
                               spot, unit, asof, max_pain=mp, gex_total=gex.total if gex else None,
                               flip=gex.flip if gex else None)
        subtitle = (f"{unit.strip()} per 1% move, dealer gamma by strike (assumed: dealers long calls, short puts)"
                    if gex_on else "Contracts open by strike") + f" · {unit.strip()} price · daily snapshot"
        with head:
            theme.panel_header(title, subtitle, sm.text)

        levels = [(f"Last {theme.fmt_num(spot, 2)}", spot, "dot")]
        if mp is not None and OPT_MAX_PAIN in lines:
            if lo <= mp <= hi:
                levels.append((f"Max pain {theme.fmt_num(mp, 2)}", mp, "dash"))
            else:
                notes.append(f"Max pain ({theme.fmt_num(mp, 2, unit)}) is outside the price range shown.")
        if gex is not None and gex.flip is not None and OPT_FLIP in lines:
            levels.append((f"Gamma flip {theme.fmt_num(gex.flip, 2)}", gex.flip, "dashdot"))
        if gex is not None:
            g = gex.by_strike.loc[(gex.by_strike.index >= lo) & (gex.by_strike.index <= hi)]
            bars = g[["calls", "puts"]].rename(columns={"calls": "Calls (dealer long)", "puts": "Puts (dealer short)"})
            fig = theme.oi_profile_fig(history, bars, y_range=(lo, hi), levels=levels, axis_decimals=0, net=g["net"],
                                       unit=unit, x_title=f"Dealer gamma ({unit.strip()} per 1% move)")
        else:
            bars = shown.rename(columns={"calls": "Calls", "puts": "Puts"})
            fig = theme.oi_profile_fig(history, bars, y_range=(lo, hi), levels=levels, axis_decimals=0,
                                       unit=unit, x_title="Open interest (contracts)")
        theme.render(fig, key="opt_profile")
        with st.expander("How to read this chart: open interest and max pain"):
            st.markdown(OPT_HOW_OI)
        with st.expander("Dealer gamma (GEX): the assumption and how to read it"):
            st.markdown(OPT_HOW_GEX)

        if history.empty:
            notes.append(f"No saved price history for {ticker}.")
        if gex_on:
            notes.append("GEX assumes dealers are long all calls and short all puts (SqueezeMetrics convention); "
                         "actual dealer positions are not published.")
        theme.panel_footer(info["source"], "Daily snapshot (open interest as of the previous session)", asof,
                           stale=sm.stale, caveat=" ".join([*notes, OPT_CAVEAT]))
        with st.expander("Raw data"):
            raw = _opt_raw(shown, chain, single, gex, unit)
            st.dataframe(raw, hide_index=True, width="stretch", column_config={
                c: st.column_config.NumberColumn(format="%.2f" if "Strike" in c or "IV" in c else "localized")
                for c in raw.columns})
    return ticker


# ---------- Equities: analyst target probability (options-implied, risk-neutral) ----------

TP_HISTORY_MONTHS = 12                 # daily closes shown before the fan, about as long as the default 12M fan
TP_RATES = ((0.25, "DGS3MO"), (0.5, "DGS6MO"), (1.0, "DGS1"), (2.0, "DGS2"), (3.0, "DGS3"))  # FRED Treasury by tenor
TP_NAMES = {"mean": "Mean", "median": "Median", "low": "Low", "high": "High"}   # tile and label order
TP_HOW = (
    f"- **Left: price.** The stock's daily close over the last {TP_HISTORY_MONTHS} months.\n"
    "- **Right: the options-implied range.** From the last price out to the chosen expiry, the fan shows the range "
    "of prices that the option market's prices imply for each date: the darker band holds the middle 50% of "
    "outcomes (25th to 75th percentile), the lighter band 90% (5th to 95th), and the dashed line is the median.\n"
    "- **Dotted line: the forward, which is the mean.** The price at which the stock can be bought today for "
    "delivery on each date: today's price grown at the risk-free rate, less expected dividends. Option prices pin "
    "the *average* outcome to it. The *median* (dashed) sits below it, often below today's price for volatile "
    "stocks, because returns compound: a −50% move needs +100% to recover, so a few very large gains lift the "
    "average while the typical path ends lower. The gap is roughly forward × (1 − e^(−½σ²T)), about 9% for a stock "
    "with 44% volatility over a year. A median below today's price is therefore not a sign that the market expects "
    "a fall; read the forward for the market's average.\n"
    "- **Diamonds: analyst targets.** Yahoo's consensus target price (mean and median of the analysts covering the "
    "stock) and the lowest and highest single target, placed on the expiry date. Each label and tile gives the "
    "risk-neutral probability that the stock **ends at or above** that price on the expiry date.\n"
    "- **How to use it.** Compare the targets with the fan. A target inside the darker band is a move the option "
    "market prices as ordinary for the horizon; one near the edge of the lighter band, or outside it, is a move it "
    "prices as unusual. A low probability for the consensus target means the analysts are more optimistic than "
    "option prices; a high one means they are less optimistic. Read it alongside the valuation panels, not as a "
    "signal on its own.\n"
    "- **Expiry.** Defaults to the listed expiry closest to 12 months, the horizon of a price target. Pick another "
    "to see how the range widens or narrows with time; the footer says how far the expiry is from 12 months.\n"
    "- **Stock.** Follows the open interest panel above. Picking a stock here unlinks it; \"Follow the chart above\" "
    "links it again."
)
TP_ASSUMPTIONS = (
    "- **Risk-neutral, not real-world.** Option prices include a premium that investors pay for protection and "
    "demand for bearing risk. The probabilities they imply therefore usually **understate the real-world chance of "
    "rises and overstate the chance of falls**. They describe how options are priced, not how likely an outcome is, "
    "and they are not a forecast.\n"
    "- **Ends at or above, on the expiry date.** The probability is for the price on the expiry date only. A price "
    "that touches the target earlier and falls back does not count; the chance of touching it at any time before "
    "expiry is higher (roughly twice as high for a target far from the price).\n"
    "- **12-month targets.** Yahoo's targets are treated as 12-month targets, and the default expiry is the listed "
    "one closest to 12 months, which can be a month or more away from it.\n"
    "- **Analyst targets.** Yahoo's consensus, with no date for each analyst's target, so some may be months old. "
    "The low and high are single analysts.\n"
    "- **Forward price.** From put-call parity on the five strikes nearest the price: forward = strike + (call mid − "
    "put mid) / discount factor, median of the five. This includes expected dividends without needing to know them. "
    "The discount rate is the FRED Treasury yield closest to the expiry's tenor (3M, 6M, 1Y, 2Y or 3Y).\n"
    "- **Implied volatility.** Solved (Black-76 on the forward) from the bid/ask mid of each out-of-the-money option: "
    "puts below the forward, calls above. Quotes without a bid, or with a spread above "
    f"{options.MAX_REL_SPREAD:.0%} of the mid, are dropped. US stock options are American-style; out-of-the-money "
    "options carry little early-exercise value, so they are priced as European.\n"
    "- **Smile and probability.** The implied volatilities are fitted with a quadratic in log(strike / forward), "
    "weighted towards strikes near the price, and continued along its edge slope beyond the strikes quoted (targets "
    "out there are marked *). The probability of ending above a price K is minus the slope of the call price in K "
    "(Breeden-Litzenberger), so it includes the volatility skew; with typical equity skew this gives a higher "
    "probability of moderate rises than a single-volatility (lognormal) model.\n"
    "- **The fan before expiry.** Only the expiry end comes from option prices. Between today and expiry, the "
    "expiry's range is scaled by the square root of time (same shape, variance growing evenly with time).\n"
    f"- **Liquidity.** At least {options.MIN_QUOTES_SIDE} usable quotes are needed on each side of the forward, or "
    "no distribution is shown. The footer flags wide quotes."
)
TP_CAVEAT = ("Risk-neutral probabilities, not forecasts: they usually understate the real-world chance of rises and "
             "overstate the chance of falls. Option quotes are a snapshot, delayed 15 minutes; options are listed only "
             "for US stocks and ETFs.")


def _mark_tp_custom() -> None:
    st.session_state["tp_custom"] = True


def _reset_tp() -> None:
    st.session_state["tp_custom"] = False


def _tp_raw(imp: options.Implied, unit: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Quotes priced (with the fitted smile and the probability of ending above each strike) and the range's
    percentiles at expiry."""
    q = imp.quotes
    cur = unit.strip()
    quotes = pd.DataFrame({
        f"Strike ({cur})": q["strike"], "Type": q["type"].str.capitalize(), "Bid": q["bid"], "Ask": q["ask"],
        "Mid": q["mid"], "Spread (% of mid)": q["spread"] * 100, "IV (%)": q["iv"] * 100,
        "Fitted IV (%)": q["fit_iv"] * 100, "P(ends ≥ strike) (%)": [imp.prob_above(k) * 100 for k in q["strike"]],
    })
    pcts = pd.DataFrame({"Percentile": [f"{p * 100:.0f}th" for p in options.CONE_QUANTILES],
                         f"Price at expiry ({cur})": [imp.quantile(p) for p in options.CONE_QUANTILES]})
    return quotes, pcts


@dataclass(frozen=True)
class _SmileInput:
    """What the smile panel draws: the target panel's fit, so both always show the same stock and expiry."""
    ticker: str
    imp: options.Implied
    unit: str
    asof: pd.Timestamp
    targets: dict[str, float]
    source: str                    # option quote source, for the footer


def _target_prob_panel(prices: pd.DataFrame, ticker_of: dict[str, str], follow: str | None) -> _SmileInput | None:
    """Risk-neutral range of the price at one expiry (default: closest to 12 months), drawn as a fan after the price
    history, with Yahoo's analyst targets and the probability of ending at or above each (DESIGN.md 14, "Analyst
    target probability"). The stock follows the open interest panel until the user picks another here. Returns the
    fit for the smile panel, or None when there is none."""
    title = "Analyst targets vs options-implied range"
    label_of = {t: label for label, t in ticker_of.items()}
    if not st.session_state.get("tp_custom") and follow in label_of:
        st.session_state["tp_sel"] = label_of[follow]  # set before the widget is created
    with st.container(border=True):
        head = st.container()  # filled after the controls so the takeaway can use the loaded data
        col_s, col_e = st.columns(2)
        with col_s:
            ticker = _pe_select("tp_sel", ticker_of, default=OPT_DEFAULT, on_change=_mark_tp_custom)
            if st.session_state.get("tp_custom"):
                st.button("Follow the chart above", icon=":material/link:", on_click=_reset_tp, key="tp_reset")
        expiries, exp_notice = data.option_expiries(ticker) if ticker else ([], None)
        today = pd.Timestamp.now(tz=data.US_EASTERN).date()
        with col_e:
            default = options.nearest_expiry(expiries, today)
            picked = st.selectbox(
                "Expiry", expiries, key=f"tp_exp_{ticker}", disabled=not expiries,
                index=expiries.index(default) if default is not None else None,
                format_func=lambda e: _opt_expiry_label(e, today),
                help="Default: the listed expiry closest to 12 months, the horizon of an analyst price target.")

        def empty(message: str, notes: tuple[str, ...] = (), failed: bool = False) -> None:
            with head:
                theme.panel_header(title, "Price · options-implied range at expiry · daily snapshot")
            (st.warning if failed else st.info)(message)
            theme.panel_footer(f"{OPT_SOURCES}; analyst targets: {STOCK_SOURCE}", "Daily snapshot", None,
                               caveat=" ".join([*notes, TP_CAVEAT]))

        if not ticker:
            return empty("No stock prices available." if not ticker_of else "Select a stock.")
        if not expiries or picked is None:
            return empty(exp_notice, failed=True) if exp_notice else \
                empty(f"No listed options found for {ticker}. Option chains are available for US-listed stocks and "
                      "ETFs only.")
        if exp_notice:
            st.warning(exp_notice)
        chain, info, notice = data.load_options(ticker, [picked])
        if notice:
            st.warning(notice)
        if chain.empty:
            return empty(f"No option data for {ticker}.")

        notes: list[str] = []
        now = pd.Timestamp.now(tz="UTC")
        spot, unit = float(info["spot"]), f" {info['currency']}"
        asof = pd.Timestamp(info["quote_time"].tz_convert(data.US_EASTERN).date())
        series = min(TP_RATES, key=lambda r: abs(r[0] - options.years_to_expiry(picked, now)))[1]
        rate = _opt_rate(notes, series)
        imp, why = options.implied_distribution(chain, spot, now, rate)
        if imp is None:
            return empty(f"Not enough usable option quotes for {ticker} expiring {picked:%d %b %Y}: {why}. A range "
                         "built from so few prices would mostly show noise. Try another expiry, or a more widely "
                         "traded stock.", tuple(notes))

        tg, t_notice = data.load_price_targets(ticker)
        if t_notice:
            st.warning(t_notice)
        targets = {k: tg["targets"][k] for k in TP_NAMES if k in tg["targets"]}
        if targets and tg["currency"] and tg["currency"] != info["currency"]:
            notes.append(f"Yahoo lists the targets in {tg['currency']} and the options in {info['currency']}; "
                         "they are compared without conversion.")
        probs = {k: imp.prob_above(v) for k, v in targets.items()}
        days = (picked.date() - today).days
        lead = next((k for k in ("mean", "median") if k in targets), None)
        sm = summarise_target_prob(ticker, picked, days, spot, unit, asof, imp.quantile(0.25), imp.quantile(0.75),
                                   target=(TP_NAMES[lead].lower(), targets[lead], probs[lead]) if lead else None,
                                   analysts=tg["analysts"])
        with head:
            theme.panel_header(title, f"{unit.strip()} · daily close, then the options-implied range to expiry · % = "
                                      "risk-neutral probability of ending at or above · daily snapshot", sm.text)

        if targets:
            theme.kpi_strip([dict(
                label=f"{TP_NAMES[k]} target {theme.fmt_num(v, 2, unit)}" + (" *" if imp.extrapolated(v) else ""),
                value=probs[k] * 100, decimals=0, unit="%", delta=(v / spot - 1) * 100, delta_decimals=1,
                delta_unit="% vs last", good_when=None,
                help=f"Risk-neutral probability, priced by options expiring {picked:%d %b %Y}, that {ticker} ends at "
                     f"or above {theme.fmt_num(v, 2, unit)}. Not a real-world forecast; see 'Assumptions' below.")
                for k, v in targets.items()])
        elif not t_notice:   # a failed fetch already says why there are no targets
            st.info(f"Yahoo Finance lists no analyst price targets for {ticker} (usual for ETFs and indices). The "
                    "options-implied range is shown without them.")

        close = prices[ticker].dropna() if ticker in prices else pd.Series(dtype=float)
        history = close.loc[close.index > close.index[-1] - pd.DateOffset(months=TP_HISTORY_MONTHS)] \
            if not close.empty else close
        cone = imp.cone(asof)
        pct = lambda q: cone[q].rename(f"{q * 100:.0f}th percentile")  # noqa: E731
        markers = [(f"{TP_NAMES[k]} {theme.fmt_num(v, 2)} · {theme.fmt_num(probs[k] * 100, 0, '%')}"
                    + (" *" if imp.extrapolated(v) else ""), v) for k, v in targets.items()]
        fig = theme.cone_fig(history, [("5–95% range", pct(0.05), pct(0.95)), ("25–75% range", pct(0.25), pct(0.75))],
                             cone[0.5].rename("Median"), markers=markers, unit=unit, axis_decimals=0,
                             mean=(f"Forward {theme.fmt_num(imp.forward, 2)} (mean)",
                                   cone["forward"].rename("Forward (mean)")))
        theme.render(fig, key="tp_cone")
        with st.expander("How to use this chart"):
            st.markdown(TP_HOW)
        with st.expander("Assumptions and method"):
            st.markdown(TP_ASSUMPTIONS)

        if abs(days - options.TARGET_HORIZON_DAYS) > 14:
            notes.append(f"Targets are taken as 12-month; this expiry is {days:,} days away "
                         f"({abs(days - options.TARGET_HORIZON_DAYS):,} days {'short of' if days < 365 else 'beyond'} "
                         "12 months).")
        if any(imp.extrapolated(v) for v in targets.values()):
            lo, hi = imp.strike_range
            notes.append(f"* Beyond the strikes quoted ({theme.fmt_num(lo, 2)}–{theme.fmt_num(hi, 2, unit)}): the "
                         "smile is extrapolated, so that probability is less reliable.")
        if not imp.parity:
            notes.append("No strike near the price has both a two-sided call and put quote: the forward is the last "
                         "price grown at the risk-free rate, with no dividends.")
        spread = float(imp.quotes["spread"].median())
        if spread > options.WIDE_SPREAD:
            notes.append(f"Quotes are wide (median bid/ask spread {theme.fmt_num(spread * 100, 0, '%')} of the mid): "
                         "treat the probabilities as rough.")
        if imp.smoothed > 0.01:
            notes.append("The fitted smile implied a negative probability in places; it was smoothed (largest change "
                         f"{theme.fmt_num(imp.smoothed * 100, 1)} pp).")
        if history.empty:
            notes.append(f"No saved price history for {ticker}.")
        targets_old = False
        if tg.get("saved"):
            saved = dt.date.fromisoformat(tg["saved"])
            targets_old = (today - saved).days > data.TARGETS_STALE_DAYS
            notes.append(f"Yahoo did not answer live, so analyst targets are from the snapshot saved "
                         f"{saved:%d %b %Y}" + (f" (over {data.TARGETS_STALE_DAYS} days old)." if targets_old else "."))
        notes.append(f"{len(imp.quotes):,} quotes priced; forward {theme.fmt_num(imp.forward, 2, unit)}; rate FRED "
                     f"{series} {theme.fmt_num(rate * 100, 2, '%')}.")
        theme.panel_footer(f"{info['source']}; analyst targets: {STOCK_SOURCE}",
                           "Daily snapshot (option quotes; analyst targets as listed)", asof,
                           stale=sm.stale or targets_old, caveat=" ".join([*notes, TP_CAVEAT]))
        with st.expander("Raw data"):
            quotes, pcts = _tp_raw(imp, unit)
            st.dataframe(quotes, hide_index=True, width="stretch", column_config={
                c: st.column_config.NumberColumn(format="%.2f") for c in quotes.columns if c != "Type"})
            st.dataframe(pcts, hide_index=True, width="stretch", column_config={
                c: st.column_config.NumberColumn(format="%.2f") for c in pcts.columns if c != "Percentile"})
    return _SmileInput(ticker, imp, unit, asof, targets, info["source"])


SMILE_HOW = (
    "- **What it shows.** Each dot is one option's implied volatility (IV): the volatility that, in the standard "
    "Black model, reproduces the option's bid/ask mid price. It is the option's price restated in a unit that can be "
    "compared across strikes, as a yield restates a bond's price. Blue circles are puts (strikes below the forward), "
    "pink triangles calls (above it); only out-of-the-money options are used, because they are the ones actively "
    "traded. The bar through each dot runs from the IV at the bid to the IV at the ask: a long bar means a wide quote "
    "and a less certain point.\n"
    "- **The line.** The smooth curve fitted through the dots, the same one that produces the range and "
    "probabilities in the panel above. Solid where quotes exist; dashed beyond the lowest and highest strike quoted, "
    "where it is extended along its edge slope. Diamonds show the fitted IV at each analyst target.\n"
    "- **Reading the shape.** A flat line would mean the market prices all price levels with the same volatility (the "
    "bell-curve, lognormal case). For stocks the line usually slopes down to the right (skew): low-strike puts cost "
    "more, in volatility terms, than high-strike calls, because investors pay for crash protection. The steeper the "
    "left side, the fatter the downside tail priced into the range above, and the higher the probability given to "
    "moderate rises (the range's mean is fixed at the forward). A line that turns up on the right means calls on "
    "large rises are also priced dearly, as for stocks where investors chase upside (takeovers, momentum names).\n"
    "- **How to use it.** Compare the level with the stock's history and peers (a higher line = options are pricing "
    "bigger moves) and the slope (a steeper skew = more demand for protection). Check the fit before trusting the "
    "panel above: dots far from the line, or long bid/ask bars, mean the probabilities are less reliable. A target in "
    "the dashed part rests on extrapolation.\n"
    "- **Vertical lines.** Dotted: the last price. Dashed: the forward price (from put-call parity), where the "
    "range above is centred."
)
SMILE_ASSUMPTIONS = (
    "- **Same inputs as the panel above.** Same stock, expiry, quote filters (no bid, or spread above "
    f"{options.MAX_REL_SPREAD:.0%} of the mid, dropped), forward and discount rate.\n"
    "- **Black-76 on the forward.** IV is solved from the undiscounted mid with the parity forward, so dividends and "
    "interest are already in the forward. US stock options are American-style; out-of-the-money options carry "
    "little early-exercise value, so they are treated as European.\n"
    "- **IV is a quoting convention, not a forecast of volatility.** It includes the premium option sellers charge "
    "for risk, so it usually runs above the volatility that the stock then shows.\n"
    "- **The fit.** A quadratic in log(strike / forward), weighted towards strikes near the forward (where options "
    "trade most), continued in a straight line beyond the strikes quoted. A quadratic is a smooth summary: it can "
    "miss sharp curvature in the far wings, which the root-mean-square error in the takeaway measures.\n"
    "- **90% / 110% of the forward.** A simple, model-free way to state the skew: IV on the fitted line at 10% below "
    "and 10% above the forward, and the gap between them in percentage points (pp)."
)


def _smile_panel(src: _SmileInput | None) -> None:
    """Implied volatility smile behind the analyst target panel: quotes' IV with bid/ask bars, the fitted smile and
    the targets on it (DESIGN.md 14, "Implied volatility smile")."""
    title = "Implied volatility smile"
    with st.container(border=True):
        if src is None:
            theme.panel_header(title, "% implied volatility by strike · daily snapshot")
            st.info("No smile to show: the panel above has no usable option quotes for this stock and expiry.")
            theme.panel_footer(OPT_SOURCES, "Daily snapshot", None)
            return
        imp, unit, cur = src.imp, src.unit, src.unit.strip()
        q = imp.quotes
        atm, put90, call110 = imp.fitted_iv([imp.forward, 0.9 * imp.forward, 1.1 * imp.forward]) * 100
        sm = summarise_smile(src.ticker, imp.expiry, imp.forward, unit, atm, put90, call110, imp.fit_error() * 100,
                             len(q), src.asof)
        theme.panel_header(title, f"% implied volatility by strike ({cur}) · expiring {imp.expiry:%d %b %Y} · daily "
                                  "snapshot", sm.text)
        lo, hi = imp.strike_range
        x_lo = min(lo, *src.targets.values(), imp.spot) if src.targets else min(lo, imp.spot)
        x_hi = max(hi, *src.targets.values(), imp.spot) if src.targets else max(hi, imp.spot)
        grid = np.linspace(x_lo, x_hi, 300)
        fit = pd.Series(imp.fitted_iv(grid) * 100, index=grid)
        groups = [(name, pd.DataFrame({"strike": g["strike"], "iv": g["iv"] * 100,
                                       "lo": g["iv_bid"].fillna(g["iv"]) * 100, "hi": g["iv_ask"].fillna(g["iv"]) * 100}))
                  for name, g in (("Puts", q[q["type"] == "put"]), ("Calls", q[q["type"] == "call"])) if not g.empty]
        markers = [(TP_NAMES[k], v, float(imp.fitted_iv([v])[0] * 100)) for k, v in src.targets.items()]
        fig = theme.smile_fig(groups, fit, quoted=(lo, hi), markers=markers, unit=unit, levels=[
            (f"Last {theme.fmt_num(imp.spot, 2)}", imp.spot, "dot"),
            (f"Forward {theme.fmt_num(imp.forward, 2)}", imp.forward, "dash")])
        theme.render(fig, key="tp_smile")
        with st.expander("How to use this chart"):
            st.markdown(SMILE_HOW)
        with st.expander("Assumptions"):
            st.markdown(SMILE_ASSUMPTIONS)
        notes = []
        if q["iv_bid"].isna().any():
            notes.append(f"{int(q['iv_bid'].isna().sum()):,} bids are too low to solve for a volatility; their bars "
                         "start at the mid.")
        theme.panel_footer(src.source, "Daily snapshot (option quotes)", src.asof,
                           stale=sm.stale, caveat=" ".join([*notes, "Implied volatility is a quoting convention that "
                                                            "includes a risk premium; it is not a volatility forecast."]))
        with st.expander("Raw data"):
            raw = pd.DataFrame({f"Strike ({cur})": q["strike"], "Type": q["type"].str.capitalize(),
                                "IV at bid (%)": q["iv_bid"] * 100, "IV at mid (%)": q["iv"] * 100,
                                "IV at ask (%)": q["iv_ask"] * 100, "Fitted IV (%)": q["fit_iv"] * 100})
            st.dataframe(raw, hide_index=True, width="stretch", column_config={
                c: st.column_config.NumberColumn(format="%.2f") for c in raw.columns if c != "Type"})


def _currency_names(series: dict[str, pd.Series]) -> tuple[dict[str, str], str | None]:
    """Trading currency per stock, and the one shared currency (None when the stocks are in different currencies)."""
    currency = {t: data.ticker_currency(t) for t in series}
    shared = set(currency.values())
    return currency, shared.pop() if len(shared) == 1 else None


def render_stock_price_panel(lookback_years: int, ma_window: int | None, universe: pd.DataFrame,
                             prices: pd.DataFrame, ticker_of: dict[str, str]) -> list[str]:
    """Search stocks by ticker or company name and compare their closing prices over the sidebar lookback, on one
    shared price axis (DESIGN.md 5.2 anatomy, 6.2 "How do related series compare"). Each stock is in its listing's
    own currency; with mixed currencies the currency moves from the axis into each legend name. Returns the labels
    chosen, for the normalised panel to follow."""
    title = "Stock price"
    with st.container(border=True):
        head = st.container()  # filled after the control so the takeaway can use the loaded data
        tickers = _stock_multiselect("stock_price_sel", ticker_of)
        series = _window_series(prices, tickers, lookback_years)
        currency, shared = _currency_names(series)
        unit = f" {shared}" if shared else ""
        subtitle = f"{shared or 'Local currency (see legend)'} per share · split-adjusted close · daily"
        sm, notes = None, []
        if series:
            lead = next(iter(series))
            sm = summarise(series[lead], replace(STOCK_TAKEAWAY, label=lead, unit=f" {currency[lead]}"))
        if not shared and series:
            notes.append("Each line is in its listing's currency (shown in the legend), so price levels in different "
                         "currencies are not directly comparable.")
        with head:
            theme.panel_header(title, subtitle, (sm.text if sm else NO_SUMMARY) if series else "")

        named = {t if shared else f"{t} ({currency[t]})": s for t, s in series.items()}
        if series:
            plot, dashed = _stock_lines(prices, series, ma_window)
            if not shared:
                plot = named  # mixed currencies means several stocks, so there is no moving average to rename
            theme.render(theme.line_fig(plot, dashed=dashed, unit=unit, decimals=2), key="stock_price")
        _stock_empty_state(bool(ticker_of), tickers, series)
        _stock_footer(universe, prices, _asof(sm, series), bool(sm and sm.stale), *notes)
        if series:
            with st.expander("Raw data"):
                st.dataframe(_raw_table(pd.concat(named, axis=1), unit, 2), width="stretch")
    return st.session_state.get("stock_price_sel", [])


def render_normalised_panel(lookback_years: int, ma_window: int | None, universe: pd.DataFrame,
                            prices: pd.DataFrame, ticker_of: dict[str, str], follow: list[str]) -> None:
    """The stock price panel's companion: the same closes rebased to 100 on a common base date, so different
    price levels (and currencies) compare as % change (DESIGN.md 6.2 "Relative performance"). Its stocks follow
    the stock price panel until the user picks others here; the button returns to following."""
    title = "Normalised price"
    subtitle = f"Index, base date = {REBASE_TO:g} · split-adjusted close · daily"
    if not st.session_state.get("stock_norm_custom"):
        st.session_state["stock_norm_sel"] = list(follow)  # set before the widget is created
    with st.container(border=True):
        head = st.container()
        tickers = _stock_multiselect("stock_norm_sel", ticker_of, on_change=_mark_norm_custom)
        if st.session_state.get("stock_norm_custom"):
            st.button("Follow the chart on the left", icon=":material/link:", on_click=_reset_norm,
                      key="stock_norm_reset")
        series = _window_series(prices, tickers, lookback_years)

        sm, notes, rebased, dashed, cut = None, [], {}, [], {}
        if series:
            # Common base date: the latest first-available date, so every line starts at 100 on the same day.
            base_date = max(s.index[0] for s in series.values())
            cut = {t: s.loc[s.index >= base_date] for t, s in series.items()}
            notes.append(f"Base date {base_date:%d %b %Y} = {REBASE_TO:g}.")
            if _currency_names(series)[1] is None:
                notes.append("Each line is a return in its listing's own currency (no FX conversion).")
            # A short-history stock trims the chart. A week's grace: markets on other holiday calendars may simply
            # not trade on the window's first day.
            if base_date > data.prices_window(prices, lookback_years).index[0] + pd.Timedelta(days=7):
                late = [t for t, s in series.items() if s.index[0] == base_date]
                notes.append(f"The chart starts later than the window because {', '.join(late)} "
                             "has no earlier prices in it.")
            lines, dashed = _stock_lines(prices, cut, ma_window)
            first = {t: s.iloc[0] for t, s in cut.items()}  # base prices
            # A moving-average line (single stock only) is scaled by its stock's base price.
            rebased = {name: line / first[name if name in first else next(iter(first))] * REBASE_TO
                       for name, line in lines.items()}
            sm = summarise_returns({t: rebased[t] for t in cut})
        with head:
            theme.panel_header(title, subtitle, (sm.text if sm else NO_SUMMARY) if series else "")

        if series:
            theme.render(theme.line_fig(rebased, dashed=dashed, decimals=1, baseline=REBASE_TO), key="stock_norm")
        _stock_empty_state(bool(ticker_of), tickers, series)
        _stock_footer(universe, prices, _asof(sm, series), bool(sm and sm.stale), *notes)
        if series:
            with st.expander("Raw data"):
                st.dataframe(_raw_table(pd.concat({t: rebased[t] for t in cut}, axis=1), " index", 1), width="stretch")


# ---------- Equities: forward P/E panels ----------

PE_UNIT = "x"
PE_DEFAULT = "AAPL"
PE_TAKEAWAY = TakeawaySpec("", unit=PE_UNIT, decimals=1, freq="D", change_days=30, change_label="1M")
PE_CAVEAT = ("Past multiples use the EPS later realized (hindsight), not what analysts expected at the time; "
             "quarters not yet reported use Yahoo's current estimates. Reported EPS is usually on an adjusted "
             "basis. See Methodology.")
PE_HIST_CAVEAT = "Bands are the window mean ±1σ and ±2σ."
PE_CMP_CAVEAT = ("Averages are total market cap over total NTM earnings of the group's stocks with positive NTM EPS "
                 "on each date (negative earners excluded), using today's share counts. Sector = GICS sector.")
PE_SP500 = "S&P 500"


def _gap_days(ticker: str) -> tuple[int, int]:
    """Quarter spacing accepted as unbroken: wider for added (non-US) tickers, see valuation.WIDE_GAP_DAYS."""
    return valuation.WIDE_GAP_DAYS if ticker in data.added_tickers() else valuation._GAP_DAYS


def _pe_frame(ticker: str, prices: pd.Series, reported: pd.Series, estimates: dict, lag_days: int):
    """(quarters, NTM EPS on every price date, NTM P/E) for one stock, full saved history."""
    quarters = valuation.build_quarters(reported, estimates, lag_days, _gap_days(ticker))
    eps = valuation.ntm_eps(quarters, prices.dropna().index)
    return quarters, eps, valuation.ntm_pe(prices, eps)


def _pe_select(key: str, ticker_of: dict[str, str], *, default: str | None = None, on_change=None,
               label: str = "Search stock") -> str | None:
    """Single-stock search box (ticker or company name, or any Yahoo ticker typed in, resolved by _stock_options on
    the next run). `default` is a ticker used only when the widget has no value yet; returns the chosen ticker."""
    labels = list(ticker_of)
    kwargs = {}
    if key not in st.session_state:
        kwargs["index"] = next((i for i, label in enumerate(labels) if ticker_of[label] == default), 0) \
            if labels else None
    picked = st.selectbox(label, labels, key=key, placeholder="Ticker or company name", on_change=on_change,
                          accept_new_options=True, help=f"{SEARCH_HELP} One stock at a time.", **kwargs)
    if picked in ticker_of:
        st.session_state[f"_{key}_good"] = picked  # where the box goes back to if a typed ticker fails
    return ticker_of.get(picked)


def _pe_close(prices: pd.DataFrame, ticker: str) -> pd.Series:
    """A stock's closes for P/E work, in its currency's major unit: London prices quoted in pence (GBp) are divided by
    100, because Yahoo reports their EPS in pounds. Charts keep the quoted unit."""
    if ticker not in prices:
        return pd.Series(dtype=float)
    return prices[ticker].dropna() / data.major_currency(data.ticker_currency(ticker))[1]


def _eps_currency_note(ticker: str) -> str | None:
    """Footer note when Yahoo lists an added stock's financial statements in a different currency from its price:
    EPS is used as Yahoo reports it, unconverted (owner's choice), so the P/E may be off by the exchange rate."""
    meta = data.added_tickers().get(ticker) or {}
    price_cur, eps_cur = data.major_currency(meta.get("currency"))[0], meta.get("financial_currency")
    if not (price_cur and eps_cur) or price_cur == eps_cur:
        return None
    return (f"Yahoo lists {ticker}'s financial statements in {eps_cur} and its price in {price_cur}. EPS is used as "
            "Yahoo reports it, without currency conversion, so this P/E may be off by the exchange rate.")


def _negative_eps_message(ticker: str, latest_eps: float) -> None:
    st.info(f"{ticker} has negative expected earnings over the next 12 months "
            f"({theme.fmt_num(latest_eps, 2, ' USD')} per share), so a P/E multiple is not meaningful. "
            "This method is not appropriate for companies with negative EPS.")


def _pe_stock(ticker: str, close: pd.Series):
    """Load one stock's EPS (warning on any fallback) and build its history. Returns (quarters, NTM EPS, NTM P/E)
    or None when there is no EPS or price to work with."""
    with st.spinner(f"Loading {ticker} EPS…"):
        reported, estimates, lag_days, notice = data.load_eps(ticker)
    if notice:
        st.warning(notice)
    if close.empty or reported.empty:
        return None
    return _pe_frame(ticker, close, reported, estimates, lag_days)


def _pe_cutoff(lookback_years: int) -> pd.Timestamp:
    return pd.Timestamp.now() - pd.Timedelta(days=365 * lookback_years)


def _ntm_pe_history_panel(lookback_years: int, prices: pd.DataFrame, ticker_of: dict[str, str]) -> str | None:
    """Forward (NTM) P/E of one S&P 500 stock over the sidebar lookback: daily close over time-weighted next-12-month
    EPS, against its window mean and ±1σ / ±2σ bands. A stock with negative NTM EPS gets a message instead of a
    chart. Returns the chosen ticker so the comparison panel can follow it."""
    title, subtitle = "Forward P/E (NTM)", "x · daily close over next-12-month EPS (time-weighted) · daily"
    with st.container(border=True):
        head = st.container()  # filled after the control so the takeaway can use the loaded data
        ticker = _pe_select("ntm_pe_sel", ticker_of, default=PE_DEFAULT)
        sm, pe, notes, asof = None, pd.Series(dtype=float), [], None

        if ticker:
            close = _pe_close(prices, ticker)
            built = _pe_stock(ticker, close)
            if built:
                quarters, eps, full_pe = built
                asof = close.index[-1]
                if pd.notna(eps.iloc[-1]) and eps.iloc[-1] <= 0:
                    with head:
                        theme.panel_header(title, subtitle)
                    _negative_eps_message(ticker, eps.iloc[-1])
                    theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, caveat=PE_CAVEAT)
                    return ticker
                if note := _eps_currency_note(ticker):
                    notes.append(note)
                cutoff = _pe_cutoff(lookback_years)
                pe = full_pe.loc[full_pe.index >= cutoff]
                if len(pe) >= 8:
                    sm = summarise(pe, replace(PE_TAKEAWAY, label=f"{ticker} NTM P/E"))
                in_window = close.index[close.index >= cutoff]
                if len(in_window):
                    dropped = int((~in_window.isin(pe.index)).sum())
                    if dropped:
                        notes.append(f"{dropped} trading days in the window are left out because NTM EPS was "
                                     "negative or not covered by the EPS history.")
                est_from = valuation.estimates_from(quarters)
                if est_from is not None and not pe.empty and pe.index[-1] >= est_from:
                    notes.append(f"From {max(est_from, pe.index[0]):%d %b %Y} the NTM EPS includes analyst "
                                 "estimates.")
                if len(pe) >= 8 and pe.index[0] > in_window[0] + pd.Timedelta(days=30):
                    notes.append(f"The chart starts {pe.index[0]:%d %b %Y}, the first date with a full year of EPS "
                                 "ahead.")
        with head:
            theme.panel_header(title, subtitle, (sm.text if sm else NO_SUMMARY) if len(pe) else "")

        if len(pe) >= 8:
            theme.render(theme.band_fig(f"{ticker} NTM P/E", pe, k=(1, 2), unit=PE_UNIT, decimals=1), key="ntm_pe")
        elif not ticker_of:
            st.info("No stock prices available.")
        elif not ticker:
            st.info("Select a stock to plot.")
        else:
            st.info(f"Not enough EPS history to compute a forward P/E for {ticker} in this window.")
        theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, stale=bool(sm and sm.stale),
                           caveat=" ".join([*notes, PE_HIST_CAVEAT, PE_CAVEAT]))
        if len(pe) >= 8:
            with st.expander("Raw data"):
                cur = data.major_currency(data.ticker_currency(ticker))[0]
                eps_cur = (data.added_tickers().get(ticker) or {}).get("financial_currency") or cur
                raw = pd.DataFrame({f"Close ({cur})": close.reindex(pe.index),
                                    f"NTM EPS ({eps_cur})": eps.reindex(pe.index), "NTM P/E (x)": pe})
                st.dataframe(_raw_table(raw, "", 2), width="stretch")
    return ticker


def _mark_pe_cmp_custom() -> None:
    st.session_state["ntm_cmp_custom"] = True


def _reset_pe_cmp() -> None:
    st.session_state["ntm_cmp_custom"] = False


def _ntm_pe_compare_panel(lookback_years: int, universe: pd.DataFrame, prices: pd.DataFrame, sp_prices: pd.DataFrame,
                          ticker_of: dict[str, str], follow: str | None) -> None:
    """The same NTM P/E line against the stock's GICS sector average and the S&P 500 average (each a tick box).
    The stock follows the history panel's choice until the user picks another here; the button returns to following.
    A stock outside the S&P 500 (the default universe) has no GICS sector, so it is compared with the S&P 500 only.
    `prices` includes added tickers; the averages are built from `sp_prices` (S&P 500 only, US trading days)."""
    title, subtitle = "Forward P/E vs sector and S&P 500", "x · daily · market-cap weighted averages, negative EPS excluded"
    label_of = {t: label for label, t in ticker_of.items()}
    if not st.session_state.get("ntm_cmp_custom") and follow in label_of:
        st.session_state["ntm_cmp_sel"] = label_of[follow]  # set before the widget is created
    with st.container(border=True):
        head = st.container()
        ticker = _pe_select("ntm_cmp_sel", ticker_of, default=PE_DEFAULT, on_change=_mark_pe_cmp_custom)
        if st.session_state.get("ntm_cmp_custom"):
            st.button("Follow the chart on the left", icon=":material/link:", on_click=_reset_pe_cmp,
                      key="ntm_cmp_reset")
        in_sp = ticker is None or ticker in set(universe["ticker"])
        col_sector, col_sp = st.columns(2)
        show_sector = col_sector.checkbox(
            "Sector average (GICS)", value=True, key="ntm_cmp_sector", disabled=not in_sp,
            help=None if in_sp else "Only for S&P 500 stocks: sector averages are built from the S&P 500.",
        ) and in_sp
        show_sp = col_sp.checkbox("S&P 500 average", value=True, key="ntm_cmp_sp")

        lines: dict[str, pd.Series] = {}
        notes, asof, sm, close = [], None, None, pd.Series(dtype=float)
        if not in_sp:
            notes.append(f"{ticker} is not in the S&P 500 (the default universe), so it is compared with the S&P 500 "
                         "average only, not a sector average.")
        if ticker and (note := _eps_currency_note(ticker)):
            notes.append(note)
        if ticker:
            close = _pe_close(prices, ticker)
            built = _pe_stock(ticker, close)
            if built:
                _, eps, full_pe = built
                asof = close.index[-1]
                if pd.notna(eps.iloc[-1]) and eps.iloc[-1] <= 0:
                    with head:
                        theme.panel_header(title, subtitle)
                    _negative_eps_message(ticker, eps.iloc[-1])
                    theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, caveat=PE_CMP_CAVEAT + " " + PE_CAVEAT)
                    return
                cutoff = _pe_cutoff(lookback_years)
                lines[ticker] = full_pe.loc[full_pe.index >= cutoff]
                if show_sector or show_sp:
                    lines.update(_pe_group_lines(universe, sp_prices, ticker, cutoff, show_sector, show_sp, notes))

        stock_pe = lines.get(ticker, pd.Series(dtype=float))
        if len(stock_pe) >= 8:
            others = {name: float(s.iloc[-1]) for name, s in lines.items() if name != ticker and not s.empty}
            sm = summarise_pe_comparison(ticker, float(stock_pe.iloc[-1]), others, asof)
        with head:
            theme.panel_header(title, subtitle, (sm.text if sm else NO_SUMMARY) if len(stock_pe) else "")

        if len(stock_pe) >= 8:
            dashed = [name for name in lines if name == PE_SP500]
            theme.render(theme.line_fig(lines, dashed=dashed, unit=PE_UNIT, decimals=1), key="ntm_pe_cmp")
        elif not ticker_of:
            st.info("No stock prices available.")
        elif not ticker:
            st.info("Select a stock to plot.")
        else:
            st.info(f"Not enough EPS history to compute a forward P/E for {ticker} in this window.")
        theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, stale=bool(sm and sm.stale),
                           caveat=" ".join([*notes, PE_CMP_CAVEAT, PE_CAVEAT]))
        if len(stock_pe) >= 8:
            with st.expander("Raw data"):
                st.dataframe(_raw_table(pd.concat(lines, axis=1), "", 1), width="stretch")


def _pe_sector_name(universe: pd.DataFrame, ticker: str) -> str | None:
    sector = universe.set_index("ticker")["sector"].get(ticker)
    return sector if isinstance(sector, str) else None


def _pe_group_lines(universe: pd.DataFrame, prices: pd.DataFrame, ticker: str, cutoff: pd.Timestamp,
                    show_sector: bool, show_sp: bool, notes: list[str]) -> dict[str, pd.Series]:
    """Sector and S&P 500 average NTM P/E lines inside the window (whichever are ticked). Adds footer notes."""
    eps_all, _, eps_notice = data.load_ntm_eps()
    shares, shares_notice = data.load_shares()
    _warn_all([n for n in (eps_notice, shares_notice) if n])
    if eps_all.empty or shares.empty:
        notes.append("Sector and S&P 500 averages are unavailable (no EPS or share counts saved).")
        return {}
    window = prices.loc[prices.index >= cutoff]
    eps_w = eps_all.reindex(window.index)
    groups: dict[str, list[str]] = {}
    sector = _pe_sector_name(universe, ticker)
    if show_sector:
        if sector:
            groups[sector] = universe.loc[universe["sector"] == sector, "ticker"].tolist()
        else:
            notes.append(f"No GICS sector for {ticker}.")
    if show_sp:
        groups[PE_SP500] = universe["ticker"].tolist()
    out = {}
    for name, members in groups.items():
        grp = valuation.group_ntm_pe(window, eps_w, shares.reindex(members).dropna())
        grp = grp[grp.index.isin(window.index)]
        if grp.empty:
            continue
        out[name] = grp["pe"]
        notes.append(f"{name} average counts {int(grp['n'].iloc[-1])} of {len(members)} stocks on "
                     f"{grp.index[-1]:%d %b %Y}.")
    if no_eps := sorted(set(universe["ticker"]) - set(eps_all.columns)):
        shown = ", ".join(no_eps[:8]) + (f" and {len(no_eps) - 8} more" if len(no_eps) > 8 else "")
        notes.append(f"Left out (no usable EPS history from Yahoo): {shown}.")
    return out


@st.fragment
def render_ntm_pe_row(lookback_years: int) -> None:
    """Forward P/E history (left) and its comparison with the sector and S&P 500 (right), side by side. One fragment,
    so choosing a stock on the left updates the right one, which follows it by default."""
    universe, prices, ticker_of, added = _stock_options("close", [("ntm_pe_sel", False), ("ntm_cmp_sel", False)])
    sp_prices, _ = data.load_prices("close")  # its notice was shown by _stock_options
    left, right = st.columns(2)
    with left:
        ticker = _ntm_pe_history_panel(lookback_years, prices, ticker_of)
    with right:
        _ntm_pe_compare_panel(lookback_years, universe, prices, sp_prices, ticker_of, ticker)
    if added:  # a new ticker: rerun the page so every other stock box offers it too
        st.rerun(scope="app")


SCREEN_HEIGHT = 600                    # px; about 16 rows, the rest scroll inside the table
SCREEN_SHADE_CAP = 0.95                # shading saturates at this quantile of each column, so outliers stay readable


NON_SP = "Non-S&P 500"                 # sector cell (and filter option) for added tickers in the P/E screen


def _added_screen_rows(sp_prices: pd.DataFrame, eps_all: pd.DataFrame, shares: pd.Series, cutoff: pd.Timestamp,
                       notes: list[str]) -> pd.DataFrame:
    """P/E screen rows for the added (non-S&P 500) tickers, in the same columns as valuation.pe_screen plus name and
    sector. Each uses its own closes and EPS; market cap is converted to USD at the latest Yahoo FX rate so the column
    sorts with the S&P rows; sector P/E and the premium columns use the S&P 500 average (the default universe). Adds
    footer notes."""
    added = data.added_tickers()
    close_all, _ = data.load_added_prices("close")  # its notice was shown by _stock_options or the panel above
    if not added or close_all.empty:
        return pd.DataFrame()
    sp_line = pd.Series(dtype=float)  # S&P 500 P/E history: sector P/E and premium comparison for added tickers
    if not (sp_prices.empty or eps_all.empty or shares.empty):
        sp_w = sp_prices.loc[sp_prices.index >= cutoff - pd.Timedelta(days=14)]  # margin to carry onto earlier dates
        sp_line = valuation.group_ntm_pe(sp_w, eps_all.reindex(sp_w.index), shares)["pe"]
    rows, no_fx, mixed = [], [], []
    for t, meta in sorted(added.items()):
        close = _pe_close(close_all, t)  # major unit (pence -> pounds), so P/E and the FX rate both use it
        if close.empty:
            continue
        with st.spinner(f"Loading {t} EPS…"):
            reported, estimates, lag_days, notice = data.load_eps(t)
        eps = valuation.ntm_eps(valuation.build_quarters(reported, estimates, lag_days, _gap_days(t)), close.index) \
            if not reported.empty else pd.Series(float("nan"), index=close.index)
        one = valuation.pe_screen(close.to_frame(t), eps.to_frame(t), pd.Series({t: meta.get("shares")}, dtype=float),
                                  pd.Series(dtype=object), cutoff, fallback_pe=sp_line)
        fx = data.fx_to_usd(data.major_currency(meta.get("currency"))[0])
        if fx is None:
            no_fx.append(t)
        if _eps_currency_note(t):
            mixed.append(f"{t} ({meta['financial_currency']} vs {data.major_currency(meta['currency'])[0]})")
        one["market_cap"] = one["market_cap"] * fx if fx is not None else float("nan")
        rows.append(one.assign(name=meta.get("name") or t, sector=NON_SP))
    if not rows:
        return pd.DataFrame()
    notes.append(f"{NON_SP} rows are tickers added from Yahoo Finance: market cap is converted to USD at the latest "
                 "Yahoo FX rate, and Sector P/E and the sector premium columns use the S&P 500 average (its last "
                 "value on or before each of the stock's dates).")
    if no_fx:
        notes.append(f"No FX rate for: {', '.join(no_fx)}, so market cap shows —.")
    if mixed:
        notes.append(f"EPS used unconverted where Yahoo's statement currency differs from the price currency, so the "
                     f"P/E may be off by the exchange rate: {', '.join(mixed)}.")
    return pd.concat(rows)


def _pe_screen_view(universe: pd.DataFrame, table: pd.DataFrame, query: str, sectors_picked: list[str],
                    added_rows: pd.DataFrame = pd.DataFrame()) -> pd.DataFrame:
    """Rows matching the search box and sector filter, largest market cap first, in the table's own units."""
    rows = universe.set_index("ticker")[["name", "sector"]].join(table, how="inner")
    if not added_rows.empty:
        rows = pd.concat([rows, added_rows.loc[~added_rows.index.isin(rows.index), rows.columns]])
    if sectors_picked:
        rows = rows[rows["sector"].isin(sectors_picked)]
    if query := query.strip():
        hit = rows.index.str.contains(query, case=False, regex=False) | \
            rows["name"].str.contains(query, case=False, regex=False)
        rows = rows[hit]
    rows["market_cap"] = rows["market_cap"] / 1e9
    return rows.sort_values("market_cap", ascending=False, na_position="last")


def _screen_targets(view: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, list[str], bool]:
    """Adds price (last completed close, listing currency), target (Yahoo's mean analyst target, from the saved
    snapshot: no Yahoo calls) and upside (% from price to target) to the screen rows. Returns the rows, footer notes
    and whether the snapshot is STALE (its usual date over data.TARGETS_STALE_DAYS old)."""
    snap = data.load_targets_snapshot()
    added = data.added_tickers()
    added_close, _ = data.load_added_prices("close")  # its notice was shown above
    price, target = {}, {}
    for t in view.index:
        src = prices if t in prices else added_close
        close = src[t].dropna() if t in src else pd.Series(dtype=float)
        price[t] = float(close.iloc[-1]) if not close.empty else float("nan")
        entry = snap.get(t) or {}
        cur = added[t].get("currency") if t in added else "USD"
        same = entry.get("currency") in (None, cur)   # compare like with like: no FX conversion
        target[t] = entry.get("targets", {}).get("mean", float("nan")) if same else float("nan")
    out = view.assign(price=pd.Series(price), target=pd.Series(target))
    out["upside"] = (out["target"] / out["price"] - 1) * 100
    dates = pd.Series([snap[t]["saved"] for t in view.index if t in snap and snap[t].get("saved")])
    notes, stale = [], False
    if dates.empty:
        notes.append("No analyst target snapshot yet: Target and Upside show —.")
    else:
        usual = dt.date.fromisoformat(dates.mode().iloc[0])
        stale = (dt.date.today() - usual).days > data.TARGETS_STALE_DAYS
        notes.append(f"Target = Yahoo's mean analyst target (consensus, no date per analyst), saved {usual:%d %b %Y} "
                     "with the weekly share-count download; Upside = target / last close − 1. Price and target are in "
                     "the listing's currency. It is the analysts' view, not this dashboard's. — where Yahoo lists no "
                     "target or no snapshot exists.")
    return out, notes, stale


def _pe_screen_styler(view: pd.DataFrame, cols: dict[str, str]):
    """Display frame (company, ticker, then numbers in the order the owner asked for) with house number formats.
    Shaded, each on the rows shown: P/E σ, premium σ and the moving-average gaps (below = up colour) and upside
    (above = up colour). Sorting in the table uses the underlying numbers, not the formatted text."""
    shown = view.reset_index().rename(columns=cols)[list(cols.values())]
    ma_cols = [cols[f"ma_{n}"] for n in valuation.MA_WINDOWS]
    formats = {cols["market_cap"]: (1, False), cols["pe"]: (1, False), cols["pe_z"]: (1, True),
               cols["sector_pe"]: (1, False), cols["prem_avg"]: (1, True), cols["prem_now"]: (1, True),
               cols["prem_z"]: (1, True), cols["price"]: (2, False), cols["target"]: (2, False),
               cols["upside"]: (1, True), **{c: (1, True) for c in ma_cols}}
    styler = shown.style.format({c: (lambda v, d=d: theme.fmt_signed(v, d)) if signed else
                                 (lambda v, d=d: theme.fmt_num(v, d)) for c, (d, signed) in formats.items()},
                                na_rep=theme.MISSING)
    styler = theme.shade_signed(styler, [cols["pe_z"], cols["prem_z"], *ma_cols], good_when="down",
                                cap_quantile=SCREEN_SHADE_CAP)
    return theme.shade_signed(styler, [cols["upside"]], good_when="up", cap_quantile=SCREEN_SHADE_CAP)


def _pe_screen_csv(view: pd.DataFrame, cols: dict[str, str]) -> bytes:
    """The rows shown (filters applied), rounded to 4 decimals."""
    return view.reset_index().rename(columns=cols)[list(cols.values())].round(4).to_csv(index=False).encode("utf-8")


def _pe_screen_add_button(query: str, universe: pd.DataFrame, view: pd.DataFrame) -> None:
    """Under the screen's search box: when the text looks like a Yahoo ticker that is neither in the S&P 500 nor added
    yet, offer to add it (it then shows as a row and in every stock box). Shown only when no row matches, or the text
    has an exchange suffix or digits, so filtering by a company name does not keep offering a download."""
    q = query.strip()
    candidate = data.normalise_ticker(q, set(universe["ticker"])) if q else None
    if not candidate or candidate in set(universe["ticker"]) or candidate in data.added_tickers():
        return
    if not (view.empty or "." in q or any(ch.isdigit() for ch in q)):
        return
    if st.button(f"Add {candidate} from Yahoo Finance", icon=":material/add:", key="pe_screen_add",
                 help="Downloads this ticker's prices and EPS and adds it to the table and to every stock box."):
        with st.spinner(f"Loading {candidate} from Yahoo Finance…"):
            _, error = data.add_ticker(candidate)
        if error:
            st.warning(error)
        else:
            st.rerun(scope="app")


@st.fragment
def render_pe_screen_panel(lookback_years: int) -> None:
    """Every S&P 500 stock, plus any added tickers, in one sortable table: market cap, NTM P/E, how far that P/E is
    from its own average over the sidebar lookback (σ, as on the history chart's bands), the GICS sector's market-cap
    weighted NTM P/E (S&P 500 average for added tickers), the stock's premium to it (average, today, σ), the analyst
    target and upside, and the distance from the 60, 100 and 200-day moving averages. Search and sector filter above;
    a ticker not in the table can be added from the search box; click a column header to sort it either way."""
    title = "Stock screener"
    window = f"{lookback_years}Y"
    subtitle = (f"S&P 500 and added tickers · USD bn, x and % (see column headers) · daily · σ and averages over the "
                f"{window} window")
    with st.container(border=True):
        head = st.container()  # filled after the controls so the takeaway describes the rows shown
        universe, universe_notice = data.load_universe()
        prices, price_notice = data.load_prices("close")
        _, added_notice = data.load_added_prices("close")
        eps_all, _, eps_notice = data.load_ntm_eps()
        shares, shares_notice = data.load_shares()
        _warn_all([n for n in (universe_notice, price_notice, added_notice, eps_notice, shares_notice) if n])

        col_search, col_sector = st.columns([2, 3])
        query = col_search.text_input("Search", key="pe_screen_q", placeholder="Ticker or company name",
                                      help="Narrows the table to tickers or company names containing this text. A "
                                           "Yahoo Finance ticker not in the table (e.g. D05.SI) can be added.")
        sector_names = sorted(universe["sector"].dropna().unique()) if not universe.empty else []
        if data.added_tickers():
            sector_names.append(NON_SP)
        picked = col_sector.multiselect("Sectors", sector_names, key="pe_screen_sectors", placeholder="All sectors",
                                        help="GICS sectors to show. Leave empty to show every sector.")

        available = not (prices.empty or universe.empty)
        sm, view, asof, added_notes, target_notes, targets_stale = None, pd.DataFrame(), None, [], [], False
        if available:
            asof = prices.index[-1]
            sector_of = universe.set_index("ticker")["sector"]
            cutoff = _pe_cutoff(lookback_years)
            table = valuation.pe_screen(prices, eps_all, shares, sector_of, cutoff)
            added_rows = _added_screen_rows(prices, eps_all, shares, cutoff, added_notes)
            view = _pe_screen_view(universe, table, query, picked, added_rows)
            view, target_notes, targets_stale = _screen_targets(view, prices)
            sm = summarise_pe_screen(view["pe_z"], window, asof)
        with head:
            theme.panel_header(title, subtitle, sm.text if sm else "")
        if available:
            _pe_screen_add_button(query, universe, view)

        cols = {"name": "Company", "ticker": "Ticker", "market_cap": "Market cap (USD bn)", "pe": "Fwd P/E (x)",
                "pe_z": f"P/E σ vs {window} avg", "sector": "Sector", "sector_pe": "Sector P/E (x)",
                "prem_avg": f"Avg sector prem. {window} (%)", "prem_now": "Sector prem. today (%)",
                "prem_z": f"Prem. σ vs {window} avg", "price": "Price", "target": "Target (mean)",
                "upside": "Upside (%)", **{f"ma_{n}": f"vs {n}D MA (%)" for n in valuation.MA_WINDOWS}}
        if not view.empty:
            st.dataframe(_pe_screen_styler(view, cols), hide_index=True, width="stretch", height=SCREEN_HEIGHT,
                         column_config={"Company": st.column_config.TextColumn(pinned=True)})
            st.download_button("Download table (CSV)", _pe_screen_csv(view, cols), file_name="stock_screener.csv",
                               mime="text/csv", icon=":material/download:", key="pe_screen_download",
                               help="The rows shown (search and sector filter applied).")
        elif available:
            st.info("No stocks match the search and sector filter.")
        else:
            st.info("No stock prices available.")

        notes = [
            "Click a column header to sort; click again to reverse.",
            f"σ = distance of today's forward P/E from its {window} mean, in standard deviations over the same window, "
            "as on the Forward P/E chart's bands (needs at least 8 days of positive NTM EPS). Stocks with negative "
            "or missing NTM EPS show — for P/E and σ.",
            "Sector P/E = GICS sector's total market cap over total NTM earnings (negative EPS excluded), as on the "
            "comparison chart. Market cap = today's shares outstanding per share class × last completed close.",
            f"Sector premium = stock P/E / Sector P/E − 1, on each day in the {window} window with both positive "
            "(negative = discount). Avg = its mean over the window; today = the latest day; Prem. σ = today's premium "
            "minus that mean, in standard deviations over the same window (needs at least 8 days). A stock is part "
            "of its own sector average, so a large company's premium is pulled towards zero. Days when NTM EPS was "
            "near zero give very high P/Es, which can inflate the average premium to hundreds or thousands of % "
            "(and widen σ); the average is a plain mean, not trimmed.",
            "vs 60D / 100D / 200D MA = last close / mean of the stock's last 60, 100 or 200 daily closes − 1 "
            "(split-adjusted close, not dividend-adjusted); — with fewer closes than that.",
            "Shading, on the rows shown: P/E σ, Prem. σ and the MA columns use the up colour when below (P/E below "
            "its own average, premium below its usual level, price below its moving average); Upside uses the up "
            "colour when the target is above the price. Each column saturates at its 95th percentile.",
            *added_notes,
            *target_notes,
        ]
        theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, stale=bool(sm and sm.stale) or targets_stale,
                           caveat=" ".join([*notes, PE_CAVEAT]))


# ---------- Equities: rolling correlation panel ----------

CORR_DEFAULTS = ("AAPL", "MSFT")
CORR_TAKEAWAY = TakeawaySpec("", unit="", decimals=2, freq="D", change_days=30, change_label="1M")
CORR_CAVEAT = ("Pearson correlation of daily total returns (split- and dividend-adjusted close, each in its listing's "
               "currency), on days both stocks have a close. Mean, ±1σ and ±2σ bands are over the loaded lookback "
               "window. Default universe: today's S&P 500 constituents, plus any added tickers.")
SESSION_NOTE = ("Markets close at different times, so a same-day return in an earlier market misses moves after its "
                "close; daily correlations between different sessions (US, Europe, Asia) are understated.")


@st.fragment
def render_correlation_panel(lookback_years: int) -> None:
    """Rolling correlation of two S&P 500 stocks' daily returns over the sidebar lookback, against its window mean
    and ±1σ / ±2σ bands (DESIGN.md 6.2 "rich or cheap vs history"). Each stock has its own search box; pills set the
    rolling window."""
    title = "Rolling correlation"
    subtitle = "Correlation coefficient (−1 to +1) of daily total returns · daily"
    st.session_state.setdefault("corr_window", correlation.DEFAULT_WINDOW)
    with st.container(border=True):
        head = st.container()  # filled after the controls so the takeaway can use the loaded data
        _, prices, ticker_of, added = _stock_options("adj_close", [("corr_a", False), ("corr_b", False)])

        col_a, col_b = st.columns(2)  # pills on their own row: beside the boxes they squeeze them at half width
        with col_a:
            a = _pe_select("corr_a", ticker_of, default=CORR_DEFAULTS[0], label="First stock")
        with col_b:
            b = _pe_select("corr_b", ticker_of, default=CORR_DEFAULTS[1], label="Second stock")
        st.segmented_control(
            "Rolling window", tuple(correlation.WINDOWS), key="corr_window", on_change=_keep_selection,
            args=("corr_window", correlation.DEFAULT_WINDOW),
            help="Trading days in each correlation: "
                 + ", ".join(f"{k} = {v}" for k, v in correlation.WINDOWS.items()) + ".")
        window_label = st.session_state["corr_window"]
        n_days = correlation.WINDOWS[window_label]

        corr, sm, notes = pd.Series(dtype=float), None, []
        if a and b and a != b:
            # Computed on the full saved history, then cut, so the line starts at the window's left edge.
            full = correlation.rolling_correlation(prices[a], prices[b], n_days)
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=365 * lookback_years)  # as data.prices_window
            corr = full.loc[full.index >= cutoff]
            if len(corr) >= 8:
                sm = summarise(corr, replace(CORR_TAKEAWAY, label=f"{a}–{b} {window_label} correlation"))
                if corr.index[0] > cutoff + pd.Timedelta(days=30):
                    notes.append(f"The chart starts {corr.index[0]:%d %b %Y}, the first date with {n_days} trading "
                                 "days of shared price history.")
                mu, sd = float(corr.mean()), float(corr.std())
                if mu + 2 * sd > 1 or mu - 2 * sd < -1:
                    notes.append("The ±2σ band extends past the possible range of −1 to +1; it is a statistical band, "
                                 "not a bound.")
                if len(_matrix_session([a, b])) > 1:
                    notes.append(SESSION_NOTE)
        with head:
            theme.panel_header(title, subtitle, (sm.text if sm else NO_SUMMARY) if len(corr) else "")

        if len(corr) >= 8:
            theme.render(theme.band_fig(f"{a}–{b}", corr, k=(1, 2), decimals=2, height=400), key="corr")
        elif not ticker_of:
            st.info("No stock prices available.")
        elif not (a and b):
            st.info("Select two stocks to compare.")
        elif a == b:
            st.info("Choose two different stocks; a stock's correlation with itself is always 1.")
        else:
            st.info(f"Not enough shared price history for a {window_label} rolling correlation of {a} and {b} "
                    "in this window.")
        theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], corr.index[-1] if len(corr) else None,
                           stale=bool(sm and sm.stale), caveat=" ".join([*notes, CORR_CAVEAT]))
        if len(corr) >= 8:
            with st.expander("Raw data"):
                raw = pd.DataFrame({f"{t} adj. close ({data.ticker_currency(t)})": prices[t] for t in (a, b)}
                                   ).reindex(corr.index)
                raw[f"{window_label} correlation"] = corr
                st.dataframe(_raw_table(raw, "", 2), width="stretch")
    if added:  # a new ticker: rerun the page so every other stock box offers it too
        st.rerun(scope="app")


MATRIX_MAX = 8                         # stocks + countries combined: cells stay readable in a half-width panel
MATRIX_STOCKS = ("AAPL", "MSFT", "NVDA", "JPM", "XOM")
MATRIX_COUNTRIES = ("US", "Europe")
MATRIX_CAVEAT = ("Pearson correlation of daily returns, per pair on the days both have a close. Stocks: split- and "
                 "dividend-adjusted close (total return, in the listing's currency). Country indices: price return in "
                 "local currency. Colours: near −1 uses the up colour, near +1 the down colour (see the sidebar "
                 "convention).")
_REGION = {"America": "US", "Europe": "Europe", "Africa": "Europe", "Atlantic": "Europe", "Asia": "Asia",
           "Australia": "Asia", "Pacific": "Asia"}  # time-zone prefix -> trading session


def _matrix_session(names: list[str]) -> set[str]:
    """Trading sessions (US, Europe, Asia) covered by the chosen countries and stocks; each stock trades on its own
    exchange's clock (S&P 500 stocks in the US session)."""
    tz_of = {c.name: c.tz for c in countries.COUNTRIES}
    return {_REGION.get((tz_of.get(n) or data.ticker_tz(n)).split("/")[0], "US") for n in names}


def _fit_matrix_limit(key: str, other: str) -> None:
    """One limit shared by the two matrix boxes. A per-box max_selections that depends on the other box would
    change the widget's identity and clear it, so the box just edited is trimmed back instead."""
    room = MATRIX_MAX - len(st.session_state.get(other, []))
    if len(st.session_state[key]) > room:
        st.session_state[key] = st.session_state[key][:max(room, 0)]
        st.session_state["corr_mx_trimmed"] = True


@st.fragment
def render_corr_matrix_panel() -> None:
    """Correlation of daily returns between any mix of S&P 500 stocks and country indices over a timeframe, as a
    heatmap with the value printed in each cell (DESIGN.md 6.2). Stocks and countries have separate search boxes that
    share one limit (MATRIX_MAX); timeframe pills sit beside them."""
    title = "Correlation matrix"
    subtitle = "Correlation coefficient (−1 to +1) of daily returns · daily"
    with st.container(border=True):
        head = st.container()  # filled after the controls so the takeaway can use the loaded data
        _, stock_px, ticker_of, added = _stock_options("adj_close", [("corr_mx_stocks", True)])
        index_px, index_notice = data.load_index_prices()
        _warn_all([index_notice] if index_notice else [])
        label_of = {t: label for label, t in ticker_of.items()}
        country_names = [n for n in countries.NAMES if n in index_px.columns]

        st.session_state.setdefault("corr_mx_stocks", [label_of[t] for t in MATRIX_STOCKS if t in label_of])
        st.session_state.setdefault("corr_mx_countries", [n for n in MATRIX_COUNTRIES if n in country_names])
        st.session_state.setdefault("corr_mx_tf", correlation.DEFAULT_MATRIX_TIMEFRAME)
        limit_help = f"Up to {MATRIX_MAX} stocks and countries combined."

        col_s, col_c = st.columns(2)  # pills on their own row below, as in the rolling correlation panel
        picked = col_s.multiselect("Stocks", list(ticker_of), key="corr_mx_stocks", max_selections=MATRIX_MAX,
                                   on_change=_fit_matrix_limit, args=("corr_mx_stocks", "corr_mx_countries"),
                                   accept_new_options=True, placeholder="Ticker or company name",
                                   help=f"{SEARCH_HELP} {limit_help}")
        picked_c = col_c.multiselect("Country indices", country_names, key="corr_mx_countries",
                                     on_change=_fit_matrix_limit, args=("corr_mx_countries", "corr_mx_stocks"),
                                     placeholder="Country", help=f"One benchmark index per country. {limit_help}")
        if st.session_state.pop("corr_mx_trimmed", False):
            st.caption(f"Limit reached: up to {MATRIX_MAX} stocks and countries combined. "
                       "Remove one to add another.")
        st.segmented_control("Timeframe", tuple(correlation.MATRIX_TIMEFRAMES), key="corr_mx_tf",
                                 on_change=_keep_selection, args=("corr_mx_tf", correlation.DEFAULT_MATRIX_TIMEFRAME))
        timeframe = st.session_state["corr_mx_tf"]

        chosen = [ticker_of[label] for label in picked if label in ticker_of] + list(picked_c)
        frame = pd.concat({t: stock_px[t] for t in chosen if t in stock_px} |
                          {n: index_px[n] for n in picked_c}, axis=1, sort=True)[chosen] if chosen else pd.DataFrame()
        corr, counts, sm, notes, asof = pd.DataFrame(), pd.DataFrame(), None, [], None
        if len(chosen) >= 2:
            corr, counts = correlation.matrix(frame, timeframe)
            asof = frame.dropna(how="all").index[-1]
            sm = summarise_corr_matrix(corr, timeframe, asof)
            thin = [f"{a}–{b}" for i, a in enumerate(chosen) for b in chosen[i + 1:] if pd.isna(corr.loc[a, b])]
            if thin:
                notes.append(f"Fewer than {correlation.MIN_PAIRED} shared daily returns in the timeframe, left blank: "
                             f"{', '.join(thin)}.")
            if len(_matrix_session(chosen)) > 1:
                notes.append(SESSION_NOTE)
            if picked_c:
                used = "; ".join(f"{c.name} = {c.index}" for c in countries.COUNTRIES if c.name in picked_c)
                notes.append(f"Indices: {used}.")
        with head:
            theme.panel_header(title, subtitle, (sm.text if sm else NO_SUMMARY) if len(chosen) >= 2 else "")

        if sm:
            theme.render(theme.corr_heatmap_fig(corr, counts=counts, height=400), key="corr_matrix")
        elif not (ticker_of or country_names):
            st.info("No prices available.")
        elif len(chosen) < 2:
            st.info("Select at least two stocks or countries.")
        else:
            st.info(f"Not enough shared daily returns in the {timeframe} timeframe for any pair.")
        theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, stale=bool(sm and sm.stale),
                           caveat=" ".join([*notes, MATRIX_CAVEAT]))
        if sm:
            with st.expander("Raw data"):
                st.dataframe(corr.style.format(lambda v: theme.fmt_num(v, 2), na_rep=theme.MISSING), width="stretch")
                st.caption("Paired daily returns per cell")
                st.dataframe(counts, width="stretch")
    if added:  # a new ticker: rerun the page so every other stock box offers it too
        st.rerun(scope="app")


# ---------- Equities: sector return panels ----------

MAX_SECTOR_LINES = 6                   # categorical palette limit (DESIGN.md 3.2); the dashed S&P 500 line is extra
SECTOR_CAVEAT = ("Price return only (no dividends). Weights are today's shares outstanding times the price on the "
                 "start date, not the S&P's float-adjusted weights, so results differ slightly from official "
                 "S&P sector indices.")
ALL_TIMEFRAMES = "All"                 # pill that selects every timeframe in the sector table
# Short GICS names for the space-tight parts (chart legend, table rows); pickers and takeaways keep full names.
SECTOR_SHORT = {
    "Communication Services": "Comm. Services", "Consumer Discretionary": "Cons. Disc.",
    "Consumer Staples": "Cons. Staples", "Information Technology": "Info. Tech.",
}


def _sector_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, bool]:
    """Load the S&P 500 list, saved closes and share counts (warning on any fallback). Returns
    (universe, prices, shares, sector of each ticker, whether there is anything to compute from)."""
    universe, universe_notice = data.load_universe()
    prices, price_notice = data.load_prices("close")
    shares, shares_notice = data.load_shares()
    _warn_all([n for n in (universe_notice, price_notice, shares_notice) if n])
    sector_of = universe.set_index("ticker")["sector"]
    return universe, prices, shares, sector_of, not (prices.empty or shares.empty or sector_of.empty)


def _sector_footer(universe: pd.DataFrame, prices: pd.DataFrame, shares: pd.Series, asof, stale: bool,
                   *notes: str) -> None:
    left_out = sorted(set(universe["ticker"]) - (set(shares.index) & set(prices.columns)))
    excluded = f"Left out (no share count or price): {', '.join(left_out)}." if left_out else None
    theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, stale=stale,
                       caveat=" ".join(filter(None, [*notes, SECTOR_CAVEAT, excluded])))


def _mark_sector_custom() -> None:
    st.session_state["sector_line_custom"] = True


def _reset_sector_lines() -> None:
    st.session_state["sector_line_custom"] = False


@st.fragment
def render_sector_line_panel() -> None:
    """Cumulative market-cap weighted return of the chosen sectors over a timeframe (1M to 12M), with the
    S&P 500 as a dashed reference. The sector picker follows the timeframe's 3 best and 3 worst sectors
    until the user edits it by hand; Reset returns to that default."""
    title, subtitle = "Sector returns", "Cumulative return, market-cap weighted · % · daily"
    st.session_state.setdefault("sector_line_tf", "3M")
    with st.container(border=True):
        head = st.container()  # filled after the controls so the takeaway can use the loaded data
        universe, prices, shares, sector_of, available = _sector_inputs()
        st.segmented_control("Timeframe", sectors.LINE_TIMEFRAMES, key="sector_line_tf",
                             on_change=_keep_selection, args=("sector_line_tf", "3M"))
        timeframe = st.session_state["sector_line_tf"]
        path = sectors.paths(prices, shares, sector_of, timeframe) if available else pd.DataFrame()

        sm, chosen, plot = None, [], {}
        if not path.empty:
            order = [s for s in sectors.market_caps(prices, shares, sector_of).index if s in path.columns]
            final = path.iloc[-1]
            ranked = final[order].sort_values(ascending=False)
            leaders = list(dict.fromkeys([*ranked.index[:3], *ranked.index[-3:]]))
            if not st.session_state.get("sector_line_custom"):
                st.session_state["sector_line_sel"] = [s for s in order if s in leaders]
            picked = st.multiselect(
                "Sectors", order, key="sector_line_sel", max_selections=MAX_SECTOR_LINES,
                on_change=_mark_sector_custom, placeholder="Choose sectors",
                help=f"Up to {MAX_SECTOR_LINES} sectors. Starts with the 3 best and 3 worst over the timeframe.")
            if st.session_state.get("sector_line_custom"):
                st.button("Reset to leaders and laggards", key="sector_line_reset", on_click=_reset_sector_lines,
                          type="tertiary", icon=":material/restart_alt:")
            chosen = [s for s in order if s in picked]
            plot = {SECTOR_SHORT.get(s, s): path[s] for s in chosen}
            plot[sectors.BENCHMARK] = path[sectors.BENCHMARK]
            sm = summarise_sector_returns({s: final[s] for s in chosen}, timeframe, final[sectors.BENCHMARK],
                                          path.index[-1])
        with head:
            theme.panel_header(title, subtitle, sm.text if sm else "")

        if chosen:
            # 400px: seven legend rows take a third of a 300px chart, so the plot would be too short to read.
            theme.render(theme.line_fig(plot, dashed=[sectors.BENCHMARK], unit="%", decimals=1, baseline=0,
                                        height=400), key="sector_line")
        else:
            st.info("Select at least one sector to plot." if not path.empty else "No sector data available.")
        _sector_footer(universe, prices, shares, None if path.empty else path.index[-1], bool(sm and sm.stale))
        if chosen:
            with st.expander("Raw data"):
                st.dataframe(_raw_table(pd.DataFrame(plot), "%", 1), width="stretch")


def _table_timeframes_changed(key: str, prev_key: str) -> None:
    """Keep the "All" pill and the individual timeframe pills of a returns table consistent with each other.
    `key` holds the pills' selection, `prev_key` the selection before this change. Callbacks run before the script
    body, so after a session reset (e.g. a reconnect on Streamlit Cloud) the keys may not exist yet: read with .get."""
    new = set(st.session_state.get(key) or [])
    prev = set(st.session_state.get(prev_key) or new)
    every = set(sectors.TIMEFRAMES)
    if ALL_TIMEFRAMES in new and ALL_TIMEFRAMES not in prev:      # All switched on: select everything
        new = every | {ALL_TIMEFRAMES}
    elif ALL_TIMEFRAMES not in new and ALL_TIMEFRAMES in prev:    # All switched off: clear the selection
        new = set()
    elif ALL_TIMEFRAMES in new and not every <= new:              # a timeframe was dropped: All no longer holds
        new.discard(ALL_TIMEFRAMES)
    elif ALL_TIMEFRAMES not in new and every <= new:              # every timeframe picked by hand: All holds
        new.add(ALL_TIMEFRAMES)
    st.session_state[key] = [o for o in (ALL_TIMEFRAMES, *sectors.TIMEFRAMES) if o in new]
    st.session_state[prev_key] = list(st.session_state[key])


@st.fragment
def render_sector_table_panel() -> None:
    """Every sector's market-cap weighted return over the chosen timeframes (1D to 12M), each column shaded
    on its own scale around zero so trends read at a glance."""
    title = "Sector returns by timeframe"
    subtitle = "Market-cap weighted return, % · daily · shaded per column"
    st.session_state.setdefault("sector_tbl_tf", [ALL_TIMEFRAMES, *sectors.TIMEFRAMES])
    st.session_state.setdefault("sector_tbl_prev", list(st.session_state["sector_tbl_tf"]))
    with st.container(border=True):
        head = st.container()
        universe, prices, shares, sector_of, available = _sector_inputs()
        st.pills("Timeframes", [ALL_TIMEFRAMES, *sectors.TIMEFRAMES], selection_mode="multi", key="sector_tbl_tf",
                 on_change=_table_timeframes_changed, args=("sector_tbl_tf", "sector_tbl_prev"),
                 help="Pick one or more timeframes, or All.")
        selected = [tf for tf in sectors.TIMEFRAMES if tf in (st.session_state["sector_tbl_tf"] or [])]

        sm, view = None, pd.DataFrame()
        if available and selected:
            table = sectors.returns_table(prices, shares, sector_of)
            rows = [*sectors.market_caps(prices, shares, sector_of).index, sectors.BENCHMARK]  # largest sector first
            view = table.loc[[r for r in rows if r in table.index], selected]
            sm = summarise_sector_table(view.drop(sectors.BENCHMARK), selected, prices.index[-1])
            view.index = view.index.map(lambda s: SECTOR_SHORT.get(s, s))
            view = view.rename_axis("Sector")
        with head:
            theme.panel_header(title, subtitle, sm.text if sm else "")

        if not view.empty:
            # No arrows and no "%" in the cells: the half-width panel is too narrow for six timeframes with them.
            # The sign, the shading and the "%" in the subtitle carry the meaning.
            st.dataframe(theme.signed_heatmap(view, unit="", arrows=False), width="stretch", height="content")
        else:
            st.info("Select at least one timeframe." if available else "No sector data available.")
        _sector_footer(universe, prices, shares, prices.index[-1] if available else None, bool(sm and sm.stale),
                       "Gains use the up colour and losses the down colour; the darkest cell in each column is "
                       "that column's largest move.")


# ---------- Equities: country return panels ----------

def _country_inputs() -> tuple[pd.DataFrame, bool]:
    """Load the saved country index closes (dates x country names), warning on any fallback."""
    prices, notice = data.load_index_prices()
    _warn_all([notice] if notice else [])
    return prices, not prices.empty


def _country_footer(asof, stale: bool, *notes: str) -> None:
    theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, stale=stale,
                       caveat=" ".join(filter(None, [*notes, countries.disclaimer()])))


@st.fragment
def render_country_line_panel() -> None:
    """Chosen countries' benchmark indices rebased to 100 on the start of a timeframe (1M to 12M), so different
    index levels compare as % change (DESIGN.md 6.2 "Relative performance")."""
    title = "Country returns"
    subtitle = f"Index, start of timeframe = {countries.REBASE_TO:g} · local-currency price return · daily"
    st.session_state.setdefault("country_line_tf", "3M")
    with st.container(border=True):
        head = st.container()  # filled after the controls so the takeaway can use the loaded data
        prices, available = _country_inputs()
        st.segmented_control("Timeframe", sectors.LINE_TIMEFRAMES, key="country_line_tf",
                             on_change=_keep_selection, args=("country_line_tf", "3M"))
        timeframe = st.session_state["country_line_tf"]
        path = countries.paths(prices, timeframe) if available else pd.DataFrame()

        picked = st.multiselect(
            "Countries", [n for n in countries.NAMES if n in prices.columns],
            default=[n for n in countries.DEFAULT_SELECTION if n in prices.columns],
            max_selections=MAX_SECTOR_LINES, key="country_line_sel", placeholder="Choose countries",
            help=f"Up to {MAX_SECTOR_LINES} countries. Each is one benchmark index (see the note below the chart).")
        chosen = [n for n in countries.NAMES if n in picked and n in path.columns]
        plot = {n: path[n] for n in chosen}
        sm = None
        if chosen:
            final = path.iloc[-1]
            sm = summarise_country_returns({n: final[n] - countries.REBASE_TO for n in chosen}, timeframe,
                                           path.index[-1])
        with head:
            theme.panel_header(title, subtitle, sm.text if sm else "")

        if chosen:
            theme.render(theme.line_fig(plot, decimals=1, baseline=countries.REBASE_TO), key="country_line")
        else:
            st.info("Select at least one country to plot." if not path.empty else "No country index data available.")
        _country_footer(None if path.empty else path.index[-1], bool(sm and sm.stale),
                        f"Every line is 100 on {path.index[0]:%d %b %Y}." if not path.empty else "")
        if chosen:
            with st.expander("Raw data"):
                st.dataframe(_raw_table(pd.DataFrame(plot), " index", 1), width="stretch")


def _country_raw_csv(table: pd.DataFrame) -> bytes:
    """Full returns table (every timeframe, all countries) with index name and ticker, for download."""
    info = {c.name: (c.index, c.ticker) for c in countries.COUNTRIES}
    raw = table.round(2)
    raw.columns = [f"{tf} return (%)" for tf in raw.columns]
    raw.insert(0, "Ticker", [info[n][1] for n in raw.index])
    raw.insert(0, "Index", [info[n][0] for n in raw.index])
    return raw.rename_axis("Country").to_csv().encode("utf-8")


@st.fragment
def render_country_table_panel() -> None:
    """Every country's index return over the chosen timeframes (1D to 12M), each column shaded on its own scale
    around zero, like the sector table; the full table can be downloaded as CSV."""
    title = "Country returns by timeframe"
    subtitle = "Index price return, local currency, % · daily · shaded per column"
    st.session_state.setdefault("country_tbl_tf", [ALL_TIMEFRAMES, *sectors.TIMEFRAMES])
    st.session_state.setdefault("country_tbl_prev", list(st.session_state["country_tbl_tf"]))
    with st.container(border=True):
        head = st.container()
        prices, available = _country_inputs()
        st.pills("Timeframes", [ALL_TIMEFRAMES, *sectors.TIMEFRAMES], selection_mode="multi", key="country_tbl_tf",
                 on_change=_table_timeframes_changed, args=("country_tbl_tf", "country_tbl_prev"),
                 help="Pick one or more timeframes, or All.")
        selected = [tf for tf in sectors.TIMEFRAMES if tf in (st.session_state["country_tbl_tf"] or [])]

        sm, view, table = None, pd.DataFrame(), pd.DataFrame()
        if available:
            table = countries.returns_table(prices)
        if available and selected:
            view = table[selected]
            sm = summarise_sector_table(view, selected, prices.index[-1], noun="countries")
            view = view.rename_axis("Country")
        with head:
            theme.panel_header(title, subtitle, sm.text if sm else "")

        if not view.empty:
            st.dataframe(theme.signed_heatmap(view, unit="", arrows=False), width="stretch", height="content")
        else:
            st.info("Select at least one timeframe." if available else "No country index data available.")
        if available:
            st.download_button("Download raw data (CSV)", _country_raw_csv(table), file_name="country_returns.csv",
                               mime="text/csv", icon=":material/download:", key="country_tbl_download",
                               help="Every country and timeframe, with the index name and Yahoo ticker.")
        _country_footer(prices.index[-1] if available else None, bool(sm and sm.stale),
                        "Gains use the up colour and losses the down colour; the darkest cell in each column is "
                        "that column's largest move.")


# ---------- TAA: Shiller CAPE panels ----------

CAPE_SOURCE = "Robert J. Shiller, Irrational Exuberance data (shillerdata.com); S&P 500 close and CPI for the live value"
CAPE_TAKEAWAY = TakeawaySpec("Shiller CAPE", unit="x", decimals=1, freq="M", change_days=28, change_label="1M")
CAPE_CPI = "CPIAUCNS"                  # the CPI Shiller uses: CPI-U, all items, not seasonally adjusted
CAPE_BREAKEVEN = "T10YIE"              # 10-year breakeven inflation, for the nominal line under the KPI
CAPE_HOW_TO_READ = (
    "- **Each dot is one month in history.** Across: the CAPE that month. Up: the real (after-inflation) total "
    "return, % a year, that US stocks then delivered over the following 10 years.\n"
    "- **The pattern.** Dots slope down: starting from a high CAPE, the next decade's returns have tended to be "
    "lower. The spread of dots shows how loose that tendency is; R² is the share of the variation the line explains.\n"
    "- **The amber line** is the fitted relationship; its equation is printed above the chart. *Linear* is a straight "
    "line in CAPE. *Earnings yield (1/CAPE)* fits a straight line in 1/CAPE, which draws as a curve that flattens at "
    "high CAPE levels.\n"
    "- **The diamond** is today's live CAPE, read off the line. Its height is the expected 10-year real return on the "
    "card above. The dotted vertical line marks today's CAPE among the historical dots: if few dots lie to its right, "
    "the line is being extended beyond the data, and the estimate is less reliable.\n"
    "- **Typical miss.** Actual 10-year returns sat about that far (one standard deviation) above or below the line in "
    "history, so read the card as the centre of a wide range, not a point forecast.\n"
    "- **The latest dot is 10 years old.** A month only gets a dot once its next 10 years are complete."
)


def _cape_live(shiller: pd.DataFrame, notes: list[str]) -> tuple[float, pd.Timestamp]:
    """Today's CAPE and its date: the latest S&P 500 close over Shiller's 10-year real earnings average, rolled
    forward with the newest CPI (FRED when it is at least as recent as Shiller's last month). Falls back to Shiller's
    latest monthly CAPE when no newer close is saved. Adds footer notes."""
    last = shiller.dropna(subset=["cape"])
    last_month, last_cape = last.index[-1], float(last["cape"].iloc[-1])
    index_px, index_notice = data.load_index_prices()
    cpi, cpi_notice = data.load_series(CAPE_CPI, 2)
    _warn_all([n for n in (index_notice, cpi_notice) if n])
    close = index_px["US"].dropna() if "US" in index_px else pd.Series(dtype=float)
    if close.empty or close.index[-1] < last_month:
        notes.append(f"No newer S&P 500 close is saved, so the value shown is Shiller's {last_month:%b %Y} CAPE.")
        return last_cape, last_month
    cpi = cpi.dropna()
    shiller_cpi = shiller["cpi"].dropna()
    if not cpi.empty and cpi.index[-1] >= shiller_cpi.index[-1]:
        cpi_now, cpi_note = float(cpi.iloc[-1]), f"FRED {CAPE_CPI} for {cpi.index[-1]:%b %Y}"
    else:
        cpi_now, cpi_note = float(shiller_cpi.iloc[-1]), f"Shiller's CPI for {shiller_cpi.index[-1]:%b %Y}"
    value = cape.live_cape(shiller, float(close.iloc[-1]), cpi_now)
    last_e = shiller["earnings"].dropna().index[-1]
    notes.append(f"Live CAPE = S&P 500 close on {close.index[-1]:%d %b %Y} over Shiller's 10-year average of real "
                 f"earnings (reported to {last_e:%b %Y}), restated with {cpi_note}. Shiller's monthly CAPE uses the "
                 "month's average price, so the two differ slightly.")
    return value, close.index[-1]


def _cape_sample_pills(key: str) -> str:
    st.session_state.setdefault(key, cape.DEFAULT_SAMPLE)
    st.segmented_control("Period", tuple(cape.SAMPLES), key=key, on_change=_keep_selection,
                         args=(key, cape.DEFAULT_SAMPLE))
    return st.session_state[key]


def _cape_band_panel(shiller: pd.DataFrame, cape_now: float, live_date: pd.Timestamp, live_notes: list[str]) -> None:
    """Shiller CAPE history against its mean and ±1σ / ±2σ bands over the chosen period, with today's live value
    as a KPI tile and as the line's last point."""
    title = "Shiller CAPE"
    subtitle = "x · S&P 500 real price over 10-year average real earnings · monthly, live last point"
    with st.container(border=True):
        head = st.container()
        kpi = st.container()  # the KPI sits above the pills, filled once the period's mean is known
        period = _cape_sample_pills("cape_band_sample")
        monthly = cape.sample(shiller, period)["cape"].dropna()
        series = monthly.copy()
        if live_date > series.index[-1]:
            series.loc[live_date] = cape_now
        mean, sd = float(series.mean()), float(series.std())
        sm = summarise(series, CAPE_TAKEAWAY)
        with head:
            theme.panel_header(title, subtitle, sm.text if sm else NO_SUMMARY)
        with kpi:
            theme.metric_tile(f"CAPE, live · vs {period.lower()} mean", cape_now, cape_now - mean, decimals=1,
                              unit="x", help=f"{period} mean {theme.fmt_num(mean, 1, 'x')}; today is "
                                             f"{theme.fmt_signed((cape_now - mean) / sd, 1)}σ from it.")
        fig = theme.band_fig("Shiller CAPE", series, k=(1, 2), unit="x", decimals=1, height=400)
        fig.update_xaxes(tickformat="%Y")  # 150 years: "Jan 81" would be ambiguous
        theme.render(fig, key="cape_band")
        last_month = shiller["cape"].dropna().index[-1]
        stale = (pd.Timestamp.today().normalize() - last_month).days > MAX_AGE_DAYS["M"]
        notes = [*live_notes, f"Bands are the {period.lower()} mean ±1σ and ±2σ ({series.index[0]:%b %Y} onwards)."]
        if stale:
            notes.append(f"Shiller's file ends {last_month:%b %Y}: download a newer ie_data from shillerdata.com and "
                         "save it as cache/shiller_ie_data.csv.")
        theme.panel_footer(CAPE_SOURCE, "Monthly (live last point daily)", live_date, stale=stale,
                           caveat=" ".join(notes))
        with st.expander("Raw data"):
            st.dataframe(_raw_table(monthly.to_frame("Shiller CAPE"), "x", 2), width="stretch")


def _cape_fit_panel(shiller: pd.DataFrame, cape_now: float, live_date: pd.Timestamp) -> None:
    """Scatter of each month's CAPE against the next 10 years' annualized real total return, with a fitted line
    (linear or 1/CAPE, a toggle) over the chosen period, and the line's value at today's CAPE as a KPI tile."""
    title = "CAPE vs next 10 years' real return"
    subtitle = "% a year, real total return · x CAPE · monthly starting points"
    with st.container(border=True):
        head = st.container()
        kpi = st.container()  # the KPI sits above the controls, filled once the fit is known
        st.session_state.setdefault("cape_model", cape.DEFAULT_MODEL)
        col_m, col_s = st.columns(2)
        with col_m:
            st.segmented_control("Fit", tuple(cape.MODELS), key="cape_model", on_change=_keep_selection,
                                 args=("cape_model", cape.DEFAULT_MODEL),
                                 help="Linear: a straight line in CAPE. Earnings yield: a straight line in 1/CAPE, "
                                      "which flattens at high CAPE levels.")
        with col_s:
            period = _cape_sample_pills("cape_fit_sample")
        model_label = st.session_state["cape_model"]
        rows = cape.sample(shiller, period)
        fit = cape.fit(rows, cape.MODELS[model_label])
        if fit is None:
            with head:
                theme.panel_header(title, subtitle, NO_SUMMARY)
            st.info("Not enough months with a complete 10-year forward return in this period.")
            theme.panel_footer(CAPE_SOURCE, "Monthly", live_date)
            return

        points = rows.dropna(subset=["cape", "fwd10_real"])
        expected = float(fit.predict(cape_now))
        sm = summarise_cape_fit(cape_now, expected, model_label, fit.r2, fit.start, fit.end, points["cape"],
                                shiller["cape"].dropna().index[-1])
        with head:
            theme.panel_header(title, subtitle, sm.text)
        with kpi:
            theme.metric_tile("Expected 10Y real return (regression-implied)", expected, decimals=1, unit="% a year",
                              help=f"The fitted line's value at today's CAPE of {theme.fmt_num(cape_now, 1, 'x')}. "
                                   "Real = after inflation; total return = dividends reinvested.")
            breakeven, be_notice = data.load_series(CAPE_BREAKEVEN, 1)
            _warn_all([be_notice] if be_notice else [])
            be = breakeven.dropna()
            if be.empty:
                nominal_text = f"Nominal: {theme.MISSING} (breakeven inflation unavailable)"
            else:
                nominal_text = (f"Nominal ≈ **{theme.fmt_num(cape.nominal(expected, float(be.iloc[-1])), 1, '%')} a "
                                f"year**, adding 10Y breakeven inflation of {theme.fmt_num(float(be.iloc[-1]), 2, '%')} "
                                f"(FRED {CAPE_BREAKEVEN}, {be.index[-1]:%d %b %Y})")
            st.markdown(f"{nominal_text} · typical miss ±{theme.fmt_num(fit.resid_sd, 1)} pp · "
                        f"R² {theme.fmt_num(fit.r2, 2)}")

        st.caption(f"Fitted line: {fit.equation()}")
        grid = np.linspace(points["cape"].min(), max(points["cape"].max(), cape_now) * 1.03, 120)
        theme.render(theme.scatter_fit_fig(
            points["cape"], points["fwd10_real"], fit_x=grid, fit_y=fit.predict(grid), point=(cape_now, expected),
            labels=points.index.strftime("%b %Y"), fit_name=f"{model_label} fit", point_name="Today (live CAPE)",
            points_name="Monthly starting points", x_title="CAPE at the start of the 10 years", x_unit="x", unit="%",
            height=400), key="cape_fit")
        with st.expander("How to read this chart"):
            st.markdown(CAPE_HOW_TO_READ)

        notes = [
            f"{fit.n:,} monthly starting points, {fit.start:%b %Y}–{fit.end:%b %Y}; a month needs 10 complete years "
            "after it.",
            "Neighbouring months share most of their 10-year window, so the points are not independent: R² describes "
            "the fit, and no significance test is shown.",
            f"Typical miss = standard deviation of actual returns around the line ({theme.fmt_num(fit.resid_sd, 1)} pp).",
        ]
        if cape_now > fit.cape_max:
            notes.append(f"Today's CAPE is above every starting point in this period (max "
                         f"{theme.fmt_num(fit.cape_max, 1, 'x')}), so the line is extended beyond the data.")
        years = (fit.end - fit.start).days / 365.25
        if years < 30:
            notes.append(f"Short period: {years:.0f} years of starting points cover only "
                         f"{int(years // 10) + 1} non-overlapping 10-year outcomes, so the fit rests on very few "
                         "independent observations.")
        notes.append("Expected = the fitted line's value; actual decades have varied widely around it. Not investment "
                     "advice.")
        theme.panel_footer(CAPE_SOURCE, "Monthly", shiller["cape"].dropna().index[-1], stale=sm.stale,
                           caveat=" ".join(notes))
        with st.expander("Raw data"):
            raw = points[["cape", "fwd10_real"]].rename(columns={"cape": "CAPE (x)",
                                                                  "fwd10_real": "Next 10Y real return (% a year)"})
            st.dataframe(_raw_table(raw, "", 2), width="stretch")


@st.fragment
def render_cape_row() -> None:
    """TAA page: Shiller CAPE against its history (left) and the CAPE-to-next-10-year-return regression with its
    expected return (right). Both use the same live CAPE."""
    shiller, notice = data.load_shiller()
    if notice:
        st.warning(notice)
    if shiller.empty or shiller["cape"].dropna().empty:
        st.info("No Shiller CAPE data to show.")
        return
    live_notes: list[str] = []
    cape_now, live_date = _cape_live(shiller, live_notes)
    left, right = st.columns(2)
    with left:
        _cape_band_panel(shiller, cape_now, live_date, live_notes)
    with right:
        _cape_fit_panel(shiller, cape_now, live_date)
