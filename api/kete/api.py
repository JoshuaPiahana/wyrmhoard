"""
HTTP API.

Deliberately thin: every endpoint is a direct call into the analysis modules,
returning plain JSON. The frontend holds no business logic, which is what
makes a design swap - a Stitch-generated interface, say - a matter of
rewriting markup rather than re-implementing maths.

Bound to loopback only in docker-compose. Nothing here authenticates, because
nothing here should ever be reachable from outside the machine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__, cache, categorise, config, db
from . import coach as coach_mod
from .analysis import cashflow, entitlements, mortgage, recurring
from .ingest import ingest_file, parse_csv


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the data directories and schema before serving the first request."""
    config.ensure_dirs()
    db.init()
    yield


app = FastAPI(
    title="kete",
    version=__version__,
    description="Household finance, computed locally.",
    lifespan=lifespan,
)

# The frontend is served from a different port by nginx in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------
@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "stats": db.stats()}


@app.get("/setup")
def setup_state() -> dict[str, Any]:
    """What the household still needs to do before the numbers mean anything."""
    hh = config.household()
    rt = config.rates()
    stats = db.stats()
    cov = categorise.coverage()
    real_config = (config.CONFIG_DIR / "household.yml").exists()

    todo = []
    if not real_config:
        todo.append(
            {
                "id": "household",
                "label": "Create config/household.yml from the example",
                "why": "Ages, mortgage and income drive the entitlement and payoff maths.",
            }
        )
    if not stats["transactions"]:
        todo.append(
            {
                "id": "import",
                "label": "Import bank CSV exports into data/inbox/",
                "why": "Everything else is derived from these.",
            }
        )
    if stats["transactions"] and not cov["trustworthy"]:
        todo.append(
            {
                "id": "categorise",
                "label": f"Categorisation is {cov['categorised_pct']}% - aim for 90%+",
                "why": "Below 90% the category charts are decorative rather than useful.",
            }
        )
    if rt.unverified_blocks:
        todo.append(
            {
                "id": "rates",
                "label": f"Verify NZ rates ({', '.join(rt.unverified_blocks)})",
                "why": "Entitlement figures stay labelled as rough estimates until checked against IRD.",
            }
        )

    return {
        "household_configured": real_config,
        "household_name": hh.name,
        "transactions": stats["transactions"],
        "coverage": cov,
        "rates_verified": not rt.any_unverified,
        "todo": todo,
        "ready": bool(stats["transactions"]) and cov["trustworthy"],
    }


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
@app.get("/summary")
def summary() -> dict[str, Any]:
    return cashflow.summary()


@app.get("/monthly")
def monthly() -> list[dict[str, Any]]:
    return cashflow.monthly()


@app.get("/categories")
def categories(months: int = 6) -> list[dict[str, Any]]:
    return cashflow.by_category(months=months)


@app.get("/recurring")
def recurring_payments() -> dict[str, Any]:
    return recurring.summary()


@app.get("/entitlements")
def entitlement_estimate() -> dict[str, Any]:
    return {
        "estimate": entitlements.estimate(),
        "checklist": entitlements.checklist(),
    }


@app.get("/mortgage")
def mortgage_view() -> dict[str, Any]:
    return mortgage.from_household(config.household())


@app.get("/coach")
def coach() -> dict[str, Any]:
    return coach_mod.summary()


