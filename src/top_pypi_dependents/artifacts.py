"""Emit the ranked JSON artifact and the Parquet edge export."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from top_pypi_dependents import warehouse

if TYPE_CHECKING:
    from pathlib import Path

    import duckdb

SCHEMA_VERSION = 2

_ROWS_SQL = """
SELECT
    r.rank_runtime,
    r.canonical_name,
    r.dependents_runtime,
    r.dependents_all
FROM rankings AS r
-- Fewer than `limit` rows when fewer projects clear `min_dependents`: a rank
-- with a zero count is not a ranking, and the single-dependent tail is over
-- half the file. Rows are ordered by count, so this truncates a contiguous
-- tail rather than punching holes -- ranks stay 1..N with no gaps.
WHERE r.snapshot_id = ? AND r.dependents_runtime >= ?
ORDER BY r.rank_runtime
LIMIT ?
"""


def read_payload(path: Path) -> dict[str, Any] | None:
    """Load a JSON artifact from disk, or ``None`` if it is not there yet.

    Serves two callers: ``artifacts`` reads the file it is about to overwrite, to
    compute rank movement; ``render`` reads the finished file it renders from.
    """
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload(
    con: duckdb.DuckDBPyConnection,
    snapshot_id: int,
    *,
    limit: int,
    min_dependents: int,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the JSON payload, including rank movement against ``previous``."""
    snapshot = warehouse.snapshot(con, snapshot_id)
    if snapshot is None:
        msg = f"no snapshot with id {snapshot_id}"
        raise ValueError(msg)

    prior_ranks: dict[str, int] = {}
    if previous is not None:
        prior_ranks = {
            str(row["project"]): int(row["rank"]) for row in previous.get("rows", [])
        }

    rows = []
    for rank, name, runtime, all_count in con.execute(
        _ROWS_SQL, [snapshot_id, min_dependents, limit]
    ).fetchall():
        prior = prior_ranks.get(name)
        rows.append(
            {
                "rank": int(rank),
                "project": name,
                "dependents": int(runtime),
                "dependents_all": int(all_count),
                "previous_rank": prior,
                "rank_change": None if prior is None else prior - int(rank),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": snapshot.captured_at.isoformat(),
        "source": snapshot.source,
        "counting": {
            "basis": "latest non-prerelease release",
            "ranked_on": "runtime",
            "min_dependents": min_dependents,
        },
        "previous_generated_at": (
            None if previous is None else previous.get("generated_at")
        ),
        "project_count": snapshot.project_count,
        "edge_count": snapshot.edge_count,
        "rows": rows,
    }


def write_json(payload: dict[str, Any], path: Path) -> None:
    """Write the payload deterministically and compactly.

    Key order is insertion order, never sorted, so two runs over the same data
    produce byte-identical files. Indentation is dropped because this file is
    committed every month and git stores a whole new blob each time: pretty
    printing cost 5 MB of an 18 MB artifact, for whitespace no one reads at
    this row count.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=False) + "\n",
        encoding="utf-8",
    )


_EDGES_SQL = """
SELECT
    d.*,
    src.is_live AS dependent_is_live,
    tgt.is_live AS dependency_is_live
FROM dependencies AS d
LEFT JOIN projects AS src
    ON src.snapshot_id = d.snapshot_id AND src.canonical_name = d.dependent
-- LEFT, because a dependency target need not be a PyPI project at all.
LEFT JOIN projects AS tgt
    ON tgt.snapshot_id = d.snapshot_id AND tgt.canonical_name = d.dependency
WHERE d.snapshot_id = ?
"""


def export_edges(con: duckdb.DuckDBPyConnection, snapshot_id: int, path: Path) -> None:
    """Write one snapshot's full edge list to Parquet.

    Each endpoint carries its liveness, so a consumer holding only this file can
    reproduce the ranking's counting rules: non-live dependents do not vote and
    non-live targets do not rank. A NULL ``dependency_is_live`` means the target
    is not a PyPI project in this snapshot at all, which is a different thing
    from a project that is known and no longer live.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # DuckDB rejects a bound parameter as a COPY target ("Unsupported parameter
    # type for filename"), so the path is interpolated as a SQL string literal
    # with embedded quotes doubled. The predicate stays parameterized.
    target = str(path).replace("'", "''")
    con.execute(
        f"COPY ({_EDGES_SQL}) TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)",
        [snapshot_id],
    )
