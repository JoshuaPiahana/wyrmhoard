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
      somebody has to remember. Two tools return raw records -
      `list_transactions` and `get_receipt` - and each says so in its own
      description, because a model gets that description and nothing else.

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

One tool writes a judgement down. Everything else here computes;
`teach_category` takes a decision only a person or a model can make - what
"SP QUAYSIDE 4829" is - and records it as a rule the household can read and
edit. That is the division of labour made concrete. Interpret it once, and
Wyrmhoard applies that interpretation identically every month afterwards
rather than having it guessed at afresh each time somebody asks.

Run it over stdio, which is how local agents launch tools and keeps the
transport off the network entirely:

    docker compose run --rm -T mcp
"""

from __future__ import annotations

from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import (
    __version__,
    accounts,
    categorise,
    config,
    db,
    documents,
    facts,
    figures,
    properties,
    taxonomy,
)
from .analysis import cashflow, income, mortgage, recurring, series

server = MCPServer(
    "wyrmhoard",
    instructions=(
        "Wyrmhoard holds one household's own bank records, payslips and "
        "itemised receipts, and computes exact figures from them. It runs "
        "entirely on the user's machine and sends nothing anywhere.\n\n"
        "Start with `get_overview`. It answers most questions on its own and "
        "states how much of the data is understood.\n\n"
        "Before drawing any conclusion, call `describe_data_gaps`. This tool "
        "knows what it cannot see - accounts that were never imported, "
        "spending it could not categorise, facts nobody has answered - and "
        "reporting confidently over those holes has caused real harm here.\n\n"
        "Prefer the summary tools. `list_transactions` returns raw records "
        "including merchant names and should only be used when the user has "
        "asked something that genuinely needs them. `get_receipt` returns the "
        "lines of one receipt, product by product, and the same restraint "
        "applies: a transaction says which shop, a receipt says which "
        "medication.\n\n"
        "When coverage is low, there is something you can do about it. "
        "`get_uncategorised` groups the unrecognised spending by merchant, and "
        "`teach_category` records what you work out as a durable rule. A "
        "public ruleset can name supermarkets and power companies but never "
        "someone's local takeaway; you can ask, and the answer then holds "
        "every month instead of being guessed at again.\n\n"
        "This is arithmetic on a household's own records, not regulated "
        "financial advice. It holds no tax or benefit rules for any country: "
        "if the user asks what they are entitled to, say that this tool cannot "
        "know, and point them at their own tax office."
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


def _described(payload: dict[str, Any], **provenance_extra: Any) -> dict[str, Any]:
    """
    A response that says what its numbers mean.

    Wraps the figures with their units and their provenance. Every read tool
    returns through here, so a consumer never has to infer a currency from a
    field name - which is the one thing an Australian tax pack, a coaching
    script and an agent holding this next to a fitness tracker all depend on.
    """
    return {
        **payload,
        "units": figures.units(payload, config.household().currency),
        "provenance": _provenance(**provenance_extra),
    }


# ---------------------------------------------------------------------------
# The default entry point
# ---------------------------------------------------------------------------
@server.tool()
def get_overview() -> dict[str, Any]:
    """
    The household's financial position in one call. Start here.

    Returns a typical month's income and spending broken down by the
    household's own spending groups, cash on hand, total debt, net worth, and
    the direction of travel over recent months.

    `taxonomy` says what each group in `by_group` means and whether it is money
    in or money out. Read it before adding two groups together: this household
    decided what belongs in each one, and how many weeks of cover they have
    depends entirely on which of those groups you think they could stop
    paying. That judgement is yours to make and to state, not this tool's.

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
    return _described(
        {
            "typical_month": {
                "income": typ.get("income_median"),
                "spending": typ.get("spend_median"),
                "left_over": typ.get("net_median"),
                "by_group": typ.get("by_group"),
                "months_used": typ.get("month_count"),
                "available": typ.get("available"),
                "note": typ.get("reason"),
            },
            "cash": {
                "total": s["cash"].get("total"),
                "excluded_accounts": s["cash"].get("excluded_accounts"),
                "as_at": s["cash"].get("as_at"),
            },
            "taxonomy": taxonomy.served(),
            "debt": s["debt"],
            "net_worth": {**s["net_worth"], "excludes": "the value of any property owned"},
            "trend": s["trend"],
        }
    )


