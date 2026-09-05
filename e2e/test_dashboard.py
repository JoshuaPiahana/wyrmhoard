"""
Browser reliability tests for the dashboard.

Scope, deliberately: does the page load, does real data reach the screen, do
the controls work, is it usable on a phone, and is it accessible. Not visual
regression - a design that is about to be replaced does not need pixel
pinning, but the data contract underneath it does.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect

TABS = ["overview", "spending", "actions", "repeats", "entitlements", "progress", "data"]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def test_page_loads_with_the_right_title(dashboard: Page):
    expect(dashboard).to_have_title(re.compile("wyrmhoard", re.I))


def test_no_console_errors_on_load(dashboard: Page, console_errors: list[str]):
    assert console_errors == [], f"Dashboard logged errors on load: {console_errors}"


def test_real_data_reaches_the_page(dashboard: Page):
    """
    The binding layer is the thing most likely to break silently: rename a
    JSON key and every value quietly becomes a dash. This asserts money
    actually rendered.
    """
    hero = dashboard.locator('[data-bind="headline.number"]')
    expect(hero).to_contain_text(re.compile(r"\$[\d,]+"))

    income = dashboard.locator('[data-bind="summary.typical_month.income_median"]')
    expect(income).to_contain_text(re.compile(r"\$[\d,]+"))


def test_no_binding_placeholder_survives_render(dashboard: Page):
    """No element the JS is responsible for should still show its placeholder."""
    stale = dashboard.evaluate(
        """() => Array.from(document.querySelectorAll('[data-bind]'))
              .filter(el => el.offsetParent !== null && el.textContent.trim() === 'Loading…')
              .map(el => el.dataset.bind)"""
    )
    assert stale == [], f"Elements never bound: {stale}"


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tab", TABS)
def test_every_tab_opens_and_shows_content(dashboard: Page, tab: str, console_errors):
    dashboard.click(f'nav.tabs button[data-tab="{tab}"]')
    panel = dashboard.locator(f'[data-panel="{tab}"]')

    expect(panel).to_be_visible()
    expect(panel.locator("h2, h3").first).to_be_visible()
    assert console_errors == [], f"Opening '{tab}' logged errors: {console_errors}"


def test_only_one_panel_is_visible_at_a_time(dashboard: Page):
    dashboard.click('nav.tabs button[data-tab="spending"]')
    visible = dashboard.evaluate(
        "() => Array.from(document.querySelectorAll('[data-panel]'))"
        ".filter(p => !p.hidden).map(p => p.dataset.panel)"
    )
    assert visible == ["spending"]


def test_selected_tab_is_marked_for_assistive_tech(dashboard: Page):
    dashboard.click('nav.tabs button[data-tab="repeats"]')
    expect(dashboard.locator('nav.tabs button[data-tab="repeats"]')).to_have_attribute(
        "aria-selected", "true"
    )


def test_tab_choice_survives_a_reload(dashboard: Page):
    dashboard.click('nav.tabs button[data-tab="progress"]')
    dashboard.reload(wait_until="networkidle")
    expect(dashboard.locator('[data-panel="progress"]')).to_be_visible(timeout=15_000)


# ---------------------------------------------------------------------------
# Content that must actually be populated
# ---------------------------------------------------------------------------
def test_spending_table_has_rows(dashboard: Page):
    dashboard.click('nav.tabs button[data-tab="spending"]')
    rows = dashboard.locator('[data-list="categories"] tr')
    expect(rows.first).to_be_visible()
    assert rows.count() > 3


def test_findings_are_rendered_with_severity(dashboard: Page):
    dashboard.click('nav.tabs button[data-tab="actions"]')
    findings = dashboard.locator('[data-list="findings"] .finding')
    expect(findings.first).to_be_visible()

    classes = dashboard.evaluate(
        """() => Array.from(document.querySelectorAll('[data-list="findings"] .finding'))
              .map(el => el.className)"""
    )
    assert any(sev in " ".join(classes) for sev in ("critical", "high", "medium", "low", "win"))


def test_the_group_bar_adds_up_to_the_full_width(dashboard: Page):
    """A stacked bar whose segments do not total 100% is visibly wrong."""
    total = dashboard.evaluate(
        """() => Array.from(document.querySelectorAll('[data-list="groupbar"] span'))
              .reduce((a, el) => a + parseFloat(el.style.width || 0), 0)"""
    )
    assert 99.0 <= total <= 101.0, f"Group bar segments total {total}%"


def test_entitlements_page_carries_its_caveat(dashboard: Page):
    """
    The unverified-rates warning is a safety feature, not decoration. If the
    rates are unverified the page must say so.
    """
    dashboard.click('nav.tabs button[data-tab="entitlements"]')
    banner = dashboard.locator("#ent-banner")
    expect(banner).to_be_visible()
    assert "estimate" in banner.inner_text().lower() or "verified" in banner.inner_text().lower()


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------
def test_report_can_be_built_from_the_ui(dashboard: Page):
    dashboard.click('nav.tabs button[data-tab="actions"]')
    dashboard.click("#btn-report")
    expect(dashboard.locator("#report-status")).to_contain_text(
        re.compile("reports/|family-meeting"), timeout=30_000
    )


def test_snapshot_can_be_taken_from_the_ui(dashboard: Page):
    """
    Asserts the note appears rather than counting rows: snapshots are keyed by
    date, so taking a second one on the same day replaces the first and the
    row count does not move. Counting made this pass or fail depending on
    which tests had run before it.
    """
    dashboard.click('nav.tabs button[data-tab="progress"]')

    note = f"e2e run {uuid4().hex[:8]}"
    dashboard.fill("#snap-note", note)
    dashboard.click("#btn-snapshot")

    expect(dashboard.locator('[data-list="snapshots"]')).to_contain_text(note, timeout=20_000)


def test_csv_upload_reports_what_the_parser_decided(dashboard: Page, tmp_path):
    """
    The parser's confidence must reach the human. A silent import is how a
    misread export becomes a month of wrong charts.
    """
    csv = tmp_path / "upload.csv"
    csv.write_text(
        "38-9014-0123456-00,01-08-2025,NEW WORLD ASHHURST,,,,,,,,,,,55.20,-55.20,900.00\n"
        "38-9014-0123456-00,02-08-2025,Z ASHHURST,,,,,,,,,,,80.00,-80.00,820.00\n",
        encoding="utf-8",
    )

    dashboard.click('nav.tabs button[data-tab="data"]')
    dashboard.set_input_files("#file", str(csv))

    result = dashboard.locator("#import-result")
    expect(result).to_contain_text("upload.csv", timeout=30_000)
    expect(result).to_contain_text(re.compile("confidence", re.I))


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------
def test_api_failure_shows_a_message_rather_than_a_blank_page(page: Page, base_url: str):
    """If the API is down the user must be told, not left staring at dashes."""
    page.route("**/api/**", lambda route: route.abort())
    page.goto(base_url, wait_until="domcontentloaded")

    expect(page.locator("#banners")).to_contain_text(
        re.compile("cannot reach the api", re.I), timeout=20_000
    )


# ---------------------------------------------------------------------------
# Responsive and accessible
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("width,height", [(375, 812), (768, 1024), (1440, 900)])
def test_no_horizontal_overflow_at_any_size(page: Page, base_url: str, width, height):
    """
    Wide tables must scroll inside their own container, never push the page
    sideways. This is the single most common way a dashboard breaks on a phone.
    """
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base_url, wait_until="networkidle")
    page.wait_for_timeout(1500)

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"Page scrolls horizontally by {overflow}px at {width}x{height}"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_renders_in_both_colour_schemes(page: Page, base_url: str, scheme):
    page.emulate_media(color_scheme=scheme)
    page.goto(base_url, wait_until="networkidle")

    bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    colour = page.evaluate("() => getComputedStyle(document.body).color")
    # Explicitly painted, not transparent, and not the same as the text.
    assert bg not in ("rgba(0, 0, 0, 0)", "transparent")
    assert bg != colour


def test_no_serious_accessibility_violations(dashboard: Page):
    """
    axe-core, limited to serious and critical issues. The report gets read by
    a family - including, in some households, someone using a screen reader.
    """
    dashboard.add_script_tag(
        url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"
    )
    dashboard.wait_for_function("() => window.axe !== undefined", timeout=20_000)

    results = dashboard.evaluate(
        """async () => {
            const r = await window.axe.run(document, {
                runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] }
            });
            return r.violations
                .filter(v => ['serious', 'critical'].includes(v.impact))
                .map(v => ({
                    id: v.id,
                    impact: v.impact,
                    // Report the offending selectors and the contrast message,
                    // otherwise a CI failure says "1 violation" and leaves the
                    // next person guessing which element.
                    targets: v.nodes.slice(0, 5).map(n => n.target.join(' ')),
                    detail: v.nodes[0] && v.nodes[0].any[0]
                        ? v.nodes[0].any[0].message : v.help,
                }));
        }"""
    )
    assert results == [], f"Accessibility violations: {results}"
