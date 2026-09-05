"""
The family meeting report.

This is the artefact the household actually sits down with, so it is shaped by
the meeting rather than by the data:

  - It opens with one number, because a meeting that starts with a table
    becomes a meeting about the table.
  - Wins come before problems. A report that reads as a list of failures gets
    held once and never again.
  - There is a page for the children that is about a shared goal, not about
    scarcity. Kids should leave the meeting feeling part of a team with a
    plan, not anxious about money. Eight-year-olds do not need a cash-flow
    statement; they need to know the family is going somewhere together.
  - It prints. Screens invite scrolling and phone-checking; paper on a table
    keeps six people looking at the same thing.

Everything is inlined - no CDN, no fonts to fetch, no JavaScript. It opens
from a USB stick in five years.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import coach as coach_mod
from . import config, db
from .analysis import cashflow, entitlements, mortgage, recurring

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _money(v: Any, dp: int = 0) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return "—"


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v):.0f}%"


def gather() -> dict[str, Any]:
    """Everything the report needs, assembled once."""
    hh = config.household()
    rt = config.rates()
    s = cashflow.summary()
    c = coach_mod.summary()
    rec = recurring.summary()
    ent = entitlements.estimate()
    loan = mortgage.from_household(hh)
    snaps = db.snapshots()

    typ = s["typical_month"]
    cats = s["by_category"]

    # The headline: one number, framed as a monthly position.
    net = typ.get("net_median") if typ.get("available") else None
    if net is None:
        headline = {
            "state": "unknown",
            "number": None,
            "label": "Not enough data yet",
            "sub": "Import a full year of bank exports to see the picture.",
        }
    elif net < 0:
        headline = {
            "state": "behind",
            "number": _money(abs(net)),
            "label": "short each month",
            "sub": f"That is {_money(abs(net) * 12)} over a year.",
        }
    else:
        headline = {
            "state": "ahead",
            "number": _money(net),
            "label": "left over each month",
            "sub": f"That is {_money(net * 12)} over a year, if it gets allocated on purpose.",
        }

    # Spending groups, ordered, with shares for the bar.
    groups: list[dict[str, Any]] = []
    if typ.get("available"):
        total = sum(v for v in typ["by_group"].values() if v > 0) or 1
        friendly = {
            "essential": "Essentials — food, power, fuel, health",
            "commitment": "Commitments — mortgage, insurance, KiwiSaver",
            "sinking": "Lumpy bills — rates, rego, Christmas",
            "discretionary": "Choices — takeaways, subscriptions, shopping",
            "unknown": "Not yet categorised",
        }
        for key, amount in sorted(typ["by_group"].items(), key=lambda kv: kv[1], reverse=True):
            if amount <= 0:
                continue
            groups.append(
                {
                    "key": key,
                    "label": friendly.get(key, key.title()),
                    "amount": amount,
                    "amount_fmt": _money(amount),
                    "share": round(100 * amount / total, 1),
                }
            )

    findings = c["findings"]
    wins = [f for f in findings if f["severity"] == "win"]
    actions = [f for f in findings if f["severity"] in ("critical", "high", "medium")][:5]

    # The kids' page hangs off the first active cash goal.
    goal = None
    plan = c["plan"]
    buffer_step = next((p for p in plan if "buffer" in p["title"].lower()), None)
    if buffer_step:
        goal = {
            "title": "Our family safety net",
            "explain": (
                "A safety net is money we keep for surprises — like the car needing "
                "fixing. When we have one, surprises stop being scary. They are just "
                "surprises."
            ),
            "progress": buffer_step.get("progress_pct", 0) or 0,
            "target": buffer_step["title"].replace("Build a starter buffer of ", ""),
        }

    # Progress against the last snapshot: the whole point of meeting again.
    progress = None
    if len(snaps) >= 2:
        prev, curr = snaps[-2], snaps[-1]

        def delta(key: str) -> dict[str, Any] | None:
            a, b = prev["metrics"].get(key), curr["metrics"].get(key)
            if a is None or b is None:
                return None
            return {"from": a, "to": b, "change": round(b - a, 2)}

        progress = {
            "since": prev["taken_on"],
            "net": delta("net_median"),
            "spend": delta("spend_median"),
            "cash": delta("cash"),
            "runway": delta("runway_weeks"),
        }

    return {
        "generated": date.today(),
        # Built by hand rather than with %-d, which is glibc-only and would
        # break the day anyone runs this outside a Linux container.
        "generated_fmt": (f"{date.today().day} {date.today().strftime('%B %Y')}"),
        "household": hh,
        "household_name": hh.name,
        "children_ages": hh.children_ages(),
        "headline": headline,
        "typical": typ,
        "groups": groups,
        "categories": cats[:12],
        "findings": findings,
        "actions": actions,
        "wins": wins,
        "plan": plan,
        "recurring": rec,
        "entitlements": ent,
        "checklist": entitlements.checklist(),
        "mortgage": loan,
        "cash": s["cash"],
        "leaks": s["small_leaks"],
        "trend": s["trend"],
        "coverage": s["coverage"],
        "stats": s["stats"],
        "monthly": s["monthly"][-14:],
        "goal": goal,
        "progress": progress,
        "snapshots": snaps,
        "rates_unverified": rt.unverified_blocks,
        "disclaimer": c["disclaimer"],
    }


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["money"] = _money
    env.filters["pct"] = _pct
    return env


def build_report(outdir: Path | None = None) -> Path:
    outdir = outdir or config.REPORT_DIR
    outdir.mkdir(parents=True, exist_ok=True)

    ctx = gather()
    html = _env().get_template("report.html.jinja").render(**ctx)

    path = outdir / f"family-meeting-{date.today().isoformat()}.html"
    path.write_text(html, encoding="utf-8")

    # A stable filename too, so bookmarks and shortcuts keep working.
    (outdir / "latest.html").write_text(html, encoding="utf-8")
    return path
