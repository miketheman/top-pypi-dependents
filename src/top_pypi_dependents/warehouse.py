"""DuckDB schema, snapshot loading, and ranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import duckdb
import pyarrow as pa

from top_pypi_dependents.normalize import canonical, parse_requirement
from top_pypi_dependents.versions import Disagreement, audit

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    from top_pypi_dependents.sources.base import MetadataSource

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id    INTEGER PRIMARY KEY,
    captured_at    TIMESTAMPTZ NOT NULL,
    source         VARCHAR NOT NULL,
    project_count  INTEGER NOT NULL,
    edge_count     INTEGER NOT NULL,
    unparsed_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    snapshot_id        INTEGER NOT NULL,
    canonical_name     VARCHAR NOT NULL,
    name               VARCHAR NOT NULL,
    latest_version     VARCHAR NOT NULL,
    latest_upload_time TIMESTAMPTZ,
    summary            VARCHAR,
    requires_python    VARCHAR,
    is_live            BOOLEAN NOT NULL,
    PRIMARY KEY (snapshot_id, canonical_name)
);

CREATE TABLE IF NOT EXISTS dependencies (
    snapshot_id    INTEGER NOT NULL,
    dependent      VARCHAR NOT NULL,
    dependency     VARCHAR NOT NULL,
    dependency_raw VARCHAR NOT NULL,
    specifier      VARCHAR,
    extra          VARCHAR,
    marker         VARCHAR,
    is_runtime     BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS rankings (
    snapshot_id        INTEGER NOT NULL,
    canonical_name     VARCHAR NOT NULL,
    rank_runtime       INTEGER,
    dependents_runtime INTEGER NOT NULL,
    rank_all           INTEGER,
    dependents_all     INTEGER NOT NULL
);
"""


# PyPI's /simple/ index listed 872,447 live projects on 2026-08-16, and the
# winners query returns one row per project that has ever published a release --
# a superset of that. A run that sees fewer than half a million projects has hit
# something degraded (a renamed dataset, a revoked permission, a truncated
# /simple/ response) rather than a shrinking Python ecosystem, and publishing its
# answer would overwrite a good ranking with a collapsed one.
MIN_WINNERS = 500_000
MIN_LIVE_NAMES = 500_000
# audit_sample.sql takes a deterministic 1% of projects, ~8,000 of them. The
# audit is the only run-time evidence that the SQL sort key picks the same
# release `packaging` would, so an empty or tiny sample means the guard passed
# without proving anything.
MIN_AUDIT_SAMPLE = 1_000
# Sampled projects whose every version is unparseable by `packaging` have no
# oracle pick and are skipped, proving nothing. Today's corpus skips a fraction
# of a percent; at more than a quarter, the audit has stopped covering enough of
# the sample to count as evidence.
MAX_AUDIT_SKIP_FRACTION = 0.25


class ImplausibleRunError(Exception):
    """An input's magnitude is too far from what a healthy run produces."""


@dataclass(frozen=True, slots=True)
class Floors:
    """The minimum plausible magnitude of each of a snapshot's inputs."""

    winners: int = MIN_WINNERS
    live_names: int = MIN_LIVE_NAMES
    audit_sample: int = MIN_AUDIT_SAMPLE
    max_audit_skip_fraction: float = MAX_AUDIT_SKIP_FRACTION


DEFAULT_FLOORS = Floors()


class AuditFailedError(Exception):
    """The source's version selection disagreed with ``packaging``."""

    def __init__(self, disagreements: list[Disagreement]) -> None:
        self.disagreements = disagreements
        preview = ", ".join(
            f"{d.project}: source={d.sql_pick!r} packaging={d.packaging_pick!r}"
            for d in disagreements[:5]
        )
        super().__init__(
            f"{len(disagreements)} version-selection disagreement(s): {preview}"
        )


@dataclass(frozen=True, slots=True)
class SnapshotLoad:
    """The outcome of loading one snapshot."""

    snapshot_id: int
    audit_skipped: int
    """``AuditResult.skipped`` from the version-selection audit: projects in
    the audit sample where ``packaging`` could not parse any version, so the
    SQL's pick had no oracle to be checked against."""


# Column order matches the CREATE TABLE statements above, because both inserts
# are `INSERT INTO <table> SELECT * FROM <registered arrow table>`.
PROJECTS_ARROW_SCHEMA = pa.schema(
    [
        ("snapshot_id", pa.int32()),
        ("canonical_name", pa.string()),
        ("name", pa.string()),
        ("latest_version", pa.string()),
        ("latest_upload_time", pa.timestamp("us", tz="UTC")),
        ("summary", pa.string()),
        ("requires_python", pa.string()),
        ("is_live", pa.bool_()),
    ]
)

DEPENDENCIES_ARROW_SCHEMA = pa.schema(
    [
        ("snapshot_id", pa.int32()),
        ("dependent", pa.string()),
        ("dependency", pa.string()),
        ("dependency_raw", pa.string()),
        ("specifier", pa.string()),
        ("extra", pa.string()),
        ("marker", pa.string()),
        ("is_runtime", pa.bool_()),
    ]
)


