# Market Outlook — UI/UX Design Guide

Audience of this file: an AI coding assistant building or modifying the dashboard. Read all of it before
touching any UI code. Rules use **MUST / MUST NOT / SHOULD** in the RFC sense. If a rule here conflicts
with a habit or a tutorial, this file wins. If something is not covered, choose the option that is
denser-but-scannable, more consistent with existing panels, and more accessible, then record the decision
in section 14.

Companion files (all in the project root): `theme.py` (tokens + chart/UI helpers = this spec in code),
`.streamlit/config.toml` (Streamlit theme), `takeaways.py` (automated takeaway text), `styleguide.py`
(living style guide; run it to see every token and component).

---

## 1. Purpose and audience

- **What it is.** A personal investment research dashboard (macro, rates and credit, equities, watchlist,
  and later a tactical asset allocation (TAA) scorecard) built on free public data (FRED, yfinance) in Streamlit.
- **Two jobs.** (1) The owner's daily decision-support tool. (2) A portfolio piece shown to senior researchers
  and portfolio managers in Singapore. It must look like a professional research terminal and show sound
  judgement: clear reasoning, transparent methods, honest caveats.
- **Reference feel.** Bloomberg-inspired, not a clone: dark, dense-but-breathable, one warm accent, monospace
  numerics, no decoration. Never copy Bloomberg's branding, logo, or exact palette.
- **Reader model.** An expert who wants the answer in 5 seconds (headline numbers, what changed) and the
  evidence in 30 seconds (charts with context), and who can drill into raw data when needed.

## 2. Principles (in priority order)

1. **Glanceable, then drillable.** Every page: headline numbers first, charts second, raw data last (expanders).
2. **Every number carries context.** Value + change + position in its own history (percentile or z-score) +
   unit + as-of date + source. A bare number is a defect.
3. **Consistency beats cleverness.** One colour system, one chart style, one panel anatomy, one number format.
   Never define colours, fonts or formats inline in a page; import them from `theme.py`.
4. **Data ink over decoration.** No gauges, pies, 3D, gradients, shadows, emoji, animation or chart junk.
5. **Colour is never the only signal.** Pair with sign, arrow, label or position. Contrast is at least 4.5:1
   for text and 3:1 for chart lines and UI fills.
6. **Describe, don't forecast.** Automated text states what the data shows. It does not predict or advise.
7. **Honest about data.** Show frequency, as-of date, staleness, gaps and provider limits. Never hide or
   silently fill a gap.
8. **Fast and calm.** Only the active page runs; panels update independently; nothing flashes or jumps.

## 3. Design tokens

Single source of truth in code: `theme.TOKENS`, `theme.UPDOWN` and `.streamlit/config.toml`. The values in
this section, `theme.py` and `config.toml` MUST match. To change a colour: edit this table, `theme.py`, and
`config.toml` in the same commit, then run `styleguide.py` in both themes and all three up/down modes.

### 3.1 Colour

| Token | Dark (default look) | Light | Role |
|---|---|---|---|
| `bg` | `#0B0F14` | `#F7F9FC` | App background (`backgroundColor`) |
| `surface` | `#121821` | `#FFFFFF` | Sidebar, chart plot area (`secondaryBackgroundColor`) |
| `raised` | `#1A2230` | `#EEF2F7` | Table header, hover, code background |
| `border` | `#2A3446` | `#D5DCE6` | Dividers, gridlines. Decorative only, never the sole boundary of an interactive control |
| `text` | `#E6EAF0` (15.9:1) | `#101828` (16.8:1) | Primary text |
| `text2` | `#A3AEBF` (8.6:1) | `#475467` (7.3:1) | Secondary text, axes, captions |
| `muted` | `#7A8699` (5.2:1) | `#667085` (4.7:1) | Lowest allowed text tone; hairlines and spikes |
| `accent` | `#FFB000` (10.5:1) | `#9A5B00` (5.1:1) | **Data ink**: series 1, highlights, the "Latest" curve |
| `primary` | `#A86300` | `#9A5B00` | **Streamlit control fill** (buttons, chips, sliders). See 3.1.1 |
| `up` / `down` | `#26C281` / `#F0616D` | `#0A7C50` / `#C8323F` | Favourable / unfavourable (default mode) |

Contrast figures are WCAG ratios against `bg`, computed, not estimated.

**3.1.1 `accent` vs `primary`.** Streamlit draws white text on `primaryColor`. Bright amber `#FFB000` gives
white text only 1.8:1, so it MUST NOT be `primaryColor` in the dark theme (verified: it fails). Dark
`primaryColor` is `#A86300` (white text 4.7:1); the bright amber stays for data ink. Never set `primaryColor`
to a colour where white text is below 4.5:1.

**3.1.2 Up/down convention.** "Up" means favourable (price rising, unemployment falling). Three modes, chosen
in the sidebar (`theme.display_controls()`), default `green_up`:

| Mode | Up | Down | Note |
|---|---|---|---|
| `green_up` | green | red | Western default |
| `red_up` | red | green | East Asian convention; offered because the audience is Singapore-based |
| `cvd` | blue `#4C9BFF` (light `#0B5FCC`) | orange `#FF9F43` (light `#A85200`) | Colour-blind safe |

Rules: read colours via `theme.tokens()["up"/"down"]` or `theme.delta_color()`, never hard-code green or red.
Green and red are reserved for favourable/unfavourable and MUST NOT be used as series colours. Every delta
also carries a sign and an arrow (`theme.fmt_delta`). Neutral changes (e.g. a yield level) use `text2`.

### 3.2 Chart palettes

| Use | Dark | Light |
|---|---|---|
| **Categorical**, unordered series, max 6 per chart, series 1 = accent | `#FFB000 #56B4E9 #E07BB5 #9B8CFF #C7D0DC #38D6CF` | `#9A5B00 #0072B2 #B5478A #6B5CE0 #5B6675 #077A76` |
| **Ordered**, ordered families (credit quality AAA→B, maturity buckets); first series = strongest | `#D9EEFC #B4DCF8 #8FC8F2 #6BB2EB #4A9CDD #2E86C8` | `#0F3B5D #154A73 #1B5989 #21689F #2777B5 #2E86C8` |
| **Diverging**, z-scores and signal heatmaps | `#F0616D → #1A2230 → #26C281` (10 steps; see `config.toml`) | `#C8323F → #EEF2F7 → #0A7C50` |
| **Sequential**, magnitude heatmaps | `#12314D → #D9EEFC` (10 steps) | `#EAF3FB → #08306B` |

- Ordered families MUST use the ordered ramp, not a rainbow. Access via `theme.ordered_colors(n)`.
- Categorical via `theme.categorical_colors(n)`. More than 6 series: filter, or use small multiples.
- Comparison curves ("1 Month Ago", "6 Months Ago"): "Latest" is solid accent with markers; comparisons are
  dashed `text2` at decreasing opacity (`theme.curve_fig`).
