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
    memo          TEXT NOT NULL,          -- what a human should read
    match_text    TEXT,                   -- every text field, for rule matching
    counterparty  TEXT,                   -- the other party's account number
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

-- What each account actually is. Roles are inferred from the data, but a
-- human's confirmation is stored here and always wins - the same pattern as
-- `overrides` above. Getting this wrong is expensive: a mortgage counted as
-- cash turns a household's savings into a six-figure negative number.
CREATE TABLE IF NOT EXISTS account_roles (
    account    TEXT PRIMARY KEY,
    role       TEXT NOT NULL,             -- everyday | savings | liability | ignore
    label      TEXT,
    confirmed  INTEGER NOT NULL DEFAULT 0,
    decided_at TEXT NOT NULL
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


# Columns added after the first release. Existing ledgers are migrated in
# place rather than rebuilt, because a household's ledger is their whole
# financial history and "delete it and re-import" is not an upgrade path.
_ADDED_COLUMNS = {
    "transactions": {
        "match_text": "TEXT",
        "counterparty": "TEXT",
    },
    "payslips": {
        "period": "TEXT",
        "employee_ref": "TEXT",
        "tax_code": "TEXT",
        "deductions_total": "REAL",
        "ytd_gross": "REAL",
        "ytd_tax": "REAL",
        "annual_rem": "REAL",
        "base_salary": "REAL",
        "er_super_annual": "REAL",
        "confidence": "TEXT",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def insert_transactions(rows: Iterable[dict[str, Any]], source_file: str) -> int:
    """Insert, skipping anything already present. Returns the count of new rows."""
    now = datetime.now().isoformat(timespec="seconds")
    payload = [
        (
            r["fingerprint"],
            r["account"],
            r["date"],
            r["memo"],
            r.get("match_text") or r["memo"],
            r.get("counterparty"),
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
               (fingerprint, account, date, memo, match_text, counterparty,
                amount, balance, category, grp, categorised_by,
                source_file, imported_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            payload,
        )
        after = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return after - before


# --------------------------------------------------------------------------
# Data management
#
# A household must be able to undo a mistake without starting again. Every
# transaction records the file it came from, so an import that turns out to be
# the wrong export, a duplicate, or somebody else's account can be lifted back
# out cleanly.
# --------------------------------------------------------------------------
def imports() -> list[dict[str, Any]]:
    """Every file imported, with how many of its rows are still in the ledger."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT il.filename,
                      il.file_sha256,
                      il.rows_seen,
                      il.rows_new,
                      il.imported_at,
                      il.parser,
                      COUNT(t.fingerprint) AS present,
                      MIN(t.date)          AS first_date,
                      MAX(t.date)          AS last_date
               FROM import_log il
               LEFT JOIN transactions t ON t.source_file = il.filename
               GROUP BY il.file_sha256
               ORDER BY il.imported_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def delete_import(filename: str) -> dict[str, Any]:
    """
    Remove every transaction that came from one file, and forget the file.

    Overrides for those transactions go too: keeping a correction attached to
    a fingerprint that no longer exists would silently reapply itself if the
    same file were imported again later, which is a surprising way for an old
    decision to come back.
    """
    with connect() as conn:
        fingerprints = [
            r["fingerprint"]
            for r in conn.execute(
                "SELECT fingerprint FROM transactions WHERE source_file = ?", (filename,)
            )
        ]
        conn.executemany(
            "DELETE FROM overrides WHERE fingerprint = ?", [(f,) for f in fingerprints]
        )
        conn.execute("DELETE FROM transactions WHERE source_file = ?", (filename,))
        conn.execute("DELETE FROM import_log WHERE filename = ?", (filename,))
    return {"filename": filename, "removed": len(fingerprints)}


def backup(dest_dir: Path) -> Path:
    """
    Copy the ledger to a timestamped file.

    Uses SQLite's own backup API rather than copying bytes: the database runs
    in WAL mode, so a plain file copy taken mid-write can land a torn or
    incomplete database, and the household would not find out until the day
    they needed it.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    dest = dest_dir / f"ledger-{stamp}.db"

    with connect() as source, sqlite3.connect(dest) as target:
        source.backup(target)
    return dest


def restore(backup_path: Path) -> dict[str, Any]:
    """
    Replace the ledger with a backup, keeping a copy of what was there.

    The displaced ledger is never simply deleted - restoring the wrong file is
    exactly the kind of mistake somebody makes once, at speed, and it should
    not be the end of their history.
    """
    if not backup_path.exists():
        raise FileNotFoundError(backup_path)

    with sqlite3.connect(backup_path) as probe:
        count = probe.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    superseded = None
    if DB_PATH.exists():
        superseded = backup(DB_PATH.parent / "backups")

    with sqlite3.connect(backup_path) as source, connect() as target:
        source.backup(target)

    return {
        "restored_from": str(backup_path),
        "transactions": int(count),
        "previous_ledger_saved_to": str(superseded) if superseded else None,
    }


def list_backups(dest_dir: Path) -> list[dict[str, Any]]:
    if not dest_dir.exists():
        return []
    out = []
    for path in sorted(dest_dir.glob("ledger-*.db"), reverse=True):
        stat = path.stat()
        out.append(
            {
                "name": path.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "taken_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )
    return out


# --------------------------------------------------------------------------
# Payslips
#
# `raw` stays NULL on purpose. Payslips carry an IRD number and this tool has
# no use for one, so the safest handling is never to hold it: the text is
# redacted on the way in and the original is not retained.
# --------------------------------------------------------------------------
_PAYSLIP_FIELDS = (
    "pay_date",
    "employer",
    "period",
    "employee_ref",
    "tax_code",
    "gross",
    "deductions_total",
    "paye",
    "kiwisaver_ee",
    "kiwisaver_er",
    "student_loan",
    "acc_levy",
    "net",
    "ytd_gross",
    "ytd_tax",
    "annual_rem",
    "base_salary",
    "er_super_annual",
    "confidence",
    "source_file",
)


def save_payslip(data: dict[str, Any]) -> int:
    """Insert or update one payslip. Re-importing the same slip is a no-op."""
    row = {k: data.get(k) for k in _PAYSLIP_FIELDS}
    columns = ", ".join(_PAYSLIP_FIELDS)
    placeholders = ", ".join("?" for _ in _PAYSLIP_FIELDS)
    with connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM payslips").fetchone()[0]
        conn.execute(
            f"INSERT OR REPLACE INTO payslips ({columns}) VALUES ({placeholders})",
            tuple(row[k] for k in _PAYSLIP_FIELDS),
        )
        after = conn.execute("SELECT COUNT(*) FROM payslips").fetchone()[0]
    return after - before


def payslips() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM payslips ORDER BY pay_date DESC").fetchall()
    return [dict(r) for r in rows]


def delete_payslip(payslip_id: int) -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM payslips WHERE id = ?", (payslip_id,))
    return cur.rowcount


# --------------------------------------------------------------------------
# Account roles
# --------------------------------------------------------------------------
def set_account_role(
    account: str, role: str, label: str | None = None, confirmed: bool = True
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO account_roles
               (account, role, label, confirmed, decided_at) VALUES (?,?,?,?,?)""",
            (
                account,
                role,
                label,
                1 if confirmed else 0,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def account_roles() -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM account_roles").fetchall()
    return {r["account"]: dict(r) for r in rows}


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
