# grocery_health

Of a supermarket shop, how much was fresh food, how much was processed, how
much was drink, and how much was not food at all?

    pip install -r requirements.txt
    python grocery_health.py                    # every receipt in the ledger
    python grocery_health.py --since 2026-07-01

Against a real online invoice:

    2026-07-28  WW Kelvin Grove  $315.73
      Fresh and whole             $   157.83   50.0%
      Processed and packaged      $    89.91   28.5%
      Drinks, non-alcoholic       $    26.60    8.4%
      Household, not food         $    21.60    6.8%
      Personal and baby care      $    16.29    5.2%
      Fees, not goods             $     3.50    1.1%

## What it proves

That a health question can be answered from Wyrmhoard's store with no change
to Wyrmhoard. This program imports nothing from the core; it reads
`/documents` over HTTP and applies its own vocabulary from `groups.yml`. The
core did not learn the word "processed".

## Whose opinion this is

The lens's. Calling dairy "fresh" is a choice, and `groups.yml` says so next
to the line that makes it. A household that disagrees edits the file.

## Where it will need work

- **In-store eReceipts carry no departments.** The online invoice says "Deli &
  Chilled Foods" above every cheese; the till receipt says nothing. Those
  lines fall through to description matching, and the patterns for that are
  three long. Sixty-one percent of the household's grocery spend is in-store,
  so that is the next real test.
- **Lines it cannot place are counted and named, never dropped.** Add a
  pattern to `groups.yml` when one appears.
