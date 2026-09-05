"""Loading and validating the household's configuration files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("WYRMHOARD_CONFIG_DIR", "config"))
DATA_DIR = Path(os.environ.get("WYRMHOARD_DATA_DIR", "data"))
REPORT_DIR = Path(os.environ.get("WYRMHOARD_REPORT_DIR", "reports"))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Person:
    name: str
    role: str
    birth_year: int | None = None
    birth_date: date | None = None

    def age_on(self, when: date) -> int | None:
        """Age in whole years, preferring an exact birth date when we have one."""
        if self.birth_date:
            years = when.year - self.birth_date.year
            if (when.month, when.day) < (self.birth_date.month, self.birth_date.day):
                years -= 1
            return years
        if self.birth_year:
            return when.year - self.birth_year
        return None


@dataclass
class Household:
    """The household's own facts. Everything else is derived from bank data."""

    raw: dict[str, Any] = field(default_factory=dict)

    # -- identity ----------------------------------------------------------
    @property
    def name(self) -> str:
        return self.raw.get("household", {}).get("name", "Our Household")

    @property
    def council(self) -> str | None:
        return self.raw.get("household", {}).get("council")

    @property
    def country(self) -> str:
        """
        ISO-ish country code. Gates the region-specific modules.

        Everything that matters - importing, categorising, cash flow,
        recurring payments, debt payoff, the report - works anywhere. Only the
        entitlement estimates are country-specific, and they switch themselves
        off rather than quietly showing a household in Ontario what a New
        Zealand family would receive.
        """
        return str(self.raw.get("household", {}).get("country", "NZ")).upper()

    @property
    def region_supported(self) -> bool:
        """True when this country has an entitlements module."""
        return self.country in {"NZ"}

    # -- people ------------------------------------------------------------
    @property
    def people(self) -> list[Person]:
        out: list[Person] = []
        for p in self.raw.get("people", []) or []:
            bd = p.get("birth_date")
            if isinstance(bd, str):
                bd = date.fromisoformat(bd)
            out.append(
                Person(
                    name=p.get("name", "?"),
                    role=p.get("role", "other"),
                    birth_year=p.get("birth_year"),
                    birth_date=bd,
                )
            )
        return out

    @property
    def children(self) -> list[Person]:
        return [p for p in self.people if p.role == "child"]

    def children_ages(self, when: date | None = None) -> list[int]:
        when = when or date.today()
        return sorted([a for a in (c.age_on(when) for c in self.children) if a is not None])

    # -- money -------------------------------------------------------------
    @property
    def mortgage(self) -> dict[str, Any]:
        return self.raw.get("mortgage", {}) or {}

    @property
    def has_mortgage(self) -> bool:
        """
        Renters, and owners who have finished paying, are first-class here.

        A missing or zero balance means no mortgage - which is different from
        a mortgage whose details have not been filled in yet, and the coach
        needs to tell those apart so it does not nag a renter about a loan
        they do not have.
        """
        balance = self.mortgage.get("balance")
        return balance is not None and float(balance) > 0

    @property
    def income(self) -> dict[str, Any]:
        """
        Legacy income block.

        Income now comes from payslips, which state it exactly. This remains
        only so an older household.yml keeps working, and as a fallback for
        anyone who has not imported a payslip yet.
        """
        return self.raw.get("income", {}) or {}

    @property
    def upside(self) -> list[dict[str, Any]]:
        """
        Income the budget must survive without.

        The one judgement a payslip cannot make: it reports what irregular
        work paid, but not whether the household should count on it.
        """
        listed = self.raw.get("upside")
        if isinstance(listed, list):
            return [u for u in listed if isinstance(u, dict)]
        return (self.income.get("upside") or []) if self.income else []

    @property
    def earners(self) -> list[dict[str, Any]]:
        """
        Every planned income stream.

        Accepts both shapes: a single `primary:` mapping, or an `earners:`
        list for households with two or more incomes. Normalising here means
        nothing downstream has to care which was written.
        """
        inc = self.income
        listed = inc.get("earners")
        if isinstance(listed, list) and listed:
            return [e for e in listed if isinstance(e, dict)]
        primary = inc.get("primary")
        return [primary] if isinstance(primary, dict) else []

    @property
    def gross_income_declared(self) -> float | None:
        """Combined declared gross across all earners, if any are filled in."""
        totals = [
            float(e["gross_annual"])
            for e in self.earners
            if e.get("gross_annual") not in (None, "")
        ]
        return sum(totals) if totals else None

    @property
    def goals(self) -> list[dict[str, Any]]:
        return self.raw.get("goals", []) or []

    @property
    def settings(self) -> dict[str, Any]:
        return self.raw.get("settings", {}) or {}

    @property
    def small_transaction_threshold(self) -> float:
        return float(self.settings.get("small_transaction_threshold", 20))

    @property
    def currency(self) -> str:
        return self.settings.get("currency", "NZD")

    @property
    def is_configured(self) -> bool:
        """True once the household has filled in their own file."""
        return bool(self.raw)


