"""
Working out what each account actually is.

This matters more than it sounds. A household with a mortgage account in the
export has a large negative balance sitting in the ledger, and summing every
balance to get "cash on hand" produces a six-figure negative number on the
front page. That is not a rounding error - it is the headline statistic being
the opposite of the truth.

Roles are *inferred* from evidence the ledger already contains, then offered
to the household for confirmation. Nobody has to fill in a form before the
numbers work, and nobody is stuck with a wrong guess. The order of authority
is deliberate:

    a human's confirmation  >  household.yml  >  inference  >  a safe default

The safe default is `savings`, not `everyday`, because misclassifying a real
everyday account as savings understates spending in a visible way, while the
reverse inflates it silently.
"""

from __future__ import annotations

from typing import Any

from . import cache, config, db

# Assets count toward cash; liabilities count toward debt; `ignore` is for
# accounts a household does not want in their picture at all.
ROLES = ("everyday", "savings", "liability", "ignore")
ASSET_ROLES = frozenset({"everyday", "savings"})

# household.yml speaks in account "kind"; map it onto roles.
KIND_TO_ROLE = {
    "transaction": "everyday",
    "everyday": "everyday",
    "savings": "savings",
    "mortgage": "liability",
    "loan": "liability",
    "credit": "liability",
}

# A balance this consistently negative is a loan, not an overdrawn cheque
# account having a bad month.
_NEGATIVE_SHARE_FOR_LIABILITY = 0.9


def _config_roles() -> dict[str, str]:
    """Roles declared in household.yml, keyed by account number where given."""
    out: dict[str, str] = {}
    for acct in config.household().raw.get("accounts", []) or []:
        number = str(acct.get("number") or "").strip()
        role = KIND_TO_ROLE.get(str(acct.get("kind") or "").lower())
        if number and role:
            out[number] = role
    return out


@cache.by_ledger
def infer_roles() -> dict[str, dict[str, Any]]:
    """
    Guess each account's role, and say why.

    The evidence string is part of the output on purpose: a household should
    be able to see *why* the tool decided their offset account is a mortgage,
    and disagree with it in one click.
    """
    from .analysis.cashflow import frame

    df = frame()
    if df.empty:
        return {}

    inferred: dict[str, dict[str, Any]] = {}
    counts = df.groupby("account").size().to_dict()

    for account, chunk in df.groupby("account"):
        with_balance = chunk[chunk["balance"].notna()].sort_values("date")
        last_balance = float(with_balance.iloc[-1]["balance"]) if len(with_balance) else None
        negative_share = float((with_balance["balance"] < 0).mean()) if len(with_balance) else 0.0
        n = int(counts.get(account, 0))

        if negative_share >= _NEGATIVE_SHARE_FOR_LIABILITY and last_balance is not None:
            role = "liability"
            confidence = "high"
            evidence = (
                f"Balance was negative on {negative_share:.0%} of {len(with_balance)} "
                f"records, currently {last_balance:,.2f}. That is a loan, not cash."
            )
        else:
            role = "savings"
            confidence = "low"
            if last_balance is not None:
                evidence = f"{n} transactions, balance currently {last_balance:,.2f}."
            else:
                evidence = f"{n} transactions, no balance column in the export."

        inferred[account] = {
            "account": account,
            "role": role,
            "confidence": confidence,
            "evidence": evidence,
            "transactions": n,
            "last_balance": round(last_balance, 2) if last_balance is not None else None,
        }

    # The busiest non-loan account is where daily life happens. Doing this
    # after the loop means it is decided against all accounts, not the first
    # one that happened to look plausible.
    candidates = {a: v["transactions"] for a, v in inferred.items() if v["role"] != "liability"}
    if candidates:
        busiest = max(candidates, key=lambda a: candidates[a])
        inferred[busiest]["role"] = "everyday"
        inferred[busiest]["confidence"] = "medium"
        inferred[busiest]["evidence"] = (
            f"Busiest account with {candidates[busiest]} transactions, so this is "
            "where day-to-day spending happens."
        )

    return inferred


