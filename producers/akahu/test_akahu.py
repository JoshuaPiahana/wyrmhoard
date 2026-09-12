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

import csv
import io
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import akahu
from akahu import (
    AP_BLANK,
    COUNTERPARTY,
    FROM_AKAHU,
    FROM_AP_PAIR,
    FROM_TRANSFER_SUFFIX,
    SOURCE,
    TRANSFERS_BLANK,
    account_label,
    calendar_date,
    counterparty_summary,
    fetch_transactions,
    only_accounts,
    paginate,
    recover_counterparties,
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
# Same bank-branch-account as EVERYDAY, different suffix - the shape of a
# household's own pots and loans, and what the transfer-suffix rule needs.
LOAN = {
    "_id": "acc_loan",
    "name": "Home loan",
    "formatted_account": "38-9014-0123456-05",
    "type": "LOAN",
    "status": "ACTIVE",
}
KIWISAVER = {
    "_id": "acc_ks",
    "name": "Growth Fund",
    "type": "KIWISAVER",
    "status": "ACTIVE",
}
ACCOUNTS = [EVERYDAY, SAVINGS, LOAN, KIWISAVER]


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
    assert transfer[COUNTERPARTY] == "38-9014-0000000-01"
    assert transfer[SOURCE] == FROM_AKAHU

    shop = rows["POS W/D PAK N SAVE"]
    assert shop["Merchant"] == "Pak'nSave"
    assert shop["Category"] == "Supermarkets"
    assert shop[SOURCE] == "", "no other party, no source"

    assert rows["TRANSFER FROM EVERYDAY"]["Balance"] == "", "absent, not zero"
    assert rows["EMPLOYER CONTRIBUTION"]["Account number"] == "Growth Fund"


def test_only_restricts_by_number_name_or_id():
    rows = to_rows(TRANSACTIONS, ACCOUNTS)

    labels = {r["Account number"] for r in only_accounts(rows, ACCOUNTS, {"Rainy day"})}
    assert labels == {"38-9014-0000000-01"}

    labels = {r["Account number"] for r in only_accounts(rows, ACCOUNTS, {"acc_ks"})}
    assert labels == {"Growth Fund"}


def test_a_transaction_on_an_unlisted_account_is_refused_not_guessed():
    orphan = txn("t", {"_id": "acc_nobody"}, "2026-09-03T00:00:00.000Z", -1.0, "?")
    with pytest.raises(SystemExit, match="acc_nobody"):
        to_rows([orphan], ACCOUNTS)


# --------------------------------------------------------------------------
# Recovering the other side of the household's own movements
# --------------------------------------------------------------------------
# The two shapes a Kiwibank feed emits for money moving between a household's
# own accounts, with the holder names and AP numbers changed. Akahu supplies
# no `other_account` on any of them - that is the whole problem.
NZ_MIDNIGHT = "2026-09-08T12:00:00.000Z"  # stored as 2026-09-09


def movement(_id, account, amount, description, when=NZ_MIDNIGHT, **extra):
    return txn(_id, account, when, amount, description, type="TRANSFER", **extra)


OWN_MOVEMENTS = [
    # A transfer: the text ends in the *other* account's suffix, on both sides.
    movement("mv_1", EVERYDAY, -520.0, "TRANSFER TO A B SAMPLE, C D SAMPLE - 05"),
    movement("mv_2", LOAN, 520.0, "TRANSFER FROM A B SAMPLE, C D SAMPLE - 00"),
    # An automatic payment: the number is in the text and the suffix is not.
    movement("mv_3", EVERYDAY, -23.68, "AP#10000001 TO A B SAMPLE", meta={"reference": "LOAN"}),
    movement("mv_4", LOAN, 23.68, "AP#10000001 FROM A B SAMPLE, C D SAMPLE"),
]


def recovered(transactions, accounts=ACCOUNTS):
    rows = to_rows(transactions, accounts)
    recover_counterparties(rows, accounts)
    return {r["Description"]: r for r in rows}


def test_a_transfer_names_its_destination_by_suffix_and_both_sides_are_filled():
    rows = recovered(OWN_MOVEMENTS)

    out = rows["TRANSFER TO A B SAMPLE, C D SAMPLE - 05"]
    assert out[COUNTERPARTY] == "38-9014-0123456-05"
    assert out[SOURCE] == FROM_TRANSFER_SUFFIX

    back = rows["TRANSFER FROM A B SAMPLE, C D SAMPLE - 00"]
    assert back[COUNTERPARTY] == "38-9014-0123456-00"
    assert back[SOURCE] == FROM_TRANSFER_SUFFIX


def test_a_suffix_counts_only_on_the_same_prefix_and_only_for_a_listed_account():
    """
    SAVINGS is -01 on a *different* bank-branch-account. "- 01" on EVERYDAY
    would mean -01 on EVERYDAY's own prefix, which nobody listed, so it stays blank
    rather than being pointed at an account the household may not even own.
    """
    rows = recovered(
        [
            movement("t1", EVERYDAY, -50.0, "TRANSFER TO A B SAMPLE - 01"),
            movement("t2", EVERYDAY, -50.0, "TRANSFER TO A B SAMPLE - 07"),
            movement("t3", EVERYDAY, -50.0, "TRANSFER TO SAVINGS"),
            movement("t4", EVERYDAY, -50.0, "TRANSFER TO A B SAMPLE - 00"),
        ]
    )

    for row in rows.values():
        assert row[COUNTERPARTY] == "" and row[SOURCE] == "", row["Description"]
    assert counterparty_summary(list(rows.values()))[TRANSFERS_BLANK] == 4


def test_both_sides_of_an_automatic_payment_are_paired_by_number_day_and_amount():
    rows = recovered(OWN_MOVEMENTS)

    out = rows["AP#10000001 TO A B SAMPLE"]
    assert out[COUNTERPARTY] == "38-9014-0123456-05"
    assert out[SOURCE] == FROM_AP_PAIR

    back = rows["AP#10000001 FROM A B SAMPLE, C D SAMPLE"]
    assert back[COUNTERPARTY] == "38-9014-0123456-00"
    assert back[SOURCE] == FROM_AP_PAIR


@pytest.mark.parametrize(
    "other, why",
    [
        (None, "the other side is not in the feed"),
        (movement("x", LOAN, 23.68, "AP#10000002 FROM A B SAMPLE"), "a different AP number"),
        (
            movement(
                "x", LOAN, 23.68, "AP#10000001 FROM A B SAMPLE", when="2026-09-09T12:00:00.000Z"
            ),
            "a different day",
        ),
        (movement("x", LOAN, 23.69, "AP#10000001 FROM A B SAMPLE"), "a different amount"),
        (
            movement("x", LOAN, 23.68, "AP#10000001 TO A B SAMPLE"),
            "the direction word disagrees with the sign",
        ),
        (
            movement("x", EVERYDAY, 23.68, "AP#10000001 FROM A B SAMPLE"),
            "the same account both sides",
        ),
    ],
)
def test_an_automatic_payment_without_exactly_one_partner_stays_blank(other, why):
    out = movement("ap_out", EVERYDAY, -23.68, "AP#10000001 TO A B SAMPLE")
    rows = recovered([out] + ([other] if other else []))

    for row in rows.values():
        assert row[COUNTERPARTY] == "" and row[SOURCE] == "", why


def test_more_than_one_candidate_is_ambiguous_and_nothing_is_guessed():
    rows = recovered(
        [
            movement("a", EVERYDAY, -23.68, "AP#10000001 TO A B SAMPLE"),
            movement("b", SAVINGS, -23.68, "AP#10000001 TO A B SAMPLE AGAIN"),
            movement("c", LOAN, 23.68, "AP#10000001 FROM A B SAMPLE"),
        ]
    )

    assert all(row[COUNTERPARTY] == "" for row in rows.values())
    assert counterparty_summary(list(rows.values()))[AP_BLANK] == 3


def test_what_akahu_supplied_is_never_overwritten():
    """
    A value from the bank outranks anything worked out from text - even when
    the text disagrees with it. And a row Akahu has already placed is not a
    candidate partner for the pairing rule, so its would-be other side stays
    blank instead of contradicting the bank.
    """
    elsewhere = {"other_account": "99-9999-9999999-99"}
    rows = recovered(
        [
            movement("t", EVERYDAY, -50.0, "TRANSFER TO A B SAMPLE - 05", meta=elsewhere),
            movement("a", EVERYDAY, -23.68, "AP#10000001 TO A B SAMPLE", meta=elsewhere),
            movement("b", LOAN, 23.68, "AP#10000001 FROM A B SAMPLE"),
        ]
    )

    assert rows["TRANSFER TO A B SAMPLE - 05"][COUNTERPARTY] == "99-9999-9999999-99"
    assert rows["TRANSFER TO A B SAMPLE - 05"][SOURCE] == FROM_AKAHU
    assert rows["AP#10000001 TO A B SAMPLE"][COUNTERPARTY] == "99-9999-9999999-99"
    assert rows["AP#10000001 FROM A B SAMPLE"][COUNTERPARTY] == ""


def test_nothing_else_is_inferred():
    """A suffix-looking tail on a payment or a purchase means nothing to these rules."""
    rows = recovered(
        [
            movement("p", EVERYDAY, -100.0, "PAY A B SAMPLE - 05"),
            movement("d", EVERYDAY, -100.0, "Direct Debit SOMEONE - 05"),
            txn("s", EVERYDAY, NZ_MIDNIGHT, -285.4, "POS W/D PAK N SAVE - 05"),
        ]
    )

    assert all(row[COUNTERPARTY] == "" and row[SOURCE] == "" for row in rows.values())


def test_the_summary_counts_what_was_filled_and_what_was_not():
    rows = to_rows(TRANSACTIONS + OWN_MOVEMENTS, ACCOUNTS)
    recover_counterparties(rows, ACCOUNTS)

    assert counterparty_summary(rows) == {
        FROM_AKAHU: 1,  # TRANSFER TO SAVINGS came with other_account
        FROM_TRANSFER_SUFFIX: 2,
        FROM_AP_PAIR: 2,
        TRANSFERS_BLANK: 1,  # TRANSFER FROM EVERYDAY: no suffix, nothing supplied
        AP_BLANK: 0,
    }


def test_the_other_side_is_seen_even_when_its_account_is_not_submitted(monkeypatch, capsys):
    """
    `--account` narrows what is submitted, not what is looked at. The loan's
    AP# row is what proves the everyday account's AP# row is internal, so the
    pairing has to happen before the loan is left out - and the summary
    printed has to describe the rows actually being sent. This goes through
    `main` because the order of those steps is the thing being pinned.
    """

    def fake_getter(app_token, user_token):
        def get(path, params):
            items = ACCOUNTS if path == "/accounts" else OWN_MOVEMENTS
            return {"success": True, "items": items, "cursor": {"next": None}}

        return get

    sent: dict[str, str] = {}

    def fake_submit(csv_text, filename, api):
        sent["csv"] = csv_text
        return {"report": {"rows_parsed": 2, "rows_seen": 2, "confidence": "high"}}

    monkeypatch.setattr(akahu, "akahu_getter", fake_getter)
    monkeypatch.setattr(akahu, "submit", fake_submit)
    monkeypatch.setattr(akahu, "rows_new", lambda filename, api: 2)
    monkeypatch.setenv("AKAHU_APP_TOKEN", "app_token_test")
    monkeypatch.setenv("AKAHU_USER_TOKEN", "user_token_test")

    argv = ["--start", "2026-09-01", "--end", "2026-09-30", "--account", "38-9014-0123456-00"]
    assert akahu.main(argv) == 0

    rows = list(csv.DictReader(io.StringIO(sent["csv"])))
    assert {r["Account number"] for r in rows} == {"38-9014-0123456-00"}
    assert {r[COUNTERPARTY] for r in rows} == {"38-9014-0123456-05"}
    assert {r[SOURCE] for r in rows} == {FROM_TRANSFER_SUFFIX, FROM_AP_PAIR}
    printed = capsys.readouterr().out
    assert (
        "0 from Akahu, 1 by transfer suffix, 1 by AP# pairing; still blank: 0 transfers, 0 AP# rows"
        in printed
    )


# --------------------------------------------------------------------------
# Stability
# --------------------------------------------------------------------------
def test_re_emitting_the_same_transactions_yields_the_same_bytes():
    """Daily runs overlap by design; identical rows must fingerprint identically."""

    def emit(transactions):
        rows = to_rows(transactions, ACCOUNTS)
        recover_counterparties(rows, ACCOUNTS)
        return to_csv(rows)

    everything = TRANSACTIONS + OWN_MOVEMENTS
    assert emit(everything) == emit(list(reversed(everything)))
