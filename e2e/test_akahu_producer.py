"""
The Akahu producer's contract with the core, asked over HTTP.

A producer may not import Wyrmhoard - test_layering.py enforces that from the
outside - so "does the core understand what this producer emits" cannot be
answered in the producer's own tests. It is answered here instead, against the
real server: the real nginx in front, the real multipart parser, the real
sniffer. If the core ever renames a header hint this fails here rather than at
6am on somebody's cron.

No browser is involved. The file lives in e2e/ because that is the suite that
has a running stack and a throwaway ledger.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

PRODUCERS = Path("/producers")
if not (PRODUCERS / "akahu" / "akahu.py").exists():
    raise RuntimeError(
        f"{PRODUCERS} is not mounted - docker-compose.yml's e2e service must mount ./producers"
    )
sys.path.insert(0, str(PRODUCERS / "akahu"))

from akahu import (  # noqa: E402
    PRODUCER,
    multipart,
    recover_counterparties,
    submit,
    to_csv,
    to_rows,
)

ACCOUNT = {
    "_id": "acc_e2e",
    "name": "Everyday",
    "formatted_account": "38-9014-0123456-00",
    "type": "CHECKING",
    "status": "ACTIVE",
}
LOAN = {
    "_id": "acc_e2e_loan",
    "name": "Home loan",
    "formatted_account": "38-9014-0123456-05",
    "type": "LOAN",
    "status": "ACTIVE",
}
TRANSACTIONS = [
    {
        "_id": "trans_e2e_1",
        "_account": "acc_e2e",
        "date": "2026-09-01T00:00:00.000Z",
        "description": "SALARY ACME LTD",
        "amount": 2380.0,
        "balance": 4530.0,
        "type": "DIRECT CREDIT",
    },
    {
        "_id": "trans_e2e_2",
        "_account": "acc_e2e",
        "date": "2026-09-02T00:00:00.000Z",
        "description": "POS W/D PAK N SAVE",
        "amount": -285.4,
        "balance": 4244.6,
        "type": "EFTPOS",
    },
    {
        "_id": "trans_e2e_3",
        "_account": "acc_e2e",
        "date": "2026-09-03T00:00:00.000Z",
        "description": "TRANSFER TO SAVINGS",
        "amount": -500.0,
        "balance": 3744.6,
        "type": "TRANSFER",
        "meta": {"particulars": "rainy day", "other_account": "38-9014-0000000-01"},
    },
    # An automatic payment to the household's own loan, the shape Akahu leaves
    # without `other_account`. The producer pairs the two sides; the core
    # must then read what it wrote back exactly as it reads the bank's own.
    {
        "_id": "trans_e2e_4",
        "_account": "acc_e2e",
        "date": "2026-09-04T00:00:00.000Z",
        "description": "AP#10000001 TO A B SAMPLE",
        "amount": -23.68,
        "balance": 3720.92,
        "type": "TRANSFER",
    },
    {
        "_id": "trans_e2e_5",
        "_account": "acc_e2e_loan",
        "date": "2026-09-04T00:00:00.000Z",
        "description": "AP#10000001 FROM A B SAMPLE",
        "amount": 23.68,
        "balance": -1000.0,
        "type": "TRANSFER",
    },
]
FILENAME = "akahu-e2e-contract.csv"


def _get(url: str) -> dict | list:
    with urllib.request.urlopen(url) as response:
        return json.load(response)


def _post(url: str, body: bytes, content_type: str) -> dict:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": content_type}, method="POST"
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


@pytest.fixture
def api(base_url: str) -> str:
    return f"{base_url}/api"


@pytest.fixture
def csv_text() -> str:
    rows = to_rows(TRANSACTIONS, [ACCOUNT, LOAN])
    recover_counterparties(rows, [ACCOUNT, LOAN])
    return to_csv(rows)


def test_the_sniffer_reads_every_column_that_matters(api: str, csv_text: str):
    """
    /preview parses without saving, which is exactly the question: signed
    amounts, the account number, the balance, and the other party's account
    as the counterparty - the field that proves a transfer is internal -
    whether the bank supplied it or the producer worked it out. The extra
    `Counterparty source` column must be ignored, not mistaken for a memo.
    """
    body, content_type = multipart(csv_text, FILENAME)
    result = _post(f"{api}/preview", body, content_type)

    report = result["report"]
    assert report["had_header"], report
    assert report["signed_amounts"], report
    assert report["confidence"] == "high", report["warnings"]
    assert report["rows_parsed"] == len(TRANSACTIONS)

    by_memo = {r["memo"]: r for r in result["sample_rows"]}
    transfer = by_memo["TRANSFER TO SAVINGS"]
    assert transfer["account"] == "38-9014-0123456-00"
    assert transfer["amount"] == -500.0
    assert transfer["balance"] == 3744.6
    assert transfer["counterparty"] == "38-9014-0000000-01"
    assert transfer["date"] == "2026-09-03"
    assert "rainy day" in transfer["match_text"]
    assert by_memo["SALARY ACME LTD"]["amount"] == 2380.0

    paid = by_memo["AP#10000001 TO A B SAMPLE"]
    assert paid["counterparty"] == "38-9014-0123456-05", "recovered by the producer"
    assert by_memo["AP#10000001 FROM A B SAMPLE"]["counterparty"] == "38-9014-0123456-00"
    assert "AP# pair" not in paid["match_text"], "the source column is not text to match on"


def test_a_real_submission_is_logged_as_this_producer_and_never_doubled(api: str, csv_text: str):
    """
    The real `submit()`, through the real front door. The import log must say
    `tool:akahu` - the whole reason this producer speaks HTTP rather than
    dropping a file in the inbox - and a second identical run must add nothing.
    """
    before = _get(f"{api}/health")["stats"]["transactions"]
    try:
        result = submit(csv_text, FILENAME, api=api)
        assert result["report"]["rows_parsed"] == len(TRANSACTIONS)

        mine = [e for e in _get(f"{api}/imports") if e["filename"] == FILENAME]
        assert mine and mine[0]["producer"] == PRODUCER
        assert mine[0]["rows_new"] == len(TRANSACTIONS)
        assert _get(f"{api}/health")["stats"]["transactions"] == before + len(TRANSACTIONS)

        submit(csv_text, FILENAME, api=api)
        assert _get(f"{api}/health")["stats"]["transactions"] == before + len(TRANSACTIONS)
    finally:
        request = urllib.request.Request(f"{api}/imports/{FILENAME}", method="DELETE")
        urllib.request.urlopen(request).close()

    assert _get(f"{api}/health")["stats"]["transactions"] == before
