"""
The coaching checks, now that each one stands on its own.

These findings used to be thirteen numbered blocks inside a single 590-line
function, which meant none of them could be exercised without computing all
of them from a full ledger. Splitting them was worth doing mainly for this:
a check can be handed the exact numbers that should trigger it and asked
whether it does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import coach, config


def context(**overrides) -> coach.Context:
    """A Context with everything empty, so a test supplies only what it needs."""
    empty: dict = {
        # Real objects rather than None: several checks ask the household
        # whether its country is supported or whether it has a mortgage, and a
        # None here would test the fixture rather than the check.
        "hh": config.Household({}),
        "rt": config.Rates({}),
        "s": {},
        "typ": {},
        "cash": {},
        "leaks": {},
        "trend": {},
        "rec": {"subscriptions_count": 0, "subscriptions_annual": 0, "items": []},
        "ent": {},
        "cats": [],
        "inferred_loans": [],
    }
    return coach.Context(**{**empty, **overrides})


def test_every_check_is_registered():
    """
    A check that exists but is not in CHECKS never runs, and nothing else
    fails - the finding simply never appears in anybody's report.
    """
    defined = {
        name for name in dir(coach) if name.startswith("_check_") and callable(getattr(coach, name))
    }
    registered = {fn.__name__ for fn in coach.CHECKS}
    assert defined == registered, f"not registered in CHECKS: {sorted(defined - registered)}"


@pytest.mark.parametrize("check", coach.CHECKS, ids=lambda f: f.__name__)
def test_every_check_survives_an_empty_ledger(check):
    """
    A new household has none of this data, and must not see a crash.

    Not asserting the checks stay silent, because some correctly do not:
    "no credit cards or personal loans" is a true finding about a ledger with
    nothing in it. Only that nothing raises, and that whatever comes back is
    a Finding rather than something the report cannot render.
    """
    produced = check(context())
    assert all(isinstance(f, coach.Finding) for f in produced)


@pytest.mark.parametrize("check", coach.CHECKS, ids=lambda f: f.__name__)
def test_every_check_has_a_docstring_saying_what_it_looks_for(check):
    assert (check.__doc__ or "").strip(), f"{check.__name__} has no docstring"


# ---------------------------------------------------------------------------
# Individual checks, given exactly the numbers that should trigger them
# ---------------------------------------------------------------------------
def test_spending_more_than_you_earn_is_the_most_severe_finding():
    typical = {
        "available": True,
        "income_median": 5000.0,
        "spend_median": 5600.0,
        "net_median": -600.0,
        "month_count": 12,
        "savings_rate_pct": -12.0,
    }
    (finding,) = coach._check_monthly_margin(context(typ=typical))

    assert finding.id == "negative_cashflow"
    assert finding.severity == "critical"
    assert finding.amount == pytest.approx(7200.0)


def test_a_balanced_month_with_no_room_is_not_treated_as_a_win():
    """It balances, but a single unexpected bill becomes debt."""
    typical = {
        "available": True,
        "income_median": 5000.0,
        "spend_median": 4900.0,
        "net_median": 100.0,
        "month_count": 12,
        "savings_rate_pct": 2.0,
    }
    (finding,) = coach._check_monthly_margin(context(typ=typical))

    assert finding.id == "thin_margin"
    assert finding.severity == "high"


def test_a_real_margin_is_named_as_a_win():
    typical = {
        "available": True,
        "income_median": 5000.0,
        "spend_median": 4000.0,
        "net_median": 1000.0,
        "month_count": 12,
        "savings_rate_pct": 20.0,
    }
    (finding,) = coach._check_monthly_margin(context(typ=typical))

    assert finding.id == "positive_margin"
    assert finding.severity == "win"


@pytest.mark.parametrize(
    "weeks,severity",
    [(1.0, "critical"), (3.0, "high"), (8.0, "medium"), (20.0, "win")],
)
def test_runway_severity_tracks_how_long_the_money_would_last(weeks, severity):
    cash = {
        "runway_weeks": weeks,
        "total": 4000.0,
        "monthly_essentials": 3000.0,
        "source": "imported balances",
    }
    (finding,) = coach._check_runway(context(cash=cash))

    assert finding.id == "runway"
    assert finding.severity == severity


def test_a_household_with_no_consumer_debt_is_told_so():
    (finding,) = coach._check_wins(context(cats=[{"category": "groceries"}]))
    assert finding.id == "no_consumer_debt"
    assert finding.severity == "win"


def test_a_household_with_consumer_debt_gets_no_such_win():
    assert coach._check_wins(context(cats=[{"category": "bnpl"}])) == []


def test_findings_are_ranked_by_severity_then_by_size(monkeypatch):
    """A family acts on three things, so the order decides which three."""
    made = [
        coach.Finding(id="a", title="", severity="low", body="", amount=100),
        coach.Finding(id="b", title="", severity="critical", body="", amount=10),
        coach.Finding(id="c", title="", severity="low", body="", amount=900),
        coach.Finding(id="d", title="", severity="win", body="", amount=5000),
    ]
    monkeypatch.setattr(coach, "CHECKS", (lambda c: made,))
    monkeypatch.setattr(coach.Context, "gather", classmethod(lambda cls: context()))

    assert [f.id for f in coach.build_findings()] == ["b", "c", "a", "d"]
