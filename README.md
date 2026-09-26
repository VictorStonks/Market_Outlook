# Market Outlook

A personal investment research dashboard built with Streamlit, using free public data
([FRED](https://fred.stlouisfed.org/) and [yfinance](https://github.com/ranaroussi/yfinance)).
Bloomberg-inspired, dense but scannable: every panel shows title, unit, source, frequency, as-of date
and a takeaway generated from the data.

## Pages
Overview · Macro · Rates & Credit · Equities · Watchlist · TAA scorecard · Methodology & data

## Run locally
```
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then add your free FRED API key
streamlit run app.py
```
Run from the project root so Streamlit picks up `.streamlit/config.toml`.

## Design
See [DESIGN.md](DESIGN.md) for the design spec. All colours, fonts and chart styles live in `theme.py`.

## Disclaimer
For information and research purposes only. Nothing here is investment advice or a forecast.
Data is provided by third parties and may be delayed or incomplete.

## License
[MIT](LICENSE)