- Moving averages: dashed, `text2`. Mean/±1σ: dashed mean line plus a shaded band at `muted` 20% opacity.
  Never draw ±σ as two coloured lines.
- The Streamlit `chart*Colors` in `config.toml` colour only native `st.line_chart`-style charts, which are
  banned (see 6.4); they exist so any accidental native chart is still on-palette.

### 3.3 Typography

| Item | Spec |
|---|---|
| Body font | IBM Plex Sans (Google Fonts, loaded by `config.toml`), fallback `sans-serif` |
| Numerals / code / tickers | IBM Plex Mono for code; numerals use the body font with **tabular figures** |
| Base size | 14px |
| Headings h1–h6 | 24, 18, 15, 14, 13, 12 px, weight 600 |
| Page title | h1 (`st.title`); panel title h3 (`st.subheader(..., anchor=False)`) |
| KPI value | 28px, weight 600 (`metricValueFontSize`) |
| Captions (units, source, as-of) | Streamlit caption (about 12px) |

Tabular figures are mandatory so digits align. Verified on Streamlit 1.58: IBM Plex Sans digits are equal
width by default, and `theme.setup_page()` also sets `font-variant-numeric: tabular-nums`. Do not switch
fonts without re-running the "1111 / 8888 same width" check in `styleguide.py`.

### 3.4 Spacing, shape, motion

- 4px grid: 4 / 8 / 12 / 16 / 24 px. Use Streamlit's own gaps; do not add spacer hacks (`st.write("")`).
- `baseRadius = "small"`. 1px `border` outlines separate panels (`st.container(border=True)`). No shadows.
- Chart heights: 300px standard, 400px hero, 220px in a 3-across small-multiple row.
- No animation, transitions or auto-refreshing pulses. Streamlit's own spinner is the only motion.

### 3.5 Voice and copy

- Sentence case for titles and labels ("Yield curve steepness", not "Yield Curve Steepness").
- Terse, factual, numbers first. No exclamation marks, no emoji, no marketing tone.
- Units: `%` for rates and yields, `pp` for percentage-point differences, `bp` for basis points, `k`/`m`/`bn`
  for large numbers. Show credit spreads (OAS) in **bp** on every page or in **%** on every page, but never mix.
- Icons: Streamlit Material Symbols (`:material/trending_up:`), used sparingly. No emoji.
- Working name/wordmark: "Market Outlook", text only.

## 4. Information architecture

