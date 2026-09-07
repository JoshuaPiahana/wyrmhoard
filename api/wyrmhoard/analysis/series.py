"""
Spending over time, per category, over a range the caller chooses.

Everything else in this package answers "the last N complete months", which
quietly decides three things on the caller's behalf: how long a window is,
where it ends, and that a month is the right unit at all.

None of those hold generally. A household paid fortnightly does not live in
months - some months carry two pay days and some carry three, so a monthly
series makes their spending look like it swings when only the calendar did.
And "petrol against inflation over two years" is a question about a range
somebody picked, not about the last six months.

So this takes `from`, `to` and a period, and reports what it found.

Two rules it follows that the older functions do not:

**Nothing is silently dropped.** `complete_months()` discards partial months
without saying so, which is defensible for a headline figure and wrong for a
series - a chart with a missing bar at the end reads as "we spent nothing",
which is the opposite of the truth. Every period is returned, carrying
`complete`, and the caller decides what to draw.

**Gaps are zeroes, not absences.** A fortnight with no fuel in it is a real
observation about a household. Leaving the point out entirely would let a
plotting library join the line across it and hide the fact.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise
from typing import Any

import pandas as pd

from .. import categorise, config, db
from . import cashflow

#: How long a period is, in days. `month` is handled separately - calendar
#: months are not a fixed number of days, which is part of why they are a poor
#: unit for anyone whose money arrives every fourteen.
PERIOD_DAYS = {"week": 7, "fortnight": 14}
PERIODS = ("week", "fortnight", "month")


def _as_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _must_date(value: str | date) -> date:
    parsed = _as_date(value)
    if parsed is None:
        raise ValueError(f"Not a date: {value!r}")
    return parsed


def pay_anchor(df: pd.DataFrame | None = None) -> dict[str, Any]:
    """
    The day a fortnight starts on, and where that came from.

    A week can align itself - Monday is a convention everyone shares. A
    fortnight cannot: it needs a day to count from, and for a household paid
    every second Thursday the only alignment that means anything is their pay
    day. Getting it wrong splits one pay cycle across two buckets and makes
    every figure wobble.

    Three sources, most trustworthy first, and the answer always says which was
    used. An inferred anchor is a guess about somebody's life, and a guess
    reported as a fact is how a chart ends up quietly wrong.
    """
    declared = config.household().settings.get("pay_anchor")
    if declared:
        return {"anchor": _must_date(declared).isoformat(), "source": "household.yml"}

    evidenced = _fortnightly_employer()
    if evidenced:
        return {"anchor": evidenced, "source": "payslips showing a fortnightly cycle"}

    df = cashflow.frame() if df is None else df
    if df.empty:
        return {"anchor": date.today().isoformat(), "source": "today (no data)"}
    first = df["date"].min().date()
    return {
        "anchor": (first - timedelta(days=first.weekday())).isoformat(),
        # Named as arbitrary on purpose. It keeps the buckets a consistent
        # width, which is all a Monday can honestly promise, and anybody whose
        # pay lands on a Thursday should set `pay_anchor` in household.yml.
        "source": "Monday of the first week in the ledger (arbitrary - set settings.pay_anchor)",
    }


def _fortnightly_employer() -> str | None:
    """
    The earliest pay date of an employer that demonstrably pays fortnightly.

    "Earliest payslip" is not evidence, and using it was a bug waiting for the
    right import order. A household with two jobs on different cycles - a
    salaried one paying every second Wednesday and casual work paying whenever
    it happens - would have anchored every fortnight to whichever payslip
    happened to sort first, silently splitting each pay cycle across two
    buckets.

    So an employer counts only if two of its pay dates are actually fourteen
    days apart. One payslip proves nothing; two a week apart prove it is not
    fortnightly. Anything short of that falls through to the caller being told
    to declare it.
    """
    by_employer: dict[str, set[str]] = {}
    for row in db.payslips():
        if row.get("pay_date"):
            by_employer.setdefault(str(row.get("employer") or ""), set()).add(row["pay_date"][:10])

    candidates: list[str] = []
    for dates in by_employer.values():
        ordered = sorted(date.fromisoformat(d) for d in dates)
        gaps = {(b - a).days for a, b in pairwise(ordered)}
        if any(gap % 14 == 0 for gap in gaps):
            candidates.append(ordered[0].isoformat())
    return min(candidates) if len(candidates) == 1 else None


def _bucket_start(dates: pd.Series, period: str, anchor: date) -> pd.Series:
    """Which period each transaction falls in, as that period's first day."""
    if period == "month":
        return dates.dt.to_period("M").dt.start_time
    span = PERIOD_DAYS[period]
    origin = pd.Timestamp(anchor)
    # Floor division, so dates before the anchor bucket backwards correctly
    # rather than collapsing onto it.
    steps = ((dates - origin).dt.days // span).astype("int64")
    return origin + pd.to_timedelta(steps * span, unit="D")


def _walk(start: date, end: date, period: str, anchor: date) -> list[tuple[date, date]]:
    """Every period between two dates, as (first day, last day) pairs."""
    out: list[tuple[date, date]] = []
    if period == "month":
        cursor = pd.Timestamp(start).to_period("M")
        last = pd.Timestamp(end).to_period("M")
        while cursor <= last:
            out.append((cursor.start_time.date(), cursor.end_time.date()))
            cursor += 1
        return out

    span = PERIOD_DAYS[period]
    steps = (start - anchor).days // span
    cursor = anchor + timedelta(days=steps * span)
    while cursor <= end:
        out.append((cursor, cursor + timedelta(days=span - 1)))
        cursor += timedelta(days=span)
    return out


def spending(
    from_: str | date | None = None,
    to: str | date | None = None,
    period: str = "fortnight",
    categories: list[str] | None = None,
    anchor: str | date | None = None,
    df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Spending per category, per period, across a range.

    `from_` and `to` default to the whole ledger. Periods outside what the
    ledger actually covers, and the period currently in progress, come back
    marked `complete: false` rather than being removed.

    `totals` in each series lines up with `periods` by position. Positional
    rather than repeated dates because a two-year weekly series across thirty
    categories is otherwise mostly timestamps.
    """
    if period not in PERIODS:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}")

    df = cashflow.frame() if df is None else df
    currency = config.household().currency

    chosen = pay_anchor(df)
    if anchor:
        chosen = {"anchor": _must_date(anchor).isoformat(), "source": "caller"}
    anchor_date = _must_date(chosen["anchor"])

    empty: dict[str, Any] = {
        "available": False,
        "reason": "No transactions in the ledger yet.",
        "period": period,
        "anchor": chosen["anchor"],
        "anchor_source": chosen["source"],
        "unit": currency,
        "periods": [],
        "series": [],
    }
    if df.empty:
        return empty

    ledger_first, ledger_last = df["date"].min().date(), df["date"].max().date()
    start = _as_date(from_) or ledger_first
    end = _as_date(to) or ledger_last
    if end < start:
        start, end = end, start

    spans = _walk(start, end, period, anchor_date)
    if not spans:
        return {**empty, "reason": "The requested range does not contain a whole period."}

    today = date.today()
    windows = [
        {
            "start": lo.isoformat(),
            "end": hi.isoformat(),
            # Complete means the ledger can actually answer for the whole
            # period: it has to be over, and it has to sit inside what was
            # imported. An unfinished period and one that predates the export
            # both read as low spending, and neither is.
            "complete": bool(hi < today and lo >= ledger_first and hi <= ledger_last),
        }
        for lo, hi in spans
    ]
    index = {w["start"]: i for i, w in enumerate(windows)}

    sub = df[df["is_spend"]]
    sub = sub[
        (sub["date"] >= pd.Timestamp(spans[0][0])) & (sub["date"] <= pd.Timestamp(spans[-1][1]))
    ]
    if categories:
        wanted = {c.strip() for c in categories if c and c.strip()}
        sub = sub[sub["category"].isin(wanted)]

    if sub.empty:
        return {
            "available": True,
            "period": period,
            "anchor": chosen["anchor"],
            "anchor_source": chosen["source"],
            "requested": {"from": start.isoformat(), "to": end.isoformat()},
            "ledger_covers": {"from": ledger_first.isoformat(), "to": ledger_last.isoformat()},
            "unit": currency,
            "complete_periods": sum(1 for w in windows if w["complete"]),
            "periods": windows,
            "series": [],
        }

    sub = sub.assign(bucket=_bucket_start(sub["date"], period, anchor_date))
    grouped = sub.groupby(["category", "bucket"])["amount"].agg(["sum", "count"])

    rules = categorise.rule_index()
    series: list[dict[str, Any]] = []
    for cat, chunk in grouped.groupby(level="category"):
        totals = [0.0] * len(windows)
        counts = [0] * len(windows)
        for (_, bucket), row in chunk.iterrows():
            slot = index.get(bucket.date().isoformat())
            if slot is None:
                continue
            totals[slot] = round(float(-row["sum"]), 2)
            counts[slot] = int(row["count"])
        rule = rules.get(str(cat))
        complete_totals = [t for t, w in zip(totals, windows, strict=True) if w["complete"]]
        series.append(
            {
                "category": str(cat),
                "label": rule.label if rule else str(cat).replace("_", " ").title(),
                "group": rule.group if rule else "unknown",
                "totals": totals,
                "transactions": counts,
                "total": round(sum(totals), 2),
                # Averaged over complete periods only. Dividing by every period
                # would let a half-finished fortnight at the end drag the
                # figure down and look like the household had cut back.
                "per_period": round(sum(complete_totals) / len(complete_totals), 2)
                if complete_totals
                else None,
            }
        )

    series.sort(key=lambda s: s["total"], reverse=True)
    return {
        "available": True,
        "period": period,
        "anchor": chosen["anchor"],
        "anchor_source": chosen["source"],
        "requested": {"from": start.isoformat(), "to": end.isoformat()},
        "ledger_covers": {"from": ledger_first.isoformat(), "to": ledger_last.isoformat()},
        "unit": currency,
        "complete_periods": sum(1 for w in windows if w["complete"]),
        "periods": windows,
        "series": series,
    }