@dataclass
class Rates:
    """NZ entitlement constants, plus honesty about whether they are current."""

    raw: dict[str, Any] = field(default_factory=dict)

    def block(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {}) or {}

    def is_verified(self, name: str) -> bool:
        return bool(self.block(name).get("verified", False))

    @property
    def any_unverified(self) -> bool:
        blocks = [
            "working_for_families",
            "best_start",
            "kiwisaver",
            "rates_rebate",
            "paye",
        ]
        return any(not self.is_verified(b) for b in blocks)

    @property
    def unverified_blocks(self) -> list[str]:
        blocks = [
            "working_for_families",
            "best_start",
            "kiwisaver",
            "rates_rebate",
            "paye",
        ]
        return [b for b in blocks if not self.is_verified(b)]


@lru_cache(maxsize=1)
def household() -> Household:
    """The real household file, falling back to the example so the app boots."""
    real = CONFIG_DIR / "household.yml"
    example = CONFIG_DIR / "household.example.yml"
    if real.exists():
        return Household(_load_yaml(real))
    return Household(_load_yaml(example))


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    """Categorisation rules, with any learned corrections merged over the top."""
    base = _load_yaml(CONFIG_DIR / "rules.yml")
    learned = _load_yaml(CONFIG_DIR / "learned.yml")
    if learned.get("categories"):
        for key, cat in learned["categories"].items():
            if key in base.get("categories", {}):
                base["categories"][key].setdefault("match", [])
                base["categories"][key]["match"].extend(cat.get("match", []))
            else:
                base.setdefault("categories", {})[key] = cat
    return base


def declared_categories() -> dict[str, str]:
    """
    The categories rules.yml itself defines, as key -> label.

    Deliberately reads rules.yml alone rather than `rules()`, and is the list
    anything validating a proposed category must check against. The merged
    view includes learned.yml, so validating against it would let one invented
    category authorise the next: a typo written once would then be a category
    forever, and the spending filed under it would sit outside every group the
    coaching maths knows about.

    Uncached on purpose. It is read when a rule is being taught, which is rare,
    and an uncached read cannot go stale against a file somebody just edited.
    """
    base = _load_yaml(CONFIG_DIR / "rules.yml")
    return {
        key: (cat or {}).get("label", key) for key, cat in (base.get("categories") or {}).items()
    }


@lru_cache(maxsize=1)
def rates() -> Rates:
    return Rates(_load_yaml(CONFIG_DIR / "nz_rates.yml"))


def reload() -> None:
    """
    Drop cached config so edits on the host take effect without a restart.

    This also clears the analysis caches. Those key on the ledger file, but
    every one of them reads config too - entitlement rates, the household's
    country, the small-transaction threshold - so a config-only change would
    otherwise serve results computed under the old settings. Enforcing it here
    rather than at each call site means the invariant holds for the CLI, the
    tests and any future caller, not just the /reload endpoint.

    Imported late to avoid a cycle: cache imports DATA_DIR from this module.
    """
    household.cache_clear()
    rules.cache_clear()
    rates.cache_clear()

    from . import cache as _cache

    _cache.clear_all()


def ensure_dirs() -> None:
    for d in (DATA_DIR, DATA_DIR / "inbox", DATA_DIR / "snapshots", REPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
