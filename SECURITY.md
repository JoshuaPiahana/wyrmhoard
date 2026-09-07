# Security and privacy

This project handles bank transaction data. That shapes every decision in it,
so it is worth being explicit about what it does and does not do.

## The privacy promise

**Wyrmhoard never sends your data anywhere.**

- No telemetry, no analytics, no crash reporting, no "anonymous usage stats".
- No cloud sync, no accounts, no phoning home, no update checks.
- The application makes **no outbound network requests at all** at runtime.
- Both containers bind to `127.0.0.1` only, so the dashboard is not reachable
  from your home network, let alone the internet.

You can verify this rather than taking it on trust:

```bash
docker compose exec api sh -c "cat /etc/hosts; netstat -tn 2>/dev/null || true"
```

It is also enforced, not merely asserted. `api/tests/test_offline.py` fails the
build if any module imports a network client, and asserts the sentence above is
still in this file, word for word. Softening the promise means deleting a test
in the same commit.

There is one exception, and it is in the **test suite only**: the browser
accessibility test loads `axe-core` from a CDN. It never runs as part of using
the tool.

### What you choose to run alongside it

That promise is about Wyrmhoard. It is not a promise about every program on
your machine, and it would be dishonest to imply otherwise.

Data reaches Wyrmhoard through **producers** — a person typing, an AI agent, a
script, a bridge to an open-banking provider. Some of those are entirely local.
Some are not. A producer that fetches your transactions from an aggregator
means that aggregator holds your transactions, on their servers, by design.
That is the trade, and it is yours to make rather than ours to make for you.

Two things follow, and both are the tool's job:

- **Every record says where it came from.** Each row carries a `producer` —
  `human:dashboard`, `agent:mcp`, `tool:akahu`. You can always see which of
  your data arrived through a third party, and which never left the house.
- **A producer must be honest about what it implies.** `docs/PRODUCERS.md`
  requires it to say so plainly, before you install it rather than after.

Wyrmhoard itself never holds a credential for anything, never has an API key,
and cannot make a request. If you install nothing, nothing leaves.

## What is stored, and where

| What | Where | Committed to git? |
|---|---|---|
| Transactions | `data/ledger.db` (SQLite) | Never |
| Bank exports you drop in | `data/inbox/` | Never |
| Your household details | `config/household.yml` | Never |
| Generated reports | `reports/` | Never |
| Categorisation rules | `config/rules.yml` | Yes — no personal data |

Two independent controls keep financial data out of the repository:

1. `.gitignore` excludes `data/`, `reports/`, `config/household.yml`, and every
   `.csv`/`.pdf` in the tree.
2. `scripts/check_no_financial_data.py` runs as a pre-commit hook **and** in
   CI. It blocks data-bearing file types, bank account numbers that are not on
   the synthetic allowlist, and digit runs that pass a card-number checksum.

The second exists because `.gitignore` is a convenience, not a control —
`git add -f` walks straight past it.

## Backups

`data/ledger.db` is your whole financial history in one file. Copy it
somewhere safe. If you back it up to cloud storage, understand that you are
choosing to put your transaction history there; the tool will not do it for
you.

## Threat model

This tool assumes:

- **You trust the machine it runs on.** It does not encrypt the ledger at
  rest. Anyone with access to your user account can read it, exactly as they
  could read a spreadsheet. Use full-disk encryption if that matters to you.
- **You are not exposing it to a network.** There is no authentication,
  because there is nothing to authenticate against on loopback. If you change
  the port bindings to `0.0.0.0`, you are publishing your finances to your
  network with no password. Do not do this.
- **The CSV files you import are your own.** The parser is defensive, but it
  is not hardened against a deliberately malicious file.

## Reporting a vulnerability

Please report privately rather than opening a public issue:

- Use GitHub's [private vulnerability reporting][gh-report] on this
  repository, or
- Open a regular issue **only** for things with no security impact.

[gh-report]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

This is a spare-time project, not a funded one. Expect a reply within a week
or two, and no bug bounty. Fixes for anything that could expose a user's
financial data will be prioritised over everything else.

## A note on self-hosted CI runners

If you fork this and enable a self-hosted runner on a **public** repository,
anyone can open a pull request that executes arbitrary code on that machine.
On a machine holding real bank data, that is a serious risk. This project uses
GitHub-hosted runners for exactly that reason, and you should too.
