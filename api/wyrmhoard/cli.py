"""
Command line interface.

The monthly routine is meant to be two commands:

    docker compose run --rm api python -m wyrmhoard.cli ingest
    docker compose run --rm api python -m wyrmhoard.cli report

Anything more elaborate than that will not survive contact with a busy month.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import categorise, config, db, samples
from .analysis import cashflow, entitlements, mortgage, recurring

app = typer.Typer(add_completion=False, help="Household finance toolkit.")
console = Console()


def _money(v: float | None) -> str:
    if v is None:
        return "-"
    return f"${v:,.2f}"


@app.command()
def init() -> None:
    """Create the database and directories."""
    config.ensure_dirs()
    db.init()
    console.print(f"[green]Ready.[/green] Ledger at {db.DB_PATH}")


@app.command()
def sample(months: int = 14) -> None:
    """Generate synthetic bank data so you can see the tool work."""
    config.ensure_dirs()
    path = config.DATA_DIR / "samples" / "kiwibank_sample.csv"
    samples.write(path, months=months)
    console.print(f"[green]Wrote[/green] {months} months of synthetic data to {path}")
    console.print("Import it with: [bold]python -m wyrmhoard.cli ingest --sample[/bold]")


@app.command()
def ingest(
    path: str = typer.Argument(None, help="A CSV file, or omit to read data/inbox/"),
    sample: bool = typer.Option(False, "--sample", help="Import the synthetic sample."),
) -> None:
    """Import bank CSV exports. Safe to re-run; duplicates are ignored."""
    config.ensure_dirs()
    db.init()
    from .ingest import ingest_file, ingest_inbox

    if sample:
        target = config.DATA_DIR / "samples" / "kiwibank_sample.csv"
        if not target.exists():
            samples.write(target)
        reports = [ingest_file(target, default_account="Everyday")]
    elif path:
        reports = [ingest_file(Path(path))]
    else:
        reports = ingest_inbox(config.DATA_DIR / "inbox")

    if not reports:
        console.print("[yellow]No CSV files found in data/inbox/[/yellow]")
        raise typer.Exit(0)

    for r in reports:
        colour = {"high": "green", "medium": "yellow", "low": "red"}.get(r.confidence, "white")
        console.print(
            f"\n[bold]{r.filename}[/bold]  "
            f"parsed [bold]{r.rows_parsed}[/bold]/{r.rows_seen} rows  "
            f"confidence [{colour}]{r.confidence}[/{colour}]"
        )
        console.print(
            f"  dates {r.date_range[0]} to {r.date_range[1]}   "
            f"columns {r.column_map}   header={r.had_header}"
        )
        for w in r.warnings:
            console.print(f"  [yellow]! {w}[/yellow]")

    result = categorise.recategorise_all()
    console.print(
        f"\nCategorised [bold]{result['categorised_pct']}%[/bold] of spending "
        f"({result['uncategorised_count']} transactions still unknown)."
    )
    if not result["trustworthy"]:
        console.print(
            "[yellow]Coverage is below 90%. Run `review` and fix the biggest "
            "unknowns before trusting the category charts.[/yellow]"
        )


@app.command()
def recategorise() -> None:
    """Re-apply rules after editing config/rules.yml."""
    result = categorise.recategorise_all()
    console.print(f"Categorised {result['categorised_pct']}% of spending.")


@app.command()
def review(limit: int = 20) -> None:
    """Show the biggest uncategorised spending, largest first."""
    rows = categorise.top_uncategorised(limit=limit)
    if not rows:
        console.print("[green]Nothing uncategorised. Good.[/green]")
        return
    table = Table(title="Biggest unknowns - fix these first")
    table.add_column("Memo")
    table.add_column("Count", justify="right")
    table.add_column("Total", justify="right")
    for r in rows:
        table.add_row(r["memo"], str(r["count"]), _money(r["total"]))
    console.print(table)
    console.print(
        "\nAdd a matching pattern to [bold]config/rules.yml[/bold], then run "
        "[bold]recategorise[/bold]."
    )


@app.command()
def summary() -> None:
    """The headline numbers."""
    s = cashflow.summary()
    stats, cov, typ = s["stats"], s["coverage"], s["typical_month"]

    console.print(
        f"\n[bold]{stats['transactions']}[/bold] transactions, "
        f"{stats['first_date']} to {stats['last_date']}, "
        f"{cov['categorised_pct']}% categorised"
    )

    if not typ.get("available"):
        console.print(f"[yellow]{typ.get('reason')}[/yellow]")
        return

    t = Table(title=f"A typical month (median of {typ['month_count']} complete months)")
    t.add_column("")
    t.add_column("Amount", justify="right")
    t.add_row("Income", _money(typ["income_median"]))
    t.add_row("Spending", _money(typ["spend_median"]))
    net = typ["net_median"]
    t.add_row(
        "[bold]Left over[/bold]",
        f"[{'green' if net >= 0 else 'red'}]{_money(net)}[/]",
    )
    console.print(t)

    g = Table(title="Where it goes")
    g.add_column("Group")
    g.add_column("Per month", justify="right")
    for name, value in sorted(typ["by_group"].items(), key=lambda kv: kv[1], reverse=True):
        if value:
            g.add_row(name.title(), _money(value))
    console.print(g)

    leaks = s["small_leaks"]
    if leaks.get("available"):
        console.print(
            f"\nSmall spending under ${leaks['threshold']:.0f}: "
            f"[bold]{_money(leaks['per_month'])}/month[/bold] "
            f"= {_money(leaks['per_year'])}/year across {leaks['count']} transactions."
        )

    cash = s["cash"]
    if cash.get("runway_weeks") is not None:
        console.print(
            f"Cash on hand {_money(cash['total'])} - about "
            f"[bold]{cash['runway_weeks']} weeks[/bold] of essentials."
        )

    rec = recurring.summary()
    console.print(
        f"Recurring commitments detected: [bold]{rec['count']}[/bold], "
        f"{_money(rec['total_monthly'])}/month ({_money(rec['total_annual'])}/year)."
    )

    ent = entitlements.estimate()
    if ent.get("headline"):
        console.print(f"\n[bold yellow]Entitlements:[/bold yellow] {ent['headline']}")


@app.command()
def snapshot(note: str = typer.Option(None, help="What changed this month?")) -> None:
    """Freeze this month's numbers so progress becomes measurable."""
    s = cashflow.summary()
    typ = s["typical_month"]
    metrics = {
        "net_median": typ.get("net_median"),
        "spend_median": typ.get("spend_median"),
        "income_median": typ.get("income_median"),
        "savings_rate_pct": typ.get("savings_rate_pct"),
        "essentials": typ.get("essentials_total"),
        "discretionary": typ.get("discretionary_total"),
        "cash": s["cash"].get("total"),
        "runway_weeks": s["cash"].get("runway_weeks"),
        "categorised_pct": s["coverage"]["categorised_pct"],
    }
    taken = db.save_snapshot(metrics, note=note)
    console.print(f"[green]Snapshot saved[/green] for {taken}.")


