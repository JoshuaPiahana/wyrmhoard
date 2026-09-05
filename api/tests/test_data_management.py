"""
Undoing an import, backing up, and restoring.

A household must be able to fix a mistake without starting again. The
mistakes are predictable: the wrong export, a duplicate, an account that
turns out to belong to somebody else. None of those should mean re-importing
a year of files.

Backups get equal weight because `ledger.db` is a household's entire
financial history in one file, and the failure mode is silent - a corrupted
backup looks fine until the day it is needed.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import accounts, cache, db
from wyrmhoard.ingest import parse_csv

ACCOUNT = "38-9014-0123456-00"
OTHER = "38-9014-0000000-01"


def csv_for(account: str, day_from: int, rows: int) -> str:
    lines = ["Account number,Date,Memo,Amount,Balance"]
    for i in range(rows):
        lines.append(f"{account},{day_from + i:02d}-01-2025,SHOP {i},-{10 + i}.00,{500 - i}.00")
    return "\n".join(lines)


def import_file(name: str, text: str) -> int:
    rows, _ = parse_csv(text, name)
    n = db.insert_transactions(rows, name)
    db.log_import(f"sha-{name}", name, len(rows), n, "test")
    cache.clear_all()
    return n


# ---------------------------------------------------------------------------
# Undoing an import
# ---------------------------------------------------------------------------
def test_imports_lists_what_is_actually_still_present():
    import_file("jan.csv", csv_for(ACCOUNT, 1, 5))
    listed = db.imports()

    assert len(listed) == 1
    assert listed[0]["filename"] == "jan.csv"
    assert listed[0]["present"] == 5
    assert listed[0]["first_date"] and listed[0]["last_date"]


def test_undoing_one_import_leaves_the_others_alone():
    import_file("jan.csv", csv_for(ACCOUNT, 1, 5))
    import_file("feb.csv", csv_for(OTHER, 10, 3))

    result = db.delete_import("jan.csv")

    assert result["removed"] == 5
    remaining = db.all_transactions()
    assert len(remaining) == 3
    assert all(t["source_file"] == "feb.csv" for t in remaining)
    assert [i["filename"] for i in db.imports()] == ["feb.csv"]


def test_undoing_an_import_also_forgets_its_corrections():
    """
    A manual correction is keyed to a transaction fingerprint. Leaving it
    behind means re-importing the same file silently reapplies a decision the
    household made and then undid.
    """
    import_file("jan.csv", csv_for(ACCOUNT, 1, 3))
    fp = db.all_transactions()[0]["fingerprint"]
    db.set_override(fp, "groceries")
    assert fp in db.overrides()

    db.delete_import("jan.csv")
    assert fp not in db.overrides()


def test_an_undone_file_can_be_imported_again():
    import_file("jan.csv", csv_for(ACCOUNT, 1, 4))
    db.delete_import("jan.csv")

    assert import_file("jan.csv", csv_for(ACCOUNT, 1, 4)) == 4


def test_undoing_a_file_that_was_never_imported_is_harmless():
    import_file("jan.csv", csv_for(ACCOUNT, 1, 2))
    assert db.delete_import("nope.csv")["removed"] == 0
    assert len(db.all_transactions()) == 2


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------
def test_backup_is_a_real_readable_database(tmp_path):
    """
    Written through SQLite's backup API, not a byte copy: the ledger runs in
    WAL mode, where a copy taken mid-write can be torn.
    """
    import_file("jan.csv", csv_for(ACCOUNT, 1, 6))
    path = db.backup(tmp_path / "backups")

    assert path.exists()
    with sqlite3.connect(path) as probe:
        assert probe.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 6


def test_restoring_brings_the_old_ledger_back(tmp_path):
    import_file("jan.csv", csv_for(ACCOUNT, 1, 6))
    snapshot = db.backup(tmp_path / "backups")

    import_file("feb.csv", csv_for(OTHER, 10, 4))
    assert len(db.all_transactions()) == 10

    result = db.restore(snapshot)

    assert result["transactions"] == 6
    assert len(db.all_transactions()) == 6


def test_restoring_saves_the_ledger_it_replaces(tmp_path):
    """Restoring the wrong file must not be the end of a household's history."""
    import_file("jan.csv", csv_for(ACCOUNT, 1, 3))
    snapshot = db.backup(tmp_path / "backups")
    import_file("feb.csv", csv_for(OTHER, 10, 7))

    result = db.restore(snapshot)

    superseded = result["previous_ledger_saved_to"]
    assert superseded, "the replaced ledger was not preserved"
    with sqlite3.connect(superseded) as probe:
        assert probe.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 10


