"""
Parsing a Woolworths NZ in-store eReceipt.

The fixture below is the shape of a real receipt with the product names
changed. The awkward bits - the two-line weighed and multi-quantity items, the
undocumented `^`/`*` prefix markers, the header line that carries the store,
the footer line that carries the date-and-time - all appeared in the first
real receipt this was tested against.

`parse()` takes text rather than a PDF so these run without one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from woolworths_instore import PRODUCER, parse

RECEIPT = """\
Woolworths Kelvin Grove PH: 06 356 6067
7 Fernlea Avenue
TAX INVOICE - Tax Invoice/Credit Note - GST No.
44-833-938
Description $
Example fruit
1.120 kg NET @ $3.75/kg 4.20
^Example flour 5kg 10.00
*Example ready meal 400g 9.00
Example spread 500g
Qty 2 @ $8.06 each 16.12
5 SUBTOTAL $39.32
TOTAL $39.32
-------------------------
WOOLWORTHS NZ 9424
KELVIN GROVE NZ
Visa Debit
08/09/26 17:20 006990
CARD:.............2561 T
PURCHASE $39.32
------------
TOTAL $39.32
APPROVED 00
-------------------------
X-2561 $39.32
#Taxable Items
TOTAL includes GST $5.13
POS 001 TRANS 6990 17:20 08/09/2026
"""


def test_the_lines_add_up_to_the_stated_total():
    """
    The one rule Wyrmhoard enforces, so the producer had better satisfy it.
    """
    doc = parse(RECEIPT)
    summed = round(sum(i["line_total"] for i in doc["items"]), 2)

    assert summed == doc["stated_total"] == 39.32


def test_the_header_and_footer_supply_identity_date_and_card():
    """
    Store from the top line, date and time and reference from the footer, card
    last four from the EFTPOS X-line - all five sit in different places on the
    document and any of them missing is a refusal.
    """
    doc = parse(RECEIPT)

    assert doc["producer"] == PRODUCER
    assert doc["merchant"] == "Woolworths Kelvin Grove"
    assert doc["observed_at"] == "2026-09-08"
    assert doc["time"] == "17:20"
    assert doc["card_last4"] == "2561"
    assert doc["reference"] == "6990"


def test_a_weighed_line_keeps_its_fraction_and_its_unit():
    """1.120 kg of fruit, priced per kg. Not 1 ea, not rounded to a whole kg."""
    fruit = next(i for i in parse(RECEIPT)["items"] if "fruit" in i["description"].lower())

    assert fruit["quantity"] == pytest.approx(1.120)
    assert fruit["unit"] == "kg"
    assert fruit["unit_price"] == pytest.approx(3.75)
    assert fruit["line_total"] == pytest.approx(4.20)


def test_a_multi_quantity_line_records_the_unit_price():
    """Qty 2 @ $8.06 each, and the line total is what was charged for both."""
    spread = next(i for i in parse(RECEIPT)["items"] if "spread" in i["description"].lower())

    assert spread["quantity"] == 2
    assert spread["unit"] == "ea"
    assert spread["unit_price"] == pytest.approx(8.06)
    assert spread["line_total"] == pytest.approx(16.12)


def test_a_single_quantity_line_has_no_unit_price():
    """
    The receipt does not print one, and inventing `line_total / 1` would look
    like a piece of retailer data when it was arithmetic done here.
    """
    flour = next(i for i in parse(RECEIPT)["items"] if "flour" in i["description"].lower())

    assert flour["quantity"] == 1
    assert flour["unit"] == "ea"
    assert flour["unit_price"] is None
    assert flour["line_total"] == 10.00


def test_the_prefix_markers_are_stripped_but_survive_in_raw():
    """
    `^` and `*` appear on some descriptions with no printed legend. Stripped
    from the description so a match on "Example flour" works, preserved in raw
    so the meaning is recoverable if we ever find out what they mean.
    """
    items = parse(RECEIPT)["items"]
    flour = next(i for i in items if "flour" in i["description"].lower())
    ready = next(i for i in items if "ready meal" in i["description"].lower())

    assert flour["description"] == "Example flour 5kg"
    assert ready["description"] == "Example ready meal 400g"
    assert flour["raw"].startswith("^Example flour")
    assert ready["raw"].startswith("*Example ready meal")


def test_no_line_carries_a_source_category():
    """An in-store receipt does not print departments. None, honestly."""
    assert all(i["source_category"] is None for i in parse(RECEIPT)["items"])


def test_a_dangling_description_with_no_continuation_is_refused():
    """
    A description on a line by itself has to be followed by a Qty or weighed
    line. If the next line is a full item instead, either we misread the first
    or the receipt has a shape not covered here - either way, refusing beats
    hallucinating a product.
    """
    broken = RECEIPT.replace("Qty 2 @ $8.06 each 16.12", "^Example other item 16.12")

    with pytest.raises(ValueError, match="had no quantity/price line"):
        parse(broken)


def test_a_continuation_with_no_description_above_it_is_refused():
    """A `Qty` line without a description above it is a parser bug, not a purchase."""
    broken = RECEIPT.replace("Example spread 500g\n", "")

    with pytest.raises(ValueError, match="no description above"):
        parse(broken)


@pytest.mark.parametrize(
    "missing,expected",
    [
        ("5 SUBTOTAL $39.32", "SUBTOTAL"),
        ("POS 001 TRANS 6990 17:20 08/09/2026", "TRANS"),
    ],
)
def test_a_receipt_missing_something_essential_is_refused(missing, expected):
    with pytest.raises(ValueError, match=expected):
        parse(RECEIPT.replace(missing, ""))


def test_a_unit_count_that_does_not_match_the_parsed_items_is_refused():
    """
    The receipt prints "5 SUBTOTAL $39.32" - five units through the till, not
    five lines. Two ea of something counts as two, a weighed line as one. If
    the count disagrees with what the parser found, a split or joined line has
    passed unnoticed - the arithmetic can still close on it - and refusing is
    the only way to catch it.
    """
    wrong = RECEIPT.replace("5 SUBTOTAL $39.32", "6 SUBTOTAL $39.32")

    with pytest.raises(ValueError, match="6 items went through the till"):
        parse(wrong)


def test_something_that_is_not_a_receipt_is_refused():
    with pytest.raises(ValueError, match="No item lines"):
        parse("Dear customer,\n\nThank you for shopping with us.\n")
