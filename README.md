# kete

A household financial picture, assembled from your own records and computed
entirely on your own machine.

Built for one family in Ashhurst: a single PAYE income, three children, a
mortgage, no consumer debt, and a month that keeps ending at zero. The point
is not to produce charts. It is to answer three questions honestly enough to
act on:

1. **Where does it actually go?**
2. **What would change the trajectory?**
3. **Is it working?** — measured, not remembered.

---

## Privacy

Nothing leaves this machine. There is no telemetry, no cloud sync, no API key,
and no third-party service. The containers bind to `127.0.0.1` only, so the
dashboard is not reachable from your own LAN, let alone the internet.

`.gitignore` excludes `data/`, `reports/`, `config/household.yml` and every
`*.csv` and `*.pdf` in the tree. Real financial data cannot be committed by
accident. Check it before you ever run `git add -A` anyway.

---

## Getting started

Everything runs in Docker inside WSL.

```bash
cd /mnt/e/projects/finance && docker compose up -d
```

Then open <http://localhost:8080>.

To see it working before trusting it with anything real, load fourteen months
of synthetic data:

```bash
./kete sample && ./kete ingest --sample
```

That data is invented. It exists so you can judge whether the tool is worth
your bank exports.

### Docker on boot

`/etc/wsl.conf` has been set to start the daemon when WSL starts:

```ini
[boot]
command = service docker start
```

This takes effect after `wsl --shutdown` from Windows and the next launch.
Until then, start it manually with `sudo service docker start`.

---

## The three things worth doing first

**1. Fill in `config/household.yml`** (copy from `household.example.yml`).
Ages, mortgage rate and repayment drive the entitlement and payoff maths. It
is gitignored. Ten minutes.

**2. Verify the rates in `config/nz_rates.yml`.**
They were seeded from a language model's training data and are almost
certainly a year out of date. Until you set `verified: true`, every
entitlement figure is labelled an estimate and the dashboard says so. This is
the highest-value ten minutes in the project — the numbers it produces are
large enough to matter and wrong enough to mislead.

**3. Export twelve months from Kiwibank.**
Internet banking → your account → *Export* → CSV. A full year matters because
it captures the bills that only arrive once: rates, insurance, rego, school
stationery, Christmas. Six months will systematically understate your real
cost of living.

Drop the files on the **Data** tab, or into `data/inbox/` and run
`./kete ingest`.

---

## The monthly routine

Deliberately short. A routine with more than two steps does not survive a busy
month.

```bash
./kete ingest      # imports everything in data/inbox/
./kete report      # builds the family meeting report
```

Then, at the meeting, take a snapshot from the **Progress** tab. That is what
turns "it feels a bit better" into a number.

Re-importing the same file is safe. Transactions are fingerprinted, so
overlapping exports de-duplicate themselves and you never have to keep track
of what you already loaded.

---

## Commands

| Command | What it does |
|---|---|
| `./kete ingest` | Import every CSV in `data/inbox/` |
| `./kete ingest --sample` | Import the synthetic sample |
| `./kete summary` | Headline numbers in the terminal |
| `./kete review` | Biggest uncategorised spending, largest first |
| `./kete recategorise` | Re-apply rules after editing `rules.yml` |
| `./kete report` | Build the family meeting report |
| `./kete snapshot` | Freeze this month's numbers |
| `./kete loan` | Mortgage payoff scenarios |
| `./kete test` | Run the test suite |
| `./kete logs` | Tail the API logs |

---

## What to trust, and what not to

The tool is built to fail loudly rather than quietly, because a confident
wrong number is worse than an admitted gap.

- **Categorisation coverage is reported everywhere.** Below 90%, the dashboard
  says the category charts are not yet trustworthy instead of drawing a clean
  chart over a shaky foundation. Fix the biggest unknowns on the Data tab —
  one decision usually covers dozens of transactions.

- **The current month is never counted as complete.** A month three days old
  shows a fortnight's rent gone and no salary in yet, which reads as
  catastrophe. It is excluded from every average.

- **"Typical" means median, not mean.** One $3,000 car repair should not become
  your normal monthly spending. Where the two diverge sharply, the report says
  so, because that gap is itself a finding.

- **Entitlement figures are estimates.** They compare what actually landed in
  your account from IRD against what a household of your shape would normally
  receive. The observed half is certain; the expected half depends on rate
  constants you have not verified yet. IRD's own calculator is the authority.

- **Import confidence is reported per file.** The CSV parser sniffs the layout
  rather than assuming one, and tells you what it decided. If it says `low`,
  read the warnings before believing anything downstream.

- **None of this is regulated financial advice.** It is arithmetic on your own
  bank data, plus prompts to go and check things with the organisations that
  actually hold the answers.

---

## Layout

```
config/
  household.yml        your family's facts (gitignored — copy the example)
  rules.yml            merchant → category rules, NZ-specific
  nz_rates.yml         entitlement constants + whether they are verified
  learned.yml          corrections you make in the UI (created on demand)
data/
  inbox/               drop bank CSV exports here
  ledger.db            SQLite; the whole ledger, easy to back up
  samples/             synthetic data, safe to commit
reports/               generated family meeting reports (gitignored)
api/kete/
  ingest/bank_csv.py   format-sniffing CSV parser
  categorise.py        memo → category
  analysis/            cashflow, recurring, mortgage, entitlements
  coach.py             findings and the sequenced plan
  report.py            the family meeting report
  api.py               FastAPI, ~20 thin endpoints
web/src/               dashboard (no build step, no framework)
```

---

## Replacing the interface

The frontend holds no business logic. The JavaScript does exactly two things:

- fills any element carrying `data-bind="path.into.state"`, formatted by
  `data-format="money|money2|pct|weeks|int|date|text"`
- renders lists into containers carrying `data-list="name"`

So a design from Stitch (or anywhere else) drops in by carrying those
attributes onto whatever markup it uses. No build step, no framework, no
rewiring of the maths. `web/src/css/app.css` starts with a token block — that
is the only part worth carrying across.

---

## Tests

```bash
./kete test
```

They cover the places where a silent bug would be most expensive: money sign
handling, CSV column detection, de-duplication on re-import, categorisation
normalisation, which months count as complete, and cache invalidation. Two are
regression tests for bugs found while building it.

---

## Troubleshooting

**Dashboard says it cannot reach the API.**
`docker compose ps` — if `kete-api` is not up, `docker compose logs api`.

**Docker daemon not running after a reboot.**
`sudo service docker start`, or `wsl --shutdown` from Windows to pick up the
`/etc/wsl.conf` boot command.

**Import confidence is `low`.**
The layout was not understood. Run `./kete ingest` and read the reported
column map, or use the Data tab, which shows the same detail. Send a redacted
sample if the sniffer needs teaching a new Kiwibank variant.

**Numbers look stale after editing a YAML file.**
Analysis is cached against the ledger file, so a config-only edit needs
"Reload config & recategorise" on the Data tab, or `./kete recategorise`.
