# Producers: getting data into Wyrmhoard

Wyrmhoard computes. It does not go and look.

That is not a limitation to work around — it is the reason a household is
willing to point this thing at two years of their bank statements. The tool
makes **no outbound network requests at all** at runtime, and
`api/tests/test_offline.py` fails the build if that stops being true.

So anything that has to be *fetched* is fetched by something else. A person
reading their rates notice, an agent watching a mailbox, a script that checks a
council website — these are **producers**. To Wyrmhoard they are the same
thing: something outside submitting data, saying who it is and where the figure
came from.

This document is that contract.

---

## The six things every submission states

| Field | What it means | Example |
|---|---|---|
| `producer` | What created this | `tool:rates-lookup` |
| `observed_at` | The date the figure was **true** | `2026-03-01` |
| `received_at` | When Wyrmhoard stored it | *set for you* |
| `method` | How it was arrived at | `council_rv` |
| `source` | Free text: a filename, a URL, "typed by hand" | `PNCC rating notice` |
| `confidence` | How much weight the producer puts on it | `medium` |

Two of these carry most of the weight.

**`observed_at` is required and is never defaulted to today.** A council rating
value set in 2023 is not a current valuation, and quietly dating it as today
turns a stale number into a confident one. This is the same failure the project
already calls out for mortgage rates: a config copy of a derived value is a
cache that goes stale silently. If you do not know when a figure was true, find
out or do not submit it.

**`confidence` belongs to the producer and is stored as given.** Wyrmhoard
never upgrades it. A number somebody half-remembers is an `estimate`, not an
`appraisal`. Labelling it generously to make a later figure look better defeats
the whole reason the basis is recorded.

### Naming a producer

`kind:name`, lowercase, where kind is `human`, `agent` or `tool`.

```
human:dashboard      somebody typing into the web UI
human:cli            somebody running ./hoard
agent:mcp            an AI agent over the MCP server
tool:rates-lookup    a program somebody chose to run
```

There is no default and an anonymous submission is refused. The kind matters
because it says what sort of mistake to expect: a person mistypes a digit, a
scraper reads the wrong element on a redesigned page.

---

## Two ways in

### Structured facts → HTTP, on loopback

```bash
curl -s -X POST http://127.0.0.1:8000/properties/valuations \
  -H 'Content-Type: application/json' \
  -d '{
    "label": "Home",
    "value": 600000,
    "method": "council_rv",
    "observed_at": "2026-03-01",
    "producer": "tool:rates-lookup",
    "source": "PNCC rating value",
    "confidence": "medium"
  }'
```

Read it back with `GET /properties`.

Submitting the same claim twice records it once — identity is the property, the
value, the date, the method and the producer together. A producer on a schedule
can run daily without filling the table with copies. Two *different* producers
reporting the same number are two claims, because independent agreement is
genuine information where one source repeating itself is not.

Nothing is ever overwritten. A new figure is appended, so a three-year-old
rating value and last month's appraisal both survive and each keeps its own
date and basis.

There is no authentication. The API binds to `127.0.0.1` only, and that is the
control — see SECURITY.md. A producer therefore has to run on the same machine.

### Documents → the inbox

Bank exports and payslips are files, so they keep the path that already works:

```bash
cp ~/Downloads/export.csv data/inbox/
./hoard ingest
```

`data/inbox/` is bind-mounted into the container, so anything that can write a
file can deliver a document. CSVs and PDFs are both read; the format is decided
once, by suffix, and anything else is refused rather than guessed at.
Re-importing is safe — transactions are fingerprinted.

Uploading through the dashboard does the same thing and records
`producer: human:dashboard`.

---

## What a producer may and may not be

**May:** gather public information. A council's published rating value, a
government rates table, an exchange rate, anything the household could look up
themselves.

**May not: touch bank credentials.** Bank account linking and open banking are
on this project's permanent "no" list — see ROADMAP.md. Handing over banking
credentials is precisely the risk this tool exists to avoid, and wrapping that
in a "producer" does not change it. CSV export is less convenient and far
safer.

A producer is always something the household chose to run. Wyrmhoard will never
start one, and cannot: it has no way to make an outbound request.

If a producer *does* reach a third party — an agent watching a mailbox, say —
that mailbox is the one place data leaves the machine. Say so, out loud, to
whoever is running it. Deliberate, documented, and off by default.

---

## Adding a new kind of data

Copy the six columns. `property_valuations` in `api/wyrmhoard/db.py` is the
reference implementation; `api/wyrmhoard/properties.py` holds the validation.

The split matters: storage functions in `db.py` write what they are given,
and every rule about what a valid submission looks like lives in the domain
module. That way a caller cannot get a different answer by going around the
front door. The HTTP endpoint does nothing but turn a `ValueError` into a 400
with the message passed through unchanged, which is why those messages are
written as sentences a person can act on rather than field names.
