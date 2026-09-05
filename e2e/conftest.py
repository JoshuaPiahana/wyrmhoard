"""
Shared fixtures for the browser tests.

These run against the real stack - nginx serving the real dashboard, talking
to the real API over the compose network - because the failures worth
catching here are integration failures. A unit test cannot tell you that the
JSON key was renamed and the page now shows dashes forever.
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import ConsoleMessage, Page

BASE_URL = os.environ.get("WYRMHOARD_E2E_URL", "http://web")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture
def console_errors(page: Page) -> list[str]:
    """
    Collect console errors and failed requests for the duration of a test.

    A dashboard that renders but throws on every interaction is broken in a
    way screenshots do not reveal, so several tests assert this stays empty.
    """
    errors: list[str] = []

    def on_console(msg: ConsoleMessage) -> None:
        if msg.type == "error":
            errors.append(f"console: {msg.text}")

    page.on("console", on_console)
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on(
        "requestfailed",
        lambda req: errors.append(f"requestfailed: {req.url} {req.failure}"),
    )
    return errors


@pytest.fixture
def dashboard(page: Page, base_url: str, console_errors: list[str]) -> Page:
    """A loaded dashboard with its first data render already complete."""
    page.goto(base_url, wait_until="networkidle")
    # The hero number starts as an em dash placeholder in static HTML; waiting
    # for it to change is how we know the binding layer actually ran.
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-bind=\"headline.number\"]');"
        "return el && el.textContent.trim() !== '' && el.textContent.trim() !== '—'; }",
        timeout=20_000,
    )
    return page
