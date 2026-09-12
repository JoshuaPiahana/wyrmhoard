"""
Shaping Akahu's JSON into the CSV Wyrmhoard already reads.

Nothing here touches Akahu, and nothing here imports Wyrmhoard - a producer's
only contact with the core is HTTP, and test_layering.py checks that from the
outside. The fixtures are the documented response shapes with the values
changed, and the account numbers are from the guard script's synthetic
allowlist. The test that the emitted CSV is actually understood by the core
lives in e2e/test_akahu_producer.py, where it can be asked over HTTP.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from akahu import (
    account_label,
    calendar_date,
    fetch_transactions,
    paginate,
    to_csv,
    to_rows,
)

EVERYDAY = {
    "_id": "acc_everyday",
    "name": "Everyday",
    "formatted_account": "38-9014-0123456-00",
    "type": "CHECKING",
    "status": "ACTIVE",
}
SAVINGS = {
    "_id": "acc_savings",
    "name": "Rainy day",
    "formatted_account": "38-9014-0000000-01",
    "type": "SAVINGS",
    "status": "ACTIVE",
}
KIWISAVER = {
    "_id": "acc_ks",
    "name": "Growth Fund",
    "type": "KIWISAVER",
    "status": "ACTIVE",
}
ACCOUNTS = [EVERYDAY, SAVINGS, KIWISAVER]


def txn(_id, account, date_, amount, description, **extra):
    return {
        "_id": _id,
        "_account": account["_id"],
        "_connection": "conn_x",
        "created_at": "2026-09-02T01:00:00.000Z",
        "date": date_,
        "description": description,
        "amount": amount,
        "type": "EFTPOS",
        **extra,
    }


TRANSACTIONS = [
    txn("trans_1", EVERYDAY, "2026-09-01T00:00:00.000Z", 2380.0, "SALARY ACME LTD", balance=4530.0),
    txn(
        "trans_2",
        EVERYDAY,
        "2026-09-02T00:00:00.000Z",
        -285.4,
        "POS W/D PAK N SAVE",
        balance=4244.6,
        merchant={"_id": "merchant_x", "name": "Pak'nSave"},
        category={"_id": "nzfcc_x", "name": "Supermarkets", "groups": {}},
    ),
    txn(
        "trans_3",
        EVERYDAY,
        "2026-09-03T00:00:00.000Z",
        -500.0,
        "TRANSFER TO SAVINGS",
        balance=3744.6,
        type="TRANSFER",
        meta={
            "particulars": "rainy day",
            "code": "sept",
            "reference": "topup",
            "other_account": "38-9014-0000000-01",
        },
    ),
    txn("trans_4", SAVINGS, "2026-09-03T00:00:00.000Z", 500.0, "TRANSFER FROM EVERYDAY"),
    txn("trans_5", KIWISAVER, "2026-09-03T00:00:00.000Z", 120.0, "EMPLOYER CONTRIBUTION"),
]


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "stamp, expected, why",
    [
        ("2026-09-12T00:00:00.000Z", "2026-09-12", "day-level: the bank's own date"),
        ("2026-09-11T12:00:00.000Z", "2026-09-12", "NZ midnight (NZST) stored as UTC"),
        ("2026-10-11T11:00:00.000Z", "2026-10-12", "NZ midnight (NZDT) stored as UTC"),
        ("2026-09-12T09:30:00.000Z", "2026-09-12", "9:30pm NZST, same day"),
        ("2026-09-12T13:30:00.000Z", "2026-09-13", "1:30am NZST, next day"),
    ],
)
def test_the_stored_date_is_the_one_a_household_would_write(stamp, expected, why):
    assert calendar_date(stamp) == expected, why


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
def test_pagination_repeats_the_window_on_every_page():
    """Akahu's cursor does not carry start/end; forgetting them returns two years."""
    calls = []

    def get(path, params):
        calls.append(dict(params))
        if "cursor" not in params:
            return {"success": True, "items": [{"n": 1}], "cursor": {"next": "page2"}}
        return {"success": True, "items": [{"n": 2}], "cursor": {"next": None}}

    items = paginate(get, "/transactions", {"start": "S", "end": "E"})

    assert [i["n"] for i in items] == [1, 2]
    assert calls == [{"start": "S", "end": "E"}, {"start": "S", "end": "E", "cursor": "page2"}]


