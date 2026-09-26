"""Panel catalogue: every FRED-backed panel as data (DESIGN.md section 10). Adding a panel = adding a spec.

Frequencies and units were checked against FRED series metadata (frequency_short, units_short).
Panels are grouped by page; the Overview page reuses some of them (see views/overview.py).
"""
from __future__ import annotations

import data
from panels import PanelSpec
from takeaways import TakeawaySpec

PP = " pp"  # unit of a difference between two percentages


def _spread_note(v: float) -> str:
    return "Curve inverted." if v < 0 else "Curve positively sloped."


# ---------- Series ----------

TREASURY_SERIES = {
    "Fed funds": "FEDFUNDS",
    "Treasury 2Y": "DGS2",
    "Treasury 10Y": "DGS10",
    "Treasury 30Y": "DGS30",
}
BREAKEVEN_SERIES = {"Breakeven 5Y": "T5YIE", "Breakeven 10Y": "T10YIE", "Breakeven 30Y": "T30YIEM"}
SPREAD_SERIES = {"10Y–Fed funds spread": "T10YFF", "5Y–Fed funds spread": "T5YFF", "10Y–2Y spread": "T10Y2Y"}
OAS_SERIES = {
    "US AAA": "BAMLC0A1CAAA", "US AA": "BAMLC0A2CAA", "US A": "BAMLC0A3CA",
    "US BBB": "BAMLC0A4CBBB", "US BB": "BAMLH0A1HYBB", "US B": "BAMLH0A2HYB",
}
YTW_SERIES = {
    "US AAA": "BAMLC0A1CAAASYTW", "US AA": "BAMLC0A2CAASYTW", "US A": "BAMLC0A3CASYTW",
    "US BBB": "BAMLC0A4CBBBSYTW", "US BB": "BAMLH0A1HYBBSYTW", "US B": "BAMLH0A2HYBSYTW",
}
CORP_CURVE_SERIES = {
    "1–3 years": "BAMLC1A0C13YSYTW", "3–5 years": "BAMLC2A0C35YSYTW", "5–7 years": "BAMLC3A0C57YSYTW",
    "7–10 years": "BAMLC4A0C710YSYTW", "10–15 years": "BAMLC7A0C1015YSYTW", "15+ years": "BAMLC8A0C15PYSYTW",
}
CORP_CURVE_TENORS = tuple((label, data.maturity_from_range_label(label)) for label in CORP_CURVE_SERIES)

# Extra series used outside panels (Overview KPI strip, recession shading).
HY_OAS = "BAMLH0A0HYM2"
RECESSIONS = "USREC"

BOFA_CAVEAT = "Data supplier (ICE BofA via FRED) limits history to about 3Y, whatever the lookback."

# ---------- Macro ----------

INFLATION = PanelSpec(
    key="inflation", title="Inflation", subtitle="CPI all items, % YoY · monthly · not seasonally adjusted",
    series={"Inflation": "CPIAUCNS"}, fred_units="pc1", decimals=1, freq="M", recessions=True,
    takeaway=TakeawaySpec("CPI inflation", unit="%", decimals=1, change_unit=PP),
)
GDP = PanelSpec(
    key="gdp", title="Real GDP growth", subtitle="Real GDP, % YoY · quarterly",
    series={"GDP": "OB000334Q"}, decimals=1, freq="Q", recessions=True,
    takeaway=TakeawaySpec("Real GDP growth", unit="%", decimals=1, change_unit=PP),
)
UNEMPLOYMENT = PanelSpec(
    key="unemployment", title="Unemployment rate", subtitle="% of labour force · monthly · not seasonally adjusted",
    series={"Unemployment": "UNRATENSA"}, decimals=1, freq="M", recessions=True,
    takeaway=TakeawaySpec("Unemployment rate", unit="%", decimals=1, change_unit=PP),
)
PAYROLLS = PanelSpec(
    key="payrolls", title="Nonfarm payrolls", subtitle="Change in jobs, thousands · monthly (MoM)",
    series={"Nonfarm payrolls": "PAYEMS"}, fred_units="chg", unit="k", decimals=0, freq="M", recessions=True,
    takeaway=TakeawaySpec("Monthly payroll change", unit="k", decimals=0),
)
CLAIMS = PanelSpec(
    key="claims", title="Initial jobless claims", subtitle="Change in claims, persons · weekly (WoW) · not seasonally adjusted",
    series={"Initial claims": "ICNSA"}, fred_units="chg", unit="", decimals=0, freq="W", recessions=True,
    takeaway=TakeawaySpec("Weekly change in initial claims", unit="", decimals=0),
)
MACRO = [INFLATION, GDP, UNEMPLOYMENT, PAYROLLS, CLAIMS]

