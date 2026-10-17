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


def summarise_corr_matrix(corr: pd.DataFrame, timeframe: str, asof: pd.Timestamp,
                          today: pd.Timestamp | None = None) -> ReturnsSummary | None:
    """One sentence on a correlation matrix: the most and least correlated pairs and the average across all pairs.
    Describes only. `returns` holds each pair's correlation ("A–B"). None with no computable pair."""
    names = list(corr.columns)
    pairs = {f"{a}–{b}": float(corr.loc[a, b]) for i, a in enumerate(names) for b in names[i + 1:]
             if pd.notna(corr.loc[a, b])}
    if not pairs:
        return None
    today = today or pd.Timestamp.today().normalize()
    ranked = sorted(pairs.items(), key=lambda kv: kv[1], reverse=True)
    (top, top_v), (bottom, bottom_v) = ranked[0], ranked[-1]
    if len(ranked) == 1:
        text = f"Over {timeframe}, {top} correlation is {fmt_num(top_v, 2)}."
    else:
        mean = sum(pairs.values()) / len(pairs)
        text = (f"Over {timeframe}, the most correlated pair is {top} ({fmt_num(top_v, 2)}) and the least is {bottom} "
                f"({fmt_num(bottom_v, 2)}); the average across {len(pairs)} pairs is {fmt_num(mean, 2)}.")
    return ReturnsSummary(pairs, asof, (today - asof).days > MAX_AGE_DAYS["D"], text)


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


def summarise_cape_fit(cape_now: float, expected: float, model_label: str, r2: float, start: pd.Timestamp,
                       end: pd.Timestamp, sample_capes: pd.Series, asof: pd.Timestamp,
                       today: pd.Timestamp | None = None) -> ReturnsSummary:
    """One sentence on the CAPE regression: the expected 10-year real return the fitted line gives at today's CAPE,
    and how many of the fit's starting months had a CAPE at or above today's. Owner-approved exception to "describes,
    never forecasts" (DESIGN.md 14, "CAPE"): the number is labelled as a regression-implied expectation, never advice.
    `returns` holds {"expected": %}."""
    today = today or pd.Timestamp.today().normalize()
    above = int((sample_capes >= cape_now).sum())
    model = model_label[:1].lower() + model_label[1:]  # "Earnings yield (1/CAPE)" -> "earnings yield (1/CAPE)"
    text = (f"At a CAPE of {fmt_num(cape_now, 1, 'x')}, the {model} fit over {start:%b %Y}–{end:%b %Y} "
            f"(R² {fmt_num(r2, 2)}) gives an expected real return of {fmt_num(expected, 1, '%')} a year over the next "
            f"10 years. {above:,} of {len(sample_capes):,} starting months had a CAPE this high or higher")
    text += ", so today is outside the fitted data." if above == 0 else \
        ", so today is at the edge of the fitted data." if above < 0.05 * len(sample_capes) else "."
    return ReturnsSummary({"expected": expected}, asof, (today - asof).days > MAX_AGE_DAYS["M"], text)


def summarise_options(ticker: str, scope: str, calls: float, puts: float, top_call: float | None,
                      top_put: float | None, spot: float, unit: str, asof: pd.Timestamp, *,
                      max_pain: float | None = None, gex_total: float | None = None, flip: float | None = None,
                      today: pd.Timestamp | None = None) -> ReturnsSummary:
    """Open interest across the strikes shown: call and put totals, put/call ratio, the strikes with the most OI, and,
    when given, max pain and dealer gamma (labelled as resting on the dealer convention). Describes positioning
    only; it never says where the price will go (DESIGN.md 8). `returns` holds {"put_call": ratio}."""
    today = today or pd.Timestamp.today().normalize()
    ratio = puts / calls if calls > 0 else float("nan")
    text = (f"{ticker} options, {scope}: {fmt_num(calls, 0)} call and {fmt_num(puts, 0)} put contracts open "
            f"(put/call {fmt_num(ratio, 2)})")
    if top_call is not None and top_put is not None:
        text += f"; most call OI at {fmt_num(top_call, 2, unit)}, most put OI at {fmt_num(top_put, 2, unit)}"
    text += "."
    if max_pain is not None:
        gap = (max_pain / spot - 1) * 100
        where = "at the last price" if round(abs(gap), 1) == 0 else \
            f"{fmt_num(abs(gap), 1, '%')} {'above' if gap > 0 else 'below'} the last price"
        text += f" Max pain is {fmt_num(max_pain, 2, unit)}, {where} of {fmt_num(spot, 2, unit)}."
    if gex_total is not None:
        size = abs(gex_total)
        amount = f"{fmt_num(size / 1e9, 2)}bn" if size >= 1e9 else f"{fmt_num(size / 1e6, 1)}m"
        sign = MINUS if gex_total < 0 else "+"
        text += f" Under the dealer convention, net dealer gamma is {sign}{amount}{unit} per 1% move"
        text += f" and changes sign at {fmt_num(flip, 2, unit)}." if flip is not None else \
            " and does not change sign within the range shown."
    return ReturnsSummary({"put_call": ratio}, asof, (today - asof).days > MAX_AGE_DAYS["D"], text)