Use `st.navigation` with page functions/files in a `views/` folder (not `pages/`; that name triggers
Streamlit's legacy auto-discovery). Only the active page's code runs, which is why this replaces `st.tabs`
(tabs execute every tab body on every rerun).

| Page | Contents (map from the current `app.py`) |
|---|---|
| **Overview** | KPI strip (10Y, 2Y, 10Y–2Y, HY OAS, later an equity index), "What changed" strip, 4–6 hero panels |
| **Macro** | Inflation, real GDP growth, unemployment, nonfarm payrolls, initial claims |
| **Rates & Credit** | Treasury rates, yield curve (+ change bars), breakevens, curve steepness, corporate OAS by quality, OAS vs history (band), YTW, corporate index curve |
| **Equities** | Index levels, sector performance (yfinance), later price/candlestick |
| **Watchlist** | One row per ticker: price, changes, sparkline (`st.column_config.LineChartColumn`) |
| **TAA Scorecard** | Placeholder; see section 12 |
| **Methodology & data** | Sources, series IDs, frequencies, how takeaways are computed, disclaimer |

**Page anatomy (top to bottom):** page title + caption ("Refreshed 19 Sep 2026 14:30 SGT"); KPI strip (1–6
tiles); optional "What changed" strip (Overview only); panel grid (2 columns; 1 column only for a hero panel;
3 only for small multiples); no footer beyond panel footers.

**Sidebar = global controls only:** lookback window, moving-average window, up/down colours, refresh. Panel-
specific controls (series pickers, comparison selectors) live inside that panel, above the chart.

**Layout mode:** `layout="wide"` (set by `theme.setup_page()`); `initial_sidebar_state="expanded"`.

## 5. Components

### 5.1 KPI tile: `theme.metric_tile` / `theme.kpi_strip`

- Content: label, value (with unit), signed delta over a stated window, sparkline (last 90–120 points).
- Max 6 tiles per strip; `st.columns(n)`; `border=True`.
- `good_when`: `"up"` for prices/equities, `"down"` for unemployment or spreads, `None` (grey) for levels with
  no good/bad reading (yields).
- The delta window (1D, 1M, ...) MUST be stated in the label or help text.

### 5.2 Panel anatomy: every chart is a panel

`with st.container(border=True):` then, in order:

1. `theme.panel_header(title, subtitle, takeaway)`: title (h3); subtitle = unit + frequency ("% YoY · monthly");
   takeaway = one generated sentence (section 8).
2. Panel-local controls (multiselect, comparison picker), if any.
3. The chart via `theme.render(fig, key=...)`.
4. `theme.panel_footer(source, freq, asof, caveat=..., stale=...)`: source · frequency · as-of date · optional
   `STALE` badge; optional provider caveat on a second line (e.g. "Data supplier limits history to 3Y").
5. `st.expander("Raw data")` with the table, collapsed by default.

Every panel MUST have all of: title, unit, source, frequency, as-of. Missing any is a defect.

### 5.3 Tables

- Use `st.dataframe(..., hide_index=True, width="stretch")` with `column_config`:
  `NumberColumn(format="%.2f")` (signed deltas: `"%+.2f"`), `LineChartColumn()` for sparklines, units in headers.
- Right-align numbers (Streamlit default for numeric columns). Missing values render as "—".
- Colour a table cell only when it encodes a signal; use `theme.delta_color()` via a pandas Styler. Never colour
  whole rows.
- Tables are canvas-rendered: CSS does not apply. They take font and colours from the theme, so keep the theme
  correct.

### 5.4 Data states (design all four, every panel)

| State | Treatment |
|---|---|
| **Loading** | Streamlit spinner with text naming the series ("Loading DGS10…"); layout keeps its size |
| **Stale** | `:orange-badge[STALE]` in the footer plus an entry in "What changed". Rule in section 8.4 |
| **Empty** | `st.info("Select at least one series to plot.")`, in place of the chart, not a blank gap |
| **Error** | `st.warning` naming the series and what is shown instead ("Fetch failed for DGS10; showing cached data from 18 Sep 2026"). Never a raw traceback. Set `[client] showErrorDetails = "none"` for public deployment |

### 5.5 Filters and controls

- Prefer `st.pills` / `st.segmented_control` for short option sets (lookback), `st.multiselect` for series.
- Keep option order stable; default selections show the full useful view (all series), not an empty chart.
- Put the timeframe control adjacent to the chart it controls; a sidebar control is allowed only when it is
  genuinely global.

## 6. Charts

Library: **Plotly**, via `theme.py` helpers only. Charts are rendered with `theme.render(fig)`
(`st.plotly_chart(fig, width="stretch", theme=None, config=...)`). Altair/matplotlib/seaborn MUST NOT be used
for new charts, and their imports MUST be removed from the project.

### 6.1 Helper API (`theme.py`)

| Function | Use |
|---|---|
| `line_fig(series, ordered=False, dashed=(), unit="", decimals=2, height=300, baseline=None)` | Time series on a shared y-axis. Replaces `line_chart`. `baseline` adds a dotted reference line (100 for rebased series) |
| `band_fig(label, s, k=1, unit, decimals, height)` | Series vs mean ±kσ band. Replaces `line_chart_with_std_dev` |
| `curve_fig(curves, unit="%", ...)` | Yield/term-structure curves; must include a `"Latest"` key. Replaces `yield_curve_chart` |
| `bar_fig(df, unit, decimals, x_title)` | Grouped bars incl. a zero line. Replaces `yield_curve_change_chart`'s bars |
| `render(fig, key)` | The only way to display a Plotly figure |
| `fmt_num`, `fmt_delta`, `delta_color` | Number formatting and change colouring |
| `metric_tile`, `kpi_strip`, `panel_header`, `panel_footer` | UI components |
| `tokens()`, `categorical_colors(n)`, `ordered_colors(n)` | Colours; never hard-code |

If a needed chart is not here, add a helper to `theme.py` (using `_base_fig` and the template) rather than
building a one-off figure in a page.

### 6.2 Which chart for which question

| Question | Chart |
|---|---|
| How has one series moved? | Line (`line_fig`) |
| How do related, unordered series compare? | Multi-line, ≤6 series, direct hover |
| How do ordered series compare (AAA→B, maturities)? | Multi-line with the ordered blue ramp (`ordered=True`) |
| Is this level rich or cheap vs history? | `band_fig` (mean ±1σ), plus percentile in the takeaway |
| What is the term structure? | `curve_fig` (points + line), with dated comparison curves |
| How much did each bucket change? | `bar_fig`, zero baseline, grouped by comparison |
| Which is biggest/smallest? | Horizontal bars, sorted |
| Many series at once / correlations / signals | Heatmap with diverging ramp, value printed in each cell |
| Relative performance | Lines rebased to 100 |
| Price action (later) | Plotly `go.Candlestick` |

**Banned:** gauges/speedometers, pie/donut, 3D, dual y-axes (rebase to 100 or stack two panels), dense
legends over 6 entries, rainbow palettes, area fills that hide other series, truncated bar axes.

### 6.3 Chart standards

- Lines 2px (moving averages 1.5px dashed); `x unified` hover with values in mono via `hovertemplate`; dotted
  crosshair spike on x.
- Horizontal gridlines only, in `border` colour; no vertical gridlines; no chart borders or titles (the panel
  provides the title).
- Level series: y-axis auto-fits the data range (do not force zero). Bar charts: always include zero.
- The unit is in the y-axis tick suffix (`unit="%"`) and the panel subtitle. Never leave a chart unit-less.
- Date ticks `%b %y`; hover header `%d %b %Y`.
- Legend: horizontal, above the plot, left-aligned; if a chart has a single series, hide the legend.
- Recession shading on macro history charts (FRED series `USREC`), in `muted` at low opacity. (To implement
  when the Macro page is built; not in `theme.py` yet.)
- Modebar hidden (`PLOTLY_CONFIG`); zoom/pan is not needed for a monitoring dashboard.
- Mixed-frequency series (e.g. monthly with daily): drop NaNs per series before plotting (`line_fig` does)
  so lines stay joined.
- Streamlit forces the Plotly paper to `backgroundColor` and the plot area to `secondaryBackgroundColor`
  (verified 1.58); the template matches, so charts read as a `surface` "well" on the page. Do not try to
  make them transparent.

## 7. Data conventions

- **Numbers:** thousands separators; true minus `−` (U+2212), never a hyphen, never parentheses; explicit `+`
  on deltas; decimals per series (rates 2, indices 0–1, payroll changes 0); use `theme.fmt_num`/`fmt_delta`
  everywhere text is shown. In `st.metric` deltas the ASCII sign is used (Streamlit needs it to pick the arrow).
- **Missing:** "—" (`theme.MISSING`). Never 0, NaN or blank.
- **Dates:** `19 Sep 2026`. Data as-of dates are the *observation* date; refresh times are stamped in
  **SGT** (`zoneinfo.ZoneInfo("Asia/Singapore")`; on Windows this needs the `tzdata` package). Keep the two distinct.
- **Frequency:** every panel states it (daily/weekly/monthly/quarterly). Do not mix a monthly and a daily
  series on a chart without saying so in the subtitle.
- **Transforms:** state them in the subtitle ("YoY % change", "MoM change"). The FRED `units` parameter
  (`pc1`, `chg`, ...) is the preferred way; document the code in the `PanelSpec`.
- **Caveats belong in the footer**, per panel, as data ("Note: ..."), not buried in chat or code comments.
- **Source attribution:** "FRED" / "Yahoo Finance via yfinance" per panel; series IDs listed on the
  Methodology page.
- **Disclaimer** (Methodology page and app caption): "Personal project. Public data only. Not investment
  advice. Views are the author's own and do not represent any employer."

## 8. Automated takeaways

Each panel shows one sentence describing its main series, generated by `takeaways.summarise()` on every load.
The owner does not review the dashboard often, so generated text can never go stale and hand-written text on
panels is forbidden.

### 8.1 Rules

- Text is **computed**, from the loaded data, in `takeaways.py`. No hand-written per-panel commentary.
- It **describes** (level, percentile, change, extremes, staleness). It MUST NOT forecast, recommend, or use
  words like "should", "expect", "likely", "bullish", "bearish", "buy", "sell".
- Numbers in the sentence use `theme.fmt_num` with the panel's unit and decimals.
- No LLM is called at render time. (An optional, scheduled, cached, AI-labelled weekly note is deliberately
  out of scope for now; do not add it.)
- Interpretive commentary ("what this means") may exist only on the Overview page as an owner-written, dated
  note, or not at all.

### 8.2 The sentence

```
{label} is {level}{unit}, {N}th percentile of the {window} range; {+/−change}{unit} over {change_label}.
[Near the {window} high. | Near the {window} low. | More than 2σ from its mean.] [{spec.note(level)}]
```

Example: "10Y–2Y spread is 0.60 pp, 100th percentile of the 5Y range; +0.08 pp over 1M. Near the 5Y high.
Curve positively sloped."

Percentile is within the *loaded lookback window* and the sentence says so ("of the 5Y range"). The `note`
callback is for factual regime flags (e.g. inverted curve), defined per panel.

### 8.3 "What changed" strip (Overview)

`takeaways.whats_changed()` lists: moves beyond 2σ of typical same-length changes; series at the top/bottom
2% of their range; stale series. If empty, render "Nothing notable versus the loaded range." (an empty state,
not a blank gap).

### 8.4 Staleness

A series is STALE when its latest observation is older than `MAX_AGE_DAYS[freq]` (D 7, W 14, M 80, Q 210),
which allows for normal publication lag. These are heuristics; if a series is flagged wrongly, adjust its
`freq` in the spec, not the thresholds, and note why in section 14.

## 9. Streamlit implementation rules

### 9.1 Styling policy (in order)

1. **`.streamlit/config.toml` first.** Fonts, colours, radius, sizes: all official settings live here.
2. **`theme.py`** for anything the config cannot express (Plotly template, formatters, components).
3. **CSS only as a last resort.** The only allowed CSS is the single `font-variant-numeric` rule already in
   `theme.setup_page()`. Do not target Streamlit's generated class names or `data-testid`s: they change between
   releases and break silently. If you truly need more, put it in one clearly-labelled block in `theme.py` and
   note it in section 14.
4. **Pin the Streamlit version** in `requirements.txt` once the app is working; upgrade deliberately, then run
   `styleguide.py` and the checklist in section 13.

### 9.2 Project layout

```
Market Outlook/
├─ app.py                  # entrypoint: setup_page, sidebar controls, st.navigation
├─ theme.py  takeaways.py  # design system + takeaway logic
├─ data.py                 # fred_data, yfinance loaders, caching, secrets
├─ panels.py               # PanelSpec + render_panel (section 10)
├─ specs.py                # panel catalogue: every FRED panel as a PanelSpec (added 2026-09-19)
├─ views/                  # overview.py macro.py rates_credit.py equities.py watchlist.py taa.py methodology.py
├─ .streamlit/config.toml  # theme (committed)
├─ .streamlit/secrets.toml # FRED key (NEVER committed; in .gitignore)
├─ styleguide.py  DESIGN.md  CLAUDE.md  requirements.txt  .gitignore
```

Run everything **from the project root** (`streamlit run app.py`): Streamlit reads `.streamlit/config.toml`
from the current working directory.

### 9.3 Performance and structure

- Data functions: `@st.cache_data(ttl=3600)`, in `data.py`. Panels never call the API directly.
- Each panel is an `@st.fragment` so its own controls rerun only that panel.
- The entrypoint runs global sidebar controls, then `st.navigation(...).run()`. Sidebar widgets are rendered
  every run so their `session_state` keys persist across pages.
- Use `width="stretch"` for charts and tables. `use_container_width` is deprecated (scheduled for removal
  after 2025-12-31) and MUST NOT be used.

### 9.4 Secrets

Read the FRED key with `st.secrets["FRED_API_KEY"]` from `.streamlit/secrets.toml`. Never in source, never in
committed files. (The key currently hard-coded in `app.py` has been shared in chat and SHOULD be regenerated
at fred.stlouisfed.org.)

### 9.5 Verified behaviours on Streamlit 1.58 (do not re-discover these)

- `chartCategoricalColors` / `chartSequentialColors` / `chartDivergingColors` are valid only under `[theme]`,
  not `[theme.dark]` / `[theme.light]` (Streamlit logs "not a valid config option" and ignores them).
- Defining both `[theme.light]` and `[theme.dark]` makes the app follow the OS setting; users can switch in the
  Settings menu. To force dark-only, delete the `[theme.light]` table.
- `st.plotly_chart(theme=None)` still gets Streamlit's paper/plot background injected (section 6.3).
- `st.context.theme.type` can be wrong on the first script run of a session; `theme.theme_mode()` falls back to
  dark and self-corrects on the next rerun.
- `config.toml` MUST be UTF-8 **without** BOM. A BOM breaks TOML parsing ("invalid character in key name").
  On Windows PowerShell 5, `Set-Content -Encoding utf8` writes a BOM; use a BOM-less writer.
- `st.metric` supports `chart_data` sparklines and named `delta_color`s on 1.58; `theme.metric_tile` degrades
  gracefully on older versions.
- Tabs run all tab bodies every rerun; that is a reason to use pages, not tabs.

## 10. Config-driven panels (kill the copy-paste)

The current app repeats "fetch, multiselect, chart, raw-data expander" about 8 times. Define panels as data:

```python
# panels.py
@dataclass(frozen=True)
class PanelSpec:
    key: str
    title: str
    subtitle: str                      # unit + frequency: "% YoY · monthly"
    series: dict[str, str]             # label -> FRED series id
    fred_units: str = "lin"            # FRED server-side transform (pc1, chg, ...)
    unit: str = "%"
    freq: str = "M"                    # D | W | M | Q
    ordered: bool = False              # ordered family -> blue ramp
    selectable: bool = False           # show a series multiselect
    kind: str = "line"                 # line | band | curve
    caveat: str | None = None
    takeaway: TakeawaySpec | None = None

@st.fragment
def render_panel(spec: PanelSpec, lookback_years: int, ma_window: int | None) -> None:
    with st.container(border=True):
        labels = list(spec.series)
        if spec.selectable:
            labels = st.multiselect("Series", labels, default=labels, key=f"{spec.key}_sel")
        if not labels:
            st.info("Select at least one series to plot."); return
        data = {l: fred_data(spec.series[l], lookback_years, spec.fred_units) for l in labels}
        lead = data[labels[0]]
        sm = summarise(lead, spec.takeaway) if spec.takeaway else None
        theme.panel_header(spec.title, spec.subtitle, sm.text if sm else "")
        plot = dict(data)
        if ma_window and len(data) == 1:
            plot[f"{ma_window}-period MA"] = moving_average(lead, ma_window)
        theme.render(theme.line_fig(plot, ordered=spec.ordered, dashed=[k for k in plot if "MA" in k],
                                    unit=spec.unit), key=spec.key)
        theme.panel_footer("FRED", FREQ_NAMES[spec.freq], sm.asof if sm else lead.index[-1],
                           caveat=spec.caveat, stale=bool(sm and sm.stale))
        with st.expander("Raw data"):
            st.dataframe(pd.concat(data, axis=1), width="stretch")
```

A page is then a list of `PanelSpec`s laid out in a 2-column grid. Adding a panel = adding a spec. The takeaway
describes the first (lead) series only. This is a sketch: adapt, but keep the anatomy in 5.2.

## 11. Migration plan for the existing `app.py`

Do these in order, running the app after each step. Do not combine steps.

1. **Housekeeping.** `pip install plotly tzdata`; create `requirements.txt`; move the FRED key to
   `.streamlit/secrets.toml` (+ `.gitignore`); delete unused imports (`matplotlib`, `seaborn`, `mtick`,
   `altair` once step 4 is done); replace any `use_container_width=True`.
2. **Theme.** Add `.streamlit/config.toml`, `theme.py`; call `theme.setup_page()` first and
   `theme.display_controls()` in the sidebar. Run `styleguide.py`; pass the checklist (section 13).
3. **Extract data.** Move `fred_data`, `moving_average`, `with_moving_average`, `yield_curve_as_of`,
   `maturity_from_range_label` to `data.py`, unchanged in behaviour.
4. **Swap the chart layer** (only these five helpers change; call sites stay):

   | Old | New |
   |---|---|
   | `_dynamic_y_chart`, `line_chart` | `theme.render(theme.line_fig(...))` |
   | `line_chart_with_std_dev` | `theme.render(theme.band_fig(...))` |
   | `yield_curve_chart` | `theme.render(theme.curve_fig(...))` |
   | `yield_curve_change_chart` | `theme.render(theme.bar_fig(...))` + `st.dataframe` |
   | `_LINE_CHART_PALETTE` | delete (use `theme` colours) |

   Credit-quality charts (AAA→B) and the corporate index curve pass `ordered=True`.
5. **Panels.** Introduce `PanelSpec`/`render_panel` (section 10); convert panels one page at a time.
6. **Pages.** Replace `st.tabs` with `st.navigation` and the `views/` files (section 4). Move Treasury,
   yield-curve, breakeven and steepness panels from Macro to Rates & Credit.
7. **KPI strip and takeaways.** Add the Overview page, KPI strips, `TakeawaySpec` per panel, "What changed".
8. **Methodology page and disclaimer.**
9. **Then** equities, watchlist (yfinance) and the TAA scorecard.

## 12. TAA scorecard: placeholder

The scoring methodology is not decided; **do not invent one.** Constraints already fixed by this guide:

- Layout: a heatmap table, one row per asset class; columns for signal groups (e.g. valuation, momentum, macro,
  sentiment), a composite, and a categorical view (Overweight / Neutral / Underweight).
- Cells show the z-score or percentile as a printed number, filled with the **diverging** ramp; the view uses
  an arrow/label as well as colour.
- Row click drills into the underlying panels (charts + takeaway) for that asset class.
- A visible Methodology expander lists signals, weights, lookbacks and data sources; the as-of date and
  staleness flags apply to every input.
- Uses the same tokens, panel anatomy and number formats as everything else.

## 13. QA checklist (run before considering any UI change done)

Run `streamlit run styleguide.py` from the project root and the real app, then confirm:

- [ ] Dark **and** light themes render correctly (switch via the Settings menu or the OS setting).
- [ ] All three up/down modes look right (sidebar selector).
- [ ] The theme fonts are loaded (body renders in IBM Plex Sans); "1111 / 8888" are the same width.
- [ ] Primary button / chips / selected tabs are readable (white text on `primary` is ≥ 4.5:1).
- [ ] No hard-coded hex colours or font names outside `theme.py` / `config.toml`
      (search the pages for `#` colour literals and `color=`).
- [ ] Every panel has title, unit, source, frequency, as-of, and a takeaway (or a stated reason it has none).
- [ ] Every chart has a unit and hover values; no banned chart types; ≤ 6 categorical series.
- [ ] Ordered families use the ordered ramp; bars include zero; ±σ is a band.
- [ ] Empty, loading, stale and error states each exist and were exercised (deselect all series; break a
      series ID; use a stale series).
- [ ] No text below 4.5:1; colour is never the only signal (arrows/signs/labels present).
- [ ] No `use_container_width`, no Altair/matplotlib/seaborn imports, no secrets in source.
- [ ] Only the active page's code runs (no `st.tabs` holding page-level content).
- [ ] Console/terminal shows no Streamlit config warnings ("not a valid config option").
- [ ] Works at a 1280px-wide laptop viewport with no horizontal scroll.

## 14. Decisions and open items

**Decided**
- Look: Bloomberg-inspired, dark default, light supported; amber data accent; IBM Plex Sans.
- Colours as in section 3; accent vs primary split (3.1.1).
- Plotly, wrapped in `theme.py`; config-file-first styling; pages via `st.navigation`.
- Automated, rule-based takeaways; no LLM commentary for now.
- No Bloomberg-style command bar.

**Open / later**
- TAA scorecard methodology (section 12).
- Candlestick helper. (Recession shading helper: done 2026-09-19, `theme.add_recessions`; verified on 10Y USREC data.)
- Whether to enforce a mobile layout (currently desktop-first; laptop ≥ 1280px).
- Optional scheduled LLM weekly note (explicitly deferred).
- Fonts are loaded from Google Fonts at runtime; self-host (`[[theme.fontFaces]]`) if offline use or privacy matters.

*Log any deviation from this guide here with a one-line reason and the date.*

**Migration log and deviations (2026-09-19)**
- Steps 1-8 of section 11 done; step 9 is placeholder pages only (Equities, Watchlist, TAA). TAA methodology not invented.
- Added `specs.py` (panel catalogue) beyond the 9.2 layout: keeps every `views/` page a thin list of specs and lets the Methodology table be generated from the same specs.
- `takeaways.TakeawaySpec` gained `change_unit`; `theme.metric_tile` gained `delta_unit`: a % yield changes in pp (3.5), so "+0.23 pp", not "+0.23%".
- Takeaway label may contain `{series}`; frequency and change window (D 1M, W 1W, M 1M using 28 days, Q 1Q) are derived per lead series in `panels.summarise_lead`, so mixed-frequency panels never mislabel or mis-flag STALE.
- Panels with a mixed-frequency lead set `lead` explicitly (Treasury rates and curve: 10Y; steepness: 10Y-2Y spread); falls back to the first selected series.
- Series labels moved to sentence case (3.5): "Fed Funds" -> "Fed funds", "1-3 Years" -> "1–3 years", "10Y-2Y Spread" -> "10Y–2Y spread".
- Initial claims relabelled from "MoM change" to weekly (WoW) change: `ICNSA` is weekly (checked against FRED metadata); the series and transform are unchanged.
- Yield-curve comparison picker defaults to 1 month and 6 months ago (was none), so the panel opens with context (5.5).
- Yield-curve change bars live in the same panel as the curve (220px), not a separate panel; the change table is in Raw data.
- Watchlist ticker input moved from the sidebar to the Watchlist page: it is panel-specific (4).
- Sidebar lookback and moving-average use `st.segmented_control` (5.5), with a callback that blocks deselecting; "Refresh data" clears the cache.
- ICE BofA caveat added to every ICE BofA panel: FRED serves only ~3Y of history for these series even when 10Y is requested (verified 2026-09-19); previously stated on one panel only.
- Raw-data tables show latest first, dates as `19 Sep 2026`, `theme.fmt_num` formatting and "—" for missing, via a pandas Styler.
- `takeaways.whats_changed` now prints a true minus for negative σ moves; takeaway changes that round to zero carry no sign.
- Error state keeps the last good copy per request in a process-level dict (`data._LAST_GOOD`) so an outage after the 1h cache expires still shows labelled data; it resets when the server restarts.
- Loading state: the spinner names the series but the panel does not reserve its chart height, so layout can shift once on first load.
- `requirements.txt`: streamlit pinned to 1.58.0 (the verified version); matplotlib, seaborn and altair removed.
- `launcher.bat` now runs the conda env `streamlitenv` from the project folder (was bare `python`).
- FRED key moved to `.streamlit/secrets.toml`. The key was previously in source and shared in chat: regenerate it (9.4).
- `styleguide.py` keeps a demo `st.tabs` and one font name in a swatch; it is the living style guide, not an app page.

**Equities data layer (2026-09-19)**
- Added a `cache/` folder beyond the 9.2 layout: saved S&P 500 constituents (`sp500_constituents.csv`) and prices (`sp500_close.parquet`, `sp500_adj_close.parquet`), so the app does not depend on GitHub or Yahoo being reachable (5.4 error state). Data-layer code stays in `data.py`.
- Investment universe = current S&P 500 constituents from a community dataset (`data.UNIVERSE_URL`), not point-in-time membership; Yahoo tickers use dashes (`BRK-B`). State this on the Methodology page.
- Two price databases from one Yahoo download: `close` (split-adjusted, price charts) and `adj_close` (split- and dividend-adjusted, future total-return work). 10Y daily, sliced in memory to the sidebar lookback.
- Today's unfinished bar is dropped while the US market is open (before 16:00 New York time), because yfinance returns the running intraday price as that day's "Close". So a download taken mid-session ends at the previous close.
- Stock price panel (Equities): `panels.render_stock_price_panel`, a standalone function rather than a `PanelSpec` (which is FRED-specific: series ids, FRED units, "FRED" footer). Same 5.2 anatomy. It is a full-width hero panel with a 400px chart (3.4).
- Search is an `st.multiselect` of `AAPL — Apple Inc.` labels (matches ticker or company name), capped at 6 stocks (3.2). Default AAPL, MSFT, NVDA (5.5). Actual prices share one auto-fitting axis, not rebased (6.2 bans dual axes). The takeaway describes the first selected stock; the moving average draws with a single stock and is computed on the full saved history, then cut to the window.
- Normalised price panel (Equities, 2026-09-19): `panels.render_normalised_panel`, beside the stock price panel in a two-column row like Rates & Credit (4). Both are now standard 300px panels, so the price panel dropped from its 400px hero size. Each has its own search box (5.5 / 4: panel-specific controls live in the panel), sharing code via `_stock_picker`.
- Rebasing: each line = close / its price on the base date x 100, on split-adjusted close (price return). Base date = the latest first-available date across the selected stocks, so all lines start at 100 on the same day; the footer always states it and says when a short-history stock shortens the chart. Moving average on a single stock is scaled by the same base price.
- Deviation from 8.1 / 10 ("the takeaway describes the lead series only"): the normalised panel's takeaway is `takeaways.summarise_returns`, one computed sentence ranking the change since the base date for every selected stock ("Over the 5Y window: NVDA +946.2%, AAPL +134.4%, ..."). Reason: a rebased chart exists to compare stocks, and a lead-only sentence would hide that. Still descriptive, still computed.
- `theme.line_fig` gained an optional `baseline` (dotted reference line, drawn at 100 here and 0 in `styleguide.py`); it does not stretch the axis. Index axis has no tick suffix; the unit ("Index, base date = 100") is in the subtitle.
- Sector returns (Equities, 2026-09-19): two panels in a second two-column row, `panels.render_sector_line_panel` and `render_sector_table_panel`, backed by a new pure module `sectors.py` (beyond the 9.2 layout; no Streamlit, so it is unit-testable). Market-cap weighted, price return only. Weight = today's shares outstanding x split-adjusted close on the timeframe's start date, so a sector's return is the change in its total market cap (buy-and-hold, no look-ahead). Start date = last close on or before latest close minus 1D / 1W / 1M / 3M / 6M / 12M (calendar). Checked against a brute-force per-stock loop (0 mismatches over 72 cells).
- Share counts: `data.load_shares`, saved to `cache/sp500_shares.csv`, refreshed weekly; "Refresh data" does not force them. Uses `sharesOutstanding` per class, NOT yfinance `marketCap`: for dual-class companies (Alphabet, Fox, News Corp) each class reports the whole company's cap, so summing would double-count. The download/retry/lock logic is now one helper, `data._ensure_saved`, shared with prices.
- Caveat (footer): weights use total shares, not the S&P's float-adjusted shares, and today's counts. Against the SPDR sector ETFs (price return) the sectors agree within about 1 pp except Communication Services and Information Technology, whose ETFs cap their largest holdings; ETFs are not the official S&P sector indices, which are not free.
- Both sector panels use panel-local timeframe pills, not the sidebar lookback (5.5: timeframe control adjacent to its chart). The line chart offers 1M, 3M, 6M, 12M only (1D and 1W have too few daily points); the table has all six plus "All". Caps the line chart at 6 sectors (3.2) and opens on the 3 best and 3 worst for the timeframe; that default follows the timeframe until the user edits the picker, then their picks stay until "Reset to leaders and laggards". A dashed S&P 500 line is drawn in addition (legend of 7 entries; the 6-series rule is applied to sector lines).
- Sector line chart is 400px, not the 300px standard (3.4): the 7-entry legend takes a third of 300px and leaves the plot too short. Sector names are shortened in the legend and table rows (`panels.SECTOR_SHORT`, e.g. "Info. Tech."); pickers and takeaways keep the full GICS names.
- Table shading is `theme.signed_heatmap`: zero-centred per column, up/down tokens (so it follows the up/down mode) blended with `raised`, tint strength capped so text keeps >= 4.5:1 (worst case checked 4.52:1 across both themes and all three modes). This is not the fixed 10-step red-green diverging ramp in 3.2, which ignores the up/down mode. Deviation from 3.1.2 / 5.3: the table shows sign and shading but no arrows, and no "%" in the cells (unit is in the subtitle), because the half-width panel could not fit six timeframes otherwise (`arrows=True` remains the default). Table rows: largest sector first, S&P 500 total last; the table is its own raw data, so no Raw data expander.
- Takeaways: `takeaways.summarise_sector_returns` (leader, laggard and the S&P 500 over the timeframe) and `summarise_sector_table` (sectors up per timeframe, leader and laggard over the longest). Both describe only.
- Currency unit is the suffix " USD" (axis, hover, takeaway) because `theme` formats units as suffixes only; a "$" prefix would need a `theme.py` change.
- Prices refresh when the saved files are over 1 day old; "Refresh data" now also forces a re-download (`data.refresh`). After a failed download it retries no sooner than 15 minutes later and shows the saved prices with a notice.

**Country returns (Equities, 2026-09-26)**
- Two panels in a third two-column row under the sector panels: `panels.render_country_line_panel` and `render_country_table_panel`, backed by a new pure module `countries.py` (beyond the 9.2 layout; no Streamlit, so it is unit-testable). One Yahoo index per country: US ^GSPC, Europe ^STOXX (STOXX Europe 600), UK ^FTSE, Japan ^N225, Hong Kong ^HSI, Singapore ^STI, Malaysia ^KLSE (KLCI). Users see country names, not tickers; the mapping is stated in every footer (`countries.disclaimer`).
- Local-currency price return only: no dividends, no FX conversion. Stated in the footer and the takeaway. Europe (STOXX 600) contains UK stocks, so it overlaps with UK; also stated.
- Data: `data.load_index_prices`, saved to `cache/country_index_close.parquet` (10Y daily, same save-and-fall-back pattern as prices via `_ensure_saved`; "Refresh data" forces it). All 7 indices are required for a download to be saved, so one broken ticker keeps yesterday's file and shows the fallback notice rather than silently dropping a country. Today's unfinished bar is blanked per index using that market's own close time (`countries.drop_open_bars`), not the US clock used for stocks.
- Different holiday calendars: indices are put on one shared calendar with the last close carried forward. A timeframe starts on the last shared date on or before latest date minus 1D/1W/1M/3M/6M/12M (same rule as `sectors.base_date`). Checked against a brute-force recompute (0 mismatches, 1D/3M/12M).
- Line chart follows 3.2 (max 6 lines, so the 7 countries are picked from, default US, Europe, Singapore) and 6.2 (rebased to 100, dotted baseline at 100). Panel-local 1M/3M/6M/12M pills, default 3M, like the sector chart. 300px standard height (sector line chart is 400px only because of its 7-entry legend). Raw data expander as on other charts.
- Table: same pills and shading as the sector table (`theme.signed_heatmap`, no arrows, no "%" in cells; same deviation as the sector table, see above). Rows in fixed country order. Adds a CSV download of every country and timeframe (with index name and ticker) because the table has no Raw data expander. `_table_timeframes_changed` now takes its session keys as arguments so both tables share it.
- Takeaways: `takeaways.summarise_country_returns` (leader, laggard over the timeframe); `summarise_sector_table` gained a `noun` argument ("countries"). Both describe only.
- Methodology page still says equity data "will come" and lists no equities sources; not updated yet (stale since the stock and sector panels).

**Forward P/E (Equities, 2026-09-26)**
- Panel under the country row (now the left half of a two-column row, see the comparison entry below): `panels._ntm_pe_history_panel`, backed by a new pure module `valuation.py` (beyond the 9.2 layout; no Streamlit, so it is unit-testable). Single-stock `st.selectbox` over the S&P 500 (default AAPL); band chart via `theme.band_fig` (mean and ±1σ over the sidebar lookback window, computed on the shown window like other band charts). 
- Method: Yahoo keeps no history of past analyst estimates, so this is a perfect-foresight approximation, stated in the footer and on the Methodology page. NTM EPS on date d = growth of cumulative quarterly EPS over the next 365 days (each quarter counts in proportion to the share of it inside the window), so the denominator rolls forward daily. Quarters already reported use `get_earnings_dates` reported EPS; unreported quarters use Yahoo's `earnings_estimate` (0q, +1q); later ones repeat the same quarter a year earlier times FY2/FY1 growth (clipped 0.5 to 2). Checked against a brute-force overlap-weighting recompute (0 mismatches).
- Fiscal period ends are not in `get_earnings_dates`: period end = report date minus a per-stock lag, the median over the recent quarters listed by `quarterly_income_stmt` (fallback 35 days). A missing quarter in the history truncates it to the unbroken run ending today.
- Negative EPS: if NTM EPS on the latest price date is <= 0, no chart, only a message (`st.info`) saying the method is not appropriate. Earlier dates with EPS <= 0 are dropped from the line, mean and band, with a count in the footer.
- Data: `data.load_eps`, fetched per stock on demand, saved to `cache/eps/<ticker>.json`, reused for a week (see the comparison entry: the bulk download now shares these files), falls back to the saved file with a notice. Needs `lxml` (added to `requirements.txt`).
- Takeaway reuses `takeaways.summarise` (level, percentile, 1M change, "More than 2σ" flag); unit is the suffix "x", 1 decimal. `theme.band_fig` gained a `k` that accepts several multiples; this panel draws ±1σ and ±2σ (the inner band reads darker, same 20% `muted` tint). Deviation from 3.2 ("mean/±1σ"), requested by the owner. Other band charts still draw ±1σ only.
- Reported EPS from Yahoo is usually adjusted (non-GAAP), so it can differ from GAAP EPS. Stated in the footer.

**Forward P/E comparison (Equities, 2026-09-26)**
- New panel right of the forward P/E history panel: `panels._ntm_pe_compare_panel`. The pair is one fragment, `panels.render_ntm_pe_row` (replaces the single-panel fragment), so choosing a stock on the left updates the right one within the same rerun. Both panels are half width, so the history chart is narrower than before.
- Compares one stock's NTM P/E line with two tick boxes (both ticked on open, owner's choice): "Sector average (GICS)" and "S&P 500 average". This chart has no historical mean or band; the history panel keeps those. The S&P 500 line is dashed so it is not told apart by colour alone (3.1.2 / principle 5). Uses `theme.line_fig`; no new chart helper.
- Stock linking: the comparison's stock box follows the history panel's stock until the user picks another one in it; a "Follow the chart on the left" button appears then and goes back to following (same pattern as the sector picker, 14 sector returns). Following works by writing the widget's session-state key before it is created.
- Average = market-cap weighted aggregate: total market cap over total NTM earnings of the group's stocks (`valuation.group_ntm_pe`), the way an index P/E is quoted, not a mean or median of stock P/Es. Owner's choice. A stock counts on a date only when it has a price and positive NTM EPS, so negative earners leave both sides. Weights use today's share counts (`data.load_shares`) for the whole window, so past market caps are approximate (same caveat as the sector returns). The group changes composition over time; the footer states how many stocks the latest value counts, and which stocks Yahoo has no EPS for. Universe is today's constituents (survivorship), as elsewhere.
- Negative EPS on the underlying stock: same message as the history panel, no chart. Negative EPS stocks inside the averages are excluded, per above.
- Data: `data.load_ntm_eps` bulk-downloads EPS for every constituent once (about 4 minutes; measured 0.4 s per stock with 6 workers), saved as the same per-stock files, marked by `cache/eps/_bulk.json` (which stocks failed); refreshed weekly. Deviation from the single-stock design above: EPS files now last a week (was a day) and "Refresh data" no longer re-fetches EPS, like share counts, so the history and comparison lines for a stock agree. The bulk download starts the first time the comparison panel opens with an average ticked, and shows a spinner; the page above it renders first. It needs at least 400 stocks to succeed or nothing is marked as done, and averages are hidden until one bulk download has succeeded (a partial set of stocks would give a misleading average).
- Takeaway: `takeaways.summarise_pe_comparison`: the stock's level and its % premium or discount to each average shown ("30% above Information Technology (27.9x) and 55% above S&P 500 (23.3x)"). Describes only.

