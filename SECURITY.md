# Security and privacy

This project handles bank transaction data. That shapes every decision in it,
so it is worth being explicit about what it does and does not do.

## The privacy promise

**Your financial data never leaves your machine.**

- No telemetry, no analytics, no crash reporting, no "anonymous usage stats".
- No cloud sync, no accounts, no API keys, no third-party services.
- The application makes **no outbound network requests at all** at runtime.
- Both containers bind to `127.0.0.1` only, so the dashboard is not reachable
  from your home network, let alone the internet.

You can verify this rather than taking it on trust:

```bash
docker compose exec api sh -c "cat /etc/hosts; netstat -tn 2>/dev/null || true"
```

There is one exception, and it is in the **test suite only**: the browser
accessibility test loads `axe-core` from a CDN. It never runs as part of using
the tool.

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
