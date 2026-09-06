"""
Turning the household's raw records into ledger rows.

One door in. Before this, format dispatch lived in three places with three
different rules - the HTTP upload checked `name.endswith(".pdf")`, the MCP tool
checked `suffix == ".pdf"`, and the inbox scan globbed `*.csv` and so could not
see a payslip at all. All three treated "not a PDF" as "is a CSV", so an
unrecognised file was fed to the CSV parser and failed confusingly rather than
being refused clearly.

Everything that takes a document now goes through `ingest_document`, which
decides the format once, records who submitted it, and logs every arrival -
including payslips, which previously left no trace in `import_log` whatsoever.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bank_csv import ParseReport, ingest_file, parse_csv  # noqa: F401
from .paths import DOCUMENT_SUFFIXES, check_suffix, resolve_within, safe_upload_name  # noqa: F401


def ingest_document(
    path: Path,
    producer: str,
    default_account: str | None = None,
) -> dict[str, Any]:
    """
    Take in one document, whatever kind it is.

    `producer` says what submitted it - `human:dashboard`, `agent:mcp`,
    `tool:<name>`. It is required rather than defaulted for the same reason it
    is required on a valuation: data with no stated origin is the thing the
    producer contract exists to prevent. See docs/PRODUCERS.md.

    Raises ValueError, with wording a caller can pass on, for a file that is
    missing or of a kind this tool does not read.
    """
    from . import payslip

    path = Path(path)
    if not path.exists():
        raise ValueError(f"No file at {path}.")

    kind = check_suffix(path)

    if kind == "pdf":
        return payslip.ingest_file(path, producer=producer)

    report = ingest_file(path, default_account=default_account, producer=producer)
    return {"kind": "transactions", "report": report.as_dict()}


def ingest_inbox(inbox: Path, producer: str = "human:inbox") -> list[dict[str, Any]]:
    """
    Import every document sitting in data/inbox/. The monthly routine.

    Now takes payslips as well as CSVs. A PDF dropped in here used to be
    invisible - the scan globbed `*.csv` - so somebody following the documented
    "drop your exports in the inbox" habit with a payslip got silence.

    Files are left where they are afterwards. Re-importing is safe: transaction
    fingerprints and payslip constraints both make it a no-op.
    """
    out: list[dict[str, Any]] = []
    for path in sorted(inbox.iterdir() if inbox.exists() else []):
        if path.is_file() and path.suffix.lower() in DOCUMENT_SUFFIXES:
            out.append(ingest_document(path, producer=producer))
    return out
