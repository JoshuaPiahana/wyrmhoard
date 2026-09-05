"""
Integration tests against the real HTTP surface.

The unit tests cover the maths; these cover the contract the dashboard
actually depends on. A refactor that quietly renames a JSON key breaks the
frontend silently - nothing throws, the page just shows dashes forever - so
the response shapes are pinned here.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyrmhoard import cache, categorise, config, db
from wyrmhoard.api import app

SAMPLE_CSV = "\n".join(
    [
        "38-9014-0123456-00,01-07-2025,SALARY ACME LTD,,,,,,,,,,2380.00,,2380.00,4530.00",
        "38-9014-0123456-00,02-07-2025,POS W/D PAK N SAVE,,,,,,,,,,,285.40,-285.40,4244.60",
        "38-9014-0123456-00,03-07-2025,D/D MERCURY NZ LTD,,,,,,,,,,,268.00,-268.00,3976.60",
        "38-9014-0123456-00,04-07-2025,NETFLIX COM,,,,,,,,,,,25.99,-25.99,3950.61",
        "38-9014-0123456-00,05-07-2025,ZZQ MYSTERY MERCHANT,,,,,,,,,,,44.00,-44.00,3906.61",
    ]
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client backed by a throwaway ledger, never the household's real one."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr(cache, "DATA_DIR", tmp_path)
    cache.clear_all()
    (tmp_path / "inbox").mkdir(parents=True, exist_ok=True)
    db.init()
    with TestClient(app) as c:
        yield c
    cache.clear_all()


@pytest.fixture
def private_config(tmp_path, monkeypatch):
    """
    A config directory of the test's own, holding only the public files.

    POST /learn writes to config/learned.yml. Without this it writes into
    whichever config directory the suite was launched against, which on a
    development machine is the household's own - so a test would file
    "ZZQ MYSTERY MERCHANT" among their real merchants and leave it there.
    """
    private = tmp_path / "config"
    private.mkdir()
    for name in ("rules.yml", "nz_rates.yml"):
        source = config.CONFIG_DIR / name
        if source.exists():
            shutil.copy(source, private / name)

    monkeypatch.setattr(config, "CONFIG_DIR", private)
    config.reload()
    categorise.compiled_rules.cache_clear()
    yield private
    config.reload()
    categorise.compiled_rules.cache_clear()


def _import_sample(client) -> dict:
    return client.post("/import", files={"file": ("kb.csv", SAMPLE_CSV, "text/csv")}).json()


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert "version" in body


@pytest.mark.parametrize(
    "path",
    [
        "/setup",
        "/summary",
        "/monthly",
        "/categories",
        "/recurring",
        "/entitlements",
        "/mortgage",
        "/coach",
        "/household",
        "/transactions",
        "/uncategorised",
        "/rules",
        "/snapshots",
        "/balances",
    ],
)
def test_every_get_endpoint_responds_on_an_empty_ledger(client, path):
    """A new install must not return 500 anywhere."""
    assert client.get(path).status_code == 200


# ---------------------------------------------------------------------------
# The response shapes the dashboard binds to
# ---------------------------------------------------------------------------
def test_summary_keeps_the_keys_the_frontend_binds_to(client):
    _import_sample(client)
    body = client.get("/summary").json()

    for key in (
        "stats",
        "coverage",
        "monthly",
        "typical_month",
        "by_category",
        "small_leaks",
        "cash",
        "trend",
    ):
        assert key in body, f"/summary lost '{key}' - the dashboard binds to it"


def test_coach_keeps_its_contract(client):
    body = client.get("/coach").json()
    assert {"findings", "plan", "counts", "disclaimer"} <= set(body)
    for finding in body["findings"]:
        assert {"id", "title", "severity", "body"} <= set(finding)
        assert finding["severity"] in {"critical", "high", "medium", "low", "win"}


def test_setup_reports_what_is_still_missing(client):
    body = client.get("/setup").json()
    assert body["transactions"] == 0
    assert body["ready"] is False
    assert any(t["id"] == "import" for t in body["todo"])


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def test_import_round_trip(client):
    body = _import_sample(client)

    assert body["report"]["rows_parsed"] == 5
    assert body["report"]["column_map"]["memo"] == 2
    assert client.get("/health").json()["stats"]["transactions"] == 5

    # And it was categorised on the way in.
    cats = {t["category"] for t in client.get("/transactions").json()}
    assert "groceries" in cats
    assert "power" in cats


def test_reimport_of_the_same_file_adds_nothing(client):
    _import_sample(client)
    _import_sample(client)
    assert client.get("/health").json()["stats"]["transactions"] == 5


def test_preview_does_not_write_to_the_ledger(client):
    """Preview exists so a human can check the parse before trusting it."""
    res = client.post("/preview", files={"file": ("kb.csv", SAMPLE_CSV, "text/csv")})

    assert res.status_code == 200
    assert res.json()["report"]["rows_parsed"] == 5
    assert client.get("/health").json()["stats"]["transactions"] == 0


def test_import_of_a_nonsense_file_is_reported_not_swallowed(client):
    res = client.post("/import", files={"file": ("junk.csv", "a,b\nc,d", "text/csv")})

    assert res.status_code == 200
    report = res.json()["report"]
    assert report["confidence"] == "low"
    assert report["warnings"]


# ---------------------------------------------------------------------------
# Categorisation overrides
# ---------------------------------------------------------------------------
def test_manual_override_sticks_and_beats_the_rules(client):
    _import_sample(client)

    unknown = client.get("/uncategorised").json()
    assert unknown, "the mystery merchant should be uncategorised"
    fps = unknown[0]["fingerprints"]

    res = client.post("/categorise", json={"fingerprints": fps, "category": "hobbies"})
    assert res.status_code == 200

    # It survives a full re-run of the rule engine.
    client.post("/recategorise")
    tx = [t for t in client.get("/transactions").json() if t["fingerprint"] in fps]
    assert tx and all(t["category"] == "hobbies" for t in tx)
    assert all(t["categorised_by"] == "manual" for t in tx)


@pytest.mark.parametrize(
    "hostile,expected",
    [
        # Only the final path component survives, so traversal segments are
        # discarded outright rather than escaped.
        ("../../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\evil.csv", "evil.csv"),
        ("/absolute/path.csv", "path.csv"),
        ("....//....//x.csv", "x.csv"),
        ("", "upload.csv"),
        (None, "upload.csv"),
        ("..", "upload.csv"),
        # Ordinary names must come through unharmed - browsers produce these.
        ("normal-export (1).csv", "normal-export (1).csv"),
        ("Kiwibank 38-9014 export.csv", "Kiwibank 38-9014 export.csv"),
        # Anything genuinely odd is neutralised rather than rejected.
        ("we;ird|name$.csv", "we_ird_name_.csv"),
    ],
)
def test_upload_filenames_cannot_escape_the_inbox(hostile, expected):
    """
    The browser supplies this name, so it is attacker-controlled in principle.
    A traversal sequence must never survive into a path we join and write to.
    """
    from wyrmhoard.api import safe_upload_name

    safe = safe_upload_name(hostile)
    assert safe == expected
    assert "/" not in safe and "\\" not in safe
    assert not safe.startswith(".")


def test_import_writes_only_inside_the_inbox(client, tmp_path):
    """End to end: a traversal filename lands in the inbox, not above it."""
    client.post(
        "/import",
        files={"file": ("../../escaped.csv", SAMPLE_CSV, "text/csv")},
    )
    assert not (tmp_path.parent / "escaped.csv").exists()
    assert list((tmp_path / "inbox").glob("*.csv")), "nothing was written to the inbox"


def test_unknown_category_is_rejected(client):
    res = client.post(
        "/categorise", json={"fingerprints": ["deadbeef"], "category": "not_a_category"}
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
def test_snapshot_round_trip(client):
    _import_sample(client)

    res = client.post("/snapshots", json={"note": "first meeting"})
    assert res.status_code == 200

    snaps = client.get("/snapshots").json()
    assert len(snaps) == 1
    assert snaps[0]["note"] == "first meeting"
    assert "categorised_pct" in snaps[0]["metrics"]


def test_report_endpoint_writes_a_file(client):
    _import_sample(client)
    res = client.post("/report")

    assert res.status_code == 200
    assert Path(res.json()["path"]).exists()


def test_reload_picks_up_config_changes(client):
    """Used by the dashboard's 'reload config' button after a rules.yml edit."""
    res = client.post("/reload")
    assert res.status_code == 200
    assert "coverage" in res.json()


