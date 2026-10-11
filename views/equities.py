"""Equities: stock price, normalised price, sector return, country return, forward P/E, forward P/E comparison, forward P/E screen, rolling correlation, options open interest and analyst target probability panels (yfinance) (DESIGN.md sections 4 and 11 step 9)."""
import streamlit as st

import panels

panels.page_header("Equities")
st.caption(panels.UNIVERSE_NOTE)  # default universe is the S&P 500; other Yahoo tickers can be typed into any stock box
lookback_years, ma_window = panels.global_settings()
panels.render_stock_row(lookback_years, ma_window)  # stock price and normalised price; the right follows the left
left, right = st.columns(2)  # sector panels have their own timeframe pills, so they ignore the sidebar lookback
with left:
    panels.render_sector_line_panel()
with right:
    panels.render_sector_table_panel()
left, right = st.columns(2)  # country panels also have their own timeframe pills
with left:
    panels.render_country_line_panel()
with right:
    panels.render_country_table_panel()
panels.render_ntm_pe_row(lookback_years)  # forward P/E history and its comparison to the sector / S&P 500
panels.render_pe_screen_panel(lookback_years)  # every stock's forward P/E, σ vs its own average and sector P/E
left, right = st.columns(2)  # rolling correlation of two stocks, and a correlation matrix of stocks and countries
with left:
    panels.render_correlation_panel(lookback_years)
with right:
    panels.render_corr_matrix_panel()
panels.render_options_row()  # open interest (max pain, dealer gamma), then analyst targets vs the implied range
