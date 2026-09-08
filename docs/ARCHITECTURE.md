# Architecture

**Wyrmhoard is a local-first store of a household's own records that computes
exact, self-describing figures from them. It holds no opinions.**

Everything below follows from that sentence. It is worth reading before adding
anything, because most of the arguments this project has had were really
arguments about which side of a line something belongs on, and this document is
that line.

---

## Three roles

Every part of the system is exactly one of these.

### Producers write

A person typing into the dashboard. An AI agent over MCP. A script that reads a
council website. A bridge to an open-banking provider. They differ in what they
can reach and how they can be wrong; to the database they are the same thing.

Each one names itself, says when its figure was *true*, and states its own
confidence. `docs/PRODUCERS.md` is the contract.

**Wyrmhoard never fetches anything.** Not a rate table, not a valuation, not an
update check. Enforced by `api/tests/test_offline.py`, which bans network
clients outright and asserts the promise in `SECURITY.md` character for
character — so the code and the claim have to move in the same commit.

### The core stores and computes

Records in, arithmetic out. It knows what a household has and what follows
arithmetically from that. It does not know what any of it means.

### Consumers read

The dashboard. An MCP agent. A coaching program. A jurisdiction pack. A report
generator. Somebody's spreadsheet.

**Consumers are allowed opinions.** Having them is the whole point. A consumer
that thinks takeaways are a problem and one that thinks they are fine can read
the same figures and disagree, and neither is misusing the tool.

---

## How to tell core from consumer

One question decides it:

> **Could two reasonable households disagree about this, without either of them
> being wrong about the facts?**

If yes, it belongs in a consumer.

| Claim | Disagreement possible? | Belongs |
|---|---|---|
| "You spent $312.40 on fuel in August" | No | **Core** |
| "This loan's rate is 4.79%, derived from its own interest charges" | No | **Core** |
| "$0 arrived from IRD in twelve months" | No | **Core** |
| "Fuel is essential spending" | Yes — a cyclist and a rural family differ | Consumer |
| "You have three weeks of runway" | Yes — it depends on what counts as essential | Consumer |
| "Your position is worsening" | Yes — over what window, and is a one-off repair a trend? | Consumer |
| "A household like yours would receive $8,771" | Yes — depends on a jurisdiction's rules | Jurisdiction pack |
| "Build a buffer before overpaying the mortgage" | Yes — this is one school of thought | Consumer |

The test catches things that look like analysis and are not. "Weeks of
essentials in the bank" is arithmetic, but it needs to know what is essential,
so it is philosophy wearing a number's clothes.

---

## The rules

### 1. The core never reaches out

Anything fetched is fetched by a producer the household chose to run. If a
figure has to come from the internet, that is a producer's job, and running it
is a decision the household makes with the trade-off in front of them.

### 2. Every figure is self-describing

A value carries its unit and the period it covers:

```json
{"value": 312.40, "unit": "NZD", "period": "2026-08"}
```

rather than `312.40`.

This is the constraint everything else depends on. A jurisdiction pack, a
coaching consumer and an agent reasoning across finance and health data are all
programs reading figures from a program they did not write. A bare number
forces each of them to guess the currency and the window from a key name, and
they will eventually guess wrong. Names must also survive being seen next to
another tool's: `get_progress` means something different to a fitness tracker.

### 3. Preferences are data, not code

The spending taxonomy, the goal ladder, household facts — the core stores what
the household declares and computes nothing from the values.

A household putting `health` in their essentials because a child needs weekly
therapy should say it once, and have every consumer respect it. If the
taxonomy lived in each consumer instead, they would drift and disagree about
the same household.

So: the core *serves* the grouping as data and never sums across groups. It
reports what each group cost; adding two of them together is a consumer's
call.

The one exception is `kind` — `spend`, `income`, `transfer` or `unknown` —
declared per group in `rules.yml`. That is a fact about which way money moved,
not a judgement, and the core needs it so a supermarket refund is counted as
income rather than as negative groceries. The name of a group and what goes in
it stay entirely the household's.

`api/tests/test_taxonomy.py` fails the build if any core module names one of
the four judgement groups, because that is how they got welded into eighteen
places the first time: nobody added them on purpose.

### 4. No jurisdiction in the core

Tax years, benefit schemes, account-number formats, payroll vocabulary — these
belong to packs, which are consumers.

A single `country` field cannot express somebody with KiwiSaver and Australian
superannuation. They do not have a country; they have accounts in two
jurisdictions. Install both packs and each says what it can about what it
recognises.

### 5. Nothing is overwritten

