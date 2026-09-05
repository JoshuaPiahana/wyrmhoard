"""
What the household actually earns.

Before payslips, gross income was guessed by taking bank deposits and dividing
by 0.78 - a fudge that is roughly right and precisely nothing. Entitlement
abatement is assessed on gross income, so that guess propagated into every
figure on the entitlements page.

A payslip states the real number. The work here is choosing WHICH real number,
because a payslip offers several and the obvious one is a trap.

    Total Rem (TR)      90,979.00
    Taxable Gross        3,533.46   (year to date)

Both appear on the same reservist payslip. The first is what the role pays at
full time; the second is what was actually earned. Adding stated remuneration
across two jobs gave a household income of $193,000 when the truth was closer
to $105,000. Year-to-date earnings cannot lie in that direction, so they are
what gets used, and a stated annual figure is only trusted when the two agree.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .. import cache, db

# NZ tax year runs 1 April to 31 March.
TAX_YEAR_START_MONTH = 4

# A stated annual salary more than this far above annualised actual earnings
# is notional - a reserve or casual role quoting its full-time rate.
_NOTIONAL_MULTIPLE = 1.6


def tax_year_bounds(today: date | None = None) -> tuple[date, date, float]:
    """(start, today, fraction elapsed) for the current NZ tax year."""
    today = today or date.today()
    year = today.year if today.month >= TAX_YEAR_START_MONTH else today.year - 1
    start = date(year, TAX_YEAR_START_MONTH, 1)
    end = date(year + 1, TAX_YEAR_START_MONTH, 1)
    elapsed = (today - start).days / (end - start).days
    return start, today, max(elapsed, 0.01)


@cache.by_ledger
def from_payslips(today: date | None = None) -> dict[str, Any]:
    """
    Gross income per employer, from the most recent payslip of each.

    Year-to-date figures are used rather than per-period ones: they already
    smooth over overtime, unpaid weeks and mid-year rises, and a single
    fortnight is a poor basis for an annual number.
    """
    slips = db.payslips()
    if not slips:
        return {"available": False, "reason": "No payslips imported yet."}

    start, today_, elapsed = tax_year_bounds(today)

    latest: dict[str, dict[str, Any]] = {}
    for slip in slips:
        key = str(slip.get("employee_ref") or slip.get("employer") or slip.get("source_file"))
        current = latest.get(key)
        if current is None or str(slip.get("pay_date") or "") > str(current.get("pay_date") or ""):
            latest[key] = slip

    jobs: list[dict[str, Any]] = []
    for key, slip in latest.items():
        ytd = slip.get("ytd_gross") or 0.0
        annualised = round(ytd / elapsed, 2) if ytd else 0.0
        package = slip.get("annual_rem")
        base = slip.get("base_salary")
        er_super = slip.get("er_super_annual")

        # On a total-remuneration contract the employer's retirement
        # contribution is carved OUT of the package, not added to it: the
        # stated total is salary plus super. Treating that total as wages
        # overstates earnings by the super amount.
        package_includes_super = bool(
            package and base and er_super and abs((package - base) - er_super) < 1.0
        )

        notional = bool(package and annualised and package > annualised * _NOTIONAL_MULTIPLE)
        jobs.append(
            {
                "ref": key,
                "employer": slip.get("employer"),
                "pay_date": slip.get("pay_date"),
                "tax_code": slip.get("tax_code"),
                "ytd_gross": round(ytd, 2),
                "ytd_tax": round(slip.get("ytd_tax") or 0.0, 2),
                "annualised": annualised,
                "package_annual": package,
                "base_salary": base,
                "employer_super_annual": er_super,
                "package_includes_super": package_includes_super,
                "stated_is_notional": notional,
                "kiwisaver_ee": slip.get("kiwisaver_ee"),
                "kiwisaver_er": slip.get("kiwisaver_er"),
                "confidence": slip.get("confidence"),
            }
        )

    jobs.sort(key=lambda j: j["ytd_gross"], reverse=True)
    total_ytd = sum(j["ytd_gross"] for j in jobs)

    # Annualised year-to-date TAXABLE gross is the figure used, for every job.
    # It is what was actually earned and what tax and entitlements key off.
    # A stated package is not used, because it can be notional for reserve
    # work and can include employer super for total-remuneration contracts -
    # both of which inflate income in ways nobody would notice.
    gross_annual = sum(job["annualised"] for job in jobs)

    notes: list[str] = []
    for job in jobs:
        if job["package_includes_super"]:
            notes.append(
                f"{job['ref']} is on a total-remuneration contract: the stated "
                f"${job['package_annual']:,.0f} is salary of ${job['base_salary']:,.0f} plus "
                f"${job['employer_super_annual']:,.0f} of employer KiwiSaver taken out of "
                f"the package, not added to it. Taxable earnings are used here instead of "
                f"the package figure. Whether employer contributions count toward Working "
                f"for Families is a question for IRD."
            )
        if job["stated_is_notional"]:
            notes.append(
                f"{job['ref']} states an annual figure of "
                f"${job['package_annual']:,.0f} but has earned ${job['ytd_gross']:,.0f} "
                f"so far this tax year. That looks like a full-time rate for casual or "
                f"reserve work, so actual earnings were used instead."
            )
    codes = [j["tax_code"] for j in jobs if j["tax_code"]]
    if codes.count("M") > 1:
        notes.append(
            "More than one job is on the primary 'M' tax code. Only one should be, "
            "and having two usually means an end-of-year tax bill. Worth checking."
        )

    return {
        "available": True,
        "source": "payslips",
        "tax_year_start": start.isoformat(),
        "as_at": today_.isoformat(),
        "fraction_elapsed": round(elapsed, 3),
        "jobs": jobs,
        "ytd_gross": round(total_ytd, 2),
        "gross_annual": round(gross_annual, 2),
        "notes": notes,
    }