def connect(path: Path | None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection; ``None`` opens an in-memory database."""
    return duckdb.connect(":memory:" if path is None else str(path))


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create every table if it does not already exist."""
    con.execute(SCHEMA)


def _insert_arrow(
    con: duckdb.DuckDBPyConnection,
    table: str,
    schema: pa.Schema,
    rows: Sequence[tuple[object, ...]],
) -> None:
    """Bulk-insert row tuples through Arrow.

    DuckDB is columnar, so row-at-a-time ``executemany`` is pathological against
    it. Measured over an 800,000-row synthetic snapshot, ``load_snapshot`` went
    from 2,227 rows/sec on ``executemany`` to 147,574 through a registered Arrow
    table -- at production size, a half-hour load inside one open transaction
    against one that finishes in well under a minute.
    """
    columns = zip(*rows, strict=True)
    data = pa.table(dict(zip(schema.names, columns, strict=True)), schema=schema)
    view = f"incoming_{table}"
    con.register(view, data)
    try:
        # Both names are module-level constants, never caller input.
        con.execute(f"INSERT INTO {table} SELECT * FROM {view}")  # noqa: S608
    finally:
        con.unregister(view)


def _require_at_least(observed: int, floor: int, what: str) -> None:
    """Fail loudly when an input is smaller than a healthy run ever produces."""
    if observed < floor:
        msg = (
            f"{what}: {observed:,}, below the floor of {floor:,}. A healthy run "
            f"is far above this, so the upstream data is degraded; refusing to "
            f"publish a collapsed ranking."
        )
        raise ImplausibleRunError(msg)


def load_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    source: MetadataSource,
    captured_at: datetime,
    floors: Floors = DEFAULT_FLOORS,
) -> SnapshotLoad:
    """Load one snapshot.

    Raises ``ImplausibleRunError`` or ``AuditFailedError`` before writing
    anything.
    """
    winners = source.winners()
    _require_at_least(len(winners), floors.winners, "winners returned by the source")

    sql_picks = {w.canonical_name: w.version for w in winners}
    sample = source.audit_sample()
    _require_at_least(len(sample), floors.audit_sample, "projects in the audit sample")
    result = audit(sample, sql_picks)
    if result.disagreements:
        raise AuditFailedError(result.disagreements)
    max_skipped = floors.max_audit_skip_fraction * len(sample)
    if result.skipped > max_skipped:
        msg = (
            f"the version-selection audit skipped {result.skipped:,} of "
            f"{len(sample):,} sampled projects (no packaging-parseable version), "
            f"above the limit of {max_skipped:,.0f}. The audit no longer covers "
            f"enough of the sample to be evidence that the SQL sort key is right."
        )
        raise ImplausibleRunError(msg)

    for winner in winners:
        expected = canonical(winner.name)
        if winner.canonical_name != expected:
            msg = (
                f"source canonicalized {winner.name!r} to {winner.canonical_name!r}; "
                f"packaging says {expected!r}"
            )
            raise ValueError(msg)

    live = source.live_names()
    _require_at_least(len(live), floors.live_names, "live names on the simple index")

    con.begin()
    try:
        next_id = con.execute(
            "SELECT coalesce(max(snapshot_id), 0) + 1 FROM snapshots"
        ).fetchone()
        snapshot_id = int(next_id[0])  # ty: ignore[not-subscriptable]

        project_rows = [
            (
                snapshot_id,
                w.canonical_name,
                w.name,
                w.version,
                w.upload_time,
                w.summary,
                w.requires_python,
                w.canonical_name in live,
            )
            for w in winners
        ]
        _insert_arrow(con, "projects", PROJECTS_ARROW_SCHEMA, project_rows)

        edge_rows = []
        unparsed = 0
        for winner in winners:
            for raw in winner.requires_dist:
                parsed = parse_requirement(raw)
                if parsed is None:
                    unparsed += 1
                    continue
                edge_rows.append(
                    (
                        snapshot_id,
                        winner.canonical_name,
                        parsed.dependency,
                        parsed.dependency_raw,
                        parsed.specifier,
                        parsed.extra,
                        parsed.marker,
                        parsed.is_runtime,
                    )
                )
        if edge_rows:
            _insert_arrow(con, "dependencies", DEPENDENCIES_ARROW_SCHEMA, edge_rows)

        con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
            [
                snapshot_id,
                captured_at,
                source.name,
                len(winners),
                len(edge_rows),
                unparsed,
            ],
        )
    except Exception:
        con.rollback()
        raise
    con.commit()
    return SnapshotLoad(snapshot_id=snapshot_id, audit_skipped=result.skipped)


RANKINGS_SQL = """
INSERT INTO rankings
WITH live_edges AS (
    SELECT d.dependent, d.dependency, d.is_runtime
    FROM dependencies AS d
    JOIN projects AS dep
        ON dep.snapshot_id = d.snapshot_id AND dep.canonical_name = d.dependent
    WHERE d.snapshot_id = ? AND dep.is_live
),
counted AS (
    SELECT
        p.canonical_name,
        count(DISTINCT CASE WHEN e.is_runtime THEN e.dependent END)
            AS dependents_runtime,
        count(DISTINCT e.dependent) AS dependents_all
    FROM projects AS p
    LEFT JOIN live_edges AS e ON e.dependency = p.canonical_name
    WHERE p.snapshot_id = ? AND p.is_live
    GROUP BY p.canonical_name
)
SELECT
    ? AS snapshot_id,
    canonical_name,
    row_number() OVER (ORDER BY dependents_runtime DESC, canonical_name ASC)
        AS rank_runtime,
    dependents_runtime,
    row_number() OVER (ORDER BY dependents_all DESC, canonical_name ASC)
        AS rank_all,
    dependents_all
FROM counted
"""


def compute_rankings(con: duckdb.DuckDBPyConnection, snapshot_id: int) -> None:
    """Populate ``rankings`` for one snapshot. Safe to call more than once."""
    con.execute("DELETE FROM rankings WHERE snapshot_id = ?", [snapshot_id])
    con.execute(RANKINGS_SQL, [snapshot_id, snapshot_id, snapshot_id])
