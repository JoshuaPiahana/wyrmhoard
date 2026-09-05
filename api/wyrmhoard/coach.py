"""
The coaching layer: turning numbers into a short list of things worth doing.

Design constraints, in priority order:

  1. Ranked, not exhaustive. A family will act on three things, not thirty.
     Findings carry a severity and the report shows the top handful.

  2. Specific and costed. "Spend less on takeaways" is useless. "Takeaways are
     $312 a month, which is $3,744 a year" is a decision people can make.

  3. Never shaming. Children read this report. It states what is true and what
     would help, and it does not moralise about how the money was spent. The
     tone rule is simple: describe the number, name the option, move on.

  4. Sequenced. Advice that is right in isolation can be wrong in order. There
     is no point overpaying a mortgage while a single car repair would put the
     household on credit. The plan reflects that.

None of this is regulated financial advice. It is arithmetic on the
household's own bank data, plus prompts to go and check things with the
organisations that actually hold the answers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import cache, config
from .analysis import cashflow, entitlements, income, mortgage, recurring

# Severity drives ordering in the report.
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "win": 4}


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    body: str
    action: str | None = None
    amount: float | None = None
    unit: str = "per year"
    evidence: str | None = None
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fmt(v: float | None) -> str:
    return f"${v:,.0f}" if v is not None else "-"


@dataclass(frozen=True)
class Context:
    """
    Everything the checks read, computed once.

    Each check used to be a numbered block inside a single 590-line function,
    sharing these as locals. Passing them explicitly is what makes the blocks
    separable: a check can now be read, tested and changed on its own, and the
    order they run in is a list rather than the order somebody happened to
    write them.
    """

    hh: Any
    rt: Any
    s: dict[str, Any]
    typ: dict[str, Any]
    cash: dict[str, Any]
    leaks: dict[str, Any]
    trend: dict[str, Any]
    rec: dict[str, Any]
    ent: dict[str, Any]
    cats: list[dict[str, Any]]
    inferred_loans: list[dict[str, Any]]

    @classmethod
    def gather(cls) -> Context:
        s = cashflow.summary()
        return cls(
            hh=config.household(),
            rt=config.rates(),
            s=s,
            typ=s["typical_month"],
            cash=s["cash"],
            leaks=s["small_leaks"],
            trend=s["trend"],
            rec=recurring.summary(),
            ent=entitlements.estimate(),
            cats=s["by_category"],
            inferred_loans=mortgage.infer_loans(),
        )


def _check_monthly_margin(c: Context) -> list[Finding]:
    """Is the household going backwards? Everything else serves this."""
    typ = c.typ
    out: list[Finding] = []

    if typ.get("available"):
        net = typ["net_median"]
        if net < 0:
            out.append(
                Finding(
                    id="negative_cashflow",
                    title="More goes out than comes in",
                    severity="critical",
                    amount=abs(net) * 12,
                    body=(
                        f"In a typical month, {_fmt(typ['income_median'])} comes in and "
                        f"{_fmt(typ['spend_median'])} goes out. That is a shortfall of "
                        f"{_fmt(abs(net))} a month, or {_fmt(abs(net) * 12)} a year. "
                        "Everything else in this report is in service of closing that gap."
                    ),
                    action=(
                        "The gap has to close from one of two directions: more coming in, "
                        "or less going out. The findings below are ordered by how much "
                        "they are worth against how hard they are."
                    ),
                    evidence=f"Median of {typ['month_count']} complete months.",
                    tags=["cashflow"],
                )
            )
        elif net < typ["income_median"] * 0.05:
            out.append(
                Finding(
                    id="thin_margin",
                    title="Almost nothing left at the end of the month",
                    severity="high",
                    amount=net * 12,
                    body=(
                        f"A typical month leaves {_fmt(net)} - about "
                        f"{typ['savings_rate_pct']}% of income. It balances, but there is "
                        "no room in it. A single unexpected bill becomes debt, because "
                        "there is nothing else for it to come out of."
                    ),
                    action="Aim to free up a margin first, then give that margin a job.",
                    evidence=f"Median of {typ['month_count']} complete months.",
                    tags=["cashflow"],
                )
            )
        else:
            out.append(
                Finding(
                    id="positive_margin",
                    title=f"A typical month leaves {_fmt(net)}",
                    severity="win",
                    amount=net * 12,
                    body=(
                        f"Income {_fmt(typ['income_median'])}, spending "
                        f"{_fmt(typ['spend_median'])}. That is {_fmt(net * 12)} a year of "
                        "room, and the question is only whether it gets allocated on "
                        "purpose or absorbed by accident."
                    ),
                    action="Give every dollar of that margin a named job before the month starts.",
                    tags=["cashflow"],
                )
            )

    return out


def _check_runway(c: Context) -> list[Finding]:
    """How long the household could survive a shock."""
    cash = c.cash
    out: list[Finding] = []

    weeks = cash.get("runway_weeks")
    if weeks is not None:
        if weeks < 2:
            sev, verdict = (
                "critical",
                "That is not enough to absorb a car repair or a broken appliance.",
            )
        elif weeks < 4:
            sev, verdict = "high", "One unexpected bill would use most of it."
        elif weeks < 13:
            sev, verdict = "medium", "Enough for a surprise, not enough for a job loss."
        else:
            sev, verdict = "win", "That is a genuine cushion."
        out.append(
            Finding(
                id="runway",
                title=f"{weeks:.0f} weeks of essentials in the bank",
                severity=sev,
                amount=cash.get("total"),
                unit="on hand",
                body=(
                    f"{_fmt(cash.get('total'))} in cash against essential spending of "
                    f"{_fmt(cash.get('monthly_essentials'))} a month. {verdict}"
                ),
                action=(
                    "A starter buffer of one month of essentials - about "
                    f"{_fmt(cash.get('monthly_essentials'))} - is the single most "
                    "stabilising thing to build first, because it is what stops a bad "
                    "week turning into debt."
                    if weeks < 4
                    else "Next milestone is three months of essentials."
                ),
                evidence=f"Balance source: {cash.get('source')}.",
                tags=["safety"],
            )
        )

    return out


def _check_entitlements(c: Context) -> list[Finding]:
    """Usually the largest single lever available."""
    ent = c.ent
    out: list[Finding] = []

    if ent.get("available") and ent.get("headline"):
        gap = ent.get("gap") or 0
        sev = {"high": "critical", "medium": "high", "low": "low"}.get(
            ent.get("severity", "low"), "medium"
        )
        caveat = (
            " These figures come from rate constants that have not yet been checked "
            "against IRD for this tax year, so treat the amount as a rough signal "
            "and let IRD's own calculator give you the real number."
            if not ent.get("rates_verified")
            else ""
        )
        out.append(
            Finding(
                id="entitlements",
                title="Family entitlements are worth an hour of your time",
                severity=sev if gap > 0 else "low",
                amount=abs(gap) if gap else None,
                body=ent["headline"] + caveat,
                action=(
                    f"Log in to myIR and run IRD's own estimator: {ent['calculator_url']}. "
                    "Check that the income, the number of children and their birth dates "
                    "IRD holds are all current. This is the highest-value hour available "
                    "to you, because it is money you are already entitled to."
                ),
                evidence=(
                    f"Bank data shows {_fmt(ent['observed']['ird_annualised'])} a year "
                    f"from IRD across {ent['observed']['ird_payments']} payments."
                    if ent.get("observed", {}).get("available")
                    else None
                ),
                tags=["income", "entitlements"],
            )
        )

    return out


def _check_subscriptions(c: Context) -> list[Finding]:
    """Small individually, which is exactly why they survive."""
    rec = c.rec
    out: list[Finding] = []

    if rec["subscriptions_count"]:
        out.append(
            Finding(
                id="subscriptions",
                title=f"{rec['subscriptions_count']} subscriptions, {_fmt(rec['subscriptions_annual'])} a year",
                severity="medium" if rec["subscriptions_annual"] > 600 else "low",
                amount=rec["subscriptions_annual"],
                body=(
                    f"Recurring subscriptions total {_fmt(rec['subscriptions_monthly'])} a "
                    f"month. Individually each one is small, which is exactly why they "
                    "survive; together they are a meaningful line in the budget."
                ),
                action=(
                    "Read the list together and keep the ones the family would miss. "
                    "Cancelling even half of these redirects real money."
                ),
                tags=["spending", "quick-win"],
            )
        )

    stale = [i for i in rec["items"] if i["possibly_cancelled"]]
    if stale:
        out.append(
            Finding(
                id="stale_recurring",
                title=f"{len(stale)} regular payments have stopped appearing",
                severity="low",
                amount=sum(i["annual_cost"] for i in stale),
                body=(
                    "These looked like regular commitments but have not been charged for "
                    "a while. Either they were cancelled - in which case the budget should "
                    "stop reserving for them - or a payment has failed and something is "
                    "quietly in arrears."
                ),
                action="Confirm which of these are genuinely finished.",
                tags=["housekeeping"],
            )
        )

    return out


def _check_small_spending(c: Context) -> list[Finding]:
    """The death-by-a-thousand-cuts number budgets miss."""
    leaks = c.leaks
    out: list[Finding] = []

    if leaks.get("available") and leaks["per_year"] > 500:
        out.append(
            Finding(
                id="small_spending",
                title=f"Small purchases add up to {_fmt(leaks['per_year'])} a year",
                severity="medium",
                amount=leaks["per_year"],
                body=(
                    f"{leaks['count']} purchases under ${leaks['threshold']:.0f}, averaging "
                    f"${leaks['average_size']:.2f} each. That is {_fmt(leaks['per_month'])} a "
                    f"month, or about {_fmt(leaks['per_week'])} a week. No single one of "
                    "these felt like a decision, which is the whole difficulty with them."
                ),
                action=(
                    "This is not about never buying coffee. It is about knowing the number, "
                    "because a budget that ignores it will never balance."
                ),
                tags=["spending"],
            )
        )

    return out


def _check_top_discretionary(c: Context) -> list[Finding]:
    """The biggest categories the household actually chooses."""
    cats = c.cats
    out: list[Finding] = []

    disc = [c for c in cats if c["group"] == "discretionary"][:3]
    if disc:
        total = sum(c["per_year"] for c in disc)
        names = ", ".join(f"{c['label']} ({_fmt(c['per_year'])})" for c in disc)
        out.append(
            Finding(
                id="top_discretionary",
                title=f"Top three choosable categories: {_fmt(total)} a year",
                severity="medium",
                amount=total,
                body=(
                    f"{names}. These are the categories where the household has the most "
                    "direct control, so they are where a decision changes the number "
                    "fastest."
                ),
                action=(
                    "Pick one - not all three - and set a weekly number for it. One "
                    "category actually changed beats three categories half-tracked."
                ),
                tags=["spending"],
            )
        )

    return out


def _check_sensitive_categories(c: Context) -> list[Finding]:
    """Categories worth an honest conversation."""
    cats = c.cats
    out: list[Finding] = []

    for cat in cats:
        if cat.get("flagged") and cat["per_year"] > 200:
            out.append(
                Finding(
                    id=f"flagged_{cat['category']}",
                    title=f"{cat['label']}: {_fmt(cat['per_year'])} a year",
                    severity="medium",
                    amount=cat["per_year"],
                    body=(
                        f"{cat['transactions']} transactions over the period, averaging "
                        f"{_fmt(cat['per_month'])} a month. Recorded here because it is a "
                        "real line in the budget, not to make a point about it."
                    ),
                    action="Worth deciding on deliberately rather than by default.",
                    tags=["spending", "sensitive"],
                )
            )

    return out


def _check_lumpy_bills(c: Context) -> list[Finding]:
    """Annual bills that ambush a household without sinking funds."""
    cats = c.cats
    out: list[Finding] = []

    sinking = [c for c in cats if c["group"] == "sinking"]
    annual_lumpy = sum(c["per_year"] for c in sinking)
    if annual_lumpy > 0:
        out.append(
            Finding(
                id="sinking_funds",
                title=f"Lumpy annual bills need {_fmt(annual_lumpy / 12)} a month set aside",
                severity="high",
                amount=annual_lumpy,
                body=(
                    f"Rates, insurance, registration, school costs and Christmas come to "
                    f"about {_fmt(annual_lumpy)} a year. They do not arrive evenly, so "
                    "they feel like emergencies even though every one of them is entirely "
                    "predictable."
                ),
                action=(
                    f"Open a separate account and move {_fmt(annual_lumpy / 12)} into it "
                    "on payday. When the rates bill lands, the money is already there and "
                    "it stops being a crisis."
                ),
                tags=["structure", "quick-win"],
            )
        )

    return out


def _check_bank_fees(c: Context) -> list[Finding]:
    """Money paid for nothing."""
    cats = c.cats
    out: list[Finding] = []

    fees = next((c for c in cats if c["category"] == "bank_fees"), None)
    if fees and fees["per_year"] > 60:
        out.append(
            Finding(
                id="bank_fees",
                title=f"Bank fees are costing {_fmt(fees['per_year'])} a year",
                severity="low",
                amount=fees["per_year"],
                body=(
                    "Account fees, and possibly dishonour or overdraft fees. Fees for "
                    "being short of money are the most expensive kind, because they "
                    "arrive exactly when there is least to pay them with."
                ),
                action=(
                    "Ask Kiwibank what fee-free options exist for your account type, and "
                    "whether any of these can be reversed."
                ),
                tags=["quick-win"],
            )
        )

    return out


def _check_kiwisaver(c: Context) -> list[Finding]:
    """Employer matching is the highest-return money available."""
    hh, rt, cats = c.hh, c.rt, c.cats
    out: list[Finding] = []

    # Payslips are authoritative here: they state the actual contribution, so
    # a household with one imported is never asked whether they contribute.
    ks = next((c for c in cats if c["category"] == "kiwisaver"), None)
    payslip_income = income.from_payslips()
    contributing = any(
        (job.get("kiwisaver_ee") or 0) != 0 for job in payslip_income.get("jobs", [])
    ) or any((e.get("kiwisaver_employee_pct") or 0) > 0 for e in hh.earners)
    if hh.region_supported and not contributing and not ks:
        ks_block = rt.block("kiwisaver")
        out.append(
            Finding(
                id="kiwisaver",
                title="KiwiSaver contributions are not visible",
                severity="medium",
                body=(
                    "No KiwiSaver deduction shows in the configuration or the bank data. "
                    "Contributing enough to get the full employer match and government "
                    "contribution is normally worth more than any other guaranteed return "
                    "available to a household. "
                    + (
                        "The exact government contribution figure in this tool has not "
                        "been verified for the current year - check the IRD page before "
                        "relying on the number."
                        if not rt.is_verified("kiwisaver")
                        else ""
                    )
                ),
                action=(
                    "Check your payslip for a KiwiSaver line. If contributions are on hold, "
                    "weigh restarting them against the monthly gap above - if the household "
                    "is going backwards each month, stabilising cash flow comes first. "
                    f"Details: {ks_block.get('source', 'https://www.ird.govt.nz/kiwisaver')}"
                ),
                tags=["future"],
            )
        )

    return out


def _check_loans(c: Context) -> list[Finding]:
    """Loan terms derived from the loan's own transactions."""
    inferred_loans = c.inferred_loans
    out: list[Finding] = []

    # Preferred over the household.yml figures below: these are derived from
    # the loan's own transactions, so they cannot go stale.
    for loan in inferred_loans:
        if not loan.get("balance"):
            continue
        acct = str(loan["account"])[-2:]

        if loan.get("upcoming_change"):
            change = loan["upcoming_change"]
            direction = "down" if change["to"] < change["from"] else "up"
            out.append(
                Finding(
                    id=f"repayment_change_{acct}",
                    title=f"Loan repayment changes on {change['due']}",
                    severity="high" if direction == "up" else "medium",
                    amount=abs(change["to"] - change["from"])
                    * (loan.get("periods_per_year") or 26),
                    body=(
                        f"Your bank has posted a notice: the repayment moves {direction} "
                        f"from {_fmt(change['from'])} to {_fmt(change['to'])} on "
                        f"{change['due']}. That is "
                        f"{_fmt(abs(change['to'] - change['from']))} per payment, about "
                        f"{_fmt(abs(change['to'] - change['from']) * (loan.get('periods_per_year') or 26))} "
                        "a year."
                    ),
                    action=(
                        "Check the payment date lands after payday, and adjust any "
                        "automatic transfer that funds it."
                        if direction == "up"
                        else "If the repayment is dropping, the difference is only saved "
                        "if something claims it. Decide now where it goes."
                    ),
                    evidence="Read from the bank's own notice in the loan account.",
                    tags=["debt"],
                )
            )

        if loan.get("is_offset") and loan.get("offset_benefit"):
            out.append(
                Finding(
                    id=f"offset_benefit_{acct}",
                    title=f"Your offset arrangement saved {_fmt(loan['offset_benefit'])}",
                    severity="win",
                    amount=loan["offset_benefit"],
                    body=(
                        f"Over the last {loan['months']} months the balances in your "
                        "everyday and savings accounts reduced the interest charged on "
                        f"this loan by {_fmt(loan['offset_benefit'])}. Money sitting in "
                        "those accounts is quietly working, which is worth knowing when "
                        "deciding where to keep a buffer."
                    ),
                    tags=["strength", "debt"],
                )
            )

        if loan.get("rate_pct"):
            projection = loan.get("projection") or {}
            overpay = ""
            if projection.get("available"):
                options: list[dict[str, Any]] = projection["scenarios"]
                best = max(options, key=lambda s: s["interest_saved"], default=None)
                if best is not None and best["extra_per_period"]:
                    overpay = (
                        f" Paying an extra {_fmt(best['extra_per_period'])} per repayment "
                        f"would clear it {best['years_saved']:.1f} years sooner and save "
                        f"{_fmt(best['interest_saved'])}."
                    )
            varied = (
                " The rate moved during this period, so treat it as a recent average "
                "rather than a fixed figure."
                if loan.get("rate_varied")
                else ""
            )
            weekly = loan["balance"] * loan["rate_pct"] / 100 / 52
            out.append(
                Finding(
                    id=f"loan_{acct}",
                    title=(
                        f"Loan ...{acct}: {_fmt(loan['balance'])} at " f"about {loan['rate_pct']}%"
                    ),
                    severity="low",
                    amount=loan["interest_gross"],
                    body=(
                        f"Worked out from the account itself, not typed in: "
                        f"{_fmt(loan['balance'])} owing, repayments of "
                        f"{_fmt(loan.get('repayment'))} {loan.get('cadence') or ''}, and "
                        f"{_fmt(loan['interest_gross'])} of interest over the last "
                        f"{loan['months']} months. That is about {_fmt(weekly)} a week "
                        "in interest alone." + varied + overpay
                    ),
                    action=(
                        "Worth acting on only after the buffer and sinking funds exist. "
                        "Overpaying a loan while there is no cash for a car repair usually "
                        "ends with borrowing it back at a worse rate."
                    ),
                    evidence=(
                        f"Derived from {loan['interest_periods']} interest charges "
                        f"({loan.get('confidence')} confidence)."
                    ),
                    tags=["debt"],
                )
            )

    return out


