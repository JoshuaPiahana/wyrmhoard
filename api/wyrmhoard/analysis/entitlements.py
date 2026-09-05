"""
New Zealand household entitlements.

The most valuable thing this module does is NOT the arithmetic. It is the
comparison: what did IRD and MSD actually pay into this household's bank
account over the last twelve months, versus what a household of this shape
would normally receive?

That framing matters because the observed number comes from the household's
own bank data and is therefore certain, while the expected number comes from
rate constants that may be a year out of date. "You received nothing from IRD
in twelve months, and you have dependent children" is a solid, actionable
finding even if every rate in nz_rates.yml is wrong.

So: observation first, estimate second, and the estimate is always labelled
an estimate and always accompanied by a link to IRD's own calculator.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from .. import cache, config, facts
from .cashflow import complete_months, frame


def observed_support(months: int = 12) -> dict[str, Any]:
    """What actually arrived from IRD and Work and Income. Ground truth."""
    df = frame()
    if df.empty:
        return {"available": False, "reason": "No transactions imported yet."}

    # See the note in recurring.detect() - DateOffset is both more accurate
    # and free of the pandas 2.2 generic-unit deprecation.
    cutoff = df["date"].max() - pd.DateOffset(months=months)
    sub = df[(df["date"] >= cutoff) & (df["amount"] > 0)]

    ird = sub[sub["category"] == "income_ird"]
    msd = sub[sub["category"] == "income_msd"]
    span_days = max(1, (df["date"].max() - max(cutoff, df["date"].min())).days)
    scale = 365.25 / span_days

    ird_total = float(ird["amount"].sum())
    msd_total = float(msd["amount"].sum())

    return {
        "available": True,
        "window_days": int(span_days),
        "ird_total": round(ird_total, 2),
        "ird_annualised": round(ird_total * scale, 2),
        "ird_payments": len(ird),
        "ird_last_seen": ird["date"].max().date().isoformat() if len(ird) else None,
        "msd_total": round(msd_total, 2),
        "msd_annualised": round(msd_total * scale, 2),
        "msd_payments": len(msd),
        "receiving_anything": bool(len(ird) or len(msd)),
    }


def observed_income(months: int = 12) -> dict[str, Any]:
    """Household income from the bank, used as the basis for abatement."""
    df = frame()
    if df.empty:
        return {"available": False}

    good = complete_months(df)
    if not good:
        return {"available": False, "reason": "No complete months yet."}
    recent = good[-months:]
    sub = df[df["month"].isin(recent) & df["is_income"]]

    # Government transfers are not "income" for abatement purposes - abatement
    # is assessed on what the household earns, so counting the tax credit
    # against itself would understate entitlement.
    earned = sub[~sub["category"].isin(["income_ird", "income_msd"])]
    total = float(earned["amount"].sum())
    annualised = total / len(recent) * 12 if recent else 0.0

    return {
        "available": True,
        "months_used": len(recent),
        "net_income_annualised": round(annualised, 2),
        "note": "This is NET (after-tax) income as it lands in the bank. "
        "Working for Families abates on BEFORE-tax family income, so the "
        "estimate below uses a grossed-up figure or your declared salary.",
    }


def _gross_estimate(hh, net_annual: float | None) -> tuple[float | None, str]:
    """
    The best available gross figure, in descending order of trustworthiness.

    Payslips first: abatement is assessed on gross income, and a payslip
    states it exactly where everything else is inference.
    """
    from .income import from_payslips

    payslips = from_payslips()
    if payslips.get("available") and payslips.get("gross_annual"):
        return float(payslips["gross_annual"]), "from imported payslips"

    declared = hh.gross_income_declared
    if declared:
        return float(declared), "declared in household.yml"
    if net_annual:
        # A rough gross-up. Deliberately crude and labelled as such - the real
        # figure belongs on a payslip, and the tool asks for one.
        return round(net_annual / 0.78, 2), "estimated from bank deposits (rough)"
    return None, "unknown"


@cache.by_ledger
def estimate(as_at: date | None = None) -> dict[str, Any]:
    """
    A rough Working for Families and Best Start estimate.

    Never present this as an entitlement. It is a prompt to go and check.
    """
    as_at = as_at or date.today()
    hh = config.household()
    rt = config.rates()

    # Region gate. Showing a household outside New Zealand what a NZ family
    # would receive would be worse than showing nothing, so the module says
    # plainly that it does not cover their country rather than guessing.
    if not hh.region_supported:
        return {
            "available": False,
            "is_estimate": True,
            "unsupported_country": hh.country,
            "reason": (
                f"Entitlement estimates are only implemented for New Zealand, "
                f"and this household is configured as {hh.country}. Everything "
                "else in the tool works normally - only this page is skipped."
            ),
            "how_to_add": (
                "Add a rates file for your country modelled on config/nz_rates.yml "
                "and extend Household.region_supported."
            ),
        }

    wff = rt.block("working_for_families")
    bs = rt.block("best_start")
    verified = rt.is_verified("working_for_families") and rt.is_verified("best_start")

    ages = hh.children_ages(as_at)
    n_children = len(ages)

    inc = observed_income()
    gross, gross_source = _gross_estimate(
        hh, inc.get("net_income_annualised") if inc.get("available") else None
    )

    result: dict[str, Any] = {
        "is_estimate": True,
        "rates_verified": verified,
        "children": n_children,
        "child_ages": ages,
        "gross_income_used": gross,
        "gross_income_source": gross_source,
        "calculator_url": wff.get("calculator") or "https://www.ird.govt.nz/working-for-families",
        "caveats": [
            "This is an estimate from locally-stored rate constants, not an "
            "entitlement calculation. IRD's own calculator is authoritative.",
            "It ignores shared care, child support, and any income the tool " "cannot see.",
        ],
    }

    if not verified:
        result["caveats"].insert(
            0,
            "The rates in config/nz_rates.yml have NOT been verified against "
            "IRD for the current tax year, so treat the figures below as a "
            "rough order of magnitude only.",
        )

    if n_children == 0:
        # Two very different silences. A household that has told us there are
        # no children should never be nagged about a credit for children; a
        # household that has told us nothing should be asked, because Working
        # for Families is usually the largest sum this tool can find and
        # missing it costs far more than an unnecessary question.
        known_childless = facts.has_children()["value"] is False
        result["available"] = False
        result["applicable"] = not known_childless
        result["reason"] = (
            "This household has no children, so Working for Families does not apply."
            if known_childless
            else (
                "No children recorded yet. If there are children here, add them to "
                "household.yml with their birth dates - some credits turn on an "
                "exact age, and this is usually the largest entitlement available."
            )
        )
        return result

    if gross is None:
        result["available"] = False
        result["reason"] = (
            "Need either a gross annual salary in household.yml or enough bank "
            "data to infer income."
        )
        return result

    # --- Family Tax Credit --------------------------------------------------
    ftc_w = wff.get("family_tax_credit_weekly", {}) or {}
    eldest = float(ftc_w.get("eldest_child", 0))
    subsequent = float(ftc_w.get("subsequent_child", 0))
    ftc_annual = (eldest + subsequent * max(0, n_children - 1)) * 52

    # --- In-Work Tax Credit -------------------------------------------------
    iwtc_w = wff.get("in_work_tax_credit_weekly", {}) or {}
    iwtc_base = float(iwtc_w.get("base_up_to_three_children", 0))
    iwtc_extra = float(iwtc_w.get("each_additional_child", 0))
    iwtc_annual = (iwtc_base + iwtc_extra * max(0, n_children - 3)) * 52

    # --- Abatement ----------------------------------------------------------
    ab = wff.get("abatement", {}) or {}
    threshold = float(ab.get("threshold_annual", 0))
    rate_pct = float(ab.get("rate_pct", 0))
    over = max(0.0, gross - threshold)
    abatement = over * rate_pct / 100.0

    entitlement_before = ftc_annual + iwtc_annual
    wff_estimate = max(0.0, entitlement_before - abatement)

    # --- Best Start ---------------------------------------------------------
    bs_weekly = float(bs.get("weekly_rate", 0))
    bs_universal_until = int(bs.get("universal_until_age", 1))
    bs_ab = bs.get("abatement", {}) or {}
    bs_threshold = float(bs_ab.get("threshold_annual", 0))
    bs_rate = float(bs_ab.get("rate_pct", 0))

    best_start_total = 0.0
    best_start_detail = []
    for age in ages:
        if age < bs_universal_until:
            amount = bs_weekly * 52
            note = "universal - not income tested under 1"
        elif age < 3:
            gross_bs = bs_weekly * 52
            bs_abate = max(0.0, gross - bs_threshold) * bs_rate / 100.0
            amount = max(0.0, gross_bs - bs_abate)
            note = "income tested"
        else:
            continue
        best_start_total += amount
        best_start_detail.append({"child_age": age, "annual": round(amount, 2), "note": note})

    total = wff_estimate + best_start_total
    obs = observed_support()

    result.update(
        {
            "available": True,
            "family_tax_credit_annual": round(ftc_annual, 2),
            "in_work_tax_credit_annual": round(iwtc_annual, 2),
            "abatement_applied": round(abatement, 2),
            "abatement_threshold": threshold,
            "working_for_families_estimate": round(wff_estimate, 2),
            "best_start_estimate": round(best_start_total, 2),
            "best_start_detail": best_start_detail,
            "total_estimate_annual": round(total, 2),
            "total_estimate_weekly": round(total / 52, 2),
            "observed": obs,
        }
    )

    # --- The finding --------------------------------------------------------
    # Expressed as a gap, with honest language about what the gap means.
    if obs.get("available"):
        received = obs["ird_annualised"] + obs["msd_annualised"]
        gap = total - received
        result["received_annualised"] = round(received, 2)
        result["gap"] = round(gap, 2)

        # If money clearly arrives from a household account we have not been
        # given, entitlements may well be landing there. Saying "you are
        # missing out" would then be a confident conclusion drawn over a known
        # hole in the data - and somebody could act on it.
        from .. import accounts as accounts_mod

        missing = accounts_mod.likely_missing_accounts()
        result["missing_accounts"] = missing
        result["view_is_incomplete"] = bool(missing)

        if missing:
            where = missing[0]["account"]
            result["headline"] = (
                f"This cannot be answered from the accounts imported so far. "
                f"Money arrives regularly from {where}, which is not among them - "
                f"so any family payments going into that account are invisible "
                f"here, and the ${received:,.0f} a year showing from IRD may "
                f"be only part of what the household receives."
            )
            result["severity"] = "low"
            result["caveats"].insert(
                0,
                "Import the missing account before drawing any conclusion from "
                "this page, or confirm directly with IRD.",
            )
        elif not obs["receiving_anything"] and total > 500:
            result["headline"] = (
                f"No payments from IRD or Work and Income appear in the last "
                f"12 months of bank data, but a household with {n_children} "
                f"children on this income would normally receive something. "
                f"This is worth an hour of your time to check."
            )
            result["severity"] = "high"
        elif gap > 2000:
            result["headline"] = (
                f"You received about ${received:,.0f} a year, but the estimate "
                f"for a household of this shape is around ${total:,.0f}. A gap "
                f"this size usually means IRD holds out-of-date income or "
                f"family details. Worth checking in myIR."
            )
            result["severity"] = "medium"
        elif gap < -2000:
            result["headline"] = (
                f"You received about ${received:,.0f} a year, which is more "
                f"than the ${total:,.0f} estimate. If your income has risen "
                f"since IRD last had it, you may be accruing an end-of-year "
                f"bill. Worth updating your details before it compounds."
            )
            result["severity"] = "medium"
        else:
            result["headline"] = "What you receive is broadly in line with the estimate."
            result["severity"] = "low"

    return result


def checklist() -> list[dict[str, Any]]:
    """Support worth checking that the tool cannot compute from bank data."""
    rt = config.rates()
    block = rt.block("other_support_to_check")
    return block.get("items", []) or []
