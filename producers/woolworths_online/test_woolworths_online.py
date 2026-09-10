"""
Parsing a Woolworths online invoice.

Every fixture below is the shape of a line from a real invoice with the product
names changed. The awkward ones are not hypothetical: the weighed produce, the
substitution sharing a ref, the description wrapping onto its own line and the
free promotional item all appeared in the first real document this was tested
against, and each of them broke a version of this parser.

`parse()` takes text rather than a PDF so these run without one. Reading the
PDF is three lines of pdfplumber and has nothing to get wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from woolworths_online import PRODUCER, parse

INVOICE = """\
Tax Invoice/Receipt
All amounts Exclude GST
GENERAL DISTRIBUTORS LIMITED Order Confirmation/Invoice Number CD49983141
Date 28 Jul, 2026
Page 1 of 3
Deliver To:
A Person
Ref Description Order No/Item No Ordered Supplied Unit Price Amount
Fulfilled By: WW Kelvin Grove 53038121
Deli & Chilled Foods
6 Example cheese 1kg block 1 ea 1 ea $16.49/ea $16.49
11 Example milk full cream 2l 2 ea 1 ea $5.35/ea $5.35
11 (Sub) Substitute milk 0 ea 1 ea $7.58/ea $5.35
farmhouse- full cream 2l bottle
Fruit & Vegetables
7 Example apples loose 2.000 kg 2.020 kg $3.50/kg $7.07
Toys
21 Example giveaway pack 10 ea 10 ea $0.00/ea $0.00
Sub Total $34.26
+ Paper Bags $1.50
+ Pick up Fee $3.50
Invoice Total $39.26
"""


def test_the_lines_add_up_to_the_stated_total():
    """
    The one rule Wyrmhoard enforces, so the producer had better satisfy it.

    Fees are submitted as lines because the core has no opinion about whether a
    checkout bag is a product or a fee - only that the arithmetic closes. That
    decision belongs here, with the shop that made it.
    """
    doc = parse(INVOICE)
    summed = round(sum(i["line_total"] for i in doc["items"]), 2)

    assert summed == doc["stated_total"] == 39.26


def test_the_header_supplies_identity_and_date():
    doc = parse(INVOICE)

    assert doc["producer"] == PRODUCER
    assert doc["reference"] == "CD49983141"
    assert doc["observed_at"] == "2026-07-28"
    assert doc["merchant"] == "WW Kelvin Grove"


def test_weighed_produce_keeps_its_fraction_and_its_unit():
    """Ordered 2.000 kg, 2.020 kg arrived, charged for what arrived."""
    apples = next(i for i in parse(INVOICE)["items"] if "apples" in i["description"])

    assert apples["quantity"] == pytest.approx(2.020)
    assert apples["unit"] == "kg"
    assert apples["line_total"] == pytest.approx(7.07)


def test_a_short_supplied_line_uses_what_arrived():
    """
    Two ordered, one supplied, one charged for.

    Submitting the ordered quantity would overstate what the household has, and
    the line would stop agreeing with its own price.
    """
    milk = next(
        i for i in parse(INVOICE)["items"] if i["description"] == "Example milk full cream 2l"
    )

    assert milk["quantity"] == 1
    assert "2 ea 1 ea" in milk["raw"], "the ordered figure stays recoverable in the raw line"


def test_a_substitution_is_its_own_line():
    """
    A substitute shares the ref of the item it replaced and is listed at its own
    price but charged at the original's. Two lines, not one, because that is
    what the household was billed for and both products entered the house.
    """
    items = parse(INVOICE)["items"]
    sub = next(i for i in items if i["description"].startswith("(Sub)"))

    assert sub["unit_price"] == pytest.approx(7.58)
    assert sub["line_total"] == pytest.approx(5.35)


def test_a_wrapped_description_joins_the_line_above_it():
    """
    "farmhouse- full cream 2l bottle" is not a product. It is the rest of the
    line before it, and an earlier version of this parser treated it as a
    department heading, which quietly filed everything after it wrongly.
    """
    sub = next(i for i in parse(INVOICE)["items"] if i["description"].startswith("(Sub)"))

    assert sub["description"].endswith("farmhouse- full cream 2l bottle")
    assert not any(
        i["source_category"] == "farmhouse- full cream 2l bottle" for i in parse(INVOICE)["items"]
    )


def test_the_page_footer_is_not_glued_onto_the_last_item():
    """
    The footer wraps mid-sentence, so its continuation lines start lower-case
    exactly like a wrapped product description does.

    Against the real invoice this produced a line called "Pick up Fee refund.
    Find Olive at https://…" and another ending "224pk refund". The document
    still reconciled - the amounts were untouched - so nothing downstream would
    ever have flagged it. Only the description was quietly wrong.
    """
    footer = (
        "\nWoolworths own virtual assistant Olive is here to help! She can assist you with a\n"
        "refund. Find Olive at https://woolworths.co.nz/contact\n"
    )
    items = parse(INVOICE + footer)["items"]

    assert all("Olive" not in i["description"] for i in items), [
        i["description"] for i in items if "Olive" in i["description"]
    ]
    assert next(i for i in items if i["description"] == "Pick up Fee")


def test_a_free_item_survives():
    """
    Ten units at $0.00. The first version of Wyrmhoard's own validation refused
    the whole document over this, which is why it is pinned at both ends.
    """
    free = next(i for i in parse(INVOICE)["items"] if i["line_total"] == 0)

    assert free["quantity"] == 10
    assert free["unit_price"] == 0.0


def test_the_shops_departments_are_carried_verbatim():
    """Not translated - Wyrmhoard stores them as the shop wrote them."""
    items = parse(INVOICE)["items"]
    by_description = {i["description"][:14]: i["source_category"] for i in items}

    assert by_description["Example cheese"] == "Deli & Chilled Foods"
    assert by_description["Example apples"] == "Fruit & Vegetables"


def test_fees_are_lines_without_a_department():
    fees = [i for i in parse(INVOICE)["items"] if i["description"] in ("Paper Bags", "Pick up Fee")]

    assert len(fees) == 2
    assert all(f["source_category"] is None for f in fees)


@pytest.mark.parametrize(
    "missing,expected",
    [
        ("Invoice Total $39.26", "Invoice Total"),
        ("Date 28 Jul, 2026", "Date"),
    ],
)
def test_a_document_missing_something_essential_is_refused(missing, expected):
    """
    A producer that submits a half-read document is worse than one that
    refuses. Wyrmhoard would catch a missing total by arithmetic, but the
    message would describe a discrepancy rather than a parse failure.
    """
    with pytest.raises(ValueError, match=expected):
        parse(INVOICE.replace(missing, ""))


def test_something_that_is_not_an_invoice_is_refused():
    with pytest.raises(ValueError, match="No item lines"):
        parse("Dear customer,\n\nThank you for shopping with us.\n")
