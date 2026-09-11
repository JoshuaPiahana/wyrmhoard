# Woolworths NZ in-store eReceipt

Reads the PDF eReceipt from a Woolworths NZ in-store shop and submits it to
Wyrmhoard as an itemised document.

Different producer to the online invoice, because it is a different format.
No departments, no substitutions, no ordered-vs-supplied columns - and two
fields the online invoice never has: **the paying card's last four digits and
the time to the minute.** Both go on the document.

**Where your data goes:** nowhere. This program opens the file you point it at
and posts to `http://localhost:8080`. It has no credentials, makes no outbound
request, and never contacts Woolworths. You fetch the receipt yourself, from
the *Woolworths* app (not the Everyday Rewards one) - each order has a share
button that emails or saves the PDF.

## Use

```bash
python woolworths_instore.py ~/Downloads/eReceipt.pdf --dry-run   # print, submit nothing
python woolworths_instore.py ~/Downloads/eReceipt.pdf             # submit
```

Requires `pdfplumber`. Re-running on the same receipt is safe - Wyrmhoard
fingerprints it and stores it once.

Output:

```
eReceipt_9424_Kelvin Grove_08Sep2026__dqgja.pdf: 28 lines summing 228.62, receipt states 228.62, dated 2026-09-08 at 17:20 on card ending 2561
Stored 28 lines.
  linked to WOOLWORTHS PALM NORTH on 2026-09-08 (high confidence)
```

## What it does not do

- **Online invoices.** The email format is a different producer - departments,
  substitutions, and a header that lies about GST all sit in
  `producers/woolworths_online/`.
- **Bulk.** One receipt per run.
- **Fetch anything.** Deliberately.
- **Guess the marker legend.** Some descriptions arrive prefixed `^` or `*`.
  The receipt does not print a legend for either, so the marker is stripped
  from the description and preserved verbatim in `raw` - the meaning is
  recoverable if the household ever finds out what it means, and no downstream
  figure is affected either way.

## Coverage, honestly

On the ledger this was built against, in-store trips are ~61% of Woolworths
spend and Woolworths is ~91% of the household's supermarket spend. Anything
this producer cannot see - a shop on a card the app is not paired with, a
receipt the household chose not to export - stays as an ordinary bank
transaction with a single category, and Wyrmhoard reports which documents it
has rather than implying the picture is complete.

## The awkward bits of the format

Every one of these came from a real receipt:

- **Two-line items.** Weighed produce and multi-quantity items print a
  description on one line and the numbers on the next. Treating the first line
  as a full item would hallucinate a product called "1.120 kg NET".
- **One-line items.** Single quantity items print description-then-amount on
  one line, with no unit price column. Fine, the schema makes unit price
  optional.
- **Undocumented prefix markers.** `^` and `*` appear on some descriptions
  with no printed legend. Stripped and stashed in `raw`.
- **No departments.** `source_category` is None on every line.
- **DD/MM/YY vs DD/MM/YYYY.** The EFTPOS block prints two-digit years; the
  footer prints four. The footer is used - two-digit dates are ambiguous the
  moment somebody imports a receipt from another year and the same shop's
  system will presumably outlast the 2000s.

## Tests

```bash
./hoard test
```

They run on receipt *text* rather than a PDF, so no real document is needed
and none is committed.
