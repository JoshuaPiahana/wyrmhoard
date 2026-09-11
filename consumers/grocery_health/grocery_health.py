"""
What did the shopping actually consist of?

A consumer. It reads receipts Wyrmhoard already holds and answers a question
Wyrmhoard deliberately does not: of what was bought, how much was fresh food,
how much was processed, how much was drink, and how much was not food at all.

    python grocery_health.py                 # every receipt in the ledger
    python grocery_health.py --since 2026-07-01

It exists to test a claim rather than to be a product. The claim is that the
store underneath Wyrmhoard is neutral - that a receipt is a record of what
happened and not "finance data" - and that a second domain can read it without
the core changing. So this program imports nothing from `wyrmhoard`. It talks
to `http://localhost:8080/api` like any other client, and the vocabulary it
applies is its own, in `groups.yml` beside it.

The only real-world fact it needs that Wyrmhoard cannot supply is what counts
as "processed". That is an opinion, this lens holds it, and a different lens
may hold another.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml

DEFAULT_API = "http://localhost:8080/api"
HERE = Path(__file__).resolve().parent

#: Lines this lens could not place. Always reported, never dropped: a split
#: that quietly leaves lines out is the exact shape of wrong number the
#: dashboard's old "choices" figure was.
UNPLACED = "unplaced"


def load_vocabulary(path: Path = HERE / "groups.yml") -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        vocab = yaml.safe_load(fh)
    return {
        "labels": {k: v["label"] for k, v in vocab["groups"].items()},
        "by_source_category": vocab.get("by_source_category") or {},
        "by_description": [
            (str(rule["match"]).lower(), rule["group"])
            for rule in vocab.get("by_description") or []
        ],
    }


def place(item: dict[str, Any], vocab: dict[str, Any]) -> str:
    """
    Which group a line belongs to, or `unplaced`.

    The shop's own department is tried first because it is a statement by the
    shop, made once, that applies to every product under it. The description
    is a fallback for lines that arrive without one.
    """
    source = item.get("source_category")
    if source and source in vocab["by_source_category"]:
        return vocab["by_source_category"][source]

    description = (item.get("description") or "").lower()
    for needle, group in vocab["by_description"]:
        if needle in description:
            return group
    return UNPLACED


def split(items: list[dict[str, Any]], vocab: dict[str, Any]) -> dict[str, Any]:
    """Totals per group, plus the lines that could not be placed, by name."""
    totals: dict[str, float] = defaultdict(float)
    unplaced: list[str] = []
    for item in items:
        group = place(item, vocab)
        totals[group] += float(item["line_total"])
        if group == UNPLACED:
            unplaced.append(item["description"])
    return {
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "sum": round(sum(totals.values()), 2),
        "unplaced": unplaced,
    }


def fetch(path: str, api: str) -> Any:
    try:
        with urllib.request.urlopen(f"{api}{path}") as response:
            return json.load(response)
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Cannot reach Wyrmhoard at {api} - is it running? ({exc.reason})"
        ) from exc


def receipts(api: str, since: date | None) -> list[dict[str, Any]]:
    """Every document with its lines. Two calls per receipt, by design.

    The listing endpoint deliberately omits product names - they are the most
    revealing thing the ledger holds, and reading them is meant to be a separate,
    explicit act. This program is that act.
    """
    listing = fetch("/documents", api)["documents"]
    out = []
    for doc in listing:
        if since and doc["observed_at"] < since.isoformat():
            continue
        out.append(fetch(f"/documents/{doc['id']}", api))
    return out


def render(doc: dict[str, Any], result: dict[str, Any], vocab: dict[str, Any]) -> str:
    total = result["sum"]
    lines = [f"{doc['observed_at']}  {doc['merchant']}  ${doc['stated_total']:,.2f}"]
    for group, amount in sorted(result["totals"].items(), key=lambda kv: -kv[1]):
        label = vocab["labels"].get(group, group)
        share = 100 * amount / total if total else 0
        lines.append(f"  {label:<28}${amount:>9,.2f}  {share:>5.1f}%")
    if result["unplaced"]:
        lines.append(
            f"  could not place {len(result['unplaced'])} line(s): {', '.join(result['unplaced'][:4])}"
        )
    if abs(total - float(doc["stated_total"])) > 0.005:
        lines.append(
            f"  ! lines sum to {total:.2f} but the receipt states {doc['stated_total']:.2f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--since", type=date.fromisoformat, help="ISO date")
    args = parser.parse_args(argv)

    vocab = load_vocabulary()
    docs = receipts(args.api, args.since)
    if not docs:
        print("No receipts in the ledger yet. A producer puts them there - see producers/.")
        return 0

    grand: dict[str, float] = defaultdict(float)
    unplaced_total = 0
    for doc in docs:
        result = split(doc["items"], vocab)
        print(render(doc, result, vocab))
        print()
        for group, amount in result["totals"].items():
            grand[group] += amount
        unplaced_total += len(result["unplaced"])

    if len(docs) > 1:
        total = sum(grand.values())
        print(f"Across {len(docs)} receipts, ${total:,.2f}")
        for group, amount in sorted(grand.items(), key=lambda kv: -kv[1]):
            print(
                f"  {vocab['labels'].get(group, group):<28}${amount:>9,.2f}  {100 * amount / total:>5.1f}%"
            )
    if unplaced_total:
        print(f"\n{unplaced_total} line(s) could not be placed. Add a pattern to groups.yml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
