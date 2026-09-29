"""Market Outlook design system: tokens, Plotly template, chart and UI helpers.

DESIGN.md is the spec; this file is its implementation. Colours here must match
.streamlit/config.toml. Every page imports from here; no page defines its own colours.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

FONT_SANS = "IBM Plex Sans, sans-serif"
FONT_MONO = "IBM Plex Mono, monospace"
MINUS = "−"  # true minus sign; never a hyphen in displayed numbers
UP_ARROW, DOWN_ARROW = "▲", "▼"
MISSING = "—"  # em dash: shown for missing values, never 0 or NaN

# ---------- Tokens ----------

TOKENS = {
    "dark": {
        "bg": "#0B0F14", "surface": "#121821", "raised": "#1A2230", "border": "#2A3446",
        "text": "#E6EAF0", "text2": "#A3AEBF", "muted": "#7A8699",
        # accent = data ink (series 1, highlights). primary = Streamlit control fill; Streamlit puts
        # white text on it, so it must give white >= 4.5:1, which bright amber cannot.
        "accent": "#FFB000", "primary": "#A86300",
        "categorical": ["#FFB000", "#56B4E9", "#E07BB5", "#9B8CFF", "#C7D0DC", "#38D6CF"],
        # Ordered ramp, least -> most contrast against the background (all >= 3:1).
        "ordered": ["#2E86C8", "#4A9CDD", "#6BB2EB", "#8FC8F2", "#B4DCF8", "#D9EEFC"],
    },
    "light": {
        "bg": "#F7F9FC", "surface": "#FFFFFF", "raised": "#EEF2F7", "border": "#D5DCE6",
        "text": "#101828", "text2": "#475467", "muted": "#667085",
        "accent": "#9A5B00", "primary": "#9A5B00",
        "categorical": ["#9A5B00", "#0072B2", "#B5478A", "#6B5CE0", "#5B6675", "#077A76"],
        "ordered": ["#2E86C8", "#2777B5", "#21689F", "#1B5989", "#154A73", "#0F3B5D"],
    },
}

# "Up" always means favourable / price rising. The colours swap by market convention.
# names = the Streamlit palette names used for st.metric deltas (see config.toml).
UPDOWN = {
    "green_up": {"label": "Green up / red down", "names": ("green", "red"),
                 "dark": ("#26C281", "#F0616D"), "light": ("#0A7C50", "#C8323F")},
    "red_up": {"label": "Red up / green down", "names": ("red", "green"),
               "dark": ("#F0616D", "#26C281"), "light": ("#C8323F", "#0A7C50")},
    "cvd": {"label": "Colour-blind safe (blue / orange)", "names": ("blue", "orange"),
            "dark": ("#4C9BFF", "#FF9F43"), "light": ("#0B5FCC", "#A85200")},
}

PLOTLY_CONFIG = {"displaylogo": False, "displayModeBar": False, "scrollZoom": False}

DISCLAIMER = ("Personal project. Public data only. Not investment advice. "
              "Views are the author's own and do not represent any employer.")

_CSS = """
<style>
/* Tabular figures everywhere so digits align in columns. Deliberately the ONLY global
   CSS override: it targets no Streamlit internals, so it survives upgrades. */
