"""Living style guide: run `streamlit run styleguide.py` to see every token and component
with synthetic data. Use it to eyeball changes to theme.py / config.toml, and to check
DESIGN.md's checklist (fonts, contrast, tabular digits, both themes, all three up/down modes)."""
import numpy as np
import pandas as pd
import streamlit as st

import theme
from takeaways import TakeawaySpec, summarise, whats_changed

theme.setup_page("Market Outlook · Style guide")

with st.sidebar:
    st.header("Controls")
    theme.display_controls()

# ---------- synthetic data (seeded, so the page is stable) ----------
rng = np.random.default_rng(7)
days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=1300)


def walk(start: float, vol: float) -> pd.Series:
    return pd.Series(start + np.cumsum(rng.normal(0, vol, len(days))), index=days)


y10, y2 = walk(4.2, 0.03), walk(4.0, 0.035)
spread = y10 - y2
credit = {q: walk(base, 0.02).clip(lower=0.2) for q, base in
          {"US AAA": 0.5, "US AA": 0.7, "US A": 1.0, "US BBB": 1.5, "US BB": 2.6, "US B": 4.0}.items()}
t = theme.tokens()

st.title("Market Outlook")
st.caption(f"Style guide · {t['mode']} theme · up/down: {theme.UPDOWN[theme.updown_mode()]['label']}")

# ---------- KPI strip ----------
st.subheader("KPI strip", anchor=False)
theme.kpi_strip([
    dict(label="US 10Y yield", value=y10.iloc[-1], delta=y10.iloc[-1] - y10.iloc[-22], unit="%", good_when=None, history=y10.tail(90)),
    dict(label="US 2Y yield", value=y2.iloc[-1], delta=y2.iloc[-1] - y2.iloc[-22], unit="%", good_when=None, history=y2.tail(90)),
    dict(label="10Y–2Y spread", value=spread.iloc[-1], delta=spread.iloc[-1] - spread.iloc[-22], unit=" pp", good_when="up", history=spread.tail(90)),
    dict(label="BB OAS", value=credit["US BB"].iloc[-1], delta=credit["US BB"].iloc[-1] - credit["US BB"].iloc[-22], unit="%", good_when="down", history=credit["US BB"].tail(90)),
])

# ---------- Panels ----------
left, right = st.columns(2)
spec = TakeawaySpec("10Y–2Y spread", unit=" pp", freq="D", note=lambda v: "Curve inverted." if v < 0 else "Curve positively sloped.")
sm = summarise(spread, spec)
with left:
    with st.container(border=True):
        theme.panel_header("Yield curve steepness", "10Y minus 2Y · percentage points · daily", sm.text if sm else "")
        theme.render(theme.line_fig({"10Y–2Y": spread, "3M MA": spread.rolling(63).mean()}, dashed=["3M MA"], unit=" pp", baseline=0), key="sg_line")
        theme.panel_footer("FRED (synthetic here)", "Daily", sm.asof if sm else None, stale=bool(sm and sm.stale),
                           caveat="Illustrative data only.")
with right:
    with st.container(border=True):
        theme.panel_header("Corporate OAS by quality", "Ordered series use one blue ramp · % · daily")
        theme.render(theme.line_fig(credit, ordered=True, unit="%"), key="sg_ordered")
        theme.panel_footer("FRED (synthetic here)", "Daily", days[-1])

left, right = st.columns(2)
with left:
    with st.container(border=True):
        theme.panel_header("Level vs history", "Mean ± 1σ shaded band")
        theme.render(theme.band_fig("US BBB", credit["US BBB"], unit="%"), key="sg_band")
        theme.panel_footer("FRED (synthetic here)", "Daily", days[-1], caveat="Data supplier limits history to 3Y.")
