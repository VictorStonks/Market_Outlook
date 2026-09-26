"""Market Outlook: entrypoint. Page setup, global sidebar controls, navigation (DESIGN.md sections 4 and 9.2).

Run from the project root: streamlit run app.py
"""
import streamlit as st

import data
import panels
import theme

theme.setup_page()  # must be the first Streamlit call (set_page_config)
data.require_api_key()

# Global controls render on every run so their session_state keys persist across pages.
with st.sidebar:
    st.header("Controls")
    panels.global_controls()
    st.caption(theme.DISCLAIMER)

pages = st.navigation([
    st.Page("views/overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page("views/macro.py", title="Macro", icon=":material/public:"),
    st.Page("views/rates_credit.py", title="Rates & Credit", icon=":material/show_chart:"),
    st.Page("views/equities.py", title="Equities", icon=":material/candlestick_chart:"),
    st.Page("views/watchlist.py", title="Watchlist", icon=":material/visibility:"),
    st.Page("views/taa.py", title="TAA scorecard", icon=":material/table_chart:"),
    st.Page("views/methodology.py", title="Methodology & data", icon=":material/menu_book:"),
])
pages.run()
