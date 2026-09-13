"""
The household's figures, read through The Richest Man in Babylon.

A lens: a consumer whose opinion is one named philosophy. This one holds
George S. Clason's seven cures for a lean purse (1926), reads what Wyrmhoard
computed, and says what Babylon would say about it - the number, then what
the principle asks, then the move it would make. It imports nothing from
`wyrmhoard`, talks to `http://localhost:8080/api` like any other client, and
keeps its opinion in `lens.yml` beside it. `LENS.md` states the same opinion
in prose, for a reader who cannot run this program.

    python babylon.py            # writes reports/lenses/babylon.json
    python babylon.py --text     # and prints the readings

The output is the contract in consumers/README.md, which is what the
dashboard's Lens tab renders. It knows nothing about Babylon; it draws
whatever emits that shape, and a second lens is a second directory here.

Two rules from the rest of this repository apply with extra force here,
because this program speaks to a family about their own money and their
children may be in the room. Every figure carries its unit, its window and
where it came from. And a group of spending this lens cannot place is
counted and named, never dropped - a reading that quietly leaves money out
is a reading that looks better than the truth.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import yaml

DEFAULT_API = "http://localhost:8080/api"
HERE = Path(__file__).resolve().parent
#: Where the dashboard looks. `reports/` is gitignored: this file holds the
#: household's real numbers.
DEFAULT_OUT = HERE.parent.parent / "reports" / "lenses"

LENS = "babylon"
TITLE = "The Richest Man in Babylon"
PHILOSOPHY = (
    "George S. Clason's parables of Babylon, 1926. A part of all you earn is "
    "yours to keep; what is called necessary grows to meet income unless you "
    "protest; enjoyment is budgeted, not forbidden. This lens holds that "
    "opinion. Wyrmhoard holds none, and a different lens may read the same "
    "figures another way."
)
REFRESH = "./hoard lens babylon"

#: A spending group this lens has no purpose for. Reported, never dropped.
UNPLACED = "unplaced"

#: The seven cures, in the book's order. Only the first two are computed so
#: far; the rest are stated in LENS.md and will follow.
CURES = {
    1: ("Start thy purse to fattening", "Of every ten coins earned, keep one."),
    2: (
        "Control thy expenditures",
        "Budget necessities and enjoyments inside the nine-tenths. What we call "
        "necessary grows to equal our income unless we protest.",
    ),
}


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def load_lens(path: Path = HERE / "lens.yml") -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        lens = yaml.safe_load(fh)
    lens.setdefault("earnings_exclude", [])
    lens.setdefault("purposes", {})
    lens.setdefault("merchants_named", 8)
    return lens


def fetch(path: str, api: str) -> Any:
    try:
        with urllib.request.urlopen(f"{api}{path}") as response:
            return json.load(response)
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Cannot reach Wyrmhoard at {api} - is it running? ({exc.reason})"
        ) from exc


def figure(value: float | None, unit: str, basis: str, source: str) -> dict[str, Any]:
    """A number that says what it is. Nothing in the output is a bare float."""
    return {
        "value": None if value is None else round(value, 2),
        "unit": unit,
        "basis": basis,
        "source": source,
    }


def money(value: float, unit: str) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}" if unit == "NZD" else f"{sign}{abs(value):,.0f} {unit}"


# ---------------------------------------------------------------------------
# Arithmetic on what the series endpoint returned
# ---------------------------------------------------------------------------
def windows(periods: list[dict[str, Any]], n: int) -> tuple[list[int], list[int]]:
    """
    The slots of the last `n` complete periods, and of the `n` before them.

    Only complete periods: the fortnight in progress reads as low spending,
    and the one before the export started reads as none, and neither is a
    fact about the household.
    """
    complete = [i for i, p in enumerate(periods) if p.get("complete")]
    now = complete[-n:] if n else complete
    before = complete[-2 * n : -n] if n and len(complete) > n else []
    return now, before


def slot_sums(rows: list[dict[str, Any]], slots: list[int]) -> list[float]:
    """Per-period total across rows, for the periods asked for."""
    return [round(sum(float(r["totals"][i]) for r in rows), 2) for i in slots]


def place(group: str | None, purposes: dict[str, list[str]]) -> str:
    """Which of Babylon's purposes a household group serves, or `unplaced`."""
    for purpose, keys in purposes.items():
        if group in keys:
            return purpose
    return UNPLACED


