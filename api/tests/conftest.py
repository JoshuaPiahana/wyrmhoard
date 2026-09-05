"""
Shared test setup.

The important thing here is ledger isolation. Several tests exercise the
coaching and analysis layers, which read the ledger; without isolation they
quietly read whatever is in `data/ledger.db` on the machine running them.

That is not a hypothetical. Four tests passed for weeks on a development
machine with a populated ledger and failed on the first CI run against a fresh
checkout, where `data/` contains nothing and the table does not exist yet:

    sqlite3.OperationalError: no such table: transactions

A test that passes only because the developer happens to have data is worse
than no test - it reports green while proving nothing. This fixture gives
every test its own empty, initialised ledger, so results depend on the test
and nothing else.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import cache, config, db


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    """A private, initialised ledger per test. Applied everywhere, always."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(db, "DB_PATH", data_dir / "ledger.db")
    monkeypatch.setattr(cache, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "REPORT_DIR", tmp_path / "reports")

    # Analysis results are cached against the ledger file, so a stale entry
    # from a previous test would survive the redirect.
    cache.clear_all()
    db.init()

    yield

    cache.clear_all()
    config.reload()
