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

#: The seven cures, in the book's order. LENS.md says what each reads.
CURES = {
    1: ("Start thy purse to fattening", "Of every ten coins earned, keep one."),
    2: (
        "Control thy expenditures",
        "Budget necessities and enjoyments inside the nine-tenths. What we call "
        "necessary grows to equal our income unless we protest.",
    ),
    3: ("Make thy gold multiply", "Put each coin to labouring, that it may reproduce its kind."),
    4: (
        "Guard thy treasures from loss",
        "Borrowed gold is for what earns, never for what is consumed.",
    ),
    5: ("Make of thy dwelling a profitable investment", "Own thy own home."),
    6: (
        "Insure a future income",
        "Provide in advance for the needs of thy growing age and the protection of thy family.",
    ),
    7: (
        "Increase thy ability to earn",
        "Cultivate thy own powers: study, and become more skilful.",
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
    lens.setdefault("interest_categories", [])
    lens.setdefault("credit_categories", [])
    lens.setdefault("insurance_categories", [])
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
        "value": None if value is None else round(value, 4 if unit == "ratio" else 2),
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


def _account_name(account: dict[str, Any]) -> str:
    return str(account.get("label") or account.get("account") or "?")


def _latest_payslips(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The most recent payslip per job, the way the core's income module picks them."""
    latest: dict[str, dict[str, Any]] = {}
    for slip in rows:
        key = str(slip.get("employee_ref") or slip.get("employer") or slip.get("source_file"))
        current = latest.get(key)
        if current is None or str(slip.get("pay_date") or "") > str(current.get("pay_date") or ""):
            latest[key] = slip
    return sorted(latest.values(), key=lambda s: -(s.get("gross") or 0.0))


def cure_three(
    accounts: list[dict[str, Any]],
    balances: list[dict[str, Any]],
    income_rows: list[dict[str, Any]],
    taxonomy: dict[str, Any],
    now: list[int],
    lens: dict[str, Any],
    unit: str,
    window: dict[str, Any],
) -> dict[str, Any]:
    """Put each coin to labouring."""
    name, principle = CURES[3]
    pots = [a for a in accounts if a.get("role") == "savings"]
    typed = [b for b in balances if b.get("kind") == "asset"]
    known = {c for g in taxonomy.get("groups", []) for c in g.get("categories", [])}
    wanted = [c for c in lens["interest_categories"] if c in known]
    interest_rows = [r for r in income_rows if r["category"] in set(wanted)]
    interest = round(sum(slot_sums(interest_rows, now)), 2)
    held = round(sum(float(a.get("last_balance") or 0.0) for a in pots), 2)
    typed_total = round(sum(float(b.get("amount") or 0.0) for b in typed), 2)

    if pots:
        reading = f"The pots hold {money(held, unit)} across {len(pots)} account{'s' if len(pots) != 1 else ''}"
        reading += f", and {money(typed_total, unit)} more is typed in." if typed else "."
    elif typed:
        reading = f"{money(typed_total, unit)} of assets is typed in; the ledger shows no savings account."
    else:
        reading = "The ledger shows no savings account and nothing typed in as an asset."
    if not wanted:
        reading += " Whether any of it earns, the ledger cannot say: the household's rules have no category for interest."
    elif interest:
        reading += f" Over the window {money(interest, unit)} arrived as interest."
    else:
        reading += " Over the window nothing arrived as interest - either it earns none, or the bank does not show it as its own line."

    options = [
        "Babylon asks that kept coins labour. Where they should is a decision this "
        "lens does not make: it recommends no product, ever."
    ]
    if held <= 0 and not typed:
        options = ["There is nothing to put to work yet. The first cure comes first."]

    table_rows = [[_account_name(a), float(a.get("last_balance") or 0.0)] for a in pots]
    table_rows += [
        [f"{b.get('label')} (typed in, {str(b.get('as_at'))[:10]})", float(b.get("amount") or 0.0)]
        for b in typed
    ]
    return {
        "cure": 3,
        "name": name,
        "principle": principle,
        "available": True,
        "window": window,
        "figures": {
            "pots_held": figure(
                held, unit, "sum of last balances on accounts in the savings role", "GET /accounts"
            ),
            "typed_assets": figure(
                typed_total, unit, "sum of balances typed in as assets", "GET /balances"
            ),
            "interest_in_window": figure(
                interest,
                unit,
                f"sum over {len(now)} fortnights of {', '.join(wanted) or 'no interest category'}",
                "GET /series?direction=in&period=fortnight",
            ),
        },
        "principle_asks": {},
        "reading": reading,
        "options": options,
        "tables": [
            {
                "title": "What is kept",
                "columns": ["Where", "Balance"],
                "units": ["text", unit],
                "rows": table_rows,
                "source": "GET /accounts, GET /balances",
            }
        ]
        if table_rows
        else [],
        "caveats": [
            "A balance is as at the last row imported for that account, not today.",
        ],
    }


def cure_four(
    accounts: list[dict[str, Any]],
    dwelling: list[str],
    spend_rows: list[dict[str, Any]],
    now: list[int],
    lens: dict[str, Any],
    unit: str,
    window: dict[str, Any],
) -> dict[str, Any]:
    """Guard thy treasures from loss."""
    name, principle = CURES[4]
    liabilities = [a for a in accounts if a.get("role") == "liability"]
    other = (
        [a for a in liabilities if a.get("account") not in dwelling] if dwelling else liabilities
    )
    owed = round(sum(abs(float(a.get("last_balance") or 0.0)) for a in other), 2)
    credit_rows = [r for r in spend_rows if r["category"] in set(lens["credit_categories"])]
    credit = round(sum(slot_sums(credit_rows, now)), 2)
    named = ", ".join(lens["credit_categories"])

    scope = "beyond the dwelling" if dwelling else "in total"
    if not other and not credit:
        reading = (
            f"Nothing is owed {scope}, and nothing left the purse for {named} over the window. "
            "The purse has no hole in it - a fact worth knowing before the next decision."
        )
        options = ["Nothing here needs changing. Babylon's rule is only that this stays true."]
    else:
        parts = []
        if other:
            parts.append(
                f"Owed {scope}: {money(owed, unit)} across {len(other)} account{'s' if len(other) != 1 else ''}."
            )
        parts.append(
            f"Over the window {money(credit, unit)} left the purse for {named}."
            if credit
            else f"Nothing left the purse for {named} over the window."
        )
        reading = " ".join(parts)
        if other and not dwelling:
            options = [
                "Babylon's rule: borrowed gold is for what earns, never for what is consumed. "
                "Until the home is recorded this lens cannot tell its loan from other debt, "
                "so it cannot say which of this is which."
            ]
        else:
            options = [
                "Babylon's rule: borrowed gold is for what earns, never for what is consumed. "
                + (
                    f"The {money(owed, unit)} {scope} is the place to look first."
                    if other
                    else "The fees are the place to look."
                )
            ]

    caveats = []
    if not dwelling:
        caveats.append(
            "No home is recorded, so this lens cannot tell the dwelling's loan from "
            "other debt. Every liability is listed here; record the home to split them."
        )
    return {
        "cure": 4,
        "name": name,
        "principle": principle,
        "available": True,
        "window": window,
        "figures": {
            "owed_beyond_the_dwelling" if dwelling else "owed": figure(
                owed,
                unit,
                "sum of last balances on liability accounts"
                + (" not linked to the home" if dwelling else ""),
                "GET /accounts",
            ),
            "credit_cost_in_window": figure(
                credit,
                unit,
                f"sum over {len(now)} fortnights of {named}",
                "GET /series?direction=out&period=fortnight",
            ),
        },
        "principle_asks": {"credit_cost_in_window": 0},
        "reading": reading,
        "options": options,
        "tables": [
            {
                "title": "Owed" + (" beyond the dwelling" if dwelling else ""),
                "columns": ["Account", "Balance"],
                "units": ["text", unit],
                "rows": [
                    [_account_name(a), -abs(float(a.get("last_balance") or 0.0))] for a in other
                ],
                "source": "GET /accounts",
            }
        ]
        if other
        else [],
        "caveats": caveats,
    }


def cure_five(
    loans: list[dict[str, Any]],
    dwelling: list[str],
    lens: dict[str, Any],
    unit: str,
    window: dict[str, Any],
) -> dict[str, Any]:
    """Own thy own home."""
    name, principle = CURES[5]
    chosen = [ln for ln in loans if ln.get("account") in dwelling] if dwelling else loans
    rows = []
    owed = interest_pf = principal_pf = 0.0
    years: list[float] = []
    payoffs: list[str] = []
    low_confidence = False
    for ln in chosen:
        balance = abs(float(ln.get("balance") or 0.0))
        rate = ln.get("rate_pct")
        ppy = float(ln.get("periods_per_year") or 26)
        repayment = float(ln.get("repayment") or 0.0)
        i_pf = round(balance * float(rate) / 100 / 26, 2) if rate else None
        p_pf = round(repayment * ppy / 26 - i_pf, 2) if i_pf is not None and repayment else None
        base = (ln.get("projection") or {}).get("base") or {}
        owed += balance
        interest_pf += i_pf or 0.0
        principal_pf += p_pf or 0.0
        if base.get("years"):
            years.append(float(base["years"]))
            payoffs.append(str(base.get("payoff_date"))[:4])
        if ln.get("confidence") == "low":
            low_confidence = True
        rows.append(
            [
                str(ln.get("account")),
                -balance,
                rate,
                i_pf,
                p_pf,
                str(base.get("payoff_date") or "")[:4] or None,
            ]
        )

    if not chosen:
        reading = (
            "No loan is recorded against a home."
            if dwelling
            else "The ledger shows no loan account."
        )
        options = [
            "Babylon's fifth cure is to own the roof. Nothing here to read until a loan is imported."
        ]
    else:
        reading = (
            f"Owed on the dwelling: {money(owed, unit)}. Each fortnight about {money(interest_pf, unit)} "
            f"goes to interest and {money(principal_pf, unit)} to the balance"
            + (
                f"; at this pace it clears in {max(years):.0f} years, around {max(payoffs)}."
                if years
                else "."
            )
        )
        options = []
        extra = lens.get("extra_per_fortnight")
        for ln in chosen:
            for s in (ln.get("projection") or {}).get("scenarios") or []:
                if s.get("extra_per_period") == extra and s.get("years_saved"):
                    options.append(
                        f"Once the tenth is kept: Wyrmhoard's own projection says an extra "
                        f"{money(float(extra), unit)} a fortnight on {ln.get('account')} clears it "
                        f"{s['years_saved']:.1f} years sooner and saves {money(float(s['interest_saved']), unit)} in interest."
                    )
        if not options:
            options = [
                "Babylon's order is the tenth first, then the roof. The arithmetic for paying faster is on the Overview."
            ]

    caveats = [
        "Interest per fortnight is balance times rate over twenty-six, not the bank's own line; "
        "the rate itself is worked out from the loan's interest charges.",
    ]
    if low_confidence:
        caveats.append(
            "The rate on at least one loan rests on few interest charges - low confidence."
        )
    if not dwelling and chosen:
        caveats.append("No home is recorded, so every loan is read as the dwelling's.")
    return {
        "cure": 5,
        "name": name,
        "principle": principle,
        "available": True,
        "window": window,
        "figures": {
            "owed_on_the_dwelling": figure(
                owed, unit, "sum of loan balances linked to the home", "GET /loans, GET /properties"
            ),
            "interest_per_fortnight": figure(
                interest_pf, unit, "balance x rate / 26, summed", "GET /loans"
            ),
            "principal_per_fortnight": figure(
                principal_pf, unit, "repayment per fortnight minus interest", "GET /loans"
            ),
            "years_to_clear": figure(
                max(years) if years else None,
                "years",
                "at current repayments, longest loan",
                "GET /loans",
            ),
        },
        "principle_asks": {},
        "reading": reading,
        "options": options,
        "tables": [
            {
                "title": "The dwelling's loans",
                "columns": [
                    "Loan",
                    "Balance",
                    "Rate %",
                    "Interest / fortnight",
                    "Principal / fortnight",
                    "Clears",
                ],
                "units": ["text", unit, "percent", unit, unit, "text"],
                "rows": rows,
                "source": "GET /loans",
            }
        ]
        if rows
        else [],
        "caveats": caveats,
    }


def cure_six(
    payslips: list[dict[str, Any]],
    recurring: list[dict[str, Any]],
    lens: dict[str, Any],
    unit: str,
    window: dict[str, Any],
) -> dict[str, Any]:
    """Insure a future income."""
    name, principle = CURES[6]
    latest = _latest_payslips(payslips)
    # A payslip states the employee's contribution as a deduction - negative -
    # and the employer's as a positive. Both are money set aside.
    ee = round(sum(abs(float(s.get("kiwisaver_ee") or 0.0)) for s in latest), 2)
    er = round(sum(abs(float(s.get("kiwisaver_er") or 0.0)) for s in latest), 2)
    gross = round(sum(float(s.get("gross") or 0.0) for s in latest), 2)
    retirement_share = share(ee + er, gross)
    insurance = [i for i in recurring if i.get("category") in set(lens["insurance_categories"])]
    insurance_year = round(sum(float(i.get("annual_cost") or 0.0) for i in insurance), 2)

    if latest:
        dates = ", ".join(sorted({str(s.get("pay_date"))[:10] for s in latest}))
        reading = (
            f"On the latest payslip{'s' if len(latest) > 1 else ''} ({dates}): {money(ee, unit)} from you and "
            f"{money(er, unit)} from the employer toward retirement"
            + (
                f", {retirement_share * 100:.1f}% of {money(gross, unit)} gross."
                if retirement_share is not None
                else "."
            )
        )
    else:
        reading = "No payslip is imported, so retirement contributions cannot be seen - a bank row shows net pay only."
    reading += (
        f" Insurance: {money(insurance_year, unit)} a year across {len(insurance)} polic{'ies' if len(insurance) != 1 else 'y'}."
        if insurance
        else " No recurring payment is categorised as insurance."
    )
    options = [
        "Babylon asks that the future be provided for in advance. The figures are here; "
        "whether they are enough is the household's question, and this lens holds no rate table to answer it."
    ]
    return {
        "cure": 6,
        "name": name,
        "principle": principle,
        "available": True,
        "window": window,
        "figures": {
            "retirement_you_per_pay": figure(
                ee, unit, "latest payslip per job, summed", "GET /payslips"
            ),
            "retirement_employer_per_pay": figure(
                er, unit, "latest payslip per job, summed", "GET /payslips"
            ),
            "retirement_share_of_gross": figure(
                retirement_share,
                "ratio",
                "both contributions over gross, latest payslips",
                "GET /payslips",
            ),
            "insurance_per_year": figure(
                insurance_year,
                unit,
                f"annual cost of recurring payments in {', '.join(lens['insurance_categories'])}",
                "GET /recurring",
            ),
        },
        "principle_asks": {},
        "reading": reading,
        "options": options,
        "tables": [
            {
                "title": "What protects the family",
                "columns": ["What", "How often", "Each time", "Per year"],
                "units": ["text", "text", unit, unit],
                "rows": [
                    [
                        i.get("merchant"),
                        i.get("cadence"),
                        i.get("typical_amount"),
                        i.get("annual_cost"),
                    ]
                    for i in insurance
                ],
                "source": "GET /recurring",
            }
        ]
        if insurance
        else [],
        "caveats": [
            "A payslip states one pay; the share is that pay's, not the year's.",
        ]
        if latest
        else [],
    }


def cure_seven(
    earned: list[float],
    earned_before: list[float],
    household: dict[str, Any],
    income: dict[str, Any],
    unit: str,
    starts: list[str],
    before_starts: list[str],
    window: dict[str, Any],
) -> dict[str, Any]:
    """Increase thy ability to earn."""
    name, principle = CURES[7]
    now_pf, before_pf = _median(earned), (_median(earned_before) if earned_before else None)
    jobs = income.get("jobs") or []
    annual = income.get("gross_annual")
    upside = [str(u.get("label")) for u in (household.get("upside") or []) if u.get("label")]

    reading = f"A typical fortnight earned {money(now_pf, unit)}"
    reading += (
        f"; in the {len(earned_before)} before, {money(before_pf, unit)}."
        if before_pf is not None
        else "."
    )
    if jobs:
        reading += f" Payslips show {len(jobs)} job{'s' if len(jobs) != 1 else ''}, annualised {money(float(annual or 0.0), unit)} gross."
    if upside:
        reading += f" Income the household chooses not to budget on: {', '.join(upside)}."
    return {
        "cure": 7,
        "name": name,
        "principle": principle,
        "available": bool(earned),
        "window": window,
        "figures": {
            "earned_per_fortnight": figure(
                now_pf,
                unit,
                f"median of {len(earned)} complete fortnights",
                "GET /series?direction=in&period=fortnight",
            ),
            "earned_before": figure(
                before_pf,
                unit,
                f"median of the {len(earned_before)} fortnights before the window",
                "GET /series?direction=in&period=fortnight",
            ),
            "jobs": figure(len(jobs), "count", "jobs with a payslip", "GET /payslips"),
            "annualised_gross": figure(
                float(annual) if annual else None,
                unit,
                "year-to-date taxable gross, annualised",
                "GET /payslips",
            ),
        },
        "principle_asks": {},
        "reading": reading,
        "options": [
            "Babylon's last cure has no figure. The surest increase is in the ability to earn, "
            "and that is study and skill, not arithmetic."
        ],
        "series": {
            "title": "Earned each fortnight, the window before and this one",
            "periods": [*before_starts, *starts],
            "rows": [{"label": "earned", "totals": [*earned_before, *earned]}],
        },
        "caveats": [],
    }


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

    # The later cures read what the household holds and owes, not only what
    # moved. Each is one call; each endpoint answers for itself when empty.
    accounts = fetch("/accounts", api)
    balances = fetch("/balances", api)
    loans = fetch("/loans", api)
    properties = fetch("/properties", api)
    payslips = fetch("/payslips", api)
    recurring = fetch("/recurring", api)
    household = fetch("/household", api)
    primary = next((p for p in properties.get("properties", []) if p.get("is_primary")), None)
    dwelling = list((primary or {}).get("loan_accounts") or [])

    starts = [periods[i]["start"] for i in now]
    before_starts = [periods[i]["start"] for i in before]
    one = cure_one(earned, spent, gifts, lens, unit, starts, window)
    two, unplaced = cure_two(earned, spend_rows, now, before, merchants, lens, unit, starts, window)
    three = cure_three(
        accounts, balances, money_in.get("series", []), taxonomy, now, lens, unit, window
    )
    four = cure_four(accounts, dwelling, spend_rows, now, lens, unit, window)
    five = cure_five(loans, dwelling, lens, unit, window)
    six = cure_six(payslips.get("payslips") or [], recurring.get("items") or [], lens, unit, window)
    seven = cure_seven(
        earned,
        slot_sums(earning_rows, before),
        household,
        payslips.get("income") or {},
        unit,
        starts,
        before_starts,
        window,
    )

    return {
        "lens": LENS,
        "title": TITLE,
        "philosophy": PHILOSOPHY,
        "refresh": REFRESH,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ledger_ends": (health.get("stats") or {}).get("last_date"),
        "unit": unit,
        "window": window,
        "readings": [one, two, three, four, five, six, seven],
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
def _cell(value: Any, kind: str, unit: str) -> str:
    """One table cell for the terminal, formatted by the unit the column declares."""
    if value is None:
        return "-"
    if kind == unit:
        return money(float(value), unit)
    if kind == "percent":
        return f"{float(value):.2f}%"
    if kind == "count":
        return f"{int(value)}"
    return str(value)


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
            units = table.get("units") or []
            for row in table["rows"]:
                cells = [
                    _cell(v, units[i] if i < len(units) else "text", unit)
                    for i, v in enumerate(row)
                ]
                lines.append(f"     {cells[0]:<40} " + "  ".join(f"{c:>10}" for c in cells[1:]))
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
