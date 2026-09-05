# Roadmap

Direction, not commitments. This is a spare-time project; anything here may
change or never happen. Contributions toward any of it are welcome.

---

## The core idea: earn your way to more insight

**The app must be useful with zero input, and get more useful with every
detail you add.** Nothing should ever be a wall — no mandatory setup wizard,
no "complete your profile to continue". Someone should be able to open it,
drop in one CSV, and get real value in two minutes.

But a tool that knows more can say more. So each piece of data the household
chooses to share should visibly unlock something, and the app should be honest
about what that something is worth *before* you spend the effort.

### Capability tiers

Each tier works standalone. None is required for the one below it.

| You provide | You unlock | Effort |
|---|---|---|
| **Nothing** | The tool explains itself and runs on synthetic data so you can judge it before trusting it | 0 min |
| **Bank CSV** | Cash flow, categories, recurring payments, small-spending leaks, monthly trend, the household report | 5 min |
| **Household basics** (who lives here) | Goals sized to your real essential spending, buffer and runway targets, the sequenced plan | 5 min |
| **Children's birth dates** | Entitlement checks — usually the single largest number the tool can find | 2 min |
| **A payslip** | Verified gross income, PAYE and retirement contributions checked against what actually arrived, sharper entitlement estimates | 5 min |
| **Mortgage details** | Payoff scenarios, interest-per-week, a refix reminder before the rate rolls over | 3 min |
| **Partner or second income** | Combined-income abatement (which is what entitlement rules actually assess), household-level planning | 3 min |
| **Tax office data** (myIR in NZ) | Reconciliation of estimated versus actual entitlements, and detection of an accruing end-of-year bill | 10 min |
| **Balances you type in** (retirement savings, other assets and debts) | Net worth with a real trend line, not just cash flow | 5 min |

### What the app should do about this

The interesting part is not the tiers — it is the app *selling the next one*,
with an honest estimate of the payoff:

> **Add a payslip → 5 minutes**
> Right now your gross income is inferred from bank deposits, which is rough.
> A payslip would make the entitlement estimate meaningful instead of
> indicative, and check that your deductions are correct.

Design rules for that mechanism:

- **Quantify the payoff where possible.** "Unlocks a feature" is weak.
  "Would sharpen a figure currently estimated at ±$4,000" is a decision.
- **Never nag.** Show the suggestion once, in context, and let it be dismissed
  permanently. This tool is used by people already under financial stress.
- **Never imply the data is required.** Declining must be a first-class
  choice, and the tool must stay honest about what it cannot see rather than
  degrading silently.
- **Say where the data goes.** Which is nowhere. Every prompt should make the
  local-only guarantee visible at the moment of asking, because that is the
  moment somebody hesitates.

`GET /setup` already returns a `todo` list and is the natural seed for this.
Today it reports what is missing; the work is to make it report what each
missing thing is *worth*.

---

## Specific things worth building

### Data and analysis

- **Payslip parsing.** `pdfplumber` is already a dependency and the `payslips`
  table already exists in the schema. Parsing is unimplemented.
- **Net worth over time.** The `manual_balances` table exists and is exposed
  via the API, but nothing charts it yet.
- **Sinking-fund tracking.** The tool identifies lumpy annual bills and tells
  you the monthly figure to set aside. It does not yet track whether you did.
- **Forecasting.** "On the current trajectory, here is the position in six
  months" — using only observed data, with the uncertainty stated.
- **Multi-account reconciliation.** Transfers between a household's own
  accounts are excluded, but the tool cannot yet confirm both sides were
  imported.

### Reach

- **More bank formats.** The parser sniffs layouts rather than assuming one,
  but it has been tested hardest against one bank. Every format someone
  reports makes it work for more people.
- **Entitlement modules for other countries.** The pattern is
  `config/nz_rates.yml` plus `analysis/entitlements.py`, gated on
  `household.country`. This is where households lose the most money, and every
  country has its own rules.
- **Regional merchant rules.** `config/rules.yml` is NZ-heavy today.
- **Rate verification helper.** The NZ constants ship unverified by design. A
  guided flow that walks somebody through checking each one against the
  official source — and stamps `verified: true` — would remove the biggest
  caveat in the product.

### Experience

- **A guided first run.** Currently `household.yml` is hand-edited. An
  in-browser editor would open this to people who will never touch YAML.
- **Report history.** Reports are generated per date; there is no way to page
  back through them in the UI.
- **Translation.** The report is read aloud at kitchen tables. It should be
  possible to read it in the language spoken there.

---

## Deliberately not on this list

These are not "not yet". They are "no", and they are why the tool can be
trusted with the data it holds.

- **Bank account linking / open banking.** Handing over credentials is exactly
  the risk this project exists to avoid. CSV export is less convenient and far
  safer.
- **Any cloud sync, account system or telemetry.** See [SECURITY.md](SECURITY.md).
- **Investment or product recommendations.** The tool does arithmetic and
  points at official sources. It does not advise, and it will not carry
  affiliate links.
- **Gamification of spending.** Streaks and badges applied to a household's
  grocery budget turn financial stress into a game somebody is losing.
- **Anything that ranks or shames.** No comparisons against "households like
  yours". The report is read by children.
