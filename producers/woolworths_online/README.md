# Woolworths NZ online-order invoice

Reads the PDF invoice emailed for a Woolworths NZ online order and submits it
to Wyrmhoard as an itemised document.

**Where your data goes:** nowhere. This program opens the file you point it at
and posts to `http://localhost:8080`. It has no credentials, makes no outbound
request, and never contacts Woolworths. You fetch the invoice yourself, the
same way you already receive it — by email.

## Use

```bash
python woolworths_online.py ~/Downloads/invoice.pdf --dry-run   # print, submit nothing
python woolworths_online.py ~/Downloads/invoice.pdf             # submit
```

Requires `pdfplumber`. Re-running on the same invoice is safe — Wyrmhoard
fingerprints it and stores it once.

Output:

```
invoice.pdf: 44 lines summing 315.73, invoice states 315.73, dated 2026-07-28
Stored 44 lines.
  linked to Woolworths Online Favona on 2026-07-29 (medium confidence)
```

`medium` because the card settled the day after the invoice was issued. A
same-day match reports `high`. Where two transactions fit equally well nothing
is linked and both are named, because attaching a receipt to the wrong shop is
worse than leaving it unattached.

## What it does not do

- **In-store receipts.** A different format entirely — no departments, but a
  card's last four digits and a timestamp. That is a separate producer.
- **Bulk.** One invoice per run. There are not enough of them for that to matter.
- **Fetch anything.** Deliberately.

## Coverage, honestly

Online orders are a minority of most households' supermarket spend — on the
ledger this was built against, 24 shops out of 183, though a disproportionate
share of the money because the baskets are larger. Everything this producer
cannot see stays as an ordinary bank transaction with a single category, and
Wyrmhoard reports which documents it has rather than implying the picture is
complete.

## The awkward bits of the format

Every one of these came from a real invoice and each broke a version of this
parser:

- **The description contains spaces**, so the columns can only be found from
  the right — quantity, unit, quantity, unit, unit price, amount.
- **Loose produce is weighed.** `2.000 kg` ordered, `2.020 kg` supplied,
  charged for what arrived. Quantity is fractional and the unit is not `ea`.
- **Substitutions share a ref** with the item they replaced, are listed at their
  own price and charged at the original's. Two lines, because both products
  entered the house and the household was billed for both.
- **Short supply.** Ordered two, one arrived, charged for one. The submitted
  quantity is what arrived; `raw` keeps the ordered figure.
- **Descriptions wrap** onto a line of their own, which is not a product.
- **The page footer also wraps**, mid-sentence, so its continuation lines start
  lower-case exactly like a wrapped description. Without care this produces a
  line called `Pick up Fee refund. Find Olive at https://…` — and because the
  amounts are untouched the document still reconciles, so nothing downstream
  ever notices.
- **Fees sit below the subtotal** and are submitted as lines, because Wyrmhoard
  requires the lines to close and has no opinion about whether a bag is a
  product.
- **Free items exist.** A promotional giveaway at $0.00, ten of them.
- **The header lies about GST**, claiming amounts exclude it. They include it —
  the arithmetic proves it, and the in-store receipt says so outright.

## Tests

```bash
./hoard test
```

They run on invoice *text* rather than a PDF, so no real document is needed and
none is committed. Reading the PDF is three lines of pdfplumber with nothing to
get wrong; the parsing is where the bugs live.