def _check_mortgage(c: Context) -> list[Finding]:
    """For households with a mortgage the accounts did not reveal."""
    hh, inferred_loans = c.hh, c.inferred_loans
    out: list[Finding] = []

    # Renters and mortgage-free owners get no findings here at all. Nagging
    # somebody about a loan they do not have is the fastest way to make a tool
    # feel like it was written for somebody else.
    # Skipped entirely when the loan accounts told us the terms themselves -
    # asking somebody to type in a rate the tool already worked out is noise.
    # Named `declared` rather than `loan`: the loop above already binds `loan`
    # to an inferred loan, and reusing the name for a config-derived one made
    # the two indistinguishable at a glance.
    declared: dict[str, Any] = {} if inferred_loans else mortgage.from_household(hh)
    if hh.has_mortgage and declared.get("available"):
        base = declared["base"]
        # Typed local: with a bare Any iterable, mypy resolves max()'s
        # default=None overload in a way that types the lambda parameter as
        # optional too.
        declared_options: list[dict[str, Any]] = declared["scenarios"]
        declared_best = max(declared_options, key=lambda s: s["interest_saved"], default=None)
        declared_overpay = ""
        if declared_best is not None and declared_best["extra_per_period"]:
            declared_overpay = (
                f" Paying an extra {_fmt(declared_best['extra_per_period'])} per repayment "
                f"would clear it {declared_best['years_saved']:.1f} years sooner and save "
                f"{_fmt(declared_best['interest_saved'])}."
            )
        out.append(
            Finding(
                id="mortgage",
                title=f"Mortgage clears {base['payoff_date'][:4]} on current payments",
                severity="low",
                amount=base["total_interest"],
                body=(
                    f"{_fmt(declared['balance'])} at {declared['interest_rate_pct']}%. On "
                    f"the current repayment that is {base['years']:.1f} more years and "
                    f"{_fmt(base['total_interest'])} of interest. Right now the loan costs "
                    f"about {_fmt(declared.get('interest_per_week_now'))} a week in "
                    "interest alone." + declared_overpay
                ),
                action=(
                    "Worth doing only after the buffer and sinking funds exist. Overpaying "
                    "a mortgage while there is no cash for a car repair usually ends with "
                    "borrowing the money back at a much higher rate."
                ),
                evidence=(
                    f"Fixed until {declared['fixed_until']}."
                    if declared.get("fixed_until")
                    else "Set `fixed_until` in household.yml to get a refix reminder."
                ),
                tags=["debt"],
            )
        )
    elif hh.has_mortgage and declared.get("missing"):
        out.append(
            Finding(
                id="mortgage_missing",
                title="Mortgage details not filled in yet",
                severity="low",
                body=(
                    "The interest rate and repayment amount are not in household.yml, so "
                    "the payoff maths cannot run. They are on your Kiwibank loan summary "
                    "and take two minutes to copy across."
                ),
                action=f"Fill in: {', '.join(declared['missing'])} in config/household.yml.",
                tags=["setup"],
            )
        )

    return out


