"""
The lens's arithmetic, on figures it did not have to fetch.

Rules enforced here, each of which is what makes a reading safe to put in
front of a family:

  1. Only complete fortnights are read. An unfinished one looks like thrift.
  2. A group the lens cannot place is counted and named, never dropped.
  3. Every figure says its unit and where it came from.
  4. What the household did not earn is stated separately, not hidden and
     not counted as earnings.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import babylon

WINDOW = {
    "from": "2026-03-04",
    "to": "2026-09-01",
    "period": "fortnight",
    "complete_periods": 3,
    "anchor": "2026-03-04",
    "anchor_source": "household.yml",
}
STARTS = ["2026-03-04", "2026-03-18", "2026-04-01"]


def _lens(**overrides):
    lens = {
        "tenth": 0.10,
        "window_fortnights": 3,
        "earnings_exclude": ["gift_received"],
        "purposes": {"necessities": ["essential", "commitment"], "enjoyments": ["discretionary"]},
        "merchants_named": 2,
    }
    lens.update(overrides)
    return lens


def _periods(n: int, incomplete_tail: int = 1) -> list[dict]:
    start = date(2026, 3, 4)
    out = []
    for i in range(n):
        lo = start + timedelta(days=14 * i)
        out.append(
            {
                "start": lo.isoformat(),
                "end": (lo + timedelta(days=13)).isoformat(),
                "complete": i < n - incomplete_tail,
            }
        )
    return out


def _row(category: str, group: str, totals: list[float]) -> dict:
    return {
        "category": category,
        "label": category,
        "group": group,
        "totals": totals,
        "transactions": [1 if t else 0 for t in totals],
        "total": sum(totals),
        "per_period": None,
    }


# ---------------------------------------------------------------------------
# Rule 1: only complete fortnights
# ---------------------------------------------------------------------------
def test_the_window_is_the_last_n_complete_periods_and_the_n_before():
    periods = _periods(8, incomplete_tail=1)  # slots 0..6 complete, 7 in progress
    now, before = babylon.windows(periods, 3)
    assert now == [4, 5, 6]
    assert before == [1, 2, 3]
    assert 7 not in now, "the fortnight in progress must never be read"


def test_a_short_ledger_has_no_before_window():
    now, before = babylon.windows(_periods(4), 3)
    assert now == [0, 1, 2]
    assert before == []


# ---------------------------------------------------------------------------
# Rule 4: earned is earned
# ---------------------------------------------------------------------------
def test_the_first_cure_reads_the_tenth_against_what_was_kept():
    earned = [2000.0, 2000.0, 2000.0]
    spent = [1900.0, 1900.0, 1900.0]
    gifts = [0.0, 500.0, 0.0]
    r = babylon.cure_one(earned, spent, gifts, _lens(), "NZD", STARTS, WINDOW)

    assert r["figures"]["kept_per_fortnight"]["value"] == 100.0
    assert r["principle_asks"]["kept_per_fortnight"]["value"] == 200.0
    assert r["gap"]["value"] == 100.0
    assert r["figures"]["kept_share"]["value"] == pytest.approx(0.05)
    # Gifts never touch the earnings figure, and are named.
    assert r["figures"]["earned_per_fortnight"]["value"] == 2000.0
    assert r["figures"]["gifts_in_window"]["value"] == 500.0
    assert any("gifts" in c for c in r["caveats"])
    assert r["series"]["target"]["value"] == 200.0
    assert r["series"]["rows"][0]["totals"] == [100.0, 100.0, 100.0]


def test_spending_more_than_was_earned_is_stated_as_a_number_not_a_verdict():
    r = babylon.cure_one([2000.0] * 3, [2300.0] * 3, [0.0] * 3, _lens(), "NZD", STARTS, WINDOW)
    assert r["figures"]["kept_per_fortnight"]["value"] == -300.0
    assert "$300 more than came in" in r["reading"]
    for word in ("waste", "shame", "bad", "fail"):
        assert word not in r["reading"].lower()


# ---------------------------------------------------------------------------
# Rule 2: nothing dropped
# ---------------------------------------------------------------------------
def test_an_unplaced_group_is_counted_and_named():
    rows = [
        _row("groceries", "essential", [500.0, 500.0, 500.0]),
        _row("takeaways", "discretionary", [100.0, 100.0, 100.0]),
        _row("mystery", "unknown", [50.0, 50.0, 50.0]),
        _row("odd", "renamed_by_household", [10.0, 10.0, 10.0]),
    ]
    r, unplaced = babylon.cure_two(
        [2000.0] * 3, rows, [0, 1, 2], [], {}, _lens(), "NZD", STARTS, WINDOW
    )
    assert r["figures"]["necessities_per_fortnight"]["value"] == 500.0
    assert r["figures"]["enjoyments_per_fortnight"]["value"] == 100.0
    assert unplaced == [
        {"group": "renamed_by_household", "total": 30.0},
        {"group": "unknown", "total": 150.0},
    ]
    assert any("unknown" in c and "renamed_by_household" in c for c in r["caveats"])


def test_the_before_window_is_reported_when_there_is_one():
    rows = [_row("groceries", "essential", [400.0, 400.0, 400.0, 500.0, 500.0, 500.0])]
    r, _ = babylon.cure_two(
        [2000.0] * 3, rows, [3, 4, 5], [0, 1, 2], {}, _lens(), "NZD", STARTS, WINDOW
    )
    assert r["figures"]["necessities_per_fortnight"]["value"] == 500.0
    assert r["figures"]["necessities_before"]["value"] == 400.0
    assert "fortnights before" in r["reading"]


def test_the_fold_row_keeps_its_count_in_the_table():
    merchants = {
        "enjoyments": {
            "series": [
                {
                    "merchant": "EXAMPLE GAMES",
                    "label": "EXAMPLE GAMES",
                    "total": 300.0,
                    "transactions": [2, 1],
                    "per_period": 150.0,
                },
                {
                    "merchant": None,
                    "label": "everything else",
                    "merchants": 7,
                    "total": 90.0,
                    "transactions": [3, 4],
                    "per_period": 45.0,
                },
            ]
        }
    }
    r, _ = babylon.cure_two(
        [2000.0] * 3, [], [0, 1, 2], [], merchants, _lens(), "NZD", STARTS, WINDOW
    )
    table = r["tables"][0]
    assert table["rows"][0] == ["EXAMPLE GAMES", 300.0, 3, 150.0]
    assert table["rows"][1] == ["everything else (7 shops)", 90.0, 7, 45.0]
    assert "EXAMPLE GAMES" in r["options"][0]
    assert "enjoyment" in r["options"][0]


# ---------------------------------------------------------------------------
# Rule 3: every figure is self-describing
# ---------------------------------------------------------------------------
def test_every_figure_carries_a_unit_and_a_source():
    one = babylon.cure_one([1.0], [1.0], [0.0], _lens(), "NZD", STARTS[:1], WINDOW)
    two, _ = babylon.cure_two([1.0], [], [0], [], {}, _lens(), "NZD", STARTS[:1], WINDOW)
    for reading in (one, two):
        for name, fig in reading["figures"].items():
            assert set(fig) >= {"value", "unit", "basis", "source"}, name
            assert fig["unit"] and fig["source"], name
        assert reading["gap"]["unit"]


def test_the_lens_file_is_its_own_opinion_and_loads():
    lens = babylon.load_lens()
    assert lens["tenth"] == 0.10
    assert "necessities" in lens["purposes"]
    assert place_all(lens) == {"essential": "necessities", "discretionary": "enjoyments"}


def place_all(lens):
    return {g: babylon.place(g, lens["purposes"]) for g in ("essential", "discretionary")}


def test_an_empty_setup_yields_no_cannot_see_and_a_gap_is_named():
    assert babylon._cannot_see({}) == []
    seen = babylon._cannot_see(
        {
            "coverage": {"categorised_pct": 61.0, "trustworthy": False},
            "todo": [{"label": "x", "why": "y"}],
        }
    )
    assert len(seen) == 2
    assert seen[0].startswith("61.0%")
    assert seen[1] == "x: y"
