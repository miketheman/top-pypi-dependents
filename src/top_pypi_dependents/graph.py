"""Emit the ranked dependency graph, laid out, for the cascade page.

Nodes are every project with enough dependents once extras count, those ranked
on runtime dependents alone first; edges are the dependencies between them,
with those declared only behind an extra kept apart. The full graph stays in the
release's DuckDB and Parquet; this is the slice a browser can draw.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Any

import igraph

from top_pypi_dependents import warehouse

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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
    extra_edges: Sequence[tuple[int, int]],
) -> tuple[igraph.Graph, list[float]]:
    """The graph the layout runs on, which is not quite the graph drawn.

    Each family gets a hidden hub its members are tied to. Real edges are
    weighted down by how many dependents their target has: left at full weight,
    every project that uses numpy is pulled onto numpy and the center becomes one
    knot. Hidden hubs are numbered after the real nodes and dropped afterwards.
    """
    count = len(names)
    links = [*edges, *extra_edges]
    dependents = [0] * count
    for _, dependency in links:
        dependents[dependency] += 1
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


# The core the force layout fills, and the band around it where projects with
# nothing to be pulled toward sit, as radii in shares of the extent.
CORE = 0.42
BELT_INNER = 0.45
BELT_OUTER = 0.5


def _in_belt(x: float, y: float) -> bool:
    return math.hypot(x - EXTENT / 2, y - EXTENT / 2) > BELT_INNER * EXTENT


# DrL's density grid spans a few thousand units around the origin and fails
# outright on a seed outside it, so last month's map is scaled to this radius.
SEED_RADIUS = 600.0


def _centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(x for x, _ in points) / len(points),
        sum(y for _, y in points) / len(points),
    )


def _center_and_radius(
    points: Sequence[tuple[float, float]],
) -> tuple[float, float, float]:
    """The centroid, and the radius holding all but the farthest half percent.

    The last half percent are small components DrL flings far out; scaling to
    them would shrink everything else to a speck.
    """
    cx, cy = _centroid(points)
    distances = sorted(math.hypot(x - cx, y - cy) for x, y in points)
    return cx, cy, distances[int(len(distances) * 0.995)] or 1.0


def align(
    points: list[tuple[float, float]],
    targets: Sequence[tuple[float, float] | None],
) -> list[tuple[float, float]]:
    """Turn, mirror and shift ``points`` to best match ``targets``.

    A force layout has no up: run twice, it can come back turned or mirrored
    even when every neighborhood is the same. Both sets are centered on the
    points that have a target -- new projects would otherwise drag the center
    and bias the fit -- and the turn that best lines them up is applied to
    every point, then moved onto the targets' center. Points without a target
    only follow along.
    """
    pairs = [(p, t) for p, t in zip(points, targets, strict=True) if t is not None]
    if not pairs:
        return points
    px, py = _centroid([p for p, _ in pairs])
    tx0, ty0 = _centroid([t for _, t in pairs])
    pairs = [((x - px, y - py), (tx - tx0, ty - ty0)) for (x, y), (tx, ty) in pairs]
    best: tuple[float, float, float] | None = None
    for flip in (1.0, -1.0):
        dot = sum(x * tx + flip * y * ty for (x, y), (tx, ty) in pairs)
        cross = sum(x * ty - flip * y * tx for (x, y), (tx, ty) in pairs)
        angle = math.atan2(cross, dot)
        # The rotation that maximizes alignment also minimizes squared error;
        # the larger of the two maxima says whether to mirror first.
        fit = math.hypot(dot, cross)
        if best is None or fit > best[0]:
            best = (fit, flip, angle)
    _, flip, angle = best
    cos, sin = math.cos(angle), math.sin(angle)
    return [
        (
            cos * (x - px) - sin * flip * (y - py) + tx0,
            sin * (x - px) + cos * flip * (y - py) + ty0,
        )
        for x, y in points
    ]


def seed_positions(
    graph: igraph.Graph,
    vertices: list[int],
    count: int,
    known: Mapping[int, tuple[float, float]],
) -> list[tuple[float, float]]:
    """Starting positions for DrL: last month's, where there is one.

    A family hub starts at its known members' centroid; a new project at the
    centroid of its placed neighbors, hubs included, so a new family member
    with no edges of its own still starts beside its family. Anything with
    neither starts at the middle.
    """
    cx, cy, radius = _center_and_radius(list(known.values()))
    scaled = {
        v: ((x - cx) / radius * SEED_RADIUS, (y - cy) / radius * SEED_RADIUS)
        for v, (x, y) in known.items()
    }

    def centroid(v: int) -> tuple[float, float] | None:
        near = [scaled[u] for u in graph.neighbors(v) if u in scaled]
        return _centroid(near) if near else None

    for v in vertices:
        if v >= count and (spot := centroid(v)) is not None:
            scaled[v] = spot
    return [scaled.get(v) or centroid(v) or (0.0, 0.0) for v in vertices]


def layout(
    names: list[str],
    edges: list[tuple[int, int]],
    extra_edges: Sequence[tuple[int, int]] = (),
    previous: dict[str, tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """Positions on ``0..EXTENT``: related projects inside, the rest around.

    A ranked project with no ranked dependents, no ranked dependencies and no
    family has nothing to be pulled toward -- its dependents are all projects
    nothing depends on. Left to the force layout, those scattered across the
    whole square and buried the structure. They sit in a band around the rim
    instead, the highest-ranked innermost.

    With ``previous`` -- last month's positions by name -- the layout starts
    from that map and is turned to match it, so a returning visitor finds
    numpy roughly where it was. Seeding alone still let the median project
    drift a fifth of the core's radius on unchanged data; with the turn, a
    tenth. Unseeded, it was nearly two-fifths.
    """
    count = len(names)
    if count == 0:
        return []
    graph, weights = _layout_graph(names, edges, extra_edges)
    graph.es["weight"] = weights
    degrees = graph.degree()
    vertices = [v for v, degree in enumerate(degrees) if degree]
    linked = graph.induced_subgraph(vertices)
    # Last month's rim band is ordered by rank, not by anything structural, so
    # a project that sat there gives no hint where it belongs now.
    known = {
        i: previous[name]
        for i, name in enumerate(names)
        if previous
        and name in previous
        and degrees[i]
        and not _in_belt(*previous[name])
    }

    # igraph draws from one process-wide generator, so it is seeded for the
    # layout and handed back after; that is what makes a month's layout
    # repeatable. DrL took about 90 seconds on the October 2026 graph against
    # Fruchterman-Reingold's 3 seconds, and earns it: FR drew one blob.
    igraph.set_random_number_generator(random.Random(SEED))  # noqa: S311 -- a layout seed, not a secret
    try:
        seed = seed_positions(graph, vertices, count, known) if known else None
        coords = linked.layout_drl(weights="weight", seed=seed)
    finally:
        igraph.set_random_number_generator(random)
    # The induced subgraph keeps `vertices` in order, so its v-th vertex is
    # vertices[v].
    placed = {node: coords[v] for v, node in enumerate(vertices) if node < count}

    center = EXTENT / 2
    core, inner, outer = CORE * EXTENT, BELT_INNER * EXTENT, BELT_OUTER * EXTENT
    positions = [(center, center)] * count
    if placed:
        # The outliers past the radius are pulled onto the core's rim.
        cx, cy, radius = _center_and_radius(list(placed.values()))
        nodes = list(placed)
        disc = []
        for node in nodes:
            x, y = placed[node]
            dx, dy = (x - cx) / radius, (y - cy) / radius
            reach = max(1.0, math.hypot(dx, dy))
            disc.append((dx / reach * core, dy / reach * core))
        targets = [
            (known[node][0] - center, known[node][1] - center)
            if node in known
            else None
            for node in nodes
        ]
        for node, (x, y) in zip(nodes, align(disc, targets), strict=True):
            # The shift that lines up last month's map can nudge a few points
            # past the core's edge; they stay on it, clear of the rim band.
            reach = max(1.0, math.hypot(x, y) / core)
            positions[node] = (center + x / reach, center + y / reach)

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


# The sky is divided into this many cells a side to find where projects crowd.
CLOUD_GRID = 128
# A cloud is a connected run of cells denser than this share of occupied ones.
# Measured on October 2026: lower and the whole core is one cloud; higher and
# Django and Flask fade out of it.
CLOUD_PERCENTILE = 0.96
# Fewer members than this and a dense patch is a clump, not worth a name.
MIN_CLOUD = 60
# A family is a cloud's namesake when it holds at least this share of members.
FAMILY_SHARE = 0.4

# Names for the neighborhoods the layout forms, matched by a family that
# dominates the cloud or by a project it contains. First match wins, each name
# is used once, and the largest cloud claims a name first. A cloud nothing
# matches is named for its best-known project. Each carries a line saying what
# lives there, because a name like "Pony Nebula" means nothing to a reader
# who does not already know Django's mascot.
_CLOUD_NAMES: tuple[tuple[str, str, str, str], ...] = (
    (
        "family",
        "odoo",
        "Odoo Orbit",
        "Odoo's add-ons, thousands of them, circling the ERP they extend.",
    ),
    (
        "family",
        "pyobjc",
        "Cocoa Moon",
        "PyObjC's bridges to Apple's Cocoa frameworks.",
    ),
    (
        "family",
        "adafruit",
        "Blinka Belt",
        "Adafruit's CircuitPython drivers, which run on a computer through Blinka.",
    ),
    (
        "family",
        "types",
        "Shadow Moons",
        "Type stubs: types-* packages that shadow the libraries they describe.",
    ),
    (
        "family",
        "alibabacloud",
        "Tea Garden",
        "Alibaba Cloud's SDKs, one per service, grown from its Tea core.",
    ),
    (
        "family",
        "ros",
        "Robot Rings",
        "Packages for ROS, the Robot Operating System.",
    ),
    (
        "family",
        "tree",
        "Sapling Belt",
        "tree-sitter grammars, one package per language.",
    ),
    (
        "family",
        "aws",
        "Cloud Formation",
        "Libraries for Amazon Web Services.",
    ),
    (
        "family",
        "azure",
        "Azure Sky",
        "Microsoft's Azure SDK, one package per service.",
    ),
    (
        "family",
        "opentelemetry",
        "Telescope Array",
        "OpenTelemetry's instrumentation, one package per library it observes.",
    ),
    (
        "family",
        "qiskit",
        "Quantum Foam",
        "Qiskit and the quantum computing projects around it.",
    ),
    (
        "family",
        "plone",
        "Planet Plone",
        "Plone, the content management system, and its add-ons.",
    ),
    (
        "family",
        "zope",
        "Pyramids",
        "Zope's components, which Plone and Pyramid grew from.",
    ),
    (
        "family",
        "textual",
        "Terminal Velocity",
        "Terminal apps built on Textual.",
    ),
    (
        "project",
        "streamlit",
        "Stellar Stream",
        "Streamlit and the components built for it.",
    ),
    (
        "project",
        "jupyterlab",
        "Jupiter",
        "Jupyter: notebooks, kernels and their extensions.",
    ),
    (
        "project",
        "django",
        "Pony Nebula",
        "Django and its apps; the pony is Django's unofficial mascot.",
    ),
    (
        "project",
        "flask",
        "Flask Nebula",
        "Flask and its extensions.",
    ),
    (
        "project",
        "numpy",
        "Numeric Nebula",
        "NumPy and the scientific projects that build on it.",
    ),
    (
        "project",
        "transformers",
        "Tensor Nebula",
        "Machine learning around Hugging Face's Transformers.",
    ),
    (
        "project",
        "mcp",
        "Agent Nebula",
        "AI agents and Model Context Protocol servers.",
    ),
    (
        "project",
        "astropy",
        "Star Charts",
        "Astronomy and earth science, around Astropy and xarray.",
    ),
    (
        "project",
        "xarray",
        "Star Charts",
        "Astronomy and earth science, around Astropy and xarray.",
    ),
    (
        "project",
        "requests",
        "Galactic Core",
        "The dense heart of the map, around requests and its neighbors.",
    ),
    (
        "project",
        "build",
        "Tool Belt",
        "Packaging and developer tooling.",
    ),
    (
        "project",
        "plone-api",
        "Planet Plone",
        "Plone, the content management system, and its add-ons.",
    ),
    (
        "project",
        "google-auth",
        "Googleplex",
        "Google's client libraries.",
    ),
    (
        "project",
        "werkzeug",
        "Pallets Pleiades",
        "The Pallets projects, Werkzeug, Jinja and Click, and what builds on them.",
    ),
    (
        "project",
        "mkdocs",
        "Markdown Moons",
        "MkDocs, its themes and its plugins.",
    ),
    (
        "project",
        "pyqt5",
        "Qt Quasar",
        "Desktop interfaces built on Qt.",
    ),
    (
        "project",
        "docutils",
        "Sphinx",
        "Sphinx, docutils and the documentation tooling around them.",
    ),
    (
        "project",
        "pydantic-ai",
        "Small Magellanic Cloud",
        "A satellite of the Agent Nebula: projects built on Pydantic AI.",
    ),
    # These crowd together only once extras are drawn, and come last so a
    # runtime cloud that happens to hold one of them keeps its own name.
    (
        "project",
        "pymdown-extensions",
        "Material Moonlets",
        "Markdown extensions and plugins for MkDocs Material.",
    ),
    (
        "project",
        "boto3-stubs",
        "Stub Satellites",
        "Type stubs for boto3, one package per AWS service.",
    ),
    (
        "project",
        "tifffile",
        "Pixel Pulsar",
        "Scientific imaging and plotting, around tifffile and pyqtgraph.",
    ),
    (
        "project",
        "pennylane",
        "Qubit Quarry",
        "Quantum computing beyond Qiskit: PennyLane, pytket and their kin.",
    ),
)
# The band around the rim, where projects with nothing to be pulled toward sit.
BELT_NAME = "Kuiper Belt"
BELT_ABOUT = "Projects with no drawn dependency to pull them inward, ringing the map."
# How many haze tints the page carries. Clouds closer than ``TINT_REACH`` cells
# never share one, so two neighbors read as two clouds rather than one.
TINTS = 6
TINT_REACH = 4


def _tints(regions: list[set[tuple[int, int]]]) -> list[int]:
    """A tint per region, in rotation, skipping any a near neighbor took.

    Rotation alone spreads the palette across the sky; the neighbor check is
    what keeps two touching clouds from reading as one.
    """
    reach = range(-TINT_REACH, TINT_REACH + 1)
    halos = [
        {(x + dx, y + dy) for x, y in cells for dx in reach for dy in reach}
        for cells in regions
    ]
    tints: list[int] = []
    for i in range(len(regions)):
        taken = {tints[j] for j in range(i) if halos[i] & regions[j]}
        order = [(i + step) % TINTS for step in range(TINTS)]
        tints.append(next((t for t in order if t not in taken), i % TINTS))
    return tints


def _blur(grid: list[list[float]]) -> list[list[float]]:
    """A separable Gaussian blur, two cells wide, clamped at the edges."""
    size = len(grid)
    kernel = [math.exp(-(d * d) / 4.5) for d in range(-4, 5)]
    total = sum(kernel)
    kernel = [k / total for k in kernel]

    def clamp(i: int) -> int:
        return min(size - 1, max(0, i))

    rows = [
        [
            sum(row[clamp(x + d - 4)] * k for d, k in enumerate(kernel))
            for x in range(size)
        ]
        for row in grid
    ]
    return [
        [
            sum(rows[clamp(y + d - 4)][x] * k for d, k in enumerate(kernel))
            for x in range(size)
        ]
        for y in range(size)
    ]


def _cloud_name(members: list[str], used: set[str]) -> tuple[str, str, list[str]]:
    """The cloud's name, what lives there, and its best-known members.

    Members arrive in rank order. A cloud named for a family shows that
    family's best known, not whatever popular project happens to sit inside it.
    """
    families: dict[str, int] = {}
    for name in members:
        prefix = family(name)
        if prefix is not None:
            families[prefix] = families.get(prefix, 0) + 1
    present = set(members)
    for kind, key, label, about in _CLOUD_NAMES:
        if label in used:
            continue
        if kind == "family" and families.get(key, 0) >= FAMILY_SHARE * len(members):
            return label, about, [m for m in members if family(m) == key][:3]
        if kind == "project" and key in present:
            return label, about, members[:3]
    return f"{members[0]} cloud", f"Projects gathered around {members[0]}.", members[:3]


def _cell(position: tuple[int, int]) -> tuple[int, int]:
    x, y = position
    return (
        min(CLOUD_GRID - 1, x * CLOUD_GRID // EXTENT),
        min(CLOUD_GRID - 1, y * CLOUD_GRID // EXTENT),
    )


def _regions(density: list[list[float]]) -> dict[tuple[int, int], int]:
    """Label each connected run of cells denser than ``CLOUD_PERCENTILE``."""
    size = len(density)
    occupied = sorted(v for row in density for v in row if v > 0)
    if not occupied:
        return {}
    threshold = occupied[int(len(occupied) * CLOUD_PERCENTILE)]
    dense = {
        (x, y) for y in range(size) for x in range(size) if density[y][x] >= threshold
    }
    region: dict[tuple[int, int], int] = {}
    label = -1
    for start in sorted(dense, key=lambda c: (c[1], c[0])):
        if start in region:
            continue
        label += 1
        stack = [start]
        region[start] = label
        while stack:
            cx, cy = stack.pop()
            for near in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if near in dense and near not in region:
                    region[near] = label
                    stack.append(near)
    return region


def clouds(
    names: list[str], positions: list[tuple[int, int]], *, ranked: int
) -> dict[str, Any]:
    """Where the first ``ranked`` projects crowd together, and what to call it.

    ``density`` is the crowding on a ``CLOUD_GRID``-square grid, row by row,
    scaled to 0..255 on a log curve so the core does not wash out everything
    else; the page draws it as haze. ``clouds`` names each dense neighborhood,
    anchored at its members' centroid, with a line about it and its three
    best-known members.
    """
    counts = [[0.0] * CLOUD_GRID for _ in range(CLOUD_GRID)]
    for i in range(ranked):
        cx, cy = _cell(positions[i])
        counts[cy][cx] += 1.0
    density = _blur(counts)
    peak = max(max(row) for row in density) or 1.0
    scaled = [
        round(255 * math.log1p(v) / math.log1p(peak)) for row in density for v in row
    ]

    region = _regions(density)
    members: dict[int, list[int]] = {}
    for i in range(ranked):
        label = region.get(_cell(positions[i]))
        if label is not None:
            members.setdefault(label, []).append(i)
    found = []
    labels = []
    used: set[str] = set()
    for label, group in sorted(members.items(), key=lambda m: (-len(m[1]), m[1][0])):
        if len(group) < MIN_CLOUD:
            continue
        name, about, top = _cloud_name([names[i] for i in group], used)
        used.add(name)
        labels.append(label)
        x, y = _centroid([positions[i] for i in group])
        found.append(
            {
                "name": name,
                "x": round(x),
                "y": round(y),
                "projects": len(group),
                "about": about,
                "top": top,
            }
        )
    # Which named cloud each cell belongs to, counted from one; zero is none.
    # The page tints the haze by it.
    index = {label: n + 1 for n, label in enumerate(labels)}
    cells = [
        index.get(region.get((x, y), -1), 0)
        for y in range(CLOUD_GRID)
        for x in range(CLOUD_GRID)
    ]
    shapes = [
        {c for c, label in region.items() if label == wanted} for wanted in labels
    ]
    for cloud, tint in zip(found, _tints(shapes), strict=True):
        cloud["tint"] = tint

    # The rim band is placed, not found, so it is named only when the projects
    # this sky covers occupy it.
    belt = any(_in_belt(*positions[i]) for i in range(ranked))
    return {
        "grid": CLOUD_GRID,
        "density": scaled,
        "clouds": found,
        "cells": cells,
        "belt": {
            "name": BELT_NAME,
            "about": BELT_ABOUT,
            "radius": round((BELT_INNER + BELT_OUTER) / 2 * EXTENT),
        }
        if belt
        else None,
    }


def encode_edges(pairs: list[tuple[int, int]], count: int) -> dict[str, list[int]]:
    """Edges as per-node out-degrees and the gaps between sorted targets.

    Node ``i``'s dependencies are the next ``degree[i]`` gaps, summed from zero.
    A list of index pairs runs to millions of five-digit numbers; most gaps are
    one or two digits, which took the October 2026 graph from 1.76 MB to 1.18 MB
    gzipped and a third less to parse.
    """
    targets: list[list[int]] = [[] for _ in range(count)]
    for dependent, dependency in pairs:
        targets[dependent].append(dependency)
    degree, gaps = [], []
    for row in targets:
        row.sort()
        degree.append(len(row))
        previous = 0
        for target in row:
            gaps.append(target - previous)
            previous = target
    return {"degree": degree, "gaps": gaps}


def decode_edges(encoded: dict[str, list[int]]) -> list[tuple[int, int]]:
    """The index pairs ``encode_edges`` was given, dependent first, in order."""
    pairs = []
    gaps = iter(encoded["gaps"])
    for dependent, degree in enumerate(encoded["degree"]):
        target = 0
        for _ in range(degree):
            target += next(gaps)
            pairs.append((dependent, target))
    return pairs


def build_graph(
    con: duckdb.DuckDBPyConnection,
    snapshot_id: int,
    *,
    min_dependents: int,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the graph payload: parallel node arrays and flat edge lists.

    Node ``i`` is the project ranked ``i + 1`` on runtime dependents. The first
    ``ranked`` nodes clear ``min_dependents`` on runtime dependents alone; the
    rest only once extras count. ``edges`` holds runtime edges and
    ``extra_edges`` the pairs declared only behind an extra, each encoded by
    ``encode_edges``. Columns rather than objects because repeating keys across
    tens of thousands of nodes roughly doubles the file.
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
    last = (
        {
            name: (x, y)
            for name, x, y in zip(
                previous["names"], previous["x"], previous["y"], strict=True
            )
        }
        if previous
        else None
    )
    positions = layout(names, edges, extra_edges, last)
    ranked = sum(1 for _, runtime, _ in nodes if runtime >= min_dependents)

    return {
        "generated_at": snapshot.captured_at.isoformat(),
        "min_dependents": min_dependents,
        "extent": EXTENT,
        "ranked": ranked,
        "names": names,
        "dependents": [int(runtime) for _, runtime, _ in nodes],
        "dependents_all": [int(every) for _, _, every in nodes],
        "x": [x for x, _ in positions],
        "y": [y for _, y in positions],
        "sky": clouds(names, positions, ranked=ranked),
        # Extras bring whole neighborhoods with them -- type stubs, boto3's
        # stubs -- so the page finds the crowds again over every drawn node.
        "extras_sky": clouds(names, positions, ranked=len(names)),
        "edges": encode_edges(edges, len(names)),
        "extra_edges": encode_edges(extra_edges, len(names)),
    }