Observations append. A newer figure never destroys an older one, because
"what did we believe in March, and who told us" is a question somebody will
eventually need to answer. `property_valuations` is the reference shape.

### 6. Every endpoint has a caller

Dead surface is a defect. An endpoint nobody calls is untested in practice,
carries maintenance cost, and misleads anyone reading the API as documentation.

---

## Writing a producer

See `docs/PRODUCERS.md`. In short: state the six provenance fields, name
yourself as `kind:name`, and never default `observed_at` to today.

A producer may hold credentials *it* was given — an open-banking token, an API
key for a public data source. Wyrmhoard never sees them and never uses them.
What a producer must do is be honest about what running it means: if your data
passes through somebody's servers to get here, the household should know that
before they install it, not after.

## Writing a consumer

Read the API or the MCP server. Nothing else is required — there is no plugin
interface to implement, no registration, no hook to define. A consumer is any
program that reads.

That is deliberate. The alternative — a plugin system inside the app — would
make consumers depend on this codebase's release cycle and Python version, and
would quietly recreate the coupling this architecture exists to remove.

**A consumer never writes.** If it needs to record something, it is wearing a
producer's hat for that call, and the same rules apply.

### Jurisdiction packs are consumers

An NZ pack reads income, transactions and accounts, and produces
Working for Families estimates, tax-year annualisation and KiwiSaver figures.
An Australian pack does the equivalent for its own scheme. Neither is special;
both are just consumers that happen to know a rulebook.

They carry the same honesty obligation as the core. An entitlement figure is an
estimate from rate tables that may be out of date, and the tax office is
authoritative. Say so in the output, every time.

---

## Where the code does not match this yet

Written down so the gap is visible rather than discovered.

| What | Problem | Where it should go |
|---|---|---|
| ~~`coach.py`~~ | **Done.** 941 lines asserting one philosophy, removed | |
| ~~`report.py` + template~~ | **Done.** 719 lines shaped around one household's family meeting | |
| `analysis/entitlements.py` `estimate()` | Returns `severity` and written advice from an analysis module | NZ pack |
| ~~`cashflow.SPEND_GROUPS`~~ | **Done.** The vocabulary is declared in `rules.yml` and served at `GET /taxonomy`; a guard test fails the build if a core module names a group | |
| ~~`cash_position().runway_weeks`~~ | **Done.** The dashboard computes it, from one constant it can see | |
| `trend().direction` / `.driver` | The numbers are facts; `improving` is a verdict | Consumer |
| `income.from_payslips().notes` | A list of written advice | Consumer |
| ~~The `snapshots` table, `POST /snapshots`, `./hoard snapshot`~~ | **Done.** Removed once `GET /series` could answer for any date range. The note survived as `notes`, which appends — the old table keyed on the day it was taken and used `INSERT OR REPLACE`, so a second entry the same day destroyed the first | |
| Tax year, KiwiSaver vocabulary, IRD redaction, NZ account formats | 115 NZ occurrences across 19 files | NZ pack |
| Figures generally | Bare floats; currency appears once in the whole MCP surface | Rule 2 |

None of this is urgent. It is the order the work should happen in, and rule 2
comes first — every extraction below it needs an interface to extract against,
and doing it later means designing each of them twice.

### Why snapshots went, kept as the worked example

The clearest case of rule 5 this codebase has produced, so it is written down
rather than left in a commit message.

A snapshot froze the month's computed figures next to a note. The figures were
the wrong thing to store: they are arithmetic over transactions that are still
sitting in the database, so a snapshot was a second and staler copy of them
that drifted out of agreement with the first every time categorisation
improved. **Observations append; derived numbers get recomputed.**

The deletion waited for `GET /series`, because until the core could answer for
an arbitrary `from`/`to` rather than only "the last N complete months", the
claim that any past month is recomputable was not yet true.

One thing in a snapshot was not reconstructible, and it survived: **the note**.
"What changed this month, in the household's own words" is a record, not a
derived figure, so by the test at the top of this document it belongs here.
`categorised_pct` was the other, and was let go deliberately — how much the
tool understood back in March is a fact about the tool, not the household.

The old table keyed on the day it was taken and wrote with `INSERT OR REPLACE`,
so a second snapshot on the same day silently destroyed the first. `notes`
appends and de-duplicates on a fingerprint instead, which is the same rule
every import already follows.

### Extracting the NZ pack

The last item on the list, and the one most often mis-sized. "115 NZ
occurrences across 19 files" is true and misleading, because those occurrences
are three different kinds of thing and only one of them is a problem.

