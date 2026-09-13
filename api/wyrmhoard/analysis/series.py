"""
Category totals over time, in either direction, over a range the caller chooses.

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

Money in is the same question with the sign flipped, and answering both here
is what let a New Zealand entitlements module leave the core. That module
asked one frozen version of it - what arrived from IRD and MSD over twelve
months - and the scheme names were never the core's business. A jurisdiction
pack asks the general question about whatever categories its rulebook names.
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

#: Which way the money went. The core knows this because it is a fact about a
#: transaction, not a judgement about it - see taxonomy.py.
DIRECTIONS = ("out", "in")

#: What a series is keyed on. `category` is the household's own grouping;
#: `merchant` is who the money went to, by the one normalisation in
#: `categorise.merchant_key`. The second exists because "what did we spend on
#: hobbies" and "what did we spend at that one shop" are different questions,
#: and the second is the one that has changed a household's mind.
BY = ("category", "merchant")

#: How many merchants a merchant series names before folding the rest into
#: one row. A two-year ledger has hundreds; a reader wants the few that
#: matter and the total kept honest, not a list.
DEFAULT_TOP = 10


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
    by: str = "category",
    top: int = DEFAULT_TOP,
) -> dict[str, Any]:
    """Money out, per category or per merchant, per period. See `by_category`."""
    return by_category("out", from_, to, period, categories, anchor, df, by, top)


def received(
    from_: str | date | None = None,
    to: str | date | None = None,
    period: str = "fortnight",
    categories: list[str] | None = None,
    anchor: str | date | None = None,
    df: pd.DataFrame | None = None,
    by: str = "category",
    top: int = DEFAULT_TOP,
) -> dict[str, Any]:
    """
    Money in, per category or per payer, per period. See `by_category`.

    This is what replaced a New Zealand entitlements function that asked one
    hardcoded version of the question - how much arrived from IRD and MSD over
    twelve months. The scheme names were never the core's business. The
    question underneath them is, and it is the same question `spending` asks
    with the sign flipped, so a jurisdiction pack can ask it about whatever
    categories its rulebook cares about.
    """
    return by_category("in", from_, to, period, categories, anchor, df, by, top)


def _per_period(totals: list[float], windows: list[dict[str, Any]]) -> float | None:
    # Averaged over complete periods only. Dividing by every period would let
    # a half-finished fortnight at the end drag the figure down and look like
    # the household had cut back.
    complete = [t for t, w in zip(totals, windows, strict=True) if w["complete"]]
    return round(sum(complete) / len(complete), 2) if complete else None


def _row(
    by: str,
    key: str,
    categories: list[str] | None,
    rules: dict[str, categorise.Rule],
    totals: list[float],
    counts: list[int],
    windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """One series, keyed by category or by merchant, in the same shape."""
    if by == "merchant":
        leading = (categories or ["unknown"])[0]
        rule = rules.get(leading)
        row: dict[str, Any] = {
            "merchant": key,
            "label": key,
            "category": leading,
            "group": rule.group if rule else "unknown",
        }
        if categories and len(categories) > 1:
            row["categories"] = categories
    else:
        rule = rules.get(key)
        row = {
            "category": key,
            "label": rule.label if rule else key.replace("_", " ").title(),
            "group": rule.group if rule else "unknown",
        }
    row.update(
        {
            "totals": totals,
            "transactions": counts,
            "total": round(sum(totals), 2),
            "per_period": _per_period(totals, windows),
        }
    )
    return row


def _everything_else(tail: list[dict[str, Any]], windows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    The merchants beyond `top`, folded into one row so the series still sums
    to the whole. It names how many it stands for and carries no category:
    they will have had several, and picking one would be inventing a fact.
    """
    totals = [round(sum(r["totals"][i] for r in tail), 2) for i in range(len(windows))]
    counts = [sum(r["transactions"][i] for r in tail) for i in range(len(windows))]
    return {
        "merchant": None,
        "label": "everything else",
        "merchants": len(tail),
        "category": None,
        "group": None,
        "totals": totals,
        "transactions": counts,
        "total": round(sum(totals), 2),
        "per_period": _per_period(totals, windows),
    }


