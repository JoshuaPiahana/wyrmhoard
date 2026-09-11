"""
Read a Woolworths NZ in-store eReceipt and submit it to Wyrmhoard.

Different producer to the online invoice because it is a different format. No
departments, no ordered-vs-supplied columns, no substitutions - and two things
the online invoice never has: the paying card's last four digits and the time
the transaction was authorised. Both go on the document, in case the linker
ever grows to use them.

    python woolworths_instore.py receipt.pdf
    python woolworths_instore.py receipt.pdf --dry-run    # print, submit nothing

It talks to two things: the PDF you hand it, and http://localhost:8080. Nothing
else, ever. Fetching the receipt is your job - the Woolworths app has a share
button on each order in the *Woolworths* app (not the Everyday Rewards one).

## The shapes an item line arrives in

Three, and the parser has to tell them apart:

    ^Edmonds Flour High Grade 5kg 10.00

A single-quantity item on one line. Description then amount. A leading `^` or
`*` is a printed marker whose legend the receipt does not include - stripped
from the description and preserved in `raw` so the meaning is recoverable if
somebody later finds out what it means.

    Banana Cavendish
    1.120 kg NET @ $3.75/kg 4.20

Weighed produce. The description is one line, the quantity, unit, unit price
and amount are the next. A parser that treated the description alone as an
item would look at the second line and hallucinate a product called "1.120 kg
NET".

    LRC Lactose Free A2 Protein Milk 1.5L
    Qty 2 @ $8.06 each 16.12

Two or more of the same thing. Same two-line shape, different continuation.

## What is not here

- Departments. Every online line came with one; every in-store line comes with
  none. `source_category` is None on every item.
- Fees. Bag charges and pick-up fees are the online format. An in-store
  receipt goes straight from items to subtotal.
- Substitutions. You picked the products yourself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

PRODUCER = "tool:woolworths-instore"
DEFAULT_API = "http://localhost:8080/api"

# The header line that carries the store name. "Woolworths Kelvin Grove PH: …".
STORE = re.compile(r"^Woolworths\s+(?P<store>.+?)\s+PH:")

# The bottom-of-receipt line with the four-digit year. Preferred over the
# in-EFTPOS "08/09/26 17:20 006990" because DD/MM/YY is ambiguous the moment
# somebody imports one from another country and a full year is not.
FOOTER = re.compile(
    r"^POS\s+\d+\s+TRANS\s+(?P<reference>\d+)\s+(?P<time>\d{2}:\d{2})\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})$"
)

# The card block prints the last four twice: once masked in "CARD:.......2561"
# and once bare in "X-2561 $228.62". The bare version is what the EFTPOS
# terminal actually printed and is the more reliable of the two.
CARD_LAST4 = re.compile(r"^X-(?P<last4>\d{4})\s+\$")

# The item count sits on the subtotal line. "28 SUBTOTAL $228.62". End of the
# item section - everything after is EFTPOS chrome and a marketing footer.
SUBTOTAL = re.compile(r"^(?P<count>\d+)\s+SUBTOTAL\s+\$(?P<amount>\d+\.\d{2})$")

# A whole item on one line: description, then dollars-and-cents at the end.
SINGLE = re.compile(r"^(?P<description>.+?)\s+(?P<amount>\d+\.\d{2})$")

# The continuation of a multi-quantity item. "Qty 2 @ $8.06 each 16.12".
MULTI = re.compile(
    r"^Qty\s+(?P<quantity>\d+)\s+@\s+\$(?P<unit_price>[\d.]+)\s+each\s+"
    r"(?P<amount>\d+\.\d{2})$"
)

# The continuation of a weighed item. "1.120 kg NET @ $3.75/kg 4.20".
WEIGHED = re.compile(
    r"^(?P<quantity>[\d.]+)\s+kg\s+NET\s+@\s+\$(?P<unit_price>[\d.]+)/kg\s+"
    r"(?P<amount>\d+\.\d{2})$"
)

# A printed marker on some descriptions. The receipt does not include a legend
# for it, so it is stripped and preserved in `raw` rather than stored as
# meaningful. See the module docstring.
MARKER = re.compile(r"^[\^*]+")


def read_pdf(path: Path) -> str:
    """The receipt as text. Split out so parsing can be tested without a PDF."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def parse(text: str) -> dict[str, Any]:
    """
    Turn receipt text into a Wyrmhoard document.

    Raises ValueError naming what was missing. A producer that submits a half-
    read receipt is worse than one that refuses to - the resulting document
    would balance against a wrong total or link to the wrong transaction.
    """
    store = reference = time = card_last4 = None
    issued = None
    stated_total: float | None = None
    stated_count: int | None = None
    items: list[dict[str, Any]] = []
    # A description on a line by itself waits for its quantity/price line to
    # arrive next. Emitted then, not before.
    pending: str | None = None

    in_items = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if store is None and (m := STORE.match(line)):
            store = "Woolworths " + m.group("store")
            continue

        if not in_items:
            # "Description $" is the header of the item section. Everything
            # before it - the store name, the GST number, the tax invoice
            # boilerplate - is not item data.
            if line == "Description $":
                in_items = True
            continue

        if m := SUBTOTAL.match(line):
            stated_total = float(m.group("amount"))
            stated_count = int(m.group("count"))
            if pending is not None:
                raise ValueError(
                    f"The item section ended with a dangling description line: {pending!r}."
                )
            break

        if m := MULTI.match(line):
            if pending is None:
                raise ValueError(f"A 'Qty' line arrived with no description above it: {line!r}.")
            items.append(
                _item(
                    pending,
                    quantity=float(m.group("quantity")),
                    unit="ea",
                    unit_price=float(m.group("unit_price")),
                    amount=float(m.group("amount")),
                    raw=f"{pending}\n{line}",
                )
            )
            pending = None
            continue

        if m := WEIGHED.match(line):
            if pending is None:
                raise ValueError(f"A weighed line arrived with no description above it: {line!r}.")
            items.append(
                _item(
                    pending,
                    quantity=float(m.group("quantity")),
                    unit="kg",
                    unit_price=float(m.group("unit_price")),
                    amount=float(m.group("amount")),
                    raw=f"{pending}\n{line}",
                )
            )
            pending = None
            continue

        if m := SINGLE.match(line):
            if pending is not None:
                # A description with no continuation followed by a full item is
                # a parse failure rather than a purchase - either we misread the
                # first line or a real receipt has a shape not covered here.
                raise ValueError(
                    f"Description {pending!r} had no quantity/price line following it "
                    f"before the next item ({line!r}) started."
                )
            items.append(
                _item(
                    line[: m.start("amount")].strip(),
                    quantity=1,
                    unit="ea",
                    unit_price=None,
                    amount=float(m.group("amount")),
                    raw=line,
                )
            )
            continue

        # A line with no trailing amount is a description waiting for its
        # continuation. Concatenate if a pending is already open - the receipts
        # seen so far do not wrap descriptions over two lines, but doing so
        # keeps the descriptions readable rather than dropping one on the floor.
        pending = f"{pending} {line}" if pending else line

    # The EFTPOS block and the footer live after the subtotal. Card last four
    # and the time-with-full-year are both there. Scanning the whole document
    # for them is fine - they are unambiguous strings.
    for line in text.splitlines():
        line = line.strip()
        if card_last4 is None and (m := CARD_LAST4.match(line)):
            card_last4 = m.group("last4")
        if reference is None and (m := FOOTER.match(line)):
            reference = m.group("reference")
            time = m.group("time")
            issued = datetime.strptime(m.group("date"), "%d/%m/%Y").date()

    if not items:
        raise ValueError("No item lines found. Is this a Woolworths in-store eReceipt?")
    if stated_total is None:
        raise ValueError("No 'SUBTOTAL' line found, so there is nothing to check against.")
    if issued is None:
        raise ValueError(
            "No 'POS … TRANS …' footer found. A document has to say what day it is about."
        )

    # The receipt's SUBTOTAL line prefixes the total with a unit count: "28
    # SUBTOTAL $228.62" is 28 things through the till, not 28 lines. Two ea of
    # something count as two, a weighed line counts as one. Cross-checking it
    # against the parsed items catches a mistaken split or join that the
    # arithmetic alone would not, because Woolworths does not print the count
    # for decoration.
    counted = sum(int(i["quantity"]) if i["unit"] == "ea" else 1 for i in items)
    if stated_count != counted:
        raise ValueError(
            f"The receipt says {stated_count} items went through the till but the parser "
            f"found {counted} (across {len(items)} lines). One of them is wrong and the "
            f"arithmetic closing does not settle it - a split or joined line can add up."
        )

    return {
        "producer": PRODUCER,
        "kind": "receipt",
        "merchant": store or "Woolworths",
        "observed_at": issued.isoformat(),
        "time": time,
        "card_last4": card_last4,
        "reference": reference,
        "stated_total": stated_total,
        "currency": "NZD",
        "confidence": "high",
        "items": items,
        "extra": {"format": "woolworths-nz in-store ereceipt"},
    }


