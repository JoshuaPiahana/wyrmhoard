"""
What the household owns, and who says it is worth that.

Every other figure in Wyrmhoard is derived: a balance comes from a bank export,
a gross salary comes from a payslip, a loan's interest rate is worked out from
its own transactions. A house value is the first number with nothing behind it
but somebody's word. That makes it the wrong thing to store as a bare number,
and the right thing to store with its provenance attached.

So a valuation is not a property of the house. It is a claim, made by a named
producer, about a value that was true on a particular date. Several such claims
can coexist and disagree - a council rating value from 2023 and an agent's
appraisal from last month are both real, and which one matters depends on the
question. Nothing here overwrites anything.

The producer contract
---------------------
Anything submitting data states six things about it: who produced it, when the
figure was true, when it arrived, how it was arrived at, where it came from,
and how confident the producer is. `docs/PRODUCERS.md` is the reference.

Two rules that look fussy and are not:

  A producer must name itself. There is no default, because "unknown origin" is
  the state this module exists to make impossible. A scraper, an agent and a
  person typing are all welcome; being anonymous is not.

  Confidence belongs to the producer and is stored as given. Wyrmhoard never
  upgrades it. A figure the household half-remembers is an `estimate`, and
  calling it an `appraisal` to make a later number look better defeats the
  entire point of recording the basis.

Wyrmhoard fetches nothing itself. A value found online got here because
somebody chose to run a program that went and looked - see SECURITY.md, and
`api/tests/test_offline.py`, which stops that changing quietly.
"""

from __future__ import annotations

from typing import Any

from . import db, provenance

# How a value was arrived at. Ordered most to least authoritative, which is
# also the order a caller should prefer when two claims cover the same date.
METHODS = ("appraisal", "council_rv", "purchase_price", "estimate")

METHOD_LABELS = {
    "appraisal": "Registered valuation or agent appraisal",
    "council_rv": "Council rating value",
    "purchase_price": "What you paid for it",
    "estimate": "Somebody's own estimate",
}

# Re-exported so callers and tests have one obvious place to look, but the
# rules themselves live in provenance.py - a second kind of submitted data
# must not be able to drift away from the first.
CONFIDENCES = provenance.CONFIDENCES
PRODUCER_KINDS = provenance.PRODUCER_KINDS


def record_valuation(
    label: str,
    value: float,
    method: str,
    observed_at: str,
    producer: str,
    source: str | None = None,
    confidence: str | None = None,
    note: str | None = None,
    is_primary: bool = False,
) -> dict[str, Any]:
    """
    Record one claim about what a property is worth.

    Creates the property if it is new. Raises ValueError with wording a caller
    can show to whoever submitted it, which is why the messages read as
    sentences rather than field names.
    """
    label = (label or "").strip()
    if not label:
        raise ValueError("A label is required - something like 'Home'.")

    producer = provenance.check_producer(producer)
    observed_at = provenance.check_observed_at(observed_at, "valuation")

    if method not in METHODS:
        raise ValueError(
            f"'{method}' is not a valuation method. Expected one of: "
            f"{', '.join(METHODS)}. Record what you actually have; calling an "
            "estimate an appraisal makes every figure derived from it wrong."
        )
    confidence = provenance.check_confidence(confidence)
    value = provenance.check_amount(value, "value")

    property_id = db.set_property(label, is_primary=is_primary)
    added = db.add_valuation(
        {
            "property_id": property_id,
            "value": value,
            "observed_at": observed_at,
            "producer": producer,
            "method": method,
            "source": source,
            "confidence": confidence,
            "note": note,
            "fingerprint": provenance.fingerprint(
                property_id, f"{value:.2f}", observed_at, method, producer
            ),
        }
    )

    return {
        "ok": True,
        "property_id": property_id,
        "label": label,
        "recorded": bool(added),
        "already_known": not added,
        "valuation": latest(property_id),
        "history_count": len(db.valuations(property_id)),
    }


def _decorate(row: dict[str, Any]) -> dict[str, Any]:
    """Add what can be said about a stored claim without recomputing it."""
    return {
        **row,
        "method_label": METHOD_LABELS.get(row.get("method", ""), row.get("method")),
        "age_days": provenance.age_of(row.get("observed_at")),
    }


def latest(property_id: int) -> dict[str, Any] | None:
    """The most recent claim by the date it was true, not the date it arrived."""
    rows = db.valuations(property_id)
    return _decorate(rows[0]) if rows else None


def history(property_id: int) -> list[dict[str, Any]]:
    """Every claim, newest first. Nothing is ever replaced, so this only grows."""
    return [_decorate(r) for r in db.valuations(property_id)]


def link_loan(property_id: int, account: str, linked: bool = True) -> dict[str, Any]:
    """
    Say which property a loan is secured on.

    Recorded but not yet used: nothing computes a loan-to-value ratio in this
    pass. It is stored now because it is a fact somebody knows today and would
    have to reconstruct later, and because the alternative - assuming every
    loan is a home loan - eventually counts a car against a house.
    """
    from . import accounts

    known = accounts.liability_accounts()
    if account not in known:
        raise ValueError(
            f"{account} is not one of this household's loan accounts. "
            f"Known loan accounts: {', '.join(sorted(known)) or 'none yet'}. "
            "Mark it as a liability on the Data tab first."
        )
    if not any(p["id"] == property_id for p in db.properties()):
        raise ValueError(f"No property with id {property_id}.")

    if linked:
        db.set_property_loan(property_id, account)
    else:
        db.clear_property_loan(property_id, account)
    return {"ok": True, "account": account, "property_id": property_id, "linked": linked}


def delete(property_id: int) -> dict[str, Any]:
    """Remove a property and everything recorded against it."""
    removed = db.delete_property(property_id)
    if not removed:
        raise ValueError(f"No property with id {property_id}.")
    return {"ok": True, "removed": property_id}


def summary() -> dict[str, Any]:
    """
    Every property, its latest claim, and what is still unknown.

    Carries the household's `housing` answer alongside, and reports a
    disagreement rather than resolving one. If they have said they rent and a
    property is recorded, both facts stay as they are and the conflict is
    surfaced: `facts.answer()` stores what the household said, and inferring an
    answer from a side effect is precisely what the agent-facing tools are told
    not to do.
    """
    from . import facts

    housing = facts.housing()
    links = db.property_loans()

    rows = []
    for prop in db.properties():
        current = latest(prop["id"])
        rows.append(
            {
                "id": prop["id"],
                "label": prop["label"],
                "is_primary": bool(prop["is_primary"]),
                "valuation": current,
                "valuation_count": len(db.valuations(prop["id"])),
                "loan_accounts": sorted(a for a, pid in links.items() if pid == prop["id"]),
                "available": current is not None,
                "reason": None if current else "No value recorded for this property yet.",
            }
        )

    conflicts = []
    if rows and housing.get("value") == "renting":
        conflicts.append(
            "A property is recorded, but the household's answer says they rent. "
            "Both have been left as they are - correct whichever is wrong."
        )
    if not rows and str(housing.get("value") or "").startswith("owner"):
        conflicts.append(
            "The household owns their home, but no property is recorded, so there "
            "is nothing to work equity out from."
        )

    return {
        "available": bool(rows),
        "count": len(rows),
        "properties": rows,
        "primary": next((r for r in rows if r["is_primary"]), None),
        "housing": housing,
        "conflicts": conflicts,
        "methods": list(METHODS),
        "note": (
            "Values here were submitted, not derived. Each carries who supplied "
            "it, how it was arrived at, and the date it was true."
        ),
    }
