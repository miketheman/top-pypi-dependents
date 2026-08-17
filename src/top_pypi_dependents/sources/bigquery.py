"""Extract winners and the audit sample from PyPI's BigQuery metadata table."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

TABLE = "bigquery-public-data.pypi.distribution_metadata"
SIMPLE_INDEX = "https://pypi.org/simple/"
SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json"

_SQL = files("top_pypi_dependents") / "sql"
WINNERS_SQL = (_SQL / "winners.sql").read_text(encoding="utf-8")
AUDIT_SQL = (_SQL / "audit_sample.sql").read_text(encoding="utf-8")

EXPECTED_COLUMNS = frozenset(
    {
        "name",
        "version",
        "upload_time",
        "filename",
        "requires_dist",
        "summary",
        "requires_python",
    }
)


def write_winners(out_dir: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write winner rows as JSONL, in the shape ``FixtureSource`` reads."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "winners.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), default=str) + "\n")


def write_audit_sample(out_dir: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write the audit sample as JSONL."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "audit_sample.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps({"name": row["name"], "version": row["version"]}) + "\n"
            )


def fetch_live_names(out_dir: Path) -> int:
    """Write every live PyPI project name, one per line. Returns the count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    response = httpx.get(SIMPLE_INDEX, headers={"Accept": SIMPLE_ACCEPT}, timeout=120.0)
    response.raise_for_status()
    names = [entry["name"] for entry in response.json()["projects"]]
    (out_dir / "live_names.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    return len(names)


def _validate_schema(client: Any, table: str) -> None:  # noqa: ANN401
    actual = {field.name for field in client.get_table(table).schema}
    missing = EXPECTED_COLUMNS - actual
    if missing:
        msg = f"{table} is missing expected column(s): {sorted(missing)}"
        raise RuntimeError(msg)


def extract_to_directory(
    out_dir: Path, *, project: str | None = None, dry_run: bool = False
) -> int:
    """Run both queries and write the three files the build stage reads."""
    from google.cloud import bigquery  # noqa: PLC0415  # ty: ignore[unresolved-import]

    client = bigquery.Client(project=project)
    _validate_schema(client, TABLE)

    if dry_run:
        config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        for label, sql in (("winners", WINNERS_SQL), ("audit", AUDIT_SQL)):
            job = client.query(sql, job_config=config)
            gib = job.total_bytes_processed / 1024**3
            print(f"{label}: {job.total_bytes_processed:,} bytes ({gib:.2f} GiB)")  # noqa: T201
        return 0

    write_winners(out_dir, (dict(row) for row in client.query(WINNERS_SQL).result()))
    write_audit_sample(out_dir, (dict(row) for row in client.query(AUDIT_SQL).result()))
    fetch_live_names(out_dir)
    return 0
