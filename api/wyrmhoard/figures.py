"""
What a number means.

A figure that travels without its unit is a figure somebody will eventually
misread. `312.40` is a guess about currency and a guess about period; both are
usually right, which is what makes the failure mode nasty when one is not.

This matters more than it used to. The consumers of these figures are
increasingly programs nobody here wrote - a jurisdiction pack computing
Australian entitlements, somebody's coaching script, an agent reasoning across
this and a completely different tool's data. Each of them reads numbers out of
a response and has to know what it is holding. `get_progress` means something
different to a fitness tracker, and `4816.85` next to `7823` is two bare floats
unless both say what they are.

The declaration is per response rather than per number
------------------------------------------------------
`{"value": 312.40, "unit": "NZD"}` on every figure is unambiguous and roughly
triples the size of every response. This project already holds that
minimisation belongs in the interface design - an agent answering an ordinary
question should not receive three thousand rows, and by the same argument it
should not receive three thousand unit strings. A consumer always receives a
whole response, so declaring once is sufficient and much cheaper.

That works only if the exceptions are explicit, which is what `NON_CURRENCY`
is. Suffix conventions look like they would do the job and do not: `per_year`
and `interest_first_year` are amounts of money, `periods_per_year` is a count,
and all three end in the same four letters.
"""

from __future__ import annotations

from typing import Any

# Every numeric figure is an amount in the household's currency unless it is
# named here. Kept as data rather than inferred from the name because the
# inference is wrong often enough to matter - see the module docstring.
NON_CURRENCY = {
    # Proportions
    "categorised_pct": "percent",
    "savings_rate_pct": "percent",
    "share_pct": "percent",
    "progress_pct": "percent",
    "kiwisaver_employee_pct": "percent",
    "kiwisaver_employer_pct": "percent",
    # Annual interest rates, which are a proportion and not an amount
    "rate_pct": "percent per year",
    "rate_low": "percent per year",
    "rate_high": "percent per year",
    "interest_rate_pct": "percent per year",
    # Durations
    "runway_weeks": "weeks",
    "years": "years",
    "years_saved": "years",
    "age_days": "days",
    "window_days": "days",
    "span_days": "days",
    "window_months": "months",
    "months": "months",
    "months_used": "months",
    "month_count": "months",
    "prior_months": "months",
    "recent_months": "months",
    # `weeks_of_essentials` is the trap this list exists for. The MCP surface
    # used to rename `runway_weeks` on the way out, and the renamed field
    # neither appeared here by its original name nor carried a unit suffix -
    # so it was silently reported as an amount of money until this was written
    # down. The core no longer computes either figure, because it needs
    # somebody to decide what counts as essential; the entry stays so that a
    # consumer reintroducing the name inherits the right unit rather than
    # rediscovering the same bug.
    "weeks_of_essentials": "weeks",
    # Proportions that are not percentages
    "fraction_elapsed": "fraction of the tax year, 0 to 1",
    "regularity": "coefficient of variation, lower is more regular",
    # Counts
    "transactions": "count",
    "transaction_count": "count",
    "uncategorised_count": "count",
    "subscriptions_count": "count",
    "valuation_count": "count",
    "history_count": "count",
    "reclassified_count": "count",
    "changed_group_count": "count",
    "interest_periods": "count",
    "periods_per_year": "count",
    "periods": "count",
    "occurrences": "count",
    "transfers": "count",
    "ird_payments": "count",
    "msd_payments": "count",
    "returned": "count",
    "count": "count",
    "children": "count",
    "matched": "count",
    "rows_seen": "count",
    "rows_new": "count",
    "line_count": "count",
    "held_count": "count",
    "linked_count": "count",
    "unlinked_count": "count",
    # Severity tallies, which share their names with nothing else numeric
    "critical": "count",
    "high": "count",
    "medium": "count",
    "low": "count",
    "win": "count",
    # Ordinals and dates that happen to be numeric
    "order": "position in a sequence",
    "year": "year",
    # Dates and identifiers that happen to be numeric
    "birth_year": "year",
    "id": "identifier",
    "property_id": "identifier",
    "receipt_id": "identifier",
    "line_no": "position in a sequence",
    # A receipt line's quantity is measured in whatever that line's `unit`
    # says - 2.020 kg of apples, 1 ea of cheese - so the unit travels on the
    # line rather than here.
    "quantity": "in the same line's `unit` field",
}

# A field whose name ends in one of these is claiming a unit, so it has to be
# declared above. Deliberately excludes `_year`, because `per_year` and
# `interest_first_year` are amounts of money.
_UNIT_SUFFIXES = ("_pct", "_weeks", "_days", "_count", "_periods", "_months")


def declares_a_unit(name: str) -> bool:
    """True when a field name claims a unit and therefore must be declared."""
    return name.endswith(_UNIT_SUFFIXES)


def unit_of(name: str, currency: str) -> str:
    """What one field is measured in."""
    return NON_CURRENCY.get(name, currency)


def numeric_field_names(payload: Any) -> set[str]:
    """Every key in a response whose value is a number, at any depth."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, bool):
                continue  # bools are ints in Python and are not measurements
            if isinstance(value, int | float):
                found.add(key)
            else:
                found |= numeric_field_names(value)
    elif isinstance(payload, list | tuple):
        for item in payload:
            found |= numeric_field_names(item)
    return found


def units(
    payload: Any,
    currency: str,
    window: dict[str, Any] | None = None,
    as_at: str | None = None,
) -> dict[str, Any]:
    """
    The unit declaration that travels with a response.

    Only the exceptions actually present are listed. Shipping the whole
    NON_CURRENCY map with every response would be the bloat that declaring
    per-response was supposed to avoid.

    `window` is the period the figures cover, for a total or an average over
    time. `as_at` is the date a point-in-time figure was true - a balance is
    not "for a period", it is "on a day", and a response carrying balances
    with no date is asking the reader to assume it means now.
    """
    present = {
        name: NON_CURRENCY[name]
        for name in sorted(numeric_field_names(payload))
        if name in NON_CURRENCY
    }
    block: dict[str, Any] = {
        "amounts_in": currency,
        "note": f"Every numeric field is an amount in {currency} unless listed in `except`.",
    }
    if present:
        block["except"] = present
    if window is not None:
        block["window"] = window
    if as_at is not None:
        block["as_at"] = as_at
    return block
