"""
Fetch a household's bank transactions from Akahu and hand them to Wyrmhoard.

This is a producer, and it is the first one here that is not entirely local.
Akahu is an open-banking aggregator: it holds a connection to your bank and a
copy of your transactions on its own servers, refreshed daily, by design. This
program copies that copy onto your machine. Read the README before running it.

    python akahu.py --start 2026-09-01 --dry-run    # fetch and print, submit nothing
    python akahu.py --start 2026-09-01              # fetch and submit

It talks to two things: https://api.akahu.io with the two tokens Akahu gave you,
and http://localhost:8080. Nothing else.

## What it holds

Two tokens, read from the environment and never written anywhere:

    AKAHU_APP_TOKEN     app_token_...    identifies your personal app
    AKAHU_USER_TOKEN    user_token_...   authorises reading your accounts

Wyrmhoard never sees either. It has no store for a credential, no way to use
one, and no way to make an outbound request - which is the reason this program
exists as a separate thing you chose to run.

## What it emits

A bank CSV, in the columns Wyrmhoard's sniffer already reads, submitted to the
same `/import` endpoint the dashboard uses - saying `tool:akahu` rather than
`human:dashboard`, so the ledger records forever which rows arrived through a
third party. Nothing about Wyrmhoard's schema knows Akahu exists.

The account column is Akahu's `formatted_account`, which is the same
bank-branch-account-suffix number your bank prints in its own exports. That is
what lets rows from a CSV you downloaded and rows from Akahu land on the same
account rather than on two accounts that happen to share a name.

## Why `--start` is required, every run

A transaction's identity in Wyrmhoard is its account, date, description,
amount and balance. Akahu's description of a transaction is the bank's, with
"minor cleanup", and that cleanup is enough that a transaction already in the
ledger from a CSV export will not be recognised as the same one when it
arrives again through Akahu. Overlap the two and spending is counted twice.

So there is no default window. You say where Akahu's history begins - the day
after your last CSV export - and you leave that flag alone. Re-running with the
same `--start` is safe: rows already stored are skipped, and only the new days
land. The window grows over time but Akahu caps it at two years and a page is
a hundred rows, so a daily run is a few requests.

## Dates

Akahu dates are ISO timestamps in UTC. Their docs show midnight UTC; a real
Kiwibank feed stamps each transaction at *New Zealand* midnight expressed in
UTC - `T12:00:00.000Z` in winter, `T11:00:00.000Z` in daylight time - so
taking the date part would put every row a day early. The rule: a stamp at
exactly midnight UTC keeps its UTC date (New Zealand is ahead, so that is
midday here and the bank's own date); anything else converts to
Pacific/Auckland first. Checked against a week of real transactions on
12 September 2026. The dry run prints both stamps so a bank that does it
differently shows up before anything is stored.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

PRODUCER = "tool:akahu"
AKAHU_API = "https://api.akahu.io/v1"
DEFAULT_API = "http://localhost:8080/api"

ENV_APP_TOKEN = "AKAHU_APP_TOKEN"
ENV_USER_TOKEN = "AKAHU_USER_TOKEN"

NZ = ZoneInfo("Pacific/Auckland")

# The header words are Wyrmhoard's own hints (`HEADER_HINTS` in
# api/wyrmhoard/ingest/bank_csv.py), spelt the way Kiwibank spells them.
# Columns the sniffer does not know - Type, Merchant, Category - are kept in
# the file because somebody will want them later, and ignored on import.
COLUMNS = (
    "Date",
    "Account number",
    "Description",
    "Amount",
    "Balance",
    "Type",
    "Particulars",
    "Code",
    "Reference",
    "Other party account number",
    "Merchant",
    "Category",
)

Getter = Callable[[str, dict[str, str]], dict[str, Any]]


# --------------------------------------------------------------------------
# Talking to Akahu
# --------------------------------------------------------------------------
def tokens_from_env() -> tuple[str, str]:
    app = os.environ.get(ENV_APP_TOKEN, "").strip()
    user = os.environ.get(ENV_USER_TOKEN, "").strip()
    missing = [n for n, v in ((ENV_APP_TOKEN, app), (ENV_USER_TOKEN, user)) if not v]
    if missing:
        raise SystemExit(
            f"Not set: {', '.join(missing)}. Export both in the shell that runs "
            "this - they are never read from a file in the repository."
        )
    return app, user


def akahu_getter(app_token: str, user_token: str) -> Getter:
    """A GET against Akahu with the two tokens in the headers Akahu expects."""

    def get(path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{AKAHU_API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {user_token}",
                "X-Akahu-Id": app_token,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = json.load(exc).get("message", exc.reason)
            except ValueError:
                message = exc.reason
            raise SystemExit(f"Akahu refused {path}: {exc.code} {message}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Cannot reach Akahu ({exc.reason}).") from exc

    return get


def paginate(get: Getter, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
    """
    Every item across every page.

    Akahu pages are a hundred rows and the cursor is opaque. The original
    `start`/`end` are repeated on every page because the cursor does not carry
    them - a detail their docs call out, and one that would otherwise return
    the whole two years from page two onward.
    """
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = dict(params)
        if cursor:
            query["cursor"] = cursor
        body = get(path, query)
        if not body.get("success", True):
            raise SystemExit(f"Akahu reported failure on {path}: {body.get('message')}")
        items.extend(body.get("items", []))
        cursor = (body.get("cursor") or {}).get("next")
        if not cursor:
            return items


def fetch_accounts(get: Getter) -> list[dict[str, Any]]:
    return paginate(get, "/accounts", {})


def fetch_transactions(get: Getter, start: date, end: date) -> list[dict[str, Any]]:
    """
    Settled transactions between two calendar dates, inclusive.

    The window asked of Akahu is a day wider each side, then trimmed here by
    calendar date. Akahu's `start` is exclusive, its timestamps are UTC, and a
    bank may stamp a day's transactions at UTC midnight or at NZ midnight - so
    rather than reason about which, over-fetch and filter on the date this
    program will actually store. Pending transactions are a separate endpoint
    and are deliberately not fetched: they change, and a changed row is a new
    row to Wyrmhoard.
    """
    params = {
        "start": f"{(start - timedelta(days=1)).isoformat()}T00:00:00.000Z",
        "end": f"{(end + timedelta(days=1)).isoformat()}T23:59:59.999Z",
    }
    items = paginate(get, "/transactions", params)
    return [t for t in items if start.isoformat() <= calendar_date(t["date"]) <= end.isoformat()]


# --------------------------------------------------------------------------
# Shaping
# --------------------------------------------------------------------------
def calendar_date(stamp: str) -> str:
    """The date a household would write on this transaction. See the docstring."""
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if when.tzinfo is None:
        return when.date().isoformat()
    utc = when.astimezone(timezone.utc)
    if (utc.hour, utc.minute, utc.second) == (0, 0, 0):
        return utc.date().isoformat()
    return utc.astimezone(NZ).date().isoformat()


def account_label(account: dict[str, Any]) -> str:
    """
    What the account is called in the ledger.

    `formatted_account` for anything that has one, because it matches the bank's
    own exports. KiwiSaver and investment accounts have none, so they fall back
    to Akahu's name for them - which will not match a CSV, but nothing about a
    KiwiSaver account arrives by CSV either.
    """
    return (account.get("formatted_account") or account.get("name") or account["_id"]).strip()


def _money(value: Any) -> str:
    return "" if value is None else f"{float(value):.2f}"


def to_rows(
    transactions: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    only: set[str] | None = None,
) -> list[dict[str, str]]:
    """
    One CSV row per transaction, in Wyrmhoard's columns.

    `only` restricts to accounts named by label, Akahu name or Akahu id, for a
    household that connected everything and wants to import less than that.
    """
    by_id = {a["_id"]: a for a in accounts}
    rows: list[dict[str, str]] = []
    for t in transactions:
        account = by_id.get(t["_account"])
        if account is None:
            raise SystemExit(
                f"Transaction {t['_id']} belongs to account {t['_account']}, which "
                "/accounts did not list. Refusing to guess which account it is."
            )
        label = account_label(account)
        if only and not ({label, account.get("name", ""), account["_id"]} & only):
            continue
        meta = t.get("meta") or {}
        rows.append(
            {
                "Date": calendar_date(t["date"]),
                "Account number": label,
                "Description": (t.get("description") or "").strip(),
                "Amount": _money(t["amount"]),
                "Balance": _money(t.get("balance")),
                "Type": t.get("type") or "",
                "Particulars": meta.get("particulars") or "",
                "Code": meta.get("code") or "",
                "Reference": meta.get("reference") or "",
                "Other party account number": meta.get("other_account") or "",
                "Merchant": (t.get("merchant") or {}).get("name") or "",
                "Category": (t.get("category") or {}).get("name") or "",
            }
        )
    rows.sort(key=lambda r: (r["Account number"], r["Date"]))
    return rows


def to_csv(rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


# --------------------------------------------------------------------------
# Talking to Wyrmhoard
# --------------------------------------------------------------------------
def multipart(csv_text: str, filename: str) -> tuple[bytes, str]:
    """
    The request body the dashboard's file picker would send, plus its content type.

    Built by hand because the standard library has no multipart encoder and a
    dependency for fourteen lines is not worth asking somebody to install.
    """
    boundary = f"----wyrmhoard-{uuid4().hex}"
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="producer"\r\n\r\n{PRODUCER}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: text/csv\r\n\r\n"
        ).encode()
        + csv_text.encode("utf-8")
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


def submit(csv_text: str, filename: str, api: str = DEFAULT_API) -> dict[str, Any]:
    """POST the file the way the dashboard does, naming this producer."""
    body, content_type = multipart(csv_text, filename)
    request = urllib.request.Request(
        f"{api}/import", data=body, headers={"Content-Type": content_type}, method="POST"
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = json.load(exc).get("detail", exc.reason)
        raise SystemExit(f"Wyrmhoard refused the import:\n  {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Cannot reach Wyrmhoard at {api} - is it running? ({exc.reason})"
        ) from exc


def rows_new(filename: str, api: str = DEFAULT_API) -> int | None:
    """How many of the submitted rows were new - the import log knows, the response does not."""
    try:
        with urllib.request.urlopen(f"{api}/imports") as response:
            logged = json.load(response)
    except (urllib.error.URLError, ValueError):
        return None
    mine = [entry for entry in logged if entry.get("filename") == filename]
    if not mine:
        return None
    return int(max(mine, key=lambda e: e.get("imported_at") or "")["rows_new"])


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date like 2026-09-01") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--start",
        type=_date,
        required=True,
        help="first date to import, inclusive - the day after your last CSV export",
    )
    parser.add_argument(
        "--end", type=_date, default=None, help="last date, inclusive; default today"
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        metavar="LABEL",
        help="import only this account (number, Akahu name or id); repeatable",
    )
    parser.add_argument("--api", default=DEFAULT_API, help=f"default {DEFAULT_API}")
    parser.add_argument("--dry-run", action="store_true", help="fetch and print, submit nothing")
    args = parser.parse_args(argv)

    end = args.end or datetime.now(NZ).date()
    if end < args.start:
        parser.error(f"--end {end} is before --start {args.start}")

    get = akahu_getter(*tokens_from_env())
    accounts = fetch_accounts(get)
    transactions = fetch_transactions(get, args.start, end)
    rows = to_rows(transactions, accounts, only=set(args.account) or None)

    print(f"Akahu lists {len(accounts)} accounts:")
    for account in accounts:
        status = account.get("status", "?")
        print(
            f"  {account_label(account):<24}{account.get('type', '?'):<12}"
            f"{status:<10}{account.get('name', '')}"
        )

    if not rows:
        print(f"No settled transactions between {args.start} and {end}.")
        return 0

    net = sum(float(r["Amount"]) for r in rows)
    dates = [r["Date"] for r in rows]
    # The dates actually returned, not the window asked for. On a first run
    # the gap between the two is how far back Akahu's history really reaches.
    print(
        f"{len(rows)} transactions dated {min(dates)} to {max(dates)} "
        f"(asked {args.start} to {end}) across "
        f"{len({r['Account number'] for r in rows})} accounts, net {net:+.2f}"
    )

    if args.dry_run:
        print("  first rows, with Akahu's own timestamp beside the date this will store:")
        for t in sorted(transactions, key=lambda t: t["date"])[:8]:
            print(
                f"  {calendar_date(t['date'])}  <- {t['date']}  "
                f"{float(t['amount']):>10.2f}  {(t.get('description') or '')[:40]}"
            )
        return 0

    filename = f"akahu-{end.isoformat()}.csv"
    result = submit(to_csv(rows), filename, api=args.api)
    report = result.get("report", {})
    for warning in report.get("warnings", []):
        print(f"  warning: {warning}")
    new = rows_new(filename, api=args.api)
    stored = f"{new} new" if new is not None else "an unknown number new"
    print(
        f"Wyrmhoard read {report.get('rows_parsed')} of {report.get('rows_seen')} rows "
        f"({report.get('confidence')} confidence), {stored}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
