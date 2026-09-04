"""
Tests for the parts where a silent bug would be most expensive.

The priority here is not coverage percentage. It is the handful of places
where being quietly wrong produces a confident, plausible, incorrect number:
sign handling on money, column detection, de-duplication on re-import, and
which months count as complete. A crash is recoverable; a dashboard that
cheerfully reports a surplus during a deficit is not.

Two of these are regression tests for bugs found during the first build:
  - the sniffer picking the account-number column as the memo
  - the current, part-finished month being treated as complete
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kete import cache, categorise, db  # noqa: E402
from kete.analysis import cashflow  # noqa: E402
from kete.ingest.bank_csv import _parse_money, parse_csv  # noqa: E402


# ---------------------------------------------------------------------------
# Money parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("45.60", 45.60),
        ("-45.60", -45.60),
        ("$1,234.56", 1234.56),
        ("(123.45)", -123.45),      # accounting negative
        ("1,000", 1000.0),
        ("", None),
        ("-", None),
        ("POS W/D", None),
        ("38-9014-0123456-00", None),  # an account number is not money
    ],
)
def test_parse_money(raw, expected):
    assert _parse_money(raw) == expected


# ---------------------------------------------------------------------------
# CSV sniffing
# ---------------------------------------------------------------------------
KIWIBANK_HEADERLESS = "\n".join(
    [
        "38-9014-0123456-00,01-07-2025,SALARY ACME LTD,,,,,,,,,,2380.00,,2380.00,4530.00",
        "38-9014-0123456-00,02-07-2025,POS W/D PAK N SAVE,,,,,,,,,,,285.40,-285.40,4244.60",
        "38-9014-0123456-00,03-07-2025,D/D MERCURY NZ LTD,,,,,,,,,,,268.00,-268.00,3976.60",
    ]
)


def test_headerless_kiwibank_maps_memo_not_account():
    """
    Regression: the account number column is long and on every row, so a
    "longest text wins" heuristic picks it as the memo and categorisation
    then matches nothing at all.
    """
    rows, report = parse_csv(KIWIBANK_HEADERLESS, "kb.csv")

    assert report.rows_parsed == 3
    assert report.column_map["memo"] == 2
    assert report.column_map["account"] == 0
    assert rows[0]["memo"] == "SALARY ACME LTD"
    assert rows[0]["account"] == "38-9014-0123456-00"


def test_headerless_signs_are_correct():
    """Income positive, spending negative. A flip here inverts the whole app."""
    rows, _ = parse_csv(KIWIBANK_HEADERLESS, "kb.csv")
    assert rows[0]["amount"] == 2380.00
    assert rows[1]["amount"] == -285.40
    assert rows[2]["amount"] == -268.00


def test_header_with_debit_credit_columns():
    text = "\n".join(
        [
            "Account number,Date,Memo,Amount (credit),Amount (debit),Balance",
            "38-9014-0123456-00,04-07-2025,NEW WORLD ASHHURST,,92.15,3884.45",
            "38-9014-0123456-00,05-07-2025,INLAND REVENUE WFFTC,140.00,,4024.45",
        ]
    )
    rows, report = parse_csv(text, "kb2.csv")

    assert report.had_header is True
    assert report.signed_amounts is False
    assert rows[0]["amount"] == -92.15
    assert rows[1]["amount"] == 140.00


def test_all_positive_amounts_is_flagged_low_confidence():
    """If nothing looks like spending, the debit column was missed."""
    text = "\n".join(
        [
            "Date,Description,Amount",
            "01-07-2025,COUNTDOWN,45.00",
            "02-07-2025,MERCURY,80.00",
        ]
    )
    _, report = parse_csv(text, "bad.csv")
    assert report.confidence == "low"
    assert any("positive" in w for w in report.warnings)


def test_nonsense_file_is_rejected_not_guessed():
    _, report = parse_csv("hello,world\nfoo,bar", "junk.csv")
    assert report.rows_parsed == 0
    assert report.confidence == "low"


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------
def test_reimport_is_idempotent(tmp_path, monkeypatch):
    """
    Bank exports overlap. Importing March and then Jan-March must not double
    every transaction in the overlap - if it does, spending silently doubles.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init()

    rows, _ = parse_csv(KIWIBANK_HEADERLESS, "kb.csv")
    first = db.insert_transactions(rows, "kb.csv")
    second = db.insert_transactions(rows, "kb.csv")

    assert first == 3
    assert second == 0


