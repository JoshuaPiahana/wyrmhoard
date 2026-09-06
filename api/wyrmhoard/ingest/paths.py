"""
Filename safety, shared by every way a document gets in.

This lived in `api.py` and so protected only the HTTP upload. The MCP
`import_document` tool - the one an AI agent drives, and therefore the one most
likely to be handed a path somebody else chose - had no checks at all beyond
"does this file exist". It would read anything the container could reach, and
the container mounts config, data and reports read-write.

That asymmetry is the reason this is a module rather than a helper: a control
that guards one door and not the other is not a control. Anything that takes a
filename or a path from outside uses these two functions.

The risk is modest today - loopback only, one household - but "it is only
reachable locally" is exactly the reasoning that ages badly the day somebody
puts the dashboard behind a tunnel to show a friend, or points an agent at a
path from an email.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

# What a document may be. Anything else is refused rather than guessed at:
# the old code treated "not .pdf" as "is a CSV", so a .exe or a .yml would be
# fed to the CSV parser and produce a confusing parse failure instead of a
# clear rejection.
DOCUMENT_SUFFIXES = {".csv", ".pdf"}


def safe_upload_name(raw_name: str | None) -> str:
    """
    Reduce an uploaded filename to something safe to join onto a directory.

    The browser sends this, so it is attacker-controlled in principle: a name
    like "../../../etc/cron.d/x" would otherwise escape the inbox entirely and
    write wherever the process can reach. Only the final path component is
    kept, separators and traversal segments are dropped, and the result is
    verified to stay inside its directory by `resolve_within`.
    """
    name = PurePosixPath((raw_name or "").replace("\\", "/")).name
    name = name.strip().lstrip(".")
    # Parentheses are kept: browsers name repeat downloads "Export (1).csv"
    # and the filename is shown back to the user in the import report, so
    # mangling it for no security gain just makes the report confusing.
    name = re.sub(r"[^A-Za-z0-9._ ()-]", "_", name)[:120].strip()
    return name or "upload.csv"


def resolve_within(root: Path, candidate: Path | str) -> Path:
    """
    Resolve a path and prove it stayed inside `root`.

    Belt to `safe_upload_name`'s braces, and the only protection available when
    the caller is handed a whole path rather than a filename. Symlinks are
    resolved first, so a link inside the directory pointing out of it is caught
    rather than followed.

    Raises ValueError with a message safe to show the caller - it names the
    directory they may use, not the path they tried, so a probe learns nothing
    about the filesystem.
    """
    root = Path(root).resolve()
    target = (
        (root / candidate).resolve()
        if not Path(candidate).is_absolute()
        else Path(candidate).resolve()
    )
    if not target.is_relative_to(root):
        raise ValueError(f"Path is outside {root}. Documents must be inside that directory.")
    return target


def check_suffix(path: Path) -> str:
    """
    The document kind, or a refusal.

    Returns "csv" or "pdf". Raises ValueError naming what is accepted, because
    "unsupported file" tells somebody nothing about what to do next.
    """
    suffix = path.suffix.lower()
    if suffix not in DOCUMENT_SUFFIXES:
        raise ValueError(
            f"'{suffix or path.name}' is not a document Wyrmhoard reads. "
            f"Expected one of: {', '.join(sorted(DOCUMENT_SUFFIXES))}."
        )
    return suffix.lstrip(".")