# ---------- Rates and credit ----------

TREASURY = PanelSpec(
    key="treasury", title="Treasury rates", subtitle="% · daily (Fed funds monthly)",
    series=TREASURY_SERIES, freq="D", freq_label="Daily (Fed funds monthly)", series_freq={"Fed funds": "M"},
    selectable=True, lead="Treasury 10Y",
    takeaway=TakeawaySpec("{series}", unit="%", change_unit=PP),
)
YIELD_CURVE = PanelSpec(
    key="yield_curve", title="Treasury yield curve", subtitle="% by maturity · latest vs earlier dates · daily (Fed funds monthly)",
    series=TREASURY_SERIES, kind="curve", freq="D", freq_label="Daily (Fed funds monthly)",
    series_freq={"Fed funds": "M"}, tenors=tuple(data.YIELD_CURVE_TENORS), lead="Treasury 10Y",
    caveat="Fed funds is plotted at 0Y (overnight) using its latest monthly observation.",
    takeaway=TakeawaySpec("{series}", unit="%", change_unit=PP),
)
BREAKEVENS = PanelSpec(
    key="breakevens", title="Breakeven inflation", subtitle="% · daily (30Y monthly)",
    series=BREAKEVEN_SERIES, freq="D", freq_label="Daily (30Y monthly)", series_freq={"Breakeven 30Y": "M"},
    selectable=True, takeaway=TakeawaySpec("{series}", unit="%", change_unit=PP),
)
STEEPNESS = PanelSpec(
    key="steepness", title="Yield curve steepness", subtitle="Treasury yield differences · pp · daily",
    series=SPREAD_SERIES, unit=PP, freq="D", selectable=True, lead="10Y–2Y spread",
    takeaway=TakeawaySpec("{series}", unit=PP, note=_spread_note),
)
OAS_QUALITY = PanelSpec(
    key="oas_quality", title="Corporate OAS by credit quality", subtitle="Option-adjusted spread, % · daily · AAA to B",
    series=OAS_SERIES, freq="D", ordered=True, selectable=True, select_label="Credit quality",
    caveat=BOFA_CAVEAT, takeaway=TakeawaySpec("{series} OAS", unit="%", change_unit=PP),
)
OAS_HISTORY = PanelSpec(
    key="oas_history", title="Corporate OAS vs history", subtitle="Option-adjusted spread, % · daily · mean ±1σ band",
    series=OAS_SERIES, kind="band", freq="D", selectable=True, select_label="Credit quality",
    caveat=BOFA_CAVEAT + " The band and percentile reflect that window.",
    takeaway=TakeawaySpec("{series} OAS", unit="%", change_unit=PP),
)
YTW = PanelSpec(
    key="ytw", title="Corporate yield to worst", subtitle="Semi-annual yield to worst, % · daily · AAA to B",
    series=YTW_SERIES, freq="D", ordered=True, selectable=True, select_label="Credit quality",
    caveat=BOFA_CAVEAT, takeaway=TakeawaySpec("{series} yield to worst", unit="%", change_unit=PP),
)
CORP_CURVE = PanelSpec(
    key="corp_curve", title="US corporate index yield curve",
    subtitle="Semi-annual yield to worst, % by maturity bucket · latest vs earlier dates · daily",
    series=CORP_CURVE_SERIES, kind="curve", freq="D", tenors=CORP_CURVE_TENORS, lead="15+ years",
    caveat="Each maturity bucket is plotted at its upper bound (1–3 years at 3Y). " + BOFA_CAVEAT,
    takeaway=TakeawaySpec("Corporate {series} yield to worst", unit="%", change_unit=PP),
)
RATES_CREDIT = [TREASURY, YIELD_CURVE, BREAKEVENS, STEEPNESS, OAS_QUALITY, OAS_HISTORY, YTW, CORP_CURVE]

ALL_PANELS = MACRO + RATES_CREDIT