* { font-variant-numeric: tabular-nums; }
</style>
"""


def theme_mode() -> str:
    """'light' or 'dark'. Falls back to dark: st.context.theme can be wrong on the very
    first script run of a session (known Streamlit issue), and it self-corrects on rerun."""
    try:
        return "light" if st.context.theme.type == "light" else "dark"
    except Exception:
        return "dark"


def updown_mode() -> str:
    return st.session_state.get("updown_mode", "green_up")


def tokens() -> dict:
    """Active tokens for the current theme + up/down mode."""
    mode = theme_mode()
    t = dict(TOKENS[mode])
    up, down = UPDOWN[updown_mode()][mode]
    t.update(mode=mode, up=up, down=down, flat=t["text2"])
    return t


def display_controls() -> None:
    """Sidebar widget for the up/down colour convention. Call once, in the entrypoint."""
    st.selectbox(
        "Up / down colours", list(UPDOWN),
        format_func=lambda k: UPDOWN[k]["label"], key="updown_mode",
    )


def setup_page(title: str = "Market Outlook") -> None:
    """First Streamlit call in the entrypoint."""
    st.set_page_config(page_title=title, layout="wide", initial_sidebar_state="expanded")
    st.markdown(_CSS, unsafe_allow_html=True)


# ---------- Colour helpers ----------

def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _pick(seq: Sequence[str], n: int) -> list[str]:
    if n <= 0:
        return []
    if n == 1:
        return [seq[-1]]
    return [seq[round(i * (len(seq) - 1) / (n - 1))] for i in range(n)]


def categorical_colors(n: int) -> list[str]:
    """Unordered series. Series 1 is always the accent. Keep n <= 6 (see DESIGN.md)."""
    pal = tokens()["categorical"]
    return [pal[i % len(pal)] for i in range(n)]


def ordered_colors(n: int) -> list[str]:
    """Ordered series (credit quality, maturity). First series gets the MOST contrast."""
    return _pick(tokens()["ordered"], n)[::-1]


# ---------- Number formatting ----------

def _is_missing(x) -> bool:
    return x is None or bool(pd.isna(x))


def fmt_num(x, decimals: int = 2, unit: str = "") -> str:
    """1234.5 -> '1,234.50'; negatives use a true minus; missing -> em dash.
    `unit` carries its own spacing: '%', ' pp', ' bp'."""
    if _is_missing(x):
        return MISSING
    body = f"{abs(x):,.{decimals}f}"
    neg = x < 0 and float(body.replace(",", "")) != 0
    return f"{MINUS if neg else ''}{body}{unit}"


def fmt_delta(x, decimals: int = 2, unit: str = "") -> str:
    """Signed change with arrow: '▲ +0.12 pp' / '▼ −0.05 pp'."""
    if _is_missing(x):
        return MISSING
    body = f"{abs(x):,.{decimals}f}"
    if float(body.replace(",", "")) == 0:
        return f"{body}{unit}"
    return f"{UP_ARROW} +{body}{unit}" if x > 0 else f"{DOWN_ARROW} {MINUS}{body}{unit}"


def fmt_signed(x, decimals: int = 2, unit: str = "") -> str:
    """Signed change without an arrow, for tight tables: '+0.12 pp' / '−0.05 pp'; zero has no sign."""
    if _is_missing(x):
        return MISSING
    body = f"{abs(x):,.{decimals}f}"
    if float(body.replace(",", "")) == 0:
        return f"{body}{unit}"
    return f"+{body}{unit}" if x > 0 else f"{MINUS}{body}{unit}"


def delta_color(x, good_when: str | None = "up") -> str:
    """Hex colour for a change. good_when: 'up' | 'down' | None (neutral)."""
    t = tokens()
    if _is_missing(x) or x == 0 or good_when is None:
        return t["flat"]
    return t["up"] if (x > 0) == (good_when == "up") else t["down"]


# ---------- Table styling ----------

def _luminance(hex_color: str) -> float:
    """WCAG relative luminance of '#RRGGBB'."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(base: str, hue: str, fraction: float) -> str:
    """`base` moved `fraction` of the way towards `hue` (0 = base, 1 = hue), as '#RRGGBB'."""
    b, h = base.lstrip("#"), hue.lstrip("#")
    return "#" + "".join(
        f"{round(int(b[i:i + 2], 16) + fraction * (int(h[i:i + 2], 16) - int(b[i:i + 2], 16))):02X}"
        for i in (0, 2, 4))


def _max_tint(base: str, hue: str, text: str) -> float:
    """Largest fraction of the way from `base` towards `hue` at which `text` still has >= 4.5:1 contrast."""
    f = 1.0
    while f > 0 and _contrast(text, _mix(base, hue, f)) < 4.5:
        f -= 0.02
    return max(f, 0.0)


def _signed_shader(good_when: str = "up", cap_quantile: float | None = None):
    """Column function for `Styler.apply`: shades each cell around zero, `up` colour for favourable values and
    `down` for unfavourable (`good_when="down"` flips which sign is favourable), darkest = the column's largest
    absolute value, or its `cap_quantile` quantile so one outlier does not wash out the rest. Colours follow the
    up/down mode. Tint strength is capped so the text keeps >= 4.5:1 contrast on every cell."""
    t = tokens()
    base, text = t["raised"], t["text"]
    strength = {side: _max_tint(base, t[side], text) for side in ("up", "down")}

    def shade(col: pd.Series) -> list[str]:
        mags = col.abs()
        span = mags.quantile(cap_quantile) if cap_quantile is not None else mags.max()
        styles = []
        for v in col:
            if pd.isna(v) or v == 0 or not span or pd.isna(span):
                styles.append("")
                continue
            side = "up" if (v > 0) == (good_when == "up") else "down"
            styles.append(f"background-color: {_mix(base, t[side], strength[side] * min(abs(v) / span, 1.0))}; "
                          f"color: {text}")
        return styles

    return shade


