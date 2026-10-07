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

    def pairs(encoded: dict[str, list[int]]) -> list[tuple[str, str]]:
        return [(names[a], names[b]) for a, b in graph.decode_edges(encoded)]

    assert sorted(pairs(payload["edges"])) == [("app", "lib"), ("tool", "lib")]
    assert sorted(pairs(payload["extra_edges"])) == [("app", "tool"), ("lib", "tool")]


def test_one_unconditional_declaration_makes_a_runtime_edge() -> None:
    """The same pair declared twice, once behind an extra, is drawn once."""
    con, snapshot_id = _snapshot(
        [("app", "lib", True), ("app", "lib", False), ("tool", "lib", True)]
    )
    payload = graph.build_graph(con, snapshot_id, min_dependents=0)
    assert len(graph.decode_edges(payload["edges"])) == 2
    assert graph.decode_edges(payload["extra_edges"]) == []


def test_edges_are_index_pairs_from_dependent_to_dependency(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    """Only edges between ranked projects: requests -> urllib3 is the one."""
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=1)
    names = payload["names"]
    pairs = graph.decode_edges(payload["edges"])
    assert [(names[a], names[b]) for a, b in pairs] == [("requests", "urllib3")]


def test_the_minimum_drops_nodes_and_their_edges(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    payload = graph.build_graph(con, snapshot_id, min_dependents=2)
    assert payload["names"] == ["requests"]
    assert payload["edges"] == {"degree": [0], "gaps": []}
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


def test_align_undoes_a_rotation_and_a_reflection() -> None:
    """Last month's map comes back the same way up, whatever DrL did."""
    target = [(3.0, 0.0), (0.0, 1.0), (-2.0, -1.0), (1.0, 2.0)]
    angle = 2.0
    turned = [
        (
            math.cos(angle) * x - math.sin(angle) * -y,
            math.sin(angle) * x + math.cos(angle) * -y,
        )
        for x, y in target
    ]
    for (x, y), (tx, ty) in zip(graph.align(turned, target), target, strict=True):
        assert math.isclose(x, tx, abs_tol=1e-9)
        assert math.isclose(y, ty, abs_tol=1e-9)


def test_align_without_targets_changes_nothing() -> None:
    points = [(1.0, 2.0), (3.0, 4.0)]
    assert graph.align(points, [None, None]) == points


def test_a_previous_layout_is_followed(con_and_snapshot: ConAndSnapshot) -> None:
    """Seeded from last month, a returning project lands where it was.

    The fixture graph is two linked projects, so last month's map is that same
    pair, swapped: an unseeded layout would not know which way round to draw it.
    """
    con, snapshot_id = con_and_snapshot
    fresh = graph.build_graph(con, snapshot_id, min_dependents=1)
    names = fresh["names"]
    swapped = {
        "names": names,
        "x": [
            fresh["x"][names.index(n)]
            for n in ("urllib3", "django", "requests", "pytest")
        ],
        "y": [
            fresh["y"][names.index(n)]
            for n in ("urllib3", "django", "requests", "pytest")
        ],
    }
    seeded = graph.build_graph(con, snapshot_id, min_dependents=1, previous=swapped)
    for name in ("requests", "urllib3"):
        i = names.index(name)
        moved = math.hypot(
            seeded["x"][i] - swapped["x"][i], seeded["y"][i] - swapped["y"][i]
        )
        assert moved < 0.05 * graph.EXTENT, name


def test_a_project_new_this_month_starts_beside_its_neighbors() -> None:
    """Only one of a family was here last month; the rest still lay out with it."""
    names = ["odoo14-a", "odoo12-b", "odoo-c", "alpha"]
    previous = {"odoo14-a": (8000, 5000)}
    positions = graph.layout(names, [], previous=previous)
    center = graph.EXTENT / 2
    family = positions[:3]
    for x, y in family:
        assert math.hypot(x - center, y - center) <= 0.43 * graph.EXTENT
    assert positions[0][0] > center, "the one known member keeps its side"


def test_edges_encode_as_degrees_and_gaps_and_decode_back() -> None:
    """Node 0 depends on 5 and 2, node 2 on 7: sorted targets, gaps from zero."""
    pairs = [(0, 5), (2, 7), (0, 2)]
    encoded = graph.encode_edges(pairs, 8)
    assert encoded == {"degree": [2, 0, 1, 0, 0, 0, 0, 0], "gaps": [2, 3, 7]}
    assert graph.decode_edges(encoded) == [(0, 2), (0, 5), (2, 7)]


def test_dense_neighborhoods_become_named_clouds() -> None:
    """A tight odoo family and a tight scientific stack, far apart."""
    names = [f"odoo14-addon-{i}" for i in range(80)] + ["numpy"]
    names += [f"sci-{i}" for i in range(79)]
    positions = [(2000 + i % 9 * 10, 2000 + i // 9 * 10) for i in range(80)]
    positions += [(7000 + i % 9 * 10, 7000 + i // 9 * 10) for i in range(80)]
    sky = graph.clouds(names, positions, ranked=len(names))
    named = {cloud["name"] for cloud in sky["clouds"]}
    assert named == {"the Oort Cloud", "the Numeric Nebula"}
    assert len(sky["density"]) == graph.CLOUD_GRID**2
    assert max(sky["density"]) == 255


def test_a_cloud_with_no_signature_is_named_for_its_best_known_project() -> None:
    names = [f"widget-{i:03d}" for i in range(80)]
    positions = [(5000 + i % 9 * 10, 5000 + i // 9 * 10) for i in range(80)]
    sky = graph.clouds(names, positions, ranked=len(names))
    assert [cloud["name"] for cloud in sky["clouds"]] == ["the widget-000 cloud"]


def test_a_sparse_sky_has_no_clouds() -> None:
    sky = graph.clouds(["a", "b"], [(100, 100), (9000, 9000)], ranked=2)
    assert sky["clouds"] == []


def test_the_rim_band_is_named_only_when_occupied() -> None:
    center = graph.EXTENT // 2
    inside = graph.clouds(["a"], [(center, center)], ranked=1)
    rim = graph.clouds(["a"], [(center, center + int(0.48 * graph.EXTENT))], ranked=1)
    assert inside["belt"] is None
    assert rim["belt"]["name"] == "the Asteroid Belt"


def test_an_empty_sky_has_no_clouds() -> None:
    sky = graph.clouds([], [], ranked=0)
    assert sky["clouds"] == []
    assert sky["belt"] is None


def test_a_straggler_is_left_out_of_the_cloud_it_is_far_from() -> None:
    names = [f"widget-{i:03d}" for i in range(80)] + ["loner"]
    positions = [(5000 + i % 9 * 10, 5000 + i // 9 * 10) for i in range(80)]
    positions.append((1000, 1000))
    sky = graph.clouds(names, positions, ranked=len(names))
    assert [cloud["projects"] for cloud in sky["clouds"]] == [80]


def test_neighboring_clouds_get_different_tints() -> None:
    """Two crowds close enough to touch must not share a haze color."""
    names = [f"left-{i:02d}" for i in range(80)] + [f"right-{i:02d}" for i in range(80)]
    positions = [(4000 + i % 9 * 10, 5000 + i // 9 * 10) for i in range(80)]
    positions += [(4400 + i % 9 * 10, 5000 + i // 9 * 10) for i in range(80)]
    sky = graph.clouds(names, positions, ranked=len(names))
    tints = [cloud["tint"] for cloud in sky["clouds"]]
    assert len(tints) == 2
    assert tints[0] != tints[1]
    assert len(sky["cells"]) == graph.CLOUD_GRID**2
    assert set(sky["cells"]) == {0, 1, 2}


def test_a_new_family_member_starts_beside_its_family() -> None:
    """odoo-new has no edges of its own; its family hub knows where to put it."""
    names = ["odoo14-a", "odoo12-b", "odoo-new", "numpy", "pandas"]
    edges = [(4, 3)]
    layout_graph, _ = graph._layout_graph(names, edges, [])  # noqa: SLF001
    vertices = [v for v in range(layout_graph.vcount()) if layout_graph.degree(v)]
    known = {
        0: (9000.0, 5000.0),
        1: (9100.0, 5000.0),
        3: (1000.0, 5000.0),
        4: (1100.0, 5000.0),
    }
    seeds = dict(
        zip(
            vertices,
            graph.seed_positions(layout_graph, vertices, 5, known),
            strict=True,
        )
    )
    family = (seeds[0][0] + seeds[1][0]) / 2
    assert abs(seeds[2][0] - family) < 1.0


def test_align_undoes_a_shift_as_well_as_a_turn() -> None:
    target = [(3.0, 0.0), (0.0, 1.0), (-2.0, -1.0), (1.0, 2.0)]
    moved = [(-y + 50.0, x - 20.0) for x, y in target]
    for (x, y), (tx, ty) in zip(graph.align(moved, target), target, strict=True):
        assert math.isclose(x, tx, abs_tol=1e-9)
        assert math.isclose(y, ty, abs_tol=1e-9)
