"""
The core must not depend on any interface.

Wyrmhoard computes; the web app, the CLI and the MCP server are three ways of
asking it to. That ordering only holds if the analysis engine can run with
none of them installed - otherwise somebody embedding it in their own program
has to install a web server to compute a number, and the project's premise
quietly stops being true.

The layering is correct today. Nothing stopped it eroding, which is what this
file is for: a convenient `from ..api import something` inside an analysis
module would pass every other test in the suite.

Checked by reading imports rather than by installing things, so it runs
anywhere and names the offending file when it fails.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PACKAGE = Path(__file__).resolve().parents[1] / "wyrmhoard"

# Modules that exist to serve a particular interface. Everything else is core.
INTERFACES = {"api", "mcp_server", "cli"}

# Third-party packages that belong to one interface and must not reach core.
INTERFACE_ONLY_PACKAGES = {
    "fastapi": "web",
    "starlette": "web",
    "uvicorn": "web",
    "multipart": "web",
    "mcp": "agent",
    "typer": "cli",
    "click": "cli",
    "rich": "cli",
}


def core_modules() -> list[Path]:
    """Every module that is not an interface."""
    return [
        path
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.stem not in INTERFACES and "__pycache__" not in path.parts
    ]


def imports_of(path: Path) -> set[str]:
    """Top-level package names and internal module names this file imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative: `from . import x` / `from .analysis import y`
                if node.module:
                    found.add(node.module.split(".")[0])
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif node.module:
                found.add(node.module.split(".")[0])
    return found


def test_there_are_core_modules_to_check():
    """Guards against the discovery above silently matching nothing."""
    names = {p.stem for p in core_modules()}
    assert {"cashflow", "categorise", "db", "coach"} <= names


@pytest.mark.parametrize("path", core_modules(), ids=lambda p: p.stem)
def test_core_modules_import_no_interface_package(path: Path):
    """A core module needing FastAPI means the engine cannot stand alone."""
    offending = imports_of(path) & set(INTERFACE_ONLY_PACKAGES)
    assert not offending, (
        f"{path.name} imports {sorted(offending)}, which belongs to the "
        f"{INTERFACE_ONLY_PACKAGES[sorted(offending)[0]]} layer. "
        "Core analysis must run without any interface installed."
    )


@pytest.mark.parametrize("path", core_modules(), ids=lambda p: p.stem)
def test_core_modules_do_not_import_an_interface_module(path: Path):
    """Dependencies point inward: interfaces know about core, never the reverse."""
    offending = imports_of(path) & INTERFACES
    assert not offending, (
        f"{path.name} imports {sorted(offending)}. Interfaces depend on the "
        "core, not the other way round."
    )


def test_each_interface_is_the_only_user_of_its_dependency():
    """
    Confirms the split is real: exactly one module should import each
    interface package. If two do, the boundary has already blurred.
    """
    users: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for package in imports_of(path) & set(INTERFACE_ONLY_PACKAGES):
            users.setdefault(package, []).append(path.stem)

    assert users.get("fastapi") == ["api"], f"fastapi is imported by {users.get('fastapi')}"
    assert users.get("mcp") == ["mcp_server"], f"mcp is imported by {users.get('mcp')}"
    assert users.get("typer") == ["cli"], f"typer is imported by {users.get('typer')}"


def test_the_requirements_files_match_the_layers():
    """Each layer's dependencies are declared where that layer says they are."""
    api_dir = Path(__file__).resolve().parents[1]
    core = (api_dir / "requirements-core.txt").read_text(encoding="utf-8").lower()
    web = (api_dir / "requirements-web.txt").read_text(encoding="utf-8").lower()

    for package in ("fastapi", "uvicorn", "mcp==", "typer"):
        assert package not in core, f"{package} is pinned in the core requirements"
    assert "fastapi" in web
    assert "pandas" in (api_dir / "requirements-core.txt").read_text(encoding="utf-8").lower()
