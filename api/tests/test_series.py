"""
Spending over a range the caller chose, in periods that mean something.

The rules being enforced, each of which exists because the older
"last N complete months" functions break one of them:

  1. A period is never silently dropped. It comes back marked incomplete.
  2. A period with no spending is a zero, not a missing point.
  3. Fortnights line up with pay, and the answer says what they lined up to.
  4. A rate never counts an unfinished period.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

import pytest
import yaml

from wyrmhoard import config, db
from wyrmhoard.analysis import cashflow, series


def _load(rows: list[tuple[str, str, float]]) -> None:
    """rows are (date, memo, amount)."""
    db.insert_transactions(
        [
            {
                "fingerprint": f"fp{i}",
                "account": "Everyday",
                "date": when,
                "memo": memo,
                "amount": amount,
                "balance": None,
                "category": "fuel" if amount < 0 else "income_salary",
                "grp": "essential" if amount < 0 else "income",
                "categorised_by": "test",
            }
            for i, (when, memo, amount) in enumerate(rows)
        ],
        source_file="test.csv",
    )
    cashflow.frame.cache_clear()


def _declare(tmp_path, monkeypatch, settings: dict) -> None:
    """
    Write a real household.yml rather than replacing `config.household`.

    Patching the function itself breaks teardown: the autouse ledger fixture
    calls `config.reload()`, which calls `household.cache_clear()`, which a
    plain lambda does not have. Going through the file also exercises the path
    a household actually uses.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "household.yml").write_text(
        yaml.safe_dump({"settings": settings}), encoding="utf-8"
    )
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    config.reload()


@pytest.fixture
def four_fortnights(tmp_path, monkeypatch):
    """
    Eight weeks of fuel against a declared pay day, with a deliberate gap.

    Dates are relative to today so the "period in progress" logic is exercised
    rather than pinned to a date that stops being in the past.

    There is a transaction on the first and last day of the range on purpose.
    A period only counts as complete when the ledger covers all of it, so an
    export starting mid-period leaves that period incomplete - which is correct
    and is exactly what the first draft of this fixture tripped over.
    """
    anchor = date.today() - timedelta(days=56)
    _declare(tmp_path / "cfg", monkeypatch, {"pay_anchor": anchor.isoformat()})
    _load(
        [
            (anchor.isoformat(), "BP CONNECT", -80.0),
            ((anchor + timedelta(days=3)).isoformat(), "BP CONNECT", -20.0),
            # Nothing at all in the second fortnight - the gap under test.
            ((anchor + timedelta(days=29)).isoformat(), "Z ENERGY", -50.0),
            ((anchor + timedelta(days=55)).isoformat(), "MOBIL", -30.0),
        ]
    )
    return anchor


def test_a_fortnight_with_no_spending_is_a_zero_not_a_missing_point(four_fortnights):
    """
    A household that bought no fuel for two weeks is a fact worth plotting.

    Leaving the point out lets a chart join the line straight across it, which
    turns "we did not drive" into "we spent steadily".
    """
    out = series.spending(period="fortnight")
    fuel = next(s for s in out["series"] if s["category"] == "fuel")

    assert fuel["totals"][0] == 100.0
    assert fuel["totals"][1] == 0.0, "the empty fortnight was dropped instead of reported"
    assert fuel["transactions"][1] == 0


def test_periods_align_to_the_declared_pay_day(four_fortnights):
    anchor = four_fortnights
    out = series.spending(period="fortnight")

    assert out["anchor"] == anchor.isoformat()
    assert out["anchor_source"] == "household.yml"
    assert out["periods"][0]["start"] == anchor.isoformat()
    starts = [date.fromisoformat(p["start"]) for p in out["periods"]]
    assert all((b - a).days == 14 for a, b in pairwise(starts))


def test_the_period_in_progress_is_returned_but_marked_incomplete(four_fortnights):
    """
    The rule the old code broke.

    `complete_months()` drops a partial period without saying so. For a
    headline median that is defensible; for a series it draws a final bar near
    zero and reads as a collapse in spending.
    """
    out = series.spending(period="fortnight", to=(date.today() + timedelta(days=13)).isoformat())
    last = out["periods"][-1]

    assert date.fromisoformat(last["end"]) >= date.today()
    assert last["complete"] is False
    assert last in out["periods"], "an unfinished period must still be returned"


def test_a_rate_never_counts_an_unfinished_period(four_fortnights):
    """
    `per_period` averages over complete periods only.

    Otherwise a fortnight that is two days old drags the average down and looks
    like the household cut back, which is the sort of wrong number that gets
    acted on.
    """
    out = series.spending(period="fortnight", to=(date.today() + timedelta(days=13)).isoformat())
    fuel = next(s for s in out["series"] if s["category"] == "fuel")

    complete = [t for t, p in zip(fuel["totals"], out["periods"], strict=True) if p["complete"]]
    assert fuel["per_period"] == pytest.approx(sum(complete) / len(complete))
    assert fuel["per_period"] > sum(fuel["totals"]) / len(fuel["totals"])


def test_periods_before_the_imported_data_are_marked_incomplete(four_fortnights):
    """
    A range reaching back past the export is not a range of zero spending.

    The household may well have bought fuel then; the ledger simply cannot say.
    Reporting those periods as complete zeroes would invent an answer.
    """
    early = (four_fortnights - timedelta(days=60)).isoformat()
    out = series.spending(period="fortnight", from_=early)

    assert out["periods"][0]["complete"] is False
    assert out["ledger_covers"]["from"] >= four_fortnights.isoformat()


def test_weeks_and_months_are_available_and_a_typo_is_refused(four_fortnights):
    assert series.spending(period="week")["period"] == "week"
    assert series.spending(period="month")["period"] == "month"
    with pytest.raises(ValueError):
        series.spending(period="fortnightly")


def test_an_undeclared_anchor_says_it_is_arbitrary(tmp_path, monkeypatch):
    """
    Guessing an anchor from one payslip was a real bug.

    A household with two jobs on different cycles would have anchored every
    fortnight to whichever payslip sorted first. Two dates fourteen days apart
    are evidence; one date is not, and the tool has to say which it had.
    """
    _declare(tmp_path / "cfg", monkeypatch, {"currency": "NZD"})
    _load([(date.today().isoformat(), "BP CONNECT", -50.0)])
    db.save_payslip({"pay_date": "2026-08-26", "employer": "A", "gross": 100.0, "net": 80.0})
    db.save_payslip({"pay_date": "2026-09-03", "employer": "B", "gross": 50.0, "net": 40.0})

    out = series.spending(period="fortnight")
    assert "arbitrary" in out["anchor_source"]
    assert out["anchor"] != "2026-08-26"


def test_an_empty_ledger_declines_rather_than_inventing_periods(tmp_path, monkeypatch):
    _declare(tmp_path / "cfg", monkeypatch, {"currency": "NZD"})
    cashflow.frame.cache_clear()
    out = series.spending(period="week")

    assert out["available"] is False
    assert out["series"] == []
    assert out["periods"] == []
