"""Emit the ranked dependency graph, laid out, for the cascade page.

Nodes are the ranked projects -- the same set `artifacts` publishes -- and edges
are the runtime dependencies between them. The full graph stays in the release's
DuckDB and Parquet; this is the slice a browser can draw.
"""

from __future__ import annotations

import json
import math
import random
from typing import TYPE_CHECKING, Any

import igraph

from top_pypi_dependents import warehouse

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import duckdb

# Coordinates are rounded onto this grid. Sub-unit precision is invisible at any
# zoom the page allows, and integers are a third the bytes of floats.
EXTENT = 10_000
# Fixed, so the same data lays out the same way every month and a returning
# visitor finds numpy where they left it.
SEED = 0

# Every project that clears the minimum once extras count, so the page can
# switch extras on without fetching anything. Ordered by the runtime ranking,
# which puts the runtime-ranked projects first: the page shows only that prefix
# until extras are switched on.
_NODES_SQL = """
SELECT canonical_name, dependents_runtime, dependents_all
FROM rankings
WHERE snapshot_id = ? AND dependents_all >= ?
ORDER BY rank_runtime
"""

# One row per pair, because one project can declare the same dependency several
# times under different markers and extras; the page draws one line, not three.
# A pair is a runtime edge if any of those declarations is unconditional.
_EDGES_SQL = """
WITH drawn AS (
    SELECT canonical_name
    FROM rankings
    WHERE snapshot_id = ? AND dependents_all >= ?
)
SELECT d.dependent, d.dependency, bool_or(d.is_runtime) AS runtime
FROM dependencies AS d
JOIN drawn AS a ON a.canonical_name = d.dependent
JOIN drawn AS b ON b.canonical_name = d.dependency
WHERE d.snapshot_id = ? AND d.dependent <> d.dependency
GROUP BY d.dependent, d.dependency
ORDER BY d.dependent, d.dependency
"""

# An edge that exists only behind an extra pulls at half strength, so the
# runtime graph -- what the page opens on -- keeps its shape.
EXTRA_WEIGHT = 0.5


# A shared name prefix is a family -- `odoo14-addon-*`, `alibabacloud-*`,
# `pytest-*` -- and families are drawn together even where no dependency says
# so. These lead too many unrelated names to mean anything.
_GENERIC_PREFIXES = frozenset(
    {"ai", "api", "agent", "data", "easy", "json", "my", "py", "python", "simple"}
)
MIN_FAMILY = 3


def family(name: str) -> str | None:
    """The family a canonical name belongs to: its first word, version stripped.

    Stripping trailing digits is what makes `odoo14-*` and `odoo12-*` one family.
    """
    if "-" not in name:
        return None
    prefix = name.split("-", 1)[0].rstrip("0123456789")
    return prefix if prefix and prefix not in _GENERIC_PREFIXES else None


def _layout_graph(
    names: list[str],
    edges: list[tuple[int, int]],
    extra_edges: list[tuple[int, int]],
) -> tuple[igraph.Graph, list[float]]:
    """The graph the layout runs on, which is not quite the graph drawn.

    Each family gets a hidden hub its members are tied to. Real edges are
    weighted down by how many dependents their target has: left at full weight,
    every project that uses numpy is pulled onto numpy and the center becomes one
    knot. Hidden hubs are numbered after the real nodes and dropped afterwards.
    """
    count = len(names)
    dependents = [0] * count
    for _, dependency in [*edges, *extra_edges]:
        dependents[dependency] += 1
    links = [*edges, *extra_edges]
    weights = [
        (1.0 if k < len(edges) else EXTRA_WEIGHT) / math.log2(2 + dependents[b])
        for k, (_, b) in enumerate(links)
    ]

    families: dict[str, list[int]] = {}
    for i, name in enumerate(names):
        prefix = family(name)
        if prefix is not None:
            families.setdefault(prefix, []).append(i)
    hub = count
    for prefix in sorted(families):
        members = families[prefix]
        if len(members) < MIN_FAMILY:
            continue
        links.extend((member, hub) for member in members)
        weights.extend([1.0] * len(members))
        hub += 1

    return igraph.Graph(n=hub, edges=links), weights


