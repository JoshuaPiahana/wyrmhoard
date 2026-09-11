"""
The document contract: what the core accepts, and what it turns away.

The core does not parse receipts. A producer reads whatever its source emits
and submits the result, so the only thing standing between a badly-written
producer and a wrong grocery figure is this validation. That makes the refusal
tests the important ones - a checker nobody has proved rejects anything is a
checker that passes everything.

The fixtures are the real documents this was designed against, retyped with
invented product names. Real ones stay in `data/`, which the financial-data
guard now blocks wholesale.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import db, documents


def a_receipt(**over):
    """A well-formed submission. Lines add up to 40.00 unless a test says otherwise."""
    body = {
        "producer": "tool:example-receipts",
        "kind": "receipt",
        "merchant": "Example Supermarket Kelvin Grove",
        "observed_at": "2026-08-14",
        "stated_total": 40.00,
        "reference": "EX-0001",
        "source": "example.pdf",
        "confidence": "high",
        "items": [
            {
                "description": "Blue milk 2l",
                "quantity": 2,
                "unit": "ea",
                "unit_price": 5.00,
                "line_total": 10.00,
                "source_category": "Dairy & Eggs",
                "raw": "Blue milk 2l  Qty 2 @ $5.00 each  10.00",
            },
            {
                "description": "Bananas loose",
                "quantity": 1.120,
                "unit": "kg",
                "unit_price": 3.75,
                "line_total": 4.20,
                "source_category": "Fruit & Vegetables",
            },
            {
                "description": "Dish soap 500ml",
                "quantity": 1,
                "unit": "ea",
                "line_total": 25.80,
                "source_category": "Cleaning & Homecare",
            },
        ],
    }
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# What it accepts
# ---------------------------------------------------------------------------
def test_a_well_formed_document_is_stored_with_its_lines():
    result = documents.submit(**a_receipt())

    assert result["stored"] is True
    assert result["items"] == 3
    lines = db.document_items(result["document_id"])
    assert [line["line_no"] for line in lines] == [1, 2, 3]


def test_a_fractional_quantity_survives():
    """
    Loose produce is weighed, not counted.

    1.120 kg of bananas is the shape of the problem: an integer quantity column
    would have silently rounded it, and the line would then no longer reconcile
    against its own price.
    """
    result = documents.submit(**a_receipt())
    bananas = next(i for i in db.document_items(result["document_id"]) if i["unit"] == "kg")

    assert bananas["quantity"] == pytest.approx(1.120)
    assert bananas["line_total"] == pytest.approx(4.20)


def test_the_shops_own_category_is_kept_verbatim():
    """
    Not translated on the way in, and not required either.

    Three real documents offered three different vocabularies - broad
    departments, none at all, and finer categories than either. Mapping any of
    them to the household's own words at import would make that decision
    permanent, which is precisely what recategorise_all() exists to avoid.
    """
    result = documents.submit(**a_receipt())
    stored = {
        i["description"]: i["source_category"] for i in db.document_items(result["document_id"])
    }

    assert stored["Bananas loose"] == "Fruit & Vegetables"
    assert all(i["category"] is None for i in db.document_items(result["document_id"])), (
        "the household's own category must be assigned later, not guessed at import"
    )


def test_the_same_document_twice_is_stored_once():
    first = documents.submit(**a_receipt())
    second = documents.submit(**a_receipt())

    assert first["stored"] is True
    assert second["stored"] is False
    assert second["document_id"] == first["document_id"]
    assert len(db.documents()) == 1


def test_the_paying_cards_last_four_and_the_time_are_stored_when_given():
    """
    An in-store eReceipt prints both; an emailed invoice prints neither.

    Storing them means a later linker upgrade can use them - a bank memo
    carrying the same last-four is a much stronger match than date and amount
    alone. Throwing them away at ingest is not something a later feature could
    undo.
    """
    result = documents.submit(**a_receipt(card_last4="2561", time="17:20"))
    stored = db.document(result["document_id"])

    assert stored["card_last4"] == "2561"
    assert stored["time"] == "17:20"


def test_something_longer_than_four_digits_is_refused_as_card_last4():
    """
    The whole reason this field exists is that four digits are what a receipt
    prints and Wyrmhoard has no business holding sixteen.
    """
    with pytest.raises(ValueError, match="four printed digits"):
        documents.submit(**a_receipt(card_last4="2561111122223333"))


def test_a_time_that_is_not_24h_hhmm_is_refused():
    """
    "5pm" and "17:20:00 NZST" are things a lazy parser could hand over, and both
    make the field useless for later comparison.
    """
    with pytest.raises(ValueError, match="HH:MM"):
        documents.submit(**a_receipt(time="5pm"))


def test_a_missing_unit_price_is_allowed():
    """One document gives a unit price on every line; another only where it varies."""
    result = documents.submit(**a_receipt())
    soap = next(i for i in db.document_items(result["document_id"]) if "soap" in i["description"])
    assert soap["unit_price"] is None


def test_a_free_item_is_a_real_line():
    """
    Found by submitting a real invoice, not by imagining one.

    It carried ten units of a promotional giveaway at $0.00 each, and the first
    version of this validation refused the whole document because a unit price
    "must be positive". A free line is a purchase that happened; refusing it
    would have meant the household could not record the shop at all.
    """
    body = a_receipt()
    body["items"].append(
        {
            "description": "Promotional giveaway",
            "quantity": 10,
            "unit": "ea",
            "unit_price": 0.00,
            "line_total": 0.00,
        }
    )
    result = documents.submit(**body)

    assert result["stored"] is True
    free = next(i for i in db.document_items(result["document_id"]) if i["line_total"] == 0)
    assert free["quantity"] == 10


def test_a_discount_line_is_a_real_line():
    """Negative money on a line is a refund or a discount, and still has to close."""
    body = a_receipt(stated_total=38.00)
    body["items"].append(
        {"description": "Member discount", "quantity": 1, "unit": "ea", "line_total": -2.00}
    )
    assert documents.submit(**body)["stored"] is True


# ---------------------------------------------------------------------------
# What it refuses. These are the tests that matter.
# ---------------------------------------------------------------------------
def test_a_document_whose_lines_do_not_add_up_is_refused():
    """
    The rule that keeps retailer-specific judgement outside the core.

    One shop bills a checkout bag as a fee below the subtotal, another as a
    product inside it. There is no opinion here about which is right - only
    that the producer submits lines that close. Same principle as a payslip
    that does not balance being reported rather than stored.
    """
    body = a_receipt()
    body["items"][0]["line_total"] = 12.00  # was 10.00

    with pytest.raises(ValueError) as exc:
        documents.submit(**body)

    message = str(exc.value)
    assert "42.00" in message and "40.00" in message, message
    assert "2.00" in message, "the message must name the discrepancy, not just report one"
    assert not db.documents(), "nothing may be stored when the arithmetic fails"


def test_a_document_with_no_lines_is_refused():
    with pytest.raises(ValueError, match="no lines"):
        documents.submit(**a_receipt(items=[]))


@pytest.mark.parametrize("field", ["description", "unit"])
def test_a_line_missing_a_required_field_is_refused(field):
    body = a_receipt()
    body["items"][1][field] = "   "

    with pytest.raises(ValueError) as exc:
        documents.submit(**body)
    assert "line 2" in str(exc.value), "the message must say which line"


def test_a_zero_quantity_is_refused():
    """A line that bought nothing is a parser bug, not a purchase."""
    body = a_receipt()
    body["items"][0]["quantity"] = 0

    with pytest.raises(ValueError) as exc:
        documents.submit(**body)
    assert "quantity on line 1" in str(exc.value), "the message must say which line"


def test_an_unattributed_document_is_refused():
    """
    Same rule as every other write: data with no stated origin is the thing the
    producer contract exists to prevent.
    """
    with pytest.raises(ValueError):
        documents.submit(**a_receipt(producer="woolworths"))


def test_a_document_dated_today_by_default_is_impossible():
    """`observed_at` is never defaulted - a receipt is about the day it was issued."""
    body = a_receipt()
    body.pop("observed_at")

    with pytest.raises(TypeError):
        documents.submit(**body)


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        documents.submit(**a_receipt(kind="shopping list"))


# ---------------------------------------------------------------------------
# Linking, which is inference and says so
# ---------------------------------------------------------------------------
def _transaction(fingerprint: str, when: str, amount: float, memo: str) -> None:
    db.insert_transactions(
        [
            {
                "fingerprint": fingerprint,
                "account": "Everyday",
                "date": when,
                "memo": memo,
                "amount": amount,
                "balance": None,
                "category": "groceries",
                "grp": "essential",
                "categorised_by": "test",
            }
        ],
        source_file="test.csv",
    )


def test_a_document_links_to_the_transaction_that_paid_for_it():
    _transaction("fp-match", "2026-08-14", -40.00, "EXAMPLE SUPERMARKET")
    result = documents.submit(**a_receipt())

    link = documents.link(result["document_id"])
    assert link["linked"] is True
    assert link["fingerprint"] == "fp-match"
    assert link["confidence"] == "high"


def test_two_equally_good_matches_are_reported_rather_than_guessed():
    """
    Two shops at the same supermarket on one day, for the same money.

    Picking one silently attaches a receipt to the wrong shop, and nothing
    downstream would ever show it. So nothing is chosen and both are named -
    the pattern properties.summary() uses for a housing conflict.
    """
    _transaction("fp-one", "2026-08-14", -40.00, "EXAMPLE SUPERMARKET")
    _transaction("fp-two", "2026-08-14", -40.00, "EXAMPLE SUPERMARKET KG")
    result = documents.submit(**a_receipt())

    link = documents.link(result["document_id"])
    assert link["linked"] is False
    assert {c["fingerprint"] for c in link["candidates"]} == {"fp-one", "fp-two"}
    assert "say which one" in link["reason"]


def test_a_document_with_no_matching_transaction_says_what_is_missing():
    """
    A receipt for an account that was never imported. Not an error - the
    document is still worth having, and the reason names the likely cause.
    """
    result = documents.submit(**a_receipt())
    link = documents.link(result["document_id"])

    assert link["linked"] is False
    assert "may not be imported" in link["reason"]


def test_the_listing_does_not_carry_product_names():
    """
    Minimisation by shape, not by policy.

    A bank line names a supermarket; a receipt names a medication. The listing
    is what a dashboard or an agent reaches for first, so reading descriptions
    has to be a separate, deliberate call.
    """
    documents.submit(**a_receipt())
    body = str(documents.summary())

    assert "Blue milk" not in body
    assert "Dish soap" not in body
