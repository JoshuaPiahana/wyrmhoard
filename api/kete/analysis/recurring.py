"""
Finding the payments that repeat.

Subscriptions, direct debits and standing commitments are where money leaves
quietly. Nobody decides each month to pay for the streaming service they
stopped watching in 2023 - they just never decided to stop.

Detection is deliberately conservative. A false positive ("you have a $400
monthly subscription") would send a family hunting for something that does not
exist, so a payment must repeat at least three times, on a recognisable
cadence, at a stable amount, before it is called recurring.
"""

from __future__ import annotations

import statistics
from typing import Any

import pandas as pd

from .. import cache, categorise
from .cashflow import frame

# Cadences we recognise, in days, with the tolerance allowed either side.
CADENCES: list[tuple[str, float, float]] = [
    ("weekly", 7, 2),
    ("fortnightly", 14, 3),
    ("monthly", 30.4, 5),
    ("quarterly", 91.3, 12),
    ("half-yearly", 182.6, 20),
    ("yearly", 365.25, 35),
]

PER_YEAR = {
    "weekly": 52.0,
    "fortnightly": 26.0,
    "monthly": 12.0,
    "quarterly": 4.0,
    "half-yearly": 2.0,
    "yearly": 1.0,
}


def _merchant_key(memo: str) -> str:
    """A stable-ish merchant identity, with reference numbers stripped out."""
    cleaned = categorise.strip_noise(memo)
    words = [w for w in cleaned.split() if not w.isdigit()]
    return " ".join(words[:4])[:40] or "(blank)"


def _classify_cadence(intervals: list[float]) -> tuple[str | None, float]:
    """Returns (cadence name, regularity score 0-1)."""
    if len(intervals) < 2:
        return None, 0.0
    median = statistics.median(intervals)
    for name, days, tol in CADENCES:
        if abs(median - days) <= tol:
            spread = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
            regularity = max(0.0, 1.0 - (spread / max(days, 1)))
            return name, round(regularity, 2)
    return None, 0.0


@cache.by_ledger
def detect(min_occurrences: int = 3, months: int = 12) -> list[dict[str, Any]]:
    df = frame()
    if df.empty:
        return []

    cutoff = df["date"].max() - pd.Timedelta(days=months * 31)
    sub = df[(df["date"] >= cutoff) & df["is_spend"]].copy()
    if sub.empty:
        return []

    sub["merchant"] = sub["memo"].map(_merchant_key)
    idx = categorise.rule_index()
    out: list[dict[str, Any]] = []

    for merchant, chunk in sub.groupby("merchant"):
        if len(chunk) < min_occurrences:
            continue
        chunk = chunk.sort_values("date")
        dates = chunk["date"].tolist()
        intervals = [
            (dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)
        ]
        intervals = [i for i in intervals if i > 0]
        if not intervals:
            continue

        cadence, regularity = _classify_cadence([float(i) for i in intervals])
        if not cadence:
            continue

        amounts = chunk["amount"].abs()
        mean_amt = float(amounts.mean())
        if mean_amt <= 0:
            continue
        variation = float(amounts.std() / mean_amt) if len(amounts) > 1 else 0.0

        # A "subscription" has a stable price. Groceries repeat weekly too, but
        # the amount swings, so variation keeps them out of this list.
        stable = variation < 0.25
        if not stable and cadence in {"weekly", "fortnightly"}:
            continue

        cat = chunk["category"].mode()
        cat = cat.iloc[0] if not cat.empty else "uncategorised"
        rule = idx.get(cat)
        last_seen = dates[-1]
        days_since = int((df["date"].max() - last_seen).days)

        out.append(
            {
                "merchant": merchant,
                "example_memo": chunk.iloc[-1]["memo"],
                "category": cat,
                "label": rule.label if rule else cat,
                "group": rule.group if rule else "unknown",
                "cadence": cadence,
                "occurrences": int(len(chunk)),
                "typical_amount": round(float(amounts.median()), 2),
                "amount_varies": not stable,
                "annual_cost": round(float(amounts.median()) * PER_YEAR[cadence], 2),
                "monthly_cost": round(
                    float(amounts.median()) * PER_YEAR[cadence] / 12, 2
                ),
                "regularity": regularity,
                "first_seen": dates[0].date().isoformat(),
                "last_seen": last_seen.date().isoformat(),
                "days_since_last": days_since,
                # Something on a monthly cadence not seen for 2+ cycles has
                # probably stopped - worth confirming rather than budgeting for.
                "possibly_cancelled": days_since
                > (365.25 / PER_YEAR[cadence]) * 2,
            }
        )

    return sorted(out, key=lambda r: r["annual_cost"], reverse=True)


def summary(min_occurrences: int = 3) -> dict[str, Any]:
    items = detect(min_occurrences=min_occurrences)
    active = [i for i in items if not i["possibly_cancelled"]]
    subs = [i for i in active if i["category"] == "subscriptions"]

    return {
        "count": len(active),
        "total_annual": round(sum(i["annual_cost"] for i in active), 2),
        "total_monthly": round(sum(i["monthly_cost"] for i in active), 2),
        "subscriptions_count": len(subs),
        "subscriptions_annual": round(sum(i["annual_cost"] for i in subs), 2),
        "subscriptions_monthly": round(sum(i["monthly_cost"] for i in subs), 2),
        "items": items,
    }