def _check_trend(c: Context) -> list[Finding]:
    """Direction of travel over recent months."""
    trend = c.trend
    out: list[Finding] = []

    if trend.get("available"):
        direction = trend["direction"]
        if direction == "improving":
            out.append(
                Finding(
                    id="trend",
                    title=f"The last three months are {_fmt(trend['net_change'])} a month better",
                    severity="win",
                    amount=trend["net_change"] * 12,
                    body=(
                        f"Recent months average {_fmt(trend['net_now'])} left over against "
                        f"{_fmt(trend['net_before'])} in the three months before. Whatever "
                        "changed, it is working."
                    ),
                    tags=["progress"],
                )
            )
        elif direction == "worsening":
            # Name the side that actually moved. Quoting a spending figure
            # while income was the real cause sends the family after the wrong
            # problem, and costs them the one meeting they were going to hold.
            if trend["driver"] == "income":
                driver_line = (
                    f"Income moved from {_fmt(trend['income_before'])} to "
                    f"{_fmt(trend['income_now'])} a month, which is the bigger change - "
                    f"spending went from {_fmt(trend['spend_before'])} to "
                    f"{_fmt(trend['spend_now'])}."
                )
                action = (
                    "The change here is on the income side, so start there: check "
                    "whether hours, a payment or an entitlement changed before "
                    "looking at spending."
                )
            else:
                driver_line = (
                    f"Spending moved from {_fmt(trend['spend_before'])} to "
                    f"{_fmt(trend['spend_now'])} a month, which is the bigger change - "
                    f"income went from {_fmt(trend['income_before'])} to "
                    f"{_fmt(trend['income_now'])}."
                )
                action = "Worth finding what changed in spending before deciding anything else."

            out.append(
                Finding(
                    id="trend",
                    title=f"The last three months are {_fmt(abs(trend['net_change']))} a month worse",
                    severity="high",
                    amount=abs(trend["net_change"]) * 12,
                    body=(
                        f"Recent months average {_fmt(trend['net_now'])} left over against "
                        f"{_fmt(trend['net_before'])} before. {driver_line}"
                    ),
                    action=action,
                    tags=["progress"],
                )
            )

    return out