def test_categories_endpoint_matches_the_rule_index(client):
    """Every category the API reports must be one the rules actually define."""
    _import_sample(client)
    known = {r["key"] for r in client.get("/rules").json()} | {"uncategorised"}
    for row in client.get("/categories").json():
        assert row["category"] in known


def test_no_endpoint_leaks_a_filesystem_path_outside_the_data_dir(client, tmp_path):
    """A published tool should not tell the world where the ledger lives."""
    _import_sample(client)
    body = client.get("/summary").text
    assert "/root" not in body
    assert str(Path.home()) not in body or str(tmp_path) in body


def test_categorise_persists_across_a_cache_clear(client):
    _import_sample(client)
    before = client.get("/summary").json()["coverage"]["categorised_pct"]
    categorise.recategorise_all()
    after = client.get("/summary").json()["coverage"]["categorised_pct"]
    assert before == after


# ---------------------------------------------------------------------------
# Teaching a rule
#
# The dashboard could only ever write per-transaction overrides, so the same
# shop came back unrecognised with the next statement while an agent driving
# the MCP server could fix it permanently. These pin the endpoint that closed
# that gap.
# ---------------------------------------------------------------------------
def test_learning_a_rule_claims_the_transactions_and_survives_reimport(client, private_config):
    _import_sample(client)
    assert "ZZQ MYSTERY MERCHANT" in client.get("/uncategorised").text

    result = client.post("/learn", json={"match": "ZZQ MYSTERY", "category": "groceries"}).json()

    assert result["matched"] == 1
    assert "ZZQ MYSTERY MERCHANT" not in client.get("/uncategorised").text

    # The point of a rule rather than an override: it decides transactions
    # that did not exist when it was written.
    client.post("/recategorise")
    assert "ZZQ MYSTERY MERCHANT" not in client.get("/uncategorised").text


