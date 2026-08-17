"""Extract winners and the audit sample from PyPI's BigQuery metadata table."""

from __future__ import annotations

import http
import json
from importlib.resources import files
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

TABLE = "bigquery-public-data.pypi.distribution_metadata"
SIMPLE_INDEX = "https://pypi.org/simple/"
SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json"
# The real index measured ~42 MB (872,875 projects) and grows monotonically but
# slowly. urllib3 v2 auto-negotiates Accept-Encoding and decompresses
# transparently with no size limit of its own, so a compromised origin or CDN
# edge could otherwise expand a small compressed body into gigabytes and
# OOM-kill the runner. 300 MB is ~7x today's size -- years of headroom at the
# index's actual growth rate -- while still bounding memory well below what a
# CI runner has to give.
MAX_INDEX_BYTES = 300 * 1024**2

_SQL = files("top_pypi_dependents") / "sql"
WINNERS_SQL = (_SQL / "winners.sql").read_text(encoding="utf-8")
AUDIT_SQL = (_SQL / "audit_sample.sql").read_text(encoding="utf-8")

# (field_type, mode) of `requires_dist`, an ARRAY<STRING> in BigQuery's schema.
REQUIRES_DIST_TYPE = ("STRING", "REPEATED")
# Both queries together measured 8.34 GB. The cap is a backstop for an
# unattended job against a billing-enabled project: if a schema or dataset
# change ever turns one of these into a full-table scan, it fails instead of
# spending.
MAX_BYTES_BILLED = 50 * 1024**3

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
    import urllib3  # noqa: PLC0415  # ty: ignore[unresolved-import]

    out_dir.mkdir(parents=True, exist_ok=True)
    # This runs after both billable BigQuery queries, so a transient failure on
    # this 42 MB fetch would discard work already paid for. A few retries with
    # backoff is worth it here; this is a single polite fetch against PyPI, not
    # a scraper, so the attempt count stays low.
    retries = urllib3.Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    response = urllib3.request(
        "GET",
        SIMPLE_INDEX,
        headers={"Accept": SIMPLE_ACCEPT},
        timeout=urllib3.Timeout(connect=10.0, read=120.0),
        retries=retries,
        preload_content=False,
    )
    try:
        if response.status != http.HTTPStatus.OK:
            msg = f"GET {SIMPLE_INDEX} returned HTTP {response.status}"
            raise RuntimeError(msg)
        # Stream and cap the (decompressed) body instead of reading it whole,
        # so a hostile or compromised response can't inflate past MAX_INDEX_BYTES
        # and exhaust the runner's memory. See MAX_INDEX_BYTES for the cap.
        chunks = []
        total = 0
        for chunk in response.stream(1024 * 1024):
            total += len(chunk)
            if total > MAX_INDEX_BYTES:
                msg = (
                    f"GET {SIMPLE_INDEX} exceeded the {MAX_INDEX_BYTES:,} byte cap "
                    f"on the simple index (at least {total:,} bytes observed); "
                    f"refusing to keep reading"
                )
                raise RuntimeError(msg)
            chunks.append(chunk)
    finally:
        response.release_conn()
    body = b"".join(chunks)
    names = [entry["name"] for entry in json.loads(body)["projects"]]
    (out_dir / "live_names.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    return len(names)


def _validate_schema(client: Any, table: str) -> None:  # noqa: ANN401
    fields = {field.name: field for field in client.get_table(table).schema}
    missing = EXPECTED_COLUMNS - fields.keys()
    if missing:
        msg = f"{table} is missing expected column(s): {sorted(missing)}"
        raise RuntimeError(msg)
    # The name alone is not enough for this one. If requires_dist were ever a
    # delimited STRING rather than ARRAY<STRING>, iterating it would yield
    # single characters, each of which parses as a valid requirement -- millions
    # of one-letter edges published with nothing raising.
    requires_dist = fields["requires_dist"]
    if (requires_dist.field_type, requires_dist.mode) != REQUIRES_DIST_TYPE:
        msg = (
            f"{table}.requires_dist is "
            f"{requires_dist.field_type}/{requires_dist.mode}; expected "
            f"ARRAY<STRING>, that is {REQUIRES_DIST_TYPE[0]}/{REQUIRES_DIST_TYPE[1]}"
        )
        raise RuntimeError(msg)


def extract_to_directory(
    out_dir: Path, *, project: str | None = None, dry_run: bool = False
) -> int:
    """Run both queries and write the three files the build stage reads."""
    from google.cloud import bigquery  # noqa: PLC0415  # ty: ignore[unresolved-import]

    client = bigquery.Client(project=project)
    _validate_schema(client, TABLE)

    if dry_run:
        config = bigquery.QueryJobConfig(
            dry_run=True,
            use_query_cache=False,
            maximum_bytes_billed=MAX_BYTES_BILLED,
        )
        for label, sql in (("winners", WINNERS_SQL), ("audit", AUDIT_SQL)):
            job = client.query(sql, job_config=config)
            gib = job.total_bytes_processed / 1024**3
            print(f"{label}: {job.total_bytes_processed:,} bytes ({gib:.2f} GiB)")  # noqa: T201
        return 0

    config = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED)
    write_winners(
        out_dir,
        (dict(row) for row in client.query(WINNERS_SQL, job_config=config).result()),
    )
    write_audit_sample(
        out_dir,
        (dict(row) for row in client.query(AUDIT_SQL, job_config=config).result()),
    )
    fetch_live_names(out_dir)
    return 0