def shade_signed(styler, columns: Sequence[str], *, good_when: str = "up", cap_quantile: float | None = None):
    """Add signed shading (see `_signed_shader`) to some columns of an existing Styler; formats are left alone."""
    return styler.apply(_signed_shader(good_when, cap_quantile), axis=0, subset=list(columns))


def signed_heatmap(df: pd.DataFrame, *, decimals: int = 1, unit: str = "%", arrows: bool = True):
    """pandas Styler for a table of signed changes (DESIGN.md 5.3, 6.2 heatmap): the value is printed in every
    cell with sign and arrow (fmt_delta; `arrows=False` drops the arrow for narrow tables and keeps the sign),
    and each COLUMN is shaded on its own scale around zero: the `up` colour for gains, the `down` colour for
    losses, darkest = the column's largest move."""
    fmt = fmt_delta if arrows else fmt_signed
    return df.style.apply(_signed_shader(), axis=0).format(lambda v: fmt(v, decimals, unit), na_rep=MISSING)


# ---------- Plotly ----------

def plotly_template() -> go.layout.Template:
    t = tokens()
    axis = dict(
        tickfont=dict(color=t["text2"], size=12), linecolor=t["border"],
        zeroline=False, automargin=True,
    )
    layout = go.Layout(
        # Streamlit's frontend overwrites these at figure level even with theme=None (paper =
        # backgroundColor, plot area = secondaryBackgroundColor), so we match rather than fight it:
        # charts read as a "well" (surface) on the page. Verified on Streamlit 1.58.
        paper_bgcolor=t["bg"], plot_bgcolor=t["surface"],
        font=dict(family=FONT_SANS, size=12, color=t["text2"]),
        colorway=t["categorical"],
        margin=dict(l=8, r=8, t=8, b=8),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=t["raised"], bordercolor=t["border"],
                        font=dict(family=FONT_MONO, size=12, color=t["text"])),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(color=t["text2"])),
        xaxis=dict(showgrid=False, showspikes=True, spikemode="across", spikethickness=1,
                   spikedash="dot", spikecolor=t["muted"], **axis),
        yaxis=dict(showgrid=True, gridcolor=t["border"], gridwidth=1, **axis),
    )
    return go.layout.Template(layout=layout)


def _base_fig(height: int, unit: str, decimals: int) -> go.Figure:
    fig = go.Figure(layout=dict(template=plotly_template(), height=height))
    fig.update_yaxes(ticksuffix=unit, tickformat=f",.{decimals}f")
    return fig


def _hover(unit: str, decimals: int) -> str:
    return f"%{{y:,.{decimals}f}}{unit}<extra></extra>"


def line_fig(
    series: Mapping[str, pd.Series], *, ordered: bool = False, dashed: Sequence[str] = (),
    unit: str = "", decimals: int = 2, height: int = 300, baseline: float | None = None,
) -> go.Figure:
    """One or more time series on a shared y-axis (drop-in for the old line_chart).
    ordered=True for ordered families (credit quality, maturity): blue ramp, first = strongest.
    `dashed` names are drawn as muted dashed lines (moving averages).
    `baseline` draws a dotted reference line at that y (100 for rebased series); it never stretches the axis."""
    t = tokens()
    names = list(series)
    colors = ordered_colors(len(names)) if ordered else categorical_colors(len(names))
    fig = _base_fig(height, unit, decimals)
    fig.update_xaxes(tickformat="%b %y", hoverformat="%d %b %Y")
    for name, color in zip(names, colors):
        s = series[name].dropna()  # mixing monthly/quarterly series: skip gaps, keep lines joined
        is_dashed = name in dashed
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=name, mode="lines",
            line=dict(color=t["text2"] if is_dashed else color,
                      width=1.5 if is_dashed else 2, dash="dash" if is_dashed else "solid"),
            hovertemplate=_hover(unit, decimals),
        ))
    if baseline is not None:
        fig.add_hline(y=baseline, line_color=t["muted"], line_width=1, line_dash="dot", layer="below")
    return fig


