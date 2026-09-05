"""
Mortgage arithmetic.

This is amortisation maths, not advice. It answers "if you keep doing X, when
does the loan end and what does the interest cost?" and nothing more. What a
household should actually do with a spare dollar depends on things this tool
cannot see, and on a sequencing question it deliberately takes a view on:

a household with a fortnight of cash in the bank is not in a position to throw
money at a mortgage. A buffer comes first, because the alternative to a buffer
is not "faster mortgage payoff" - it is credit at 20% the first time the car
needs a new clutch.
"""

from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

PERIODS_PER_YEAR = {
    "weekly": 52,
    "fortnightly": 26,
    "monthly": 12,
}

# Banks announce repayment changes as their own zero-dollar transaction, e.g.
# "From 735.44 to 722.42 due 10SEP2026". That is a rate change or a refix
# landing, and it is worth more than most of what a budgeting tool can tell
# somebody - so it is parsed rather than left sitting in Uncategorised.
_REPAYMENT_NOTICE = re.compile(
    r"FROM\s+([\d,]+\.\d{2})\s+TO\s+([\d,]+\.\d{2})\s+DUE\s+(\d{1,2}\s*[A-Z]{3}\s*\d{4})",
    re.I,
)

# An offset loan charges interest on (balance - offset accounts), and states
# what the arrangement saved: "LOAN INTEREST OFFSET Benefit of $8.85".
# Ignoring it makes the implied interest rate absurdly low, because the
# interest actually charged is only a fraction of what the balance would cost.
_OFFSET_BENEFIT = re.compile(r"BENEFIT OF \$?([\d,]+\.?\d*)", re.I)


def _needs(*fields: str) -> dict[str, Any]:
    return {
        "available": False,
        "missing": list(fields),
        "reason": "Fill these into config/household.yml under `mortgage:` - "
        "they are on your Kiwibank loan summary.",
    }


def schedule(
    balance: float,
    annual_rate_pct: float,
    repayment: float,
    frequency: str = "fortnightly",
    extra: float = 0.0,
    max_years: int = 60,
) -> dict[str, Any]:
    """Run the loan forward until it clears. Returns totals and a yearly curve."""
    n = PERIODS_PER_YEAR.get(frequency, 26)
    rate = (annual_rate_pct / 100.0) / n
    payment = repayment + extra

    interest_only = balance * rate
    if payment <= interest_only:
        return {
            "available": False,
            "reason": (
                f"A payment of ${payment:,.2f} per {frequency[:-2] if frequency.endswith('ly') else frequency} "
                f"does not cover the ${interest_only:,.2f} of interest that accrues each period, "
                "so the balance would never fall."
            ),
        }

    bal = balance
    total_interest = 0.0
    periods = 0
    curve: list[dict[str, Any]] = []
    start = date.today()
    limit = max_years * n

    while bal > 0.005 and periods < limit:
        interest = bal * rate
        principal = min(payment - interest, bal)
        bal -= principal
        total_interest += interest
        periods += 1
        if periods % n == 0 or bal <= 0.005:
            curve.append(
                {
                    "year": round(periods / n, 2),
                    "balance": round(max(bal, 0.0), 2),
                    "interest_paid": round(total_interest, 2),
                }
            )

    years = periods / n
    payoff = start + timedelta(days=int(years * 365.25))

    return {
        "available": True,
        "periods": periods,
        "years": round(years, 2),
        "payoff_date": payoff.isoformat(),
        "payment_per_period": round(payment, 2),
        "frequency": frequency,
        "total_interest": round(total_interest, 2),
        "total_paid": round(total_interest + balance, 2),
        "curve": curve,
    }


def scenarios(
    balance: float,
    annual_rate_pct: float,
    repayment: float,
    frequency: str = "fortnightly",
    extras: tuple[float, ...] = (0, 25, 50, 100, 200),
) -> dict[str, Any]:
    """
    What each extra dollar per payment actually buys.

    Presented as time saved and interest saved, because "seven years earlier"
    lands in a family meeting in a way that "$48,000 of interest" does not.
    """
    base = schedule(balance, annual_rate_pct, repayment, frequency, extra=0)
    if not base.get("available"):
        return base

    out = []
    for extra in extras:
        s = schedule(balance, annual_rate_pct, repayment, frequency, extra=extra)
        if not s.get("available"):
            continue
        out.append(
            {
                "extra_per_period": extra,
                "extra_per_month": round(extra * PERIODS_PER_YEAR.get(frequency, 26) / 12, 2),
                "years": s["years"],
                "payoff_date": s["payoff_date"],
                "total_interest": s["total_interest"],
                "years_saved": round(base["years"] - s["years"], 2),
                "interest_saved": round(base["total_interest"] - s["total_interest"], 2),
            }
        )
    return {"available": True, "base": base, "scenarios": out}


def _parse_notice_date(raw: str) -> str | None:
    """'10SEP2026' or '10 SEP 2026' to an ISO date."""
    cleaned = re.sub(r"\s+", "", raw).upper()
    try:
        return datetime.strptime(cleaned, "%d%b%Y").date().isoformat()
    except ValueError:
        return None


def _cadence_from(dates: list[date]) -> tuple[str | None, int]:
    """Name the rhythm of a series of payments, and how many fall in a year."""
    if len(dates) < 3:
        return None, 0
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None, 0
    median = statistics.median(gaps)
    for name, days, tol in (
        ("weekly", 7, 2),
        ("fortnightly", 14, 3),
        ("monthly", 30.4, 5),
    ):
        if abs(median - days) <= tol:
            return name, PERIODS_PER_YEAR[name]
    return None, 0


