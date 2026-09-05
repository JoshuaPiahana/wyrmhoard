#!/usr/bin/env python3
"""
Block financial data from ever entering the repository.

.gitignore is a convenience, not a control: `git add -f` walks straight past
it, and a mis-scoped rule silently stops protecting you. For a public repo
built around somebody's bank records, "we had a gitignore" is not a good
enough answer. This runs as a pre-commit hook AND in CI, so the guarantee
holds even for a contributor who never installed the hooks.

It fails on:
  - data-bearing file types (.csv, .pdf, .ofx, .qif, .db, spreadsheets)
  - a real household.yml, or anything under reports/
  - bank account numbers that are not on the synthetic allowlist
  - long digit runs that look like card numbers (Luhn-checked)

Exit code 1 blocks the commit. Standard library only, so it runs anywhere.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BLOCKED_SUFFIXES = {
    ".csv",
    ".tsv",
    ".pdf",
    ".ofx",
    ".qif",
    ".qfx",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".xlsx",
    ".xls",
}

BLOCKED_PATHS = (
    "config/household.yml",
    "config/overrides.yml",
    "config/learned.yml",
)

BLOCKED_DIRS = ("reports/", "data/inbox/", "data/snapshots/")

# Synthetic values used by the sample generator and the test suite. These are
# the ONLY account numbers permitted in tracked source.
ALLOWED_ACCOUNTS = {
    "38-9014-0123456-00",
    "38-9014-0000000-01",
    "38-9014-0000000-05",
    # An account that is deliberately NOT the household's, for testing that
    # foreign counterparties are not treated as internal transfers.
    "99-9999-9999999-99",
}

# NZ bank account: bank-branch-account-suffix.
ACCOUNT_RE = re.compile(r"\b\d{2}-\d{3,4}-\d{6,8}-\d{2,4}\b")

# Long digit runs that might be a card number.
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yml",
    ".yaml",
    ".json",
    ".html",
    ".jinja",
    ".js",
    ".css",
    ".sh",
    ".toml",
    ".cfg",
    ".ini",
    ".txt",
    "",
}


def luhn_ok(digits: str) -> bool:
    """Real card numbers pass Luhn; version strings and hashes do not."""
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def check(paths: list[str]) -> list[str]:
    problems: list[str] = []

    for raw in paths:
        path = Path(raw)
        posix = path.as_posix()

        if path.suffix.lower() in BLOCKED_SUFFIXES:
            problems.append(
                f"{posix}: {path.suffix} files may contain financial records and are "
                f"never committed. If this is genuinely safe sample data, add an "
                f"explicit exception to scripts/check_no_financial_data.py."
            )
            continue

        if posix in BLOCKED_PATHS or any(posix.startswith(d) for d in BLOCKED_DIRS):
            problems.append(f"{posix}: this path holds real household data.")
            continue

        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # This file necessarily contains the patterns it screens for.
        if posix.endswith("scripts/check_no_financial_data.py"):
            continue

        for account in set(ACCOUNT_RE.findall(text)):
            if account not in ALLOWED_ACCOUNTS:
                problems.append(
                    f"{posix}: contains what looks like a bank account number "
                    f"({account}). Use one of the synthetic values instead."
                )

        for match in set(CARD_RE.findall(text)):
            digits = re.sub(r"[ -]", "", match)
            if luhn_ok(digits):
                problems.append(
                    f"{posix}: contains a {len(digits)}-digit number that passes a "
                    f"card-number checksum. Verify it is not a real card."
                )

    return problems


SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    # Only skipped in the no-git fallback below. These directories are where
    # real household data is SUPPOSED to live - they are gitignored, and
    # flagging them would make every local run fail on correct behaviour.
    # When git is available (CI, pre-commit) the authoritative tracked-file
    # list is used instead, so a force-added file in here is still caught.
    "data",
    "reports",
}


def _all_candidate_files() -> list[str]:
    """
    Every file worth checking.

    Prefers `git ls-files` so the check matches exactly what would be
    published, but falls back to a filesystem walk - git is not installed in
    the analysis container, and a guard that only runs where git happens to
    exist is not a guard.
    """
    try:
        import subprocess

        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
        tracked = [p for p in out.stdout.splitlines() if p.strip()]
        if tracked:
            return tracked
    except (OSError, subprocess.SubprocessError):
        pass

    found: list[str] = []
    root = Path.cwd()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path.relative_to(root).as_posix())
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Files to check.")
    parser.add_argument("--all", action="store_true", help="Check every tracked file (used in CI).")
    args = parser.parse_args()

    paths = args.paths
    if args.all or not paths:
        paths = _all_candidate_files()

    problems = check(paths)
    if problems:
        print("\nFinancial-data guard FAILED:\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nNothing containing real financial data may be committed to this " "repository.\n",
            file=sys.stderr,
        )
        return 1

    print(f"Financial-data guard passed ({len(paths)} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
