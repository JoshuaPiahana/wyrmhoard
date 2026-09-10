"""
The guard that keeps household data out of the repository.

`scripts/check_no_financial_data.py` runs pre-commit and in CI, and until now
nothing tested it. That is an odd gap for the one control standing between a
real bank export and a public repository - a guard nobody exercises is a guard
nobody knows is still working.

These tests exist because of a specific near miss. Receipt-level data was about
to be added, and the guard would have passed a supermarket purchase history
naming every item a household bought. Three separate reasons, all of which had
to line up:

  - a purchase-history export is `.json` or `.html`, and neither is in
    BLOCKED_SUFFIXES - nor can be, without failing on `web/src/index.html`
  - BLOCKED_DIRS named `data/inbox/` and `data/snapshots/` but not the
    directory a receipt would land in
  - the content scan looks for bank account numbers and IRD numbers, and a
    receipt contains neither

So `data/` is now blocked wholesale. The test that matters is the first one:
it fails if anybody narrows that back to a list of subdirectories.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _guard():
    """The guard script, loaded from source - it lives outside the package."""
    candidates = (
        Path(__file__).resolve().parents[2],
        Path("/repo"),
        Path.cwd(),
    )
    root = next((c for c in candidates if (c / "scripts").is_dir()), None)
    if root is None:
        pytest.skip("repo root not reachable from here; this guard runs in CI")

    script = root / "scripts" / "check_no_financial_data.py"
    spec = importlib.util.spec_from_file_location("_guard", script)
    if spec is None or spec.loader is None:
        pytest.skip(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path",
    [
        # The near miss, in the two formats a supermarket actually exports.
        "data/receipts/order-88213.json",
        "data/receipts/receipt.html",
        # Subdirectories that exist today.
        "data/inbox/kiwibank-export.csv",
        "data/payslips/september.pdf",
        "data/ledger.db",
        # And one nobody has thought of yet, which is the point.
        "data/whatever-comes-next/anything.txt",
    ],
)
def test_nothing_tracked_under_data_is_ever_acceptable(path: str):
    """
    `data/` is blocked wholesale, not by naming its subdirectories.

    Every subdirectory added since this guard was written - payslips, backups,
    receipts next - was a hole until somebody remembered to list it. Blocking
    the parent removes the class of mistake rather than the instance.
    """
    problems = _guard().check([path])
    assert problems, f"the guard would allow {path} to be committed"


@pytest.mark.parametrize("path", ["data/.gitkeep", "data/samples/kiwibank_sample.json"])
def test_the_two_permitted_paths_under_data_still_pass(path: str):
    """`.gitkeep` keeps the directory; `data/samples/` is where synthetic data goes."""
    assert _guard().check([path]) == []


@pytest.mark.parametrize(
    "path",
    [
        "web/src/index.html",
        ".claude/settings.json",
        "api/requirements-core.txt",
        "README.md",
    ],
)
def test_ordinary_tracked_files_are_not_swept_up(path: str):
    """
    Why the rule is directory-based rather than suffix-based.

    Adding `.html` and `.json` to BLOCKED_SUFFIXES would have been the obvious
    fix and would have failed the build on the dashboard's own markup. This
    test is here so that shortcut gets caught if anybody tries it later.
    """
    assert _guard().check([path]) == []


def test_an_account_number_not_on_the_allowlist_is_still_caught():
    """
    The content scan is the other half, and it is unchanged.

    The test number is assembled at runtime rather than written out, and that
    is not squeamishness. The first version of this file spelled one out to
    look realistic, and CI rejected the file - correctly, because "never put a
    real account number in a test" is a rule that cannot afford a carve-out for
    numbers the author believes are invented. The guard reads file text, so a
    number that is never a literal is never a violation.
    """
    guard = _guard()
    not_allowlisted = "-".join(("99", "9999", "9999999", "97"))

    assert not_allowlisted not in guard.ALLOWED_ACCOUNTS
    assert guard.ACCOUNT_RE.search(f"paid to {not_allowlisted} on Tuesday")
    assert "38-9014-0123456-00" in guard.ALLOWED_ACCOUNTS
