"""
The three-answer facts.

The whole point of this module is the difference between "no" and "not told",
so most of these tests are pairs: the same empty data, two different stated
answers, two different behaviours.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import config, db, facts
from wyrmhoard.analysis import entitlements


def set_household(monkeypatch, raw: dict) -> None:
    """
    Replace the loaded household with an explicit one.

    Wrapped in lru_cache to keep the real interface: config.reload() calls
    household.cache_clear(), and the autouse fixture calls reload() on the way
    out of every test, so a plain lambda breaks teardown rather than the test.
    """
    monkeypatch.setattr(
        config, "household", functools.lru_cache(maxsize=1)(lambda: config.Household(raw))
    )


# ---------------------------------------------------------------------------
# Unknown is the shipped default
# ---------------------------------------------------------------------------
def test_everything_is_unknown_when_nothing_is_said(monkeypatch):
    set_household(monkeypatch, {})
    for fact in facts.all_facts().values():
        assert fact["value"] is None
        assert fact["known"] is False


def test_unknown_facts_come_with_a_question_to_ask(monkeypatch):
    set_household(monkeypatch, {})
    questions = facts.unknown()
    assert {q["fact"] for q in questions} == {"has_children", "has_partner", "housing"}
    assert all(q["question"].strip().endswith("?") for q in questions)


# ---------------------------------------------------------------------------
# Children: no, versus not told
# ---------------------------------------------------------------------------
def test_a_listed_child_settles_it(monkeypatch):
    set_household(
        monkeypatch,
        {"people": [{"name": "Riley", "role": "child", "birth_date": "2019-04-12"}]},
    )
    fact = facts.has_children()
    assert fact["value"] is True
    assert fact["source"] == "people"


def test_stated_no_children_is_not_the_same_as_silence(monkeypatch):
    set_household(monkeypatch, {"household": {"has_children": False}})
    assert facts.has_children()["value"] is False

    set_household(monkeypatch, {"household": {}})
    assert facts.has_children()["value"] is None


def test_saying_yes_without_dates_still_counts_but_says_dates_are_needed(monkeypatch):
    set_household(monkeypatch, {"household": {"has_children": True}})
    fact = facts.has_children()
    assert fact["value"] is True
    assert "birth dates" in fact["evidence"]


# ---------------------------------------------------------------------------
# The behaviour this exists for
# ---------------------------------------------------------------------------
def test_a_childless_household_is_told_the_credit_does_not_apply(monkeypatch):
    """No nagging a couple about a credit for children they do not have."""
    set_household(monkeypatch, {"household": {"country": "NZ", "has_children": False}})
    result = entitlements.estimate()
    assert result["available"] is False
    assert result["applicable"] is False
    assert "does not apply" in result["reason"]


def test_a_silent_household_is_asked_rather_than_assumed_childless(monkeypatch):
    """The expensive failure: a family that never hears about the credit."""
    set_household(monkeypatch, {"household": {"country": "NZ"}})
    result = entitlements.estimate()
    assert result["available"] is False
    assert result["applicable"] is True
    assert "household.yml" in result["reason"]


# ---------------------------------------------------------------------------
# Partner
# ---------------------------------------------------------------------------
def test_a_listed_partner_settles_it(monkeypatch):
    set_household(monkeypatch, {"people": [{"name": "Sam", "role": "partner"}]})
    assert facts.has_partner()["value"] is True


@pytest.mark.parametrize("stated,expected", [(True, True), (False, False), (None, None)])
def test_stated_partner_is_taken_at_its_word(monkeypatch, stated, expected):
    set_household(monkeypatch, {"household": {"has_partner": stated}})
    assert facts.has_partner()["value"] is expected


# ---------------------------------------------------------------------------
# Housing: the one fact with evidence available
# ---------------------------------------------------------------------------
def test_a_loan_account_implies_owning_with_a_mortgage(monkeypatch):
    set_household(monkeypatch, {})
    from wyrmhoard import accounts as accounts_mod

    monkeypatch.setattr(accounts_mod, "liability_accounts", lambda: {"99-9999-9999999-99"})

    fact = facts.housing()
    assert fact["value"] == "owner_with_mortgage"
    assert fact["source"] == "inferred"


def test_rent_payments_imply_renting_when_there_is_no_loan(monkeypatch):
    set_household(monkeypatch, {})
    from wyrmhoard import accounts as accounts_mod

    monkeypatch.setattr(accounts_mod, "liability_accounts", lambda: set())
    monkeypatch.setattr(facts, "_rent_payment_count", lambda: 6)

    fact = facts.housing()
    assert fact["value"] == "renting"
    assert fact["source"] == "inferred"


def test_one_rent_payment_is_not_a_tenancy(monkeypatch):
    """A single payment is a favour to a friend, not somewhere to live."""
    set_household(monkeypatch, {})
    from wyrmhoard import accounts as accounts_mod

    monkeypatch.setattr(accounts_mod, "liability_accounts", lambda: set())
    monkeypatch.setattr(facts, "_rent_payment_count", lambda: 1)

    assert facts.housing()["value"] is None


def test_the_household_overrides_the_inference(monkeypatch):
    """The loan the tool found might be a car loan. Saying so must win."""
    set_household(monkeypatch, {"household": {"housing": "renting"}})
    from wyrmhoard import accounts as accounts_mod

    monkeypatch.setattr(accounts_mod, "liability_accounts", lambda: {"99-9999-9999999-99"})

    fact = facts.housing()
    assert fact["value"] == "renting"
    assert fact["source"] == "stated"


def test_a_nonsense_housing_value_is_rejected_rather_than_believed(monkeypatch):
    set_household(monkeypatch, {"household": {"housing": "mansion"}})
    fact = facts.housing()
    assert fact["value"] is None
    assert "mansion" in fact["evidence"]


def test_rent_counting_survives_a_missing_table(monkeypatch, tmp_path):
    """No ledger is absence of evidence, not evidence of ownership."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "nothing" / "ledger.db")
    assert facts._rent_payment_count() == 0


