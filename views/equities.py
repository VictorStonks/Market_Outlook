"""Equities: stock price, normalised price, sector return, country return, forward P/E, forward P/E comparison, rolling correlation and forward P/E screen panels (yfinance) (DESIGN.md sections 4 and 11 step 9)."""
import streamlit as st

import panels

panels.page_header("Equities")
lookback_years, ma_window = panels.global_settings()
left, right = st.columns(2)  # two-column grid, as on Rates & Credit (DESIGN.md 4)
with left:
    panels.render_stock_price_panel(lookback_years, ma_window)
with right:
    panels.render_normalised_panel(lookback_years, ma_window)
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
left, right = st.columns(2)  # rolling correlation of two stocks, and a correlation matrix of stocks and countries
with left:
    panels.render_correlation_panel(lookback_years)
with right:
    panels.render_corr_matrix_panel()
panels.render_pe_screen_panel(lookback_years)  # every stock's forward P/E, target and upside in one sortable table
