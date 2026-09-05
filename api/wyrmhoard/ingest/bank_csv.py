"""
Bank CSV ingestion.

Kiwibank has shipped several different CSV layouts over the years, and which
one you get depends on where in internet banking you clicked. Rather than
hard-code one and break the first time it changes, this module *sniffs* the
file: it finds the date column by trying to parse dates, finds the money
columns by looking at what is numeric, and works out whether amounts are
signed or split into debit/credit.

Every parse returns a ParseReport saying what it decided and how confident it
is. When confidence is low the caller is expected to show that to the human
rather than quietly importing nonsense - a mis-parsed sign flip would turn
spending into income and make the entire dashboard a lie.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .. import db

# --------------------------------------------------------------------------
# Column naming. Kiwibank's own header words, plus the obvious synonyms.
# --------------------------------------------------------------------------
HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "processed date", "value date"),
    "memo": (
        "memo",
        "description",
        "details",
        "particulars",
        "narrative",
        "transaction details",
        "op name",
        "other party",
    ),
    "amount": ("amount", "transaction amount", "value"),
    "credit": ("amount (credit)", "credit", "deposit", "money in", "cr"),
    "debit": ("amount (debit)", "debit", "withdrawal", "money out", "dr"),
    "balance": ("balance", "running balance", "closing balance"),
    "account": ("account number", "account", "acct"),
    # The other side of the transaction. When this is one of the household's
    # own accounts it proves the row is an internal transfer - a far stronger
    # signal than looking for the word "transfer" in free text.
    "counterparty": ("other party account number", "other party account"),
    # Extra descriptive columns. Banks scatter the useful words across
    # several of these: a payment can say nothing in Description and name the
    # payee in Particulars. They are joined into `match_text` for rule
    # matching while `memo` stays the single human-readable field.
    "particulars": ("particulars",),
    "reference": ("reference",),
    "other_party": ("other party name",),
}

# Columns whose text joins `memo` to form the string rules match against.
_MATCH_TEXT_FIELDS = ("memo", "particulars", "reference", "other_party")

DATE_FORMATS = (
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%y",
    "%d/%m/%y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y/%m/%d",
)

_MONEY_RE = re.compile(r"^\(?-?\$?\s?-?[\d,]+(\.\d{1,2})?\)?$")

# NZ bank account numbers: bank-branch-account-suffix, e.g. 38-9014-0123456-00.
# Worth detecting explicitly - it is the single biggest source of confusion for
# a naive sniffer, because it is long, textual, and present on every row.
_NZ_ACCOUNT_RE = re.compile(r"^\d{2}-\d{3,4}-\d{6,8}-\d{2,4}$")


@dataclass
class ParseReport:
    """What the sniffer decided, so a human can sanity-check it."""

    filename: str
    parser: str = "sniffer"
    rows_seen: int = 0
    rows_parsed: int = 0
    had_header: bool = False
    column_map: dict[str, int] = field(default_factory=dict)
    date_format: str | None = None
    signed_amounts: bool = True
    confidence: str = "unknown"  # high | medium | low
    warnings: list[str] = field(default_factory=list)
    date_range: tuple[str | None, str | None] = (None, None)
    net_total: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "parser": self.parser,
            "rows_seen": self.rows_seen,
            "rows_parsed": self.rows_parsed,
            "had_header": self.had_header,
            "column_map": self.column_map,
            "date_format": self.date_format,
            "signed_amounts": self.signed_amounts,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "date_range": list(self.date_range),
            "net_total": round(self.net_total, 2),
        }


# --------------------------------------------------------------------------
# Field-level parsing
# --------------------------------------------------------------------------
def _parse_date(value: str) -> tuple[date, str] | None:
    v = (value or "").strip().strip('"')
    if not v:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date(), fmt
        except ValueError:
            continue
    return None


def _parse_money(value: str) -> float | None:
    """
    Handles $, thousands separators, and accounting negatives -  (123.45) -
    which some exports use instead of a minus sign.
    """
    v = (value or "").strip().strip('"').replace(" ", "")
    if not v or v in {"-", "--"}:
        return None
    if not _MONEY_RE.match(v):
        return None
    negative = v.startswith("(") and v.endswith(")")
    v = v.strip("()").replace("$", "").replace(",", "")
    try:
        amount = float(v)
    except ValueError:
        return None
    return -amount if negative else amount


def _looks_like_header(row: list[str]) -> bool:
    """A header row has words in it and no parseable dates or money."""
    if not row:
        return False
    joined = " ".join(row).lower()
    known = sum(1 for hints in HEADER_HINTS.values() for h in hints if h in joined)
    has_date = any(_parse_date(c) for c in row)
    has_money = sum(1 for c in row if _parse_money(c) is not None)
    return known >= 2 and not has_date and has_money == 0


# Specific fields are resolved before generic ones. Kiwibank's split-column
# export heads its columns "Amount (credit)" and "Amount (debit)", and the
# generic hint "amount" is a substring of both - so resolving `amount` first
# claims the credit column, reads every debit as blank, and silently drops
# half the file while reporting the rest as income.
# `counterparty` resolves before `account`: "Other Party Account Number"
# contains "account number", so the generic field would otherwise claim it.
_FIELD_ORDER = (
    "date",
    "counterparty",
    "credit",
    "debit",
    "balance",
    "account",
    "amount",
    "memo",
    "particulars",
    "reference",
    "other_party",
)

# A column whose name mentions credit or debit is never the plain signed
# amount, however much of the word "amount" it contains.
_AMOUNT_EXCLUSIONS = ("credit", "debit", "cr", "dr", "deposit", "withdrawal")


def _map_header(row: list[str]) -> dict[str, int]:
    """Match header cells to canonical fields, most specific first."""
    mapping: dict[str, int] = {}
    cells = [(i, (c or "").strip().lower().strip('"')) for i, c in enumerate(row)]

    for field_name in _FIELD_ORDER:
        hints = HEADER_HINTS.get(field_name, ())
        for hint in sorted(hints, key=len, reverse=True):
            for i, cell in cells:
                if i in mapping.values():
                    continue
                if field_name == "amount" and any(x in cell for x in _AMOUNT_EXCLUSIONS):
                    continue
                if cell == hint or (len(hint) > 4 and hint in cell):
                    mapping[field_name] = i
                    break
            if field_name in mapping:
                break

    # Having both halves of a split column makes the signed column redundant,
    # and trusting it over them is how sign errors creep in.
    if "credit" in mapping and "debit" in mapping:
        mapping.pop("amount", None)
    return mapping


def _sniff_columns(rows: list[list[str]]) -> tuple[dict[str, int], str | None]:
    """
    No usable header - work it out from the data.

    Strategy: the date column is whichever parses as a date most often. Money
    columns are whichever parse as numbers most often; the last of those is the
    running balance and the one before it is the amount.

    The memo is the text column with the most DISTINCT values - not the longest
    one. That distinction is load-bearing: an account number column is long and
    present on every row, so "longest text" reliably picks it instead of the
    description. Cardinality separates them cleanly, because an account number
    has one distinct value across the file and a memo has hundreds.
    """
    if not rows:
        return {}, None

    width = max(len(r) for r in rows)
    date_hits: dict[int, int] = {}
    date_fmt: dict[int, str] = {}
    money_hits: dict[int, int] = {}
    text_len: dict[int, int] = {}
    distinct: dict[int, set[str]] = {}
    account_hits: dict[int, int] = {}

    for row in rows:
        for i in range(width):
            cell = row[i] if i < len(row) else ""
            stripped = cell.strip()
            if stripped and _NZ_ACCOUNT_RE.match(stripped):
                account_hits[i] = account_hits.get(i, 0) + 1
                continue
            parsed = _parse_date(cell)
            if parsed:
                date_hits[i] = date_hits.get(i, 0) + 1
                date_fmt.setdefault(i, parsed[1])
            elif _parse_money(cell) is not None:
                money_hits[i] = money_hits.get(i, 0) + 1
            elif stripped:
                text_len[i] = text_len.get(i, 0) + len(stripped)
                distinct.setdefault(i, set()).add(stripped.upper())

    mapping: dict[str, int] = {}
    fmt = None
    if date_hits:
        di = max(date_hits, key=lambda k: date_hits[k])
        mapping["date"] = di
        fmt = date_fmt.get(di)

    if account_hits:
        mapping["account"] = max(account_hits, key=lambda k: account_hits[k])

    money_cols = sorted((c for c, n in money_hits.items() if n >= max(1, len(rows) // 2)))
    if len(money_cols) >= 2:
        # Kiwibank puts the running balance last. Balance values are large and
        # rarely negative; amounts straddle zero. Use that to confirm.
        candidate_balance = money_cols[-1]
        candidate_amount = money_cols[-2]
        mapping["balance"] = candidate_balance
        mapping["amount"] = candidate_amount
    elif len(money_cols) == 1:
        mapping["amount"] = money_cols[0]

    # Rank text columns by how many distinct values they hold. A column with a
    # single repeated value is structural (account number, bank name, a code),
    # never a description. Total length breaks ties between genuine candidates.
    candidates = {
        i: (len(vals), text_len.get(i, 0))
        for i, vals in distinct.items()
        if i not in mapping.values() and len(vals) > 1
    }
    if candidates:
        mapping["memo"] = max(candidates, key=lambda k: candidates[k])
    elif text_len:
        mapping["memo"] = max(text_len, key=lambda k: text_len[k])

    return mapping, fmt


def _detect_dialect(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


# --------------------------------------------------------------------------
# File-level parsing
# --------------------------------------------------------------------------
def parse_csv(
    text: str, filename: str = "upload.csv", default_account: str = "unknown"
) -> tuple[list[dict[str, Any]], ParseReport]:
    report = ParseReport(filename=filename)

    text = text.lstrip("﻿")
    delimiter = _detect_dialect(text)
    raw_rows = [
        r for r in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in r)
    ]
    report.rows_seen = len(raw_rows)
    if not raw_rows:
        report.warnings.append("File is empty.")
        report.confidence = "low"
        return [], report

    # Kiwibank sometimes prefixes a couple of preamble lines before the header.
    start = 0
    for i, row in enumerate(raw_rows[:5]):
        if _looks_like_header(row):
            start = i
            report.had_header = True
            break

    if report.had_header:
        mapping = _map_header(raw_rows[start])
        body = raw_rows[start + 1 :]
        fmt = None
    else:
        body = raw_rows
        mapping, fmt = _sniff_columns(body)
        report.warnings.append(
            "No header row found - columns were inferred from the data. "
            "Check the sample rows below before trusting the import."
        )

    # Header gave us names but maybe not a date format; and a header can still
    # be wrong about which column really holds the money.
    if report.had_header and "date" not in mapping:
        sniffed, fmt = _sniff_columns(body)
        mapping = {**sniffed, **mapping}

    report.column_map = mapping
    # Count data rows, not lines. Reporting "114 of 115 rows parsed" on a
    # perfectly clean import - because one of them was the header - reads as a
    # silent failure and undermines the confidence signal.
    report.rows_seen = len(body)

    if "date" not in mapping or (
        "amount" not in mapping and "debit" not in mapping and "credit" not in mapping
    ):
        report.warnings.append(
            "Could not identify a date column and an amount column. "
            "This does not look like a bank transaction export."
        )
        report.confidence = "low"
        return [], report

    signed = "amount" in mapping
    report.signed_amounts = signed

    out: list[dict[str, Any]] = []
    occurrence: dict[tuple, int] = {}
    dates: list[date] = []

    def cell(row: list[str], key: str) -> str:
        idx = mapping.get(key)
        if idx is None or idx >= len(row):
            return ""
        return row[idx]

    for row in body:
        parsed_date = _parse_date(cell(row, "date"))
        if not parsed_date:
            continue
        when, this_fmt = parsed_date
        fmt = fmt or this_fmt
        dates.append(when)

        if signed:
            amount = _parse_money(cell(row, "amount"))
        else:
            credit = _parse_money(cell(row, "credit")) or 0.0
            debit = _parse_money(cell(row, "debit")) or 0.0
            amount = credit - abs(debit)
        if amount is None:
            continue

        memo = (cell(row, "memo") or "").strip()
        if not memo:
            # Fall back to joining every non-numeric, non-date cell.
            bits = [
                c.strip()
                for i, c in enumerate(row)
                if c.strip()
                and i not in mapping.values()
                and _parse_money(c) is None
                and not _parse_date(c)
            ]
            memo = " ".join(bits)[:200] or "(no description)"

        account = (cell(row, "account") or default_account).strip() or default_account
        balance = _parse_money(cell(row, "balance"))
        counterparty = (cell(row, "counterparty") or "").strip() or None

        # Everything the bank told us about this row, deduplicated so a memo
        # that already repeats the payee does not weight the match twice.
        seen: set[str] = set()
        parts: list[str] = []
        for field_name in _MATCH_TEXT_FIELDS:
            value = (cell(row, field_name) or "").strip()
            if value and value.upper() not in seen:
                seen.add(value.upper())
                parts.append(value)
        match_text = " ".join(parts)[:400] or memo

        key = (account, when.isoformat(), memo.upper(), round(amount, 2))
        occurrence[key] = occurrence.get(key, 0) + 1

        out.append(
            {
                "fingerprint": db.fingerprint(
                    account, when.isoformat(), memo, amount, balance, occurrence[key]
                ),
                "account": account,
                "date": when.isoformat(),
                "memo": memo,
                "match_text": match_text,
                "counterparty": counterparty,
                "amount": round(amount, 2),
                "balance": balance,
            }
        )

    report.rows_parsed = len(out)
    report.date_format = fmt
    report.net_total = sum(r["amount"] for r in out)
    if dates:
        report.date_range = (min(dates).isoformat(), max(dates).isoformat())

    # Confidence. These heuristics exist to stop a silent sign-flip or a
    # half-read file from becoming a confident-looking dashboard.
    ratio = report.rows_parsed / max(1, len(body))
    if ratio < 0.5:
        report.confidence = "low"
        report.warnings.append(
            f"Only {report.rows_parsed} of {len(body)} data rows parsed. "
            "Something about this layout is not understood."
        )
    elif report.had_header and ratio > 0.95:
        report.confidence = "high"
    else:
        report.confidence = "medium"

    if out and all(r["amount"] >= 0 for r in out):
        report.confidence = "low"
        report.warnings.append(
            "Every amount is positive, so nothing looks like spending. The "
            "debit column was probably not detected - check the mapping."
        )

    return out, report


def ingest_file(path: Path, default_account: str | None = None) -> ParseReport:
    """Parse and store one CSV. Re-importing the same file is a no-op."""
    sha = db.file_sha256(path)
    prior = db.already_imported(sha)

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    account = default_account or path.stem
    rows, report = parse_csv(text, filename=path.name, default_account=account)

    if prior:
        report.warnings.append(
            f"This exact file was already imported on {prior['imported_at']}. "
            "Nothing new was added."
        )

    new = db.insert_transactions(rows, source_file=path.name) if rows else 0
    db.log_import(sha, path.name, report.rows_seen, new, report.parser)
    report.rows_parsed = report.rows_parsed
    return report


def ingest_inbox(inbox: Path) -> list[ParseReport]:
    """Import every CSV sitting in data/inbox/. The monthly routine."""
    reports = []
    for path in sorted(inbox.glob("*.csv")):
        reports.append(ingest_file(path))
    return reports
