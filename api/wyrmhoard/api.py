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

from . import __version__, accounts, cache, categorise, config, db, facts, properties
from . import coach as coach_mod
from .analysis import cashflow, entitlements, mortgage, recurring
from .ingest import ingest_document, parse_csv, resolve_within, safe_upload_name


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the data directories and schema before serving the first request."""
    config.ensure_dirs()
    db.init()
    yield


app = FastAPI(
    title="Wyrmhoard",
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
    for unknown_fact in facts.unknown():
        todo.append(
            {
                "id": f"fact_{unknown_fact['fact']}",
                "label": unknown_fact["question"],
                "why": "Nothing in a bank export can answer this, and the tool "
                "would rather ask than assume. Answering it in household.yml "
                "removes this permanently - including answering 'no'.",
            }
        )
    for conflict in properties.summary()["conflicts"]:
        todo.append(
            {
                "id": "property",
                "label": "Record what your home is worth",
                "why": conflict,
            }
        )
    for gap in accounts.likely_missing_accounts()[:2]:
        todo.append(
            {
                "id": "missing_account",
                "label": f"Import {gap['account']} — {gap['transfers']} transfers in, "
                f"${gap['total']:,.0f}",
                "why": "Money arrives regularly from this account, so income and "
                "entitlement figures are only part of the picture until it is included.",
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


@app.get("/loans")
def loans() -> list[dict[str, Any]]:
    """
    Each loan's terms, worked out from its own transactions.

    Nothing here is typed in by the household: balance, repayment, cadence,
    interest rate and any upcoming repayment change are all derived from the
    account's own history.
    """
    return mortgage.infer_loans()


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
        # Tri-state, each with the evidence behind it. An empty `people` list
        # above cannot distinguish "no children" from "not filled in yet";
        # these can, and the difference changes what the coach says.
        "facts": facts.all_facts(),
    }


# --------------------------------------------------------------------------
# Properties
#
# The producer-facing surface. Anything outside Wyrmhoard that has worked out
# what a house is worth - a person, an agent, a scraper somebody chose to run -
# submits it here, saying who it is and where the figure came from. Wyrmhoard
# never goes and looks; see SECURITY.md and docs/PRODUCERS.md.
# --------------------------------------------------------------------------
@app.get("/properties")
def list_properties() -> dict[str, Any]:
    """Every property, its most recent valuation, and the provenance of each."""
    return properties.summary()


class PropertyValuationRequest(BaseModel):
    label: str
    value: float
    method: str
    observed_at: str
    producer: str
    source: str | None = None
    confidence: str | None = None
    note: str | None = None
    is_primary: bool = False


@app.post("/properties/valuations")
def record_valuation(req: PropertyValuationRequest) -> dict[str, Any]:
    """
    Record what a property is worth, and who says so.

    Appends rather than replaces: an older claim is never destroyed by a newer
    one, so a council rating value from three years ago and last month's
    appraisal both remain, each with its own date and basis.
    """
    try:
        result = properties.record_valuation(**req.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    cache.clear_all()
    return {**result, "properties": properties.summary()}


class PropertyLoanRequest(BaseModel):
    account: str
    linked: bool = True


@app.post("/properties/{property_id}/loans")
def link_property_loan(property_id: int, req: PropertyLoanRequest) -> dict[str, Any]:
    """Say which property a loan is secured on. Recorded, not yet computed from."""
    try:
        result = properties.link_loan(property_id, req.account, linked=req.linked)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    cache.clear_all()
    return {**result, "properties": properties.summary()}


@app.delete("/properties/{property_id}")
def remove_property(property_id: int) -> dict[str, Any]:
    try:
        result = properties.delete(property_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    cache.clear_all()
    return {**result, "properties": properties.summary()}


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------
@app.get("/accounts")
def list_accounts() -> list[dict[str, Any]]:
    """
    Every account, with its role and the evidence behind it.

    The evidence is returned so the dashboard can show *why* an account was
    called a loan, rather than asking somebody to trust a label.
    """
    return sorted(accounts.roles().values(), key=lambda a: str(a.get("account")))


class AccountRoleRequest(BaseModel):
    account: str
    role: str
    label: str | None = None


@app.post("/accounts")
def confirm_account_role(req: AccountRoleRequest) -> dict[str, Any]:
    """Confirm or correct an account's role. A human decision always wins."""
    if req.role not in accounts.ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(accounts.ROLES)}")
    db.set_account_role(req.account, req.role, req.label, confirmed=True)
    cache.clear_all()
    # Roles change what counts as a transfer, so the ledger needs re-reading.
    categorise.recategorise_all()
    return {"ok": True, "accounts": list_accounts()}


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


class FactRequest(BaseModel):
    fact: str
    # Explicitly typed rather than left as Any so FastAPI does not coerce a
    # posted "false" into the string it looks like. None means "unset it",
    # which is not the same answer as false.
    value: bool | str | None = None


