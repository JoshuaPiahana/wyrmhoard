# Consumers

A consumer is a program that reads from Wyrmhoard and holds an opinion about
what it finds. The dashboard is one. The MCP server is one. Anything under this
directory is one too, and the rule for all of them is the same: they talk to
`http://localhost:8080/api` and nothing else. `api/tests/test_layering.py`
checks that no program here imports `wyrmhoard` directly.

## Why this directory exists

The store underneath Wyrmhoard is meant to be neutral. A supermarket receipt is
a record of what was bought; "how much did we spend" and "how much of it was
fresh food" are two questions asked of the same forty lines, and the second is
not a finance question at all.

If that is true, a second domain should be able to read the store without the
core changing. `grocery_health/` is the test of that claim - and it passed,
with `git diff --stat api/wyrmhoard/` empty.

## The rule that decides what lives here

> Could two reasonable households disagree about this, without either being
> wrong about the facts?

If yes, it is an opinion, and it belongs in a consumer. Whether yoghurt is
"fresh" or "processed" is one. Whether rent is "unavoidable" was another, and
it lived in the dashboard until it was taken out. The core stores what the
shop said, verbatim, and has no view.

If no - it is a fact, or arithmetic on facts - it belongs in the core, where
every consumer gets the same answer.

## When an opinion moves into the core

When two consumers want the same one. The finance groups in `config/rules.yml`
made that move because the dashboard and the agent were about to hold separate
copies and drift. A health vocabulary will make it the day a second consumer
wants it, and not before. Until then each consumer owns its own, beside its
code, where changing it is a one-file edit with no release.

## Writing one

1. Read from the API. `GET /documents` lists receipts without product names;
   `GET /documents/{id}` returns the lines. That split is deliberate - product
   names are the most revealing thing the ledger holds, and reading them is
   meant to be a separate act.
2. Keep your vocabulary in a file beside your code, and say in it whose
   opinion it is.
3. Never drop a line you cannot place. Count it, name it, and say so. A split
   that quietly leaves lines out is a total that looks complete and is not.
4. Do not import `wyrmhoard`. If you need something the API does not serve,
   that is a read path with a real caller, and worth asking for.
