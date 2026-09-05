<!--
  Thanks for contributing. Keep the description short; the diff shows the what,
  so use this space for the why.
-->

## What and why

<!-- What changes, and what problem it solves. -->

## How it was tested

<!-- `./kete check` output, or which specific tests. Say what you could NOT test. -->

## Checklist

- [ ] No real financial data is included in this change (use `api/kete/samples.py`)
- [ ] `./kete check` passes locally
- [ ] New behaviour has a test; a bug fix has a **regression** test
- [ ] Still works for households unlike mine — no mortgage, no children, two
      incomes, outside New Zealand (see `api/tests/test_household_shapes.py`)
- [ ] No new outbound network calls at runtime (see [SECURITY.md](../SECURITY.md))
- [ ] Numbers shown to users are honest: nothing invented, gaps acknowledged
