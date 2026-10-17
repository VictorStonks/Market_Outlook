"""FRED download: search or pick FRED series, see their metadata, choose transforms, frequency and timeframe, preview
the raw table and download it as one CSV (DESIGN.md 14, 2026-10-17). Same password as the Yahoo Finance download."""
import panels
import specs

panels.page_header("FRED download")
if panels.download_unlocked():
    panels.render_fred_download_panel(specs.known_series())
