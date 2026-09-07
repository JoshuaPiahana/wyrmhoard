"""
The core must not know what a spending group means.

Two kinds of test in here, doing different jobs.

The behavioural ones prove a household can rename the whole vocabulary and
still get correct arithmetic - the actual promise.

The guard at the bottom enforces the *rule*, and it is the one worth keeping
when the others get rewritten. It fails if any core module mentions one of the
four judgement group names, because that is how they got welded into eighteen
places the first time: nobody added them on purpose, each one looked like a
reasonable local shortcut.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from wyrmhoard import categorise, config, taxonomy

#: Group names that assert something about a household rather than about
#: money. `income`, `transfer` and `unknown` are absent on purpose: those are
#: kinds, which the core is allowed to reason about.
JUDGEMENT_NAMES = ("essential", "discretionary", "sinking", "commitment")

#: Modules that are allowed to say them, and why.
ALLOWED = {
    # Declares the fallback vocabulary for a rules.yml written before the
    # `groups:` block existed.
    "taxonomy.py",
    # Ships an example ledger, which has to pick some categories.
    "samples.py",
}


#: Matches a group name used *as* a group name - the bare word, a plural, or a
#: figure named after one (`essentials_total`). Deliberately does not match
#: prose or a name with the group buried inside it, like the unit-map entry
#: `weeks_of_essentials`: explaining the rule in a docstring is the point, and
#: only code taking a side is a defect.
NAMED_A_GROUP = re.compile(rf"^({'|'.join(JUDGEMENT_NAMES)})s?(_|$)", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[int]:
    """id() of every node that is a docstring, so prose is not mistaken for code."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _identifiers(node: ast.AST, docstrings: set[int]) -> list[str]:
    """Every name or string literal this node introduces, docstrings excluded."""
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
    for root in candidates:
        if root.is_dir():
            return sorted(root.rglob("*.py"))
    pytest.skip("Cannot locate the wyrmhoard package from this working directory.")


@pytest.fixture
def renamed(tmp_path, monkeypatch):
    """
    A household that threw the shipped vocabulary away entirely.

    Barefoot-ish names, none of which the code has ever seen, and a deliberate
    trap: `grow` is a spending group whose name looks like saving.
    """
    (tmp_path / "rules.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "groups": {
                    "blow": {"label": "Blow", "kind": "spend"},
                    "mojo": {"label": "Mojo", "kind": "spend"},
                    "grow": {"label": "Grow", "kind": "spend"},
                    "earnings": {"label": "Earnings", "kind": "income"},
                    "shuffle": {"label": "Shuffle", "kind": "transfer"},
                },
                "categories": {
                    "groceries": {"label": "Groceries", "group": "blow", "match": ["PAK N SAVE"]},
                    "wages": {"label": "Wages", "group": "earnings", "match": ["SALARY"]},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    config.reload()
    categorise.compiled_rules.cache_clear()
    yield tmp_path
    monkeypatch.undo()
    config.reload()
    categorise.compiled_rules.cache_clear()


def test_a_household_can_rename_every_group(renamed):
    assert set(taxonomy.groups()) == {"blow", "mojo", "grow", "earnings", "shuffle", "unknown"}
    assert taxonomy.kind_of("blow") == "spend"
    assert taxonomy.is_spend("grow") is True
    assert taxonomy.is_spend("earnings") is False


def test_unknown_survives_a_vocabulary_that_omits_it(renamed):
    """Unmatched memos are filed as unknown whatever the household declared."""
    assert "unknown" in taxonomy.groups()
    assert taxonomy.kind_of("unknown") == "unknown"


def test_a_refund_flips_to_the_households_own_income_group(renamed):
    """
    The one thing the core genuinely needs from the taxonomy.

    A supermarket refund matches the groceries rule and arrives positive. It
    has to stop being spending, and it has to land in whatever this household
    calls income - not in a group named `income` that they never declared.
    """
    key, group, _ = categorise.categorise_one("POS W/D PAK'nSAVE PALM STH", amount=-84.20)
    assert (key, group) == ("groceries", "blow")

    key, group, _ = categorise.categorise_one("PAK'nSAVE REFUND", amount=84.20)
    assert key == "groceries"
    assert group == "earnings", "a refund was filed as spending in a renamed vocabulary"


def test_spending_groups_include_unknown_but_not_income(renamed):
    """
    Uncategorised spending is still spending.

    Dropping it would make a half-categorised ledger look tidier than it is,
    which is the opposite of what this tool is for.
    """
    groups = taxonomy.spending_groups()
    assert "unknown" in groups
    assert "earnings" not in groups
    assert "shuffle" not in groups


def test_an_undeclared_kind_is_reported_rather_than_guessed(tmp_path, monkeypatch):
    (tmp_path / "rules.yml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "groups": {"vibes": {"label": "Vibes", "kind": "spendy"}},
                "categories": {"x": {"label": "X", "group": "orphan", "match": ["X"]}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    config.reload()
    try:
        problems = taxonomy.problems()
        assert any("spendy" in p for p in problems)
        assert any("orphan" in p for p in problems)
        # Reported, not raised: a bad rules file must not stop an import.
        assert taxonomy.kind_of("vibes") == "unknown"
    finally:
        monkeypatch.undo()
        config.reload()


def test_the_shipped_rules_declare_every_group_they_use():
    """The vocabulary in config/rules.yml has to be internally consistent."""
    assert taxonomy.problems() == []


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------
def test_no_core_module_names_a_judgement_group():
    """
    The rule, enforced rather than remembered.

    Calling groceries essential is a household's judgement. The moment a core
    module says the word, it has taken a side, and it will be wrong for
    somebody. Consumers may say it as loudly as they like - `web/src/js/app.js`
    does, deliberately and in one place.

    If this fails, the fix is almost never to add the file to ALLOWED. It is to
    ask the taxonomy what kind a group is, or to move the decision to whoever
    is reading.
    """
    offenders: list[str] = []
    for path in _core_modules():
        if path.name in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for word in _identifiers(node, _docstrings(tree)):
                if NAMED_A_GROUP.match(word):
                    offenders.append(f"{path.name}:{getattr(node, 'lineno', 0)}: {word!r}")

    assert not offenders, "core modules naming a judgement group:\n  " + "\n  ".join(
        sorted(set(offenders))
    )