def _check_wins(c: Context) -> list[Finding]:
    """What is already going right, named explicitly."""
    cats = c.cats
    out: list[Finding] = []

    has_consumer_debt = any(c["category"] in {"bnpl", "credit_card", "personal_loan"} for c in cats)
    if not has_consumer_debt:
        out.append(
            Finding(
                id="no_consumer_debt",
                title="No credit cards, Afterpay or personal loans",
                severity="win",
                body=(
                    "Nothing in the data looks like consumer debt. That is genuinely "
                    "unusual for a household under this much pressure, and it means every "
                    "dollar freed up goes to work immediately instead of servicing "
                    "interest first. It is the strongest thing in this whole picture."
                ),
                tags=["strength"],
            )
        )

    return out


# The order the checks run in. It decides nothing the reader sees: the ranking
# below sorts every finding by severity and then by size, so this list only
# groups related checks together for whoever is reading the file.
CHECKS = (
    _check_monthly_margin,
    _check_runway,
    _check_entitlements,
    _check_subscriptions,
    _check_small_spending,
    _check_top_discretionary,
    _check_sensitive_categories,
    _check_lumpy_bills,
    _check_bank_fees,
    _check_kiwisaver,
    _check_loans,
    _check_mortgage,
    _check_trend,
    _check_wins,
)