@app.command()
def report(open_after: bool = False) -> None:
    """Build the family meeting report."""
    from .report import build_report

    path = build_report()
    console.print(f"[green]Report written to[/green] {path}")


@app.command()
def loan() -> None:
    """Mortgage payoff scenarios."""
    result = mortgage.from_household(config.household())
    if not result.get("available"):
        console.print(f"[yellow]{result.get('reason')}[/yellow]")
        if result.get("missing"):
            console.print(f"Missing: {', '.join(result['missing'])}")
        return
    t = Table(title="What an extra repayment buys")
    t.add_column("Extra / payment", justify="right")
    t.add_column("Per month", justify="right")
    t.add_column("Payoff", justify="right")
    t.add_column("Years saved", justify="right")
    t.add_column("Interest saved", justify="right")
    for s in result["scenarios"]:
        t.add_row(
            _money(s["extra_per_period"]),
            _money(s["extra_per_month"]),
            s["payoff_date"][:7],
            f"{s['years_saved']:.1f}",
            _money(s["interest_saved"]),
        )
    console.print(t)


@app.command()
def reset(confirm: bool = typer.Option(False, "--yes", help="Required.")) -> None:
    """Delete the ledger database. Your CSV files are untouched."""
    if not confirm:
        console.print("[red]Refusing without --yes.[/red]")
        raise typer.Exit(1)
    if db.DB_PATH.exists():
        db.DB_PATH.unlink()
    db.init()
    console.print("Ledger cleared.")


if __name__ == "__main__":
    app()
