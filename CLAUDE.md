# Market Outlook: project instructions

Personal investment dashboard (Streamlit + FRED + yfinance) that doubles as a portfolio piece for a
senior researcher / portfolio manager role in Singapore.

## Before any UI work
Read `DESIGN.md` in full. It is the design spec; follow it over any default habit. In particular:

- Use `theme.py` for all colours, fonts, formats, charts and UI components. Never hard-code hex colours,
  font names or number formats in a page.
- Charts are Plotly via `theme.render(theme.line_fig(...))` and siblings. No Altair, matplotlib or seaborn.
- Style via `.streamlit/config.toml` first. No CSS targeting Streamlit's internal class names or test-ids.
- Every panel needs title, unit, source, frequency, as-of date and a generated takeaway (`takeaways.py`).
  Takeaway text is computed from data, never hand-written, and never forecasts or advises.
- Use `width="stretch"`, not `use_container_width`.

## Run and verify
- Run from the project root: `streamlit run app.py` (Streamlit reads `.streamlit/config.toml` from the CWD).
- After any theme or component change run `streamlit run styleguide.py` and work through the checklist in
  `DESIGN.md` section 13 (both themes, all three up/down modes).
- Python environment: conda env `streamlitenv`.

## Never
- Put API keys in source. The FRED key goes in `.streamlit/secrets.toml` (gitignored) and is read with
  `st.secrets["FRED_API_KEY"]`.
- Write `.streamlit/config.toml` with a BOM (see `DESIGN.md` 9.5).
- Invent the TAA scoring methodology; `DESIGN.md` section 12 lists only the fixed UI constraints.

## Record deviations
If you must depart from `DESIGN.md`, log it in section 14 with a one-line reason and the date.
