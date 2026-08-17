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