**Forward P/E screen (Equities, 2026-09-27)**
- Full-width panel under the forward P/E row: `panels.render_pe_screen_panel`, one row per S&P 500 stock: company, ticker, market cap (USD bn), NTM P/E, σ vs average, sector, sector P/E. A "Sector" name column was added before sector P/E so the number is readable on its own. Built by the pure `valuation.pe_screen`.
- Yahoo analyst target price, upside % and the close-price column were specified but dropped by the owner (2026-09-27): targets need ~500 Yahoo `info` calls a day, which hit Yahoo's rate limit in testing and would share that limit with the share-count and EPS downloads. Revisit with a lighter source or a slow throttled download.
- Sorting is Streamlit's own column-header sort (both directions, every column; numbers sort on values, not formatted text). Opens largest market cap first. Controls (owner's choice): a name/ticker search box and a GICS sector multiselect where empty means all sectors (placeholder "All sectors"), a deviation from 5.5's "default = all selected" because eleven chips would crowd the panel. CSV download of the rows shown.
- σ uses the sidebar lookback (owner's choice) and the same windowed mean and sample σ as the history chart's bands, so a stock's σ equals its chart (checked: AAPL 2.504 both ways; sector P/E equals the comparison chart's latest value). Needs 8 positive-EPS days. Negative or missing NTM EPS: row kept, P/E and σ show "—" (owner's choice).
- Shading (owner's choice): σ column only, via new `theme.shade_signed` (the `signed_heatmap` shader, now shared, with a `good_when` flip and a quantile cap). P/E below its own average = up colour (`good_when="down"`), stated in the footer. Saturates at the column's 95th percentile so a few outliers among ~500 rows do not wash out the rest. Sign always printed (no arrows, to keep columns narrow).
- Takeaway: `takeaways.summarise_pe_screen` on the rows shown: how many are below their own average and how many are beyond 2σ. Describes only.

**Rolling correlation (Equities, 2026-09-29)**
- Left half of a two-column row between the forward P/E row and the forward P/E screen (was full width until the correlation matrix was added beside it): `panels.render_correlation_panel`, backed by a new pure module `correlation.py` (beyond the 9.2 layout; no Streamlit, so it is unit-testable). Two single-stock search boxes over the S&P 500 (the P/E panels' `_pe_select`, which gained a `label`), default AAPL and MSFT. Choosing the same stock twice shows an info message instead of a chart.
- Returns: daily % change of the split- and dividend-adjusted close (`adj_close`, total return), on dates both stocks have a close. Pearson correlation over a trailing window of 21/63/126/252 trading days, chosen with panel-local pills 1M/3M/6M/12M (default 3M, owner's choice); history shown follows the sidebar lookback, like the forward P/E chart. Computed on the full saved history, then cut to the window, so the line starts at the left edge. Checked against a brute-force `np.corrcoef` recompute (0 mismatches over 640 points, 8 pairs, all four windows).
- Chart: `theme.band_fig` with ±1σ and ±2σ (owner's choice, same deviation from 3.2 as the forward P/E history chart), mean and σ over the shown window. 400px, not the 300px half-width standard (3.4), so it matches the correlation matrix beside it. When the ±2σ band crosses ±1 the footer says it is a statistical band, not a bound.
- Takeaway reuses `takeaways.summarise` (level, percentile, 1M change, 2σ flag); no unit, 2 decimals. Describes only.

**Correlation matrix (Equities, 2026-09-29)**
- Right half of the rolling correlation row: `panels.render_corr_matrix_panel`, computed by `correlation.matrix`. Two multiselects, "Stocks" (S&P 500, default AAPL, MSFT, NVDA, JPM, XOM) and "Country indices" (the 7 country indices, default US, Europe), with timeframe pills 1M/3M/6M/12M/3Y/5Y (default 12M) on a row below them. The owner first asked for the pills to the right of the boxes; a 1280px screenshot showed the half-width boxes truncating chips ("A…") and the pills wrapping, so the owner chose pills below, in both correlation panels. The timeframe is independent of the sidebar lookback (5.5).
- Limit: 8 items combined (`panels.MATRIX_MAX`). Each box's `max_selections` is 8 minus the other box's count, read from session state before either widget is drawn; the country box is disabled when 8 stocks are chosen. Reason: at a 1280px laptop viewport the half-width panel leaves about 40px per cell at 8 items, enough for "−0.12" at 11px.
- Returns: stocks `adj_close` (total return, USD); indices close (price return, local currency). Each pair uses only the dates both have a close and the returns dated after (latest date − timeframe); fewer than 15 paired returns leaves a blank cell, listed in the footer. Footer warns that daily correlations across trading sessions (US / Europe / Asia) are understated because of different close times, whenever the selection spans more than one session.
- Chart: new `theme.corr_heatmap_fig` (Plotly heatmap, value printed in each cell, fixed −1 to +1 scale, neutral `raised` at 0, 400px). Colour is the owner's choice of green near −1 and red near +1, implemented with the up/down tokens (`good_when="down"`: low correlation = up colour), so it is green/red in the default mode and follows the red-up and colour-blind modes like every other shaded element. Tints are capped with the same contrast rule as `shade_signed` (text >= 4.5:1), shared through a new `theme._max_tint`. This is the up/down blend, not the fixed red-green diverging ramp in 3.2, for the same reason as the sector table. No colour bar (values are printed; the scale is stated in the footer). The diagonal prints 1.00 but is left untinted: a full-strength cell on every item's self-correlation drew the eye away from the real pairs (checked in a 1280px screenshot).
- Takeaway: `takeaways.summarise_corr_matrix`: most and least correlated pairs and the average across pairs. Describes only.
- Raw data expander: the matrix and the paired-return count per cell.
