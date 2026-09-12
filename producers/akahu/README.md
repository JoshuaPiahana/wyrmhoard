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
with Akahu's own timestamp beside the date that would be stored, then the
first rows whose other party this producer worked out rather than the bank.
Three things are worth your eyes before the first real run:

- **The account numbers.** They should match the accounts already on the
  dashboard exactly, suffix included. If they do not, the same account will
  appear twice, once from CSVs and once from Akahu.
- **The dates.** Akahu stamps transactions in UTC, and a Kiwibank feed uses
  New Zealand midnight expressed in UTC (`T12:00Z`, or `T11:00Z` in daylight
  time) rather than the midnight-UTC their docs show. The producer converts;
  see "Dates" in the module docstring for the rule. Verified on a week of
  real Kiwibank data. A different bank may do it differently, which is why
  the dry run prints both stamps.
- **The recovered transfers.** Each printed row names the account this
  producer decided the money went to or came from. If one is wrong, do not
  run - the rules are in "What it works out for itself" below, and a bank
  that writes its descriptions differently needs its own.

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
  spending, and categorisation coverage fell from 81.5% to 48.1%. **This
  producer puts it back** from the bank's own text - see the next section.
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

## What it works out for itself

Three rules, for the three shapes a Kiwibank feed carries when money moves
between a customer's own accounts. Nothing else is inferred, and a value
Akahu did supply is never overwritten.

**A transfer names its destination's suffix.** `TRANSFER TO J SAMPLE - 02`
on account `…-00` went to `…-02`, and the `TRANSFER FROM J SAMPLE - 00` on
`…-02` is the same money arriving. The rule fills the other party only when
an account with that suffix, on the *same* bank-branch-account prefix, is one
Akahu listed. `- 07` when there is no `-07` stays blank; so does a transfer
whose text has no suffix at all.

**An automatic payment appears on both sides.** `AP#12345678 TO J SAMPLE`
for -$23.68 on one account and `AP#12345678 FROM J SAMPLE` for +$23.68 on
another, the same day, are one payment. Each row is filled with the other's
account. The pairing has to be exact - same number, same day, amounts equal
and opposite, `TO` on the paying side - and exactly one candidate on each
side. One side alone (the other account not connected to Akahu, or the
payee somebody else) stays blank. Two candidates stay blank. It never
guesses.

**The same payment into a pot loses its number on the way.** Where the
bank's own export says `AP#12345678 FROM J SAMPLE` on the receiving pot, the
feed says `Automatic Payment Rainy day J SAMPLE` - the number gone, the
reference kept. The paying side still reads `AP#12345678 TO J SAMPLE`, and
both rows carry the same reference: the memo typed when the payment was set
up. So the pairing key is the reference instead of the number, with the same
day, the same amount and the same exactly-one-each-side test. A payment set
up with no reference cannot pair this way and stays blank, because day and
amount alone could join two unrelated payments. Seen on every sinking-fund
top-up in one household's feed, while the same payments into the loan
accounts kept their numbers - so both rules are needed.

Every row carries a `Counterparty source` column saying which of `akahu`,
`transfer suffix`, `AP# pair` or `AP reference pair` put the value there, so
the file in `data/inbox/` always distinguishes what the bank said from what
this program worked out. Wyrmhoard ignores the column. The run reports the
counts either way:

```
  other party's account: 1 from Akahu, 6 by transfer suffix, 4 by AP# pairing, 6 by AP reference; still blank: 1 transfers, 0 automatic payments
```

The "still blank" numbers are the ones to read. A transfer or automatic
payment with no other party will be categorised from its text like any other
row, which for a movement between your own pots usually means it reads as
spending. A blank is not always wrong: `TRANSFER FROM J SAMPLE - 00` where
the sender is somebody else's account with a `-00` suffix on a *different*
prefix is money arriving from outside, and it stays blank on purpose.
The dry run also prints the first few rows each rule filled, next to the
account it chose, so they can be checked against internet banking before
anything is stored.

Two things this does not do. It does not go back and fill rows already in
the ledger: a row's identity is its account, date, description, amount and
balance, so a row imported before this existed is skipped as already stored,
other party and all. And the pairing looks across *every* account Akahu
listed, not just the ones `--account` names - the loan's side of an `AP#` is
what proves the everyday account's side is internal, and it is found before
the loan is left out of the submission.

This is knowledge about one bank's descriptions and lives here for that
reason. A different bank writes different text, and the rules will simply not
fire - the counts will say so.
