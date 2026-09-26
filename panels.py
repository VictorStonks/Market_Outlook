"""Config-driven panels (DESIGN.md sections 5.2 and 10): a panel is a PanelSpec, a page is a list of them.

Every panel renders the same anatomy: header (title, unit + frequency, generated takeaway), panel-local
controls, chart, footer (source, frequency, as-of, STALE, caveat), collapsed raw data.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace

import pandas as pd
import streamlit as st

import countries
import data
import sectors
import theme
from takeaways import (ReturnsSummary, Summary, TakeawaySpec, summarise, summarise_country_returns,
                       summarise_returns, summarise_sector_returns, summarise_sector_table)

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
STOCK_UNIT = " USD"                    # theme formats a unit as a suffix (axis ticks, hover, takeaway)
STOCK_SOURCE = "Yahoo Finance via yfinance"
STOCK_TAKEAWAY = TakeawaySpec("", unit=STOCK_UNIT, decimals=2, freq="D", change_days=30, change_label="1M")
STOCK_CAVEAT = "Dividends are not included in the price. The universe is today's S&P 500 constituents."
REBASE_TO = 100.0                      # every rebased line starts at this value on the base date


def _stock_picker(key: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str], bool]:
    """Top of a stock panel: load the S&P 500 list and the saved close prices (warning on any fallback), then
    show the panel's own search box. Returns (universe, prices, selected tickers, any stock available)."""
    universe, universe_notice = data.load_universe()
    prices, price_notice = data.load_prices("close")
    _warn_all([n for n in (universe_notice, price_notice) if n])

    listed = universe[universe["ticker"].isin(prices.columns)]  # only stocks we hold prices for
    ticker_of = dict(zip(listed["label"], listed["ticker"]))
    label_of = dict(zip(listed["ticker"], listed["label"]))
    picked = st.multiselect(
        "Search stocks", list(ticker_of), default=[label_of[t] for t in DEFAULT_STOCKS if t in label_of],
        max_selections=MAX_STOCKS, key=key, placeholder="Ticker or company name",
        help=f"Type a ticker or company name. Up to {MAX_STOCKS} stocks.",
    )
    return universe, prices, [ticker_of[label] for label in picked], not listed.empty


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


@st.fragment
def render_stock_price_panel(lookback_years: int, ma_window: int | None) -> None:
    """Search S&P 500 stocks by ticker or company name and compare their closing prices over the sidebar
    lookback, on one shared price axis (DESIGN.md 5.2 anatomy, 6.2 "How do related series compare")."""
    title, subtitle = "Stock price", "USD per share · split-adjusted close · daily"
    with st.container(border=True):
        head = st.container()  # filled after the control so the takeaway can use the loaded data
        universe, prices, tickers, available = _stock_picker("stock_price_sel")
        series = _window_series(prices, tickers, lookback_years)
        sm = None
        if series:
            lead = next(iter(series))
            sm = summarise(series[lead], replace(STOCK_TAKEAWAY, label=lead))
        with head:
            theme.panel_header(title, subtitle, (sm.text if sm else NO_SUMMARY) if series else "")

        if series:
            plot, dashed = _stock_lines(prices, series, ma_window)
            theme.render(theme.line_fig(plot, dashed=dashed, unit=STOCK_UNIT, decimals=2), key="stock_price")
        _stock_empty_state(available, tickers, series)
        _stock_footer(universe, prices, _asof(sm, series), bool(sm and sm.stale))
        if series:
            with st.expander("Raw data"):
                st.dataframe(_raw_table(pd.concat(series, axis=1), STOCK_UNIT, 2), width="stretch")


@st.fragment
def render_normalised_panel(lookback_years: int, ma_window: int | None) -> None:
    """The stock price panel's companion: the same closes rebased to 100 on a common base date, so different
    price levels compare as % change (DESIGN.md 6.2 "Relative performance")."""
    title = "Normalised price"
    subtitle = f"Index, base date = {REBASE_TO:g} · split-adjusted close · daily"
    with st.container(border=True):
        head = st.container()
        universe, prices, tickers, available = _stock_picker("stock_norm_sel")
        series = _window_series(prices, tickers, lookback_years)

        sm, notes, rebased, dashed, cut = None, [], {}, [], {}
        if series:
            # Common base date: the latest first-available date, so every line starts at 100 on the same day.
            base_date = max(s.index[0] for s in series.values())
            cut = {t: s.loc[s.index >= base_date] for t, s in series.items()}
            notes.append(f"Base date {base_date:%d %b %Y} = {REBASE_TO:g}.")
            if base_date > data.prices_window(prices, lookback_years).index[0]:  # a short-history stock trims the chart
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
        _stock_empty_state(available, tickers, series)
        _stock_footer(universe, prices, _asof(sm, series), bool(sm and sm.stale), *notes)
        if series:
            with st.expander("Raw data"):
                st.dataframe(_raw_table(pd.concat({t: rebased[t] for t in cut}, axis=1), " index", 1), width="stretch")


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