def by_category(
    direction: str = "out",
    from_: str | date | None = None,
    to: str | date | None = None,
    period: str = "fortnight",
    categories: list[str] | None = None,
    anchor: str | date | None = None,
    df: pd.DataFrame | None = None,
    by: str = "category",
    top: int = DEFAULT_TOP,
) -> dict[str, Any]:
    """
    Totals per period across a range, in either direction, keyed how the
    caller asks.

    `direction` is `out` for money spent or `in` for money received. Both
    exclude transfers between the household's own accounts, so neither counts
    a shuffle between two pots as either.

    `by` is `category` (the default, one row per category key) or `merchant`
    (one row per counterparty, as `categorise.merchant_key` normalises the
    memo). `categories` filters either way, so "which shops is the hobbies
    money going to" is `by="merchant", categories=["hobbies"]`.

    A merchant series names the `top` largest and folds every other merchant
    into one final row labelled "everything else", carrying how many it
    stands for. The series still sums to the whole: a top ten that quietly
    lost its tail would be a total that looks complete and is not. `top=0`
    means every merchant.

    `from_` and `to` default to the whole ledger. Periods outside what the
    ledger actually covers, and the period currently in progress, come back
    marked `complete: false` rather than being removed.

    `totals` in each series lines up with `periods` by position. Positional
    rather than repeated dates because a two-year weekly series across thirty
    categories is otherwise mostly timestamps.
    """
    if period not in PERIODS:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}")
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {', '.join(DIRECTIONS)}")
    if by not in BY:
        raise ValueError(f"by must be one of {', '.join(BY)}")
    if top < 0:
        raise ValueError("top must be 0 (every merchant) or a positive count")

    df = cashflow.frame() if df is None else df
    currency = config.household().currency

    chosen = pay_anchor(df)
    if anchor:
        chosen = {"anchor": _must_date(anchor).isoformat(), "source": "caller"}
    anchor_date = _must_date(chosen["anchor"])

    empty: dict[str, Any] = {
        "available": False,
        "reason": "No transactions in the ledger yet.",
        "direction": direction,
        "by": by,
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

    # Spending is stored negative, so flip its sign to report a cost as a
    # positive number. Money in is already positive.
    sub = df[df["is_spend"]] if direction == "out" else df[df["is_income"]]
    sign = -1.0 if direction == "out" else 1.0
    sub = sub[
        (sub["date"] >= pd.Timestamp(spans[0][0])) & (sub["date"] <= pd.Timestamp(spans[-1][1]))
    ]
    if categories:
        wanted = {c.strip() for c in categories if c and c.strip()}
        sub = sub[sub["category"].isin(wanted)]

    if sub.empty:
        return {
            "available": True,
            "direction": direction,
            "by": by,
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
    if by == "merchant":
        sub = sub.assign(merchant=sub["memo"].map(categorise.merchant_key))
    grouped = sub.groupby([by, "bucket"])["amount"].agg(["sum", "count"])

    # A merchant can sit in more than one category - a supermarket that sells
    # fuel, a shop recategorised partway through the ledger. Report the one
    # most of its rows carry, and name the others rather than pretend.
    categories_of: dict[str, list[str]] = {}
    if by == "merchant":
        categories_of = {
            str(m): [str(c) for c in chunk["category"].value_counts().index]
            for m, chunk in sub.groupby("merchant")
        }

    rules = categorise.rule_index()
    series: list[dict[str, Any]] = []
    for key, chunk in grouped.groupby(level=by):
        totals = [0.0] * len(windows)
        counts = [0] * len(windows)
        for (_, bucket), row in chunk.iterrows():
            slot = index.get(bucket.date().isoformat())
            if slot is None:
                continue
            totals[slot] = round(float(sign * row["sum"]), 2)
            counts[slot] = int(row["count"])
        series.append(
            _row(by, str(key), categories_of.get(str(key)), rules, totals, counts, windows)
        )

    series.sort(key=lambda s: s["total"], reverse=True)
    if by == "merchant" and top and len(series) > top:
        series = [*series[:top], _everything_else(series[top:], windows)]
    return {
        "available": True,
        "direction": direction,
        "by": by,
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
