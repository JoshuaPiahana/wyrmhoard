"""
Turning bank memos into categories.

Bank memos are hostile input: "POS W/D PAK'nSAVE PALM STH 4829", "D/D
MERCURY NZ LTD", "AP TO 38-9014-0..". They are inconsistently punctuated,
truncated at odd lengths, and full of reference numbers.

So we normalise aggressively before matching - strip punctuation, collapse
whitespace, uppercase - which lets one rule ("PAK N SAVE") catch every
spelling the bank might emit. The cost is that rules cannot rely on
punctuation; the benefit is that the ruleset stays short enough to maintain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import config, db

_PUNCT_RE = re.compile(r"[^A-Z0-9 ]+")
_WS_RE = re.compile(r"\s+")
_SQUASH_RE = re.compile(r"[^A-Z0-9]+")

# Below this length, a squashed pattern starts matching across word
# boundaries - "AMI" would hit "CERAMIC", "PET" would hit "CARPET". Short
# patterns stay on the spaced form only, where word gaps still protect them.
_MIN_SQUASH_LEN = 6

# Reference numbers and card fragments carry no signal and cause false
# matches (a memo ending "...VET 2024" should not match a regex for VET).
_NOISE_RE = re.compile(
    r"\b(POS W D|POS WD|EFTPOS|VISA PURCHASE|DEBIT CARD|CARD \d+|"
    r"D D|A P|AP TO|DIRECT DEBIT|AUTOMATIC PAYMENT|BILL PAYMENT|"
    r"PAYMENT TO|PAYMENT FROM|REF \w+|\d{6,})\b"
)


def normalise(text: str) -> str:
    up = (text or "").upper()
    up = _PUNCT_RE.sub(" ", up)
    up = _WS_RE.sub(" ", up).strip()
    return up


def strip_noise(text: str) -> str:
    """Normalised text with bank plumbing removed - used for display too."""
    cleaned = _NOISE_RE.sub(" ", normalise(text))
    return _WS_RE.sub(" ", cleaned).strip()


def squash(text: str) -> str:
    """
    Everything but letters and digits removed.

    Needed because stripping punctuation to spaces does not make spellings
    agree: "PAK'nSAVE" becomes "PAK NSAVE", which matches neither the rule
    "PAK N SAVE" nor "PAKNSAVE". Squashing both sides makes all three spellings
    the same string.
    """
    return _SQUASH_RE.sub("", (text or "").upper())


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    group: str
    priority: int
    flag: bool
    # "in", "out", or None for either. The same word means opposite things
    # depending on which way the money went: "gift" leaving the account is
    # something bought, "gift" arriving is money from family. Without this,
    # one rule has to serve both and gets one of them wrong.
    direction: str | None
    literals: tuple[str, ...]
    squashed: tuple[str, ...]
    regexes: tuple[re.Pattern[str], ...]

    def applies_to(self, amount: float) -> bool:
        if self.direction == "in":
            return amount > 0
        if self.direction == "out":
            return amount < 0
        return True

    def matches(self, haystack: str, squashed_haystack: str) -> bool:
        if any(lit in haystack for lit in self.literals):
            return True
        if any(lit in squashed_haystack for lit in self.squashed):
            return True
        return any(rx.search(haystack) for rx in self.regexes)


@lru_cache(maxsize=1)
def compiled_rules() -> list[Rule]:
    raw = config.rules()
    out: list[Rule] = []
    for key, cat in (raw.get("categories") or {}).items():
        literals: list[str] = []
        regexes: list[re.Pattern[str]] = []
        for pattern in cat.get("match", []) or []:
            if isinstance(pattern, str) and pattern.startswith("re:"):
                try:
                    regexes.append(re.compile(pattern[3:], re.IGNORECASE))
                except re.error:
                    continue
            else:
                literals.append(normalise(str(pattern)))
        clean = tuple(p for p in literals if p)
        out.append(
            Rule(
                key=key,
                label=cat.get("label", key),
                group=cat.get("group", "unknown"),
                priority=int(cat.get("priority", 50)),
                flag=bool(cat.get("flag", False)),
                direction=cat.get("direction"),
                literals=clean,
                squashed=tuple(s for s in (squash(p) for p in clean) if len(s) >= _MIN_SQUASH_LEN),
                regexes=tuple(regexes),
            )
        )
    out.sort(key=lambda r: (r.priority, r.key))
    return out


def rule_index() -> dict[str, Rule]:
    return {r.key: r for r in compiled_rules()}


def categorise_one(memo: str, amount: float = 0.0) -> tuple[str, str, str]:
    """Returns (category_key, group, decided_by)."""
    haystack = strip_noise(memo)
    squashed_haystack = squash(haystack)
    for rule in compiled_rules():
        if not rule.applies_to(amount):
            continue
        if rule.matches(haystack, squashed_haystack):
            group = rule.group
            # A rule can be right about the merchant but wrong about direction:
            # a refund from a shop is income, not spending.
            if group in {"essential", "discretionary", "sinking", "commitment"} and amount > 0:
                group = "income"
            return rule.key, group, "rule"
    return "uncategorised", "unknown", "unmatched"


def is_internal_transfer(counterparty: str | None) -> bool:
    """
    True when the other side of this transaction is one of the household's own
    accounts.

    This is the one signal that is certain. Everything else in this module is
    pattern-matching against free text a bank wrote for a human, but a
    counterparty account number either belongs to this household or it does
    not. It matters most for households with several accounts: money shuffled
    between six of their own pots would otherwise be counted as both income
    and spending, inflating the whole picture.
    """
    if not counterparty:
        return False
    from .accounts import own_accounts

    cp = counterparty.strip()
    known = own_accounts()
    return cp in known or cp.replace("-", "") in known


def recategorise_all() -> dict[str, Any]:
    """
    Re-run categorisation across the whole ledger.

    Safe to run any time: manual overrides always win, so a human correction
    is never lost to a later rule change.
    """
    config.reload()
    compiled_rules.cache_clear()

    overrides = db.overrides()
    idx = rule_index()
    updates: list[tuple[str, str, str, str]] = []

    for tx in db.all_transactions():
        fp = tx["fingerprint"]
        if fp in overrides:
            key = overrides[fp]
            rule = idx.get(key)
            group = rule.group if rule else "unknown"
            if (
                group in {"essential", "discretionary", "sinking", "commitment"}
                and tx["amount"] > 0
            ):
                group = "income"
            updates.append((key, group, "manual", fp))
            continue
        # A counterparty that is one of our own accounts settles it outright,
        # ahead of any text rule.
        if is_internal_transfer(tx.get("counterparty")):
            updates.append(("transfer", "transfer", "counterparty", fp))
            continue

        # Match against every text column the bank gave us, not just the one
        # shown on screen - payees often appear only in Particulars.
        key, group, by = categorise_one(tx.get("match_text") or tx["memo"], tx["amount"])
        updates.append((key, group, by, fp))

    with db.connect() as conn:
        conn.executemany(
            "UPDATE transactions SET category=?, grp=?, categorised_by=? WHERE fingerprint=?",
            updates,
        )

    return coverage()


def coverage() -> dict[str, Any]:
    """
    How much of the household's spending we actually understand.

    This gates the rest of the tool. Below ~90% coverage the category charts
    are decorative rather than informative, and the dashboard says so.
    """
    with db.connect() as conn:
        total_out = conn.execute(
            "SELECT COALESCE(SUM(ABS(amount)),0) FROM transactions "
            "WHERE amount < 0 AND grp != 'transfer'"
        ).fetchone()[0]
        unknown_out = conn.execute(
            "SELECT COALESCE(SUM(ABS(amount)),0) FROM transactions "
            "WHERE amount < 0 AND (grp = 'unknown' OR category = 'uncategorised')"
        ).fetchone()[0]
        n_unknown = conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE grp = 'unknown' OR category = 'uncategorised'"
        ).fetchone()[0]
        n_total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    pct = 100.0 * (1 - (unknown_out / total_out)) if total_out else 0.0
    return {
        "categorised_pct": round(pct, 1),
        "uncategorised_spend": round(unknown_out, 2),
        "total_spend": round(total_out, 2),
        "uncategorised_count": n_unknown,
        "transaction_count": n_total,
        "trustworthy": pct >= 90.0,
    }


def is_unknown(tx: dict[str, Any]) -> bool:
    """
    True when no rule has claimed this transaction.

    Both columns are checked because they can disagree: an override naming a
    category we no longer have a rule for leaves the group `unknown` while the
    category reads as something real.
    """
    return tx.get("category") in (None, "uncategorised") or tx.get("grp") == "unknown"


def top_uncategorised(limit: int = 25) -> list[dict[str, Any]]:
    """
    The fastest path to good coverage: fix the biggest unknowns first.

    Grouped by cleaned memo so one decision can cover thirty transactions.
    """
    rows = [tx for tx in db.all_transactions() if is_unknown(tx) and tx["amount"] < 0]
    buckets: dict[str, dict[str, Any]] = {}
    for tx in rows:
        key = strip_noise(tx["memo"])[:40] or "(blank)"
        b = buckets.setdefault(
            key, {"memo": key, "count": 0, "total": 0.0, "example": tx["memo"], "fingerprints": []}
        )
        b["count"] += 1
        b["total"] += abs(tx["amount"])
        b["fingerprints"].append(tx["fingerprint"])
    out = sorted(buckets.values(), key=lambda b: b["total"], reverse=True)[:limit]
    for b in out:
        b["total"] = round(b["total"], 2)
    return out


# ---------------------------------------------------------------------------
# Teaching
# ---------------------------------------------------------------------------
# A shorter literal matches as a substring inside unrelated memos, and one bad
# pattern quietly miscounts every month after it. Three is what rules.yml
# itself already risks ("IRD", "MSD"); below that, use a regex, where \b puts
# the word boundaries back explicitly.
_MIN_PATTERN_CHARS = 3

# Long enough for any merchant, short enough to reject a whole memo pasted in
# wholesale - those carry reference numbers that differ between transactions,
# so a rule built from one matches exactly the transaction it came from.
_MAX_PATTERN_CHARS = 80

_LEARNED_HEADER = """\
# ===========================================================================
# learned.yml - patterns taught to this household's copy of Wyrmhoard.
#
# Merged over config/rules.yml on every categorisation run, so a pattern here
# takes effect without touching the public ruleset. Only `match` is read for a
# category rules.yml already defines, which means nothing learned here can
# change a category's group or label - and so nothing learned here can quietly
# move spending between essential and discretionary.
#
# Safe to edit by hand: it is plain YAML, re-read on every run. Comments other
# than this header do not survive the next taught rule, so keep notes of your
# own somewhere they will last.
#
# This file is gitignored and must stay that way. rules.yml is public and
# generic; this one fills up with a household's actual merchants - their
# corner shop, their piano teacher, their doctor - which is a picture of how a
# family lives.
# ===========================================================================
"""


def learned_path() -> Path:
    """Resolved at call time, so a redirected config directory is honoured."""
    return config.CONFIG_DIR / "learned.yml"


def _check_pattern(pattern: str) -> None:
    """Refuse a pattern that would cost more in false matches than it fixes."""
    if not pattern:
        raise ValueError("A pattern is required: a distinctive fragment of the merchant name.")
    if len(pattern) > _MAX_PATTERN_CHARS:
        raise ValueError(
            f"Pattern is {len(pattern)} characters, over the {_MAX_PATTERN_CHARS} allowed. "
            "Use the part of the memo that names the merchant, not the whole line - the "
            "reference numbers around it differ between transactions."
        )
    if pattern.startswith("re:"):
        try:
            re.compile(pattern[3:], re.IGNORECASE)
        except re.error as exc:
            # compiled_rules() drops a bad regex silently, which would look
            # like a rule that simply never matches anything.
            raise ValueError(f"'{pattern}' is not a valid regular expression: {exc}") from exc
        return
    if len(squash(pattern)) < _MIN_PATTERN_CHARS:
        raise ValueError(
            f"'{pattern}' is too short to match on safely - it would appear inside "
            "unrelated memos. Use a longer fragment, or a regex with word boundaries: "
            rf'"re:\b{squash(pattern)}\b".'
        )


def _append_pattern(category: str, pattern: str) -> bool:
    """Write the pattern into learned.yml. False if it was already there."""
    path = learned_path()
    doc: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}

    patterns = doc.setdefault("categories", {}).setdefault(category, {}).setdefault("match", [])
    # Compared on the normalised form because that is what matching uses:
    # "PAK'nSAVE" and "PAK N SAVE" are one rule, not two.
    if normalise(pattern) in {normalise(str(p)) for p in patterns}:
        return False

    patterns.append(pattern)
    path.parent.mkdir(parents=True, exist_ok=True)
    # width is set high so a long pattern is never folded across lines; the
    # cap above keeps the result inside the project's 100-column yamllint.
    body = yaml.safe_dump(
        doc, sort_keys=True, default_flow_style=False, allow_unicode=True, width=200
    )
    path.write_text(_LEARNED_HEADER + body, encoding="utf-8")
    return True


def learn(pattern: str, category: str) -> dict[str, Any]:
    """
    Record one pattern against a category, then re-run categorisation.

    This is how the long tail gets fixed. A public ruleset can cover
    supermarkets and power companies; it can never cover a particular town's
    takeaway shop, and only the household knows what "SP QUAYSIDE 4829" was.
    Writing that answer into config/learned.yml turns it into a rule that
    decides the same way every month, rather than a judgement made afresh -
    and possibly differently - each time somebody looks at the ledger.

    The category must already exist in rules.yml. A pattern can teach the tool
    a merchant it has never seen; it cannot invent a category, because a
    category carries a group and the group drives the coaching maths.

    Raises ValueError, with wording a caller can pass straight to whoever
    asked, when the category is unknown or the pattern is unsafe to match on.
    """
    pattern = (pattern or "").strip()
    labels = config.declared_categories()
    if category not in labels:
        raise ValueError(
            f"Unknown category '{category}'. It must be one rules.yml already defines: "
            f"{', '.join(sorted(labels))}."
        )
    _check_pattern(pattern)

    # Snapshotted before the write, so the count reported afterwards is what
    # this rule actually claimed rather than everything in the category.
    unknown_before = {tx["fingerprint"] for tx in db.all_transactions() if is_unknown(tx)}
    added = _append_pattern(category, pattern)

    # recategorise_all() reloads config first, so the pattern just written is
    # compiled before anything is matched against it.
    cover = recategorise_all()

    matched = sum(
        1
        for tx in db.all_transactions()
        if tx["fingerprint"] in unknown_before and tx["category"] == category
    )
    return {
        "pattern": pattern,
        "category": category,
        "label": labels[category],
        "already_known": not added,
        "matched": matched,
        "path": str(learned_path()),
        "coverage": cover,
    }
