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
    "ICE BofA credit indices. Equity data comes from Yahoo Finance via yfinance."
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

st.subheader("Forward P/E (NTM), Equities page", anchor=False)
st.markdown(
    "Yahoo Finance publishes today's analyst estimates but not what analysts expected on past dates, so a true "
    "historical forward P/E cannot be built from free data. The panel builds an approximation from scratch:\n"
    "- **Denominator.** Next-12-month (NTM) EPS on each date: the next 365 days of quarterly EPS, each quarter "
    "counted in proportion to the share of it inside the window, so it rolls forward daily rather than stepping "
    "at each report. Quarters already reported use reported EPS; later ones use Yahoo's current estimates.\n"
    "- **Numerator.** The daily split-adjusted close.\n"
    "- **Hindsight.** Past multiples use the EPS that was later realized, not what analysts expected at the time, "
    "so the history is a perfect-foresight P/E.\n"
    "- **Estimates beyond the next two quarters** repeat the same quarter a year earlier, scaled by the expected "
    "growth from this fiscal year to the next.\n"
    "- **Negative EPS.** If NTM EPS is negative today, no chart is drawn. Earlier dates with negative NTM EPS "
    "are left out of the line, the mean and the band.\n"
    "- **Band.** Mean, ±1σ and ±2σ are computed over the loaded lookback window."
)

st.subheader("Forward P/E vs sector and S&P 500", anchor=False)
st.markdown(
    "The comparison panel plots the same NTM P/E for one stock against the average of its GICS sector and of the "
    "S&P 500. It has no historical mean or band.\n"
    "- **Average.** Total market cap of the group divided by its total NTM earnings, the way an index P/E is "
    "quoted, so large companies weigh more. It is not a mean or median of the stocks' P/Es.\n"
    "- **Negative EPS.** A stock counts on a date only if its NTM EPS is positive, so loss-makers are left out "
    "of both market cap and earnings. The footer says how many stocks the latest value counts.\n"
    "- **Weights.** Today's share counts are used for the whole window, so past market caps are approximate. "
    "The universe is today's S&P 500 constituents.\n"
    "- **Data.** EPS for all constituents is downloaded once (a few minutes on the first run) and refreshed "
    "weekly."
)

st.subheader("Rolling correlation", anchor=False)
st.markdown(
    "Correlation between two S&P 500 stocks, recomputed every trading day over a trailing window.\n"
    "- **Returns.** Daily % change of the split- and dividend-adjusted close (total return), only on days both "
    "stocks have a close.\n"
    "- **Window.** 1M, 3M, 6M or 12M = 21, 63, 126 or 252 trading days. A value is shown only once the window is "
    "full, so a recently listed stock starts its line later.\n"
    "- **Statistic.** Pearson correlation, from −1 (move opposite) to +1 (move together).\n"
    "- **Band.** Mean, ±1σ and ±2σ of the rolling correlation over the loaded lookback window. The ±2σ band can "
    "extend past ±1; it is a statistical band, not a bound."
)

st.subheader("Correlation matrix", anchor=False)
st.markdown(
    f"Correlation of daily returns between up to {panels.MATRIX_MAX} S&P 500 stocks and country indices combined, "
    "over a timeframe from 1M to 5Y back from the latest close.\n"
    "- **Returns.** Stocks use the split- and dividend-adjusted close (total return, USD); country indices use the "
    "index level (price return, local currency).\n"
    "- **Pairs.** Each pair uses only the days both have a close. A pair with fewer than "
    "15 shared daily returns in the timeframe is left blank.\n"
    "- **Time zones.** Asian markets close before Europe and the US open, so a same-day return misses moves that "
    "happen after the earlier close. Daily correlations between different sessions are therefore understated.\n"
    "- **Colours.** Fixed scale from −1 to +1, neutral at 0. Near −1 uses the up colour (green by default) and near "
    "+1 the down colour (red by default), following the sidebar convention. The value is printed in every cell."
)

st.subheader("Forward P/E screen", anchor=False)
st.markdown(
    "One row per S&P 500 stock, sortable by any column (click the header; click again to reverse), with a search "
    "box and a sector filter. Opens largest market cap first.\n"
    "- **Forward P/E.** The same NTM P/E as the chart, on the latest close. Negative or missing NTM EPS shows as —.\n"
    "- **σ vs average.** Today's forward P/E minus its mean over the sidebar lookback, divided by its standard "
    "deviation over the same window: the same numbers as the chart's bands.\n"
    "- **Sector P/E.** The GICS sector average from the comparison panel (total market cap over total NTM "
    "earnings, negative EPS excluded).\n"
    "- **Market cap.** Today's shares outstanding per share class × the last completed US close, so dual-class companies appear once per "
    "class."
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
