"""
Where a figure came from, and how much to trust it.

Most numbers in Wyrmhoard are derived: a balance from a bank export, a salary
from a payslip, a loan's rate from its own transactions. Those carry their
provenance implicitly, because you can go and look at the rows they came from.

Some cannot. What a house is worth, what is in a KiwiSaver account, what the
consumer price index did last quarter - these arrive from outside, and a bare
number with no origin is one nobody can question later. So anything submitted
from outside states the same six things:

    producer     what created it            tool:rates-lookup
    observed_at  when the figure was TRUE   2026-03-01
    received_at  when Wyrmhoard stored it   (set for you)
    method       how it was arrived at      council_rv
    source       free text                  "PNCC rating notice"
    confidence   the producer's own claim   medium

This module holds the rules that apply to all of them, so a second kind of
submitted data cannot quietly diverge from the first. `docs/PRODUCERS.md` is
the reference for anyone writing a producer.

Two rules that look fussy and are not:

  A producer must name itself. There is no default, because "unknown origin"
  is the state this exists to make impossible.

  `observed_at` is required and never defaults to today. Dating an old figure
  as current is how a number somebody half-remembers becomes a valuation the
  tool reports with a straight face.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any

# `kind:name` - a person, an AI agent, or a program. The kind says what sort of
# mistake to expect: a person mistypes a digit, a scraper reads the wrong
# element on a redesigned page.
PRODUCER_KINDS = ("human", "agent", "tool")
_PRODUCER_RE = re.compile(rf"^({'|'.join(PRODUCER_KINDS)}):[a-z0-9][a-z0-9_.-]*$")

CONFIDENCES = ("high", "medium", "low")


def check_producer(producer: str) -> str:
    """Normalised producer name, or a refusal a caller can show verbatim."""
    producer = (producer or "").strip().lower()
    if not producer:
        raise ValueError(
            "A producer is required: say what is submitting this, as "
            f"'{PRODUCER_KINDS[0]}:name'. Data with no stated origin is exactly "
            "what this contract exists to prevent."
        )
    if not _PRODUCER_RE.match(producer):
        raise ValueError(
            f"'{producer}' is not a producer name. Expected '<kind>:<name>' where "
            f"kind is one of {', '.join(PRODUCER_KINDS)} - for example "
            "'human:dashboard', 'agent:mcp', 'tool:rates-lookup'."
        )
    return producer


def check_observed_at(observed_at: Any, what: str = "figure") -> str:
    """The date a figure was true. Required, and never in the future."""
    if not observed_at:
        raise ValueError(
            f"An observed_at date is required: the day this {what} was true, "
            "which is not necessarily today. Defaulting it would quietly turn a "
            "number somebody half-remembers into a current one."
        )
    try:
        when = date.fromisoformat(str(observed_at))
    except ValueError as exc:
        raise ValueError(f"'{observed_at}' is not an ISO date (YYYY-MM-DD).") from exc
    if when > date.today():
        raise ValueError(f"observed_at {when.isoformat()} is in the future.")
    return when.isoformat()


def check_confidence(confidence: str | None) -> str | None:
    """
    The producer's own claim, stored as given.

    Wyrmhoard never upgrades one. A figure somebody half-remembers is low
    confidence, and relabelling it to make a later number look better defeats
    the point of recording it at all.
    """
    if confidence is None:
        return None
    if confidence not in CONFIDENCES:
        raise ValueError(
            f"'{confidence}' is not a confidence. Expected one of: {', '.join(CONFIDENCES)}."
        )
    return confidence


def check_amount(value: Any, what: str = "value", allow_negative: bool = False) -> float:
    """A number, and by default a positive one."""
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{value}' is not a number.") from exc
    if not allow_negative and amount <= 0:
        raise ValueError(f"A {what} must be positive.")
    return amount


def fingerprint(*parts: Any) -> str:
    """
    Identity of a claim, so re-submitting it is a no-op.

    Deliberately excludes when it arrived: the same producer reporting the same
    figure for the same date twice is one claim, whether that happens twice in
    a minute or twice in a year. It includes the producer, because two sources
    independently agreeing is genuinely more information than one source
    repeating itself.
    """
    payload = "|".join(str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def received_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def age_of(observed_at: str | None) -> int | None:
    """Days since the figure was true, or None if that cannot be worked out."""
    try:
        return (date.today() - date.fromisoformat(str(observed_at))).days
    except (ValueError, TypeError):
        return None
