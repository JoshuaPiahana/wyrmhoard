"""
Reading payslips, and knowing when the reading is wrong.

Every employer words a payslip differently, so the parser looks for labels
rather than positions and carries aliases for the common New Zealand payroll
systems. That will still not cover everything, which is why the real safety
net is arithmetic: gross plus deductions must equal net. When it does, the
three numbers that matter were read correctly. When it does not, the payslip
is rejected rather than allowed to distort every income figure downstream.

The layouts below are invented. Two are deliberately unlike each other, to
check that nothing depends on one employer's template.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import cache, db
from wyrmhoard.analysis import income
from wyrmhoard.ingest.payslip import parse_text, redact

# A government/SAP-style slip: sections, a rate column, year-to-date block.
SAP_STYLE = """
Period: 2026 / 11 Payroll Area: CV Pay Date: 26/08/2026
Member: A Person
Personnel Number: 01067286 IRD Number: 123456789 Tax Code: M
Payment Summary
Total Payments This Period: 3,771.12 Paid To Bank Account: 389019 0484413000
Total Deductions This Period: -1,100.65
Amount Paid This Period: 2,670.47
Deductions Total Deductions -1,100.65
Full Income Tax -921.12
Kiwi Saver Employee Dedn -131.98
Superannuation Contributions and Deductions
Scheme Rate Amount
Kiwi Saver Employee Dedn 3.50 -131.98
Kiwi Saver Company Contri 4.00 150.84
Annual Remuneration Year to Date Values
Total Rem (TR) 101,971.00 Taxable Gross 41,482.27
Base Salary 98,049.00 Full Income tax -10,132.32
"""

# A small-business style slip: no sections, different words, no rate column.
XERO_STYLE = """
Payslip for B Person
Pay Period Ending 15/08/2026
Gross Earnings 2,400.00
PAYE -456.00
KiwiSaver (Employee) -72.00
Student Loan Repayment -84.00
Total Deductions -612.00
Net Pay 1,788.00
KiwiSaver (Employer) 72.00
Year to Date Gross 28,800.00
"""


def test_sap_style_payslip_reads_correctly():
    r = parse_text(SAP_STYLE, "sap.pdf")

    assert r.confidence == "high"
    assert r.pay_date == "2026-08-26"
    assert r.tax_code == "M"
    assert r.values["gross"] == 3771.12
    assert r.values["net"] == 2670.47
    assert r.values["paye"] == -921.12


def test_the_same_label_in_two_sections_does_not_confuse_it():
    """
    "Full Income tax" is the fortnight's PAYE near the top and the year's
    total further down. Searching the whole document returns whichever comes
    first, which is wrong half the time.
    """
    r = parse_text(SAP_STYLE, "sap.pdf")

    assert r.values["paye"] == -921.12, "picked up the year-to-date figure"
    assert r.values["ytd_tax"] == -10132.32, "picked up the period figure"


def test_a_rate_column_is_not_mistaken_for_an_amount():
    """ "Kiwi Saver Company Contri 4.00 150.84" - the 4.00 is a percentage."""
    r = parse_text(SAP_STYLE, "sap.pdf")

    assert r.values["kiwisaver_er"] == 150.84
    assert r.values["kiwisaver_ee"] == -131.98


def test_hours_worked_are_not_mistaken_for_a_salary():
    """ "Fortnightly Base Salary 80.00 47.1389" - the 80.00 is hours."""
    text = SAP_STYLE.replace(
        "Payment Summary",
        "Pay Details\n13/08/2026 Fortnightly Base Salary 80.00 47.1389 3,771.12\nPayment Summary",
    )
    assert parse_text(text, "sap.pdf").values["base_salary"] == 98049.00


def test_a_completely_different_layout_also_works():
    """Nothing may depend on one employer's template."""
    r = parse_text(XERO_STYLE, "xero.pdf")

    assert r.confidence == "high"
    assert r.pay_date == "2026-08-15"
    assert r.values["gross"] == 2400.00
    assert r.values["net"] == 1788.00
    assert r.values["student_loan"] == -84.00
    assert r.values["ytd_gross"] == 28800.00


def test_a_payslip_that_does_not_add_up_is_rejected():
    """The safety net. A misread payslip must never be quietly believed."""
    broken = XERO_STYLE.replace("Net Pay 1,788.00", "Net Pay 9,999.00")
    r = parse_text(broken, "broken.pdf")

    assert r.confidence == "low"
    assert any("do not add up" in w for w in r.warnings)


def test_a_period_with_no_pay_is_valid_not_broken():
    """Casual and reserve work has empty periods; that is not a parse failure."""
    text = """
    Pay Date: 03/09/2026
    Tax Code: ST
    Total Payments This Period: 0.00
    Total Deductions This Period: 0.00
    Amount Paid This Period: 0.00
    """
    r = parse_text(text, "zero.pdf")

    assert r.confidence == "high"
    assert r.values["gross"] == 0.0
    assert any("No pay in this period" in w for w in r.warnings)


def test_something_that_is_not_a_payslip_is_refused():
    r = parse_text("Dear customer, your order has shipped.", "letter.pdf")

    assert r.confidence == "low"
    assert any("may not be a payslip" in w for w in r.warnings)


