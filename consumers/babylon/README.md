# Babylon: the household's figures, read through one old book

The first lens. It reads what Wyrmhoard computed and says what *The Richest
Man in Babylon* would say about it - the number, then what the principle
asks, then the move it would make. `LENS.md` is the opinion in full;
`lens.yml` is the few numbers it fixes and its reading of the household's
own spending groups.

```bash
./hoard up                 # the API has to be running
./hoard lens babylon       # writes reports/lenses/babylon.json
./hoard lens babylon --text   # and prints the readings
```

Then open the dashboard's **Lens** tab. Running it again after the next
import refreshes the reading; the tab says when it was read and where the
ledger ended, so a stale one is never mistaken for a fresh one.

Anywhere with a Python and PyYAML, against a running stack:

```bash
python babylon.py --api http://localhost:8080/api
```

## What it holds and what it does not

It imports nothing from `wyrmhoard`, talks to the API over HTTP like any
other client, and makes no other request. The reading is a file under
`reports/`, which is gitignored because it holds the household's real
numbers, and it is served to the dashboard by nginx as a plain directory -
the API has no part in it, because a lens holds an opinion and the core
does not.

All seven cures are computed: keep a tenth; control expenditure, down to
which shops each purpose went to; what the pots hold and earn; what is owed
beyond the roof; the roof itself, interest against principal; what provides
for the future; and what was earned, this window against the last. Three
of them read what the household holds rather than what moved, so they are
only as complete as the accounts imported and the home recorded - the
reading says so where it matters.

It is arithmetic on a household's own records and one book's opinion about
it. It is not financial advice, and it recommends no product.

## Editing the opinion

`lens.yml` is yours. A household that renamed its spending groups will see
them all under "not placed" until it maps them there; one that thinks a
tenth is too little changes one number. `test_babylon.py` checks the
arithmetic on synthetic figures and never needs the API.