def band_fig(
    label: str, s: pd.Series, *, k: float | Sequence[float] = 1.0, unit: str = "", decimals: int = 2,
    height: int = 300,
) -> go.Figure:
    """Series against its historical mean with shaded +-k sigma bands (replaces the two red lines). `k` may be
    several multiples, e.g. (1, 2): each is one band at the same tint, so the inner band reads darker."""
    t = tokens()
    s = s.dropna()
    mu, sd = float(s.mean()), float(s.std())
    x = s.index
    fig = _base_fig(height, unit, decimals)
    fig.update_xaxes(tickformat="%b %y", hoverformat="%d %b %Y")
    for band in sorted([k] if isinstance(k, (int, float)) else k, reverse=True):  # widest first, so it sits behind
        fig.add_trace(go.Scatter(x=x, y=[mu + band * sd] * len(x), mode="lines", line=dict(width=0),
                                 hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scatter(x=x, y=[mu - band * sd] * len(x), mode="lines", line=dict(width=0),
                                 fill="tonexty", fillcolor=_rgba(t["muted"], 0.2),
                                 name=f"±{band:g}σ band", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=[x[0], x[-1]], y=[mu, mu], mode="lines",
                             line=dict(color=t["text2"], width=1.5, dash="dash"),
                             name=f"Mean ({fmt_num(mu, decimals, unit)})", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=s.values, name=label, mode="lines",
                             line=dict(color=t["accent"], width=2), hovertemplate=_hover(unit, decimals)))
    return fig


def curve_fig(
    curves: Mapping[str, pd.Series], *, unit: str = "%", decimals: int = 2, height: int = 300,
) -> go.Figure:
    """Yield curves: x = maturity in years, y = yield. Must include 'Latest' (solid accent);
    comparison curves are progressively dimmer dashed greys."""
    t = tokens()
    fig = _base_fig(height, unit, decimals)
    fig.update_xaxes(title_text="Maturity (years)", title_font=dict(color=t["text2"]),
                     tickformat="~g", hoverformat="~g")
    fig.update_layout(hovermode="x")
    older = 0
    for name, s in curves.items():
        s = s.dropna().sort_index()
        if s.empty:
            continue
        if name == "Latest":
            line, marker = dict(color=t["accent"], width=2.5), dict(size=7, color=t["accent"])
        else:
            line = dict(color=_rgba(t["text2"], max(0.4, 0.9 - 0.15 * older)), width=1.5, dash="dash")
            marker = dict(size=5, color=_rgba(t["text2"], max(0.4, 0.9 - 0.15 * older)))
            older += 1
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name, mode="lines+markers",
                                 line=line, marker=marker, hovertemplate=_hover(unit, decimals)))
    return fig


def bar_fig(
    df: pd.DataFrame, *, unit: str = "", decimals: int = 2, height: int = 300, x_title: str = "",
) -> go.Figure:
    """Grouped bars: index = x categories, one column per group. Always includes zero."""
    t = tokens()
    fig = _base_fig(height, unit, decimals)
    fig.update_layout(barmode="group")
    fig.update_xaxes(title_text=x_title, title_font=dict(color=t["text2"]), type="category", tickformat=None)
    fig.add_hline(y=0, line_color=t["muted"], line_width=1)
    for name, color in zip(df.columns, categorical_colors(len(df.columns))):
        fig.add_trace(go.Bar(x=[str(i) for i in df.index], y=df[name].values, name=str(name),
                             marker_color=color, hovertemplate=_hover(unit, decimals)))
    return fig


def corr_heatmap_fig(
    matrix: pd.DataFrame, *, counts: pd.DataFrame | None = None, decimals: int = 2, height: int = 400,
    good_when: str = "down",
) -> go.Figure:
    """Correlation matrix (DESIGN.md 6.2 heatmap): the value is printed in every cell on a fixed −1 to +1 scale,
    neutral (`raised`) at 0. With `good_when="down"`, correlations near −1 take the up colour and near +1 the down
    colour, so the colours follow the up/down mode. Tints stop where the text still has >= 4.5:1 contrast. Missing
    values show as gaps. `counts` (same shape) adds the number of paired returns to the hover."""
    t = tokens()
    base, text = t["raised"], t["text"]
    low, high = ("up", "down") if good_when == "down" else ("down", "up")
    scale = [[0.0, _mix(base, t[low], _max_tint(base, t[low], text))], [0.5, base],
             [1.0, _mix(base, t[high], _max_tint(base, t[high], text))]]
    labels = [str(c) for c in matrix.columns]
    z = matrix.to_numpy(dtype=float)
    shade = z.copy()
    np.fill_diagonal(shade, 0.0)  # each item with itself is always 1: print it, but leave the cell untinted
    hover = "%{y} × %{x}: %{text}"
    if counts is not None:
        hover += "<br>%{customdata:,.0f} paired daily returns"
    fig = go.Figure(layout=dict(template=plotly_template(), height=height, hovermode="closest"))
    fig.add_trace(go.Heatmap(
        z=shade, x=labels, y=labels, zmin=-1, zmax=1, colorscale=scale, showscale=False, xgap=2, ygap=2,
        text=[[fmt_num(v, decimals) for v in row] for row in z], texttemplate="%{text}",
        textfont=dict(color=text, size=12 if len(labels) <= 6 else 11),
        customdata=None if counts is None else counts.to_numpy(dtype=float),
        hovertemplate=hover + "<extra></extra>",
    ))
    fig.update_xaxes(showgrid=False, showspikes=False, side="top", type="category")
    fig.update_yaxes(showgrid=False, showspikes=False, autorange="reversed", type="category")
    return fig


