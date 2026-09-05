"""
Loan terms derived from the loan account itself.

Households do not keep a mortgage rate up to date in a config file. They fill
it in once, it changes at the next refix, and the tool quietly reports a wrong
payoff date forever. Everything needed is already in the account: repayments
arrive as credits, interest as debits, and the balance after each.

The awkward cases are the ones worth pinning:

  * an offset loan charges interest on the balance MINUS linked accounts, so
    interest-divided-by-balance implies a rate near zero
  * a fully-offset period is charged nothing and produces no row at all, so
    summing a year and dividing understates the rate
  * repayments change whenever the rate does, so there is no single "the
    repayment"
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import cache, categorise, db
from wyrmhoard.analysis import mortgage

LOAN = "38-9014-0000000-05"
FORTNIGHT = 14


def add(account: str, when: date, amount: float, balance: float, memo: str):
    db.insert_transactions(
        [
            {
                "fingerprint": db.fingerprint(account, when.isoformat(), memo, amount, balance),
                "account": account,
                "date": when.isoformat(),
                "memo": memo,
                "match_text": memo,
                "counterparty": None,
                "amount": amount,
                "balance": balance,
            }
        ],
        "test.csv",
    )


def build_loan(
    *,
    periods: int = 26,
    repayment: float = 735.44,
    rate_pct: float = 5.0,
    balance: float = 170000.0,
    offset_benefit: float | None = None,
    skip_interest_every: int | None = None,
    notice: tuple[float, float, str] | None = None,
):
    """A fortnightly loan, optionally offset, optionally with a bank notice."""
    owed = balance
    when = date.today() - timedelta(days=FORTNIGHT * periods)

    for i in range(periods):
        gross = owed * (rate_pct / 100) / 26
        charged = gross
        memo = "LOAN INTEREST"
        if offset_benefit is not None:
            charged = max(gross - offset_benefit, 0.0)
            memo = f"LOAN INTEREST OFFSET Benefit of ${offset_benefit:.2f}"

        # A fully-offset period produces no interest row whatsoever.
        if not (skip_interest_every and i % skip_interest_every == 0):
            owed += charged
            add(LOAN, when, -round(charged, 2), -round(owed, 2), memo)

        owed -= repayment
        add(LOAN, when, repayment, -round(owed, 2), "AP FROM US LOAN PYMT")
        when += timedelta(days=FORTNIGHT)

    if notice:
        old, new, due = notice
        add(LOAN, when - timedelta(days=1), 0.0, -round(owed, 2), f"From {old} to {new} due {due}")

    cache.clear_all()
    categorise.recategorise_all()


def only_loan():
    loans = mortgage.infer_loans()
    assert len(loans) == 1, f"expected one loan, got {loans}"
    return loans[0]


# ---------------------------------------------------------------------------
def test_no_loan_accounts_returns_nothing():
    assert mortgage.infer_loans() == []


def test_balance_repayment_and_cadence_come_from_the_account():
    build_loan()
    loan = only_loan()

    assert loan["cadence"] == "fortnightly"
    assert loan["periods_per_year"] == 26
    assert loan["repayment"] == 735.44
    assert loan["balance"] > 0, "balance is reported as an amount owing, not a negative"


def test_the_implied_rate_is_close_to_the_real_one():
    build_loan(rate_pct=5.0)
    loan = only_loan()

    assert loan["rate_pct"] == pytest.approx(5.0, abs=0.4)
    assert loan["confidence"] == "high"


def test_offset_benefit_is_added_back_before_computing_the_rate():
    """
    The regression that matters. On an offset loan the interest actually
    charged is a fraction of what the balance would cost, so using it directly
    reports a mortgage running at a fraction of a percent.
    """
    build_loan(rate_pct=6.0, balance=4000.0, repayment=23.68, offset_benefit=4.0)
    loan = only_loan()

    assert loan["is_offset"] is True
    assert loan["offset_benefit"] > 0
    assert loan["interest_gross"] > loan["interest_charged"]
    # Naive maths would land far below the real rate.
    assert loan["rate_pct"] == pytest.approx(6.0, abs=0.8)


def test_fully_offset_periods_do_not_drag_the_rate_down():
    """Periods with no interest charged produce no row; the median ignores them."""
    build_loan(
        rate_pct=6.0, balance=4000.0, repayment=23.68, offset_benefit=4.0, skip_interest_every=3
    )
    loan = only_loan()

    assert loan["interest_periods"] < 26
    assert loan["rate_pct"] == pytest.approx(6.0, abs=0.8)


def test_an_upcoming_repayment_change_is_read_from_the_bank_notice():
    """Banks announce these as zero-dollar rows. It is the most useful thing there."""
    due = (date.today() + timedelta(days=20)).strftime("%d%b%Y").upper()
    build_loan(notice=(735.44, 722.42, due))
    loan = only_loan()

    assert loan["upcoming_change"] is not None
    assert loan["upcoming_change"]["from"] == 735.44
    assert loan["upcoming_change"]["to"] == 722.42
    assert loan["upcoming_change"]["due"] >= date.today().isoformat()


def test_a_past_notice_is_not_reported_as_upcoming():
    past = (date.today() - timedelta(days=60)).strftime("%d%b%Y").upper()
    build_loan(notice=(800.00, 735.44, past))
    assert only_loan()["upcoming_change"] is None


def test_repayment_changes_are_tracked():
    build_loan(periods=6, repayment=800.0)
    build_loan(periods=6, repayment=750.0)
    loan = only_loan()

    amounts = [c["amount"] for c in loan["repayment_changes"]]
    assert 800.0 in amounts and 750.0 in amounts


def test_a_payoff_projection_is_produced_without_any_typed_input():
    build_loan()
    loan = only_loan()

    proj = loan["projection"]
    assert proj["available"] is True
    assert proj["base"]["years"] > 0
    # Paying more must never take longer.
    assert proj["scenarios"][-1]["years"] <= proj["scenarios"][0]["years"]
