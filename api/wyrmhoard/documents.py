"""
Documents that itemise a transaction, and the shape they have to arrive in.

The bank says $315.73 left the account on 28 July. A receipt says what that
bought: forty-two lines, several categories, one of them a bag of apples
weighing 2.02kg. That detail is the only way to answer "how much of our
supermarket spend is actually alcohol", which no amount of bank data can.

**The core does not parse receipts.** A producer reads whatever its source
emits and submits the result. That is not squeamishness about PDFs - it is the
release-cadence argument that put jurisdiction rules outside too. A supermarket
redesigns its receipt on its own schedule, and a parser living in here would
mean cutting a release of a household finance tool because a shop changed its
letterhead.

So this module's whole job is the other half: **define the shape, check it, and
refuse what does not fit.** Three real documents from two retailers were read
before it was written, and they agreed on remarkably little:

    online invoice   departments, ordered-vs-supplied columns, substitutions,
                     a header claiming amounts exclude GST when they include it
    in-store receipt no departments, card last four, a timestamp, quantity on a
                     continuation line, GST stated correctly
    a third site     finer categories than either, SKUs, no payment detail, and
                     a total it will only call "estimated"

Four fields per line survived that comparison - description, quantity, unit,
line total - and those are what is required. Everything else is optional,
stored verbatim, or left in `extra` for whoever finds a use for it.

One rule does the real work: **the lines must add up to the total the document
states.** That is what keeps retailer-specific judgement outside. One shop
bills a checkout bag as a fee below the subtotal, another as a product inside
it; the core has no opinion about which is right, only that the arithmetic
closes. A document that does not balance is reported and not stored, exactly as
a payslip that does not balance already is.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from . import db, provenance

# Four printed digits, no more, no less. A card number is 16 and Wyrmhoard has
# no business holding one.
_CARD_LAST4 = re.compile(r"^\d{4}$")
# 24-hour HH:MM. Refusing a stray timezone or seconds keeps this a piece of
# printed identifying detail and not a synthesised timestamp.
_TIME_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

#: What a document can be. Deliberately short - a new kind should be a
#: considered addition, not a free-text field that accumulates typos.
KINDS = ("receipt", "invoice", "statement")

#: Money must reconcile to the cent. A tolerance here would be a slow leak: the
#: first rounding fudge becomes the excuse for the second, and the check that
#: makes this table trustworthy stops meaning anything.
CENT = 0.005


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _require(value: Any, what: str, line_no: int | None = None) -> str:
    text = _clean(value)
    if not text:
        where = f" on line {line_no}" if line_no else ""
        raise ValueError(f"Every document needs {what}{where}.")
    return text


def check_item(raw: dict[str, Any], line_no: int) -> dict[str, Any]:
    """
    One line, checked.

    Quantity may be fractional - loose bananas arrive as 1.120 kg - so it is
    validated as an amount rather than a count. Unit is required but not
    constrained to a list: `ea` and `kg` cover what has been seen, and a source
    that says `pack` or `bunch` is describing the world accurately.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"Line {line_no} is not an object.")

    # check_amount already refuses zero and negatives, and names the line in
    # its message - a line that bought nothing is a parser bug, not a purchase.
    quantity = provenance.check_amount(raw.get("quantity"), what=f"quantity on line {line_no}")
    unit_price = raw.get("unit_price")
    return {
        "line_no": line_no,
        "description": _require(raw.get("description"), "a description", line_no),
        "quantity": quantity,
        "unit": _require(raw.get("unit"), "a unit (ea, kg, …)", line_no),
        # Zero and negative are both real. A promotional giveaway is priced at
        # 0.00 and still occupies a line with a quantity of ten; a discount
        # line is negative. Only quantity has to be strictly positive, because
        # a line that bought nothing is a parser fault rather than a purchase.
        "unit_price": (
            provenance.check_amount(
                unit_price, what=f"unit price on line {line_no}", allow_negative=True
            )
            if unit_price is not None
            else None
        ),
        # Negative is allowed: a discount or a refunded line is money coming
        # back, and a document that contains one still has to add up.
        "line_total": provenance.check_amount(
            raw.get("line_total"), what=f"line total on line {line_no}", allow_negative=True
        ),
        "source_category": _clean(raw.get("source_category")),
        "raw": _clean(raw.get("raw")),
        "extra": json.dumps(raw["extra"]) if raw.get("extra") else None,
    }


