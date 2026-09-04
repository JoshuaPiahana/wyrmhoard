"""Loading and validating the household's configuration files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("KETE_CONFIG_DIR", "config"))
DATA_DIR = Path(os.environ.get("KETE_DATA_DIR", "data"))
REPORT_DIR = Path(os.environ.get("KETE_REPORT_DIR", "reports"))


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
        return sorted(
            [a for a in (c.age_on(when) for c in self.children) if a is not None]
        )

    # -- money -------------------------------------------------------------
    @property
    def mortgage(self) -> dict[str, Any]:
        return self.raw.get("mortgage", {}) or {}

    @property
    def income(self) -> dict[str, Any]:
        return self.raw.get("income", {}) or {}

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


@lru_cache(maxsize=1)
def rates() -> Rates:
    return Rates(_load_yaml(CONFIG_DIR / "nz_rates.yml"))


def reload() -> None:
    """Drop cached config so edits on the host take effect without a restart."""
    household.cache_clear()
    rules.cache_clear()
    rates.cache_clear()


def ensure_dirs() -> None:
    for d in (DATA_DIR, DATA_DIR / "inbox", DATA_DIR / "snapshots", REPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
