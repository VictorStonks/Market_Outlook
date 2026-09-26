"""Watchlist: placeholder until the yfinance table is built (DESIGN.md sections 4 and 11 step 9)."""
import streamlit as st

import panels

panels.page_header("Watchlist")

# Panel-specific control, so it lives on the page rather than in the global sidebar (DESIGN.md 4).
raw = st.text_input("Tickers (comma-separated)", "AAPL, MSFT, SPY", key="watchlist_tickers")
tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
st.caption(f"Tracking: {', '.join(tickers) if tickers else 'none'}")
st.info("Not built yet: price, changes and sparklines per ticker (Yahoo Finance via yfinance).")
