"""
SQLite storage.

One file, no server, trivially backed up by copying it. The schema is small
enough to read in one sitting, which matters for a tool a household needs to
still trust in five years.

The important idea here is the *fingerprint*: a stable hash of a transaction's
identifying fields. Bank exports overlap - you will download "last 3 months"
in March and again in April - so the same transaction arrives repeatedly.
Fingerprinting makes re-import idempotent, which in turn makes the monthly
routine safe to do carelessly. A tool you have to be careful with is a tool
you stop using.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR

DB_PATH = DATA_DIR / "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    fingerprint   TEXT PRIMARY KEY,
    account       TEXT NOT NULL,
    date          TEXT NOT NULL,          -- ISO yyyy-mm-dd
    memo          TEXT NOT NULL,
    amount        REAL NOT NULL,          -- negative = money out
    balance       REAL,
    category      TEXT,
    grp           TEXT,                   -- essential/discretionary/...
    categorised_by TEXT,                  -- rule | manual | unmatched
    source_file   TEXT,
    imported_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tx_date     ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_tx_category ON transactions(category);
CREATE INDEX IF NOT EXISTS ix_tx_group    ON transactions(grp);

-- Manual corrections live separately so re-running categorisation never
-- silently discards a human's decision.
CREATE TABLE IF NOT EXISTS overrides (
    fingerprint TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    decided_at  TEXT NOT NULL,
    note        TEXT
);

-- Provenance: which file did each number come from, and have we seen it?
CREATE TABLE IF NOT EXISTS import_log (
    file_sha256 TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    rows_seen   INTEGER NOT NULL,
    rows_new    INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    parser      TEXT
);

-- Point-in-time snapshots. This is how progress becomes measurable rather
-- than remembered.
CREATE TABLE IF NOT EXISTS snapshots (
    taken_on TEXT PRIMARY KEY,
    metrics  TEXT NOT NULL,               -- JSON blob
    note     TEXT
);

CREATE TABLE IF NOT EXISTS payslips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pay_date      TEXT NOT NULL,
    employer      TEXT,
    gross         REAL,
    paye          REAL,
    kiwisaver_ee  REAL,
    kiwisaver_er  REAL,
    student_loan  REAL,
    acc_levy      REAL,
    net           REAL,
    source_file   TEXT,
    raw           TEXT,
    UNIQUE(pay_date, employer, gross, net)
);

-- Balances the household types in (KiwiSaver, mortgage, savings) that no CSV
-- can tell us. Historised so net worth has a real trend line.
CREATE TABLE IF NOT EXISTS manual_balances (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    as_at      TEXT NOT NULL,
    label      TEXT NOT NULL,
    kind       TEXT NOT NULL,             -- asset | liability
    amount     REAL NOT NULL,
    note       TEXT,
    UNIQUE(as_at, label)
);
"""


def fingerprint(
    account: str,
    when: str,
    memo: str,
    amount: float,
    balance: float | None,
    occurrence: int = 0,
) -> str:
    """
    Stable identity for a transaction.

    Balance is included because it is what distinguishes two genuinely
    separate but otherwise identical transactions (two $4.50 coffees on the
    same day). Where the export carries no balance we fall back to an
    occurrence counter computed within the file.
    """
    bal = f"{balance:.2f}" if balance is not None else f"occ{occurrence}"
    payload = f"{account}|{when}|{memo.strip().upper()}|{amount:.2f}|{bal}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def insert_transactions(rows: Iterable[dict[str, Any]], source_file: str) -> int:
    """Insert, skipping anything already present. Returns the count of new rows."""
    now = datetime.now().isoformat(timespec="seconds")
    payload = [
        (
            r["fingerprint"],
            r["account"],
            r["date"],
            r["memo"],
            r["amount"],
            r.get("balance"),
            r.get("category"),
            r.get("grp"),
            r.get("categorised_by"),
            source_file,
            now,
        )
        for r in rows
    ]
    if not payload:
        return 0
    with connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.executemany(
            """INSERT OR IGNORE INTO transactions
               (fingerprint, account, date, memo, amount, balance,
                category, grp, categorised_by, source_file, imported_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            payload,
        )
        after = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return after - before


def log_import(sha: str, filename: str, rows_seen: int, rows_new: int, parser: str) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO import_log
               (file_sha256, filename, rows_seen, rows_new, imported_at, parser)
               VALUES (?,?,?,?,?,?)""",
            (
                sha,
                filename,
                rows_seen,
                rows_new,
                datetime.now().isoformat(timespec="seconds"),
                parser,
            ),
        )


def already_imported(sha: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM import_log WHERE file_sha256 = ?", (sha,)).fetchone()
    return dict(row) if row else None


def all_transactions() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM transactions ORDER BY date, fingerprint").fetchall()
    return [dict(r) for r in rows]


def set_override(fp: str, category: str, note: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO overrides (fingerprint, category, decided_at, note)
               VALUES (?,?,?,?)""",
            (fp, category, datetime.now().isoformat(timespec="seconds"), note),
        )


def overrides() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT fingerprint, category FROM overrides").fetchall()
    return {r["fingerprint"]: r["category"] for r in rows}


def save_snapshot(metrics: dict[str, Any], note: str | None = None) -> str:
    taken = date.today().isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO snapshots (taken_on, metrics, note) VALUES (?,?,?)",
            (taken, json.dumps(metrics, default=str), note),
        )
    return taken


def snapshots() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM snapshots ORDER BY taken_on").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metrics"] = json.loads(d["metrics"])
        out.append(d)
    return out


def set_manual_balance(
    as_at: str, label: str, kind: str, amount: float, note: str | None = None
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO manual_balances (as_at, label, kind, amount, note)
               VALUES (?,?,?,?,?)""",
            (as_at, label, kind, amount, note),
        )


def manual_balances() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM manual_balances ORDER BY as_at, label").fetchall()
    return [dict(r) for r in rows]


def stats() -> dict[str, Any]:
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        rng = conn.execute("SELECT MIN(date) AS lo, MAX(date) AS hi FROM transactions").fetchone()
        accounts = [
            r[0] for r in conn.execute("SELECT DISTINCT account FROM transactions").fetchall()
        ]
        files = conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0]
    return {
        "transactions": n,
        "first_date": rng["lo"],
        "last_date": rng["hi"],
        "accounts": accounts,
        "files_imported": files,
    }
