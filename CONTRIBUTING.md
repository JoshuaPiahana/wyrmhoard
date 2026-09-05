# Contributing

Thanks for looking. This is a household tool that people trust with real money
decisions, which makes some contributions much more welcome than others.

## Before anything else

**Never commit financial data.** Not yours, not a friend's, not "anonymised"
data. If you need a fixture, extend `api/wyrmhoard/samples.py`, which generates
synthetic transactions. A pre-commit hook and a CI job both block data-bearing
files, but the first line of defence is you.

```bash
pip install pre-commit && pre-commit install
```

## Getting set up

```bash
git clone <your fork>
cd wyrmhoard
cp config/household.example.yml config/household.yml
docker compose up -d
./hoard sample && ./hoard ingest --sample
```

Everything runs in containers. You do not need Python on your machine.

## The checks

```bash
./hoard lint     # data guard, ruff, mypy, yamllint, shellcheck, eslint
./hoard test     # unit and integration
./hoard e2e      # browser tests against the running stack
./hoard check    # all three, in the order CI runs them
```

CI runs the same things. If `./hoard check` passes locally, CI should pass too.

## What makes a good contribution

**Very welcome:**

- **Bank format support.** The CSV sniffer handles layouts it has seen. If it
  misreads your bank's export, that is a bug worth fixing — open an issue with
  a *synthetic* sample in the same shape, never your real file.
- **Merchant rules for your region.** `config/rules.yml` is currently
  NZ-heavy. Rules for other countries make the tool useful to more people.
- **A rates module for another country.** `config/nz_rates.yml` and
  `api/wyrmhoard/analysis/entitlements.py` show the pattern. Entitlements are where
  households lose the most money, and every country has its own.
- **Accessibility and clarity fixes.** This gets read by families, sometimes
  by children, sometimes by people using a screen reader.

**Please discuss first:**

- Anything that adds a network call. The privacy promise in
  [SECURITY.md](SECURITY.md) is the point of the project, not a detail.
- Anything that recommends specific investments or products. The tool does
  arithmetic and points at official sources; it deliberately stops short of
  financial advice.
- New runtime dependencies. Every one is something that can rot in three
  years, and this needs to still run in three years.

## House rules the code follows

These are not style preferences; they are why the output can be trusted.

1. **Never invent a number.** If the data does not support a figure, say so.
   A confident wrong number is worse than an acknowledged gap.
2. **Degrade loudly.** Poor categorisation coverage, unverified rate
   constants, a low-confidence CSV parse — all of these are surfaced, not
   smoothed over.
3. **Never shame.** Report what is true and what would help. Do not editorialise
   about how somebody spent their money.
4. **Assume nothing about the household.** No mortgage, no children, two
   incomes, renting, outside New Zealand — all are first-class. There are
   tests for this in `api/tests/test_household_shapes.py`; please keep them
   passing.

## Tests

New behaviour needs a test. Bugs need a *regression* test — several existing
tests carry a comment explaining the bug they pin, which is the most useful
documentation in the repo.

Where to put things:

| Kind | Location |
|---|---|
| Parsing, categorisation, maths | `api/tests/test_core.py` |
| Unusual household shapes | `api/tests/test_household_shapes.py` |
| A brand-new install with no data | `api/tests/test_fresh_install.py` |
| HTTP contract | `api/tests/test_api.py` |
| Anything involving the browser | `e2e/test_dashboard.py` |

## Commits and pull requests

- Explain *why* in the commit body. The what is in the diff.
- One logical change per pull request.
- Say how you tested it, and mention anything you could not test.

## Licence

Contributions are accepted under the [MIT Licence](LICENSE).
