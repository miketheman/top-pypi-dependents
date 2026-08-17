"""Emit the ranked JSON artifact and the Parquet edge export."""

from __future__ import annotations

import json
from datetime import UTC
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    import duckdb

SCHEMA_VERSION = 1

_ROWS_SQL = """
SELECT
    r.rank_runtime,
    r.canonical_name,
    r.dependents_runtime,
    r.dependents_all
FROM rankings AS r
WHERE r.snapshot_id = ?
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
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the JSON payload, including rank movement against ``previous``."""
    header = con.execute(
        "SELECT captured_at, source, project_count, edge_count "
        "FROM snapshots WHERE snapshot_id = ?",
        [snapshot_id],
    ).fetchone()
    if header is None:
        msg = f"no snapshot with id {snapshot_id}"
        raise ValueError(msg)
    captured_at, source, project_count, edge_count = header

    prior_ranks: dict[str, int] = {}
    if previous is not None:
        prior_ranks = {
            str(row["project"]): int(row["rank"]) for row in previous.get("rows", [])
        }

    rows = []
    for rank, name, runtime, all_count in con.execute(
        _ROWS_SQL, [snapshot_id, limit]
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
        # DuckDB localizes TIMESTAMPTZ to the session timezone on fetch; the
        # instant is preserved but isoformat() would otherwise emit local time.
        "generated_at": captured_at.astimezone(UTC).isoformat(),
        "source": source,
        "counting": {
            "basis": "latest non-prerelease release",
            "ranked_on": "runtime",
        },
        "previous_generated_at": (
            None if previous is None else previous.get("generated_at")
        ),
        "project_count": int(project_count),
        "edge_count": int(edge_count),
        "rows": rows,
    }


def write_json(payload: dict[str, Any], path: Path) -> None:
    """Write the payload deterministically, so git diffs stay readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def export_edges(con: duckdb.DuckDBPyConnection, snapshot_id: int, path: Path) -> None:
    """Write one snapshot's full edge list to Parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # DuckDB rejects a bound parameter as a COPY target ("Unsupported parameter
    # type for filename"), so the path is interpolated as a SQL string literal
    # with embedded quotes doubled. The predicate stays parameterized.
    target = str(path).replace("'", "''")
    con.execute(
        f"COPY (SELECT * FROM dependencies WHERE snapshot_id = ?) "  # noqa: S608
        f"TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)",
        [snapshot_id],
    )
