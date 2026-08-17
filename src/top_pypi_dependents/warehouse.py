"""DuckDB schema, snapshot loading, and ranking."""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb

from top_pypi_dependents.normalize import canonical, parse_requirement
from top_pypi_dependents.versions import Disagreement, audit

if TYPE_CHECKING:
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


def connect(path: Path | None) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection; ``None`` opens an in-memory database."""
    return duckdb.connect(":memory:" if path is None else str(path))


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create every table if it does not already exist."""
    con.execute(SCHEMA)


def load_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    source: MetadataSource,
    captured_at: datetime,
) -> int:
    """Load one snapshot. Raises ``AuditFailedError`` before writing anything."""
    winners = source.winners()
    sql_picks = {w.canonical_name: w.version for w in winners}
    sample = source.audit_sample()
    disagreements = audit(sample, sql_picks)
    if disagreements:
        raise AuditFailedError(disagreements)

    for winner in winners:
        expected = canonical(winner.name)
        if winner.canonical_name != expected:
            msg = (
                f"source canonicalized {winner.name!r} to {winner.canonical_name!r}; "
                f"packaging says {expected!r}"
            )
            raise ValueError(msg)

    live = source.live_names()

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
        con.executemany(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?)", project_rows
        )

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
            con.executemany(
                "INSERT INTO dependencies VALUES (?, ?, ?, ?, ?, ?, ?, ?)", edge_rows
            )

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
    return snapshot_id


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
