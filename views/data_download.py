"""Yahoo Finance download: pick Yahoo Finance tickers, see which datasets each has, choose datasets, interval, timeframe and
price adjustment, preview the raw table and download it as one CSV (DESIGN.md 14, 2026-10-17). Password-protected."""
import panels

panels.page_header("Yahoo Finance download")
if panels.download_unlocked():
    panels.render_download_panel()