@app.get("/household")
def household() -> dict[str, Any]:
    hh = config.household()
    return {
        "name": hh.name,
        "council": hh.council,
        "people": [
            {"name": p.name, "role": p.role, "age": p.age_on(date.today())} for p in hh.people
        ],
        "children_ages": hh.children_ages(),
        "goals": hh.goals,
        "currency": hh.currency,
        "configured": (config.CONFIG_DIR / "household.yml").exists(),
    }


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------
@app.get("/transactions")
def transactions(
    month: str | None = None,
    category: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    rows = db.all_transactions()
    if month:
        rows = [r for r in rows if r["date"].startswith(month)]
    if category:
        rows = [r for r in rows if r["category"] == category]
    return rows[-limit:]


@app.get("/uncategorised")
def uncategorised(limit: int = 25) -> list[dict[str, Any]]:
    return categorise.top_uncategorised(limit=limit)


class OverrideRequest(BaseModel):
    fingerprints: list[str]
    category: str
    note: str | None = None


@app.post("/categorise")
def apply_override(req: OverrideRequest) -> dict[str, Any]:
    """Correct a categorisation. Manual decisions always beat rules."""
    if req.category not in categorise.rule_index() and req.category != "uncategorised":
        raise HTTPException(400, f"Unknown category '{req.category}'.")
    for fp in req.fingerprints:
        db.set_override(fp, req.category, req.note)
    return categorise.recategorise_all()


@app.post("/recategorise")
def recategorise() -> dict[str, Any]:
    return categorise.recategorise_all()


@app.get("/rules")
def rules() -> list[dict[str, Any]]:
    return [
        {"key": r.key, "label": r.label, "group": r.group, "flag": r.flag}
        for r in categorise.compiled_rules()
    ]


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
@app.post("/preview")
async def preview(file: UploadFile = File(...)) -> dict[str, Any]:
    """
    Parse without saving.

    Lets the human confirm the sniffer read the file correctly before any of it
    reaches the ledger - which is the difference between a mis-parsed export
    being a five-second annoyance and a month of wrong charts.
    """
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    rows, report = parse_csv(text, filename=file.filename or "upload.csv")
    return {"report": report.as_dict(), "sample_rows": rows[:10]}


@app.post("/import")
async def import_csv(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    inbox = config.DATA_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    dest = inbox / (file.filename or "upload.csv")
    dest.write_bytes(raw)

    report = ingest_file(dest)
    coverage = categorise.recategorise_all()
    return {"report": report.as_dict(), "coverage": coverage}


@app.post("/import-inbox")
def import_inbox() -> dict[str, Any]:
    from .ingest import ingest_inbox

    reports = [r.as_dict() for r in ingest_inbox(config.DATA_DIR / "inbox")]
    return {"reports": reports, "coverage": categorise.recategorise_all()}


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------
class SnapshotRequest(BaseModel):
    note: str | None = None


@app.get("/snapshots")
def snapshots() -> list[dict[str, Any]]:
    return db.snapshots()


@app.post("/snapshots")
def take_snapshot(req: SnapshotRequest) -> dict[str, Any]:
    s = cashflow.summary()
    typ = s["typical_month"]
    metrics = {
        "net_median": typ.get("net_median"),
        "income_median": typ.get("income_median"),
        "spend_median": typ.get("spend_median"),
        "savings_rate_pct": typ.get("savings_rate_pct"),
        "essentials": typ.get("essentials_total"),
        "discretionary": typ.get("discretionary_total"),
        "cash": s["cash"].get("total"),
        "runway_weeks": s["cash"].get("runway_weeks"),
        "categorised_pct": s["coverage"]["categorised_pct"],
    }
    taken = db.save_snapshot(metrics, note=req.note)
    return {"taken_on": taken, "metrics": metrics}


class BalanceRequest(BaseModel):
    as_at: str
    label: str
    kind: str
    amount: float
    note: str | None = None


@app.get("/balances")
def balances() -> list[dict[str, Any]]:
    return db.manual_balances()


@app.post("/balances")
def add_balance(req: BalanceRequest) -> dict[str, Any]:
    if req.kind not in {"asset", "liability"}:
        raise HTTPException(400, "kind must be 'asset' or 'liability'")
    db.set_manual_balance(req.as_at, req.label, req.kind, req.amount, req.note)
    return {"ok": True}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
@app.post("/report")
def make_report() -> dict[str, Any]:
    from .report import build_report

    path = build_report()
    return {"path": str(path), "filename": Path(path).name}


@app.get("/report/latest")
def latest_report():
    reports = sorted(config.REPORT_DIR.glob("family-meeting-*.html"))
    if not reports:
        raise HTTPException(404, "No report generated yet.")
    return FileResponse(reports[-1], media_type="text/html")


@app.post("/reload")
def reload_config() -> dict[str, Any]:
    """
    Pick up edits to the YAML files without restarting the container.

    The analysis caches key on the ledger file, so a config-only change - a new
    rule, a verified rate - would otherwise serve stale results. Clear them
    explicitly here.
    """
    config.reload()
    categorise.compiled_rules.cache_clear()
    cache.clear_all()
    return {"ok": True, "coverage": categorise.recategorise_all()}


@app.exception_handler(Exception)
async def unhandled(request, exc):  # pragma: no cover
    return JSONResponse(status_code=500, content={"error": str(exc)})
