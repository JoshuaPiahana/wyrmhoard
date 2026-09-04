"""
Mortgage arithmetic.

This is amortisation maths, not advice. It answers "if you keep doing X, when
does the loan end and what does the interest cost?" and nothing more. What a
household should actually do with a spare dollar depends on things this tool
cannot see, and on a sequencing question it deliberately takes a view on:

a family with three children and a fortnight of cash in the bank is not in a
position to throw money at a mortgage. A buffer comes first, because the
alternative to a buffer is not "faster mortgage payoff" - it is credit at 20%
the first time the car needs a new clutch.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

PERIODS_PER_YEAR = {
    "weekly": 52,
    "fortnightly": 26,
    "monthly": 12,
}


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
                "extra_per_month": round(
                    extra * PERIODS_PER_YEAR.get(frequency, 26) / 12, 2
                ),
                "years": s["years"],
                "payoff_date": s["payoff_date"],
                "total_interest": s["total_interest"],
                "years_saved": round(base["years"] - s["years"], 2),
                "interest_saved": round(
                    base["total_interest"] - s["total_interest"], 2
                ),
            }
        )
    return {"available": True, "base": base, "scenarios": out}


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
        base = result["base"]
        # Interest per week is the number that makes the cost of the loan feel
        # real, and it is what an extra repayment is actually buying back.
        result["interest_first_year"] = round(
            float(balance) * float(rate) / 100.0, 2
        )
        result["interest_per_week_now"] = round(
            float(balance) * float(rate) / 100.0 / 52, 2
        )
    return result
