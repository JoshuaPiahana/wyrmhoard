"""
Synthetic sample data.

Generates a plausible fourteen months of transactions for a single-income
family with three children, a mortgage, and a household that spends slightly
more than it earns. It exists so the tool can be seen working - and its
analysis sanity-checked - before anybody trusts it with real bank data.

The output deliberately mimics the *hardest* Kiwibank layout: sixteen columns,
no header row, DD-MM-YYYY dates, and separate credit/debit columns. If the
sniffer can read this, it can read the friendlier exports.

Numbers here are invented. Any resemblance to a real household's finances is
the point, but none of it is real data.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

ACCOUNT = "38-9014-0123456-00"

GROCERY_STORES = ["PAK N SAVE PALM STH", "COUNTDOWN TERRACE END", "NEW WORLD ASHHURST"]
FUEL_STOPS = ["Z ASHHURST", "BP CONNECT PALMERSTON", "GULL FEILDING"]
TAKEAWAYS = [
    "MCDONALDS PALMERSTON",
    "DOMINOS PIZZA PN",
    "KFC PALMERSTON NTH",
    "HELL PIZZA TERRACE",
    "UBER EATS",
]
CAFES = ["CAFE ESPRESSO PN", "COLUMBUS COFFEE", "THE BAKERY ASHHURST"]
SHOPS = ["THE WAREHOUSE PALM STH", "KMART PALMERSTON", "BRISCOES PN", "TEMU COM", "TRADE ME LTD"]
SUBS = [
    ("NETFLIX COM", 25.99, 30),
    ("SPOTIFY NZ", 19.99, 30),
    ("DISNEY PLUS", 15.99, 30),
    ("APPLE COM BILL", 4.99, 30),
    ("MICROSOFT 365", 12.00, 30),
    ("SKY TV NZ", 55.00, 30),
]


def _row(when: date, memo: str, amount: float, balance: float) -> list[str]:
    """The classic sixteen-column Kiwibank shape, credit and debit split out."""
    credit = f"{amount:.2f}" if amount > 0 else ""
    debit = f"{abs(amount):.2f}" if amount < 0 else ""
    return [
        ACCOUNT,
        when.strftime("%d-%m-%Y"),
        memo,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        credit,
        debit,
        f"{amount:.2f}",
        f"{balance:.2f}",
    ]


def generate(months: int = 14, seed: int = 20260905) -> list[list[str]]:
    rnd = random.Random(seed)
    today = date.today()
    start = (today - timedelta(days=months * 30)).replace(day=1)

    rows: list[tuple[date, str, float]] = []

    # --- Fortnightly pay ---------------------------------------------------
    pay_day = start
    while pay_day.weekday() != 3:  # land on a Thursday
        pay_day += timedelta(days=1)
    while pay_day < today:
        net = round(rnd.gauss(2380, 45), 2)
        rows.append((pay_day, "SALARY MANAWATU ENGINEERING LTD", net))
        pay_day += timedelta(days=14)

    # --- Working for Families: present, but well below entitlement ---------
    # Modelled as a household whose IRD details are stale - the exact pattern
    # the entitlements module is built to surface.
    wff_day = start
    while wff_day < today:
        rows.append((wff_day, "INLAND REVENUE WFFTC", 92.00))
        wff_day += timedelta(days=7)

    # --- Mortgage, fortnightly --------------------------------------------
    m_day = start + timedelta(days=2)
    while m_day < today:
        rows.append((m_day, "LOAN PAYMENT LN 02 HOME", -612.40))
        m_day += timedelta(days=14)

    # --- Monthly commitments ----------------------------------------------
    cur = start
    while cur < today:
        month_start = cur.replace(day=1)

        # `month_start` is bound as a default rather than captured, so the
        # closure cannot drift onto a later month if this is ever called
        # lazily. It is a latent bug today only because every call happens
        # inside the same iteration.
        def on(day: int, _month_start: date = month_start) -> date:
            return _month_start + timedelta(days=day - 1)

        rows.append((on(5), "MERCURY NZ LTD", -round(rnd.gauss(268, 40), 2)))
        rows.append((on(8), "SPARK NEW ZEALAND", -119.99))
        rows.append((on(12), "AA INSURANCE CAR HOME", -214.50))
        rows.append((on(15), "PNCC RATES INSTALMENT", -186.00))
        rows.append((on(20), "SOUTHERN CROSS HEALTH", -142.00))
        rows.append((on(28), "MONTHLY AC FEE", -5.00))

        for name, amount, _ in SUBS:
            rows.append((on(rnd.randint(2, 26)), name, -amount))

        # Groceries, weekly-ish
        for w in range(4):
            rows.append(
                (
                    on(3 + w * 7),
                    rnd.choice(GROCERY_STORES),
                    -round(rnd.gauss(292, 55), 2),
                )
            )

        # Fuel
        for w in range(3):
            rows.append((on(4 + w * 9), rnd.choice(FUEL_STOPS), -round(rnd.gauss(96, 18), 2)))

        # Takeaways and cafes - the discretionary drift
        for _ in range(rnd.randint(6, 11)):
            rows.append(
                (
                    on(rnd.randint(1, 28)),
                    rnd.choice(TAKEAWAYS),
                    -round(rnd.gauss(38, 14), 2),
                )
            )
        for _ in range(rnd.randint(8, 16)):
            rows.append((on(rnd.randint(1, 28)), rnd.choice(CAFES), -round(rnd.gauss(9, 3), 2)))

        # General shopping
        for _ in range(rnd.randint(3, 7)):
            rows.append((on(rnd.randint(1, 28)), rnd.choice(SHOPS), -round(rnd.gauss(64, 40), 2)))

        # Small everyday spending - the leak the report annualises
        for _ in range(rnd.randint(10, 20)):
            rows.append(
                (
                    on(rnd.randint(1, 28)),
                    rnd.choice(["FOUR SQUARE ASHHURST", "Z ASHHURST", "DAIRY ASHHURST"]),
                    -round(rnd.uniform(3, 18), 2),
                )
            )

        # Lotto - small, regular, and worth a conversation
        if rnd.random() < 0.7:
            rows.append((on(rnd.randint(1, 28)), "MYLOTTO NZ", -round(rnd.choice([12, 15, 20]), 2)))

        cur = (month_start + timedelta(days=32)).replace(day=1)

    # --- Annual and lumpy bills that ambush the budget ---------------------
    for offset, memo, amount in [
        (40, "NZTA REGO 12 MONTH", -113.94),
        (95, "VTNZ WARRANT OF FITNESS", -76.00),
        (150, "AMI CONTENTS INSURANCE ANNUAL", -684.00),
        (210, "ASHHURST SCHOOL STATIONERY", -187.50),
        (250, "CAR SERVICE MANAWATU AUTOMOTIVE", -1240.00),
        (300, "TOYWORLD CHRISTMAS", -640.00),
        (330, "DENTAL PALMERSTON NORTH", -420.00),
    ]:
        when = start + timedelta(days=offset)
        if when < today:
            rows.append((when, memo, amount))

    # --- Occasional weekend work: irregular, never budgeted ----------------
    for offset in range(20, months * 30, 45):
        when = start + timedelta(days=offset)
        if when < today and rnd.random() < 0.6:
            rows.append((when, "WAGES WEEKEND CONTRACT", round(rnd.gauss(340, 60), 2)))

    # --- Assemble with a running balance -----------------------------------
    # The monthly loop fills whole calendar months, so the current month runs
    # past today. A real bank export never contains future transactions, and
    # leaving them in makes the part-finished month look complete.
    rows = [r for r in rows if r[0] < today]
    rows.sort(key=lambda r: r[0])
    balance = 2150.00
    out: list[list[str]] = []
    for when, memo, amount in rows:
        balance += amount
        out.append(_row(when, memo, amount, balance))
    return out


def write(path: Path, months: int = 14) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = generate(months=months)
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return path
