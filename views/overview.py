"""Overview: KPI strip, what changed, hero panels (DESIGN.md section 4)."""
from dataclasses import replace
from typing import NamedTuple

import streamlit as st

import data
import panels
import specs
import theme
from takeaways import TakeawaySpec, summarise, whats_changed


class Kpi(NamedTuple):
    label: str
    series_id: str
    unit: str
    good_when: str | None      # 'up' | 'down' | None (no good/bad reading)
    delta_unit: str | None = None


# All daily series; the delta is the 1M change quoted in the tile's help text.
KPIS = (
    Kpi("10Y Treasury yield", "DGS10", "%", None, specs.PP),
    Kpi("2Y Treasury yield", "DGS2", "%", None, specs.PP),
    Kpi("10Y–2Y spread", "T10Y2Y", specs.PP, "up"),
    Kpi("HY OAS", specs.HY_OAS, "%", "down", specs.PP),
)

# Reused from the catalogue, under their own keys so widget state does not collide with other pages.
HERO = [replace(s, key=f"ov_{s.key}") for s in
        (specs.YIELD_CURVE, specs.STEEPNESS, specs.OAS_QUALITY, specs.INFLATION, specs.UNEMPLOYMENT, specs.PAYROLLS)]

panels.page_header("Overview")
lookback_years, ma_window = panels.global_settings()

# ---------- KPI strip ----------
tiles, summaries, notices = [], {}, []
with st.spinner("Loading key indicators…"):
    for kpi in KPIS:
        s, notice = data.load_series(kpi.series_id, lookback_years)
        if notice:
            notices.append(notice)
        ts = TakeawaySpec(kpi.label, unit=kpi.unit, freq="D", change_days=30, change_label="1M")
        sm = summarise(s, ts) if not s.dropna().empty else None
        if sm:
            summaries[kpi.label] = sm
        tiles.append(dict(
            label=kpi.label, value=sm.latest if sm else None, delta=sm.change if sm else None,
            unit=kpi.unit, delta_unit=kpi.delta_unit, good_when=kpi.good_when, history=s.dropna().tail(90),
            help=(f"Change over 1M; sparkline shows the last 90 observations. FRED {kpi.series_id}, daily"
                  + (f", as of {sm.asof:%d %b %Y}." if sm else "."))))
for notice in notices:
    st.warning(notice)
theme.kpi_strip(tiles)

# ---------- What changed ----------
# Extend the KPI summaries with the macro headline series so the strip covers more than rates and credit.
for spec in (specs.INFLATION, specs.UNEMPLOYMENT, specs.PAYROLLS):
    label, series_id = next(iter(spec.series.items()))
    s, _ = data.load_series(series_id, lookback_years, spec.fred_units)
    sm, _ = panels.summarise_lead(spec, {label: s} if not s.dropna().empty else {})
    if sm:
        summaries[spec.title] = sm

with st.container(border=True):
    st.subheader("What changed", anchor=False)
    st.caption(f"Versus the loaded {lookback_years}Y range · moves beyond 2σ, range extremes and stale data")
    lines = whats_changed(summaries)
    st.markdown("\n".join(f"- {line}" for line in lines) if lines else "Nothing notable versus the loaded range.")

# ---------- Hero panels ----------
panels.render_grid(HERO, lookback_years, ma_window)