def test_the_window_is_fetched_a_day_wide_and_trimmed_by_calendar_date():
    """
    Over-fetch, then keep what falls inside the dates the household asked for.

    The rows on the edges are exactly the ones a timezone slip would get wrong,
    so both edges are exercised: a row Akahu returns from the widened window
    that is *before* start must be dropped, and one *on* start must be kept.
    """
    asked = {}

    def get(path, params):
        asked.update(params)
        return {
            "success": True,
            "cursor": {"next": None},
            "items": [
                txn("t0", EVERYDAY, "2026-08-31T00:00:00.000Z", -1.0, "before"),
                txn("t1", EVERYDAY, "2026-09-01T00:00:00.000Z", -1.0, "on start"),
                txn("t2", EVERYDAY, "2026-09-05T00:00:00.000Z", -1.0, "on end"),
                txn("t3", EVERYDAY, "2026-09-06T00:00:00.000Z", -1.0, "after"),
            ],
        }

    kept = fetch_transactions(get, date(2026, 9, 1), date(2026, 9, 5))

    assert [t["description"] for t in kept] == ["on start", "on end"]
    assert asked["start"] == "2026-08-31T00:00:00.000Z"
    assert asked["end"] == "2026-09-06T23:59:59.999Z"


# --------------------------------------------------------------------------
# Shaping
# --------------------------------------------------------------------------
def test_the_account_column_is_the_number_the_bank_prints():
    """So rows from a CSV export and rows from Akahu land on the same account."""
    assert account_label(EVERYDAY) == "38-9014-0123456-00"
    assert account_label(KIWISAVER) == "Growth Fund", "no number: fall back to Akahu's name"


def test_every_documented_field_lands_in_a_column():
    rows = {r["Description"]: r for r in to_rows(TRANSACTIONS, ACCOUNTS)}

    transfer = rows["TRANSFER TO SAVINGS"]
    assert transfer["Date"] == "2026-09-03"
    assert transfer["Account number"] == "38-9014-0123456-00"
    assert transfer["Amount"] == "-500.00", "money out stays negative"
    assert transfer["Balance"] == "3744.60"
    assert transfer["Type"] == "TRANSFER"
    assert transfer["Particulars"] == "rainy day"
    assert transfer["Code"] == "sept"
    assert transfer["Reference"] == "topup"
    assert transfer["Other party account number"] == "38-9014-0000000-01"

    shop = rows["POS W/D PAK N SAVE"]
    assert shop["Merchant"] == "Pak'nSave"
    assert shop["Category"] == "Supermarkets"

    assert rows["TRANSFER FROM EVERYDAY"]["Balance"] == "", "absent, not zero"
    assert rows["EMPLOYER CONTRIBUTION"]["Account number"] == "Growth Fund"


def test_only_restricts_by_number_name_or_id():
    labels = {r["Account number"] for r in to_rows(TRANSACTIONS, ACCOUNTS, only={"Rainy day"})}
    assert labels == {"38-9014-0000000-01"}

    labels = {r["Account number"] for r in to_rows(TRANSACTIONS, ACCOUNTS, only={"acc_ks"})}
    assert labels == {"Growth Fund"}


def test_a_transaction_on_an_unlisted_account_is_refused_not_guessed():
    orphan = txn("t", {"_id": "acc_nobody"}, "2026-09-03T00:00:00.000Z", -1.0, "?")
    with pytest.raises(SystemExit, match="acc_nobody"):
        to_rows([orphan], ACCOUNTS)


# --------------------------------------------------------------------------
# Stability
# --------------------------------------------------------------------------
def test_re_emitting_the_same_transactions_yields_the_same_bytes():
    """Daily runs overlap by design; identical rows must fingerprint identically."""
    shuffled = list(reversed(TRANSACTIONS))
    assert to_csv(to_rows(TRANSACTIONS, ACCOUNTS)) == to_csv(to_rows(shuffled, ACCOUNTS))