@server.tool()
def describe_data_gaps() -> dict[str, Any]:
    """
    What this tool cannot see. Call before drawing conclusions.

    Reports accounts that money clearly arrives from but which were never
    imported, how much spending could not be categorised, whether any payslips
    exist, and how many purchases have an itemised receipt behind them.

    This matters more than it sounds. Wyrmhoard once told a household they
    appeared to be missing family tax credits; the credits were arriving in a
    partner's account that had not been imported. Reporting confidently over a
    known hole is the most damaging thing this tool can do, so the hole is
    described explicitly rather than left to be inferred.
    """
    coverage = categorise.coverage()
    missing = accounts.likely_missing_accounts()
    payslips = income.from_payslips()

    gaps: list[str] = []
    for gap in missing:
        gaps.append(
            f"Account {gap['account']} is not imported, but {gap['transfers']} "
            f"transfers totalling {gap['total']:,.2f} arrived from it. Any income "
            f"paid into it is invisible here."
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
            "and is approximate."
        )

    # What a purchase consisted of is invisible unless a receipt was submitted
    # for it. Said either way: an agent once had every line of a receipt in
    # this database and no way to learn that, so it reported a hole that was
    # not there - the one failure this tool exists to prevent.
    receipts = documents.summary()
    held = receipts["count"]
    linked = held - receipts["unlinked"]
    if held:
        gaps.append(
            f"{held} itemised receipt(s) are held, linked to {linked} transaction(s). "
            "What was bought is knowable for those purchases only - `get_receipt` "
            "reads the lines - and for no other."
        )
    else:
        gaps.append(
            "No itemised receipts are held, so nothing here can say what any "
            "purchase consisted of, only where it was made. A producer can submit "
            "receipts - see producers/."
        )

    # Facts about the people, which no export can supply. Listed as questions
    # rather than gaps because an agent can simply ask them, and one answer
    # here is often worth more than any amount of further analysis.
    unknown_facts = facts.unknown()
    for item in unknown_facts:
        gaps.append(
            f"Unknown: {item['question']} Until this is answered the tool "
            "treats it as unestablished rather than assuming an answer."
        )

    return _described(
        {
            "has_gaps": bool(gaps),
            "gaps": gaps,
            "missing_accounts": missing,
            "coverage": coverage,
            "receipts": {"held_count": held, "linked_count": linked},
            "household_facts": facts.all_facts(),
            "questions_for_the_household": unknown_facts,
            "guidance": (
                "State these limitations when answering. Do not present a figure as "
                "settled if a gap above could change it."
                if gaps
                else "No significant gaps. Figures can be quoted with normal confidence."
            ),
        }
    )


# ---------------------------------------------------------------------------
# Detail, still summarised
# ---------------------------------------------------------------------------
@server.tool()
def get_spending_breakdown(months: int = 6) -> dict[str, Any]:
    """
    Spending by category over recent complete months.

    Each category reports a monthly and annual figure, its share of the total,
    and which group the household filed it under. `taxonomy` says what those
    group names mean here - do not assume the household uses the same words or
    the same divisions as anybody else.

    For a series over time rather than an average, use
    `get_spending_over_time`, which is the one to reach for whenever the
    question is about a trend, a comparison between two periods, or anything
    the household wants plotted.

    Args:
        months: how many recent complete months to average over.
    """
    rows = cashflow.by_category(months=months)
    leaks = cashflow.small_leaks(months=months)
    return _described(
        {
            "categories": rows,
            "small_purchases": leaks,
            "window_months": months,
            "taxonomy": taxonomy.served(),
        }
    )