def add_recessions(fig: go.Figure, usrec: pd.Series, x_min, x_max) -> None:
    """Shade NBER recessions (FRED USREC: monthly, 1 = recession) as muted bands under the lines.
    Clipped to [x_min, x_max] so the shading never stretches the x-axis beyond the data."""
    flag = usrec.dropna().sort_index().astype(int)
    starts = flag.index[(flag == 1) & (flag.shift(fill_value=0) == 0)]
    ends = flag.index[(flag == 1) & (flag.shift(-1, fill_value=0) == 0)]
    for start, end in zip(starts, ends):
        x0, x1 = max(start, x_min), min(end + pd.offsets.MonthEnd(0), x_max)
        if x0 < x1:
            fig.add_vrect(x0=x0, x1=x1, fillcolor=_rgba(tokens()["muted"], 0.18), line_width=0, layer="below")


def render(fig: go.Figure, key: str | None = None) -> None:
    """theme=None: our own template styles the chart, not Streamlit's."""
    st.plotly_chart(fig, width="stretch", theme=None, config=PLOTLY_CONFIG, key=key)


# ---------- Streamlit UI components ----------

def metric_tile(
    label: str, value, delta=None, *, decimals: int = 2, unit: str = "", delta_decimals: int | None = None,
    delta_unit: str | None = None, good_when: str | None = None, history=None, help: str | None = None,
) -> None:
    """KPI tile: label, value, signed delta, optional sparkline.
    good_when: 'up' (price, equity), 'down' (unemployment, spreads), None (neutral grey).
    delta_unit: unit of the change when it differs from the level's (a yield in % changes in ' pp')."""
    dd = decimals if delta_decimals is None else delta_decimals
    du = unit if delta_unit is None else delta_unit
    up_name, down_name = UPDOWN[updown_mode()]["names"]
    if _is_missing(delta) or delta == 0 or good_when is None:
        color = "off"
    else:
        color = up_name if (delta > 0) == (good_when == "up") else down_name
    kwargs = dict(
        label=label, value=fmt_num(value, decimals, unit),
        delta=None if _is_missing(delta) else f"{delta:+,.{dd}f}{du}",
        delta_color=color, border=True, help=help,
    )
    if history is not None:
        kwargs.update(chart_data=[float(v) for v in pd.Series(history).dropna().tail(120)], chart_type="line")
    try:
        st.metric(**kwargs)
    except (TypeError, st.errors.StreamlitAPIException):
        # Older Streamlit without sparklines / named delta colours: degrade, don't crash.
        kwargs.pop("chart_data", None)
        kwargs.pop("chart_type", None)
        kwargs["delta_color"] = "off"
        st.metric(**kwargs)


def kpi_strip(tiles: Sequence[dict]) -> None:
    """A row of at most 6 metric_tile(**kwargs) tiles."""
    assert 0 < len(tiles) <= 6, "KPI strip holds 1-6 tiles (DESIGN.md)"
    for col, tile in zip(st.columns(len(tiles)), tiles):
        with col:
            metric_tile(**tile)


def panel_header(title: str, subtitle: str = "", takeaway: str = "") -> None:
    st.subheader(title, anchor=False)
    if subtitle:
        st.caption(subtitle)
    if takeaway:
        st.markdown(takeaway)


def panel_footer(
    source: str, freq: str, asof: pd.Timestamp | None, *, caveat: str | None = None, stale: bool = False,
) -> None:
    parts = [f"Source: {source}", freq, f"As of {asof:%d %b %Y}" if asof is not None else "As of —"]
    if stale:
        parts.append(":orange-badge[STALE]")
    st.caption(" · ".join(parts))
    if caveat:
        st.caption(f"Note: {caveat}")
