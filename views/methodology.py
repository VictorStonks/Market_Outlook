"""Methodology & data: sources, series IDs, frequencies, how takeaways are computed, disclaimer."""
import pandas as pd
import streamlit as st

import data
import panels
import specs
import theme
from takeaways import MAX_AGE_DAYS

panels.page_header("Methodology & data")

st.subheader("Data sources", anchor=False)
st.markdown(
    "All charts use free public data. Macro, rates and credit series come from "
    "[FRED](https://fred.stlouisfed.org) (Federal Reserve Bank of St. Louis), which redistributes the "
    "ICE BofA credit indices. Equity data will come from Yahoo Finance via yfinance once those pages are built."
)
rows = []
for spec in specs.ALL_PANELS:
    for label, series_id in spec.series.items():
        rows.append({
            "Panel": spec.title, "Series": label, "FRED ID": series_id,
            "Frequency": panels.FREQ_NAMES[spec.series_freq.get(label, spec.freq)],
            "Transform": data.FRED_UNITS_LABEL[spec.fred_units],
        })
rows += [
    {"Panel": "Overview key indicators", "Series": "HY OAS", "FRED ID": specs.HY_OAS,
     "Frequency": "Daily", "Transform": data.FRED_UNITS_LABEL["lin"]},
    {"Panel": "Recession shading (Macro)", "Series": "NBER recession indicator", "FRED ID": specs.RECESSIONS,
     "Frequency": "Monthly", "Transform": data.FRED_UNITS_LABEL["lin"]},
]
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

st.subheader("How takeaways are computed", anchor=False)
freq_rows = ", ".join(f"{panels.FREQ_NAMES[k].lower()} {v} days" for k, v in MAX_AGE_DAYS.items())
st.markdown(
    "The sentence at the top of each panel is generated from the loaded data on every load. It is not "
    "hand-written, and it describes what the data shows without forecasting or advising.\n"
    "- **Level and percentile.** The latest value and its percentile within the loaded lookback window "
    "(the sentence names the window, for example \"of the 5Y range\").\n"
    "- **Change.** The latest value minus the value one period earlier: 1M for daily and monthly series, "
    "1W for weekly, 1Q for quarterly.\n"
    "- **Flags.** \"Near the high\" or \"Near the low\" at the top or bottom 2% of the window; "
    "\"More than 2σ from its mean\" otherwise when the latest value is beyond two standard deviations.\n"
    "- **What changed (Overview).** Recent moves beyond 2σ of typical moves of the same length, series in the "
    "top or bottom 2% of their range, and stale series.\n"
    f"- **Stale.** A series is flagged STALE when its latest observation is older than: {freq_rows}. "
    "These allow for normal publication lag and are heuristics."
)

st.subheader("Conventions", anchor=False)
st.markdown(
    "- **Dates.** Data as-of dates are observation dates. The refresh time under each page title is in "
    "Singapore time (SGT) and marks when the data cache was last filled.\n"
    "- **Units.** % for rates and yields, pp for percentage-point differences, k for thousands. "
    "Credit spreads (OAS) are shown in % throughout.\n"
    "- **Up and down colours.** \"Up\" means favourable. The convention (green up, red up, or "
    "colour-blind safe) is set in the sidebar. Every change also carries a sign and an arrow.\n"
    "- **Missing values** show as an em dash, never as zero."
)

st.subheader("Known data limits", anchor=False)
st.markdown(
    "- ICE BofA credit series on FRED go back about 3 years regardless of the lookback window, "
    "so percentiles and the ±1σ band for those series describe that shorter window.\n"
    "- Monthly and quarterly series are published with a lag, so their latest observation can be weeks or "
    "months old. Panels that mix frequencies say so in the subtitle.\n"
    "- Yield-curve panels use the tenors available in FRED; the corporate index curve plots each maturity "
    "bucket at its upper bound.\n"
    "- If a fetch fails, the panel shows the last successful copy with a warning naming the series."
)

st.subheader("Disclaimer", anchor=False)
st.markdown(theme.DISCLAIMER)
