"""Rates & Credit: Treasuries, curve, breakevens, corporate spreads and yields (DESIGN.md section 4)."""
import panels
import specs

panels.page_header("Rates & Credit")
lookback_years, ma_window = panels.global_settings()
panels.render_grid(specs.RATES_CREDIT, lookback_years, ma_window)
