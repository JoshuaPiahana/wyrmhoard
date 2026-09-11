"""
The health lens, and the one thing it must never do: lose a line.

A split that quietly drops what it cannot place is the exact shape of wrong
number the dashboard's old "choices" figure was - a total that looked complete
and was not. So every test here checks both halves: that the groups sum to
the receipt, and that anything unplaced is named rather than vanished.

`place()` and `split()` are pure, so nothing here needs Wyrmhoard running.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grocery_health import UNPLACED, load_vocabulary, place, split

VOCAB = load_vocabulary()

ITEMS = [
    {
        "description": "Example apples loose",
        "source_category": "Fruit & Vegetables",
        "line_total": 7.07,
    },
    {
        "description": "Example cheese 1kg",
        "source_category": "Deli & Chilled Foods",
        "line_total": 16.49,
    },
    {
        "description": "Example crackers",
        "source_category": "Biscuits & Crackers",
        "line_total": 2.00,
    },
    {
        "description": "Example cola 1.5l",
        "source_category": "Drinks - Hot & Cold",
        "line_total": 3.50,
    },
    {
        "description": "Example dish soap",
        "source_category": "Cleaning & Homecare",
        "line_total": 4.99,
    },
    {"description": "Paper Bags", "source_category": None, "line_total": 1.50},
    {"description": "Pick up Fee", "source_category": None, "line_total": 3.50},
]


def test_the_shops_department_decides_first():
    assert place(ITEMS[0], VOCAB) == "fresh"
    assert place(ITEMS[2], VOCAB) == "processed"
    assert place(ITEMS[3], VOCAB) == "drinks"
    assert place(ITEMS[4], VOCAB) == "household"


def test_a_line_with_no_department_falls_back_to_its_description():
    """Fee lines on an online invoice, and every line on an in-store receipt."""
    assert place(ITEMS[5], VOCAB) == "household"
    assert place(ITEMS[6], VOCAB) == "service"


def test_the_groups_add_up_to_the_receipt():
    result = split(ITEMS, VOCAB)
    assert result["sum"] == pytest.approx(sum(i["line_total"] for i in ITEMS))
    assert result["sum"] == pytest.approx(39.05)


def test_an_unrecognised_line_is_named_not_dropped():
    """
    The rule this file exists for.

    A line the lens cannot place still counts toward the total, under
    `unplaced`, and its description is returned so somebody can add a pattern.
    The alternative - leaving it out - produces a split that looks complete and
    is wrong by exactly the amount that was quietly removed.
    """
    mystery = {"description": "Unknown thing", "source_category": "Aisle 7", "line_total": 9.99}
    result = split([*ITEMS, mystery], VOCAB)

    assert result["totals"][UNPLACED] == pytest.approx(9.99)
    assert result["unplaced"] == ["Unknown thing"]
    assert result["sum"] == pytest.approx(39.05 + 9.99), "unplaced money still counts"


def test_a_free_line_places_without_disturbing_the_total():
    free = {"description": "Promotional giveaway", "source_category": "Toys", "line_total": 0.0}
    result = split([*ITEMS, free], VOCAB)
    assert result["sum"] == pytest.approx(39.05)
    assert "Promotional giveaway" in result["unplaced"], (
        "no rule for Toys yet, and that is reported"
    )


def test_every_group_the_vocabulary_names_has_a_label():
    """A group with no label renders as its key, which reads as a bug on screen."""
    named = set(VOCAB["labels"])
    used = set(VOCAB["by_source_category"].values()) | {g for _, g in VOCAB["by_description"]}
    assert used <= named, f"groups used without a label: {sorted(used - named)}"