def test_identical_same_day_purchases_are_kept_separate(tmp_path, monkeypatch):
    """Two $4.50 coffees on one day are two transactions, not one."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init()

    text = "\n".join(
        [
            "38-9014-0123456-00,01-07-2025,CAFE ASHHURST,,,,,,,,,,,4.50,-4.50,100.00",
            "38-9014-0123456-00,01-07-2025,CAFE ASHHURST,,,,,,,,,,,4.50,-4.50,95.50",
        ]
    )
    rows, _ = parse_csv(text, "c.csv")
    assert db.insert_transactions(rows, "c.csv") == 2


# ---------------------------------------------------------------------------
# Categorisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "memo",
    ["POS W/D PAK'nSAVE PALM STH", "PAKNSAVE PALMERSTON", "Pak N Save Terrace End"],
)
def test_punctuation_variants_all_match_groceries(memo):
    """The rule is written once; the bank spells it three ways."""
    key, group, _ = categorise.categorise_one(memo, -80.0)
    assert key == "groceries"
    assert group == "essential"


def test_refund_flips_direction_to_income():
    """A refund from a shop is money in, not negative spending."""
    _, group, _ = categorise.categorise_one("COUNTDOWN REFUND", 45.00)
    assert group == "income"


def test_unmatched_memo_is_not_silently_bucketed():
    key, group, by = categorise.categorise_one("ZZQ INTERNAL 99182", -20.0)
    assert key == "uncategorised"
    assert group == "unknown"
    assert by == "unmatched"


def test_transfers_are_recognised_so_they_can_be_excluded():
    key, group, _ = categorise.categorise_one("TRANSFER TO 38-9014-0000000-01", -200.0)
    assert key == "transfer"
    assert group == "transfer"


# ---------------------------------------------------------------------------
# Complete months
# ---------------------------------------------------------------------------
def _frame_from(dates: list[date]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([d.isoformat() for d in dates]),
            "amount": [-10.0] * len(dates),
            "grp": ["essential"] * len(dates),
            "category": ["groceries"] * len(dates),
            "memo": ["X"] * len(dates),
            "account": ["A"] * len(dates),
            "balance": [None] * len(dates),
        }
    )
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["is_spend"] = True
    df["is_income"] = False
    return df


def test_current_month_is_never_complete():
    """
    Regression: a month three days old looked "complete" because transactions
    reached both ends of the data. Judging the household on it reported income
    near zero - the salary simply had not landed yet.
    """
    today = date.today()
    first_of_month = today.replace(day=1)
    if (today - first_of_month).days < 2:
        pytest.skip("Too early in the month for this scenario to exist.")

    dates = [first_of_month, today]
    assert cashflow.complete_months(_frame_from(dates)) == []


def test_a_fully_past_month_is_complete():
    today = date.today()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    df = _frame_from([last_month_start, last_month_end])
    assert cashflow.complete_months(df) == [last_month_start.strftime("%Y-%m")]


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------
def test_ledger_cache_invalidates_when_data_changes(tmp_path, monkeypatch):
    """
    A stale cache that looks fresh is the worst failure mode this tool has, so
    the version key must actually move when the ledger is written to.
    """
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    ledger = tmp_path / "ledger.db"
    ledger.write_bytes(b"one")

    before = cache.ledger_version()
    ledger.write_bytes(b"two but longer")
    after = cache.ledger_version()

    assert before != after


def test_wal_file_is_part_of_the_cache_key(tmp_path, monkeypatch):
    """SQLite writes land in the -wal file; ignoring it serves stale numbers."""
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    (tmp_path / "ledger.db").write_bytes(b"db")

    before = cache.ledger_version()
    (tmp_path / "ledger.db-wal").write_bytes(b"pending write")
    assert cache.ledger_version() != before