# ---------------------------------------------------------------------------
# The rent rule that the housing inference depends on
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "memo",
    ["RENT 12 MAIN ST", "WEEKLY RENT", "TENANCY SERVICES BOND", "QUINOVIC PALMERSTON"],
)
def test_rent_payments_are_categorised_as_rent(memo):
    from wyrmhoard.categorise import categorise_one

    key, group, _ = categorise_one(memo, amount=-450.0)
    assert key == "rent", f"{memo!r} was categorised as {key}"
    assert group == "commitment"


@pytest.mark.parametrize("memo", ["PARENT TEACHER ASSN", "CURRENT ACCOUNT FEE", "APEX RENTALS"])
def test_the_rent_rule_does_not_fire_on_words_containing_rent(memo):
    """A plain substring match would classify all three of these as rent."""
    from wyrmhoard.categorise import categorise_one

    key, _, _ = categorise_one(memo, amount=-50.0)
    assert key != "rent", f"{memo!r} was wrongly categorised as rent"


# ---------------------------------------------------------------------------
# Answering without editing YAML
#
# The questions were added to the setup checklist before there was any way to
# respond to them except opening household.yml, which made "there are no
# children here" harder to say than it should be.
# ---------------------------------------------------------------------------
def test_an_answer_given_in_the_app_settles_the_question(monkeypatch):
    set_household(monkeypatch, {})
    assert facts.has_children()["value"] is None

    facts.answer("has_children", False)

    fact = facts.has_children()
    assert fact["value"] is False
    assert fact["known"] is True


def test_clearing_an_answer_puts_the_question_back(monkeypatch):
    """None is not the same answer as false - it unsettles rather than settles."""
    set_household(monkeypatch, {})
    facts.answer("has_partner", True)
    assert facts.has_partner()["value"] is True

    facts.answer("has_partner", None)

    assert facts.has_partner()["value"] is None
    assert "has_partner" in {q["fact"] for q in facts.unknown()}


def test_an_answer_in_the_app_beats_the_file(monkeypatch):
    """Otherwise a correction made in the dashboard appears not to work."""
    set_household(monkeypatch, {"household": {"has_partner": False}})
    assert facts.has_partner()["value"] is False

    facts.answer("has_partner", True)
    assert facts.has_partner()["value"] is True


def test_clearing_falls_back_to_the_file_rather_than_to_unknown(monkeypatch):
    set_household(monkeypatch, {"household": {"housing": "renting"}})
    facts.answer("housing", "owner_freehold")
    assert facts.housing()["value"] == "owner_freehold"

    facts.answer("housing", None)
    assert facts.housing()["value"] == "renting"


def test_an_answer_survives_into_the_stored_shape(monkeypatch):
    """Stored as text, so the round trip has to give back a real boolean."""
    set_household(monkeypatch, {})
    facts.answer("has_children", True)
    assert db.household_facts()["has_children"] == "true"
    assert facts.has_children()["value"] is True


@pytest.mark.parametrize("bad", ["true", "yes", 1, 0, ""])
def test_a_yes_no_fact_refuses_anything_that_is_not_a_boolean(monkeypatch, bad):
    """
    A form posting the string "false" is truthy nearly everywhere, and reading
    it as yes would answer the question backwards.
    """
    set_household(monkeypatch, {})
    with pytest.raises(ValueError):
        facts.answer("has_children", bad)
    assert facts.has_children()["value"] is None


def test_an_unknown_housing_option_is_refused_with_the_valid_ones(monkeypatch):
    set_household(monkeypatch, {})
    with pytest.raises(ValueError) as exc:
        facts.answer("housing", "mansion")
    assert "owner_with_mortgage" in str(exc.value)
    assert facts.housing()["value"] is None


def test_an_unknown_fact_is_refused(monkeypatch):
    set_household(monkeypatch, {})
    with pytest.raises(ValueError) as exc:
        facts.answer("has_yacht", True)
    assert "has_children" in str(exc.value)


def test_the_evidence_says_where_the_answer_came_from(monkeypatch):
    """
    That line is how somebody finds an answer again in order to change it, so
    it must not send them to a file that never mentioned it.
    """
    set_household(monkeypatch, {"household": {"housing": "renting"}})
    assert "household.yml" in facts.housing()["evidence"]

    facts.answer("housing", "owner_freehold")
    assert "in the app" in facts.housing()["evidence"]

    facts.answer("has_children", False)
    assert "in the app" in facts.has_children()["evidence"]