def submit(
    producer: str,
    kind: str,
    merchant: str,
    observed_at: str,
    stated_total: Any,
    items: list[dict[str, Any]],
    currency: str = "NZD",
    reference: str | None = None,
    source: str | None = None,
    confidence: str | None = None,
    card_last4: str | None = None,
    time: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Store one document and its lines, or refuse it and say why.

    Refusing is the point. Every message here is written for whoever has to fix
    it - a person reading it in the dashboard, or a producer author passing it
    on - so "the lines add up to $310.73 but the document says $315.73" rather
    than "validation error".

    Submitting the same document twice stores it once. The fingerprint is built
    from the producer, the date, the reference and the total, so a producer that
    runs nightly over the same mailbox does not multiply its own history.
    """
    producer = provenance.check_producer(producer)
    observed = provenance.check_observed_at(observed_at, what="document")
    confidence = provenance.check_confidence(confidence)

    if card_last4 is not None and not _CARD_LAST4.match(str(card_last4)):
        raise ValueError(
            f"`card_last4` is the four printed digits of the paying card, got {card_last4!r}. "
            f"Wyrmhoard is not a place for a full card number."
        )
    if time is not None and not _TIME_HHMM.match(str(time)):
        raise ValueError(f"`time` is 24-hour HH:MM as printed on the document, got {time!r}.")

    if kind not in KINDS:
        raise ValueError(f"`kind` must be one of {', '.join(KINDS)} - got {kind!r}.")
    merchant = _require(merchant, "a merchant")
    total = provenance.check_amount(stated_total, what="stated total", allow_negative=True)

    if not items:
        raise ValueError("A document with no lines is not a document.")
    checked = [check_item(raw, i) for i, raw in enumerate(items, 1)]

    summed = round(sum(item["line_total"] for item in checked), 2)
    if abs(summed - total) > CENT:
        raise ValueError(
            f"The {len(checked)} lines add up to {summed:.2f} but the document states "
            f"{total:.2f}, a difference of {abs(summed - total):.2f}. Fees, bags and "
            f"discounts have to be submitted as lines - the core has no opinion about "
            f"which of those is a product, only that the arithmetic closes."
        )

    fp = provenance.fingerprint(producer, observed, reference or merchant, f"{total:.2f}")
    stored = db.add_document(
        {
            "kind": kind,
            "merchant": merchant,
            "reference": _clean(reference),
            "observed_at": observed,
            "received_at": provenance.received_now(),
            "producer": producer,
            "source": _clean(source),
            "confidence": confidence,
            "stated_total": total,
            "currency": _require(currency, "a currency"),
            "card_last4": _clean(card_last4),
            "time": _clean(time),
            "extra": json.dumps(extra) if extra else None,
            "fingerprint": fp,
        },
        checked,
    )
    return {**stored, "items": len(checked), "merchant": merchant, "observed_at": observed}


def link(document_id: int, as_at: date | None = None) -> dict[str, Any]:
    """
    Work out which transaction a document belongs to, and say how sure that is.

    Matching on date and amount is inference, and inference gets recorded with
    its method rather than applied silently. Where two transactions fit equally
    well - two shops at the same supermarket on one day, for the same money -
    nothing is chosen. The candidates are reported and a person decides, which
    is the pattern properties.summary() already uses for a housing conflict.
    """
    doc = db.document(document_id)
    if doc is None:
        raise ValueError(f"No document with id {document_id}.")

    candidates = db.transactions_matching(
        amount=-abs(doc["stated_total"]),
        observed_at=doc["observed_at"],
        window_days=3,
    )
    if not candidates:
        return {
            "linked": False,
            "reason": (
                f"No transaction of {doc['stated_total']:.2f} within three days of "
                f"{doc['observed_at']}. The account it was paid from may not be imported."
            ),
            "candidates": [],
        }

    if len(candidates) > 1:
        return {
            "linked": False,
            "reason": (
                f"{len(candidates)} transactions match {doc['stated_total']:.2f} near "
                f"{doc['observed_at']}. Nothing has been linked - say which one."
            ),
            "candidates": [
                {"fingerprint": c["fingerprint"], "date": c["date"], "memo": c["memo"]}
                for c in candidates
            ],
        }

    match = candidates[0]
    exact = match["date"] == doc["observed_at"]
    db.link_document(
        document_id,
        match["fingerprint"],
        method="amount and date" if exact else "amount, date within three days",
        confidence="high" if exact else "medium",
        decided_at=(as_at or date.today()).isoformat(),
    )
    return {
        "linked": True,
        "fingerprint": match["fingerprint"],
        "date": match["date"],
        "memo": match["memo"],
        "confidence": "high" if exact else "medium",
    }


def summary(limit: int | None = None) -> dict[str, Any]:
    """
    Every document, newest first, with what it cost and whether it is linked.

    Deliberately does not return line descriptions. Product names are the most
    revealing thing this database will ever hold - a bank line says a
    supermarket, a receipt says which medication - so reading them is a
    separate, deliberate call.
    """
    rows = db.documents(limit=limit)
    return {
        "documents": rows,
        "count": len(rows),
        "unlinked": sum(1 for r in rows if not r.get("fingerprint")),
    }
