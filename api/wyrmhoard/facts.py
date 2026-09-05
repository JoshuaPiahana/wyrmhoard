"""
The household facts that no bank export can settle.

Bank data is remarkably revealing about money and silent about people. It can
tell you a mortgage is being repaid; it cannot tell you whether the people
repaying it have children. Everything the ledger *can* answer is derived on
demand elsewhere in this package. What is left is the handful of facts that
only the household knows - and the important thing about them is that there
are three answers, not two.

"No children" and "you never told me about children" produce identical empty
lists, and a tool that cannot tell them apart gets both cases wrong:

  - A couple with no children is told to go and configure their children, over
    and over, about a benefit that will never apply to them.
  - A family with three children who have not filled in the file is quietly
    assessed as childless and never hears about the entitlement they are
    owed - which, for Working for Families, is usually the largest single
    number this tool is capable of finding.

The second failure is much the worse of the two, and it is silent. So the
shipped default for every fact here is `None`, meaning "not established", and
`None` makes the tool say so out loud rather than assume.

Resolution order matches `accounts.py`, for the same reason: a human's stated
answer always beats a guess, and a guess always beats a default.

    what the household wrote down  >  what the ledger implies  >  unknown

`housing` is the one fact here with real evidence available: a household
repaying a home loan owns a house, and a household paying rent does not.
Neither is certain - a liability account might be a car loan, and rent might
be for a storage unit - so inference is reported as inference, with the
evidence attached, and the household can correct it.
"""

from __future__ import annotations

from typing import Any

from . import config

# Every value `housing` may take. `None` is not in this tuple on purpose:
# it is the absence of an answer, not one of the answers.
HOUSING = ("owner_with_mortgage", "owner_freehold", "renting", "other")

# The question to put to the household when a fact is unknown, and why it is
# worth asking. Kept apart because they are used differently: the question is
# shown to a person and must be answerable in one breath, while the reason is
# what an agent needs in order to judge whether asking is worth interrupting
# for. Both appear in the setup checklist and in `describe_data_gaps`, so
# neither has to invent its own wording and the household meets the same
# question phrased the same way wherever it comes up.
QUESTIONS = {
    "has_children": "Are there children in this household?",
    "has_partner": "Is there a partner or spouse in this household?",
    "housing": "Do you own this home with a mortgage, own it outright, or rent?",
}

WHY = {
    "has_children": (
        "Their birth dates unlock the entitlement checks, and some credits turn " "on an exact age."
    ),
    "has_partner": "Entitlements are assessed on combined household income.",
    "housing": (
        "Mortgage payoff advice is noise to a renter, and deposit advice is noise "
        "to somebody who already owns."
    ),
}

# How many rent-categorised payments before we believe somebody rents. One is
# a favour to a friend; a handful spread over months is a tenancy.
_MIN_RENT_PAYMENTS = 3


def _stated(key: str) -> Any:
    """What the household wrote down, or None if they left it alone."""
    return (config.household().raw.get("household") or {}).get(key)


def _fact(
    value: Any,
    source: str,
    confidence: str,
    evidence: str,
) -> dict[str, Any]:
    return {
        "value": value,
        "known": value is not None,
        "source": source,
        "confidence": confidence,
        "evidence": evidence,
    }


def _unknown(key: str) -> dict[str, Any]:
    return {
        "value": None,
        "known": False,
        "source": "unset",
        "confidence": "none",
        "evidence": "Nothing in the data can establish this.",
        "question": QUESTIONS[key],
        "why_it_matters": WHY[key],
    }


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
def has_children() -> dict[str, Any]:
    """
    Whether children live here.

    A named child in `people` settles it - somebody took the trouble to write
    it down. Otherwise the household's explicit answer stands, including an
    explicit "no", which is what stops the coach nagging a couple about
    Working for Families forever.
    """
    named = config.household().children
    if named:
        return _fact(
            True,
            "people",
            "certain",
            f"{len(named)} child(ren) listed in household.yml.",
        )

    stated = _stated("has_children")
    if stated is True:
        return _fact(
            True,
            "stated",
            "certain",
            "Household says yes, but no birth dates are recorded yet - "
            "entitlement estimates need the dates, not just the count.",
        )
    if stated is False:
        return _fact(False, "stated", "certain", "Household says there are no children.")

    return _unknown("has_children")


def has_partner() -> dict[str, Any]:
    """Whether there are two adults, which changes how income is assessed."""
    partners = [p for p in config.household().people if p.role == "partner"]
    if partners:
        return _fact(True, "people", "certain", "A partner is listed in household.yml.")

    stated = _stated("has_partner")
    if stated in (True, False):
        return _fact(
            stated,
            "stated",
            "certain",
            f"Household says {'yes' if stated else 'no'}.",
        )

    return _unknown("has_partner")


# ---------------------------------------------------------------------------
# Housing
# ---------------------------------------------------------------------------
def _rent_payment_count() -> int:
    """How many payments the ledger has categorised as rent."""
    from . import db

    try:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE category = 'rent' AND amount < 0"
            ).fetchone()
    except Exception:
        # No ledger yet, or a table that does not exist. Absence of evidence,
        # not evidence of ownership.
        return 0
    return int(row[0]) if row else 0


def housing() -> dict[str, Any]:
    """
    Owning with a mortgage, owning outright, or renting.

    Worth inferring rather than asking, because it is the one fact here the
    ledger has an opinion about, and because getting it wrong is conspicuous:
    advice about overpaying a mortgage is noise to a renter, and advice about
    saving a deposit is noise to somebody who already owns.
    """
    stated = _stated("housing")
    if stated:
        value = str(stated)
        if value not in HOUSING:
            return _fact(
                None,
                "stated",
                "none",
                f"household.housing is '{value}', which is not one of {list(HOUSING)}.",
            )
        return _fact(value, "stated", "certain", "Set in household.yml.")

    from . import accounts

    loans = accounts.liability_accounts()
    if loans:
        return _fact(
            "owner_with_mortgage",
            "inferred",
            "high",
            f"{len(loans)} loan account(s) in the ledger. If one of these is a "
            "car or personal loan rather than a home loan, set `housing` in "
            "household.yml to correct it.",
        )

    rent_payments = _rent_payment_count()
    if rent_payments >= _MIN_RENT_PAYMENTS:
        return _fact(
            "renting",
            "inferred",
            "medium",
            f"{rent_payments} payments categorised as rent, and no loan account.",
        )

    return _unknown("housing")


# ---------------------------------------------------------------------------
# The set
# ---------------------------------------------------------------------------
def all_facts() -> dict[str, dict[str, Any]]:
    """Every tri-state fact, resolved."""
    return {
        "has_children": has_children(),
        "has_partner": has_partner(),
        "housing": housing(),
    }


def unknown() -> list[dict[str, str]]:
    """The facts still unestablished, with the question to ask for each."""
    return [
        {"fact": key, "question": QUESTIONS[key], "why_it_matters": WHY[key]}
        for key, fact in all_facts().items()
        if not fact["known"]
    ]


def summary() -> dict[str, Any]:
    """Facts plus what is missing, which is the shape callers actually want."""
    resolved = all_facts()
    return {
        "facts": resolved,
        "unknown": unknown(),
        "all_known": all(f["known"] for f in resolved.values()),
    }
