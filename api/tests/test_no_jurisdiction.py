"""
The core holds no country's rulebook.

This is rule 4, enforced rather than remembered. It is the third guard of its
kind - `test_offline.py` for the network, `test_taxonomy.py` for the spending
vocabulary - and they all exist for the same reason: nobody adds a violation
on purpose. Each one arrives as a reasonable local shortcut.

**The line this draws is between policy and vocabulary, and it is not the line
a naive grep for "NZ" would draw.**

A payslip parser recognising the word "KiwiSaver" is doing exactly what a
merchant rule does when it recognises "PAK N SAVE": matching local text.
Every country has a retirement contribution, an income-tax line and a tax
year. The structure is universal, only the words are local, and the words
belong wherever the parsing happens.

A module knowing that Working for Families abates at 27 cents in the dollar
above $42,700 is something else entirely. That is a rulebook, it changes on a
date somebody else chooses, and it is wrong for every household outside one
country. That belongs to a jurisdiction pack, which is a consumer.

So this bans scheme names and rate constants, and deliberately says nothing
about payslip field names. See `docs/ARCHITECTURE.md`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

#: Benefit and tax schemes. Naming one in the core means holding an opinion
#: about a country's rules - which household qualifies, at what rate, abated
#: how. Every one of these is a rulebook with an owner who is not this project.
SCHEMES = (
    "working_for_families",
    "workingforfamilies",
    "family_tax_credit",
    "in_work_tax_credit",
    "best_start",
    "beststart",
    "rates_rebate",
    "accommodation_supplement",
    "winter_energy",
    "wfftc",
)

#: Names that mean "a table of somebody's tax rates lives here".
RATE_TABLES = (
    "nz_rates",
    "tax_rates",
    "benefit_rates",
    "abatement",
)

BANNED = SCHEMES + RATE_TABLES

#: Modules exempt, and why. Keep this short and keep the reasons here rather
#: than in a commit message.
ALLOWED = {
    # Generates a synthetic ledger, so it has to write plausible bank memos -
    # "INLAND REVENUE WFFTC" is the same kind of string as "PAK N SAVE". It is
    # invented transaction text for a fictional household, not a rule about a
    # real one, and the demo would be worse for being jurisdiction-neutral
    # about what a bank statement actually looks like.
    "samples.py",
}

#: Matched against identifiers and string literals, case-insensitively, as a
#: whole word or a prefix. `best_start_estimate` is caught; a sentence
#: mentioning a scheme in a docstring is not, because explaining why the rule
#: exists is the point of the rule.
NAMES_A_SCHEME = re.compile(rf"(?:^|[^a-z0-9])({'|'.join(BANNED)})(?:[^a-z0-9]|$)", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _identifiers(node: ast.AST, docstrings: set[int]) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [] if id(node) in docstrings else [node.value]
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    return []


def _core_modules() -> list[Path]:
    candidates = [
        Path(__file__).resolve().parents[2] / "api" / "wyrmhoard",
        Path("/repo/api/wyrmhoard"),
        Path.cwd() / "api" / "wyrmhoard",
        Path(__file__).resolve().parents[1] / "wyrmhoard",
    ]
    # One exit rather than a return inside the loop and a fall-through after
    # it. `pytest.skip` raises, so the old shape could not actually return
    # None - but it read as though it could, and CodeQL flagged it as such.
    root = next((c for c in candidates if c.is_dir()), None)
    if root is None:
        pytest.skip("Cannot locate the wyrmhoard package from this working directory.")
    return sorted(root.rglob("*.py"))


def _config_dir() -> Path:
    candidates = (
        Path(__file__).resolve().parents[2] / "config",
        Path("/repo/config"),
        Path.cwd() / "config",
    )
    found = next((c for c in candidates if c.is_dir()), None)
    if found is None:
        pytest.skip("Cannot locate the config directory.")
    return found


def test_no_core_module_names_a_benefit_scheme():
    """
    The rule.

    If this fails, the fix is not to add an exception. It is to ask what the
    core actually needs - almost always "how much arrived in category X over
    range Y", which `analysis/series.py` answers without knowing what X means -
    and leave the scheme's name to whoever owns the rulebook.
    """
    offenders: list[str] = []
    for path in _core_modules():
        if path.name in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            for word in _identifiers(node, docstrings):
                if NAMES_A_SCHEME.search(word):
                    offenders.append(f"{path.name}:{getattr(node, 'lineno', 0)}: {word[:60]!r}")

    assert not offenders, "core modules naming a benefit scheme or rate table:\n  " + "\n  ".join(
        sorted(set(offenders))
    )


def test_no_rate_table_ships_with_the_core():
    """
    A rate file is a rulebook whether or not any code reads it.

    `config/nz_rates.yml` shipped seven blocks all marked `verified: false`,
    which is the shape of the problem: constants somebody has to re-check on a
    calendar this project does not control.
    """
    stray = sorted(
        p.name
        for p in _config_dir().glob("*.yml")
        if re.search(r"rates|tax|benefit", p.stem, re.IGNORECASE)
    )
    assert not stray, f"rate tables in config/: {stray}"


def test_payslip_vocabulary_is_deliberately_allowed():
    """
    The other half of the line, asserted so nobody "fixes" it later.

    A future contributor tightening this guard until it also bans `kiwisaver_ee`
    would be removing the parser's ability to read a New Zealand payslip, which
    is not the same kind of thing at all. This test fails if that happens, and
    the docstring above says why.
    """
    payslip = next(p for p in _core_modules() if p.name == "payslip.py")
    source = payslip.read_text(encoding="utf-8")

    assert "kiwisaver_ee" in source, "the payslip parser lost its retirement-contribution field"
    for term in ("kiwisaver_ee", "paye", "acc_levy"):
        assert not NAMES_A_SCHEME.search(term), (
            f"the guard now bans {term!r}, which is payslip vocabulary rather than "
            "a benefit scheme - see this module's docstring"
        )
