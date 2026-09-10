# Working on Wyrmhoard

A household finance tool holding one family's real bank data. Read
`docs/ARCHITECTURE.md` before adding anything — it decides what belongs here
and what belongs in a consumer.

## Running things

Everything runs in containers. **Never run bare `pytest`** — `./hoard test`
mounts the repo at `/repo`, and three guard tests silently *skip* without it
rather than failing (the e2e isolation check, the Playwright version coupling
check, and the SECURITY.md promise check).

    ./hoard test      # unit and integration
    ./hoard lint      # data guard, ruff, mypy, shellcheck, eslint
    ./hoard e2e       # browser tests, ~2.5 min
    ./hoard check     # all three, in the order CI runs them

Run `./hoard check` before pushing. Local green has not meant CI green here
before.

## Non-negotiables

- **Never commit financial data.** `scripts/check_no_financial_data.py` runs
  pre-commit and in CI, because `.gitignore` is a convenience and `git add -f`
  walks straight past it.
- **Never put a real account number in a test or a doc.** Use the synthetic
  allowlist in the guard script.
- **Never read, store or log an IRD number.** Payslip text is redacted before
  anything else touches it.
- **The core makes no outbound network requests.** `api/tests/test_offline.py`
  enforces this and asserts SECURITY.md still says so, word for word.
  Anything that must be fetched is fetched by a producer — see
  `docs/PRODUCERS.md`.
- **Not regulated financial advice.** Arithmetic on a household's own records,
  plus prompts to check official sources.
- **The core holds no country's tax or benefit rules.** Payslip vocabulary and
  merchant patterns are fine - they are local text, not a rulebook.
  `api/tests/test_no_jurisdiction.py` draws that line.
- **Never weaken `main`'s branch protection to get something merged.** It is
  deliberately strict: eleven required checks, a pull request, no bypass for
  admins, and unresolved review comments block the merge. That last one means
  a CodeQL nit can stop a merge, which is the intended behaviour and was
  chosen knowing it. If a merge is blocked, fix the thing or ask - do not turn
  the setting off. Three separate defects reached a green `./hoard check` and
  were caught only here.

## How we work

**Show one before doing twenty.** Change one representative call site, show the
resulting shape, agree it, then propagate. This caught a bug that would have
reported "2.7 dollars of essentials" to an agent.

**When something breaks, ask what rule was violated, then encode the rule.**
Every guard test here exists because something got through once:
`test_layering.py`, `test_offline.py`, `test_figures.py`, and the Playwright
version-coupling check. They are cheap and they keep paying.

**Measure before believing a plausible cause.** The test suite took 488s. The
obvious culprit was the filesystem; measuring showed 4%. The real cause was
SQLite fsync, and fixing it gave 488s → 14s.

**Treat generated output as a claim.** Including mine. A comment asserting an
invariant is worth checking against the code — one here claimed a taught rule
could not move spending between groups, and it could.

**Commit messages explain why, not what.** They are the documentation somebody
actually reads later.

## Traps that have cost real time

- `git push` works from WSL, not Git Bash — the SSH key GitHub knows lives in
  WSL.
- Git Bash rewrites Unix-looking paths before passing them to a Windows binary.
  `hoard` sets `MSYS_NO_PATHCONV`; anything new that mounts a path needs the
  same care.
- A stale `DOCKER_HOST` env var silently overrides Docker Desktop's context and
  makes every command fail while the engine is fine.
- **The data guard cannot see a file you have not staged.** It runs
  `git ls-files`, which reads the index, so a brand-new file is invisible to
  `./hoard lint` and to the pre-commit hook until it is added. Running
  `git add -A && git commit` as one command fires the hook *before* the add,
  so a new file's first check happens in CI. Stage first, then lint, then
  commit. This let an account number into a test file once.

## Where things are

    api/wyrmhoard/        the core: storage, ingest, analysis
    api/wyrmhoard/mcp_server.py   the agent interface
    web/src/              the dashboard — a consumer, no business logic
    docs/ARCHITECTURE.md  what is core and what is a consumer
    docs/PRODUCERS.md     how data gets in
    config/rules.yml      categorisation rules (public, generic)