def build_findings() -> list[Finding]:
    """
    Every check, run against one shared snapshot of the numbers.

    Ranked most severe first, and within a severity by how much money is at
    stake - a family will act on three things, not thirty, so the order is
    what decides which three they read.
    """
    c = Context.gather()
    findings = [finding for check in CHECKS for finding in check(c)]
    findings.sort(key=lambda f: (SEVERITY_RANK.get(f.severity, 9), -(f.amount or 0)))
    return findings


def build_plan() -> list[dict[str, Any]]:
    """
    A sequenced plan. Order matters more than content.

    Each step names what "done" looks like, so progress is checkable rather
    than a feeling.
    """
    hh = config.household()
    s = cashflow.summary()
    typ = s["typical_month"]
    cash = s["cash"]
    essentials = typ.get("essentials_total") if typ.get("available") else None
    on_hand = cash.get("total")
    cats = s["by_category"]
    lumpy = sum(c["per_year"] for c in cats if c["group"] == "sinking")

    steps: list[dict[str, Any]] = []

    steps.append(
        {
            "order": 1,
            "title": "Know the number",
            "why": "You cannot steer what you cannot see. This is already done - "
            "the rest of the plan depends on keeping it current.",
            "done_when": "Bank exports imported and over 90% categorised, refreshed monthly.",
            "status": "done" if s["coverage"]["trustworthy"] else "in progress",
        }
    )

    if hh.region_supported:
        steps.append(
            {
                "order": 2,
                "title": "Claim what you are already entitled to",
                "why": "It is the only step that adds income without adding hours, and "
                "it is usually the largest single number available.",
                "done_when": "IRD's calculator run, myIR details confirmed current, and "
                "the entitlement checklist worked through.",
                "status": "todo",
            }
        )

    target = essentials if essentials else 2000
    steps.append(
        {
            "order": 3,
            "title": f"Build a starter buffer of {_fmt(target)}",
            "why": "One month of essentials in a separate account. This is what stops a "
            "car repair becoming a debt, and it is why it comes before everything else.",
            "done_when": f"{_fmt(target)} sitting in an account you do not carry a card for.",
            "status": "done" if on_hand and target and on_hand >= target else "in progress",
            "progress_pct": round(min(100, 100 * on_hand / target), 1) if on_hand and target else 0,
        }
    )

    if lumpy:
        steps.append(
            {
                "order": 4,
                "title": f"Automate {_fmt(lumpy / 12)} a month for the lumpy bills",
                "why": "Rates, insurance, rego, school and Christmas are predictable. "
                "Saving for them monthly turns five annual emergencies into nothing at all.",
                "done_when": "An automatic payment into a separate account on every payday.",
                "status": "todo",
            }
        )

    steps.append(
        {
            "order": 5,
            "title": "Pick one category and give it a weekly number",
            "why": "One category actually changed beats a whole budget half-followed. "
            "Choose the one the family minds least.",
            "done_when": "Four consecutive weeks inside the number.",
            "status": "todo",
        }
    )

    steps.append(
        {
            "order": 6,
            "title": f"Grow the buffer to three months ({_fmt(essentials * 3) if essentials else 'TBC'})",
            "why": "Three months of essentials covers a job loss or an illness, which is "
            "the risk a single-income household carries most.",
            "done_when": "Three months of essential spending held in savings.",
            "status": "todo",
        }
    )

    if hh.has_mortgage:
        steps.append(
            {
                "order": 7,
                "title": "Then, and only then, attack the mortgage",
                "why": "Every extra dollar shortens the loan and saves interest - but "
                "only once the buffer exists. Overpaying without a buffer means "
                "borrowing it back later at a worse rate.",
                "done_when": "An extra amount set on the loan, reviewed at each refix.",
                "status": "todo",
            }
        )
    else:
        steps.append(
            {
                "order": 7,
                "title": "Choose what the surplus is for",
                "why": "With no mortgage to shorten, a stable surplus needs a named "
                "destination or it quietly becomes spending. What that destination "
                "should be is a decision for your household - this tool deliberately "
                "does not make investment recommendations.",
                "done_when": "The monthly surplus has a name and an automatic payment.",
                "status": "todo",
            }
        )

    # Renumber so a skipped step never leaves a visible gap.
    for i, step in enumerate(steps, start=1):
        step["order"] = i
    return steps


@cache.by_ledger
def summary() -> dict[str, Any]:
    findings = build_findings()
    rt = config.rates()
    return {
        "findings": [f.as_dict() for f in findings],
        "plan": build_plan(),
        "counts": {
            sev: sum(1 for f in findings if f.severity == sev)
            for sev in ("critical", "high", "medium", "low", "win")
        },
        "rates_unverified": rt.unverified_blocks,
        "disclaimer": (
            "This is arithmetic on your own bank data, not regulated financial "
            "advice. Entitlement figures are estimates - IRD and Work and Income "
            "are the authority on what you are actually entitled to."
        ),
    }
