"""
Account roles, and the cash figure that depends on them.

This is where the tool was most wrong on real data. A household exported six
Kiwibank accounts, two of which were loans, and `cash_position()` summed every
balance - reporting their cash on hand as MINUS $168,636 when it was actually
about $2,861. The front page said the opposite of the truth.

These tests pin the fix and the inference that drives it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import accounts, cache, categorise, config, db
from wyrmhoard.analysis import cashflow


def add(account: str, day: int, amount: float, balance: float, memo="THING", counterparty=None):
    """One transaction, straight into the ledger."""
    date = f"2025-01-{day:02d}"
    db.insert_transactions(
        [
            {
                "fingerprint": db.fingerprint(account, date, memo, amount, balance),
                "account": account,
                "date": date,
                "memo": memo,
                "match_text": memo,
                "counterparty": counterparty,
                "amount": amount,
                "balance": balance,
            }
        ],
        "test.csv",
    )
    cache.clear_all()


# The project's synthetic account numbers, allowlisted in
# scripts/check_no_financial_data.py. Never use a real bank's branch prefix in
# a fixture: the guard blocked a first draft of this file for exactly that,
# because the prefix alone identifies which bank a household uses.
EVERYDAY = "38-9014-0123456-00"
SAVINGS = "38-9014-0000000-01"
LOAN = "38-9014-0000000-05"
NOT_OURS = "99-9999-9999999-99"


def build_household():
    """An everyday account, a savings pot, and a mortgage."""
    for day in range(1, 13):
        add(EVERYDAY, day, -20.0, 1000.0 - day)
    add(SAVINGS, 1, 100.0, 2000.0)
    for day in range(1, 6):
        add(LOAN, day, 800.0, -187000.0 + day)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------
def test_a_persistently_negative_balance_is_a_loan():
    build_household()
    roles = accounts.infer_roles()

    assert roles[LOAN]["role"] == "liability"
    assert roles[LOAN]["confidence"] == "high"
    # The evidence must be legible - somebody has to be able to disagree.
    assert "negative" in roles[LOAN]["evidence"].lower()


def test_the_busiest_account_is_the_everyday_one():
    build_household()
    roles = accounts.infer_roles()

    assert roles[EVERYDAY]["role"] == "everyday"
    assert roles[SAVINGS]["role"] == "savings"


def test_a_quiet_positive_account_is_never_guessed_as_everyday():
    """
    The default for an ambiguous account is savings, not everyday. Guessing
    'everyday' would silently inflate reported spending; guessing 'savings'
    understates it visibly, which is the safer way to be wrong.
    """
    add(SAVINGS, 1, 50.0, 500.0)
    assert accounts.infer_roles()[SAVINGS]["role"] in {"savings", "everyday"}
    # With only one account it may be called everyday; with a busier one present
    # it must not be.
    for day in range(1, 20):
        add(EVERYDAY, day, -10.0, 900.0)
    assert accounts.infer_roles()[SAVINGS]["role"] == "savings"


def test_confirmation_beats_inference():
    build_household()
    assert accounts.roles()[LOAN]["role"] == "liability"

    db.set_account_role(LOAN, "savings")
    cache.clear_all()

    assert accounts.roles()[LOAN]["role"] == "savings"
    assert accounts.roles()[LOAN]["confidence"] == "confirmed"


# ---------------------------------------------------------------------------
# The cash figure
# ---------------------------------------------------------------------------
def test_cash_excludes_loan_accounts():
    """The regression. Cash must be the positive accounts only."""
    build_household()
    cash = cashflow.cash_position()

    assert cash["total"] > 0, "loan balances leaked back into cash"
    assert LOAN in cash["excluded_accounts"]
    assert LOAN not in cash["by_account"]


def test_debt_and_net_worth_are_reported_separately():
    build_household()

    debt = cashflow.debt_position()
    assert debt["total"] == pytest.approx(186995.0, abs=10)

    nw = cashflow.net_worth()
    assert nw["available"] is True
    assert nw["net_worth"] == pytest.approx(nw["assets"] - nw["liabilities"], abs=0.01)
    assert nw["net_worth"] < 0


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------
def test_counterparty_in_our_own_accounts_is_a_transfer(tmp_path, monkeypatch):
    """
    Money moved between a household's own accounts is neither income nor
    spending. With six accounts, missing this double-counts almost everything.
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "household.yml").write_text(
        yaml.safe_dump(
            {"accounts": [{"label": "Everyday", "number": EVERYDAY, "kind": "transaction"}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    config.reload()

    assert categorise.is_internal_transfer(EVERYDAY) is True
    # Punctuation differs between bank screens; both forms must match.
    assert categorise.is_internal_transfer(EVERYDAY.replace("-", "")) is True
    assert categorise.is_internal_transfer(NOT_OURS) is False
    assert categorise.is_internal_transfer(None) is False


def test_internal_transfers_are_excluded_from_income_and_spending():
    build_household()
    add(EVERYDAY, 20, -500.0, 500.0, memo="MOVING MONEY", counterparty=SAVINGS)
    categorise.recategorise_all()

    df = cashflow.frame()
    moved = df[df["memo"] == "MOVING MONEY"].iloc[0]
    assert moved["grp"] == "transfer"
    assert not moved["is_spend"]
    assert not moved["is_income"]


# ---------------------------------------------------------------------------
# The browser tests must never touch real data
# ---------------------------------------------------------------------------
def test_e2e_compose_override_isolates_the_data_directory():
    """
    Regression, and an expensive one: the e2e suite drove the live API, which
    wrote uploads into the real ./data directory and imported them, mixing
    synthetic transactions into a household's actual ledger.

    If someone removes this isolation, this fails.
    """
    # Runs both on the host (CI) and inside the api container, where only the
    # package is mounted and the repo root appears at /repo.
    candidates = [
        Path(__file__).resolve().parents[2] / "docker-compose.e2e.yml",
        Path("/repo/docker-compose.e2e.yml"),
        Path.cwd() / "docker-compose.e2e.yml",
    ]
    override = next((p for p in candidates if p.exists()), None)
    if override is None:
        pytest.skip("repo root not reachable from here; this guard runs in CI")

    # Compose defines custom tags (`!reset`) that SafeLoader refuses. Ignore
    # unknown local tags rather than fall back to string matching, so the
    # assertions below stay structural.
    class ComposeLoader(yaml.SafeLoader):
        pass

    ComposeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)

    spec = yaml.load(override.read_text(encoding="utf-8"), Loader=ComposeLoader)
    mounts = spec["services"]["api"]["volumes"]

    assert not any(
        m.startswith("./data:") for m in mounts
    ), "the e2e stack mounts the real ./data directory"
    assert any(
        m.startswith("e2e-data:") for m in mounts
    ), "the e2e stack must use a throwaway volume for /data"
