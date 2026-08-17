"""Extract winners and the audit sample from PyPI's BigQuery metadata table."""

from __future__ import annotations

import http
import json
import logging
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from top_pypi_dependents import log

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

LOGGER = logging.getLogger(__name__)

# Row and byte intervals for progress lines inside the two loops long enough to
# look hung: ~1M winner rows, and a ~42 MB index stream.
_PROGRESS_EVERY = 50_000
_PROGRESS_BYTES = 25 * 1024**2

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


def write_winners(out_dir: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    """Write winner rows as JSONL, in the shape ``FixtureSource`` reads.

    Returns the row count. ``rows`` is a lazy cursor over roughly a million
    rows, so this is the only place that number can be known without buying it
    twice, and the count is what tells a watched run it is still moving.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with (out_dir / "winners.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), default=str) + "\n")
            written += 1
            if written % _PROGRESS_EVERY == 0:
                LOGGER.debug("winners: %s rows written", f"{written:,}")
    return written


def write_audit_sample(out_dir: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    """Write the audit sample as JSONL. Returns the row count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    with (out_dir / "audit_sample.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps({"name": row["name"], "version": row["version"]}) + "\n"
            )
            written += 1
    return written


def write_source(out_dir: Path) -> None:
    """Record that this directory holds BigQuery data, not the checked-in corpus.

    `build` reads these files through `FixtureSource`, which names the JSONL
    layout rather than any particular origin. Without this marker the published
    payload and the rendered footer both claim a source of "fixture".
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "source.txt").write_text("bigquery\n", encoding="utf-8")


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
        logged = 0
        for chunk in response.stream(1024 * 1024):
            total += len(chunk)
            if total - logged >= _PROGRESS_BYTES:
                logged = total
                LOGGER.debug("live names: %.0f MiB streamed", total / 1024**2)
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
    LOGGER.info("validating %s against the expected schema", TABLE)
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

    with log.stage(LOGGER, "extract.winners") as outcome:
        job = client.query(WINNERS_SQL, job_config=config)
        rows = job.result()
        _log_billing(job)
        outcome["rows"] = write_winners(out_dir, (dict(row) for row in rows))

    with log.stage(LOGGER, "extract.audit") as outcome:
        job = client.query(AUDIT_SQL, job_config=config)
        rows = job.result()
        _log_billing(job)
        outcome["rows"] = write_audit_sample(out_dir, (dict(row) for row in rows))

    with log.stage(LOGGER, "extract.live-names") as outcome:
        outcome["names"] = fetch_live_names(out_dir)

    write_source(out_dir)
    return 0


def _log_billing(job: Any) -> None:  # noqa: ANN401  # google's QueryJob, imported lazily
    """Report what a finished query actually cost, cache hits included."""
    processed = job.total_bytes_processed or 0
    LOGGER.info(
        "scanned %.2f GiB (billed %.2f GiB, cache_hit=%s)",
        processed / 1024**3,
        (job.total_bytes_billed or 0) / 1024**3,
        job.cache_hit,
    )
