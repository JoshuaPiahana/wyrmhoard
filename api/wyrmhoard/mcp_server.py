"""
Wyrmhoard as a tool an AI can drive.

The division of labour this project has settled on: Wyrmhoard computes,
something else interprets. A language model is far better than any dashboard
at open-ended questions - "what if I went to four days a week?" - and far
worse at summing three thousand transactions without drifting. So this exposes
exact, reproducible figures and refuses to guess; the model does the reasoning.

Three rules shape every tool below.

  Summaries by default, raw data on request.
      An agent answering "can we afford a holiday?" needs a two-kilobyte
      summary, not three thousand rows naming every shop a family visited.
      Minimisation is built into which tools exist, not left to a policy
      somebody has to remember. `list_transactions` exists, but it is the only
      one that returns raw records and its description says so.

  Every number carries its provenance.
      Units, the window it covers, how it was derived, how confident the tool
      is. An interpreting model cannot caveat what it was not told, and an
      uncaveated estimate is how somebody ends up ringing the tax office about
      money they already receive.

  What the tool cannot see is a first-class answer.
      `describe_data_gaps` is not an afterthought. This tool has already told
      a household they were missing family tax credits when those credits were
      simply arriving in an account it had not been given. Knowing the shape
      of the hole matters as much as the figures around it.

Run it over stdio, which is how local agents launch tools and keeps the
transport off the network entirely:

    docker compose run --rm -T mcp
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__, accounts, categorise, config, db
from . import coach as coach_mod
from .analysis import cashflow, entitlements, income, mortgage, recurring

server = MCPServer(
    "wyrmhoard",
    instructions=(
        "Wyrmhoard holds one household's own bank and payslip records and "
        "computes exact figures from them. It runs entirely on the user's "
        "machine and sends nothing anywhere.\n\n"
        "Start with `get_overview`. It answers most questions on its own and "
        "states how much of the data is understood.\n\n"
        "Before drawing any conclusion, call `describe_data_gaps`. This tool "
        "knows what it cannot see - accounts that were never imported, "
        "spending it could not categorise, tax rates nobody has verified - and "
        "reporting confidently over those holes has caused real harm here.\n\n"
        "Prefer the summary tools. `list_transactions` returns raw records "
        "including merchant names and should only be used when the user has "
        "asked something that genuinely needs them.\n\n"
        "This is arithmetic on a household's own records, not regulated "
        "financial advice. Entitlement figures are estimates; the tax office "
        "is authoritative."
    ),
)


def _provenance(**extra: Any) -> dict[str, Any]:
    """Every response says where it came from and how far to trust it."""
    stats = db.stats()
    coverage = categorise.coverage()
    return {
        "source": "the household's own imported records, computed locally",
        "transactions": stats["transactions"],
        "covering": {"from": stats["first_date"], "to": stats["last_date"]},
        "categorised_pct": coverage["categorised_pct"],
        "figures_trustworthy": coverage["trustworthy"],
        "wyrmhoard_version": __version__,
        **extra,
    }


# ---------------------------------------------------------------------------
# The default entry point
# ---------------------------------------------------------------------------
@server.tool()
def get_overview() -> dict[str, Any]:
    """
    The household's financial position in one call. Start here.

    Returns a typical month's income and spending, cash on hand and how many
    weeks of essentials that covers, total debt, net worth, and the direction
    of travel over recent months.

    "Typical" means the median of complete months, not the mean, so one large
    car repair does not become somebody's normal monthly spending. The current
    month is always excluded because a part-finished month shows rent paid and
    no salary yet.

    Net worth counts money only. It does not include the value of any property
    the household owns, so a household with a mortgage will show a large
    negative figure that is not the whole story.
    """
    s = cashflow.summary()
    typ = s["typical_month"]
    return {
        "typical_month": {
            "income": typ.get("income_median"),
            "spending": typ.get("spend_median"),
            "left_over": typ.get("net_median"),
            "essentials": typ.get("essentials_total"),
            "discretionary": typ.get("discretionary_total"),
            "months_used": typ.get("month_count"),
            "available": typ.get("available"),
            "note": typ.get("reason"),
        },
        "cash": {
            "total": s["cash"].get("total"),
            "weeks_of_essentials": s["cash"].get("runway_weeks"),
            "excluded_accounts": s["cash"].get("excluded_accounts"),
        },
        "debt": s["debt"],
        "net_worth": {**s["net_worth"], "excludes": "the value of any property owned"},
        "trend": s["trend"],
        "currency": config.household().currency,
        "provenance": _provenance(),
    }


@server.tool()
def describe_data_gaps() -> dict[str, Any]:
    """
    What this tool cannot see. Call before drawing conclusions.

    Reports accounts that money clearly arrives from but which were never
    imported, how much spending could not be categorised, whether any payslips
    exist, and whether the tax rate constants have been checked against the
    official source.

    This matters more than it sounds. Wyrmhoard once told a household they
    appeared to be missing family tax credits; the credits were arriving in a
    partner's account that had not been imported. Reporting confidently over a
    known hole is the most damaging thing this tool can do, so the hole is
    described explicitly rather than left to be inferred.
    """
    coverage = categorise.coverage()
    missing = accounts.likely_missing_accounts()
    rates = config.rates()
    payslips = income.from_payslips()

    gaps: list[str] = []
    for gap in missing:
        gaps.append(
            f"Account {gap['account']} is not imported, but {gap['transfers']} "
            f"transfers totalling {gap['total']:,.2f} arrived from it. Any income "
            f"or entitlements paid into it are invisible here."
        )
    if not coverage["trustworthy"]:
        gaps.append(
            f"Only {coverage['categorised_pct']}% of spending is categorised "
            f"({coverage['uncategorised_spend']:,.2f} unclassified). Category "
            f"breakdowns are indicative rather than reliable."
        )
    if not payslips.get("available"):
        gaps.append(
            "No payslips imported, so gross income is inferred from bank deposits "
            "and is approximate. Entitlement estimates are sensitive to it."
        )
    if rates.unverified_blocks:
        gaps.append(
            "Tax and entitlement rate constants have not been checked against the "
            f"official source ({', '.join(rates.unverified_blocks)}). Any figure "
            "derived from them is a rough signal only."
        )

    return {
        "has_gaps": bool(gaps),
        "gaps": gaps,
        "missing_accounts": missing,
        "coverage": coverage,
        "guidance": (
            "State these limitations when answering. Do not present a figure as "
            "settled if a gap above could change it."
            if gaps
            else "No significant gaps. Figures can be quoted with normal confidence."
        ),
    }


# ---------------------------------------------------------------------------
# Detail, still summarised
# ---------------------------------------------------------------------------
@server.tool()
def get_spending_breakdown(months: int = 6) -> dict[str, Any]:
    """
    Spending by category over recent complete months.

    Each category reports a monthly and annual figure, its share of the total,
    and which group it belongs to: essential, commitment (contractual, hard to
    change quickly), sinking (lumpy annual bills), or discretionary. The
    grouping is the useful part - it separates what a household could change
    from what it cannot.

    Args:
        months: how many recent complete months to average over.
    """
    rows = cashflow.by_category(months=months)
    leaks = cashflow.small_leaks(months=months)
    return {
        "categories": rows,
        "small_purchases": leaks,
        "window_months": months,
        "provenance": _provenance(),
    }


@server.tool()
def get_recurring_commitments() -> dict[str, Any]:
    """
    Payments that repeat - subscriptions, insurance, direct debits.

    Only counts something as recurring after three occurrences on a
    recognisable cadence at a stable amount, so groceries do not appear. Items
    not seen for two or more cycles are flagged as possibly cancelled, which
    is worth confirming either way: a stopped payment might be a subscription
    ended, or a bill quietly in arrears.
    """
    return {**recurring.summary(), "provenance": _provenance()}


@server.tool()
def get_loans() -> dict[str, Any]:
    """
    Each loan's real terms, derived from its own transactions.

    Nothing here is typed in: balance, repayment, cadence, interest rate and
    payoff projection all come from the loan account's history.

    Two subtleties worth passing on to the user. An offset loan charges
    interest on the balance minus linked accounts, so the benefit is added back
    before computing the rate - otherwise a mortgage appears to run at a
    fraction of a percent. And banks post upcoming repayment changes as
    zero-dollar transactions, which are read and reported here.
    """
    return {"loans": mortgage.infer_loans(), "provenance": _provenance()}


@server.tool()
def get_income() -> dict[str, Any]:
    """
    Gross income per job, from imported payslips.

    Uses annualised year-to-date taxable earnings, never a stated annual
    package. A stated figure can be notional - a reserve or casual role quoting
    its full-time rate - and on a total-remuneration contract it bundles in the
    employer's retirement contribution. Both inflate income in ways that are
    easy to miss, so `notes` explains whenever either applies.
    """
    return {**income.from_payslips(), "provenance": _provenance()}


@server.tool()
def get_entitlements() -> dict[str, Any]:
    """
    Government support the household may be entitled to, and what it actually
    receives.

    The observed half is certain: it is read from the bank data. The expected
    half is an ESTIMATE from locally-stored rate constants that may be a tax
    year out of date, and it is only implemented for New Zealand.

    Never present the estimate as an entitlement. If it differs from what is
    received, the useful output is "worth checking with the tax office", not a
    figure the household is owed.
    """
    return {
        "estimate": entitlements.estimate(),
        "other_support_to_check": entitlements.checklist(),
        "warning": (
            "Estimates only. The tax office is authoritative and its own "
            "calculator should be used before acting on any figure here."
        ),
    }


@server.tool()
def get_recommendations() -> dict[str, Any]:
    """
    Ranked findings and a sequenced plan.

    Findings are ordered by what they are worth against how hard they are, and
    each carries a severity and a costed amount. The plan is deliberately
    ordered: a cash buffer comes before extra debt repayment, because
    overpaying a loan with no buffer means borrowing it back at a worse rate
    the first time something breaks.

    Tone matters when relaying these. They are read by households under
    financial stress, sometimes by children. State the number, name the
    option, and do not moralise about past spending.
    """
    return {**coach_mod.summary(), "provenance": _provenance()}


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
@server.tool()
def get_progress() -> dict[str, Any]:
    """
    Snapshots taken over time, so change is measured rather than remembered.

    Each snapshot freezes the month's key figures. Comparing them is the only
    way to answer "is this working?", which is the question a household
    actually cares about.
    """
    snaps = db.snapshots()
    return {
        "snapshots": snaps,
        "count": len(snaps),
        "note": (
            "Take a snapshot after each monthly review. With fewer than two "
            "there is nothing to compare."
        ),
    }


@server.tool()
def take_snapshot(note: str | None = None) -> dict[str, Any]:
    """
    Freeze this month's figures so future progress can be measured against
    them.

    Worth doing at the end of a monthly review. Snapshots are keyed by date, so
    taking a second one on the same day replaces the first.

    Args:
        note: what changed this month, in the household's own words.
    """
    s = cashflow.summary()
    typ = s["typical_month"]
    metrics = {
        "net_median": typ.get("net_median"),
        "income_median": typ.get("income_median"),
        "spend_median": typ.get("spend_median"),
        "essentials": typ.get("essentials_total"),
        "discretionary": typ.get("discretionary_total"),
        "cash": s["cash"].get("total"),
        "runway_weeks": s["cash"].get("runway_weeks"),
        "categorised_pct": s["coverage"]["categorised_pct"],
    }
    return {"taken_on": db.save_snapshot(metrics, note=note), "metrics": metrics}


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
@server.tool()
def import_document(path: str) -> dict[str, Any]:
    """
    Import a bank CSV export or a payslip PDF from a local file path.

    The file type is worked out automatically. Re-importing something already
    imported is safe: transactions are fingerprinted, so overlapping exports
    de-duplicate themselves.

    Always relay the returned confidence. A CSV whose layout was not understood
    reports `low`, and a payslip whose figures do not add up is REJECTED rather
    than recorded - because a misread salary would quietly distort everything
    else. If confidence is low, say so instead of reporting the import as done.

    Args:
        path: absolute path to a .csv or .pdf file on this machine.
    """
    source = Path(path)
    if not source.exists():
        return {"ok": False, "error": f"No file at {path}"}

    if source.suffix.lower() == ".pdf":
        from .ingest.payslip import parse_pdf

        payslip = parse_pdf(source)
        stored = 0
        if payslip.confidence != "low" and payslip.pay_date:
            stored = db.save_payslip(
                {
                    "employer": payslip.employee_ref,
                    "source_file": source.name,
                    "pay_date": payslip.pay_date,
                    "period": payslip.period,
                    "employee_ref": payslip.employee_ref,
                    "tax_code": payslip.tax_code,
                    "confidence": payslip.confidence,
                    **payslip.values,
                }
            )
        return {
            "ok": bool(stored),
            "kind": "payslip",
            "accepted": bool(stored),
            "report": payslip.as_dict(),
        }

    from .ingest import ingest_file

    parsed = ingest_file(source)
    coverage = categorise.recategorise_all()
    return {
        "ok": parsed.confidence != "low",
        "kind": "bank_export",
        "report": parsed.as_dict(),
        "coverage": coverage,
    }


@server.tool()
def list_transactions(
    month: str | None = None, category: str | None = None, limit: int = 100
) -> dict[str, Any]:
    """
    Individual transactions. Use sparingly.

    This is the only tool returning raw records, and they name every shop,
    person and service the household paid. Prefer `get_spending_breakdown` for
    anything about totals or patterns; reach for this only when the user has
    asked something that genuinely needs individual rows, such as identifying a
    specific unrecognised payment.

    Args:
        month: restrict to one month, as YYYY-MM.
        category: restrict to one category key.
        limit: maximum rows to return.
    """
    rows = db.all_transactions()
    if month:
        rows = [r for r in rows if str(r["date"]).startswith(month)]
    if category:
        rows = [r for r in rows if r["category"] == category]
    trimmed = [
        {
            "date": r["date"],
            "description": r["memo"],
            "amount": r["amount"],
            "category": r["category"],
        }
        for r in rows[-limit:]
    ]
    return {
        "transactions": trimmed,
        "returned": len(trimmed),
        "matched": len(rows),
        "privacy_note": (
            "These rows identify where a household shops and who it pays. Use "
            "only what the question needs and do not repeat them wholesale."
        ),
    }


def main() -> None:
    """Run over stdio, which keeps the transport off the network entirely."""
    config.ensure_dirs()
    db.init()
    server.run()


if __name__ == "__main__":
    main()
