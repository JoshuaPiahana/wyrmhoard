"""
The agent-facing contract.

Wyrmhoard's job is to compute; interpreting the figures is somebody else's.
That makes this interface the product, and three properties of it worth
pinning:

  * summaries by default, so an agent answering an ordinary question never
    receives three thousand rows naming every shop a family visited
  * every figure carries its provenance, because an interpreting model cannot
    caveat what it was not told
  * what the tool cannot see is a first-class answer, not something a caller
    has to think to ask about

Tool descriptions are tested too. They are the only documentation a model
gets, and it cannot ask a follow-up question.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import cache, categorise, db, mcp_server
from wyrmhoard.ingest import parse_csv

ACCOUNT = "38-9014-0123456-00"
OUTSIDE = "99-9999-9999999-99"


def load_some_data(rows: int = 8):
    """Real consecutive dates - a naive day counter stops parsing past the 31st."""
    lines = ["Account number,Date,Memo,Amount,Balance"]
    day = date(2025, 1, 1)
    for i in range(rows):
        lines.append(f"{ACCOUNT},{day.strftime('%d-%m-%Y')},NEW WORLD,-{50 + i}.00,{900 - i}.00")
        day += timedelta(days=1)
    parsed, _ = parse_csv("\n".join(lines), "test.csv")
    db.insert_transactions(parsed, "test.csv")
    cache.clear_all()
    categorise.recategorise_all()


def registered_tools() -> dict[str, str]:
    """Name -> description, as an agent would receive them."""
    tools = asyncio.run(mcp_server.server.list_tools())
    return {t.name: (t.description or "") for t in tools}


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
def test_the_expected_tools_are_exposed():
    names = set(registered_tools())
    for expected in (
        "get_overview",
        "describe_data_gaps",
        "get_spending_breakdown",
        "get_loans",
        "get_income",
        "get_entitlements",
        "get_recommendations",
        "import_document",
        "take_snapshot",
        "list_transactions",
    ):
        assert expected in names, f"{expected} is missing from the agent contract"


def test_every_tool_explains_itself():
    """
    A model gets the description and nothing else - it cannot ask what a tool
    does. One line is not enough for anything here.
    """
    for name, description in registered_tools().items():
        assert len(description) > 120, f"{name} has too thin a description for a model"


def test_the_server_tells_an_agent_how_to_behave():
    instructions = mcp_server.server.instructions or ""
    assert "describe_data_gaps" in instructions, "agents are not told to check for gaps"
    assert "not regulated financial advice" in instructions.lower()


def test_the_raw_transaction_tool_warns_against_itself():
    """The one tool returning raw records must say why to avoid it."""
    description = registered_tools()["list_transactions"]
    assert "sparingly" in description.lower()
    assert "get_spending_breakdown" in description


# ---------------------------------------------------------------------------
# Summaries by default
# ---------------------------------------------------------------------------
def test_the_summary_does_not_grow_with_the_data():
    """
    The minimisation principle, made measurable.

    Asserting a fixed size ratio would really be testing how much data the
    fixture loaded - it is 4x at sixty transactions and 154x at three and a
    half thousand. The property that actually matters is that the summary is
    roughly CONSTANT while the raw rows grow without bound, so an agent's
    ordinary question costs the same whether a household has one year of
    records or ten.
    """
    # Both volumes span several complete months. Comparing a few weeks against
    # a year would only show the summary filling in from "not enough data yet",
    # which is a one-time step rather than growth with volume.
    load_some_data(rows=200)
    small_summary = len(json.dumps(mcp_server.get_overview()))
    small_raw = len(json.dumps(mcp_server.list_transactions(limit=10_000)))

    load_some_data(rows=700)
    big_summary = len(json.dumps(mcp_server.get_overview()))
    big_raw = len(json.dumps(mcp_server.list_transactions(limit=10_000)))

    assert big_raw > small_raw * 3, "fixture did not actually add much data"
    assert (
        big_summary < small_summary * 1.1
    ), "the summary grows with transaction count - raw rows are leaking into it"


def test_the_overview_carries_no_merchant_names():
    """An ordinary question must not leak where a household shops."""
    load_some_data()
    assert "NEW WORLD" not in json.dumps(mcp_server.get_overview()).upper()


def test_raw_transactions_carry_a_privacy_note():
    load_some_data()
    assert "privacy_note" in mcp_server.list_transactions()


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool",
    ["get_overview", "get_spending_breakdown", "get_loans", "get_recommendations"],
)
def test_figures_travel_with_their_provenance(tool):
    load_some_data()
    result = getattr(mcp_server, tool)()

    assert "provenance" in result, f"{tool} returns figures with no provenance"
    p = result["provenance"]
    assert "categorised_pct" in p
    assert "figures_trustworthy" in p
    assert p["covering"]["from"] is not None


def test_the_overview_says_net_worth_excludes_property():
    """
    A household with a mortgage shows a large negative net worth. Without this
    caveat, that number is alarming and wrong.
    """
    load_some_data()
    assert "property" in mcp_server.get_overview()["net_worth"]["excludes"]


# ---------------------------------------------------------------------------
# Knowing what cannot be seen
# ---------------------------------------------------------------------------
def test_gaps_are_reported_when_an_account_is_missing():
    rows = []
    for i in range(8):
        rows.append(
            {
                "fingerprint": db.fingerprint(ACCOUNT, f"2025-01-{i + 1:02d}", "IN", 200.0, 1.0),
                "account": ACCOUNT,
                "date": f"2025-01-{i + 1:02d}",
                "memo": "TRANSFER FROM PARTNER",
                "match_text": "TRANSFER FROM PARTNER",
                "counterparty": OUTSIDE,
                "amount": 200.0,
                "balance": 1.0,
            }
        )
    db.insert_transactions(rows, "in.csv")
    cache.clear_all()

    gaps = mcp_server.describe_data_gaps()

    assert gaps["has_gaps"] is True
    assert any(OUTSIDE in g for g in gaps["gaps"])
    assert "Do not present a figure as settled" in gaps["guidance"]


def test_gaps_include_poor_categorisation():
    load_some_data()
    with db.connect() as conn:
        conn.execute("UPDATE transactions SET category='uncategorised', grp='unknown'")
    cache.clear_all()

    assert any("categorised" in g for g in mcp_server.describe_data_gaps()["gaps"])


def test_entitlements_always_carry_their_warning():
    """This estimate has already caused a wrong recommendation once."""
    result = mcp_server.get_entitlements()
    assert "authoritative" in result["warning"].lower()


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
def test_importing_a_missing_file_fails_cleanly(tmp_path):
    result = mcp_server.import_document(str(tmp_path / "nope.csv"))
    assert result["ok"] is False
    assert "No file at" in result["error"]


def test_importing_a_bank_export_reports_its_confidence(tmp_path):
    csv = tmp_path / "bank.csv"
    csv.write_text(
        "Account number,Date,Memo,Amount,Balance\n"
        f"{ACCOUNT},01-01-2025,COUNTDOWN,-45.00,900.00\n"
        f"{ACCOUNT},02-01-2025,MERCURY,-80.00,820.00\n",
        encoding="utf-8",
    )
    result = mcp_server.import_document(str(csv))

    assert result["kind"] == "bank_export"
    assert result["report"]["confidence"] in {"high", "medium", "low"}


def test_an_unreadable_file_is_reported_not_swallowed(tmp_path):
    junk = tmp_path / "junk.csv"
    junk.write_text("hello,world\nfoo,bar\n", encoding="utf-8")

    result = mcp_server.import_document(str(junk))
    assert result["ok"] is False
    assert result["report"]["confidence"] == "low"