@server.tool()
def get_spending_over_time(
    from_date: str | None = None,
    to_date: str | None = None,
    period: str = "fortnight",
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """
    Spending per category, per period, across a date range.

    This is the tool for any question about change - "is our fuel spending
    going up", "what did groceries cost each fortnight last year", "compare
    this winter to last". Everything else here averages a recent window and
    cannot answer those.

    Periods are `week`, `fortnight` or `month`. Prefer `fortnight` for a
    household paid fortnightly: calendar months hold two pay days sometimes and
    three others, so a monthly series shows swings that are the calendar
    moving rather than the household. `anchor_source` in the reply says what
    the fortnights are lined up to, and whether that alignment means anything -
    read it before drawing conclusions about a cycle.

    `totals` lines up with `periods` by position. Periods the ledger cannot
    answer for - one still in progress, or before the imported data starts -
    are returned with `complete: false` rather than dropped. A zero in a
    complete period is a real observation; a zero in an incomplete one is not.
    `per_period` already excludes incomplete periods.

    Args:
        from_date: ISO date to start at. Defaults to the start of the ledger.
        to_date: ISO date to end at. Defaults to the end of the ledger.
        period: week | fortnight | month.
        categories: category keys to include. Defaults to all of them, which on
            a long range is a lot of numbers - name the few you care about.
    """
    try:
        payload = series.spending(from_=from_date, to=to_date, period=period, categories=categories)
    except ValueError as exc:
        return {"error": str(exc), "allowed_periods": list(series.PERIODS)}
    return _described(payload)


@server.tool()
def get_income_over_time(
    from_date: str | None = None,
    to_date: str | None = None,
    period: str = "fortnight",
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """
    Money received per category, per period, across a date range.

    The same shape as `get_spending_over_time` with the sign flipped. Use it
    for "how much did we actually get from X", where X is any income category
    the household's rules define - wages, a government payment, gifts, a
    refund. `get_taxonomy` lists them.

    Transfers between the household's own accounts are excluded, so money
    shuffled between two of their pots is not counted as income.

    This tool holds no rules about any country's benefits or tax credits, and
    cannot tell anyone what they are entitled to. What it can do is state
    exactly what arrived and when, which is the certain half of that question -
    and a household seeing nothing at all arrive from an agency they expected
    to hear from is a finding worth raising, carefully. The tax office is the
    only authority on the other half.

    Read `describe_data_gaps` first. Money arriving in an account that was
    never imported is invisible here, and reporting "nothing received" over
    that hole has misled a household before.

    Args:
        from_date: ISO date to start at. Defaults to the start of the ledger.
        to_date: ISO date to end at. Defaults to the end of the ledger.
        period: week | fortnight | month.
        categories: category keys to include. Defaults to all of them.
    """
    try:
        payload = series.received(from_=from_date, to=to_date, period=period, categories=categories)
    except ValueError as exc:
        return {"error": str(exc), "allowed_periods": list(series.PERIODS)}
    return _described(payload)


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
    return _described(recurring.summary())


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
    return _described({"loans": mortgage.infer_loans()})


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
    return _described(income.from_payslips())


# ---------------------------------------------------------------------------
# Categorisation
# ---------------------------------------------------------------------------
@server.tool()
def get_uncategorised(limit: int = 50) -> dict[str, Any]:
    """
    Spending no rule recognised, grouped by merchant, biggest first.

    This is the work an agent is genuinely better at than the tool. Wyrmhoard
    can normalise "POS W/D SP QUAYSIDE 4829" into a stable merchant string and
    count what it cost; it cannot know what that shop is. You often can, or
    can ask the household in one question.

    Groups rather than rows, deliberately. Thirty visits to the same takeaway
    are one decision, not thirty, and the group carries the count and the
    total so the expensive unknowns are obvious.

    Money going out only. An unrecognised deposit will not appear here;
    `describe_data_gaps` is where unexplained income shows up.

    Pair with `teach_category`, using a key from `valid_categories`.

    Args:
        limit: how many merchant groups to return.
    """
    groups = categorise.top_uncategorised(limit=limit)
    cover = categorise.coverage()
    return _described(
        {
            "groups": [
                {
                    "merchant": g["memo"],
                    "example_memo": g["example"],
                    "count": g["count"],
                    "total": g["total"],
                }
                for g in groups
            ],
            "returned": len(groups),
            "uncategorised_spend": cover["uncategorised_spend"],
            "uncategorised_count": cover["uncategorised_count"],
            "valid_categories": config.declared_categories(),
            "next_step": (
                "Identify each merchant, then call `teach_category` with a distinctive "
                "fragment of its name and one of `valid_categories`. Ask the household "
                "about anything you cannot place - a wrong guess becomes a rule."
            ),
            "privacy_note": (
                "These name where the household shops. They are here so unknown "
                "merchants can be identified; use them for that and do not repeat them "
                "wholesale."
            ),
        }
    )


@server.tool()
def teach_category(match: str, category: str) -> dict[str, Any]:
    """
    Teach the tool a merchant. This writes a rule to disk.

    The rule goes into config/learned.yml on the household's machine, which is
    merged over the public ruleset on every categorisation run. That is the
    point: an answer worked out once is then applied the same way every month,
    rather than being re-judged - and possibly judged differently - each time
    somebody looks at the ledger.

    `match` is a fragment of the merchant string, matched case-insensitively
    against the memo with punctuation ignored, so "PAK N SAVE" catches
    "PAK'nSAVE" too. Prefix it with "re:" for a regular expression, which is
    how a short or ambiguous token gets word boundaries.

    `category` must be one rules.yml already defines - `get_uncategorised`
    returns the list. A rule can teach a new merchant but never a new
    category, because a category carries a group and the group drives the
    coaching maths.

    Categorisation is re-run immediately, and the reply says how many
    transactions the new rule actually claimed. Zero means the pattern is
    wrong, not that the work is done.

    Check `warning` before reporting success. Rules are evaluated in priority
    order, so a pattern can also take transactions off a category that already
    had them - and if the two categories sit in different spending groups, the
    household's essentials total and their weeks-of-runway change as a result.
    `reclassified` lists exactly what moved. Say so when it happens; a rule
    that quietly reclassifies a supermarket shop as discretionary is worse
    than one that matches nothing.

    Args:
        match: a distinctive fragment of the merchant name, or "re:<regex>".
        category: the category key to file it under.
    """
    try:
        result = categorise.learn(match, category, producer="agent:mcp")
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "valid_categories": config.declared_categories(),
        }

    if result["already_known"]:
        note = (
            f"'{result['pattern']}' was already recorded against "
            f"{result['label']}, so nothing changed."
        )
    elif result["matched"] == 0:
        note = (
            "Saved, but it matched nothing currently uncategorised. Check it against "
            "the merchant strings from `get_uncategorised` - a pattern that matches "
            "nothing is worse than none, because it looks like the gap was closed."
        )
    else:
        note = (
            f"{result['matched']} transactions are now filed under "
            f"{result['label']}, and future ones will be too."
        )

    # Appended rather than replacing the note, because it applies whichever of
    # the three cases above produced it - including "matched nothing", where a
    # rule that claimed no new spending can still have moved some.
    if result["warning"]:
        note = f"{note} {result['warning']}"

    return {
        "ok": True,
        "learned": {
            "match": result["pattern"],
            "category": result["category"],
            "label": result["label"],
        },
        "matched": result["matched"],
        "reclassified": result["reclassified"],
        "changed_group_count": result["changed_group_count"],
        "warning": result["warning"],
        "already_known": result["already_known"],
        "written_to": result["path"],
        "coverage": result["coverage"],
        "note": note,
        "provenance": _provenance(),
    }


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
@server.tool()
def get_notes(limit: int | None = None) -> dict[str, Any]:
    """
    What the household said about past months, in their own words.

    This is the context the figures cannot supply. A fortnight where fuel
    doubled means something quite different if the note for that month says
    "drove to Auckland for the funeral", and an agent reasoning from the
    numbers alone will draw the wrong conclusion confidently.

    For the numbers themselves, use `get_spending_over_time`. This tool
    replaced one that returned frozen copies of those figures, which went stale
    against the ledger they were copied from.

    Args:
        limit: how many to return. Defaults to all of them.
    """
    rows = db.notes(limit=limit)
    return {"notes": rows, "count": len(rows)}


@server.tool()
def answer_household_fact(fact: str, value: bool | str | None = None) -> dict[str, Any]:
    """
    Record an answer to something no bank export can reveal. Writes to disk.

    `describe_data_gaps` lists these as questions. This is how the answer gets
    recorded once the user gives it, so the tool stops asking and starts
    reasoning correctly.

    Three facts, and each takes three answers - true, false, or null for "not
    established". The difference between false and null matters: told there
    are no children, the tool stops raising Working for Families entirely;
    told nothing, it keeps asking, because for a family that does qualify that
    credit is usually the largest sum this tool can find.

        has_children  true | false | null
        has_partner   true | false | null
        housing       owner_with_mortgage | owner_freehold | renting | other | null

    Only record what the user actually said. Do not infer an answer from the
    conversation and store it as though they had given it - a stored answer
    outranks the tool's own inference from their bank data, so a wrong guess
    here is worse than no answer at all. Pass null to clear one.

    Args:
        fact: has_children, has_partner or housing.
        value: the answer, or null to put the question back.
    """
    try:
        return {"ok": True, "fact": fact, "resolved": facts.answer(fact, value)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "valid_facts": list(facts.QUESTIONS)}


@server.tool()
def get_property() -> dict[str, Any]:
    """
    What the household owns, and who says it is worth that.

    Unlike every other figure this tool reports, a property value was typed in
    rather than derived. Each one carries its basis - a council rating value, an
    agent's appraisal, the purchase price, or somebody's guess - and the date it
    was true. Say both out loud when quoting a value. "Their home is worth
    $600,000" and "their 2023 council rating value was $600,000" are different
    claims, and only the second one is true.

    Valuations are a history. Several can coexist and disagree; the newest by
    observation date is the current one, but the others are not wrong, just
    older.

    Nothing computes equity or a loan-to-value ratio from this yet.
    """
    return _described(properties.summary())


@server.tool()
def record_property_value(
    label: str,
    value: float,
    method: str,
    observed_at: str,
    source: str | None = None,
    confidence: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """
    Record what a property is worth. This writes to disk.

    This tool CANNOT look a value up. Wyrmhoard makes no outbound network
    requests at all - that promise is why a household is willing to point it at
    their bank statements. If the user does not know what their home is worth,
    ask them to check their council's rating value or a recent appraisal. Do
    not search the web and record the result as though this tool found it.

        method       appraisal | council_rv | purchase_price | estimate
        observed_at  the date the figure was TRUE, not today
        confidence   high | medium | low, as YOU judge the figure

    Record what the user actually has. A number they half-remember is an
    `estimate`, not an `appraisal`, and every figure later derived from it
    inherits that difference. Do not upgrade a guess to make it look better.

    `observed_at` is required and is not defaulted, because dating an old
    figure as today silently turns a stale number into a current valuation.
    If the user does not know when it was true, ask.

    Args:
        label: which property - "Home", or a name for a second one.
        value: what it is worth, in the household's currency.
        method: how that figure was arrived at.
        observed_at: ISO date the figure was true.
        source: where it came from - a rates notice, an agent's letter.
        confidence: how much weight to put on it.
        note: anything a person should know when reading it later.
    """
    try:
        result = properties.record_valuation(
            label=label,
            value=value,
            method=method,
            observed_at=observed_at,
            producer="agent:mcp",
            source=source,
            confidence=confidence,
            note=note,
        )
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "valid_methods": list(properties.METHODS),
            "valid_confidences": list(properties.CONFIDENCES),
        }
    return result


@server.tool()
def record_note(note: str, observed_at: str | None = None) -> dict[str, Any]:
    """
    Record what the household said about a month. This writes to disk.

    Worth doing at the end of a review, when somebody explains a month: what
    they changed, what went wrong, what the unusual expense was. Those
    sentences are the one thing this tool cannot reconstruct later from the
    transactions, and nobody remembers them a year on.

    Write down what the user actually said, not your reading of it. A note is a
    record of their words; your interpretation belongs in your reply to them,
    where they can disagree with it.

    Notes append. Two notes about the same month both survive, and neither
    replaces the other.

    Args:
        note: what the household said, in their words.
        observed_at: the date the note is ABOUT. Defaults to today, which is
            wrong if they are describing a month that has already ended - pass
            a date in that month instead.
    """
    try:
        return db.add_note(
            note=note,
            observed_at=observed_at or date.today().isoformat(),
            producer="agent:mcp",
        )
    except ValueError as exc:
        return {"stored": False, "error": str(exc)}


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

    The file must be inside the household's data directory. This tool used to
    accept any path the container could reach, which on a tool an agent drives
    is the wrong default: a path can arrive from an email or a web page as
    easily as from the person asking.

    Args:
        path: path to a .csv or .pdf inside the data directory.
    """
    from .ingest import ingest_document, resolve_within

    try:
        source = resolve_within(config.DATA_DIR, path)
        result = ingest_document(source, producer="agent:mcp")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if result["kind"] == "payslip":
        return {"ok": bool(result["accepted"]), **result}

    report = result["report"]
    return {
        "ok": report["confidence"] != "low",
        "kind": "bank_export",
        "report": report,
        "coverage": categorise.recategorise_all(),
    }


@server.tool()
def list_transactions(
    month: str | None = None, category: str | None = None, limit: int = 100
) -> dict[str, Any]:
    """
    Individual transactions. Use sparingly.

    These are raw records, and they name every shop, person and service the
    household paid. Prefer `get_spending_breakdown` for anything about totals
    or patterns; reach for this only when the user has asked something that
    genuinely needs individual rows, such as identifying a specific
    unrecognised payment.

    A row that has an itemised receipt carries its `receipt_id`. That is the
    only way to learn a purchase can be broken down further; `get_receipt`
    reads the lines.

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
    itemised = db.linked_document_ids()
    trimmed = [
        {
            "date": r["date"],
            "description": r["memo"],
            "amount": r["amount"],
            "category": r["category"],
            **({"receipt_id": itemised[r["fingerprint"]]} if r["fingerprint"] in itemised else {}),
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


@server.tool()
def list_receipts(limit: int | None = None) -> dict[str, Any]:
    """
    Every itemised document held - receipts, invoices, statements - newest first.

    Each entry says which shop, what day, what it cost, how many lines it has,
    which producer submitted it and whether it has been matched to a bank
    transaction. It does not include the lines: product names are the most
    revealing thing this database holds, and reading them is a separate,
    deliberate call to `get_receipt`.

    Call this to learn whether a question about what was bought can be
    answered at all. Most purchases have no receipt; `describe_data_gaps` says
    how many do.

    Args:
        limit: at most this many, newest first. Default: all.
    """
    summary = documents.summary(limit=limit)
    return _described(
        {
            "receipts": [
                {
                    "receipt_id": d["id"],
                    "kind": d["kind"],
                    "merchant": d["merchant"],
                    "observed_at": d["observed_at"],
                    "stated_total": d["stated_total"],
                    "currency": d["currency"],
                    "line_count": d["item_count"],
                    "producer": d["producer"],
                    "confidence": d["confidence"],
                    "linked_to_a_transaction": bool(d.get("fingerprint")),
                }
                for d in summary["documents"]
            ],
            "count": summary["count"],
            "unlinked_count": summary["unlinked"],
        }
    )


@server.tool()
def get_receipt(receipt_id: int) -> dict[str, Any]:
    """
    One receipt, line by line. Use sparingly.

    This returns product names. A transaction says which shop; a receipt says
    which medication, which brand, which quantity - the most revealing records
    the household holds. Prefer `get_spending_breakdown` and
    `get_spending_over_time` for anything about money; reach for this only when
    the user has asked what a purchase consisted of.

    Each line carries the shop's own department in `source_category`, verbatim
    and untranslated - "Deli & Chilled Foods", "Personal Care" - where the shop
    supplied one. Wyrmhoard has no view on whether a line is healthy, essential
    or anything else; that is the caller's judgement to make and to own. Lines
    always sum to `stated_total`; a document that did not was refused on the
    way in.

    Args:
        receipt_id: from `list_receipts` or a transaction's `receipt_id`.
    """
    doc = db.document(receipt_id)
    if doc is None:
        return {"error": f"No receipt with id {receipt_id}. `list_receipts` shows what is held."}
    lines = [
        {
            "line_no": i["line_no"],
            "description": i["description"],
            "quantity": i["quantity"],
            "unit": i["unit"],
            "unit_price": i["unit_price"],
            "line_total": i["line_total"],
            "source_category": i["source_category"],
        }
        for i in db.document_items(receipt_id)
    ]
    return _described(
        {
            "receipt_id": doc["id"],
            "kind": doc["kind"],
            "merchant": doc["merchant"],
            "observed_at": doc["observed_at"],
            "stated_total": doc["stated_total"],
            "currency": doc["currency"],
            "lines": lines,
            "line_count": len(lines),
            "privacy_note": (
                "These lines name what a household bought, item by item. Answer the "
                "question asked and do not repeat them wholesale."
            ),
        },
        producer=doc["producer"],
        confidence=doc["confidence"],
    )


def main() -> None:
    """
    Start the server on whichever transport the caller asked for.

    stdio is the default and the safest: the agent spawns this process and
    talks to it down a pipe, so nothing listens anywhere and the transport
    cannot be reached by anything else on the machine.

    streamable-http exists because some clients cannot spawn a process at all.
    Claude Desktop on Windows, for one, runs its MCP servers in a sandbox that
    cannot see a Docker installation living inside WSL - there is simply
    nothing for it to launch. Those clients connect to a URL instead.

    The HTTP listener binds 0.0.0.0 INSIDE the container because that is the
    only way Docker can map a port to it. Exposure is controlled where it
    belongs, in compose, which publishes it on 127.0.0.1 only - the same
    arrangement the web API already uses. Nothing is reachable from the
    network.
    """
    import os

    config.ensure_dirs()
    db.init()

    transport = os.environ.get("WYRMHOARD_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        server.run()
        return

    port = int(os.environ.get("WYRMHOARD_MCP_PORT", "8787"))
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=port,
    )


if __name__ == "__main__":
    main()
