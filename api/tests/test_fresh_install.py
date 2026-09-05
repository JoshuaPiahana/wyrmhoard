"""
The state every new user starts in: nothing imported yet.

This is the most-visited code path in the project and the least-exercised
during development, because the author always has data. Everything must
degrade to "nothing to show yet" rather than crashing, dividing by zero, or
asserting a confident $0.00 that looks like a real answer.

It also pins the typed-empty-frame fix: an untyped empty DataFrame gives
every column `object` dtype and omits the derived columns, which produced a
pandas deprecation warning that is scheduled to become a hard error.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kete import cache, categorise, coach, config, db
from kete.analysis import cashflow, entitlements, recurring


@pytest.fixture
def empty_ledger(tmp_path, monkeypatch):
    """A brand-new install: schema created, not a single transaction."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    cache.clear_all()
    db.init()
    yield tmp_path
    cache.clear_all()


def test_empty_frame_is_typed_like_a_populated_one(empty_ledger):
    df = cashflow.frame()

    assert df.empty
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert pd.api.types.is_float_dtype(df["amount"])
    assert pd.api.types.is_bool_dtype(df["is_spend"])
    # The derived columns must exist, or consumers KeyError instead of
    # returning an honest empty result.
    for col in ("is_spend", "is_income", "month", "grp", "category", "balance"):
        assert col in df.columns


def test_no_analysis_function_crashes_on_an_empty_ledger(empty_ledger):
    assert cashflow.monthly() == []
    assert cashflow.complete_months() == []
    assert cashflow.by_category() == []
    assert cashflow.typical_month()["available"] is False
    assert cashflow.trend()["available"] is False
    assert recurring.detect() == []
    assert recurring.summary()["count"] == 0
    assert categorise.top_uncategorised() == []


def test_empty_ledger_raises_no_deprecation_warnings(empty_ledger):
    """
    Regression: date arithmetic against an untyped empty column emitted a
    NumPy timedelta DeprecationWarning that pandas will turn into an error.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        recurring.summary()
        cashflow.summary()


def test_coverage_reports_honestly_rather_than_100_percent(empty_ledger):
    """
    Nothing categorised out of nothing is not "100% categorised". Claiming a
    perfect score on an empty ledger would be the tool's first lie.
    """
    cov = categorise.coverage()
    assert cov["transaction_count"] == 0
    assert cov["trustworthy"] is False


def test_summary_is_serialisable_and_flags_that_there_is_no_data(empty_ledger):
    s = cashflow.summary()
    assert s["stats"]["transactions"] == 0
    assert s["typical_month"]["available"] is False
    assert "reason" in s["typical_month"]
    assert s["cash"]["runway_weeks"] is None


def test_coach_produces_a_usable_plan_with_no_data(empty_ledger):
    """
    A new user opening the app should get a starting point, not an empty page
    or a stack trace.
    """
    result = coach.summary()
    assert isinstance(result["findings"], list)
    assert len(result["plan"]) >= 1
    assert result["plan"][0]["status"] in {"todo", "in progress", "done"}
    # Step numbering must stay contiguous however many steps were skipped.
    assert [s["order"] for s in result["plan"]] == list(range(1, len(result["plan"]) + 1))


def test_entitlements_declines_rather_than_inventing_a_number(empty_ledger):
    result = entitlements.estimate()
    assert result["available"] is False
    assert "total_estimate_annual" not in result


def test_report_renders_from_an_empty_ledger(empty_ledger, tmp_path, monkeypatch):
    """
    The report is the deliverable. It must produce a real page saying there is
    nothing to show yet, rather than failing to build at all.
    """
    from kete import report

    monkeypatch.setattr(config, "REPORT_DIR", tmp_path / "reports")
    path = report.build_report(outdir=tmp_path / "reports")

    html = Path(path).read_text(encoding="utf-8")
    assert html.lstrip().startswith("<!doctype html>")
    assert "Not enough data yet" in html
    # No unrendered template syntax, and no Python None leaking into the page.
    assert "{{" not in html
    assert ">None<" not in html
