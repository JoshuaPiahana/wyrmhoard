"""
The tool must work for households that look nothing like the one it was built
against.

Any tool of this kind is developed against a single real household's data,
which is exactly the situation in which assumptions get baked in silently.
These tests pin the behaviour for the households most likely to be let down by
that: renters, people without children, single people, two-income households,
and anyone outside New Zealand.

The bar is not "produces something". It is "produces nothing wrong" - a renter
should get no mortgage advice at all, rather than mortgage advice about a
balance of zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import config
from wyrmhoard.analysis import entitlements, mortgage


def write_household(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: dict) -> None:
    """Point the config layer at a throwaway household.yml."""
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "household.yml").write_text(yaml.safe_dump(data), encoding="utf-8")

    # nz_rates.yml is read by the entitlements module; copy the real one so we
    # are testing our logic rather than an empty file.
    real_rates = Path(__file__).resolve().parents[2] / "config" / "nz_rates.yml"
    if real_rates.exists():
        (cfg / "nz_rates.yml").write_text(real_rates.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    config.reload()


@pytest.fixture(autouse=True)
def _restore_config():
    """Never let a test leak its fake config into the next one."""
    yield
    config.reload()


# ---------------------------------------------------------------------------
# Empty / minimal configuration
# ---------------------------------------------------------------------------
def test_empty_config_does_not_crash(tmp_path, monkeypatch):
    """An empty file is a legitimate starting point, not an error."""
    write_household(tmp_path, monkeypatch, {})
    hh = config.household()

    assert hh.name == "Our Household"
    assert hh.children_ages() == []
    assert hh.has_mortgage is False
    assert hh.earners == []
    assert hh.gross_income_declared is None


# ---------------------------------------------------------------------------
# Renters and mortgage-free owners
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("balance", [None, 0, 0.0])
def test_no_mortgage_is_recognised(tmp_path, monkeypatch, balance):
    write_household(tmp_path, monkeypatch, {"mortgage": {"balance": balance}})
    assert config.household().has_mortgage is False


def test_renter_gets_no_mortgage_findings(tmp_path, monkeypatch):
    """
    A renter should see no mortgage content whatsoever - not a zero-balance
    loan, not a prompt to fill in missing loan details.
    """
    write_household(
        tmp_path, monkeypatch, {"household": {"country": "NZ"}, "mortgage": {"balance": None}}
    )
    from wyrmhoard import coach

    findings = coach.build_findings()
    ids = {f.id for f in findings}
    assert "mortgage" not in ids
    assert "mortgage_missing" not in ids

    plan_titles = " ".join(s["title"].lower() for s in coach.build_plan())
    assert "mortgage" not in plan_titles


def test_homeowner_with_mortgage_still_gets_the_maths(tmp_path, monkeypatch):
    write_household(
        tmp_path,
        monkeypatch,
        {
            "mortgage": {
                "balance": 250000,
                "interest_rate_pct": 6.0,
                "repayment": 900,
                "repayment_frequency": "fortnightly",
            }
        },
    )
    hh = config.household()
    assert hh.has_mortgage is True

    result = mortgage.from_household(hh)
    assert result["available"] is True
    assert result["base"]["years"] > 0
    # Paying more must never take longer.
    extras = result["scenarios"]
    assert extras[-1]["years"] <= extras[0]["years"]


# ---------------------------------------------------------------------------
# Household composition
# ---------------------------------------------------------------------------
def test_no_children_means_no_entitlement_estimate(tmp_path, monkeypatch):
    write_household(
        tmp_path,
        monkeypatch,
        {"household": {"country": "NZ"}, "people": [{"name": "A", "role": "earner"}]},
    )
    result = entitlements.estimate()
    assert result["available"] is False
    assert "children" in result["reason"].lower()


def test_two_earners_are_summed_for_income(tmp_path, monkeypatch):
    """Entitlement abatement uses combined household income, not one salary."""
    write_household(
        tmp_path,
        monkeypatch,
        {
            "income": {
                "earners": [
                    {"label": "One", "gross_annual": 60000},
                    {"label": "Two", "gross_annual": 40000},
                ]
            }
        },
    )
    hh = config.household()
    assert len(hh.earners) == 2
    assert hh.gross_income_declared == 100000


def test_single_earner_primary_shape_still_works(tmp_path, monkeypatch):
    """The one-earner `primary:` shape must keep working alongside `earners:`."""
    write_household(
        tmp_path, monkeypatch, {"income": {"primary": {"label": "Job", "gross_annual": 55000}}}
    )
    hh = config.household()
    assert len(hh.earners) == 1
    assert hh.gross_income_declared == 55000


# ---------------------------------------------------------------------------
# Outside New Zealand
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("country", ["AU", "GB", "US", "CA"])
def test_non_nz_disables_entitlements_cleanly(tmp_path, monkeypatch, country):
    """
    Showing an Australian household what a NZ family would receive would be
    worse than showing nothing. The module must decline, and say why.
    """
    write_household(
        tmp_path,
        monkeypatch,
        {
            "household": {"country": country},
            "people": [{"name": "Kid", "role": "child", "birth_year": 2020}],
        },
    )
    result = entitlements.estimate()

    assert result["available"] is False
    assert result["unsupported_country"] == country
    assert "New Zealand" in result["reason"]
    # It must not leak a number that somebody could mistake for their own.
    assert "total_estimate_annual" not in result


def test_non_nz_gets_no_kiwisaver_finding(tmp_path, monkeypatch):
    write_household(tmp_path, monkeypatch, {"household": {"country": "GB"}})
    from wyrmhoard import coach

    assert "kiwisaver" not in {f.id for f in coach.build_findings()}


def test_non_nz_plan_omits_the_entitlements_step(tmp_path, monkeypatch):
    write_household(tmp_path, monkeypatch, {"household": {"country": "AU"}})
    from wyrmhoard import coach

    plan = coach.build_plan()
    assert not any("entitled" in s["title"].lower() for s in plan)
    # The plan must still be sequential with no numbering gaps.
    assert [s["order"] for s in plan] == list(range(1, len(plan) + 1))


def test_country_defaults_to_nz_and_is_case_insensitive(tmp_path, monkeypatch):
    write_household(tmp_path, monkeypatch, {"household": {"country": "nz"}})
    assert config.household().country == "NZ"
    assert config.household().region_supported is True
