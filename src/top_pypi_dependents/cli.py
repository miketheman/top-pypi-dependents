"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from top_pypi_dependents import artifacts, render, warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    import duckdb

DEFAULT_LIMIT = 100_000
# The top-ranked project's dependent count moves by a percent or two a month.
# Halving means the graph collapsed -- most commonly because /simple/ came back
# short, so most dependents stopped being live and stopped voting -- and the
# last good `data/latest.json` must not be overwritten with that.
MIN_TOP_DEPENDENTS_RATIO = 0.5


def _build(args: argparse.Namespace) -> int:
    con = warehouse.connect(Path(args.database))
    warehouse.create_schema(con)
    load_result = warehouse.load_snapshot(
        con,
        source=FixtureSource(Path(args.input)),
        captured_at=datetime.now(tz=UTC),
        floors=warehouse.Floors(
            winners=args.min_projects,
            live_names=args.min_projects,
            audit_sample=args.min_audit_sample,
        ),
    )
    snapshot_id = load_result.snapshot_id
    warehouse.compute_rankings(con, snapshot_id)
    row = con.execute(
        "SELECT project_count, edge_count, unparsed_count FROM snapshots "
        "WHERE snapshot_id = ?",
        [snapshot_id],
    ).fetchone()
    con.close()
    if row is None:
        msg = f"snapshot {snapshot_id} vanished immediately after being written"
        raise SystemExit(msg)
    project_count, edge_count, unparsed_count = row
    print(  # noqa: T201
        f"snapshot {snapshot_id}: {project_count} projects, {edge_count} edges, "
        f"{unparsed_count} unparsed requirements, "
        f"{load_result.audit_skipped} audit-skipped (unparseable in every "
        f"sampled version)"
    )
    return 0


def _latest_snapshot(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT max(snapshot_id) FROM snapshots").fetchone()
    if row is None or row[0] is None:
        msg = "database contains no snapshots; run `build` first"
        raise SystemExit(msg)
    return int(row[0])


def _captured_at(con: duckdb.DuckDBPyConnection, snapshot_id: int) -> datetime:
    row = con.execute(
        "SELECT captured_at FROM snapshots WHERE snapshot_id = ?", [snapshot_id]
    ).fetchone()
    if row is None:
        msg = f"snapshot {snapshot_id} has no row in `snapshots`"
        raise SystemExit(msg)
    return row[0].astimezone(UTC)


def _deltas_baseline(
    previous: dict[str, Any] | None, captured_at: datetime
) -> dict[str, Any] | None:
    """Return the payload to compute rank movement against, if there is one.

    A retry of the same month re-runs against a checkout where `data/latest.json`
    is already this month's, so the deltas would be computed against this run's
    own output and every row would render as unmoved. Movement is dropped rather
    than faked.
    """
    if previous is None:
        return None
    generated_at = previous.get("generated_at")
    if generated_at is None:
        return previous
    prior = datetime.fromisoformat(str(generated_at)).astimezone(UTC)
    if (prior.year, prior.month) == (captured_at.year, captured_at.month):
        print(  # noqa: T201
            f"previous payload was generated {prior.isoformat()}, the same UTC "
            f"month as this snapshot; ignoring it rather than computing rank "
            f"movement against this run's own output"
        )
        return None
    return previous


def _check_top_row_has_not_collapsed(
    payload: dict[str, Any], previous: dict[str, Any] | None
) -> None:
    """Fail if the leader's dependent count fell off a cliff since last time."""
    prior_rows = [] if previous is None else previous.get("rows") or []
    prior_top = prior_rows[0].get("dependents") if prior_rows else None
    if prior_top is None:
        return
    rows = payload["rows"]
    top = int(rows[0]["dependents"]) if rows else 0
    lowest_plausible = MIN_TOP_DEPENDENTS_RATIO * int(prior_top)
    if top < lowest_plausible:
        msg = (
            f"the top-ranked project has {top:,} dependents, against "
            f"{int(prior_top):,} in the previous payload -- below the plausible "
            f"floor of {lowest_plausible:,.0f}. The graph collapsed; refusing to "
            f"overwrite the last good ranking."
        )
        raise warehouse.ImplausibleRunError(msg)


def _artifacts(args: argparse.Namespace) -> int:
    con = warehouse.connect(Path(args.database))
    snapshot_id = _latest_snapshot(con)
    out = Path(args.output)
    previous = artifacts.read_payload(out)
    payload = artifacts.build_payload(
        con,
        snapshot_id,
        limit=args.limit,
        previous=_deltas_baseline(previous, _captured_at(con, snapshot_id)),
    )
    # Checked against the payload on disk even when it is this month's own, so a
    # retry still notices a collapse between the two runs.
    _check_top_row_has_not_collapsed(payload, previous)
    artifacts.write_json(payload, out)
    if args.edges:
        artifacts.export_edges(con, snapshot_id, Path(args.edges))
    con.close()
    return 0


def _render(args: argparse.Namespace) -> int:
    payload = artifacts.read_payload(Path(args.payload))
    if payload is None:
        msg = f"{args.payload} not found; run `artifacts` first"
        raise SystemExit(msg)
    try:
        tiers = tuple(int(part) for part in args.tiers.split(","))
    except ValueError:
        msg = f"--tiers must be a comma-separated list of integers, not {args.tiers!r}"
        raise SystemExit(msg) from None
    render.render_site(payload, Path(args.output), tiers=tiers)
    return 0


def _extract(args: argparse.Namespace) -> int:
    # Local import keeps google-cloud-bigquery out of the import path for every
    # other subcommand; CI runs `uv sync` without the `bigquery` group to prove it.
    from top_pypi_dependents.sources.bigquery import (  # noqa: PLC0415
        extract_to_directory,
    )

    return extract_to_directory(
        Path(args.output), project=args.project, dry_run=args.dry_run
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="top-pypi-dependents")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser(
        "extract", help="pull winners and audit sample from BigQuery"
    )
    extract.add_argument(
        "--output", default="build", help="directory to write JSONL into"
    )
    extract.add_argument("--project", default=None, help="GCP billing project id")
    extract.add_argument(
        "--dry-run", action="store_true", help="report bytes to be scanned and exit"
    )
    extract.set_defaults(func=_extract)

    build = sub.add_parser("build", help="load extracted data into DuckDB")
    build.add_argument("--input", default="build", help="directory holding the JSONL")
    build.add_argument("--database", default="build/dependents.duckdb")
    build.add_argument(
        "--min-projects",
        type=int,
        default=warehouse.MIN_WINNERS,
        help="fail if the source reports fewer projects, or fewer live names, "
        "than this; lower it to build from the test fixtures",
    )
    build.add_argument(
        "--min-audit-sample",
        type=int,
        default=warehouse.MIN_AUDIT_SAMPLE,
        help="fail if the version-selection audit covers fewer projects than this",
    )
    build.set_defaults(func=_build)

    art = sub.add_parser("artifacts", help="emit ranked JSON and the edge export")
    art.add_argument("--database", default="build/dependents.duckdb")
    art.add_argument("--output", default="data/latest.json")
    art.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    art.add_argument("--edges", default=None, help="path for the Parquet edge export")
    art.set_defaults(func=_artifacts)

    site = sub.add_parser("render", help="render the static site")
    site.add_argument(
        "--payload", default="data/latest.json", help="the ranked JSON to render"
    )
    site.add_argument("--output", default="site")
    site.add_argument("--tiers", default="100,1000,10000")
    site.set_defaults(func=_render)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to a stage."""
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except warehouse.ImplausibleRunError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    sys.exit(main())
