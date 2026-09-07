#!/usr/bin/env bash
# Refuse to let a commit through unless the tree is green.
#
# This exists because the person who owns this repo does not read the diffs.
# Instructions in CLAUDE.md ask Claude to run the checks; a hook makes it so
# whether Claude remembers, agrees, or has convinced itself this one is fine.
# It runs regardless of what the model decides, which is the whole point.
#
# Runs lint and the unit tests - about 45 seconds. Deliberately NOT the browser
# tests, which take a further two and a half minutes and would make committing
# something people avoid. Run `./hoard e2e` before pushing.
#
# Blocks by exiting 2 with the reason on stderr.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO" || exit 0

INPUT=$(cat)

# No jq here and the Windows `python` on PATH is the Microsoft Store stub, so
# the command is matched out of the raw JSON. Both `git commit` and
# `git -c core.hooksPath=… commit` have to match - the second is the form
# Claude actually uses, and matching only the literal "git commit" would let
# every one of its commits straight past.
if ! printf '%s' "$INPUT" | grep -q '"command"'; then
  exit 0
fi
if ! printf '%s' "$INPUT" | grep -qE '\bgit\b[^"]*\bcommit\b'; then
  exit 0
fi

export PATH="$PATH:/d/docker/resources/bin"
unset DOCKER_HOST   # a stale value silently overrides Docker Desktop's context

# Tell "the engine is down" apart from "your code is broken". Blocking a commit
# with a message about failing tests, when the truth is Docker is not running,
# sends somebody hunting for a bug that does not exist.
if ! docker info >/dev/null 2>&1; then
  echo "Commit blocked: Docker is not running, so the checks could not be run." >&2
  echo "Start Docker Desktop and try again." >&2
  exit 2
fi

if ! OUTPUT=$(./hoard lint 2>&1); then
  echo "Commit blocked: ./hoard lint failed." >&2
  printf '%s\n' "$OUTPUT" | grep -iE "error|failed|\.py:|\.js:" | head -20 >&2
  exit 2
fi

if ! OUTPUT=$(./hoard test 2>&1); then
  echo "Commit blocked: ./hoard test failed." >&2
  printf '%s\n' "$OUTPUT" | grep -E "FAILED|Error|assert" | head -20 >&2
  exit 2
fi

exit 0
