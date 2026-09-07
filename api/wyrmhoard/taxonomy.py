"""
The household's grouping vocabulary - served, not assumed.

Wyrmhoard used to have four group names welded into it: `essential`,
`commitment`, `sinking`, `discretionary`. Anything summing them was asserting
that groceries keep a family safe and takeaways do not, which is a judgement a
household makes, not arithmetic.

So the vocabulary moved into `config/rules.yml` as data. A household can
rename every group, delete one, or invent a different set entirely, and
nothing here breaks - because nothing here knows what any of the names mean.

The one thing that is *not* a preference is `kind`. It says whether a group is
money leaving, money arriving, or movement between the household's own
accounts. That is structural: the categoriser needs it so that a refund from a
supermarket counts as income rather than as negative groceries. `kind` is a
fact about direction; the group name and its membership are opinion.

Consumers read `served()` - over `GET /taxonomy`, or attached to the MCP
overview - and decide for themselves what to do with it. A budgeting consumer
might add up everything this household called essential; another might not
care. Both read the same declaration, so they cannot disagree about what this
household actually said.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import config

#: The only kinds understood. A group declaring anything else is reported as a
#: problem rather than guessed at, because guessing the direction of money is
#: how a refund becomes negative spending.
KINDS = ("spend", "income", "transfer", "unknown")

#: What a category gets when nothing has been declared for it. Never removed
#: from the vocabulary, because the categoriser emits it for every memo it
#: could not match, and a ledger with no vocabulary at all still has unknowns.
UNKNOWN = "unknown"

#: Used only when `rules.yml` declares no `groups:` block - an older file, or
#: one written before the block existed. Matches what the shipped rules.yml
#: declares, so an un-migrated household sees no change.
FALLBACK: dict[str, dict[str, str]] = {
    "essential": {"label": "Essentials", "kind": "spend"},
    "commitment": {"label": "Commitments", "kind": "spend"},
    "sinking": {"label": "Lumpy bills", "kind": "spend"},
    "discretionary": {"label": "Choices", "kind": "spend"},
    "income": {"label": "Income", "kind": "income"},
    "transfer": {"label": "Transfers", "kind": "transfer"},
    UNKNOWN: {"label": "Unrecognised", "kind": "unknown"},
}


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    kind: str


@lru_cache(maxsize=1)
def groups() -> dict[str, Group]:
    """
    The declared vocabulary, keyed by group name.

    Cached because `kind_of` is asked once per transaction during a full
    recategorisation. `config.reload()` clears it.
    """
    declared = config.rules().get("groups")
    if not isinstance(declared, dict) or not declared:
        declared = FALLBACK

    out: dict[str, Group] = {}
    for key, body in declared.items():
        name = str(key)
        body = body if isinstance(body, dict) else {}
        kind = str(body.get("kind", "unknown")).strip().lower()
        out[name] = Group(
            key=name,
            label=str(body.get("label") or name.replace("_", " ").title()),
            kind=kind if kind in KINDS else "unknown",
        )

    # Guaranteed to exist whatever the household declared, so that a memo
    # nothing matched still lands somewhere nameable.
    out.setdefault(UNKNOWN, Group(UNKNOWN, "Unrecognised", "unknown"))
    return out


def kind_of(group: str | None) -> str:
    """
    Whether this group is money out, money in, or neither.

    An undeclared group is `unknown` rather than an error. Being wrong about a
    household's own vocabulary should show up in `problems()`, where somebody
    can read it, instead of stopping an import.
    """
    found = groups().get(group or UNKNOWN)
    return found.kind if found else "unknown"


def is_spend(group: str | None) -> bool:
    """True for groups the household declared as money leaving."""
    return kind_of(group) == "spend"


def income_group() -> str:
    """
    Where a positive amount belongs when the rule that matched said spending.

    A refund from a supermarket matches the groceries rule and then arrives as
    money in. The categoriser has to file it somewhere, and it must not be a
    spending group or the household's grocery total goes down by the refund
    twice over. The first declared income group wins; a household that declared
    none gets `unknown`, which is visible and countable rather than quietly
    wrong.
    """
    found = keys_of_kind("income")
    return found[0] if found else UNKNOWN


def keys_of_kind(*kinds: str) -> tuple[str, ...]:
    """Group names of the given kinds, in declaration order."""
    wanted = set(kinds)
    return tuple(g.key for g in groups().values() if g.kind in wanted)


def spending_groups() -> tuple[str, ...]:
    """
    The groups a spending breakdown should report.

    Includes `unknown`: spending nothing has categorised yet is still spending,
    and hiding it would make the totals look tidier than the data is.
    """
    return keys_of_kind("spend", "unknown")


def categories_by_group() -> dict[str, list[str]]:
    """Which categories the household filed under each group."""
    out: dict[str, list[str]] = {key: [] for key in groups()}
    for key, body in (config.rules().get("categories") or {}).items():
        grp = str((body or {}).get("group", UNKNOWN))
        out.setdefault(grp, []).append(str(key))
    for names in out.values():
        names.sort()
    return out


def problems() -> list[str]:
    """
    Anything wrong with the declaration, in words somebody can act on.

    Surfaced rather than raised. A household editing their own rules file
    should get a readable complaint from the tool, not a stack trace on the
    next import.
    """
    found: list[str] = []
    declared = config.rules().get("groups")
    if declared is not None and not isinstance(declared, dict):
        return ["`groups:` in rules.yml must be a mapping of name to {label, kind}."]

    for key, body in (declared or {}).items():
        kind = str((body or {}).get("kind", "")).strip().lower()
        if kind not in KINDS:
            found.append(
                f"Group `{key}` declares kind `{kind or 'none'}`, which is not one of "
                f"{', '.join(KINDS)}. It will be treated as unknown."
            )

    known = set(groups())
    used = {
        str((b or {}).get("group", UNKNOWN))
        for b in (config.rules().get("categories") or {}).values()
    }
    for grp in sorted(used - known):
        found.append(
            f"Category group `{grp}` is used in rules.yml but not declared under `groups:`. "
            "Spending in it will be reported as unrecognised."
        )
    return found


def served() -> dict[str, Any]:
    """
    The whole vocabulary, as a consumer receives it.

    `source` is named so a reader knows which file to edit, and the categories
    are attached so one call is enough to render a breakdown - a consumer
    should never have to join this against another endpoint to find out what
    the household put in `essential`.
    """
    members = categories_by_group()
    return {
        "source": "config/rules.yml",
        "kinds": list(KINDS),
        "groups": [
            {
                "key": g.key,
                "label": g.label,
                "kind": g.kind,
                "categories": members.get(g.key, []),
            }
            for g in groups().values()
        ],
        "problems": problems(),
    }
