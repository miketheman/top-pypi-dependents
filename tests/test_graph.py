import json
import math
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from top_pypi_dependents import graph, warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"
# The fixture corpus is far below the production plausibility floors.
FLOORS = warehouse.Floors(winners=1, live_names=1, audit_sample=1)

ConAndSnapshot = tuple[duckdb.DuckDBPyConnection, int]


@pytest.fixture
def con_and_snapshot() -> ConAndSnapshot:
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con,
        source=FixtureSource(FIXTURES),
        captured_at=datetime(2026, 9, 1, tzinfo=UTC),
        floors=FLOORS,
    ).snapshot_id
    warehouse.compute_rankings(con, snapshot_id)
    return con, snapshot_id


def test_nodes_are_the_ranked_projects_in_rank_order(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=1)
    assert payload["names"] == ["requests", "django", "urllib3", "pytest"]
    assert payload["dependents"] == [6, 1, 1, 0]
    assert payload["dependents_all"] == [6, 1, 1, 1]


def test_projects_ranked_only_with_extras_come_last(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    """pytest's one dependent declares it behind an extra; the page hides it
    until extras are on, by drawing only the first ``ranked`` nodes."""
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=1)
    assert payload["ranked"] == 3
    assert payload["names"][payload["ranked"] :] == ["pytest"]


def _snapshot(declarations: list[tuple[str, str, bool]]) -> ConAndSnapshot:
    """A hand-built snapshot: every project live, these dependencies declared."""
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    names = sorted({name for pair in declarations for name in pair[:2]})
    con.execute(
        "INSERT INTO snapshots VALUES (1, '2026-09-01T00:00:00Z', 'fixture', ?, ?, 0)",
        [len(names), len(declarations)],
    )
    for name in names:
        con.execute(
            "INSERT INTO projects VALUES (1, ?, ?, '1.0', NULL, NULL, NULL, true)",
            [name, name],
        )
    for dependent, dependency, runtime in declarations:
        con.execute(
            "INSERT INTO dependencies VALUES (1, ?, ?, ?, NULL, ?, NULL, ?)",
            [dependent, dependency, dependency, None if runtime else "dev", runtime],
        )
    warehouse.compute_rankings(con, 1)
    return con, 1


def test_a_pair_declared_only_behind_an_extra_is_an_extra_edge() -> None:
    con, snapshot_id = _snapshot(
        [
            ("app", "lib", True),
            ("tool", "lib", True),
            ("app", "tool", False),
            ("lib", "tool", False),
        ]
    )
    payload = graph.build_graph(con, snapshot_id, min_dependents=0)
    names = payload["names"]

    def pairs(flat: list[int]) -> list[tuple[str, str]]:
        return [
            (names[a], names[b]) for a, b in zip(flat[::2], flat[1::2], strict=True)
        ]

    assert pairs(payload["edges"]) == [("app", "lib"), ("tool", "lib")]
    assert pairs(payload["extra_edges"]) == [("app", "tool"), ("lib", "tool")]


def test_one_unconditional_declaration_makes_a_runtime_edge() -> None:
    """The same pair declared twice, once behind an extra, is drawn once."""
    con, snapshot_id = _snapshot(
        [("app", "lib", True), ("app", "lib", False), ("tool", "lib", True)]
    )
    payload = graph.build_graph(con, snapshot_id, min_dependents=0)
    assert len(payload["edges"]) == 4
    assert payload["extra_edges"] == []


def test_edges_are_index_pairs_from_dependent_to_dependency(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    """Only edges between ranked projects: requests -> urllib3 is the one."""
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=1)
    names = payload["names"]
    pairs = list(zip(payload["edges"][::2], payload["edges"][1::2], strict=True))
    assert [(names[a], names[b]) for a, b in pairs] == [("requests", "urllib3")]


def test_the_minimum_drops_nodes_and_their_edges(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=2)
    assert payload["names"] == ["requests"]
    assert payload["edges"] == []
    assert payload["min_dependents"] == 2


def test_positions_fall_inside_the_extent(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=1)
    assert len(payload["x"]) == len(payload["y"]) == len(payload["names"])
    for x, y in zip(payload["x"], payload["y"], strict=True):
        assert 0 <= x <= graph.EXTENT
        assert 0 <= y <= graph.EXTENT


def test_layout_is_repeatable(con_and_snapshot: ConAndSnapshot) -> None:
    """A returning visitor should find a project where they left it."""
    con, snapshot_id = con_and_snapshot
    first = graph.build_graph(con, snapshot_id, min_dependents=1)
    second = graph.build_graph(con, snapshot_id, min_dependents=1)
    assert (first["x"], first["y"]) == (second["x"], second["y"])


def test_a_project_with_nothing_to_pull_it_sits_on_the_rim(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    """django has no ranked neighbor and no family; it goes in the outer band."""
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=1)
    center = graph.EXTENT / 2

    def radius(name: str) -> float:
        i = payload["names"].index(name)
        return math.hypot(payload["x"][i] - center, payload["y"][i] - center)

    assert radius("django") > 0.44 * graph.EXTENT
    assert radius("requests") <= 0.43 * graph.EXTENT


def test_an_empty_graph_lays_out_as_nothing() -> None:
    assert graph.layout([], []) == []


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("odoo14-addon-sale", "odoo"),
        ("odoo-addon-sale", "odoo"),
        ("pytest-cov", "pytest"),
        ("python-dateutil", None),
        ("py-cpuinfo", None),
        ("requests", None),
    ],
)
def test_family_is_the_first_word_without_its_version(
    name: str, family: str | None
) -> None:
    assert graph.family(name) == family


def test_a_family_is_drawn_together_even_without_dependencies() -> None:
    """Three plugins of one family and three strangers, none depending on any."""
    names = ["odoo14-a", "odoo12-b", "odoo-c", "alpha", "beta", "gamma"]
    layout = graph.layout(names, [])
    center = graph.EXTENT / 2
    for i, (x, y) in enumerate(layout):
        inside = math.hypot(x - center, y - center) <= 0.43 * graph.EXTENT
        assert inside == (i < 3), names[i]


def test_two_of_a_prefix_are_not_yet_a_family() -> None:
    layout = graph.layout(["odoo14-a", "odoo12-b"], [])
    center = graph.EXTENT / 2
    for x, y in layout:
        assert math.hypot(x - center, y - center) > 0.44 * graph.EXTENT


def test_write_graph_is_compact_json(
    tmp_path: Path, con_and_snapshot: ConAndSnapshot
) -> None:
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=1)
    out = tmp_path / "data" / "graph.json"
    graph.write_graph(payload, out)
    text = out.read_text(encoding="utf-8")
    assert json.loads(text) == payload
    assert ", " not in text


def test_an_unknown_snapshot_is_an_error(con_and_snapshot: ConAndSnapshot) -> None:
    con, _ = con_and_snapshot
    with pytest.raises(ValueError, match="no snapshot with id 99"):
        graph.build_graph(con, 99, min_dependents=1)
