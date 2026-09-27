"""Rule-based panel takeaways, computed from the data on every load.

Rules (DESIGN.md section 8): text is generated, never hand-written; it DESCRIBES the data
(level, percentile, change, staleness) and never forecasts or recommends.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from theme import MINUS, fmt_num

# Heuristic ceilings on the age of the latest observation before a series is flagged STALE.
# They include normal publication lag (e.g. CPI is ~45-75 days old at any time) plus a grace period.
MAX_AGE_DAYS = {"D": 7, "W": 14, "M": 80, "Q": 210}


@dataclass(frozen=True)
class TakeawaySpec:
    label: str                      # "10Y-2Y spread"
    unit: str = ""                  # "%", " pp", " bp", "k"
    decimals: int = 2
    freq: str = "D"                 # D | W | M | Q  (drives the STALE flag)
    change_days: int = 30
    change_label: str = "1M"
    change_unit: str | None = None  # unit of the change when it differs from the level's: " pp" for a % yield
    note: Callable[[float], str | None] | None = None   # e.g. lambda v: "Curve inverted." if v < 0 else None


@dataclass(frozen=True)
class Summary:
    latest: float
    asof: pd.Timestamp
    change: float | None
    move_z: float | None            # size of the recent change vs. typical changes of the same length
    percentile: float               # 0-100, within the loaded window
    z: float                        # distance from the window mean, in standard deviations
    stale: bool
    text: str


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _window_label(s: pd.Series) -> str:
    years = (s.index[-1] - s.index[0]).days / 365.25
    return f"{round(years)}Y" if years >= 1 else f"{max(1, round(years * 12))}M"


def summarise(s: pd.Series, spec: TakeawaySpec, today: pd.Timestamp | None = None) -> Summary | None:
    """Return None when there is not enough data to say anything honest."""
    s = s.dropna().sort_index()
    if len(s) < 8:
        return None
    today = today or pd.Timestamp.today().normalize()
    asof, latest = s.index[-1], float(s.iloc[-1])

    prior = s.asof(asof - pd.Timedelta(days=spec.change_days))
    change = None if pd.isna(prior) else latest - float(prior)

    past = s.reindex(s.index - pd.Timedelta(days=spec.change_days), method="ffill")
    changes = pd.Series(s.to_numpy() - past.to_numpy()).dropna()
    move_z = None
    if change is not None and len(changes) > 8 and changes.std() > 0:
        move_z = change / float(changes.std())

    percentile = float((s <= latest).mean() * 100)
    sd = float(s.std())
    z = (latest - float(s.mean())) / sd if sd > 0 else 0.0
    window = _window_label(s)
    stale = (today - asof).days > MAX_AGE_DAYS[spec.freq]

    if sd == 0:  # constant series: percentile / extreme wording would be meaningless
        text = f"{spec.label} is {fmt_num(latest, spec.decimals, spec.unit)}, unchanged across the {window} window."
        return Summary(latest, asof, change, move_z, percentile, z, stale, text)

    text = (f"{spec.label} is {fmt_num(latest, spec.decimals, spec.unit)}, "
            f"{_ordinal(round(percentile))} percentile of the {window} range")
    if change is not None:
        # No sign when the change rounds to zero at the displayed precision ("0.0", not "+0.0").
        sign = "" if round(abs(change), spec.decimals) == 0 else "+" if change > 0 else MINUS
        change_unit = spec.unit if spec.change_unit is None else spec.change_unit
        text += f"; {sign}{fmt_num(abs(change), spec.decimals, change_unit)} over {spec.change_label}"
    text += "."
    if percentile >= 98:
        text += f" Near the {window} high."
    elif percentile <= 2:
        text += f" Near the {window} low."
    elif abs(z) >= 2:
        text += " More than 2σ from its mean."
    if spec.note and (extra := spec.note(latest)):
        text += f" {extra}"
    return Summary(latest, asof, change, move_z, percentile, z, stale, text)


@dataclass(frozen=True)
class ReturnsSummary:
    returns: dict[str, float]       # % change from the base date to the latest observation, per series
    asof: pd.Timestamp              # newest observation across the series
    stale: bool
    text: str


def summarise_returns(rebased: Mapping[str, pd.Series], today: pd.Timestamp | None = None) -> ReturnsSummary | None:
    """Ranked change since the base date for series rebased to 100 on that date (DESIGN.md 14: unlike
    `summarise`, this describes every series, not only the lead). None when there is too little data."""
    series = {name: s.dropna().sort_index() for name, s in rebased.items()}
    if not series or any(len(s) < 8 for s in series.values()):
        return None
    today = today or pd.Timestamp.today().normalize()
    returns = {name: float(s.iloc[-1]) - 100.0 for name, s in series.items()}
    asof = max(s.index[-1] for s in series.values())
    ranked = sorted(returns.items(), key=lambda kv: kv[1], reverse=True)
    window = _window_label(next(iter(series.values())))
    text = (f"Over the {window} window: {', '.join(f'{name} {_signed_pct(v)}' for name, v in ranked)} "
            "(price only, excluding dividends).")
    return ReturnsSummary(returns, asof, (today - asof).days > MAX_AGE_DAYS["D"], text)


def _signed_pct(x: float) -> str:
    """+12.3% / −4.1%; no sign when the change rounds to zero at the displayed precision."""
    sign = "" if round(abs(x), 1) == 0 else "+" if x > 0 else MINUS
    return f"{sign}{fmt_num(abs(x), 1, '%')}"


def summarise_sector_returns(returns: Mapping[str, float], timeframe: str, benchmark: float | None,
                             asof: pd.Timestamp, today: pd.Timestamp | None = None) -> ReturnsSummary | None:
    """One sentence on the selected sectors' return over a timeframe: who led, who lagged, and the S&P 500
    for context. Describes only. None when no sector is selected."""
    returns = {name: r for name, r in returns.items() if pd.notna(r)}
    if not returns:
        return None
    today = today or pd.Timestamp.today().normalize()
    ranked = sorted(returns.items(), key=lambda kv: kv[1], reverse=True)
    (top, top_r), (bottom, bottom_r) = ranked[0], ranked[-1]
    if len(ranked) == 1:
        text = f"Over {timeframe}, {top} returned {_signed_pct(top_r)}"
    else:
        text = f"Over {timeframe}, {top} led at {_signed_pct(top_r)} and {bottom} lagged at {_signed_pct(bottom_r)}"
    if benchmark is not None and pd.notna(benchmark):
        text += f"; S&P 500 {_signed_pct(benchmark)}"
    text += " (market-cap weighted, price only)."
    return ReturnsSummary(returns, asof, (today - asof).days > MAX_AGE_DAYS["D"], text)


def summarise_country_returns(returns: Mapping[str, float], timeframe: str, asof: pd.Timestamp,
                              today: pd.Timestamp | None = None) -> ReturnsSummary | None:
    """One sentence on the selected countries' index return over a timeframe: who led and who lagged.
    Describes only. None when no country has a return."""
    returns = {name: r for name, r in returns.items() if pd.notna(r)}
    if not returns:
        return None
    today = today or pd.Timestamp.today().normalize()
    ranked = sorted(returns.items(), key=lambda kv: kv[1], reverse=True)
    (top, top_r), (bottom, bottom_r) = ranked[0], ranked[-1]
    if len(ranked) == 1:
        text = f"Over {timeframe}, {top} returned {_signed_pct(top_r)}"
    else:
        text = f"Over {timeframe}, {top} led at {_signed_pct(top_r)} and {bottom} lagged at {_signed_pct(bottom_r)}"
    text += " (local-currency price return)."
    return ReturnsSummary(returns, asof, (today - asof).days > MAX_AGE_DAYS["D"], text)


def summarise_sector_table(table: pd.DataFrame, timeframes: Sequence[str], asof: pd.Timestamp,
                           today: pd.Timestamp | None = None, noun: str = "sectors") -> ReturnsSummary | None:
    """One sentence on a rows x timeframes return table (sectors by default; `noun` names the rows): how many
    are up per timeframe, and the leader and laggard over the longest timeframe shown. Describes only.
    None when there is no data."""
    if table.empty or not timeframes:
        return None
    today = today or pd.Timestamp.today().normalize()
    longest = table[timeframes[-1]].dropna()  # callers pass timeframes shortest -> longest
    if longest.empty:
        return None
    ranked = longest.sort_values(ascending=False)
    n = int(table.notna().sum().max())
    up = {tf: int((table[tf] > 0).sum()) for tf in timeframes}
    leader = f"{ranked.index[0]} leads ({_signed_pct(ranked.iloc[0])}) and {ranked.index[-1]} lags ({_signed_pct(ranked.iloc[-1])})"
    if len(timeframes) == 1:
        text = f"Over {timeframes[0]}, {up[timeframes[0]]} of {n} {noun} are up; {leader}."
    else:
        text = (f"{noun.capitalize()} up (of {n}): {', '.join(f'{tf} {k}' for tf, k in up.items())}; "
                f"over {timeframes[-1]} {leader}.")
    return ReturnsSummary(ranked.to_dict(), asof, (today - asof).days > MAX_AGE_DAYS["D"], text)


def whats_changed(summaries: Mapping[str, Summary], *, move_z: float = 2.0, extreme_pct: float = 98) -> list[str]:
    """Lines for the Overview 'What changed' strip: unusually large recent moves,
    new-range extremes, and stale data. Empty list means nothing notable (say so in the UI)."""
    lines = []
    for name, sm in summaries.items():
        if sm.stale:
            lines.append(f"{name}: data may be stale (latest {sm.asof:%d %b %Y}).")
        if sm.move_z is not None and abs(sm.move_z) >= move_z:
            sign = "+" if sm.move_z > 0 else ""  # fmt_num supplies the true minus for negatives
            lines.append(f"{name}: unusually large {'rise' if sm.move_z > 0 else 'fall'} "
                         f"({sign}{fmt_num(sm.move_z, 1)}σ vs typical moves of the same length).")
        if sm.percentile >= extreme_pct:
            lines.append(f"{name}: at the top of its loaded range ({_ordinal(round(sm.percentile))} percentile).")
        elif sm.percentile <= 100 - extreme_pct:
            lines.append(f"{name}: at the bottom of its loaded range ({_ordinal(round(sm.percentile))} percentile).")
    return lines


def summarise_pe_screen(pe_z: pd.Series, window: str, asof: pd.Timestamp,
                        today: pd.Timestamp | None = None) -> ReturnsSummary | None:
    """One sentence on the stocks shown in the forward P/E table: how many sit below their own average forward P/E
    over the window, and how many are more than 2σ from it. Describes only. `returns` holds the median σ.
    None when no row is shown."""
    if pe_z.empty:
        return None
    today = today or pd.Timestamp.today().normalize()
    z = pe_z.dropna()
    if z.empty:
        text = f"None of the {len(pe_z)} stocks shown has a forward P/E."
    else:
        text = (f"{int((z < 0).sum())} of {len(z)} stocks with a forward P/E are below their {window} average "
                f"({int((z.abs() > 2).sum())} more than 2σ from it).")
    return ReturnsSummary({"median σ": float(z.median()) if len(z) else float("nan")}, asof,
                          (today - asof).days > MAX_AGE_DAYS["D"], text)


def summarise_pe_comparison(label: str, level: float, others: Mapping[str, float], asof: pd.Timestamp,
                            today: pd.Timestamp | None = None) -> ReturnsSummary:
    """One sentence placing a stock's NTM P/E against the group averages shown (sector, S&P 500), as a % premium or
    discount. `others` maps a name to its latest value; with none, it only states the level (DESIGN.md 8: describes,
    never advises). `returns` holds the premium (%) per name."""
    today = today or pd.Timestamp.today().normalize()
    text = f"{label} NTM P/E is {fmt_num(level, 1, 'x')}"
    premiums = {}
    parts = []
    for name, value in others.items():
        premium = (level / value - 1) * 100
        premiums[name] = premium
        rel = "in line with" if round(abs(premium)) == 0 else \
            f"{fmt_num(abs(premium), 0, '%')} {'above' if premium > 0 else 'below'}"
        parts.append(f"{rel} {name} ({fmt_num(value, 1, 'x')})")
    if parts:
        text += ", " + " and ".join(parts)
    return ReturnsSummary(premiums, asof, (today - asof).days > MAX_AGE_DAYS["D"], text + ".")
