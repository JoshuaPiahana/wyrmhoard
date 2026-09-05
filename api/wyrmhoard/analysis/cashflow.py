"""
Cash flow: the honest answer to "where does it actually go?"

Two decisions shape everything downstream:

  1. Internal transfers are excluded from both income and spending. Moving
     $200 from Everyday to Savings is not earning $200 and not spending $200,
     but a naive sum counts it as both and inflates the picture ~30%.

  2. "Income" means money that arrives from outside the household. If only one
     account is imported, transfers *out* to an un-imported account will look
     like spending. The tool flags that rather than pretending otherwise.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from .. import cache, categorise, config, db

SPEND_GROUPS = {"essential", "discretionary", "sinking", "commitment"}


def _empty_frame() -> pd.DataFrame:
    """
    An empty ledger, typed exactly like a populated one.

    This matters more than it looks. An empty ledger is the normal state for
    anyone who has just installed the tool, and a bare `pd.DataFrame(columns=
    [...])` gives every column `object` dtype and omits the derived ones
    entirely. Consumers then either KeyError on `is_spend`, or do date
    arithmetic against a float NaN and get a deprecation warning that pandas
    has said will become a hard error.

    Returning a correctly-typed frame means every downstream function behaves
    identically whether or not the household has imported anything yet.
    """
    return pd.DataFrame(
        {
            "fingerprint": pd.Series(dtype="object"),
            "account": pd.Series(dtype="object"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "memo": pd.Series(dtype="object"),
            "amount": pd.Series(dtype="float64"),
            "balance": pd.Series(dtype="float64"),
            "category": pd.Series(dtype="object"),
            "grp": pd.Series(dtype="object"),
            "month": pd.Series(dtype="object"),
            "is_spend": pd.Series(dtype="bool"),
            "is_income": pd.Series(dtype="bool"),
        }
    )


@cache.by_ledger
def frame() -> pd.DataFrame:
    """
    The whole ledger as a DataFrame, with month and direction derived.

    Cached until the ledger changes, because every other function here starts
    by calling it and one dashboard load asks nine times.

    Callers must treat the result as read-only. Every consumer in this package
    filters into a new object rather than assigning into it; if you need to add
    a column, take a .copy() first.
    """
    rows = db.all_transactions()
    if not rows:
        return _empty_frame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["grp"] = df["grp"].fillna("unknown")
    df["category"] = df["category"].fillna("uncategorised")
    df["is_spend"] = (df["amount"] < 0) & (df["grp"] != "transfer")
    df["is_income"] = (df["amount"] > 0) & (df["grp"] != "transfer")
    return df


def _label_for(key: str) -> str:
    idx = categorise.rule_index()
    rule = idx.get(key)
    return rule.label if rule else key.replace("_", " ").title()


def monthly(df: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    """Income, spending and the gap, month by month."""
    df = frame() if df is None else df
    if df.empty:
        return []
    g = df.groupby("month")
    out = []
    for month, chunk in g:
        income = float(chunk.loc[chunk["is_income"], "amount"].sum())
        spend = float(-chunk.loc[chunk["is_spend"], "amount"].sum())
        out.append(
            {
                "month": month,
                "income": round(income, 2),
                "spend": round(spend, 2),
                "net": round(income - spend, 2),
                "savings_rate_pct": round(100 * (income - spend) / income, 1)
                if income > 0
                else None,
                "transactions": int(len(chunk)),
            }
        )
    return sorted(out, key=lambda r: r["month"])


def complete_months(df: pd.DataFrame | None = None) -> list[str]:
    """
    Months we have full data for.

    The first and last month of an export are almost always partial, and
    including them drags averages in a way that quietly misleads. A month
    counts as complete if we have transactions within 3 days of both ends.

    The month in progress is always excluded, however full it looks. Judging
    the household on a month that is three days old produces alarming
    nonsense - a fortnightly salary may not have landed yet, so income reads
    as near zero while the rent has already gone out.
    """
    df = frame() if df is None else df
    if df.empty:
        return []
    today = pd.Timestamp.today().normalize()
    months = []
    for month, chunk in df.groupby("month"):
        period = pd.Period(month, freq="M")
        first, last = period.start_time, period.end_time
        if last >= today:
            continue
        lo, hi = chunk["date"].min(), chunk["date"].max()
        if (lo - first).days <= 3 and (last - hi).days <= 3:
            months.append(month)
    return sorted(months)


def typical_month(df: pd.DataFrame | None = None, months: int = 6) -> dict[str, Any]:
    """
    A representative month, built from complete months only.

    Uses the median rather than the mean: one $3,000 car repair should not
    become "your normal monthly spending". The mean is reported alongside so
    the gap between them is visible - a wide gap means lumpy spending, which
    is itself a finding.
    """
    df = frame() if df is None else df
    good = complete_months(df)
    if not good:
        return {"available": False, "reason": "Not enough complete months of data yet."}

    recent = good[-months:]
    rows = [m for m in monthly(df) if m["month"] in recent]
    if not rows:
        return {"available": False, "reason": "No complete months in range."}

    inc = pd.Series([r["income"] for r in rows])
    spd = pd.Series([r["spend"] for r in rows])

    sub = df[df["month"].isin(recent)]
    by_group = {}
    for grp in SPEND_GROUPS | {"unknown"}:
        vals = (
            sub[(sub["grp"] == grp) & (sub["amount"] < 0)]
            .groupby("month")["amount"]
            .sum()
            .abs()
            .reindex(recent, fill_value=0.0)
        )
        by_group[grp] = round(float(vals.median()), 2)

    median_income = float(inc.median())
    median_spend = float(spd.median())

    return {
        "available": True,
        "months_used": recent,
        "month_count": len(recent),
        "income_median": round(median_income, 2),
        "income_mean": round(float(inc.mean()), 2),
        "spend_median": round(median_spend, 2),
        "spend_mean": round(float(spd.mean()), 2),
        "net_median": round(median_income - median_spend, 2),
        "savings_rate_pct": round(100 * (median_income - median_spend) / median_income, 1)
        if median_income > 0
        else None,
        "by_group": by_group,
        "essentials_total": round(
            by_group.get("essential", 0)
            + by_group.get("commitment", 0)
            + by_group.get("sinking", 0),
            2,
        ),
        "discretionary_total": round(by_group.get("discretionary", 0), 2),
        "lumpiness": round(abs(float(spd.mean()) - median_spend) / median_spend * 100, 1)
        if median_spend
        else 0.0,
    }


def by_category(df: pd.DataFrame | None = None, months: int | None = 6) -> list[dict[str, Any]]:
    """Spending by category over the recent complete months, largest first."""
    df = frame() if df is None else df
    if df.empty:
        return []
    good = complete_months(df)
    if months and good:
        good = good[-months:]
    sub = df[df["month"].isin(good)] if good else df
    sub = sub[sub["is_spend"]]
    if sub.empty:
        return []

    n_months = max(1, len(good) if good else 1)
    g = sub.groupby("category")["amount"].agg(["sum", "count"])
    total = float(-g["sum"].sum())

    out = []
    for cat, row in g.iterrows():
        spend = float(-row["sum"])
        idx = categorise.rule_index().get(cat)
        out.append(
            {
                "category": cat,
                "label": _label_for(cat),
                "group": idx.group if idx else "unknown",
                "flagged": bool(idx.flag) if idx else False,
                "total": round(spend, 2),
                "per_month": round(spend / n_months, 2),
                "per_year": round(spend / n_months * 12, 2),
                "share_pct": round(100 * spend / total, 1) if total else 0.0,
                "transactions": int(row["count"]),
            }
        )
    return sorted(out, key=lambda r: r["total"], reverse=True)


def small_leaks(df: pd.DataFrame | None = None, months: int = 6) -> dict[str, Any]:
    """
    Death by a thousand cuts.

    Households routinely under-estimate small spending by a wide margin,
    because no single transaction feels worth remembering. Annualising it is
    usually the most surprising number in the whole report.
    """
    df = frame() if df is None else df
    threshold = config.household().small_transaction_threshold
    good = complete_months(df)[-months:] if not df.empty else []
    sub = df[df["month"].isin(good)] if good else df
    sub = sub[sub["is_spend"] & (sub["amount"].abs() < threshold)]
    n_months = max(1, len(good) if good else 1)

    if sub.empty:
        return {"threshold": threshold, "available": False}

    total = float(-sub["amount"].sum())
    return {
        "available": True,
        "threshold": threshold,
        "count": int(len(sub)),
        "total": round(total, 2),
        "per_month": round(total / n_months, 2),
        "per_year": round(total / n_months * 12, 2),
        "average_size": round(total / len(sub), 2),
        "per_week": round(total / n_months * 12 / 52, 2),
    }


def cash_position() -> dict[str, Any]:
    """
    Cash on hand, and how long it would last.

    Prefers the latest running balance from the bank export. Falls back to the
    household's declared opening balance when balances are not in the export.
    """
    df = frame()
    hh = config.household()

    declared = None
    for acct in hh.raw.get("accounts", []) or []:
        if acct.get("kind") in {"savings", "transaction"} and acct.get("opening_balance"):
            declared = float(acct["opening_balance"])
            break

    latest_balances: dict[str, float] = {}
    if not df.empty and df["balance"].notna().any():
        withbal = df[df["balance"].notna()].sort_values("date")
        for account, chunk in withbal.groupby("account"):
            latest_balances[account] = round(float(chunk.iloc[-1]["balance"]), 2)

    total = sum(latest_balances.values()) if latest_balances else declared
    typ = typical_month(df)
    essentials = typ.get("essentials_total") if typ.get("available") else None

    weeks = None
    if total is not None and essentials:
        weeks = round(total / (essentials / 4.33), 1)

    return {
        "total": round(total, 2) if total is not None else None,
        "source": "bank export" if latest_balances else "household.yml (declared)",
        "by_account": latest_balances,
        "monthly_essentials": essentials,
        "runway_weeks": weeks,
        "as_at": df["date"].max().date().isoformat() if not df.empty else None,
    }


def trend(months: int = 12) -> dict[str, Any]:
    """
    Is it getting better or worse?

    Compares the most recent three complete months against the three before
    them. This is the number the family meeting actually turns on.
    """
    rows = monthly()
    good = set(complete_months())
    rows = [r for r in rows if r["month"] in good][-months:]
    if len(rows) < 4:
        return {"available": False, "reason": "Need at least four complete months to show a trend."}

    recent = rows[-3:]
    prior = rows[-6:-3] if len(rows) >= 6 else rows[:-3]
    if not prior:
        return {"available": False, "reason": "Not enough history to compare against."}

    def avg(rs, key):
        return sum(r[key] for r in rs) / len(rs)

    recent_net, prior_net = avg(recent, "net"), avg(prior, "net")
    recent_spend, prior_spend = avg(recent, "spend"), avg(prior, "spend")
    recent_income, prior_income = avg(recent, "income"), avg(prior, "income")

    spend_change = recent_spend - prior_spend
    income_change = recent_income - prior_income

    # Which side actually moved? Reporting a worsening position while quoting
    # a falling spend number reads as a contradiction and sends people after
    # the wrong problem, so name the dominant driver explicitly.
    driver = "income" if abs(income_change) > abs(spend_change) else "spending"

    return {
        "available": True,
        "recent_months": [r["month"] for r in recent],
        "prior_months": [r["month"] for r in prior],
        "net_now": round(recent_net, 2),
        "net_before": round(prior_net, 2),
        "net_change": round(recent_net - prior_net, 2),
        "spend_now": round(recent_spend, 2),
        "spend_before": round(prior_spend, 2),
        "spend_change": round(spend_change, 2),
        "income_now": round(recent_income, 2),
        "income_before": round(prior_income, 2),
        "income_change": round(income_change, 2),
        "driver": driver,
        "direction": "improving"
        if recent_net > prior_net
        else "worsening"
        if recent_net < prior_net
        else "flat",
    }


@cache.by_ledger
def summary() -> dict[str, Any]:
    """Everything the dashboard needs in one call."""
    df = frame()
    return {
        "stats": db.stats(),
        "coverage": categorise.coverage(),
        "monthly": monthly(df),
        "typical_month": typical_month(df),
        "by_category": by_category(df),
        "small_leaks": small_leaks(df),
        "cash": cash_position(),
        "trend": trend(),
        "generated_at": date.today().isoformat(),
    }
