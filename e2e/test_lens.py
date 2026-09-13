"""
A lens, end to end: the program reads the live API, the server lists what it
wrote, and the dashboard renders it without knowing its name.

Two things are proven here that a unit test cannot. First, that the API
actually serves what the lens asks for - `/series?by=merchant` among them -
because a consumer's own tests may not import the core and so can only ever
check arithmetic on fixtures. Second, that the Lens tab is generic: a second
lens the dashboard has never heard of, written straight into the directory,
appears in the dropdown and renders when chosen.

The lenses directory is a throwaway volume under docker-compose.e2e.yml,
shared read-write with this container and read-only with nginx, for the same
reason the ledger is: the real one holds the household's real numbers.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

CONSUMERS = Path("/consumers")
LENSES = Path("/lenses")
if not (CONSUMERS / "babylon" / "babylon.py").exists():
    raise RuntimeError(
        f"{CONSUMERS} is not mounted - docker-compose.yml's e2e service must mount ./consumers"
    )
if not LENSES.is_dir():
    raise RuntimeError(f"{LENSES} is not mounted - docker-compose.e2e.yml must mount e2e-lenses")
sys.path.insert(0, str(CONSUMERS / "babylon"))

import babylon  # noqa: E402

#: A lens the dashboard has never seen. The smallest document that exercises
#: every block the renderer draws: figures, options, a series with a target,
#: a table, caveats, and the standing footer.
EXAMPLE = {
    "lens": "example",
    "title": "An example lens",
    "philosophy": "Whatever this household says it values. Synthetic, for the browser tests.",
    "refresh": "./hoard lens example",
    "generated_at": "2026-01-01T00:00:00",
    "ledger_ends": "2025-12-31",
    "unit": "NZD",
    "window": {
        "from": "2025-10-01",
        "to": "2025-12-31",
        "period": "fortnight",
        "complete_periods": 2,
    },
    "readings": [
        {
            "cure": 1,
            "name": "The one reading",
            "principle": "Keep more than you spend.",
            "available": True,
            "figures": {
                "kept_per_fortnight": {
                    "value": 120.0,
                    "unit": "NZD",
                    "basis": "median",
                    "source": "GET /series",
                },
                "kept_share": {
                    "value": 0.06,
                    "unit": "ratio",
                    "basis": "kept over earned",
                    "source": "GET /series",
                },
            },
            "principle_asks": {"kept_share": 0.1},
            "gap": {"value": 80.0, "unit": "NZD", "basis": "per fortnight", "source": "derived"},
            "reading": "A typical fortnight kept $120. The example asks for $200.",
            "options": ["Move $200 on pay day."],
            "series": {
                "title": "Kept each fortnight",
                "periods": ["2025-10-01", "2025-10-15"],
                "rows": [{"label": "kept", "totals": [100.0, 140.0]}],
                "target": {"label": "asked", "value": 200.0},
            },
            "tables": [
                {
                    "title": "Where it went",
                    "columns": ["Shop", "Total", "Visits"],
                    "units": ["text", "NZD", "count"],
                    "rows": [["EXAMPLE SHOP", 300.0, 3], ["everything else (4 shops)", 90.0, 7]],
                }
            ],
            "caveats": ["Synthetic."],
        }
    ],
    "unplaced": [{"group": "unknown", "total": 12.0}],
    "cannot_see": ["Nothing, this is made up."],
}


def _listing(base_url: str) -> list[str]:
    with urllib.request.urlopen(f"{base_url}/lenses/") as response:
        return sorted(e["name"] for e in json.load(response) if e["type"] == "file")


@pytest.fixture(scope="module")
def readings(base_url: str):
    """Run the real lens against the live server, and plant the synthetic one beside it."""
    lens = babylon.load_lens()
    doc = babylon.read(f"{base_url}/api", lens)
    (LENSES / "babylon.json").write_text(json.dumps(doc), encoding="utf-8")
    (LENSES / "example.json").write_text(json.dumps(EXAMPLE), encoding="utf-8")
    try:
        yield {"babylon": doc, "example": EXAMPLE}
    finally:
        for path in LENSES.glob("*.json"):
            path.unlink()


# ---------------------------------------------------------------------------
# Before any lens exists
# ---------------------------------------------------------------------------
def test_with_no_lens_the_tab_explains_itself(dashboard: Page, base_url: str, console_errors):
    """Zero input still gets a useful screen - and no 404 in the console."""
    assert _listing(base_url) == [], "another test left a lens behind"
    dashboard.click('nav.tabs button[data-tab="lens"]')
    panel = dashboard.locator('[data-panel="lens"]')
    expect(panel).to_contain_text("No lens has been run yet")
    expect(panel.locator("#lens-pick")).to_be_disabled()
    assert console_errors == [], console_errors


# ---------------------------------------------------------------------------
# The program against the live API
# ---------------------------------------------------------------------------
def test_the_lens_reads_the_live_api_and_emits_the_contract(readings):
    doc = readings["babylon"]
    assert {
        "lens",
        "title",
        "philosophy",
        "generated_at",
        "ledger_ends",
        "unit",
        "window",
        "readings",
        "unplaced",
        "cannot_see",
        "refresh",
    } <= set(doc)
    assert doc["window"]["period"] == "fortnight"
    assert doc["window"]["complete_periods"] >= 1, "the synthetic sample should span fortnights"
    assert [r["cure"] for r in doc["readings"]] == [1, 2, 3, 4, 5, 6, 7]
    for reading in doc["readings"]:
        assert reading["available"] is True
        for name, fig in reading["figures"].items():
            assert fig["unit"] and fig["source"], f"{name} is a bare number"
        if "series" in reading:
            assert reading["series"]["periods"], "a reading with no periods drew nothing"
    # The merchant read path the lens exists to use: a table per purpose,
    # every row either a named shop or the fold that keeps the sum true.
    two = doc["readings"][1]
    assert two["tables"], "no merchant tables - is /series?by=merchant served?"
    for table in two["tables"]:
        assert table["columns"][0] == "Shop"
        assert all(len(row) == len(table["columns"]) for row in table["rows"])


def test_the_server_lists_what_was_written(readings, base_url: str):
    assert _listing(base_url) == ["babylon.json", "example.json"]


# ---------------------------------------------------------------------------
# The dashboard, generic over lenses
# ---------------------------------------------------------------------------
def test_the_tab_renders_a_lens_it_has_never_heard_of(dashboard: Page, readings, console_errors):
    dashboard.click('nav.tabs button[data-tab="lens"]')
    panel = dashboard.locator('[data-panel="lens"]')
    pick = panel.locator("#lens-pick")

    expect(pick).to_be_enabled()
    expect(pick.locator("option")).to_have_count(2)
    # First alphabetically is the default when nothing is remembered.
    expect(panel.locator(".lens-head h2")).to_have_text(readings["babylon"]["title"])
    expect(panel.locator(".reading")).to_have_count(7)

    pick.select_option("example")
    expect(panel.locator(".lens-head h2")).to_have_text("An example lens")
    expect(panel.locator(".reading")).to_have_count(1)
    expect(panel.locator(".reading .reading-text")).to_contain_text("kept $120")
    expect(panel.locator(".reading .options li")).to_have_text("Move $200 on pay day.")
    expect(panel.locator(".lens-chart svg rect")).to_have_count(2)
    expect(panel.locator(".lens-chart svg line.target")).to_have_count(1)
    expect(panel.locator(".lens-table tbody tr")).to_have_count(2)
    expect(panel.locator(".lens-table tbody tr").last).to_contain_text("everything else (4 shops)")
    expect(panel.locator(".figures dd").first).to_have_text("$120")
    expect(panel.locator(".figures dd").nth(1)).to_have_text("6%")
    expect(panel).to_contain_text("Not placed by this lens")
    expect(panel).to_contain_text("Nothing, this is made up.")
    assert console_errors == [], console_errors


def test_the_chosen_lens_survives_a_reload(dashboard: Page, readings):
    dashboard.click('nav.tabs button[data-tab="lens"]')
    dashboard.locator("#lens-pick").select_option("example")
    dashboard.reload(wait_until="networkidle")
    panel = dashboard.locator('[data-panel="lens"]')
    expect(panel.locator(".lens-head h2")).to_have_text("An example lens", timeout=15_000)
    expect(dashboard.locator("#lens-pick")).to_have_value("example")


def test_every_reading_names_the_window_it_covers(dashboard: Page, readings):
    """The staleness line: when it was read, and where the ledger ended."""
    dashboard.click('nav.tabs button[data-tab="lens"]')
    dashboard.locator("#lens-pick").select_option("babylon")
    when = dashboard.locator('[data-panel="lens"] .lens-when')
    expect(when).to_contain_text("from a ledger ending")
    expect(when).to_contain_text("complete fortnights")
    expect(when).to_contain_text("./hoard lens")