def test_learning_writes_only_to_learned_yml(client, private_config):
    """rules.yml is public; a household's own merchants must never land in it."""
    _import_sample(client)
    client.post("/learn", json={"match": "ZZQ MYSTERY", "category": "groceries"})

    assert "ZZQ MYSTERY" in (private_config / "learned.yml").read_text(encoding="utf-8")
    assert "ZZQ MYSTERY" not in (private_config / "rules.yml").read_text(encoding="utf-8")


def test_a_refused_pattern_returns_the_reason_not_just_a_status(client, private_config):
    """
    The dashboard shows this text to whoever typed it, so it has to say what
    was wrong. A bare 400 leaves them guessing.
    """
    res = client.post("/learn", json={"match": "ZZ", "category": "groceries"})
    assert res.status_code == 400
    assert "too short" in res.json()["detail"]
    assert not (private_config / "learned.yml").exists(), "a refused pattern still wrote a rule"


def test_an_invented_category_is_refused(client, private_config):
    res = client.post("/learn", json={"match": "ZZQ MYSTERY", "category": "beer_money"})
    assert res.status_code == 400
    assert "beer_money" in res.json()["detail"]


def test_learning_reports_spending_it_moves_out_of_another_category(client, private_config):
    """The same warning the MCP tool carries, over HTTP, for the same reason."""
    _import_sample(client)
    result = client.post("/learn", json={"match": "PAK N SAVE", "category": "takeaways"}).json()

    assert result["changed_group_count"] == 1
    assert result["warning"] is not None
    moved = result["reclassified"][0]
    assert moved["from_group"] == "essential"
    assert moved["to_group"] == "discretionary"