| | What it is | Size | Where it goes |
|---|---|---|---|
| **Policy** | `entitlements.estimate()`, `checklist()`, `config/nz_rates.yml` — a rulebook, with all seven rate blocks shipping `verified: false` | 225 lines + 138 of YAML | A pack |
| **Facts** | `observed_support()`, `observed_income()` — *"$0 arrived from IRD in twelve months"* | 86 lines | **Stays.** Arithmetic on the household's own records |
| **Vocabulary** | `payslip.py` patterns for `kiwisaver_ee` / `paye` / `acc_levy`, `income.tax_year_bounds()` | scattered | **Stays.** See below |

**Vocabulary is not jurisdiction.** A payslip parser recognising the word
"KiwiSaver" is doing the same job as a merchant rule recognising "PAK N SAVE":
matching local text. Every country has a retirement contribution, an
income-tax line and a tax year; only the words and the boundary date are
local. Renaming those fields would also be a schema migration on the
`payslips` table, for no gain anyone can point at. Left alone deliberately.

**Facts are not jurisdiction either.** The decision table at the top of this
document already settles it: *"$0 arrived from IRD in twelve months"* is Core.
The only NZ in it is the category key `income_ird`, which lives in `rules.yml`
as the household's own data. Better still, that function is a special case of
a question the core should answer generally — *how much arrived in category X
over range Y* — which is the income counterpart of `GET /series`. Generalising
the series module deletes the NZ from it rather than moving it.

#### The work, in order

1. **Extend `analysis/series.py` to income.** It answers spending per category
   per period; the same function answering money *in* subsumes
   `observed_support()` and `observed_income()` with no scheme name in either.
2. **Delete the policy half.** `estimate()`, `checklist()`, `nz_rates.yml`,
   `config.Rates`, `Household.region_supported`, and the `country` gate.
   Remove `GET /entitlements`, the `get_entitlements` MCP tool, and the
   dashboard's Entitlements tab. Drop "rate constants nobody has verified"
   from `describe_data_gaps`; keep "accounts money arrives from that were
   never imported", which is the half that has actually found something.
3. **Add the guard.** `test_no_jurisdiction.py`, on the AST like
   `test_taxonomy.py`. It bans **scheme names and rate constants** in core
   modules — `working_for_families`, `best_start`, `rates_rebate` — and
   deliberately does *not* ban payslip field names, because that is the line
   this section exists to draw.

#### The pack itself is not this work

Build it when there is a reason to, and build it **in its own repository**.

- **Different clock.** NZ rates change on 1 April; the core does not. Sharing
  a repo means either cutting a core release for a tax change, or letting
  rates go stale waiting for one.
- **A boundary is only real when it is physical.** In-repo, `from wyrmhoard
  import db` is one keystroke away and someone will reach for it — the same
  argument this document already makes against a plugin system.
- **Liability.** An entitlement estimate is the closest thing this project
  produces to advice. Outside the core repo, "not financial advice" is a
  cleaner claim.
- **It is the proof.** If a pack can be written against the published API and
  MCP surface alone, the consumer contract is real. If it cannot, that is a
  finding about the API, and worth discovering on purpose.

#### What this costs, honestly

The household loses the Working for Families estimate — which on this project
was the single largest sum the tool ever surfaced. Two things make that
acceptable and one makes it uncomfortable.

Acceptable: the figure was never trustworthy. Every rate block ships
`verified: false`, and the tool has always labelled the number indicative and
pointed at IRD's own calculator. And the *certain* half survives — the
observed side is what caught family payments arriving in an account that had
never been imported, which is the finding that mattered.

Uncomfortable: "we deleted the most valuable-looking number in the product"
is a real cost, and if the answer turns out to be that somebody wants it back
immediately, then the pack is step one rather than step three and this
sequence is wrong.

---

## What this costs

Recorded honestly, because a document that only lists benefits is marketing.

**The tool becomes less immediately useful to a non-technical household.**
Deleting the report removes the only artefact somebody who will never open a
terminal ever sees. "Any user" can quietly narrow to "any user with an MCP
client". The bet is that a neutral core with several consumers serves more
people than one opinionated app serves well — but it is a bet, and if the
product ever needs a non-technical path in, this is the decision to revisit.

**More moving parts.** A household wanting coaching now installs something.
That is worse than it working out of the box, and it is the price of not
assuming everybody wants the same advice.

**The privacy promise gets longer.** "Your data never leaves your machine" is a
sentence anybody can check. "Wyrmhoard never sends your data anywhere, and here
is how to tell what each producer does" is true, more useful, and harder to say
on a landing page.
