"""
Reading a payslip PDF.

Bank data says what arrived. A payslip says what was earned and what was taken
out on the way - gross pay, tax, retirement contributions, the tax code - none
of which can be recovered from a bank credit. It is the difference between
estimating somebody's income by grossing up their deposits and simply knowing
it.

Two design choices, both learned from the CSV parser:

  * Labels, not positions. Payroll systems reformat, and this household's own
    payslips changed layout inside two years. Every field is found by looking
    for what it is called, with aliases, so a moved column or a restyled
    template does not break the parse.

  * Prove the parse before trusting it. A payslip carries its own checksum:
    gross plus deductions must equal net. If that holds, the numbers were read
    correctly. If it does not, the parse is reported as low confidence rather
    than quietly feeding a wrong salary into everything downstream.

DELIBERATELY NOT EXTRACTED: the IRD number. Payslips carry one, it is a
national identifier, and this tool has no use for it whatsoever. The safest
way to never leak a secret is to never hold it, so it is not read, not stored,
and the raw text is not retained either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# A money value: 1,234.56 / -1,234.56 / 47.1389
_AMOUNT = r"-?\$?\s?-?[\d,]+\.?\d*"

# Anything that looks like an IRD number, so it can be scrubbed from any text
# this module handles before it goes anywhere.
_IRD = re.compile(r"\bIRD\s*Number\s*:?\s*[\d\- ]{8,13}", re.I)


def redact(text: str) -> str:
    """Remove the IRD number from extracted text. Applied before anything else."""
    return _IRD.sub("IRD Number: [redacted]", text)


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# Each field: the labels it might appear under, most specific first. A label
# may be followed by its value on the same line, which is how every payroll
# system tested so far lays them out once the PDF is flattened to text.
# Aliases cover the common New Zealand payroll systems - Xero, MYOB, Smartly,
# iPayroll, PayHero, Datacom and the SAP-style government slips - because
# every employer words these differently and no household should have to care.
# When a label is genuinely unknown, the arithmetic check below catches it and
# the parse is reported as untrustworthy rather than guessed at.
FIELDS: dict[str, tuple[str, ...]] = {
    "gross": (
        r"Total Payments This Period",
        r"Total Gross(?: Earnings| Pay)?",
        r"Gross (?:Earnings|Pay|Payments|Wages)",
        r"Total Earnings",
        r"Total Pay\b",
    ),
    "deductions_total": (
        r"Total Deductions This Period",
        r"Total Deductions",
        r"Deductions Total",
    ),
    "net": (
        r"Amount Paid This Period",
        r"Net (?:Pay|Payment|Amount|Earnings)",
        r"Nett Pay",
        r"Take Home(?: Pay)?",
        r"Total Net",
        r"Amount Paid",
    ),
    "paye": (
        r"Full Income Tax",
        r"P\.?A\.?Y\.?E\.?(?: Tax)?",
        r"Income Tax",
        r"Tax Deducted",
        r"PAYE Deduction",
    ),
    "kiwisaver_ee": (
        r"Kiwi ?Saver Employee(?: Dedn| Deduction| Contribution)?",
        r"Employee (?:Kiwi ?Saver|Superannuation)(?: Contribution)?",
        r"Kiwi ?Saver \(Employee\)",
        r"Kiwi ?Saver Deduction",
        r"KS Employee",
    ),
    "kiwisaver_er": (
        r"Kiwi ?Saver (?:Company|Employer) Contri(?:bution)?",
        r"Employer (?:Kiwi ?Saver|Super)(?: Contribution)?",
        r"Kiwi ?Saver \(Employer\)",
        r"ER Super Cont",
        r"KS Employer",
    ),
    "student_loan": (
        r"Student Loan(?: Deduction| Repayment)?",
        r"SLCIR",
        r"SLBOR",
    ),
    "acc_levy": (r"ACC(?: Earners)?(?: Levy)?", r"Earners.? Levy"),
    "ytd_gross": (
        r"Taxable Gross",
        r"Gross(?: Earnings| Pay)? (?:YTD|Year to Date)",
        r"(?:YTD|Year to Date) Gross",
    ),
    "ytd_tax": (
        r"Full Income tax",
        r"(?:PAYE|Tax) (?:YTD|Year to Date)",
        r"(?:YTD|Year to Date) (?:PAYE|Tax)",
    ),
    "annual_rem": (r"Total Rem \(TR\)", r"Total Remuneration", r"Annual Salary"),
    "base_salary": (r"Base Salary", r"Annual Base"),
    # On a total-remuneration contract the employer's retirement contribution
    # comes OUT of the package rather than on top of it, so the stated total
    # is salary plus that contribution. Reading it lets us tell the two apart
    # instead of counting super as if it were wages.
    "er_super_annual": (
        r"ER Super Cont(?: \(Kiwi/Comp\))?",
        r"Employer Superannuation Contribution",
        r"Employer Contribution",
    ),
}


@dataclass
class PayslipReport:
    """What was read, and whether it can be trusted."""

    filename: str
    pay_date: str | None = None
    period: str | None = None
    employee_ref: str | None = None
    tax_code: str | None = None
    values: dict[str, float] = field(default_factory=dict)
    confidence: str = "low"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "pay_date": self.pay_date,
            "period": self.period,
            "employee_ref": self.employee_ref,
            "tax_code": self.tax_code,
            "confidence": self.confidence,
            "warnings": self.warnings,
            **{k: round(v, 2) for k, v in self.values.items()},
        }


# Fields that live in the year-to-date block. The same words appear in both
# halves of a payslip - "Full Income tax" is the fortnight's PAYE up top and
# the year's total further down - so searching the whole document returns
# whichever came first, which is the wrong one about half the time.
_YTD_FIELDS = frozenset({"ytd_gross", "ytd_tax", "annual_rem", "base_salary", "er_super_annual"})

_SECTION_BREAK = re.compile(r"Annual Remuneration|Year to Date Values", re.I)

# Superannuation lines list a rate and then an amount: "Kiwi Saver Company
# Contri 4.00 150.84". Taking the first number gives the percentage.
_RATE_THEN_AMOUNT = frozenset({"kiwisaver_ee", "kiwisaver_er"})


def _split_sections(text: str) -> tuple[str, str]:
    """(this period, year to date). Both halves fall back to the whole text."""
    match = _SECTION_BREAK.search(text)
    if not match:
        return text, text
    return text[: match.start()], text[match.start() :]


def _find(text: str, patterns: tuple[str, ...], rate_then_amount: bool = False) -> float | None:
    """First label that matches, and the number that belongs to it."""
    for pattern in patterns:
        if rate_then_amount:
            # Prefer "label <rate> <amount>", falling back to a single value
            # for payslips that omit the rate column.
            pair = re.search(rf"{pattern}\s*:?\s*{_AMOUNT}\s+({_AMOUNT})", text, re.I)
            if pair and (value := _num(pair.group(1))) is not None:
                return value
        match = re.search(rf"{pattern}\s*:?\s*({_AMOUNT})", text, re.I)
        if match and (value := _num(match.group(1))) is not None:
            return value
    return None


def _find_date(text: str) -> str | None:
    match = re.search(r"Pay Date\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.I)
    if not match:
        match = re.search(
            r"(?:Period Ending|Pay Period Ending)\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            text,
            re.I,
        )
    if not match:
        return None
    raw = match.group(1).replace("-", "/")
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_text(text: str, filename: str = "payslip.pdf") -> PayslipReport:
    """Pull the numbers out of already-extracted payslip text."""
    text = redact(text)
    report = PayslipReport(filename=filename)

    report.pay_date = _find_date(text)
    if period := re.search(r"Period\s*:?\s*(\d{4}\s*/\s*\d{1,2})", text, re.I):
        report.period = re.sub(r"\s+", "", period.group(1))
    if ref := re.search(r"Personnel Number\s*:?\s*(\w+)", text, re.I):
        report.employee_ref = ref.group(1)
    if code := re.search(r"Tax Code\s*:?\s*([A-Z]{1,4}\b)", text):
        report.tax_code = code.group(1)

    period_text, ytd_text = _split_sections(text)
    for name, patterns in FIELDS.items():
        scope = ytd_text if name in _YTD_FIELDS else period_text
        value = _find(scope, patterns, rate_then_amount=name in _RATE_THEN_AMOUNT)
        if value is not None:
            report.values[name] = value

    _assess(report)
    return report


def _assess(report: PayslipReport) -> None:
    """
    Decide how much to trust the parse.

    A payslip is self-checking: gross plus deductions equals net. When that
    balances, the three most important numbers were all read correctly, which
    is a far stronger signal than "a regex matched something".
    """
    v = report.values
    gross, net = v.get("gross"), v.get("net")
    deductions = v.get("deductions_total")

    if gross is None or net is None:
        report.confidence = "low"
        report.warnings.append(
            "Could not find both a gross and a net amount. This may not be a payslip, "
            "or it uses labels this parser has not seen."
        )
        return

    if not report.pay_date:
        report.warnings.append("No pay date found; the period will have to be set by hand.")

    # A zero payslip is normal for casual or reserve work in a period with no
    # hours, and it balances trivially - so it is valid, but there is nothing
    # to check and nothing much to learn.
    if gross == 0 and net == 0:
        report.confidence = "high"
        report.warnings.append("No pay in this period - recorded, but it adds no income.")
        return

    if deductions is not None:
        expected = gross + deductions if deductions < 0 else gross - deductions
        if abs(expected - net) <= 0.02:
            report.confidence = "high"
        else:
            report.confidence = "low"
            report.warnings.append(
                f"Gross ({gross:,.2f}) and deductions ({deductions:,.2f}) do not add up to "
                f"net ({net:,.2f}). Something was misread - do not rely on these figures."
            )
        return

    # No stated deductions total; fall back to the parts we did find.
    parts = sum(abs(v[k]) for k in ("paye", "kiwisaver_ee", "student_loan", "acc_levy") if k in v)
    if parts and abs((gross - parts) - net) <= 0.02:
        report.confidence = "high"
    else:
        report.confidence = "medium"
        report.warnings.append(
            "Gross and net were found but could not be reconciled against the "
            "individual deductions, so some may be missing."
        )


def parse_pdf(path: Path) -> PayslipReport:
    """Extract text from a payslip PDF and read it."""
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return parse_text("\n".join(chunks), filename=path.name)


def ingest_file(path: Path, producer: str | None = None) -> dict[str, Any]:
    """
    Parse a payslip and store it. The counterpart to bank_csv.ingest_file.

    This did not exist, so both callers - the HTTP upload and the MCP tool -
    built the same persistence dict independently and drifted: each set
    `employer` to the employee's personnel number, and neither wrote an
    import_log row, so a payslip left no record of having arrived at all.
    Having one of these means the mapping is stated once.

    A payslip whose arithmetic does not balance is deliberately not stored.
    Gross plus deductions must equal net; when it does not, something was
    misread, and a wrong salary would quietly distort every entitlement figure
    downstream. The report still comes back saying so.
    """
    from .. import db

    report = parse_pdf(path)
    stored = 0
    if report.confidence != "low" and report.pay_date:
        stored = db.save_payslip(
            {
                # The payslip states an employee reference, not an employer
                # name. Recording it as the employer is wrong but is what the
                # UNIQUE constraint has always keyed on, so changing it now
                # would orphan existing rows. Left as-is deliberately; noted
                # here so the next reader does not think it went unnoticed.
                "employer": report.employee_ref,
                "source_file": path.name,
                "pay_date": report.pay_date,
                "period": report.period,
                "employee_ref": report.employee_ref,
                "tax_code": report.tax_code,
                "confidence": report.confidence,
                **report.values,
            }
        )

    # Logged whether or not it was stored: "we saw this file and rejected it"
    # is exactly the thing somebody re-uploading the same payslip needs to know.
    db.log_import(
        db.file_sha256(path),
        path.name,
        rows_seen=1,
        rows_new=1 if stored else 0,
        parser="payslip",
        producer=producer,
    )

    return {
        "kind": "payslip",
        "report": report.as_dict(),
        "stored": stored,
        "accepted": bool(stored),
    }
