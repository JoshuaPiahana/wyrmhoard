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
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import cache, categorise, config, db, mcp_server
from wyrmhoard.ingest import parse_csv

ACCOUNT = "38-9014-0123456-00"
OUTSIDE = "99-9999-9999999-99"

# Merchants no public rule can place, which is the whole point of the long
# tail: a supermarket is in rules.yml, the shop on the corner never will be.
UNKNOWN = "SP QUAYSIDE 4829"
OTHER_UNKNOWN = "EFTPOS ARROWFIELD LTD"


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


def load_unknown_spending(memo: str, rows: int = 3, amount: float = -12.50):
    """Spending no rule in rules.yml can claim."""
    lines = ["Account number,Date,Memo,Amount,Balance"]
    day = date(2025, 3, 1)
    for _ in range(rows):
        lines.append(f"{ACCOUNT},{day.strftime('%d-%m-%Y')},{memo},{amount:.2f},500.00")
        day += timedelta(days=1)
    parsed, _ = parse_csv("\n".join(lines), "unknown.csv")
    db.insert_transactions(parsed, "unknown.csv")
    cache.clear_all()
    categorise.recategorise_all()


def registered_tools() -> dict[str, str]:
    """Name -> description, as an agent would receive them."""
    tools = asyncio.run(mcp_server.server.list_tools())
    return {t.name: (t.description or "") for t in tools}


@pytest.fixture
def private_config(tmp_path, monkeypatch):
    """
    A config directory of the test's own, holding only the public files.

    Teaching a rule writes to disk. Without this it writes into whichever
    config/ the suite was launched against, which on a development machine is
    the household's own. household.yml and learned.yml are deliberately not
    copied either: a developer with real learned rules already present must
    get the same result as CI running with none.
    """
    private = tmp_path / "config"
    private.mkdir()
    for name in ("rules.yml", "nz_rates.yml", "household.example.yml"):
        source = config.CONFIG_DIR / name
        if source.exists():
            shutil.copy(source, private / name)

    monkeypatch.setattr(config, "CONFIG_DIR", private)
    config.reload()
    categorise.compiled_rules.cache_clear()

    yield private

    config.reload()
    categorise.compiled_rules.cache_clear()


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
        "get_uncategorised",
        "teach_category",
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


# ---------------------------------------------------------------------------
# The categorisation long tail
# ---------------------------------------------------------------------------
def test_the_write_tool_says_that_it_writes():
    """An agent has to know a call has consequences before it makes it."""
    description = registered_tools()["teach_category"]
    assert "writes" in description.lower()
    assert "learned.yml" in description


def test_uncategorised_spending_comes_back_grouped_by_merchant(private_config):
    """
    Grouping is what makes this usable. One takeaway visited thirty times is a
    single question to answer, and an agent handed thirty rows would have to
    work that out for itself - over a response thirty times the size.
    """
    load_unknown_spending(UNKNOWN, rows=3, amount=-12.50)
    load_unknown_spending(OTHER_UNKNOWN, rows=1, amount=-40.00)

    result = mcp_server.get_uncategorised()
    groups = {g["merchant"]: g for g in result["groups"]}

    assert result["returned"] == 2, f"four transactions did not collapse to two: {groups}"
    assert groups[UNKNOWN]["count"] == 3
    assert groups[UNKNOWN]["total"] == 37.50
    # Grouped on the cleaned memo, so the bank's plumbing does not split a
    # merchant across two groups.
    assert groups["ARROWFIELD LTD"]["count"] == 1


def test_an_invented_category_is_refused_before_anything_is_written(private_config):
    """
    A category carries a group, and the group drives the coaching maths. A
    model that could invent one would file spending outside every group the
    maths knows about - and the typo would be permanent.
    """
    load_unknown_spending(UNKNOWN, rows=2)

    result = mcp_server.teach_category(match="QUAYSIDE", category="artisanal_cheese")

    assert result["ok"] is False
    assert "artisanal_cheese" in result["error"]
    assert "groceries" in result["valid_categories"], "the error must say what is allowed"
    assert not (private_config / "learned.yml").exists(), "a refused category still wrote a rule"


def test_a_pattern_too_short_to_match_safely_is_refused(private_config):
    """A two-letter literal appears inside unrelated memos, and miscounts silently."""
    result = mcp_server.teach_category(match="AA", category="takeaways")

    assert result["ok"] is False
    # The suggested way out has to be usable as written. An escape mangled in
    # the message hands back a pattern that does not mean what it says.
    assert r"re:\bAA\b" in result["error"], "the error should offer a working regex"


def test_a_taught_rule_lands_where_config_rules_reads_it(private_config):
    load_unknown_spending(UNKNOWN, rows=2)

    mcp_server.teach_category(match="QUAYSIDE", category="takeaways")

    learned = yaml.safe_load((private_config / "learned.yml").read_text(encoding="utf-8"))
    assert learned["categories"]["takeaways"]["match"] == ["QUAYSIDE"]

    # The shape only matters because config.rules() has to merge it, so assert
    # on the merge rather than on the file alone.
    merged = config.rules()["categories"]["takeaways"]
    assert "QUAYSIDE" in merged["match"]
    assert merged["group"] == "discretionary", "a learned pattern must not change the group"

    # rules.yml is public. A household's local merchants must never reach it.
    assert "QUAYSIDE" not in (private_config / "rules.yml").read_text(encoding="utf-8")


def test_teaching_reports_how_many_transactions_it_caught(private_config):
    load_unknown_spending(UNKNOWN, rows=5)
    load_unknown_spending(OTHER_UNKNOWN, rows=2)

    result = mcp_server.teach_category(match="QUAYSIDE", category="takeaways")

    assert result["ok"] is True
    assert result["matched"] == 5, "the count is this rule's matches, not the whole category"

    # And the caller can see the effect without a second round trip.
    remaining = [g["merchant"] for g in mcp_server.get_uncategorised()["groups"]]
    assert remaining == ["ARROWFIELD LTD"]