with right:
    with st.container(border=True):
        theme.panel_header("Yield curve", "Latest vs earlier curves")
        mats = [0.25, 2, 5, 10, 30]
        latest = pd.Series([4.4, 4.0, 4.1, 4.3, 4.6], index=mats)
        curves = {"Latest": latest, "1 Month Ago": latest - 0.10, "6 Months Ago": latest - pd.Series([0.3, 0.5, 0.4, 0.3, 0.2], index=mats)}
        theme.render(theme.curve_fig(curves), key="sg_curve")
        theme.panel_footer("FRED (synthetic here)", "Daily", days[-1])

with st.container(border=True):
    theme.panel_header("Change in yield vs latest", "Bars always include zero")
    change = pd.DataFrame({k: latest - v for k, v in curves.items() if k != "Latest"})
    theme.render(theme.bar_fig(change, unit="%", x_title="Maturity (years)"), key="sg_bar")

# ---------- What changed ----------
st.subheader("What changed", anchor=False)
lines = whats_changed({"10Y–2Y spread": sm, "BB OAS": summarise(credit["US BB"], TakeawaySpec("BB OAS", unit="%"))} if sm else {})
st.markdown("\n".join(f"- {l}" for l in lines) if lines else "Nothing notable versus the loaded range.")

# ---------- Table ----------
st.subheader("Table", anchor=False)
rows = pd.DataFrame({
    "Series": list(credit),
    "Latest (%)": [s.iloc[-1] for s in credit.values()],
    "1M chg (pp)": [s.iloc[-1] - s.iloc[-22] for s in credit.values()],
    "90D": [s.tail(90).tolist() for s in credit.values()],
})
st.dataframe(
    rows, hide_index=True, width="stretch",
    column_config={
        "Latest (%)": st.column_config.NumberColumn(format="%.2f"),
        "1M chg (pp)": st.column_config.NumberColumn(format="%+.2f"),
        "90D": st.column_config.LineChartColumn(),
    },
)

# ---------- Tokens ----------
st.subheader("Tokens", anchor=False)
swatch = " ".join(
    f"<span style='display:inline-block;width:88px;margin:0 8px 8px 0;font:12px IBM Plex Mono,monospace'>"
    f"<span style='display:block;height:28px;background:{v};border:1px solid {t['border']};border-radius:2px'></span>{k}<br>{v}</span>"
    for k, v in {**{k: t[k] for k in ("bg", "surface", "raised", "border", "text", "text2", "muted", "accent", "up", "down")},
                 **{f"cat{i + 1}": c for i, c in enumerate(t["categorical"])},
                 **{f"ord{i + 1}": c for i, c in enumerate(theme.ordered_colors(6))}}.items()
)
st.markdown(swatch, unsafe_allow_html=True)

# ---------- Widgets (contrast check: text on selected/primary fills) ----------
st.subheader("Widgets", anchor=False)
st.multiselect("Series to show", ["US AAA", "US AA", "US A", "US BBB"], default=["US AAA", "US A"], key="sg_multi")
w1, w2, w3 = st.columns(3)
w1.pills("Lookback", ["1Y", "3Y", "5Y", "10Y"], default="5Y", key="sg_pills")
w2.segmented_control("Chart type", ["Line", "Area", "Bar"], default="Line", key="sg_seg")
w3.checkbox("Show moving average", value=True, key="sg_check")
st.slider("Lookback (years)", 1, 10, 5, key="sg_slider")
tab_a, tab_b = st.tabs(["Data", "Notes"])
with tab_a:
    st.caption("Selected tab underline uses the primary colour.")
with st.expander("Raw data"):
    st.caption("Expander for raw data tables (progressive disclosure).")

# ---------- Type & states ----------
st.subheader("Type and states", anchor=False)
st.markdown("Tabular check (each pair must be the same width): `1111` / `8888` → 1111 / 8888")
c1, c2, c3 = st.columns(3)
c1.button("Primary action", type="primary")
c2.button("Secondary action")
c3.markdown(":orange-badge[STALE] · :green-badge[LIVE]")
st.info("Empty state: no series selected. Choose at least one to plot.")
st.warning("Fetch failed for DGS10; showing cached data from 18 Sep 2026.")