def roles() -> dict[str, dict[str, Any]]:
    """The merged, authoritative view. Confirmation beats config beats guess."""
    merged = infer_roles()
    for account, role in _config_roles().items():
        entry = merged.setdefault(
            account,
            {"account": account, "transactions": 0, "last_balance": None},
        )
        entry.update({"role": role, "confidence": "declared", "evidence": "Set in household.yml."})

    for account, row in db.account_roles().items():
        entry = merged.setdefault(
            account,
            {"account": account, "transactions": 0, "last_balance": None},
        )
        entry.update(
            {
                "role": row["role"],
                "confidence": "confirmed",
                "evidence": "Confirmed by you.",
                "label": row.get("label"),
            }
        )
    return merged


def accounts_with_role(*wanted: str) -> set[str]:
    return {a for a, v in roles().items() if v.get("role") in wanted}


def asset_accounts() -> set[str]:
    return {a for a, v in roles().items() if v.get("role") in ASSET_ROLES}


def liability_accounts() -> set[str]:
    return accounts_with_role("liability")


# Enough repeat transfers that this is a standing arrangement rather than
# somebody paying you back for a coffee.
_MIN_TRANSFERS_TO_SUSPECT = 6


@cache.by_ledger
def likely_missing_accounts() -> list[dict[str, Any]]:
    """
    External accounts that behave like part of this household.

    Money arriving from the same outside account, over and over, is usually a
    partner moving their income across, or a second account the household
    holds but has not exported yet.

    This matters more than it sounds. The tool reports what it can see and
    calls that the household's income. If a partner's account holds the family
    tax credits and only transfers some across, the tool will confidently
    report those credits as missing - which is worse than saying nothing,
    because somebody could act on it and ring IRD about money they already
    receive.

    So: notice the gap, and say so, rather than drawing a confident conclusion
    over a hole in the data.
    """
    from .analysis.cashflow import frame

    df = frame()
    if df.empty or "counterparty" not in df.columns:
        return []

    known = own_accounts()
    incoming = df[(df["amount"] > 0) & df["counterparty"].notna()]

    out: list[dict[str, Any]] = []
    for counterparty, chunk in incoming.groupby("counterparty"):
        account = str(counterparty).strip()
        if not account or account in known or account.replace("-", "") in known:
            continue
        if len(chunk) < _MIN_TRANSFERS_TO_SUSPECT:
            continue

        # A salary or a benefit comes from an organisation, not a household
        # account, so those are not gaps in our view of the household.
        text = " ".join(str(m).upper() for m in chunk["memo"].head(20))
        if any(word in text for word in ("SALARY", "WAGE", "PAYROLL", "INLAND REVENUE")):
            continue

        out.append(
            {
                "account": account,
                "transfers": int(len(chunk)),
                "total": round(float(chunk["amount"].sum()), 2),
                "first_seen": chunk["date"].min().date().isoformat(),
                "last_seen": chunk["date"].max().date().isoformat(),
                "example": str(chunk.iloc[-1]["memo"])[:80],
            }
        )

    return sorted(out, key=lambda a: a["transfers"], reverse=True)


def view_is_incomplete() -> bool:
    """True when money clearly arrives from a household account we cannot see."""
    return bool(likely_missing_accounts())


def own_accounts() -> set[str]:
    """
    Every account belonging to this household, in every form the bank writes
    them. Used to recognise internal transfers, where the counterparty is one
    of these rather than a shop or an employer.
    """
    known = set(roles())
    for acct in config.household().raw.get("accounts", []) or []:
        number = str(acct.get("number") or "").strip()
        if number:
            known.add(number)
    # Banks are inconsistent about punctuation between screens and exports.
    return known | {a.replace("-", "") for a in known}
