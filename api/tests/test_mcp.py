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

from wyrmhoard import cache, categorise, config, db, documents, mcp_server
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


def load_a_receipt(paid_on: str = "2025-01-03") -> int:
    """
    One itemised receipt, linked to the third row `load_some_data` makes.

    Invented products. Real ones stay in `data/`, which the guard blocks.
    """
    load_some_data()
    result = documents.submit(
        producer="tool:example-receipts",
        kind="receipt",
        merchant="New World Example",
        observed_at=paid_on,
        stated_total=52.00,
        items=[
            {
                "description": "Blue milk 2l",
                "quantity": 2,
                "unit": "ea",
                "unit_price": 5.00,
                "line_total": 10.00,
                "source_category": "Dairy & Eggs",
            },
            {
                "description": "Bananas loose",
                "quantity": 1.12,
                "unit": "kg",
                "unit_price": 3.75,
                "line_total": 4.20,
                "source_category": "Fruit & Vegetables",
            },
            {
                "description": "Dish soap 500ml",
                "quantity": 1,
                "unit": "ea",
                "line_total": 37.80,
                "source_category": "Cleaning & Homecare",
            },
        ],
    )
    documents.link(result["document_id"])
    cache.clear_all()
    return result["document_id"]


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
        "get_uncategorised",
        "get_spending_over_time",
        "get_notes",
        "teach_category",
        "import_document",
        "record_note",
        "list_transactions",
        "list_receipts",
        "get_receipt",
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


RAW_TOOLS = ("list_transactions", "get_receipt")


@pytest.mark.parametrize("tool", RAW_TOOLS)
def test_every_raw_tool_warns_against_itself(tool):
    """
    A tool returning raw records must say why to avoid it, and name the
    summary to prefer. There were one of these for a year; now there are two,
    and the rule is the same for each - a model reads the description and
    nothing else, so the description is where the restraint has to live.
    """
    description = registered_tools()[tool]
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
    assert big_summary < small_summary * 1.1, (
        "the summary grows with transaction count - raw rows are leaking into it"
    )


def test_the_overview_carries_no_merchant_names():
    """An ordinary question must not leak where a household shops."""
    load_some_data()
    assert "NEW WORLD" not in json.dumps(mcp_server.get_overview()).upper()


def test_raw_transactions_carry_a_privacy_note():
    load_some_data()
    assert "privacy_note" in mcp_server.list_transactions()


def test_a_receipt_carries_a_privacy_note():
    receipt_id = load_a_receipt()
    assert "privacy_note" in mcp_server.get_receipt(receipt_id)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool",
    ["get_overview", "get_spending_breakdown", "get_loans", "get_property"],
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


def test_the_server_tells_agents_it_holds_no_tax_rules():
    """
    The estimate this replaced caused a wrong recommendation once.

    A model asked "are we owed anything?" will answer from its own training if
    the server does not say otherwise, and a confident wrong figure about a
    benefit is the most expensive mistake this project can make. So the
    instructions have to say plainly that the tool cannot know.
    """
    instructions = mcp_server.server.instructions.lower()
    assert "no tax or benefit rules" in instructions
    assert "tax office" in instructions


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
def inbox_file(name: str, body: str) -> str:
    """
    Write a document where an agent is allowed to read one.

    import_document only reads inside the data directory now. It used to accept
    any path the container could reach, which on a tool an agent drives is the
    wrong default - a path arrives from an email or a web page as easily as
    from the person asking.
    """
    from wyrmhoard import config

    inbox = config.DATA_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_importing_a_missing_file_fails_cleanly():
    from wyrmhoard import config

    result = mcp_server.import_document(str(config.DATA_DIR / "inbox" / "nope.csv"))
    assert result["ok"] is False
    assert "No file at" in result["error"]


def test_a_path_outside_the_data_directory_is_refused(tmp_path):
    """
    The containment check, on the surface most exposed to a path somebody else
    chose. Before this, the whole check was "does the file exist".
    """
    outside = tmp_path / "etc" / "passwd"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("root:x:0:0\n", encoding="utf-8")

    result = mcp_server.import_document(str(outside))

    assert result["ok"] is False
    assert "outside" in result["error"]


@pytest.mark.parametrize(
    "hostile", ["../../../etc/passwd", "..\\..\\windows\\win.ini", "/etc/hosts"]
)
def test_traversal_out_of_the_data_directory_is_refused(hostile):
    result = mcp_server.import_document(hostile)
    assert result["ok"] is False
    assert "outside" in result["error"] or "No file at" in result["error"]


def test_a_file_type_the_tool_does_not_read_is_refused_rather_than_guessed_at():
    """
    "Not a PDF" used to mean "is a CSV", so anything else was fed to the CSV
    parser and failed confusingly instead of being turned away.
    """
    path = inbox_file("secrets.yml", "password: hunter2\n")
    result = mcp_server.import_document(path)

    assert result["ok"] is False
    assert ".csv" in result["error"] and ".pdf" in result["error"]


def test_importing_a_bank_export_reports_its_confidence():
    path = inbox_file(
        "bank.csv",
        "Account number,Date,Memo,Amount,Balance\n"
        f"{ACCOUNT},01-01-2025,COUNTDOWN,-45.00,900.00\n"
        f"{ACCOUNT},02-01-2025,MERCURY,-80.00,820.00\n",
    )
    result = mcp_server.import_document(path)

    assert result["kind"] == "bank_export"
    assert result["report"]["confidence"] in {"high", "medium", "low"}


def test_an_unreadable_file_is_reported_not_swallowed():
    path = inbox_file("junk.csv", "hello,world\nfoo,bar\n")

    result = mcp_server.import_document(path)
    assert result["ok"] is False
    assert result["report"]["confidence"] == "low"


