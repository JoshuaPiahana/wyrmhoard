"""
Figures that say what they are.

The consumers of these numbers are increasingly programs nobody here wrote - a
jurisdiction pack, somebody's coaching script, an agent holding this response
next to a fitness tracker's. Each reads a float out of a dict and has to know
what it is holding, and `4816.85` beside `7823` is two bare numbers unless both
declare themselves.

The interesting test here is the last one. Units were nearly shipped with
`weeks_of_essentials` reported as an amount of money, because the MCP layer
renames `runway_weeks` on the way out and the renamed field neither appears in
the map nor carries a unit suffix. Nothing would have failed; an agent would
simply have been told a household had 2.7 dollars of essentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import figures

READ_TOOLS = [
    "get_overview",
    "describe_data_gaps",
    "get_spending_breakdown",
    "get_recurring_commitments",
    "get_loans",
    "get_income",
    "get_entitlements",
    "get_uncategorised",
    "get_property",
]


def test_a_response_declares_the_currency_its_amounts_are_in():
    from wyrmhoard import mcp_server

    units = mcp_server.get_overview()["units"]

    assert units["amounts_in"] == "NZD"
    assert "unless listed in `except`" in units["note"]


def test_only_the_exceptions_actually_present_are_declared():
    """
    Shipping the whole map with every response would be the bloat that
    declaring per-response was meant to avoid.
    """
    payload = {"spend": 100.0, "runway_weeks": 3.2}

    declared = figures.units(payload, "NZD")["except"]

    assert declared == {"runway_weeks": "weeks"}
    assert "share_pct" not in declared, "declared a unit for a field that is not here"


def test_a_response_with_no_exceptions_omits_the_block_entirely():
    assert "except" not in figures.units({"spend": 100.0}, "NZD")


def test_booleans_are_not_measurements():
    """`available: true` is not a quantity, and Python thinks bools are ints."""
    assert figures.numeric_field_names({"available": True, "total": 5.0}) == {"total"}


def test_numeric_fields_are_found_at_any_depth():
    payload = {"a": {"b": [{"runway_weeks": 3}, {"spend": 1.0}]}}
    assert figures.numeric_field_names(payload) == {"runway_weeks", "spend"}


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_every_read_tool_says_what_its_numbers_mean(tool):
    from wyrmhoard import mcp_server

    result = getattr(mcp_server, tool)()

    assert "units" in result, f"{tool} returns figures that do not say what they are"
    assert result["units"]["amounts_in"], f"{tool} declares no currency"


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_no_read_tool_reports_a_unit_bearing_field_as_money(tool):
    """
    A field whose name claims a unit - _pct, _weeks, _days, _count - must be
    declared. If one is not, it is being reported as an amount of currency,
    silently and wrongly.
    """
    from wyrmhoard import mcp_server

    result = getattr(mcp_server, tool)()
    payload = {k: v for k, v in result.items() if k not in ("units", "provenance")}

    undeclared = {
        name
        for name in figures.numeric_field_names(payload)
        if figures.declares_a_unit(name) and name not in figures.NON_CURRENCY
    }

    assert not undeclared, (
        f"{tool} returns {sorted(undeclared)}, which claim a unit but are not in "
        "figures.NON_CURRENCY - so they are being reported as amounts of money. "
        "Add them there with what they are actually measured in."
    )


def test_a_renamed_field_keeps_its_unit():
    """
    The regression this whole module exists for.

    `runway_weeks` becomes `weeks_of_essentials` in the MCP layer. The rename
    drops it out of every suffix convention, so it has to be declared under
    both names or an agent is told a household has 2.7 dollars of essentials.
    """
    assert figures.NON_CURRENCY["runway_weeks"] == "weeks"
    assert figures.NON_CURRENCY["weeks_of_essentials"] == "weeks"

    assert figures.unit_of("weeks_of_essentials", "NZD") == "weeks"
    assert figures.unit_of("spend_median", "NZD") == "NZD"
