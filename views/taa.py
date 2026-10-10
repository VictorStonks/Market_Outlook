"""TAA scorecard: US equity valuation (Shiller CAPE and its regression on the next 10 years' real return), then the
scorecard placeholder. The scoring methodology is undecided; do not invent one (DESIGN.md section 12)."""
import streamlit as st

import panels

panels.page_header("TAA scorecard")
panels.render_cape_row()  # Shiller CAPE vs history (left), CAPE vs next 10Y real return and its expected return (right)

st.subheader("Scorecard", anchor=False)
st.info("Not built yet: the scoring methodology has not been decided.")
st.markdown(
    "Fixed by the design guide:\n"
    "- One row per asset class; columns for signal groups, a composite and a categorical view "
    "(Overweight / Neutral / Underweight).\n"
    "- Cells print the z-score or percentile and are filled with the diverging colour ramp; "
    "the view also carries an arrow or label.\n"
    "- Selecting a row opens the underlying panels (charts and takeaway) for that asset class.\n"
    "- A visible methodology expander lists signals, weights, lookbacks and data sources; "
    "as-of dates and staleness flags apply to every input."
)
