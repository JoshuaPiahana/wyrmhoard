# Akahu: bank transactions without the monthly export

Fetches settled transactions from [Akahu](https://akahu.nz) and submits them to
Wyrmhoard as a bank CSV, the same shape your bank's own export takes.

**Where your data goes: through Akahu.** Akahu is an open-banking aggregator.
Running this means Akahu holds a live connection to your bank and a copy of
your transactions on its servers, refreshed daily, for as long as the
connection stands. That is the product, not a side effect. This program then
copies Akahu's copy to `http://localhost:8080`, and every row it stores is
marked `tool:akahu` so you can always see which of your ledger passed through
a third party. Every other producer in this repository is entirely local.
Downloading a CSV yourself still involves nobody else at all.

A personal Akahu app can read accounts and transactions. It cannot make
payments, and this program does not ask for anything it does not need.

## What it holds

Two tokens, read from environment variables and written nowhere:

```
AKAHU_APP_TOKEN=app_token_...
AKAHU_USER_TOKEN=user_token_...
```

Wyrmhoard never sees them. It has no credential store, no way to use one and
no way to make an outbound request, which is the reason this lives in
`producers/` as a program you chose to run. Keep the tokens out of the
repository tree and out of your shell's environment: a file under `~/.config/`
that only you can read (`chmod 600`), loaded into a subshell for this one
command. One line in `~/.bashrc` does that:

```bash
akahu() { ( set -a; . ~/.config/wyrmhoard/akahu.env; set +a; python3 ~/finance/producers/akahu/akahu.py "$@" ); }
```

`env | grep AKAHU` afterwards shows nothing - the tokens existed for that
command and are gone. Revoke them at [my.akahu.nz](https://my.akahu.nz) if
you stop using this.

## Use

The first run is a dry run, and it should be checked against internet banking:

```bash
python akahu.py --start 2026-09-01 --dry-run
```

It lists every account Akahu can see, then the transactions it would submit,
with Akahu's own timestamp beside the date that would be stored. Two things
are worth your eyes before the first real run:

- **The account numbers.** They should match the accounts already on the
  dashboard exactly, suffix included. If they do not, the same account will
  appear twice, once from CSVs and once from Akahu.
- **The dates.** Akahu stamps transactions in UTC, and a Kiwibank feed uses
  New Zealand midnight expressed in UTC (`T12:00Z`, or `T11:00Z` in daylight
  time) rather than the midnight-UTC their docs show. The producer converts;
  see "Dates" in the module docstring for the rule. Verified on a week of
  real Kiwibank data. A different bank may do it differently, which is why
  the dry run prints both stamps.

Then:

```bash
python akahu.py --start 2026-09-01
```

Standard library only; nothing to install. `--account` restricts to one
account by number, Akahu name or id, and repeats. `--end` defaults to today.

### `--start` is required, and where it goes depends on what is already there

Wyrmhoard identifies a transaction by its account, date, description, amount
and balance. Akahu's description is the bank's "with minor cleanup", and that
is enough for a transaction already imported from a CSV to look like a
different one when it arrives again through Akahu. Overlap the two sources and
spending is counted twice. So there is no default window.

**Keeping your CSV history:** set `--start` to the day after your last CSV
export and keep using that value. Re-running is safe: rows already in the
ledger are skipped and only the new days land.

**Replacing it with Akahu's:** delete the CSV imports first (the Data tab, or
`DELETE /imports/<filename>`), take a backup before you do, and run once with
`--start` before Akahu's history begins - it reaches back two years and the
dry run reports the earliest date it actually returned. Anything older than
that in your CSVs is gone with them, so look at the gap before choosing this.
After the backfill, later runs can use any recent `--start`; Akahu rows dedupe
against Akahu rows exactly, and there is no reason to pull two years a day.

Manual category corrections and receipt links ride on the rows they were made
against, so they do not survive a replacement. The import log and a backup
tell you what there was.

### Running it daily

Akahu refreshes each connection once a day on its own schedule. A cron entry
in WSL that runs after that, with the tokens exported in the same line or
sourced from a file only you can read, is enough:

```
30 7 * * *  . ~/.config/wyrmhoard/akahu.env && python3 ~/finance/producers/akahu/akahu.py --start 2026-09-01
```

Or, with the `akahu` function from "What it holds", just `akahu --start 2026-09-01`
by hand whenever you want the ledger current.

Each run submits one file, `akahu-<date>.csv`, into `data/inbox/`, and the
import log records how many rows it carried and how many were new.

## What it does not do

- **Pending transactions.** A separate Akahu endpoint, deliberately unused.
  They change, and a changed row is a new row to Wyrmhoard.
- **Trigger a refresh.** Akahu's daily one is enough for a household ledger.
- **Use Akahu's enrichment.** The merchant name and NZFCC category Akahu
  attaches are kept in the CSV as `Merchant` and `Category`, because somebody
  will want them, and ignored on import. Categorisation runs on the bank's
  own description, the same as for a CSV export, so the two paths agree.
- **Reconcile against CSV history.** See `--start` above. Doing this properly
  would mean matching on amount and date with fuzzy descriptions, and getting
  it wrong silently is worse than asking you for one date.

## Coverage, honestly

Verified on 12 September 2026 against a real Kiwibank connection: seven
accounts listed, two years fetched (3,548 rows), account suffixes matching the
bank's own CSV export, every stamp arriving as NZ midnight in UTC (the one
thing the documentation would not have predicted), and every money row and
balance matching the export line for line. The emitted CSV is proven to be
read by Wyrmhoard's sniffer in `e2e/test_akahu_producer.py`.

**What Akahu does not carry, measured against the same bank's own export:**

- **The other party's account number on the household's own transfers.**
  Akahu supplies it for payments to other people (`PAY`, direct debits) and
  not for movements between the household's own accounts - `TRANSFER TO …`
  and the `AP#` automatic payments that feed loans and sinking funds. On this
  ledger that was 773 of the 1,147 rows the export had it on. That field is
  how the core proves a transfer is internal rather than guessing from the
  word "transfer", so without it every movement between pots read as
  spending, and categorisation coverage fell from 81.5% to 48.1%. The suffix
  survives in Akahu's description text and both sides of every `AP#` are in
  the feed, so this is recoverable by this producer. **Not yet done** - see
  the open question at the end.
- **Zero-dollar informational rows on loan accounts.** `OFFSET Benefit of
  $12.18` and `Rate 6.500% (V) loaded` are rows in the bank's export and
  absent from Akahu. Loan inference reads the first to work out an offset
  loan's real interest rate, and reads repayment-change notices to see a
  refix coming. Not recoverable from Akahu. A CSV export of the loan accounts
  remains the source for those, and `--account` lets this producer leave the
  loan accounts alone so the two never overlap.

So the honest recommendation, in this README's own terms: keep your CSV
history, run this for the transaction accounts only, and do not replace loan
account history with it. Replacing everything was tried on this ledger,
measured, and restored from backup the same afternoon.

Not yet exercised: a bank other than Kiwibank, a credit card, or a KiwiSaver
account.