def test_an_import_records_which_agent_submitted_it():
    """The point of the producer contract: nothing arrives anonymously."""
    from wyrmhoard import db

    path = inbox_file(
        "traceable.csv",
        f"Account number,Date,Memo,Amount,Balance\n{ACCOUNT},03-01-2025,PAK N SAVE,-60.00,760.00\n",
    )
    mcp_server.import_document(path)

    logged = [row for row in db.imports() if row["filename"] == "traceable.csv"]
    assert logged and logged[0]["producer"] == "agent:mcp"


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

    # Nothing was taken off another category, so there is nothing to warn about.
    assert result["reclassified"] == []
    assert result["warning"] is None


def test_teaching_reports_spending_it_moves_out_of_another_category(private_config):
    """
    The quiet failure: a rule that takes transactions a rule already had.

    Categories are evaluated in priority order, so teaching a supermarket to
    takeaways (priority 15) beats groceries (priority 20) and the shop stops
    counting as essential spending. Essentials drive the weeks-of-runway
    figure on the overview, so this changes a headline number - and counting
    only previously-uncategorised matches reported it as doing nothing at all.
    """
    load_unknown_spending("COUNTDOWN NEWTOWN", rows=4, amount=-180.00)

    before = [tx for tx in db.all_transactions() if tx["memo"] == "COUNTDOWN NEWTOWN"]
    assert before, "the fixture memo must exist for this test to mean anything"
    assert {tx["grp"] for tx in before} == {"essential"}, "should start as groceries"

    result = mcp_server.teach_category(match="COUNTDOWN NEWTOWN", category="takeaways")

    assert result["ok"] is True
    assert result["matched"] == 0, "none of these were uncategorised"
    assert result["changed_group_count"] == 4
    assert result["warning"] is not None
    assert "runway" in result["warning"]
    assert "essentials total" in result["note"]

    moved = result["reclassified"][0]
    assert moved["from_category"] == "groceries"
    assert moved["from_group"] == "essential"
    assert moved["to_group"] == "discretionary"

    after = [tx for tx in db.all_transactions() if tx["memo"] == "COUNTDOWN NEWTOWN"]
    assert {tx["grp"] for tx in after} == {"discretionary"}


def test_a_taught_rule_records_who_taught_it(private_config):
    """
    A rule an agent guessed at and one the household typed used to be
    indistinguishable in learned.yml. That matters a year later, when a
    category total looks wrong and somebody has to work out which rule to doubt.
    """
    load_unknown_spending(UNKNOWN, rows=3)
    mcp_server.teach_category(match="QUAYSIDE", category="takeaways")

    learned = yaml.safe_load((private_config / "learned.yml").read_text(encoding="utf-8"))
    recorded = learned["taught"]["takeaways"]["QUAYSIDE"]

    assert recorded["producer"] == "agent:mcp"
    assert recorded["recorded_at"]

    # And the extra key must not disturb how rules are read.
    assert "QUAYSIDE" in config.rules()["categories"]["takeaways"]["match"]


# ---------------------------------------------------------------------------
# Receipts: what was bought, on request
# ---------------------------------------------------------------------------
def test_a_transaction_with_a_receipt_says_so():
    """
    The failure this exists for: an agent asked what a shop consisted of, with
    every line of the receipt sitting in the database, had no way to learn
    that and reported a hole that was not there. The row now carries the id.
    """
    receipt_id = load_a_receipt()
    rows = mcp_server.list_transactions()["transactions"]

    with_receipt = [r for r in rows if "receipt_id" in r]
    assert len(with_receipt) == 1
    assert with_receipt[0]["receipt_id"] == receipt_id
    assert with_receipt[0]["amount"] == -52.0


def test_gaps_say_how_many_purchases_can_be_itemised():
    load_some_data()
    none = mcp_server.describe_data_gaps()
    assert none["receipts"] == {"held_count": 0, "linked_count": 0}
    assert any("No itemised receipts" in g for g in none["gaps"])

    load_a_receipt()
    some = mcp_server.describe_data_gaps()
    assert some["receipts"] == {"held_count": 1, "linked_count": 1}
    assert any("get_receipt" in g for g in some["gaps"]), "the agent is told how to read them"


def test_the_receipt_listing_names_no_product():
    """Listing is the summary; the lines are the raw call. Same split as the API."""
    load_a_receipt()
    listing = mcp_server.list_receipts()

    assert listing["count"] == 1
    entry = listing["receipts"][0]
    assert entry["line_count"] == 3
    assert entry["linked_to_a_transaction"] is True
    assert "Blue milk" not in json.dumps(listing)


def test_a_receipt_is_served_line_by_line_with_the_shops_own_words():
    receipt_id = load_a_receipt()
    receipt = mcp_server.get_receipt(receipt_id)

    assert receipt["line_count"] == 3
    assert round(sum(line["line_total"] for line in receipt["lines"]), 2) == receipt["stated_total"]
    bananas = next(line for line in receipt["lines"] if "Bananas" in line["description"])
    assert bananas["source_category"] == "Fruit & Vegetables", "the shop's word, verbatim"
    assert bananas["unit"] == "kg"
    assert receipt["provenance"]["producer"] == "tool:example-receipts"
    assert receipt["units"]["except"]["quantity"], "a quantity is not an amount of money"


def test_a_missing_receipt_is_an_answer_not_a_crash():
    load_some_data()
    result = mcp_server.get_receipt(999)
    assert "error" in result
    assert "list_receipts" in result["error"], "and the agent is told where to look"