def summarise_target_prob(ticker: str, expiry: pd.Timestamp, days: int, spot: float, unit: str, asof: pd.Timestamp,
                          q25: float, q75: float, *, target: tuple[str, float, float] | None = None,
                          analysts: int | None = None, today: pd.Timestamp | None = None) -> ReturnsSummary:
    """What the option prices of one expiry imply: the risk-neutral probability of ending at or above the headline
    analyst target (`target` = (name, price, probability)), and the middle half of the implied range. Describes
    market pricing only; the word "risk-neutral" is always in the sentence (DESIGN.md 8; 14, "Analyst target
    probability"). `returns` holds {"probability": p} (NaN without a target)."""
    today = today or pd.Timestamp.today().normalize()
    when = f"Options expiring {expiry:%d %b %Y} ({days:,} days)"
    span = f"{fmt_num(q25, 2)}–{fmt_num(q75, 2, unit)}"
    prob = float("nan")
    if target is not None:
        name, price, prob = target
        gap = (price / spot - 1) * 100
        where = "at the last price" if round(abs(gap), 1) == 0 else \
            f"{fmt_num(abs(gap), 1, '%')} {'above' if gap > 0 else 'below'} the last price"
        who = f"; {analysts:,} analysts" if analysts else ""
        text = (f"{when} price a {fmt_num(prob * 100, 0, '%')} risk-neutral probability that {ticker} ends at or "
                f"above its {name} analyst target of {fmt_num(price, 2, unit)} ({where}{who}). The middle half of "
                f"the risk-neutral range at expiry is {span}.")
    else:
        text = (f"{when} put the middle half of {ticker}'s risk-neutral range at expiry at {span}, against a last "
                f"price of {fmt_num(spot, 2, unit)}.")
    return ReturnsSummary({"probability": prob}, asof, (today - asof).days > MAX_AGE_DAYS["D"], text)


def summarise_smile(ticker: str, expiry: pd.Timestamp, forward: float, unit: str, atm: float, put90: float,
                    call110: float, fit_error: float, quotes: int, asof: pd.Timestamp, *,
                    today: pd.Timestamp | None = None) -> ReturnsSummary:
    """The smile in one sentence: implied volatility (all in %) at the forward and at 90% / 110% of it, the gap
    between them (skew, in pp) and how closely the fit matches the quotes. Describes only (DESIGN.md 8).
    `returns` holds {"atm": atm, "skew": put90 - call110}."""
    today = today or pd.Timestamp.today().normalize()
    skew = put90 - call110
    side = "puts priced at a higher volatility than calls" if skew > 0 else \
        "calls priced at a higher volatility than puts" if skew < 0 else "puts and calls priced alike"
    text = (f"{ticker} options expiring {expiry:%d %b %Y}: implied volatility is {fmt_num(atm, 1, '%')} at the "
            f"forward ({fmt_num(forward, 2, unit)}), {fmt_num(put90, 1, '%')} at 90% of it and "
            f"{fmt_num(call110, 1, '%')} at 110%, a {fmt_num(abs(skew), 1, ' pp')} gap with {side}. The fitted smile "
            f"misses the {quotes:,} quotes by {fmt_num(fit_error, 1, ' pp')} on average (root mean square).")
    return ReturnsSummary({"atm": atm, "skew": skew}, asof, (today - asof).days > MAX_AGE_DAYS["D"], text)


def _or_list(items: Sequence[str]) -> str:
    items = [i.lower() for i in items]
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


def summarise_download(table: pd.DataFrame, lacking: Mapping[str, Sequence[str]],
                       none_in_window: Mapping[str, Sequence[str]]) -> str:
    """One sentence on the file the Data download page will write: tickers, datasets, rows and date span, which
    tickers Yahoo has no data for in a selected dataset (`lacking`) and which have none inside the timeframe
    (`none_in_window`). `table` has (Ticker, Dataset, Field) columns. Describes only."""
    if table.empty:
        return "No data for the tickers and datasets selected."
    tickers = table.columns.get_level_values(0).unique()
    datasets = table.columns.get_level_values(1).unique()
    text = (f"{len(tickers)} ticker{'s' * (len(tickers) != 1)}, {len(datasets)} dataset{'s' * (len(datasets) != 1)}, "
            f"{len(table):,} rows from {table.index.min():%d %b %Y} to {table.index.max():%d %b %Y}")
    gaps = [f"{t} has no {_or_list(ds)} data" for t, ds in lacking.items() if ds]
    gaps += [f"{t} has no {_or_list(ds)} in this timeframe" for t, ds in none_in_window.items() if ds]
    return text + (f"; {'; '.join(gaps)}." if gaps else ".")


def summarise_fred_download(table: pd.DataFrame, n_transforms: int, empty: Sequence[str]) -> str:
    """One sentence on the file the FRED download page will write: series, transforms, rows and date span, and which
    series have no observations in the timeframe. `table` has (Series ID, Title, Field) columns. Describes only."""
    if table.empty:
        return "No observations for the series selected in this timeframe."
    n = table.columns.get_level_values(0).nunique()
    text = (f"{n} series, {n_transforms} transform{'s' * (n_transforms != 1)}, {len(table):,} rows from "
            f"{table.index.min():%d %b %Y} to {table.index.max():%d %b %Y}")
    return text + (f"; no observations in this timeframe for {', '.join(empty)}." if empty else ".")
