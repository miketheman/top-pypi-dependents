import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from top_pypi_dependents import artifacts, render, warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"
# The fixture corpus is far below the production plausibility floors.
FLOORS = warehouse.Floors(winners=1, live_names=1, audit_sample=1)


@pytest.fixture
def payload() -> dict:
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con,
        source=FixtureSource(FIXTURES),
        captured_at=datetime(2026, 9, 1, tzinfo=UTC),
        floors=FLOORS,
    ).snapshot_id
    warehouse.compute_rankings(con, snapshot_id)
    return artifacts.build_payload(
        con,
        snapshot_id,
        limit=50,
        min_dependents=1,
        previous={
            "generated_at": "2026-08-01T00:00:00+00:00",
            "rows": [{"rank": 4, "project": "requests"}],
        },
    )


def test_renders_one_page_per_tier_plus_index(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2, 5))
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "top-2.html").exists()
    assert (tmp_path / "top-5.html").exists()


def test_page_contains_ranked_rows(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "top-2.html").read_text(encoding="utf-8")
    assert "requests" in html
    assert "pypi.org/project/requests/" in html


def test_page_shows_rank_movement(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "top-2.html").read_text(encoding="utf-8")
    assert "&#9650; 3" in html  # requests climbed from 4 to 1


def test_tier_larger_than_the_payload_does_not_crash(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, tiers=(10_000,))
    html = (tmp_path / "top-10000.html").read_text(encoding="utf-8")
    assert "requests" in html


def test_page_is_self_contained(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "top-2.html").read_text(encoding="utf-8")
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "cdn." not in html


def test_index_states_the_methodology(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "yanked" in html.lower()
    assert "extra" in html.lower()


def test_footer_shows_a_readable_date(tmp_path: Path, payload: dict) -> None:
    """A microsecond ISO timestamp is machine detail; the page is for people."""
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "September 1, 2026" in text
    assert "2026-09-01T00:00:00+00:00" not in text


def test_footer_names_a_bigquery_source_in_prose(tmp_path: Path, payload: dict) -> None:
    render.render_site({**payload, "source": "bigquery"}, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "PyPI metadata on BigQuery" in text


def test_footer_names_the_fixture_source_in_prose(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "the checked-in fixture" in text


def test_site_serves_the_payload_as_json(tmp_path: Path, payload: dict) -> None:
    """Consumers should fetch the data from Pages, not from a raw-git URL."""
    render.render_site(payload, tmp_path, tiers=(2,))
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8")) == payload


def test_site_serves_a_minified_payload_too(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    pretty = (tmp_path / "latest.json").read_text(encoding="utf-8")
    minified = (tmp_path / "latest.min.json").read_text(encoding="utf-8")
    assert json.loads(minified) == payload
    assert len(minified) < len(pretty)
    assert minified.count("\n") == 1


def test_index_links_both_data_downloads(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "latest.json" in text
    assert "latest.min.json" in text


def test_footer_separates_corpus_size_from_rows_listed(
    tmp_path: Path, payload: dict
) -> None:
    """The corpus numbers describe what was analysed, not what the file holds."""
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Ranked from 16 projects and 25 dependency edges" in text
    assert "3 projects with at least 1 dependent are listed" in text


def test_footer_pluralises_the_dependent_threshold(
    tmp_path: Path, payload: dict
) -> None:
    payload["counting"]["min_dependents"] = 2
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "at least 2 dependents are listed" in text


def test_tier_pages_carry_the_same_footer(tmp_path: Path, payload: dict) -> None:
    """A "Top 100" page under "1,003,087 projects" is where this misleads most."""
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "top-2.html").read_text(encoding="utf-8")
    assert "3 projects with at least 1 dependent are listed" in text
