import hashlib
import json
import re
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


def test_renders_a_ranked_root_and_a_data_page(tmp_path: Path, payload: dict) -> None:
    """The ranking is the root; the method and the queries are their own page."""
    render.render_site(payload, tmp_path, rows=5)
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "data.html").exists()
    assert not (tmp_path / "top-2.html").exists()
    assert not (tmp_path / "top-5.html").exists()


def test_page_contains_ranked_rows(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "requests" in html
    assert "pypi.org/project/requests/" in html


def test_page_shows_rank_movement(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    # The glyph for sighted readers, the word for a screen reader: a rise and
    # a fall must not be announced identically.
    assert '&#9650; <span class="sr-only">up </span>3' in html


def test_tier_larger_than_the_payload_does_not_crash(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, rows=10000)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "requests" in html


def test_page_is_self_contained(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "cdn." not in html


def test_data_page_states_the_methodology(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "data.html").read_text(encoding="utf-8")
    assert "yanked" in html.lower()
    assert "extra" in html.lower()


def test_footer_shows_a_readable_date(tmp_path: Path, payload: dict) -> None:
    """A microsecond ISO timestamp is machine detail; the page is for people.

    Scoped to the footer line: the payload sample further up the page quotes the
    raw `generated_at` on purpose, because that is what the JSON really holds.
    """
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    footer = next(line for line in text.splitlines() if "Generated" in line)
    assert "September 1, 2026" in footer
    assert "2026-09-01T00:00:00+00:00" not in footer


def test_footer_names_a_bigquery_source_in_prose(tmp_path: Path, payload: dict) -> None:
    render.render_site({**payload, "source": "bigquery"}, tmp_path, rows=2)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "PyPI metadata on BigQuery" in text


def test_footer_names_the_fixture_source_in_prose(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "the checked-in fixture" in text


def test_site_serves_the_payload_as_json(tmp_path: Path, payload: dict) -> None:
    """Consumers should fetch the data from Pages, not from a raw-git URL."""
    render.render_site(payload, tmp_path, rows=2)
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8")) == payload


def test_site_serves_a_minified_payload_too(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    pretty = (tmp_path / "latest.json").read_text(encoding="utf-8")
    minified = (tmp_path / "latest.min.json").read_text(encoding="utf-8")
    assert json.loads(minified) == payload
    assert len(minified) < len(pretty)
    assert minified.count("\n") == 1


def test_data_page_links_both_data_downloads(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "data.html").read_text(encoding="utf-8")
    assert "latest.json" in text
    assert "latest.min.json" in text


def test_footer_separates_corpus_size_from_rows_listed(
    tmp_path: Path, payload: dict
) -> None:
    """The corpus numbers describe what was analyzed, not what the file holds."""
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Ranked from 16 projects and 25 dependency edges" in text
    assert "3 projects with at least 1 dependent are listed" in text


def test_footer_pluralises_the_dependent_threshold(
    tmp_path: Path, payload: dict
) -> None:
    payload["counting"]["min_dependents"] = 2
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "at least 2 dependents are listed" in text


def test_both_pages_carry_the_same_footer(tmp_path: Path, payload: dict) -> None:
    """A "Top 100" page under "1,003,087 projects" is where this misleads most."""
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "3 projects with at least 1 dependent are listed" in text


def test_data_page_shows_query_examples(tmp_path: Path, payload: dict) -> None:
    """The release assets are useless to someone who cannot see a starting query."""
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "data.html").read_text(encoding="utf-8")
    assert "count(DISTINCT dependent)" in text
    assert "edges-2026-09.parquet" in text
    assert "dependents-2026-09.duckdb" in text


def test_query_examples_name_the_month_of_the_data(
    tmp_path: Path, payload: dict
) -> None:
    """Hardcoding a month would leave the examples wrong from the next run on."""
    payload["generated_at"] = "2027-01-01T00:00:00+00:00"
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "data.html").read_text(encoding="utf-8")
    assert "edges-2027-01.parquet" in text


def test_data_page_shows_the_payload_shape(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "data.html").read_text(encoding="utf-8")
    assert "schema_version" in text
    assert "min_dependents" in text


def test_payload_shape_shows_one_row_not_all_of_them(
    tmp_path: Path, payload: dict
) -> None:
    """A schema sample carrying every row would be the artifact, not a sample."""
    render.render_site(payload, tmp_path, rows=2)
    text = (tmp_path / "data.html").read_text(encoding="utf-8")
    assert text.count("&#34;rank&#34;:") == 1


def test_rankings_page_holds_rows_up_to_the_largest_step(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert html.count("<tr data-name=") == 2


def test_a_search_with_no_match_explains_the_page_is_bounded(
    tmp_path: Path, payload: dict
) -> None:
    """The page holds the largest step; the JSON lists every ranked project."""
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'id="empty"' in html
    assert "latest.json" in html


def test_the_filter_says_what_it_searches(tmp_path: Path, payload: dict) -> None:
    """The page lists a slice; the filter reaches every ranked project."""
    render.render_site(payload, tmp_path, rows=3)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'aria-describedby="filter-hint"' in html
    assert 'id="filter-hint"' in html
    assert "Searches every ranked project" in html


def test_a_polite_status_region_reports_the_count(
    tmp_path: Path, payload: dict
) -> None:
    """The visible count changing announces nothing on its own."""
    render.render_site(payload, tmp_path, rows=3)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'role="status"' in html
    assert 'id="announce"' in html


def test_site_serves_a_search_index_of_every_ranked_project(
    tmp_path: Path, payload: dict
) -> None:
    """The page carries a slice; the index carries all of them, so search can too."""
    render.render_site(payload, tmp_path, rows=1)
    index = json.loads((tmp_path / "search-index.json").read_text(encoding="utf-8"))
    assert index["count"] == len(payload["rows"])
    assert len(index["projects"]) == len(payload["rows"])


def test_search_index_entries_are_arrays_not_objects(
    tmp_path: Path, payload: dict
) -> None:
    """Repeating four keys 45,612 times would double a file the browser fetches."""
    render.render_site(payload, tmp_path, rows=1)
    index = json.loads((tmp_path / "search-index.json").read_text(encoding="utf-8"))
    first = payload["rows"][0]
    assert index["projects"][0] == [
        first["project"],
        first["rank"],
        first["dependents"],
        first["dependents_all"],
        first["rank_change"],
    ]


def test_rankings_page_searches_past_its_own_rows(
    tmp_path: Path, payload: dict
) -> None:
    """A project ranked past the page is still findable by name."""
    render.render_site(payload, tmp_path, rows=1)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    script = (tmp_path / "assets" / "rankings.js").read_text(encoding="utf-8")
    assert 'fetch("search-index.json")' in script
    assert 'id="beyond"' in html


def test_the_page_reports_how_many_projects_it_carries(
    tmp_path: Path, payload: dict
) -> None:
    """The script needs its own row count to know what the index adds."""
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'data-on-page="2"' in html


def test_change_column_is_dropped_when_nothing_moved(
    tmp_path: Path, payload: dict
) -> None:
    """A column of 45,612 identical `new` markers is a column carrying no bits."""
    first_run = {
        **payload,
        "rows": [
            {**row, "previous_rank": None, "rank_change": None}
            for row in payload["rows"]
        ],
    }
    render.render_site(first_run, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'scope="col">Change</th>' not in html
    assert "data-show-change" not in html


def test_change_column_returns_once_projects_move(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, rows=5)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<th class="num hide-narrow" scope="col">Change</th>' in html
    assert "data-show-change" in html


def test_a_narrow_screen_keeps_rank_project_and_the_ranked_count(
    tmp_path: Path, payload: dict
) -> None:
    """Four columns do not fit a phone; the two that are not the ranking go."""
    render.render_site(payload, tmp_path, rows=5)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<th class="num hide-narrow" scope="col">Change</th>' in html
    assert '<th class="num hide-narrow" scope="col">Incl. extras</th>' in html
    assert '<th class="num" scope="col">Dependents</th>' in html


def test_pages_offer_a_skip_link(tmp_path: Path, payload: dict) -> None:
    """A keyboard reader should not tab the whole table to reach the footer."""
    render.render_site(payload, tmp_path, rows=2)
    for page in ("index.html", "data.html"):
        html = (tmp_path / page).read_text(encoding="utf-8")
        assert 'class="skip"' in html
        assert 'href="#content"' in html


def test_content_sits_in_a_main_landmark(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    for page in ("index.html", "data.html"):
        html = (tmp_path / page).read_text(encoding="utf-8")
        assert '<main id="content">' in html
        assert "</main>" in html


def test_every_column_header_carries_scope(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    headers = [line for line in html.splitlines() if "<th " in line]
    assert headers
    assert all('scope="col"' in header for header in headers)


def test_the_rank_column_is_named_for_a_screen_reader(
    tmp_path: Path, payload: dict
) -> None:
    """`#` announces as "number sign"; it is the answer the page exists for."""
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<span aria-hidden="true">#</span><span class="sr-only">Rank</span>' in html


def test_the_table_names_itself(tmp_path: Path, payload: dict) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<caption" in html


def test_messages_are_not_dressed_as_the_count_line(
    tmp_path: Path, payload: dict
) -> None:
    """One voice for three semantics made "no match" look like "showing 100"."""
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<p class="note" id="beyond-note"' in html
    assert '<p class="note" id="empty"' in html
    assert html.count('class="count"') == 1


def test_the_search_field_travels_with_the_results(
    tmp_path: Path, payload: dict
) -> None:
    """On a phone the field pins to the top; results scroll under it."""
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<div class="search">' in html


def _wide(payload: dict, count: int) -> dict:
    """The fixture ranks three projects; four-digit ranks need more than that."""
    row = payload["rows"][0]
    return {
        **payload,
        "rows": [
            {**row, "rank": n, "project": f"project-{n}"} for n in range(1, count + 1)
        ],
    }


def test_rank_is_formatted_like_every_other_number(
    tmp_path: Path, payload: dict
) -> None:
    """A rank of 1000 beside an injected 1,001 breaks the column it shares."""
    render.render_site(_wide(payload, 1200), tmp_path, rows=1200)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<td class="num rank">1,000</td>' in html
    assert '<td class="num rank">1000</td>' not in html


def test_the_change_cell_is_dropped_with_its_header(
    tmp_path: Path, payload: dict
) -> None:
    """A td guarded separately from its th would shift every value one column."""
    first_run = {
        **payload,
        "rows": [
            {**row, "previous_rank": None, "rank_change": None}
            for row in payload["rows"]
        ],
    }
    render.render_site(first_run, tmp_path, rows=3)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'class="num move' not in html


def test_the_page_lists_its_rows_without_a_reveal_control(
    tmp_path: Path, payload: dict
) -> None:
    """Every listed row is visible: the search reaches past the page, so the
    ladder that used to bound layout has nothing left to bound."""
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "data-limit" not in html
    assert "<button" not in html
    rows = [
        line for line in html.splitlines() if line.strip().startswith("<tr data-name=")
    ]
    assert len(rows) == 2
    assert not any(row.endswith(" hidden>") for row in rows)


def test_the_hint_and_the_count_do_not_repeat_each_other(
    tmp_path: Path, payload: dict
) -> None:
    """Two elements, two jobs: the hint claims reach, the count reports state."""
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    hint = next(line for line in html.splitlines() if 'id="filter-hint"' in line)
    assert "3" not in hint  # the ranked total belongs to the count line alone
    assert 'Showing <span id="shown">2</span>' in html


def test_the_footer_links_the_source_it_names(tmp_path: Path, payload: dict) -> None:
    """A reader asked to trust the number should be able to reach the table."""
    render.render_site({**payload, "source": "bigquery"}, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert (
        '<a href="https://docs.pypi.org/api/bigquery/#project-metadata-table"'
        ' target="_blank" rel="noopener" aria-describedby="new-tab">'
        "PyPI metadata on BigQuery</a>" in html
    )


def test_a_source_with_no_documentation_is_not_linked(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "the checked-in fixture" in html
    assert '<a href="https://docs.pypi.org' not in html


def test_the_cascade_page_is_only_rendered_with_a_graph(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, rows=2)
    assert not (tmp_path / "cascade.html").exists()
    assert "cascade.html" not in (tmp_path / "index.html").read_text(encoding="utf-8")


def _graph_file(tmp_path: Path, payload: dict, **fields: object) -> Path:
    source = tmp_path / "graph.json"
    graph = {"generated_at": payload["generated_at"], "min_dependents": 3, **fields}
    source.write_text(json.dumps(graph) + "\n", encoding="utf-8")
    return source


def test_a_graph_adds_the_cascade_page_and_serves_the_graph(
    tmp_path: Path, payload: dict
) -> None:
    source = _graph_file(tmp_path, payload)
    site = tmp_path / "site"
    render.render_site(payload, site, rows=2, graph=source)
    assert (site / "graph.json").read_bytes() == source.read_bytes()
    page = (site / "cascade.html").read_text(encoding="utf-8")
    assert 'href="cascade.html" aria-current="page"' in page
    script = (site / "assets" / "cascade.js").read_text(encoding="utf-8")
    assert 'fetch("graph.json")' in script
    assert 'href="cascade.html"' in (site / "index.html").read_text(encoding="utf-8")


def test_every_page_loads_only_its_own_assets(tmp_path: Path, payload: dict) -> None:
    """Nothing from another origin, and every asset a page names is served."""
    site = tmp_path / "site"
    render.render_site(payload, site, rows=2, graph=_graph_file(tmp_path, payload))
    for page in ("index.html", "data.html", "cascade.html"):
        html = (site / page).read_text(encoding="utf-8")
        assert not re.search(r'(src|href)="(https?:)?//[^"]*\.(js|css)', html)
        for name in re.findall(r'"\./assets/([\w.-]+)\?v=', html):
            assert (site / "assets" / name).is_file(), name


def test_an_asset_url_changes_with_its_content(tmp_path: Path, payload: dict) -> None:
    """A returning reader's cached script is dropped exactly when it changes."""
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    content = (tmp_path / "assets" / "rankings.js").read_bytes()
    digest = hashlib.sha256(content).hexdigest()[:12]
    assert f'src="./assets/rankings.js?v={digest}"' in html


def test_scripts_import_each_other_through_the_versioned_urls(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, rows=2)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    found = re.search(r'<script type="importmap">(.*?)</script>', html)
    assert found is not None
    imports = json.loads(found.group(1))["imports"]
    assert imports["./assets/format.js"].startswith("./assets/format.js?v=")
    assert not any(name.endswith(".css") for name in imports)


def test_links_off_the_site_open_a_new_tab_and_say_so(
    tmp_path: Path, payload: dict
) -> None:
    render.render_site(payload, tmp_path, rows=2)
    for page in ("index.html", "data.html"):
        html = (tmp_path / page).read_text(encoding="utf-8")
        assert '<span id="new-tab" hidden>Opens in a new tab</span>' in html
        for anchor in re.findall(r'<a href="https?://[^"]*"[^>]*>', html):
            assert 'target="_blank" rel="noopener"' in anchor, anchor
            assert 'aria-describedby="new-tab"' in anchor, anchor


def test_the_key_states_the_graphs_own_threshold(tmp_path: Path, payload: dict) -> None:
    """The graph's minimum, not the ranking's: the two are set separately."""
    source = _graph_file(tmp_path, payload)
    render.render_site(payload, tmp_path / "site", rows=2, graph=source)
    html = (tmp_path / "site" / "cascade.html").read_text(encoding="utf-8")
    assert "at least 3 dependents are drawn" in html


def test_a_graph_from_another_snapshot_is_refused_before_writing(
    tmp_path: Path, payload: dict
) -> None:
    source = _graph_file(tmp_path, payload, generated_at="2026-08-01T00:00:00+00:00")
    site = tmp_path / "site"
    with pytest.raises(render.StaleGraphError, match="2026-08-01"):
        render.render_site(payload, site, rows=2, graph=source)
    assert not site.exists()


def test_simulated_releases_are_hidden_unless_asked_for(
    tmp_path: Path, payload: dict
) -> None:
    """Not a live feed yet, so the toggle ships hidden behind ?simulate."""
    render.render_site(
        payload, tmp_path / "site", rows=2, graph=_graph_file(tmp_path, payload)
    )
    html = (tmp_path / "site" / "cascade.html").read_text(encoding="utf-8")
    assert '<label class="toggle" id="simulate-toggle" hidden>' in html
    script = (tmp_path / "site" / "assets" / "cascade.js").read_text(encoding="utf-8")
    assert 'has("simulate")' in script


def test_clouds_can_be_switched_off(tmp_path: Path, payload: dict) -> None:
    render.render_site(
        payload, tmp_path / "site", rows=2, graph=_graph_file(tmp_path, payload)
    )
    html = (tmp_path / "site" / "cascade.html").read_text(encoding="utf-8")
    assert '<input id="clouds" type="checkbox" checked> Clouds' in html


def test_rendering_without_a_graph_removes_an_earlier_cascade_page(
    tmp_path: Path, payload: dict
) -> None:
    site = tmp_path / "site"
    render.render_site(payload, site, rows=2, graph=_graph_file(tmp_path, payload))
    render.render_site(payload, site, rows=2)
    assert not (site / "cascade.html").exists()
    assert not (site / "graph.json").exists()


def test_a_graph_file_that_is_not_a_graph_is_refused(
    tmp_path: Path, payload: dict
) -> None:
    source = tmp_path / "graph.json"
    source.write_text("null\n", encoding="utf-8")
    with pytest.raises(render.StaleGraphError, match="does not hold a graph"):
        render.render_site(payload, tmp_path / "site", rows=2, graph=source)


def test_the_self_reference_rule_is_stated_only_for_data_counted_that_way(
    tmp_path: Path, payload: dict
) -> None:
    """October's payload counted self-references; its page must not deny it."""
    older = {**payload, "counting": {"basis": "latest non-prerelease release"}}
    render.render_site(older, tmp_path / "old", rows=2)
    render.render_site(payload, tmp_path / "new", rows=2)
    old = (tmp_path / "old" / "data.html").read_text(encoding="utf-8")
    new = (tmp_path / "new" / "data.html").read_text(encoding="utf-8")
    assert "its own dependent" not in old
    assert "dependent &lt;&gt; dependency" not in old
    assert "dependency_is_live\nGROUP BY dependency" in old
    assert "its own dependent" in new
    assert (
        "dependency_is_live\n  AND dependent &lt;&gt; dependency\nGROUP BY dependency"
        in new
    )
