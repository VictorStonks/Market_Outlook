"""Macro: inflation, growth, labour market (DESIGN.md section 4)."""
import panels
import specs

panels.page_header("Macro")
lookback_years, ma_window = panels.global_settings()
panels.render_grid(specs.MACRO, lookback_years, ma_window)
