"""
Properties, valuations, and the producer contract they are the first test of.

The point of these tests is not that a house value can be stored. It is that a
stored value can always answer "who says so, on what basis, and as at when" -
because this is the first figure in the system with no ledger behind it, and a
number nobody can question is worse than no number at all.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import db, properties

YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
LAST_YEAR = (date.today() - timedelta(days=400)).isoformat()


def record(**over):
    """A valid submission, so each test varies one thing."""
    payload = {
        "label": "Home",
        "value": 600000.0,
        "method": "council_rv",
        "observed_at": YESTERDAY,
        "producer": "tool:rates-lookup",
        "source": "PNCC rating value",
        "confidence": "medium",
    }
    payload.update(over)
    return properties.record_valuation(**payload)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
def test_a_valuation_keeps_every_provenance_field():
    """All six, or the figure cannot be questioned later."""
    result = record(note="checked against the rates notice")
    stored = result["valuation"]

    assert stored["value"] == 600000.0
    assert stored["producer"] == "tool:rates-lookup"
    assert stored["method"] == "council_rv"
    assert stored["observed_at"] == YESTERDAY
    assert stored["received_at"]
    assert stored["source"] == "PNCC rating value"
    assert stored["confidence"] == "medium"
    assert stored["note"] == "checked against the rates notice"


def test_observed_at_and_received_at_are_different_questions():
    """
    When a figure was true is not when it arrived. Conflating them is how
    manual_balances lost the fact that a value had ever changed.
    """
    stored = record(observed_at=LAST_YEAR)["valuation"]

    assert stored["observed_at"] == LAST_YEAR
    assert stored["received_at"].startswith(date.today().isoformat())
    assert stored["age_days"] >= 400


@pytest.mark.parametrize(
    "bad", ["", None, "scraper", "human", "tool:", "tool:A B", "robot:thing", ":name"]
)
def test_a_producer_must_name_itself(bad):
    """Anonymous data is the state this whole contract exists to prevent."""
    with pytest.raises(ValueError) as exc:
        record(producer=bad)
    assert "producer" in str(exc.value).lower()
    assert db.valuations() == []


def test_a_producer_name_is_normalised_so_one_producer_is_one_producer():
    """Otherwise 'Tool:Rates' and 'tool:rates' look like corroboration."""
    first = record(producer="Tool:Rates-Lookup")
    assert first["valuation"]["producer"] == "tool:rates-lookup"

    second = record(producer="tool:rates-lookup")
    assert second["recorded"] is False, "the same producer under a different case"


@pytest.mark.parametrize("good", ["human:dashboard", "agent:mcp", "tool:rates-lookup"])
def test_each_producer_kind_is_accepted(good):
    assert record(producer=good)["ok"] is True


def test_a_future_observation_date_is_refused():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError) as exc:
        record(observed_at=tomorrow)
    assert "future" in str(exc.value)


def test_a_missing_observation_date_is_refused_rather_than_defaulted():
    """
    Defaulting to today silently turns "a number I half-remember from 2023"
    into a current valuation, which is the exact staleness failure this project
    already calls out for mortgage rates.
    """
    with pytest.raises(ValueError) as exc:
        record(observed_at=None)
    assert "required" in str(exc.value)


def test_an_unknown_method_is_refused_and_the_error_lists_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        record(method="zillow")
    for method in properties.METHODS:
        assert method in str(exc.value)


@pytest.mark.parametrize("bad", [0, -1, "abc", None])
def test_a_value_must_be_a_positive_number(bad):
    with pytest.raises(ValueError):
        record(value=bad)


# ---------------------------------------------------------------------------
# History, not a current value
# ---------------------------------------------------------------------------
def test_the_same_claim_submitted_twice_is_recorded_once():
    """A producer on a schedule must not fill the table with duplicates."""
    first = record()
    second = record()

    assert first["recorded"] is True
    assert second["recorded"] is False
    assert second["already_known"] is True
    assert len(properties.history(first["property_id"])) == 1


def test_a_new_figure_does_not_destroy_the_old_one():
    prop_id = record(value=600000, observed_at=LAST_YEAR)["property_id"]
    record(value=655000, observed_at=YESTERDAY, method="appraisal", producer="human:dashboard")

    rows = properties.history(prop_id)
    assert len(rows) == 2
    assert [r["value"] for r in rows] == [655000.0, 600000.0]


def test_latest_means_most_recently_true_not_most_recently_submitted():
    """
    A producer submitting an old council figure today must not displace a fresh
    appraisal recorded last week.
    """
    prop_id = record(value=655000, observed_at=YESTERDAY, method="appraisal")["property_id"]
    record(value=600000, observed_at=LAST_YEAR, method="council_rv")

    assert properties.latest(prop_id)["value"] == 655000.0


def test_two_producers_agreeing_are_two_claims_not_one():
    """Independent corroboration is information; one source repeating is not."""
    prop_id = record(producer="tool:rates-lookup")["property_id"]
    record(producer="human:dashboard")

    assert len(properties.history(prop_id)) == 2


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
def test_the_first_property_becomes_primary_on_its_own():
    record(label="Home")
    assert properties.summary()["primary"]["label"] == "Home"


def test_only_one_property_is_ever_primary():
    record(label="Home")
    record(label="The rental", is_primary=True)

    primaries = [p for p in properties.summary()["properties"] if p["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["label"] == "The rental"


def test_a_second_property_is_just_another_row():
    record(label="Home")
    record(label="The rental", value=430000)

    assert properties.summary()["count"] == 2


def test_deleting_a_property_takes_its_valuations_with_it():
    prop_id = record()["property_id"]
    assert db.valuations(prop_id)

    properties.delete(prop_id)

    assert db.valuations(prop_id) == []
    assert properties.summary()["count"] == 0


def test_deleting_something_that_is_not_there_says_so():
    with pytest.raises(ValueError):
        properties.delete(999)


# ---------------------------------------------------------------------------
# Loan links
# ---------------------------------------------------------------------------
def test_a_loan_can_only_be_linked_if_it_is_a_known_loan_account(monkeypatch):
    from wyrmhoard import accounts

    monkeypatch.setattr(accounts, "liability_accounts", lambda: {"99-9999-9999999-99"})
    prop_id = record()["property_id"]

    properties.link_loan(prop_id, "99-9999-9999999-99")
    assert properties.summary()["properties"][0]["loan_accounts"] == ["99-9999-9999999-99"]

    with pytest.raises(ValueError) as exc:
        properties.link_loan(prop_id, "38-9014-0123456-00")
    assert "not one of this household's loan accounts" in str(exc.value)


def test_a_loan_can_be_unlinked_again(monkeypatch):
    from wyrmhoard import accounts

    monkeypatch.setattr(accounts, "liability_accounts", lambda: {"99-9999-9999999-99"})
    prop_id = record()["property_id"]
    properties.link_loan(prop_id, "99-9999-9999999-99")

    properties.link_loan(prop_id, "99-9999-9999999-99", linked=False)

    assert properties.summary()["properties"][0]["loan_accounts"] == []


# ---------------------------------------------------------------------------
# Agreement with what the household said
# ---------------------------------------------------------------------------
def test_a_recorded_property_alongside_a_renting_answer_is_reported_not_resolved(monkeypatch):
    """
    Both are things a person asserted. The tool says they disagree and changes
    neither - inferring an answer from a side effect is what the agent-facing
    tools are explicitly told not to do.
    """
    from wyrmhoard import facts

    record()
    facts.answer("housing", "renting")

    summary = properties.summary()
    assert summary["conflicts"]
    assert "rent" in summary["conflicts"][0]
    assert facts.housing()["value"] == "renting", "the stated fact must not be overwritten"


def test_an_owner_with_no_property_recorded_is_told_what_is_missing(monkeypatch):
    from wyrmhoard import facts

    facts.answer("housing", "owner_with_mortgage")

    summary = properties.summary()
    assert summary["count"] == 0
    assert any("no property is recorded" in c for c in summary["conflicts"])


def test_summary_is_calm_on_an_empty_ledger():
    summary = properties.summary()
    assert summary["available"] is False
    assert summary["count"] == 0
    assert summary["properties"] == []
    assert summary["primary"] is None