def infer_loans(months: int = 12) -> list[dict[str, Any]]:
    """
    Work out each loan's real terms from its own transactions.

    A loan account records everything needed: repayments arrive as credits,
    interest as debits, and the balance after each. So rather than asking a
    household to copy figures off a statement - which they will do once, and
    never update - the terms are derived and kept current automatically.

    Three things this handles that a naive reading gets wrong:

      * Repayments change. A refix or a floating rate moves them, so there is
        no single "the repayment". The most recent one is used and the history
        is reported.
      * Offset loans charge interest on the balance MINUS the linked accounts.
        Dividing interest charged by balance then implies a rate near zero.
        The statement says what the offset saved, so it is added back to
        recover the gross interest.
      * A fully-offset fortnight is charged nothing at all and produces no
        row. Summing a year and dividing understates the rate, so the rate is
        computed per period and the median taken.
    """
    from .. import accounts as accounts_mod
    from .cashflow import frame

    df = frame()
    if df.empty:
        return []

    cutoff = df["date"].max() - pd.DateOffset(months=months)
    out: list[dict[str, Any]] = []

    for account in sorted(accounts_mod.liability_accounts()):
        rows = df[df["account"] == account].sort_values("date")
        if rows.empty:
            continue

        with_balance = rows[rows["balance"].notna()]
        balance = abs(float(with_balance.iloc[-1]["balance"])) if len(with_balance) else None

        # Repayments: money in reduces what is owed.
        credits = rows[rows["amount"] > 0]
        repayment_changes: list[dict[str, Any]] = []
        for _, r in credits.iterrows():
            amount = round(float(r["amount"]), 2)
            if not repayment_changes or repayment_changes[-1]["amount"] != amount:
                repayment_changes.append(
                    {"amount": amount, "from_date": r["date"].date().isoformat()}
                )
        repayment = repayment_changes[-1]["amount"] if repayment_changes else None
        cadence, per_year = _cadence_from([d.date() for d in credits["date"]])

        # Interest, grossed up for any offset benefit.
        interest_rows = rows[(rows["category"] == "loan_interest") & (rows["date"] >= cutoff)]
        charged = float(abs(interest_rows["amount"]).sum())
        benefit = 0.0
        rates: list[float] = []
        for _, r in interest_rows.iterrows():
            gross = abs(float(r["amount"]))
            match = _OFFSET_BENEFIT.search(str(r["memo"]))
            if match:
                saved = float(match.group(1).replace(",", ""))
                benefit += saved
                gross += saved
            bal = abs(float(r["balance"])) if r["balance"] and r["balance"] != 0 else None
            if bal and per_year:
                rates.append(gross / bal * per_year * 100)

        # Upcoming repayment change, straight from the bank's own notice.
        upcoming = None
        for _, r in rows.iterrows():
            m = _REPAYMENT_NOTICE.search(str(r["memo"]))
            if not m:
                continue
            due = _parse_notice_date(m.group(3))
            if due and due >= date.today().isoformat():
                upcoming = {
                    "from": float(m.group(1).replace(",", "")),
                    "to": float(m.group(2).replace(",", "")),
                    "due": due,
                }

        loan: dict[str, Any] = {
            "account": account,
            "balance": round(balance, 2) if balance is not None else None,
            "repayment": repayment,
            "cadence": cadence,
            "periods_per_year": per_year or None,
            "repayment_changes": repayment_changes[-6:],
            "upcoming_change": upcoming,
            "is_offset": benefit > 0,
            "offset_benefit": round(benefit, 2),
            "interest_charged": round(charged, 2),
            "interest_gross": round(charged + benefit, 2),
            "interest_periods": int(len(interest_rows)),
            "months": months,
        }

        if rates:
            loan["rate_pct"] = round(statistics.median(rates), 2)
            loan["rate_low"] = round(min(rates), 2)
            loan["rate_high"] = round(max(rates), 2)
            # A wide spread means the rate moved, not that the maths is shaky.
            loan["rate_varied"] = round(max(rates) - min(rates), 2) > 0.5
            loan["confidence"] = "high" if len(rates) >= 6 else "low"
        else:
            loan["rate_pct"] = None
            loan["confidence"] = "none"

        if balance and repayment and loan.get("rate_pct") and cadence:
            loan["projection"] = scenarios(balance, loan["rate_pct"], repayment, cadence)

        out.append(loan)

    return out


def from_household(hh) -> dict[str, Any]:
    """Build the mortgage picture from config, saying plainly what is missing."""
    m = hh.mortgage
    balance = m.get("balance")
    rate = m.get("interest_rate_pct")
    repayment = m.get("repayment")
    freq = m.get("repayment_frequency", "fortnightly")

    missing = [
        name
        for name, val in (
            ("balance", balance),
            ("interest_rate_pct", rate),
            ("repayment", repayment),
        )
        if val in (None, "")
    ]
    if missing:
        result = _needs(*missing)
        result["balance"] = balance
        return result

    result = scenarios(float(balance), float(rate), float(repayment), freq)
    result["balance"] = float(balance)
    result["interest_rate_pct"] = float(rate)
    result["fixed_until"] = m.get("fixed_until")

    if result.get("available"):
        # Interest per week is the number that makes the cost of the loan feel
        # real, and it is what an extra repayment is actually buying back.
        result["interest_first_year"] = round(float(balance) * float(rate) / 100.0, 2)
        result["interest_per_week_now"] = round(float(balance) * float(rate) / 100.0 / 52, 2)
    return result
