"""
The tool must not be able to phone home.

SECURITY.md makes a flat promise: "The application makes no outbound network
requests at all at runtime." That promise is the reason somebody is willing to
point this thing at two years of their bank statements, and until now the only
thing holding it up was a checkbox in the pull-request template.

A checklist catches the change somebody remembers to declare. These tests catch
the one they do not - an import added while debugging, a library that fetches
on first use, a well-meant "just check for updates".

They matter more now than they did. The project is deliberately growing a seam
that points outward: producers gather data elsewhere and submit it here. That
is the safe shape, and it stays safe only while the receiving end genuinely
cannot reach out. The moment Wyrmhoard fetches anything itself, the argument
for letting it hold this data changes, and it should change in a commit where
somebody had to edit the promise and delete a test to do it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "wyrmhoard"


def repo_root() -> Path | None:
    """
    Runs on the host (CI) and inside the api container, where only the package
    is mounted and the repo root appears at /repo - the same pattern as the
    other guards that read project files.
    """
    candidates = [Path(__file__).resolve().parents[2], Path("/repo"), Path.cwd()]
    return next((p for p in candidates if (p / "SECURITY.md").exists()), None)


# Modules that open a socket, at any level. `socket` is here even though it is
# stdlib and unglamorous: it is what everything else is built on, so allowing
# it would make the rest of this list decorative.
NETWORK_MODULES = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "http",  # http.client
    "socket",
    "socketserver",
    "ftplib",
    "smtplib",
    "telnetlib",
    "asyncio",  # open_connection / start_server
    "websockets",
    "paramiko",
}

# `urllib` is only a problem for `urllib.request` and `urllib.error`; parse and
# quote are pure string handling and are genuinely useful. Checked separately.
URLLIB_NETWORK_SUBMODULES = {"request", "error"}

# The sentence SECURITY.md must keep saying, character for character. Written
# out here so that softening it is a visible edit to a test rather than a quiet
# rewording of a document nobody diffs.
PROMISE = "The application makes **no outbound network requests at all** at runtime."


def package_modules() -> list[Path]:
    return [p for p in sorted(PACKAGE.rglob("*.py")) if "__pycache__" not in p.parts]


def imported_modules(path: Path) -> set[str]:
    """
    Every module this file imports, as dotted names.

    Returns the full path rather than the top-level package so `urllib.parse`
    can be told apart from `urllib.request`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            for alias in node.names:
                found.add(f"{node.module}.{alias.name}")
    return found


def test_there_are_modules_to_check():
    """Guards against the discovery above silently matching nothing."""
    names = {p.stem for p in package_modules()}
    assert {"api", "db", "coach", "mcp_server"} <= names


@pytest.mark.parametrize("path", package_modules(), ids=lambda p: p.stem)
def test_no_module_imports_a_network_client(path: Path):
    """
    No allowlist. If a module genuinely needs one of these, that is a decision
    worth making in the open, by editing this list and saying why.
    """
    imported = imported_modules(path)
    offending = {name for name in imported if name.split(".")[0] in NETWORK_MODULES}
    offending |= {
        name
        for name in imported
        if name.split(".")[0] == "urllib"
        and len(name.split(".")) > 1
        and name.split(".")[1] in URLLIB_NETWORK_SUBMODULES
    }

    assert not offending, (
        f"{path.name} imports {sorted(offending)}. Wyrmhoard makes no outbound "
        "network requests at runtime - see SECURITY.md. If data has to come "
        "from somewhere else, a separate producer fetches it and submits it "
        "here; see docs/PRODUCERS.md."
    )


def test_security_md_still_promises_no_outbound_requests():
    """
    The promise and the code have to move together.

    Without this, a feature that needs a lookup can be shipped by quietly
    softening one sentence in a document nobody re-reads. With it, the sentence
    and this assertion have to be changed in the same commit, which is a
    conversation rather than an oversight.
    """
    root = repo_root()
    if root is None:
        pytest.skip("repo root not reachable from here; this guard runs in CI")

    security = (root / "SECURITY.md").read_text(encoding="utf-8")
    assert PROMISE in security, (
        "SECURITY.md no longer contains the exact no-outbound-requests promise. "
        "If that was deliberate, the tests above are now wrong too, and both "
        "changes belong in one commit with the reasoning written down."
    )


def test_the_runtime_dependencies_are_not_network_clients():
    """
    An import ban is only as good as what is installed beside it.

    A library that fetches on first use never appears in an import statement of
    ours, so the check above cannot see it. This will not catch everything, but
    it catches somebody adding an HTTP client to the core layer and reaching for
    it later.
    """
    api_dir = Path(__file__).resolve().parents[1]
    for layer in ("core", "web", "mcp", "cli"):
        pinned = (api_dir / f"requirements-{layer}.txt").read_text(encoding="utf-8").lower()
        for banned in ("requests==", "aiohttp==", "urllib3=="):
            assert banned not in pinned, f"{banned} is pinned in the {layer} layer"

    # httpx is the FastAPI TestClient transport and belongs to dev only. If it
    # ever appears in a shipped layer, something at runtime is making requests.
    for layer in ("core", "mcp", "cli"):
        pinned = (api_dir / f"requirements-{layer}.txt").read_text(encoding="utf-8").lower()
        assert "httpx" not in pinned, f"httpx is pinned in the {layer} layer, not just dev"