def _item(
    description: str,
    *,
    quantity: float,
    unit: str,
    unit_price: float | None,
    amount: float,
    raw: str,
) -> dict[str, Any]:
    """One item, with any leading `^`/`*` marker stripped and stashed in raw."""
    stripped = MARKER.sub("", description).strip()
    return {
        "description": stripped,
        "quantity": quantity,
        "unit": unit,
        "unit_price": unit_price,
        "line_total": amount,
        # In-store receipts do not print a department. Storing None honestly is
        # better than guessing at one.
        "source_category": None,
        "raw": raw,
    }


def submit(document: dict[str, Any], api: str = DEFAULT_API) -> dict[str, Any]:
    """POST it, and pass Wyrmhoard's refusal straight through if it refuses."""
    request = urllib.request.Request(
        f"{api}/documents",
        data=json.dumps(document).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = json.load(exc).get("detail", exc.reason)
        raise SystemExit(f"Wyrmhoard refused this receipt:\n  {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Cannot reach Wyrmhoard at {api} - is it running? ({exc.reason})"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("receipt", type=Path, help="the eReceipt PDF")
    parser.add_argument("--api", default=DEFAULT_API, help=f"default {DEFAULT_API}")
    parser.add_argument("--dry-run", action="store_true", help="parse and print, submit nothing")
    args = parser.parse_args(argv)

    if not args.receipt.exists():
        print(f"No file at {args.receipt}", file=sys.stderr)
        return 1

    document = parse(read_pdf(args.receipt))
    summed = round(sum(i["line_total"] for i in document["items"]), 2)
    print(
        f"{args.receipt.name}: {len(document['items'])} lines summing {summed:.2f}, "
        f"receipt states {document['stated_total']:.2f}, dated {document['observed_at']} "
        f"at {document['time']} on card ending {document['card_last4']}"
    )

    if args.dry_run:
        for item in document["items"]:
            print(f"  {item['description'][:56]:<58}{item['line_total']:>8.2f}")
        return 0

    result = submit(document, api=args.api)
    if not result.get("stored"):
        print("Already recorded - nothing to do.")
        return 0

    link = result.get("link", {})
    print(f"Stored {result['items']} lines.")
    print(
        f"  linked to {link.get('memo')} on {link.get('date')} "
        f"({link.get('confidence')} confidence)"
        if link.get("linked")
        else f"  not linked: {link.get('reason')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
