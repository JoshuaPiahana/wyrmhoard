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

## Lenses

A lens is a consumer whose opinion is one named philosophy. `babylon/` is
the first: it reads the household's figures through the seven cures in *The
Richest Man in Babylon* and says what that book would say - the number, then
what the principle asks, then the move it would make. A household that
follows a different school writes a different lens, and the dashboard's
**Lens** tab lets them switch between whichever they have run.

Three files make a lens, beside the usual program and tests:

- `lens.yml` - the numbers the philosophy fixes (a tenth, a window) and its
  reading of the household's own group keys. Say whose opinion it is.
- `LENS.md` - the same opinion in prose, written for a reader who cannot ask
  a follow-up question: which figures each principle reads, from which
  endpoint, and what it says about them. An agent driving Wyrmhoard over
  MCP can apply the lens from this file alone.
- the program - reads the API, writes `reports/lenses/<name>.json` in the
  shape below. `./hoard lens <name>` runs it.

Two rules from above apply with extra force. A group key the lens cannot
place goes in `unplaced` with its total, never dropped. And every figure
says its unit and where it came from, because a lens speaks to a family
about their own money and a bare number is a guess about what it means.

### The contract

The dashboard renders any file in `reports/lenses/` that has this shape. It
knows no lens by name - `api/tests/test_layering.py` fails the build if one
appears under `web/src/` - so a second lens is a second file and nothing else.

```json
{
  "lens": "babylon",
  "title": "The Richest Man in Babylon",
  "philosophy": "One paragraph: whose opinion this is and what it values.",
  "refresh": "./hoard lens babylon",
  "generated_at": "2026-09-13T10:00:00",
  "ledger_ends": "2026-09-12",
  "unit": "NZD",
  "window": {"from": "…", "to": "…", "period": "fortnight", "complete_periods": 13},
  "readings": [
    {
      "cure": 1,
      "name": "Start thy purse to fattening",
      "principle": "Of every ten coins earned, keep one.",
      "available": true,
      "figures": {
        "kept_per_fortnight": {"value": 0.0, "unit": "NZD", "basis": "median of 13 complete fortnights", "source": "GET /series?direction=in&period=fortnight"}
      },
      "principle_asks": {"kept_share": 0.1},
      "gap": {"value": 0.0, "unit": "NZD", "basis": "…", "source": "derived"},
      "reading": "One or two sentences: the number, then what the principle asks.",
      "options": ["The move the philosophy would make, sized from the figures."],
      "series": {"title": "…", "periods": ["2026-03-04"], "rows": [{"label": "kept", "totals": [0.0]}], "target": {"label": "the tenth", "value": 0.0}},
      "tables": [{"title": "…", "columns": ["Shop", "Total", "Visits"], "units": ["text", "NZD", "count"], "rows": [["…", 0.0, 0]]}],
      "caveats": ["…"]
    }
  ],
  "unplaced": [{"group": "unknown", "total": 0.0}],
  "cannot_see": ["What the ledger cannot answer for, always stated."]
}
```

Everything inside a reading after `name` is optional; the renderer draws
what is present. A figure's `unit` is the household currency, `ratio`,
`count`, or any other word, and the dashboard formats by it. `unplaced` and
`cannot_see` are the standing footer and are never folded away.
