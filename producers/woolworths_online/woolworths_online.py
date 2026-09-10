"""
Read a Woolworths NZ online-order invoice and submit it to Wyrmhoard.

This is a producer. It lives outside `api/wyrmhoard/` on purpose: a supermarket
redesigns its invoice on its own schedule, and a parser for that layout in the
core would mean releasing a household finance tool because a shop changed its
letterhead. The core defines the shape a document arrives in and refuses what
does not fit; knowing what a particular PDF looks like is this program's job.

    python woolworths_online.py invoice.pdf
    python woolworths_online.py invoice.pdf --dry-run     # print, submit nothing

It talks to two things: the PDF you hand it, and http://localhost:8080. Nothing
else, ever.

## What it has to cope with

The format is more awkward than it looks, and every one of these came from a
real invoice rather than from imagination:

    6 Alpine cheese colby 1kg block 1 ea 1 ea $16.49/ea $16.49

Columns are Ref, Description, Ordered, Supplied, Unit Price, Amount - and the
description contains spaces, so the columns can only be found from the right.

    7 Fresh fruit apples granny smith loose 2.000 kg 2.020 kg $3.50/kg $7.07

Loose produce is weighed. Ordered 2.000 kg, supplied 2.020 kg, charged for what
arrived. Quantity is fractional and the unit is not always `ea`.

    11 Woolworths milk full cream 4% milk fat 2l 2 ea 1 ea $5.35/ea $5.35
    11 (Sub) Meadow fresh milk 0 ea 1 ea $7.58/ea $5.35
    farmhouse- full cream 2l bottle

Two of the two milks ordered arrived as one plus a substitute, sharing a ref.
The substitute is listed at its own price and charged at the original's. And
the description wraps onto a line of its own, which is not an item.

    Sub Total $310.73
    + Paper Bags $1.50
    + Pick up Fee $3.50
    Invoice Total $315.73

Fees sit below the subtotal. Wyrmhoard requires the lines to add up to the
stated total and has no opinion about whether a bag is a product or a fee, so
they are submitted as lines. That decision belongs here, with the shop that
made it.
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

PRODUCER = "tool:woolworths-online"
DEFAULT_API = "http://localhost:8080/api"

# Read from the right: quantity, unit, quantity, unit, unit price, amount. The
# description is whatever is left between the ref number and the first quantity,
# which is the only way to allow spaces in a product name.
ITEM = re.compile(
    r"^(?P<ref>\d+)\s+(?P<description>.*?)\s+"
    r"(?P<ordered>[\d.]+)\s*(?P<ordered_unit>ea|kg)\s+"
    r"(?P<supplied>[\d.]+)\s*(?P<unit>ea|kg)\s+"
    r"\$(?P<unit_price>[\d.]+)/(?:ea|kg)\s+\$(?P<amount>[\d.]+)$"
)

FEE = re.compile(r"^\+\s*(?P<label>.+?)\s+\$(?P<amount>[\d.]+)$")
INVOICE_TOTAL = re.compile(r"^Invoice Total\s+\$(?P<amount>[\d.]+)$")
REFERENCE = re.compile(r"Order Confirmation/Invoice Number\s+(?P<ref>\S+)")
ISSUED = re.compile(r"^Date\s+(?P<date>\d{1,2}\s+\w{3},\s*\d{4})$")
STORE = re.compile(r"^Fulfilled By:\s*(?P<store>.+?)\s+\d+\s*$")

# Boilerplate that repeats on every page and is never a department heading.
NOISE = re.compile(
    r"\d|\$|^Page|Invoice|GST|Deliver|Order|Courier|New Zealand|Tax|Ref\s", re.IGNORECASE
)


def read_pdf(path: Path) -> str:
    """The invoice as text. Split out so parsing can be tested without a PDF."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def parse(text: str) -> dict[str, Any]:
    """
    Turn invoice text into a Wyrmhoard document.

    Raises ValueError naming what was missing, because a producer that submits
    a half-read document is worse than one that refuses to.
    """
    items: list[dict[str, Any]] = []
    department: str | None = None
    reference = issued = store = None
    stated_total: float | None = None
    # A wrapped description sits immediately under the line it belongs to.
    # Without tracking that, the page footer - which also starts lower-case,
    # because it wraps mid-sentence - gets glued onto the last item. A real
    # invoice ended up with a line called "Pick up Fee refund. Find Olive at
    # https://…" before this existed.
    previous_was_item = False

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Everything past the total is footer: contact details, a licence
        # number, and an apology about substitutions.
        if stated_total is not None:
            break

        if reference is None and (m := REFERENCE.search(line)):
            reference = m.group("ref")
        if issued is None and (m := ISSUED.match(line)):
            issued = datetime.strptime(m.group("date").replace(",", ""), "%d %b %Y").date()
        if store is None and (m := STORE.match(line)):
            store = m.group("store")
        if m := INVOICE_TOTAL.match(line):
            stated_total = float(m.group("amount"))
            continue

        if m := ITEM.match(line):
            items.append(
                {
                    "description": m.group("description"),
                    # What arrived, not what was asked for. A short-supplied
                    # line is charged for the smaller number, and `raw` keeps
                    # the ordered figure for anyone who wants it.
                    "quantity": float(m.group("supplied")),
                    "unit": m.group("unit"),
                    "unit_price": float(m.group("unit_price")),
                    "line_total": float(m.group("amount")),
                    "source_category": department,
                    "raw": line,
                }
            )
            previous_was_item = True
            continue

        if m := FEE.match(line):
            items.append(
                {
                    "description": m.group("label"),
                    "quantity": 1,
                    "unit": "ea",
                    "line_total": float(m.group("amount")),
                    "source_category": None,
                    "raw": line,
                }
            )
            previous_was_item = True
            continue

        # A wrapped description continues the item directly above it and starts
        # lower-case. A department heading is Title Case and stands alone.
        if previous_was_item and line[0].islower():
            items[-1]["description"] += " " + line
            items[-1]["raw"] += " " + line
            continue

        previous_was_item = False
        if 2 < len(line) < 34 and not NOISE.search(line) and line[0].isupper():
            department = line

    if not items:
        raise ValueError("No item lines found. Is this a Woolworths online invoice?")
    if stated_total is None:
        raise ValueError("No 'Invoice Total' line found, so there is nothing to check against.")
    if issued is None:
        raise ValueError("No 'Date' line found. A document has to say what day it is about.")

    return {
        "producer": PRODUCER,
        "kind": "receipt",
        "merchant": store or "Woolworths",
        "observed_at": issued.isoformat(),
        "reference": reference,
        "stated_total": stated_total,
        "currency": "NZD",
        "confidence": "high",
        "items": items,
        "extra": {"format": "woolworths-nz online invoice"},
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
        raise SystemExit(f"Wyrmhoard refused this invoice:\n  {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Cannot reach Wyrmhoard at {api} - is it running? ({exc.reason})"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("invoice", type=Path, help="the invoice PDF")
    parser.add_argument("--api", default=DEFAULT_API, help=f"default {DEFAULT_API}")
    parser.add_argument("--dry-run", action="store_true", help="parse and print, submit nothing")
    args = parser.parse_args(argv)

    if not args.invoice.exists():
        print(f"No file at {args.invoice}", file=sys.stderr)
        return 1

    document = parse(read_pdf(args.invoice))
    summed = round(sum(i["line_total"] for i in document["items"]), 2)
    print(
        f"{args.invoice.name}: {len(document['items'])} lines summing {summed:.2f}, "
        f"invoice states {document['stated_total']:.2f}, dated {document['observed_at']}"
    )

    if args.dry_run:
        for item in document["items"]:
            cat = item["source_category"] or "-"
            print(f"  {cat:<28}{item['description'][:44]:<46}{item['line_total']:>8.2f}")
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