# ---------------------------------------------------------------------------
# The IRD number must never survive
# ---------------------------------------------------------------------------
def test_the_ird_number_is_redacted():
    """
    A payslip carries a national identifier this tool has no use for. The
    safest way never to leak a secret is never to hold it.
    """
    assert "123456789" in SAP_STYLE
    assert "123456789" not in redact(SAP_STYLE)
    assert "[redacted]" in redact(SAP_STYLE)


def test_a_parsed_payslip_never_carries_the_ird_number():
    dumped = str(parse_text(SAP_STYLE, "sap.pdf").as_dict())
    assert "123456789" not in dumped


# ---------------------------------------------------------------------------
# Turning payslips into an income figure
# ---------------------------------------------------------------------------
def save(
    ref: str,
    ytd: float,
    stated: float | None,
    pay_date: str,
    tax_code: str = "M",
    base: float | None = None,
    er_super: float | None = None,
):
    db.save_payslip(
        {
            "pay_date": pay_date,
            "employer": ref,
            "employee_ref": ref,
            "tax_code": tax_code,
            "ytd_gross": ytd,
            "annual_rem": stated,
            "base_salary": base,
            "er_super_annual": er_super,
            "confidence": "high",
            "source_file": f"{ref}.pdf",
        }
    )
    cache.clear_all()


def test_no_payslips_means_no_claim():
    assert income.from_payslips()["available"] is False


def test_a_stated_salary_that_agrees_with_earnings_is_a_cross_check():
    """
    When actual earnings annualise close to the stated salary, the stated
    figure is not notional - but it is still not the number used. Annualised
    taxable earnings are, because they include overtime and allowances and
    exclude anything the package figure has bundled in.
    """
    save("main", ytd=50000.0, stated=100000.0, pay_date="2026-09-30")
    result = income.from_payslips(today=date(2026, 9, 30))

    assert result["jobs"][0]["stated_is_notional"] is False
    assert result["gross_annual"] == pytest.approx(100000.0, abs=1000.0)
    assert result["gross_annual"] == result["jobs"][0]["annualised"]


def test_a_notional_full_time_rate_is_not_believed():
    """
    The trap that mattered. A reservist payslip states what the role pays at
    full time - $90,979 - beside $3,533 actually earned. Adding stated figures
    across two jobs gave a household income of $193,000 instead of $105,000.
    """
    save("main", ytd=41482.27, stated=101971.00, pay_date="2026-08-26")
    save("reserve", ytd=3533.46, stated=90979.00, pay_date="2026-09-03", tax_code="ST")

    result = income.from_payslips(today=date(2026, 9, 5))
    reserve = next(j for j in result["jobs"] if j["ref"] == "reserve")

    assert reserve["stated_is_notional"] is True
    assert result["gross_annual"] < 130000, "believed a notional full-time rate"
    assert any("full-time rate" in n for n in result["notes"])


def test_a_total_remuneration_package_is_not_counted_as_salary():
    """
    On a total-remuneration contract the employer's KiwiSaver contribution
    comes OUT of the stated package rather than on top of it. Reading the
    package as wages overstates earnings by the super amount - here $3,922 -
    which then flows into every entitlement figure.
    """
    save(
        "main",
        ytd=41482.27,
        stated=101971.00,
        base=98049.00,
        er_super=3922.00,
        pay_date="2026-08-26",
    )
    result = income.from_payslips(today=date(2026, 9, 5))
    job = result["jobs"][0]

    assert job["package_includes_super"] is True
    assert job["employer_super_annual"] == 3922.00
    # The package figure must not become the income figure.
    assert result["gross_annual"] < 101971.00
    assert any("total-remuneration" in n for n in result["notes"])


def test_a_normal_contract_is_not_flagged_as_total_remuneration():
    """Employer super on top of salary is the ordinary case and needs no note."""
    save(
        "main",
        ytd=50000.0,
        stated=100000.0,
        base=100000.0,
        er_super=3000.0,
        pay_date="2026-09-30",
    )
    job = income.from_payslips(today=date(2026, 9, 30))["jobs"][0]
    assert job["package_includes_super"] is False


def test_income_uses_taxable_earnings_not_the_stated_package():
    """
    Annualised year-to-date taxable gross is what tax and entitlements key
    off. A stated package can be notional, or can bundle employer super, and
    both inflate income in ways nobody would notice.
    """
    save("main", ytd=50000.0, stated=120000.0, base=110000.0, pay_date="2026-09-30")
    result = income.from_payslips(today=date(2026, 9, 30))

    assert result["gross_annual"] == result["jobs"][0]["annualised"]
    assert result["gross_annual"] != 120000.0


def test_two_primary_tax_codes_are_flagged():
    """Two jobs both on 'M' usually means an end-of-year tax bill."""
    save("one", ytd=30000.0, stated=None, pay_date="2026-08-01", tax_code="M")
    save("two", ytd=20000.0, stated=None, pay_date="2026-08-02", tax_code="M")

    assert any("tax code" in n for n in income.from_payslips()["notes"])


def test_only_the_latest_payslip_per_employer_counts():
    """Year-to-date figures already accumulate; summing every slip double counts."""
    save("main", ytd=10000.0, stated=None, pay_date="2026-05-01")
    save("main", ytd=41482.27, stated=None, pay_date="2026-08-26")

    result = income.from_payslips(today=date(2026, 9, 5))
    assert len(result["jobs"]) == 1
    assert result["ytd_gross"] == 41482.27