def test_restoring_a_missing_backup_raises_rather_than_wiping(tmp_path):
    import_file("jan.csv", csv_for(ACCOUNT, 1, 3))
    with pytest.raises(FileNotFoundError):
        db.restore(tmp_path / "not-there.db")
    assert len(db.all_transactions()) == 3, "the ledger was damaged by a failed restore"


def test_backups_are_listed_newest_first(tmp_path):
    import_file("jan.csv", csv_for(ACCOUNT, 1, 2))
    db.backup(tmp_path / "b")
    db.backup(tmp_path / "b")

    listed = db.list_backups(tmp_path / "b")
    assert len(listed) >= 1
    assert all("size_kb" in b and "taken_at" in b for b in listed)


def test_listing_backups_in_a_missing_directory_is_empty_not_an_error(tmp_path):
    assert db.list_backups(tmp_path / "never-created") == []


# ---------------------------------------------------------------------------
# Knowing what we cannot see
# ---------------------------------------------------------------------------
def test_repeated_transfers_from_an_unknown_account_are_flagged():
    """
    A partner's account regularly moving money across is a hole in the tool's
    view. Reporting confidently over that hole is how it tells somebody they
    are missing entitlements they already receive.
    """
    rows = []
    for i in range(8):
        rows.append(
            {
                "fingerprint": db.fingerprint(ACCOUNT, f"2025-01-{i + 1:02d}", "IN", 200.0, 1.0),
                "account": ACCOUNT,
                "date": f"2025-01-{i + 1:02d}",
                "memo": "TRANSFER FROM PARTNER",
                "match_text": "TRANSFER FROM PARTNER",
                "counterparty": "99-9999-9999999-99",
                "amount": 200.0,
                "balance": 1.0,
            }
        )
    db.insert_transactions(rows, "in.csv")
    cache.clear_all()

    missing = accounts.likely_missing_accounts()
    assert missing, "a repeatedly-transferring outside account was not noticed"
    assert missing[0]["account"] == "99-9999-9999999-99"
    assert missing[0]["transfers"] == 8
    assert accounts.view_is_incomplete() is True


def test_an_employer_paying_wages_is_not_mistaken_for_a_household_account():
    """Salary arrives from an outside account too, and is not a gap in our view."""
    rows = []
    for i in range(10):
        rows.append(
            {
                "fingerprint": db.fingerprint(ACCOUNT, f"2025-02-{i + 1:02d}", "PAY", 2000.0, 1.0),
                "account": ACCOUNT,
                "date": f"2025-02-{i + 1:02d}",
                "memo": "Salary NZ DEFENCE FORCE Wage/salary",
                "match_text": "Salary NZ DEFENCE FORCE Wage/salary",
                "counterparty": "99-9999-9999999-98",
                "amount": 2000.0,
                "balance": 1.0,
            }
        )
    db.insert_transactions(rows, "pay.csv")
    cache.clear_all()

    assert accounts.likely_missing_accounts() == []


def test_a_one_off_payment_from_a_friend_is_not_flagged():
    rows = [
        {
            "fingerprint": db.fingerprint(ACCOUNT, "2025-03-01", "GIFT", 50.0, 1.0),
            "account": ACCOUNT,
            "date": "2025-03-01",
            "memo": "Bill Payment gift",
            "match_text": "Bill Payment gift",
            "counterparty": "99-9999-9999999-99",
            "amount": 50.0,
            "balance": 1.0,
        }
    ]
    db.insert_transactions(rows, "gift.csv")
    cache.clear_all()

    assert accounts.likely_missing_accounts() == []
