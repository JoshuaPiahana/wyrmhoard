"""
The core knows no shop's name, and never sums a document with its transaction.

Two rules, both structural, both easy to break by accident.

**No retailer in the core.** A supermarket redesigns its receipt on its own
schedule. A parser for that layout living in `api/wyrmhoard/` would mean cutting
a release of a household finance tool because a shop changed its letterhead -
which is the release-cadence argument that already put jurisdiction rules
outside. The core defines the shape a document arrives in, checks it, and
refuses what does not fit; reading a particular PDF is a producer's job.

**No analysis reads `document_items`.** A transaction says $315.73 left the
account and its lines say the same $315.73 in detail. Anything summing both is
wrong by exactly 2x, and it will look plausible - a grocery figure that doubled
reads like a bad month, not like a bug. Keeping items out of the analysis path
makes that arithmetically impossible rather than merely discouraged.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

#: Shops. Naming one in the core means the core carries knowledge that changes
#: on somebody else's release schedule.
RETAILERS = (
    "woolworths",
    "countdown",
    "newworld",
    "new_world",
    "foodstuffs",
    "paknsave",
    "pak_n_save",
    "bunnings",
    "kmart",
)

NAMES_A_RETAILER = re.compile(rf"(?:^|[^a-z0-9])({'|'.join(RETAILERS)})(?:[^a-z0-9]|$)", re.I)

#: Modules allowed to say them, and why.
ALLOWED = {
    # Writes a synthetic ledger, so it needs plausible merchant text. Invented
    # transactions for a fictional household, not knowledge of a real shop's
    # document format.
    "samples.py",
}

#: Line items are read by storage, by the module that owns them, and by the
#: interfaces that serve them on an explicit request. The bug this guards
#: against is `analysis/` learning to read them, because that is where every
#: figure the household sees gets computed.
ITEMS_FORBIDDEN_IN = "analysis"


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
    candidates = (
        Path(__file__).resolve().parents[2] / "api" / "wyrmhoard",
        Path("/repo/api/wyrmhoard"),
        Path.cwd() / "api" / "wyrmhoard",
        Path(__file__).resolve().parents[1] / "wyrmhoard",
    )
    root = next((c for c in candidates if c.is_dir()), None)
    if root is None:
        pytest.skip("Cannot locate the wyrmhoard package from this working directory.")
    return sorted(root.rglob("*.py"))


def test_no_core_module_names_a_retailer():
    """
    If this fails, the fix is not to add an exception.

    It is to move the retailer-specific part into a producer under
    `producers/`, and leave the core defining the shape it accepts. The core
    already refuses a document whose lines do not add up; that is the whole of
    what it needs to know about any particular shop.
    """
    offenders: list[str] = []
    for path in _core_modules():
        if path.name in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            for word in _identifiers(node, docstrings):
                found = NAMES_A_RETAILER.search(word)
                if found:
                    # Name the match, not the first 60 characters. A long
                    # multi-line string truncates to its opening line, which
                    # tells the reader nothing about why it was flagged.
                    offenders.append(
                        f"{path.name}:{getattr(node, 'lineno', 0)}: names {found.group(1)!r}"
                    )

    assert not offenders, "core modules naming a retailer:\n  " + "\n  ".join(
        sorted(set(offenders))
    )


def test_no_analysis_module_reads_line_items():
    """
    The double-counting guard.

    `analysis/` computes every figure the household sees. If any of it learns
    to read `document_items`, a grocery total can end up counting the same
    money twice - once from the bank row and once from the lines describing it.

    Storage, the module that owns them, and the interfaces that serve them on
    an explicit request are all fine. `GET /documents/{id}` returning lines is
    the intended read path; `cashflow.by_category()` reaching for them is not.
    """
    offenders: list[str] = []
    for path in _core_modules():
        if ITEMS_FORBIDDEN_IN not in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), 1):
            if "document_items" in line and not line.lstrip().startswith(("#", "*", '"', "'")):
                offenders.append(f"{path.name}:{lineno}: {line.strip()[:70]}")

    assert not offenders, (
        "line items reached outside storage - a figure summing them alongside "
        "their parent transaction is wrong by 2x:\n  " + "\n  ".join(offenders)
    )