def share(part: float, whole: float) -> float | None:
    return None if not whole else round(part / whole, 4)


def _median(values: list[float]) -> float:
    return round(median(values), 2) if values else 0.0


# ---------------------------------------------------------------------------
# The cures
# ---------------------------------------------------------------------------
def cure_one(
    earned: list[float],
    spent: list[float],
    gifts: list[float],
    lens: dict[str, Any],
    unit: str,
    starts: list[str],
    window: dict[str, Any],
) -> dict[str, Any]:
    """Of every ten coins earned, keep one."""
    name, principle = CURES[1]
    kept = [round(e - s, 2) for e, s in zip(earned, spent, strict=True)]
    n = len(earned)
    basis = f"median of {n} complete fortnights"
    src_in = "GET /series?direction=in&period=fortnight"
    src_out = "GET /series?direction=out&period=fortnight"

    earned_pf, spent_pf, kept_pf = _median(earned), _median(spent), _median(kept)
    tenth = round(lens["tenth"] * earned_pf, 2)
    gap = round(tenth - kept_pf, 2)
    kept_share = share(sum(kept), sum(earned))

    if not n:
        reading = "No complete fortnight in the ledger yet, so nothing to read."
        options: list[str] = []
    elif kept_pf < 0:
        reading = (
            f"A typical fortnight earned {money(earned_pf, unit)} and spent "
            f"{money(spent_pf, unit)} - {money(-kept_pf, unit)} more than came in. "
            f"Babylon asks that a tenth, {money(tenth, unit)}, is kept before anything is spent."
        )
        options = [
            f"Move {money(tenth, unit)} to a pot on pay day, before the rest is paid. "
            "The tenth first; the nine-tenths is what is left to live on.",
        ]
    elif kept_pf < tenth:
        part = round(earned_pf / kept_pf) if kept_pf else 0
        reading = (
            f"A typical fortnight earned {money(earned_pf, unit)} and kept "
            f"{money(kept_pf, unit)} - one part in {part}. "
            f"Babylon asks for one in ten: {money(tenth, unit)}."
        )
        options = [
            f"Raise the amount moved on pay day by {money(gap, unit)}, to {money(tenth, unit)}.",
        ]
    else:
        reading = (
            f"A typical fortnight earned {money(earned_pf, unit)} and kept "
            f"{money(kept_pf, unit)} - more than the tenth Babylon asks. The purse is fattening."
        )
        options = ["Keep the tenth moving on pay day. The next cure is what the kept coins do."]

    caveats = [
        "The whole of a loan repayment counts as spending here, principal "
        "included - the roof is not gold in the purse. The dwelling has its own cure.",
    ]
    total_gifts = round(sum(gifts), 2)
    if total_gifts:
        caveats.append(
            f"{money(total_gifts, unit)} arrived over the window as gifts or help "
            "from family. It is real and it is not counted as earned: Babylon's "
            "rule is about what you earn."
        )

    return {
        "cure": 1,
        "name": name,
        "principle": principle,
        "available": bool(n),
        "window": window,
        "figures": {
            "earned_per_fortnight": figure(earned_pf, unit, basis, src_in),
            "spent_per_fortnight": figure(spent_pf, unit, basis, src_out),
            "kept_per_fortnight": figure(kept_pf, unit, basis + ", earned minus spent", src_in),
            "kept_share": figure(
                kept_share, "ratio", f"kept over earned, summed across {n} fortnights", src_in
            ),
            "window_earned": figure(sum(earned), unit, f"sum of {n} fortnights", src_in),
            "window_spent": figure(sum(spent), unit, f"sum of {n} fortnights", src_out),
            "window_kept": figure(sum(kept), unit, f"sum of {n} fortnights", src_in),
            "gifts_in_window": figure(
                total_gifts, unit, "sum of excluded income categories", src_in
            ),
        },
        "principle_asks": {
            "kept_share": lens["tenth"],
            "kept_per_fortnight": figure(
                tenth, unit, "one tenth of a typical fortnight's earnings", "lens.yml"
            ),
        },
        "gap": figure(gap, unit, "the tenth minus what was kept, per fortnight", "derived"),
        "reading": reading,
        "options": options,
        "series": {
            "title": "Kept each fortnight, against the tenth",
            "periods": starts,
            "rows": [{"label": "kept", "totals": kept}],
            "target": {"label": "the tenth", "value": tenth},
        },
        "caveats": caveats,
    }


