# Wyrmhoard

**Know your hoard to the last coin.**

See where your household's money actually goes, decide what to change, and
measure whether it worked — without your bank data ever leaving your computer.

[![CI](https://github.com/JoshuaPiahana/wyrmhoard/actions/workflows/ci.yml/badge.svg)](https://github.com/JoshuaPiahana/wyrmhoard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Most budgeting apps want your bank login, sell your spending data, or stop
working when the subscription lapses. This one runs in two containers on your
own machine, reads CSV files you export yourself, and talks to nothing.

It answers three questions:

1. **Where does it actually go?** Twelve months of transactions, categorised.
2. **What would change things?** A short, ranked, costed list — not thirty tips.
3. **Is it working?** Snapshots you take each month, so progress is measured
   rather than remembered.

It also produces a **printable report designed for a household meeting**,
including a page written for children.

> **A wyrm knows its hoard to the last coin.** It does not guess, it is not
> surprised, and nothing goes missing without being noticed. The gold is not
> the point — knowing exactly what you have is.

The project is **Wyrmhoard**; the command you type is **`./hoard`**.

---

## Is this for me?

**Yes, if** you can export CSV files from your bank and run one command.

The tool makes no assumptions about your household. Renting, no mortgage, one
income, two incomes, no children, five children, single, retired — all of it
is supported and tested. Sections that do not apply to you simply do not
appear.

**Two honest limitations:**

- **Bank support.** The CSV parser works out the layout of the file rather
  than assuming one, so it handles most bank exports. It has been tested
  hardest against Kiwibank (New Zealand). If yours misreads, the tool tells
  you rather than importing nonsense — and [it is a bug worth
  reporting](CONTRIBUTING.md).
- **Entitlements are New Zealand only.** The module that checks whether you
  are claiming everything you are owed currently understands NZ rules only.
  Set `country:` to anything else and that one page switches itself off; every
  other part works normally. [Adding your country](CONTRIBUTING.md) is very
  welcome.

---

## Getting started

**You need:** [Docker](https://docs.docker.com/get-started/get-docker/) and
about fifteen minutes. Nothing else — no Python, no Node.

> **Not comfortable with a terminal?** This README is written to be pasted
> into an AI assistant. Try: *"I want to run the Wyrmhoard project on my computer.
> Here is its README. Walk me through it one step at a time, and tell me
> exactly what to type."* Every command below is complete and safe to copy.

### 1. Get the code

```bash
git clone https://github.com/JoshuaPiahana/wyrmhoard.git
cd wyrmhoard
```

### 2. Start it

```bash
docker compose up -d
```

First run takes a few minutes to download and build. When it finishes, open
**<http://localhost:8080>**. You should see the dashboard with a banner
saying there is no data yet — that is correct.

### 3. See it working with fake data

Before trusting it with anything real, look at it working:

```bash
./hoard sample
./hoard ingest --sample
```

Refresh the browser. The dashboard now shows fourteen months of **invented**
transactions for a fictional household. Nothing here is real; it exists so you
can judge the tool before feeding it your own records.

When you have seen enough, clear it out:

```bash
./hoard reset --yes
```

### 4. Add your own details

```bash
cp config/household.example.yml config/household.yml
```

Open `config/household.yml` in any text editor. Every field is optional and
explained in the file. Fill in what you know; leave the rest.

This file is **gitignored** and stays on your machine.

### 5. Export your bank transactions

In your internet banking, find the export or download option for your account
and choose **CSV**. Ask for **twelve months**.

A full year matters more than it sounds. Six months misses the bills that only
arrive once — insurance, rates, vehicle registration, school costs, Christmas
— and a budget that ignores those will never balance.

Drop the files onto the **Data** tab in the dashboard, or copy them into
`data/inbox/` and run:

```bash
./hoard ingest
```

The tool reports what it made of each file: how many rows it read, which
column it thinks is which, and how confident it is. **Read that before
believing anything downstream.** If it says `low`, something about your bank's
layout is not understood yet.

### 6. Tidy up the categories

Open the **Data** tab and look at "Fix the unknowns". Assign a category to the
biggest few — one decision usually covers dozens of transactions.

Aim for 90%+ coverage. Below that, the dashboard tells you the category charts
are not yet trustworthy instead of drawing a confident picture over a shaky
foundation.

---

## The monthly routine

Deliberately two commands. A routine with more steps does not survive a busy
month.

```bash
./hoard ingest      # import whatever is in data/inbox/
./hoard report      # build the household meeting report
```

Then open `reports/latest.html`, sit down with it, and take a snapshot from
the **Progress** tab when you are done. That snapshot is what turns "it feels
a bit better" into a number you can see next month.

Re-importing a file you have already imported is safe. Every transaction is
fingerprinted, so overlapping exports de-duplicate themselves and you never
have to track what you already loaded.

---

## Commands

| Command | What it does |
|---|---|
| `./hoard up` | Start the dashboard at <http://localhost:8080> |
| `./hoard down` | Stop everything |
| `./hoard ingest` | Import every CSV in `data/inbox/` |
| `./hoard ingest --sample` | Import synthetic demo data |
| `./hoard summary` | Headline numbers in the terminal |
| `./hoard review` | Biggest uncategorised spending, largest first |
| `./hoard recategorise` | Re-apply rules after editing `config/rules.yml` |
| `./hoard report` | Build the household meeting report |
| `./hoard snapshot` | Freeze this month's numbers |
| `./hoard loan` | Mortgage payoff scenarios |
| `./hoard reset --yes` | Delete the ledger (your CSV files are untouched) |
| `./hoard lint` / `test` / `e2e` / `check` | The quality gates |
| `./hoard logs` | Tail the API logs |

On Windows, run these from **WSL** or **Git Bash**.

---

## What to trust, and what not to

The tool is built to fail loudly rather than quietly, because a confident
wrong number is worse than an admitted gap.

- **Categorisation coverage is reported everywhere.** Below 90%, the dashboard
  says so rather than pretending the charts are meaningful.
- **The current month is never counted as complete.** A month three days old
  shows the rent gone and no pay in yet, which reads as catastrophe.
- **"Typical" means median, not mean.** One large car repair should not become
  your normal monthly spending. Where the two diverge sharply, the report says
  so — that gap is itself a finding.
- **Import confidence is reported per file.** The parser tells you what it
  decided about your CSV's layout.
- **Entitlement figures are estimates.** The tool compares what actually
  landed in your account against what a household of your shape would
  normally receive. The observed half is certain. The expected half depends on
  rate constants in `config/nz_rates.yml` that ship **unverified** and are
  labelled as such until you check them against the official source.
- **This is not financial advice.** It is arithmetic on your own bank data,
  plus prompts to go and check things with the organisations that hold the
  answers. It does not recommend investments or products, and it never will.

---

## Privacy

Your data never leaves your machine. No telemetry, no cloud, no accounts, no
API keys, and no outbound network requests at runtime. Both containers bind to
`127.0.0.1` only.

Two independent controls keep financial data out of git: `.gitignore`, and a
guard script that runs on every commit and in CI — because `.gitignore` is a
convenience, not a control.

Full detail, including the threat model and what is **not** protected, is in
[SECURITY.md](SECURITY.md).

---

## How it fits together

```
config/
  household.yml     your household's facts (gitignored — copy the example)
  rules.yml         merchant → category rules
  nz_rates.yml      entitlement constants, and whether they are verified
data/
  inbox/            drop bank CSV exports here
  ledger.db         SQLite — your whole history in one backup-able file
reports/            generated meeting reports (gitignored)
api/wyrmhoard/
  ingest/           format-sniffing CSV parser
  categorise.py     memo → category
  analysis/         cash flow, recurring payments, debt payoff, entitlements
  coach.py          ranked findings and the sequenced plan
  report.py         the meeting report
  api.py            FastAPI — thin endpoints over the analysis
web/src/            dashboard — no build step, no framework
e2e/                browser tests
```

### Replacing the interface

The frontend holds no business logic. The JavaScript does two things: it fills
elements carrying `data-bind="path.into.state"`, and renders lists into
containers carrying `data-list="name"`. Any design that keeps those attributes
drops straight in — no build step, no framework, no rewiring of the maths.

---

## Quality gates

Every push runs: a financial-data and secret scan, `ruff`, `mypy`, `yamllint`,
`shellcheck`, `eslint`, unit and integration tests on Python 3.11 and 3.12, a
Docker build, twenty-eight browser tests (including automated accessibility
and mobile-overflow checks), and a job that follows this README's quickstart
on a clean machine to prove the instructions still work.

```bash
./hoard check
```

---

## Driving it with an AI

Wyrmhoard computes; interpreting the figures is somebody else's job. A
language model is far better than any dashboard at open-ended questions —
*"what if I went to four days a week?"* — and far worse at summing three
thousand transactions without drifting. So it exposes exact figures and
refuses to guess, and an agent does the reasoning.

It speaks **[MCP](https://modelcontextprotocol.io)** over stdio, so nothing
listens on a port and the transport never touches the network. Point an agent
at it with:

```bash
docker compose -f /absolute/path/to/docker-compose.yml run --rm -T mcp
```

For Claude Desktop, that goes in `claude_desktop_config.json` as the `command`
and `args` of an MCP server entry.

**Summaries by default.** An agent answering "can we afford a holiday?" gets a
three-kilobyte summary, not three thousand rows naming every shop you visited.
On a real two-year ledger that is **154× less data** than the raw records —
and `list_transactions` is the only tool that returns them at all, with a
description telling the model to avoid it. Minimisation is built into which
tools exist, not left to a policy somebody has to remember.

**Every figure carries its provenance** — the window it covers, how it was
derived, how much of the data is understood. A model cannot caveat what it was
not told.

**`describe_data_gaps` is a first-class tool**, and the server's instructions
tell agents to call it before drawing conclusions. It reports accounts money
arrives from that were never imported, spending it could not categorise, and
rate constants nobody has verified. This exists because the tool once told a
household they were missing family tax credits that were simply arriving in an
account it had not been given.

You also get **OpenAPI free** at `/openapi.json` if your agent framework
prefers that.

## Where this is going

The tool is useful with zero input and gets more useful with each detail you
choose to add — bank CSV unlocks cash flow, household basics unlock goals, a
payslip sharpens the entitlement maths, mortgage details unlock payoff
scenarios. Nothing is ever required, and the plan is for the app to tell you
what each next step is *worth* before you spend the effort.

[ROADMAP.md](ROADMAP.md) has the detail, including the things this project
deliberately will not do.

## Contributing

Bank formats, merchant rules for your region, and entitlement modules for
other countries are the most valuable things anyone could add. See
[CONTRIBUTING.md](CONTRIBUTING.md).

**Never commit financial data** — use the synthetic generator in
`api/wyrmhoard/samples.py`.

## Licence

[MIT](LICENSE).
