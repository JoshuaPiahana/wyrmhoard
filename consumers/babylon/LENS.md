# The Richest Man in Babylon, as a lens

**Whose opinion:** George S. Clason's, from *The Richest Man in Babylon*
(1926), as this lens reads it. Wyrmhoard holds no opinion; a household that
follows another school picks another lens.

**What it values:** a part of all you earn is yours to keep. What we call
necessary grows to meet income unless we protest. Enjoyment is budgeted, not
forbidden. Gold that is kept should labour; gold that is lent should be
guarded; the dwelling should be owned; the future should be provided for;
and the surest increase is in the ability to earn.

This file is the lens in prose. It is written for a reader who cannot ask a
follow-up question - an agent driving Wyrmhoard over MCP, or a person with
the API in front of them - so that the lens can be applied without running
`babylon.py`. The program computes the first two cures today; the other
five are stated here and follow.

Two rules hold for every cure. The number, then what Babylon asks, then the
move it would make - never a verdict on the household, and never shame,
because the reading is read at a kitchen table with children present. And
nothing is dropped: a group of spending the lens cannot place is counted
and named.

The window is the last thirteen complete fortnights, aligned to pay day
(`anchor` in the series reply), and the thirteen before them are "before".
Fortnights, because the household is paid fortnightly; calendar months hold
two pay days sometimes and three others.

---

## 1. Start thy purse to fattening

> "For every ten coins thou placest within thy purse take out for use but
> nine. Thy purse will start to fatten at once."

**Reads:** `GET /series?direction=in&period=fortnight` and
`GET /series?direction=out&period=fortnight`. Earned is every income
category except those `lens.yml` lists under `earnings_exclude` (gifts,
help from family - real, stated separately, not earned). Spent is every
spending row including `unknown`; uncategorised money out is still money
out. Kept is earned minus spent, fortnight by fortnight.

**Says:** the typical fortnight's earned, spent and kept (medians), the
kept share over the window, and the tenth in dollars. The gap is the tenth
minus what was kept.

**Asks:** move the tenth to a pot on pay day, before the rest is paid. The
tenth first; the nine-tenths is what is left to live on.

**A judgement inside it:** the whole of a loan repayment counts as spending
here, principal included. The roof is not gold in the purse. The dwelling
has its own cure (5), and a lens that disagrees - one that counts principal
as saving - is a different lens.

## 2. Control thy expenditures

> "That which each of us calls our 'necessary expenses' will always grow to
> equal our incomes unless we protest to the contrary. … Budget thy
> expenses that thou mayest have coins to pay for thy necessities, to pay
> for thy enjoyments and to gratify thy worthwhile desires without spending
> more than nine-tenths of thy earnings."

**Reads:** `GET /taxonomy` for the household's own groups, then the
spending series by group. `lens.yml` maps group keys to Babylon's purposes:
necessities and enjoyments (the book does not separate enjoyments from
worthwhile desires by arithmetic, and neither does this lens). A key not
mapped is unplaced - counted, named. Then
`GET /series?by=merchant&direction=out&period=fortnight&category=…` for the
categories in each purpose, over the window, top eight shops and the rest
folded into one row that says how many it stands for.

**Says:** necessities and enjoyments per typical fortnight and as a share
of earnings; the same for the window before; and the shops each purpose
went to, with total, visits and per-fortnight. The largest shop in each
purpose is named as a fact.

**Asks:** that both purposes fit inside nine-tenths of earnings. Of the
largest enjoyment, Babylon's question is whether it was decided, not
whether it was enjoyed. Of the largest necessity, Babylon's warning is that
what is called necessary grows to meet income.

## 3. Make thy gold multiply

> "Put each coin to labouring that it may reproduce its kind."

**Reads:** `GET /accounts` for accounts in the `savings` role and their
last balance; `GET /balances` for anything typed in; the income series for
any category that is interest.

**Says:** what the pots hold and what the ledger shows them earning. On
most ledgers that is "nothing recorded", and the lens says so rather than
inventing a rate.

**Asks:** that kept coins earn. It does not say where - that is a product
recommendation, and this project never makes one.

## 4. Guard thy treasures from loss

> "Guard thy treasure from loss by investing only where thy principal is
> safe."

**Reads:** `GET /accounts` for liabilities; the spending series for
`bnpl`, `loan_interest` and `bank_fees` over the window.

**Says:** what is owed and on what; whether any interest or fee left the
purse for consumer credit. A household with no card debt and no
buy-now-pay-later has a strength here, and the lens names it - that is not
praise, it is a fact worth knowing before the next decision.

## 5. Make of thy dwelling a profitable investment

> "Own thy own home."

**Reads:** `GET /loans` for each loan's balance, rate, and the interest and
principal in each repayment.

**Says:** interest and principal per fortnight, and the years to clear at
the current pace. Whether to pay faster is a decision the other cures
inform; this one only states the arithmetic.

## 6. Insure a future income

> "Provide in advance for the needs of thy growing age and the protection
> of thy family."

**Reads:** `GET /payslips` for retirement contributions, employee and
employer; `GET /recurring` for insurance.

**Says:** contributions per fortnight and as a share of gross; what
insurance is paid and how often.

## 7. Increase thy ability to earn

> "Cultivate thy own powers, to study and become wiser, to become more
> skilful."

**Reads:** the income series, this window against before;
`GET /household` for `upside` - income the household has chosen not to
budget on.

**Says:** the earned trend, and names the upside income as "not budgeted,
by your choice". Babylon's principle is stated; there is no figure for it.

---

## What this lens cannot see

Every reading carries `cannot_see`, drawn from `GET /setup`: the share of
spending not yet categorised (counted as spent, but not placed), and the
todo list - an account money arrives from that was never imported, a home
with no value recorded. It is never folded away, because the last time a
tool here reported confidently over a hole in the data it told a household
they were missing money that was arriving in an account it had not been
given.
