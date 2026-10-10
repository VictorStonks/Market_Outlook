"""Config-driven panels (DESIGN.md sections 5.2 and 10): a panel is a PanelSpec, a page is a list of them.

Every panel renders the same anatomy: header (title, unit + frequency, generated takeaway), panel-local
controls, chart, footer (source, frequency, as-of, STALE, caveat), collapsed raw data.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

import pandas as pd
import streamlit as st

import correlation
import countries
import data
import sectors
import theme
import valuation
from takeaways import (ReturnsSummary, Summary, TakeawaySpec, summarise, summarise_corr_matrix,
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
    sorts with the S&P rows; sector P/E is the S&P 500 average (the default universe). Adds footer notes."""
    added = data.added_tickers()
    close_all, _ = data.load_added_prices("close")  # its notice was shown by _stock_options or the panel above
    if not added or close_all.empty:
        return pd.DataFrame()
    sp_pe = float("nan")
    if not (sp_prices.empty or eps_all.empty or shares.empty):
        grp = valuation.group_ntm_pe(sp_prices.iloc[[-1]], eps_all.reindex(sp_prices.index).iloc[[-1]], shares)
        sp_pe = float(grp["pe"].iloc[-1]) if not grp.empty else sp_pe
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
                                  pd.Series(dtype=object), cutoff)
        fx = data.fx_to_usd(data.major_currency(meta.get("currency"))[0])
        if fx is None:
            no_fx.append(t)
        if _eps_currency_note(t):
            mixed.append(f"{t} ({meta['financial_currency']} vs {data.major_currency(meta['currency'])[0]})")
        one["market_cap"] = one["market_cap"] * fx if fx is not None else float("nan")
        one["sector_pe"] = sp_pe
        rows.append(one.assign(name=meta.get("name") or t, sector=NON_SP))
    if not rows:
        return pd.DataFrame()
    notes.append(f"{NON_SP} rows are tickers added from Yahoo Finance: market cap is converted to USD at the latest "
                 "Yahoo FX rate, and Sector P/E shows the S&P 500 average.")
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


def _pe_screen_styler(view: pd.DataFrame, cols: dict[str, str]):
    """Display frame (company, ticker, then numbers in the order the owner asked for) with house number formats;
    σ is shaded. Sorting in the table uses the underlying numbers, not the formatted text."""
    shown = view.reset_index().rename(columns=cols)[list(cols.values())]
    formats = {cols["market_cap"]: (1, False), cols["pe"]: (1, False), cols["pe_z"]: (1, True),
               cols["sector_pe"]: (1, False)}
    styler = shown.style.format({c: (lambda v, d=d: theme.fmt_signed(v, d)) if signed else
                                 (lambda v, d=d: theme.fmt_num(v, d)) for c, (d, signed) in formats.items()},
                                na_rep=theme.MISSING)
    return theme.shade_signed(styler, [cols["pe_z"]], good_when="down", cap_quantile=SCREEN_SHADE_CAP)


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
    from its own average over the sidebar lookback (σ, as on the history chart's bands) and the GICS sector's market-cap
    weighted NTM P/E (S&P 500 average for added tickers). Search and sector filter above; a ticker not in the table
    can be added from the search box; click a column header to sort it either way."""
    title = "Forward P/E screen"
    window = f"{lookback_years}Y"
    subtitle = f"S&P 500 and added tickers · USD bn and x (see column headers) · daily · σ over the {window} window"
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
        sm, view, asof, added_notes = None, pd.DataFrame(), None, []
        if available:
            asof = prices.index[-1]
            sector_of = universe.set_index("ticker")["sector"]
            cutoff = _pe_cutoff(lookback_years)
            table = valuation.pe_screen(prices, eps_all, shares, sector_of, cutoff)
            added_rows = _added_screen_rows(prices, eps_all, shares, cutoff, added_notes)
            view = _pe_screen_view(universe, table, query, picked, added_rows)
            sm = summarise_pe_screen(view["pe_z"], window, asof)
        with head:
            theme.panel_header(title, subtitle, sm.text if sm else "")
        if available:
            _pe_screen_add_button(query, universe, view)

        cols = {"name": "Company", "ticker": "Ticker", "market_cap": "Market cap (USD bn)", "pe": "Fwd P/E (x)",
                "pe_z": f"P/E σ vs {window} avg", "sector": "Sector", "sector_pe": "Sector P/E (x)"}
        if not view.empty:
            st.dataframe(_pe_screen_styler(view, cols), hide_index=True, width="stretch", height=SCREEN_HEIGHT,
                         column_config={"Company": st.column_config.TextColumn(pinned=True)})
            st.download_button("Download table (CSV)", _pe_screen_csv(view, cols), file_name="forward_pe_screen.csv",
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
            "Shading: a P/E below its own average uses the up colour; it saturates at the 95th percentile.",
            *added_notes,
        ]
        theme.panel_footer(STOCK_SOURCE, FREQ_NAMES["D"], asof, stale=bool(sm and sm.stale),
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
    `key` holds the pills' selection, `prev_key` the selection before this change."""
    new, prev = set(st.session_state[key] or []), set(st.session_state[prev_key])
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