@app.post("/household/facts")
def answer_fact(req: FactRequest) -> dict[str, Any]:
    """
    Answer one of the questions no bank export can settle.

    These went into the setup checklist before there was any way to respond to
    them except editing household.yml by hand, which made "there are no
    children here" oddly difficult to say - and left the tool asking forever.

    The answer is stored in the ledger, not written back into household.yml.
    That file is mostly comments explaining what each field is for, and
    answering a question by re-serialising the file would strip them.
    household.yml still works and still wins if both are set.
    """
    try:
        return facts.answer(req.fact, req.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class LearnRequest(BaseModel):
    match: str
    category: str


@app.post("/learn")
def learn_rule(req: LearnRequest) -> dict[str, Any]:
    """
    Teach a merchant, so the same decision is not made again next month.

    The difference between this and POST /categorise is how long the answer
    lasts. An override fixes the transactions it names and nothing else, so
    the same shop arrives unrecognised again with the next statement. A rule
    is matched against everything, including transactions that do not exist
    yet.

    Agents have had this since the MCP server gained `teach_category`; the
    dashboard could only ever fix rows one batch at a time, which made the
    person sitting in front of the tool worse off than the model driving it.

    Both write paths run through categorise.learn(), so the validation, the
    file format and the reporting of moved spending are the same either way.
    """
    try:
        return categorise.learn(req.match, req.category, producer="human:dashboard")
    except ValueError as exc:
        # The messages are written to be read by whoever asked, so pass them
        # through rather than replacing them with something generic.
        raise HTTPException(400, str(exc)) from exc


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
    """
    One drop zone for everything the household has.

    Working out which kind of document arrived is the tool's job, not theirs,
    and that decision now lives in one place - `ingest.ingest_document` - so
    the browser, the CLI and an agent all reach the same conclusion about the
    same file.
    """
    raw = await file.read()
    name = safe_upload_name(file.filename)

    folder = (
        config.DATA_DIR / ("payslips" if name.lower().endswith(".pdf") else "inbox")
    ).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        # Belt and braces: even with the name sanitised, confirm the final path
        # really is inside the folder before writing anything.
        dest = resolve_within(folder, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    dest.write_bytes(raw)

    try:
        result = ingest_document(dest, producer="human:dashboard")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if result["kind"] == "payslip":
        cache.clear_all()
        return result
    return {**result, "coverage": categorise.recategorise_all()}


@app.get("/payslips")
def list_payslips() -> dict[str, Any]:
    from .analysis import income

    return {"payslips": db.payslips(), "income": income.from_payslips()}


@app.delete("/payslips/{payslip_id}")
def remove_payslip(payslip_id: int) -> dict[str, Any]:
    removed = db.delete_payslip(payslip_id)
    if not removed:
        raise HTTPException(404, "No such payslip.")
    cache.clear_all()
    return {"removed": removed}


# --------------------------------------------------------------------------
# Data management
# --------------------------------------------------------------------------
@app.get("/imports")
def list_imports() -> list[dict[str, Any]]:
    """Every file imported, and how much of it is still in the ledger."""
    return db.imports()


@app.delete("/imports/{filename}")
def undo_import(filename: str) -> dict[str, Any]:
    """
    Lift one import back out - the wrong export, a duplicate, somebody else's
    account. Everything else stays exactly as it was.
    """
    safe = safe_upload_name(filename)
    known = {i["filename"] for i in db.imports()}
    if safe not in known:
        raise HTTPException(404, f"No import named '{safe}'.")

    result = db.delete_import(safe)
    cache.clear_all()
    result["coverage"] = categorise.recategorise_all()
    return result


@app.get("/backups")
def list_backups() -> dict[str, Any]:
    return {
        "backups": db.list_backups(config.DATA_DIR / "backups"),
        "location": str(config.DATA_DIR / "backups"),
    }


@app.post("/backups")
def make_backup() -> dict[str, Any]:
    """
    Snapshot the ledger.

    This is the whole financial history in one file. Copy it somewhere off
    this machine too - a backup that only exists on the disk that failed is
    not a backup.
    """
    path = db.backup(config.DATA_DIR / "backups")
    return {
        "created": path.name,
        "path": str(path),
        "size_kb": round(path.stat().st_size / 1024, 1),
    }


class RestoreRequest(BaseModel):
    name: str


@app.post("/backups/restore")
def restore_backup(req: RestoreRequest) -> dict[str, Any]:
    """Replace the ledger with a backup. The displaced one is saved first."""
    backups_dir = (config.DATA_DIR / "backups").resolve()
    target = (backups_dir / safe_upload_name(req.name)).resolve()
    if not target.is_relative_to(backups_dir) or not target.exists():
        raise HTTPException(404, f"No backup named '{req.name}'.")

    result = db.restore(target)
    cache.clear_all()
    result["coverage"] = categorise.recategorise_all()
    return result


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