def layout(
    names: list[str],
    edges: list[tuple[int, int]],
    extra_edges: Sequence[tuple[int, int]] = (),
) -> list[tuple[int, int]]:
    """Positions on ``0..EXTENT``: related projects inside, the rest around.

    A ranked project with no ranked dependents, no ranked dependencies and no
    family has nothing to be pulled toward -- its dependents are all projects
    nothing depends on. Left to the force layout, those scattered across the
    whole square and buried the structure. They sit in a band around the rim
    instead, the highest-ranked innermost.
    """
    count = len(names)
    if count == 0:
        return []
    graph, weights = _layout_graph(names, edges, list(extra_edges))
    graph.es["weight"] = weights
    graph.vs["node"] = range(graph.vcount())
    linked = graph.induced_subgraph(
        [v for v in range(graph.vcount()) if graph.degree(v)]
    )

    # igraph draws from one process-wide generator, so it is seeded for the
    # layout and handed back after; that is what makes a month's layout
    # repeatable. DrL took about a minute on the October 2026 graph against
    # Fruchterman-Reingold's 3 seconds, and earns it: FR drew one blob.
    igraph.set_random_number_generator(random.Random(SEED))  # noqa: S311 -- a layout seed, not a secret
    try:
        coords = linked.layout_drl(weights="weight")
    finally:
        igraph.set_random_number_generator(random)
    placed = {
        node: coords[v] for v, node in enumerate(linked.vs["node"]) if node < count
    }

    center = EXTENT / 2
    core, inner, outer = 0.42 * EXTENT, 0.45 * EXTENT, 0.5 * EXTENT
    positions = [(center, center)] * count
    if placed:
        cx = sum(x for x, _ in placed.values()) / len(placed)
        cy = sum(y for _, y in placed.values()) / len(placed)
        distances = sorted(math.hypot(x - cx, y - cy) for x, y in placed.values())
        # The last half percent are small components DrL flings far out.
        # Scaling to them would shrink the core to a speck, so they are pulled
        # onto the rim instead.
        radius = distances[int(len(distances) * 0.995)] or 1.0
        for node, (x, y) in placed.items():
            dx, dy = (x - cx) / radius, (y - cy) / radius
            reach = max(1.0, math.hypot(dx, dy))
            positions[node] = (center + dx / reach * core, center + dy / reach * core)

    # A sunflower spiral fills the band at even density whatever its count.
    alone = [i for i in range(count) if i not in placed]
    golden = math.pi * (3 - math.sqrt(5))
    for k, node in enumerate(alone):
        share = (k + 0.5) / len(alone)
        r = math.sqrt(inner**2 + (outer**2 - inner**2) * share)
        positions[node] = (
            center + r * math.cos(k * golden),
            center + r * math.sin(k * golden),
        )
    return [(round(x), round(y)) for x, y in positions]


def build_graph(
    con: duckdb.DuckDBPyConnection, snapshot_id: int, *, min_dependents: int
) -> dict[str, Any]:
    """Assemble the graph payload: parallel node arrays and flat edge lists.

    Node ``i`` is the project ranked ``i + 1`` on runtime dependents. The first
    ``ranked`` nodes clear ``min_dependents`` on runtime dependents alone; the
    rest only once extras count. ``edges`` holds runtime edges and
    ``extra_edges`` the pairs declared only behind an extra, each alternating
    dependent, dependency, as node indices. Columns rather than objects because
    repeating keys across tens of thousands of nodes roughly doubles the file.
    """
    snapshot = warehouse.snapshot(con, snapshot_id)
    if snapshot is None:
        msg = f"no snapshot with id {snapshot_id}"
        raise ValueError(msg)

    nodes = con.execute(_NODES_SQL, [snapshot_id, min_dependents]).fetchall()
    names = [name for name, _, _ in nodes]
    index = {name: i for i, name in enumerate(names)}
    edges: list[tuple[int, int]] = []
    extra_edges: list[tuple[int, int]] = []
    for dependent, dependency, runtime in con.execute(
        _EDGES_SQL, [snapshot_id, min_dependents, snapshot_id]
    ).fetchall():
        (edges if runtime else extra_edges).append(
            (index[dependent], index[dependency])
        )
    positions = layout(names, edges, extra_edges)

    return {
        "generated_at": snapshot.captured_at.isoformat(),
        "min_dependents": min_dependents,
        "extent": EXTENT,
        "ranked": sum(1 for _, runtime, _ in nodes if runtime >= min_dependents),
        "names": names,
        "dependents": [int(runtime) for _, runtime, _ in nodes],
        "dependents_all": [int(every) for _, _, every in nodes],
        "x": [x for x, _ in positions],
        "y": [y for _, y in positions],
        "edges": [i for edge in edges for i in edge],
        "extra_edges": [i for edge in extra_edges for i in edge],
    }


def write_graph(graph: dict[str, Any], path: Path) -> None:
    """Write the graph compactly; it is fetched by every visit to the page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, separators=(",", ":")) + "\n", encoding="utf-8")
