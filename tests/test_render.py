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


def test_renders_one_rankings_page_plus_index(tmp_path: Path, payload: dict) -> None:
    """One page with reveal steps, replacing a page per tier."""
    render.render_site(payload, tmp_path, tiers=(2, 5))
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "rankings.html").exists()
    assert not (tmp_path / "top-2.html").exists()
    assert not (tmp_path / "top-5.html").exists()


def test_page_contains_ranked_rows(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    assert "requests" in html
    assert "pypi.org/project/requests/" in html


def test_page_shows_rank_movement(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    assert "&#9650; 3" in html  # requests climbed from 4 to 1


def test_tier_larger_than_the_payload_does_not_crash(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, tiers=(10_000,))
    html = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    assert "requests" in html


def test_page_is_self_contained(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "cdn." not in html


def test_index_states_the_methodology(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "yanked" in html.lower()
    assert "extra" in html.lower()


def test_footer_shows_a_readable_date(tmp_path: Path, payload: dict) -> None:
    """A microsecond ISO timestamp is machine detail; the page is for people.

    Scoped to the footer line: the payload sample further up the page quotes the
    raw `generated_at` on purpose, because that is what the JSON really holds.
    """
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    footer = next(line for line in text.splitlines() if "Generated" in line)
    assert "September 1, 2026" in footer
    assert "2026-09-01T00:00:00+00:00" not in footer


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


def test_rankings_page_carries_the_same_footer(tmp_path: Path, payload: dict) -> None:
    """A "Top 100" page under "1,003,087 projects" is where this misleads most."""
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    assert "3 projects with at least 1 dependent are listed" in text


def test_index_shows_query_examples(tmp_path: Path, payload: dict) -> None:
    """The release assets are useless to someone who cannot see a starting query."""
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "count(DISTINCT dependent)" in text
    assert "edges-2026-09.parquet" in text
    assert "dependents-2026-09.duckdb" in text


def test_query_examples_name_the_month_of_the_data(
    tmp_path: Path, payload: dict
) -> None:
    """Hardcoding a month would leave the examples wrong from the next run on."""
    payload["generated_at"] = "2027-01-01T00:00:00+00:00"
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "edges-2027-01.parquet" in text


def test_index_shows_the_payload_shape(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "schema_version" in text
    assert "min_dependents" in text


def test_payload_shape_shows_one_row_not_all_of_them(
    tmp_path: Path, payload: dict
) -> None:
    """A schema sample carrying every row would be the artifact, not a sample."""
    render.render_site(payload, tmp_path, tiers=(2,))
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert text.count("&#34;rank&#34;:") == 1


def test_rows_past_the_first_step_start_hidden(tmp_path: Path, payload: dict) -> None:
    """Hidden in the markup, not by script: the point is to skip their layout."""
    render.render_site(payload, tmp_path, tiers=(1, 3))
    html = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    rows = [
        line.strip()
        for line in html.splitlines()
        if line.strip().startswith("<tr data-name=")
    ]
    assert len(rows) == 3
    assert sum(1 for row in rows if row.endswith(" hidden>")) == 2


def test_a_reveal_button_is_offered_for_each_further_step(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, tiers=(1, 2, 3))
    html = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    assert 'data-limit="2"' in html
    assert 'data-limit="3"' in html
    # The first step is what the page already shows, so it needs no button.
    assert 'data-limit="1"' not in html


def test_rankings_page_holds_rows_up_to_the_largest_step(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, tiers=(1, 2))
    html = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    assert html.count("<tr data-name=") == 2


def test_a_search_with_no_match_explains_the_page_is_bounded(
    tmp_path: Path, payload: dict
) -> None:
    """The page holds the largest step; the JSON lists every ranked project."""
    render.render_site(payload, tmp_path, tiers=(1, 2))
    html = (tmp_path / "rankings.html").read_text(encoding="utf-8")
    assert 'id="empty"' in html
    assert "latest.json" in html