def cure_two(
    earned: list[float],
    spend_rows: list[dict[str, Any]],
    now: list[int],
    before: list[int],
    merchants: dict[str, dict[str, Any]],
    lens: dict[str, Any],
    unit: str,
    starts: list[str],
    window: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Budget necessities and enjoyments inside the nine-tenths."""
    name, principle = CURES[2]
    purposes: dict[str, list[str]] = lens["purposes"]
    n = len(now)
    src = "GET /series?direction=out&period=fortnight"

    by_purpose: dict[str, list[dict[str, Any]]] = {p: [] for p in purposes}
    unplaced_rows: dict[str, list[dict[str, Any]]] = {}
    for row in spend_rows:
        purpose = place(row.get("group"), purposes)
        if purpose == UNPLACED:
            unplaced_rows.setdefault(str(row.get("group")), []).append(row)
        else:
            by_purpose[purpose].append(row)

    earned_pf = _median(earned)
    nine_tenths = round((1 - lens["tenth"]) * earned_pf, 2)
    figures: dict[str, Any] = {
        "nine_tenths": figure(
            nine_tenths, unit, "nine tenths of a typical fortnight's earnings", "lens.yml"
        ),
    }
    series_rows = []
    parts = []
    for purpose, rows in by_purpose.items():
        now_pf = _median(slot_sums(rows, now))
        before_pf = _median(slot_sums(rows, before)) if before else None
        figures[f"{purpose}_per_fortnight"] = figure(
            now_pf, unit, f"median of {n} complete fortnights", src
        )
        figures[f"{purpose}_share"] = figure(
            share(now_pf, earned_pf),
            "ratio",
            "per fortnight, over a typical fortnight's earnings",
            src,
        )
        figures[f"{purpose}_before"] = figure(
            before_pf, unit, f"median of the {len(before)} fortnights before the window", src
        )
        series_rows.append({"label": purpose, "totals": slot_sums(rows, now)})
        parts.append((purpose, now_pf, before_pf))

    unplaced = [
        {"group": group, "total": round(sum(slot_sums(rows, now)), 2)}
        for group, rows in sorted(unplaced_rows.items())
    ]

    if not n:
        reading = "No complete fortnight in the ledger yet, so nothing to read."
        options: list[str] = []
    else:
        pieces = []
        for purpose, now_pf, _ in parts:
            pct = share(now_pf, earned_pf)
            pieces.append(
                f"{purpose} {money(now_pf, unit)}"
                + (f" ({pct * 100:.0f}% of earnings)" if pct is not None else "")
            )
        both = round(sum(p[1] for p in parts), 2)
        both_share = share(both, earned_pf)
        reading = (
            f"A typical fortnight: {' and '.join(pieces)}. Together {money(both, unit)}"
            + (f", {both_share * 100:.0f}% of earnings" if both_share is not None else "")
            + f". Babylon asks that they fit inside nine-tenths: {money(nine_tenths, unit)}."
        )
        if before:
            then = ", ".join(
                f"{purpose} {money(b, unit)}" for purpose, _, b in parts if b is not None
            )
            reading += f" In the {len(before)} fortnights before: {then}."
        options = _largest_merchants(merchants, unit)

    caveats = [
        "Merchant names are the bank's memos, normalised by Wyrmhoard's one "
        "rule. A branch named in the first four words shows as its own row.",
    ]
    if unplaced:
        named = ", ".join(u["group"] for u in unplaced)
        total = round(sum(u["total"] for u in unplaced), 2)
        caveats.append(
            f"{money(total, unit)} over the window sits in groups this lens does not "
            f"place ({named}). It is counted as spent in the first cure and in "
            "neither purpose here - edit lens.yml if it belongs to one."
        )

    tables = []
    for purpose, table in merchants.items():
        rows = []
        for r in table.get("series", []):
            label = r["label"]
            if r.get("merchant") is None and r.get("merchants"):
                label = f"everything else ({r['merchants']} shops)"
            rows.append([label, r["total"], sum(r["transactions"]), r.get("per_period")])
        tables.append(
            {
                "title": f"Where the {purpose} money went",
                "columns": ["Shop", "Total", "Visits", "Per fortnight"],
                "units": ["text", unit, "count", unit],
                "rows": rows,
                "source": f"GET /series?by=merchant&direction=out&period=fortnight&top={lens['merchants_named']}",
            }
        )

    reading_doc = {
        "cure": 2,
        "name": name,
        "principle": principle,
        "available": bool(n),
        "window": window,
        "figures": figures,
        "principle_asks": {"within_share_of_earnings": round(1 - lens["tenth"], 2)},
        "gap": figure(
            round(sum(p[1] for p in parts) - nine_tenths, 2),
            unit,
            "necessities and enjoyments beyond nine-tenths, per fortnight",
            "derived",
        ),
        "reading": reading,
        "options": options,
        "series": {
            "title": "Necessities and enjoyments each fortnight",
            "periods": starts,
            "rows": series_rows,
            "target": {"label": "nine-tenths", "value": nine_tenths},
        },
        "tables": tables,
        "caveats": caveats,
    }
    return reading_doc, unplaced


#: The singular of each purpose, for a sentence about one shop.
SINGULAR = {"necessities": "necessity", "enjoyments": "enjoyment"}


def _largest_merchants(merchants: dict[str, dict[str, Any]], unit: str) -> list[str]:
    """One sentence per purpose naming its largest shop. A fact, then Babylon's question."""
    out = []
    for purpose, table in merchants.items():
        named = [r for r in table.get("series", []) if r.get("merchant")]
        if not named:
            continue
        top = named[0]
        visits = sum(top["transactions"])
        per = top.get("per_period")
        line = (
            f"The largest single {SINGULAR.get(purpose, purpose)} over the window: "
            f"{money(top['total'], unit)} across {visits} visits at {top['label']}"
            + (f", {money(per, unit)} a fortnight" if per is not None else "")
            + "."
        )
        if purpose == "enjoyments":
            line += " Babylon's question is whether it was decided, not whether it was enjoyed."
        else:
            line += " Babylon's warning is that what is called necessary grows to meet income."
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# Reading the household
# ---------------------------------------------------------------------------
def read(api: str, lens: dict[str, Any]) -> dict[str, Any]:
    """Fetch what the cures need and assemble the contract."""
    health = fetch("/health", api)
    setup = fetch("/setup", api)
    taxonomy = fetch("/taxonomy", api)
    money_in = fetch("/series?direction=in&period=fortnight", api)
    money_out = fetch("/series?direction=out&period=fortnight", api)

    unit = money_out.get("unit") or money_in.get("unit") or "NZD"
    periods = money_out.get("periods") or []
    # Both series share an anchor and a range, so slots line up. Trust but
    # check: a mismatch here would silently pair one fortnight's income with
    # another's spending.
    if [p["start"] for p in money_in.get("periods") or []] != [p["start"] for p in periods]:
        raise SystemExit(
            "income and spending series returned different periods; cannot line them up"
        )

    n = int(lens["window_fortnights"])
    now, before = windows(periods, n)
    window = {
        "from": periods[now[0]]["start"] if now else None,
        "to": periods[now[-1]]["end"] if now else None,
        "period": "fortnight",
        "complete_periods": len(now),
        "anchor": money_out.get("anchor"),
        "anchor_source": money_out.get("anchor_source"),
    }

    excluded = set(lens["earnings_exclude"])
    earning_rows = [r for r in money_in.get("series", []) if r["category"] not in excluded]
    gift_rows = [r for r in money_in.get("series", []) if r["category"] in excluded]
    spend_rows = money_out.get("series", [])

    earned = slot_sums(earning_rows, now)
    spent = slot_sums(spend_rows, now)
    gifts = slot_sums(gift_rows, now)

    merchants = _merchants_by_purpose(api, taxonomy, lens, window) if now else {}

    starts = [periods[i]["start"] for i in now]
    one = cure_one(earned, spent, gifts, lens, unit, starts, window)
    two, unplaced = cure_two(earned, spend_rows, now, before, merchants, lens, unit, starts, window)

    return {
        "lens": LENS,
        "title": TITLE,
        "philosophy": PHILOSOPHY,
        "refresh": REFRESH,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ledger_ends": (health.get("stats") or {}).get("last_date"),
        "unit": unit,
        "window": window,
        "readings": [one, two],
        "unplaced": unplaced,
        "cannot_see": _cannot_see(setup),
    }


def _merchants_by_purpose(
    api: str, taxonomy: dict[str, Any], lens: dict[str, Any], window: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """The top shops per purpose over the window, with the tail folded, not dropped."""
    groups = {g["key"]: g for g in taxonomy.get("groups", [])}
    out = {}
    for purpose, keys in lens["purposes"].items():
        categories = [c for k in keys for c in (groups.get(k) or {}).get("categories", [])]
        if not categories:
            continue
        query = urllib.parse.urlencode(
            [
                ("direction", "out"),
                ("period", "fortnight"),
                ("by", "merchant"),
                ("top", str(lens["merchants_named"])),
                ("from", window["from"]),
                ("to", window["to"]),
                *[("category", c) for c in categories],
            ]
        )
        out[purpose] = fetch(f"/series?{query}", api)
    return out


def _cannot_see(setup: dict[str, Any]) -> list[str]:
    """What the ledger cannot answer for. Always stated, never folded away."""
    out = []
    coverage = setup.get("coverage") or {}
    pct = coverage.get("categorised_pct")
    if pct is not None and not coverage.get("trustworthy"):
        out.append(
            f"{pct}% of spending is categorised. The rest is counted as spent, "
            "but this lens cannot say which purpose it served."
        )
    for todo in setup.get("todo") or []:
        out.append(f"{todo.get('label')}: {todo.get('why')}")
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def render_text(doc: dict[str, Any]) -> str:
    unit = doc["unit"]
    w = doc["window"]
    lines = [
        f"{doc['title']} - read {doc['generated_at'][:10]} from a ledger ending {doc['ledger_ends']}",
        f"Window: {w['complete_periods']} fortnights, {w['from']} to {w['to']}",
        "",
    ]
    for r in doc["readings"]:
        lines.append(f"{r['cure']}. {r['name']}")
        lines.append(f"   {r['principle']}")
        lines.append(f"   {r['reading']}")
        for option in r.get("options", []):
            lines.append(f"   -> {option}")
        for table in r.get("tables", []):
            lines.append(f"   {table['title']}:")
            for row in table["rows"]:
                lines.append(
                    f"     {row[0]:<40} {money(row[1], unit):>9}  {row[2]:>3} visits"
                    + (f"  {money(row[3], unit):>8}/fn" if row[3] is not None else "")
                )
        lines.append("")
    if doc["unplaced"]:
        lines.append(
            "Not placed by this lens: "
            + ", ".join(f"{u['group']} {money(u['total'], unit)}" for u in doc["unplaced"])
        )
    if doc["cannot_see"]:
        lines.append("Cannot see:")
        lines.extend(f"  - {c}" for c in doc["cannot_see"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory for babylon.json")
    parser.add_argument("--text", action="store_true", help="also print the readings")
    parser.add_argument("--window", type=int, help="fortnights to read (default from lens.yml)")
    args = parser.parse_args(argv)

    lens = load_lens()
    if args.window:
        lens["window_fortnights"] = args.window
    doc = read(args.api, lens)

    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"{LENS}.json"
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    if args.text:
        print(render_text(doc))
        print()
    print(f"Wrote {target}. Open the dashboard's Lens tab, or run again after the next import.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
