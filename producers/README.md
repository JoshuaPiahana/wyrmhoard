# Producers

A producer is a program that puts data into Wyrmhoard. It is not a plugin,
there is no interface to implement, and nothing here is imported by the core.
A producer talks to `http://localhost:8080/api` like any other client would.

**Wyrmhoard itself never fetches anything.** That promise is enforced by
`api/tests/test_offline.py`, which bans network clients from the core outright.
Anything that has to be collected — a receipt downloaded from a shop, a
statement pulled from a bank — is collected by a producer the household chose
to run, with the trade-off in front of them.

## Why parsers live here and not in the core

A supermarket redesigns its invoice on its own schedule. A parser for that
layout inside `api/wyrmhoard/` would mean cutting a release of a household
finance tool because a shop changed its letterhead.

So the split is:

| | Where | Why |
|---|---|---|
| Knowing what a particular PDF looks like | **a producer** | changes on somebody else's clock |
| Defining the shape a document must arrive in | the core | changes when this project decides it does |

`api/tests/test_no_retailer.py` enforces it: no module under `api/wyrmhoard/`
may name a shop.

## The contract

`POST /documents` with a parsed document. Four fields are required on every
line — `description`, `quantity`, `unit`, `line_total` — because those are the
only four that three real documents from two retailers all agreed on.

**The lines must add up to the stated total.** The core has no opinion about
whether a checkout bag is a product or a fee; one shop bills it each way. It
only requires the arithmetic to close, which leaves that judgement here, with
the shop that made it.

A document that does not balance is refused, and the message says by how much.

See `docs/PRODUCERS.md` for the six provenance fields and the naming rules, and
`producers/woolworths_online/` for a working example.

## Writing one

1. Parse whatever your source emits into the shape above
2. Keep the source's own words — put its category string in `source_category`
   verbatim and never translate it. Wyrmhoard maps it to the household's
   vocabulary later, in a decision that stays revisable
3. Name yourself: `tool:your-source`, `agent:whatever`, `human:something`
4. Say when the document was *true*, not when you read it
5. Keep the original line in `raw`. Whatever you did not model, somebody will
   want later

If adding your source needs a change to Wyrmhoard's schema, the shape is too
specific and that is worth raising rather than working around.

## Honesty obligation

If running your producer means a household's data passes through somebody
else's servers, say so in its README, above the fold. That is the whole reason
every stored row records which producer supplied it.
