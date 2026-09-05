#!/usr/bin/env bash
# Every static check, in the order that gives the most useful failure first.
# Run inside the api container:  ./hoard lint
set -uo pipefail
# /repo when run in the container, the repo root when run directly.
cd /repo 2>/dev/null || cd "$(dirname "$0")/.." || exit 1

fail=0

# The repo is bind-mounted from the host, so git sees an owner it does not
# recognise and refuses to read it. Harmless here - the container is ours.
git config --global --add safe.directory "$(pwd)" 2>/dev/null || true

echo "=== financial-data guard ==="
python scripts/check_no_financial_data.py --all || fail=1

echo
echo "=== ruff (lint) ==="
ruff check . || fail=1

echo
echo "=== ruff (format check) ==="
ruff format --check . || fail=1

echo
echo "=== mypy ==="
mypy || fail=1

echo
echo "=== yamllint ==="
yamllint --strict . || fail=1

echo
echo "=== shellcheck ==="
shellcheck hoard scripts/*.sh || fail=1

echo
if [ "$fail" -eq 0 ]; then
  echo "All static checks passed."
else
  echo "Static checks FAILED." >&2
fi
exit "$fail"
