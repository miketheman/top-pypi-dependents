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

    import duckdb

DEFAULT_LIMIT = 100_000


def _build(args: argparse.Namespace) -> int:
    con = warehouse.connect(Path(args.database))
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con,
        source=FixtureSource(Path(args.input)),
        captured_at=datetime.now(tz=UTC),
    )
    warehouse.compute_rankings(con, snapshot_id)
    con.close()
    return 0


def _latest_snapshot(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT max(snapshot_id) FROM snapshots").fetchone()
    if row is None or row[0] is None:
        msg = "database contains no snapshots; run `build` first"
        raise SystemExit(msg)
    return int(row[0])


def _artifacts(args: argparse.Namespace) -> int:
    con = warehouse.connect(Path(args.database))
    snapshot_id = _latest_snapshot(con)
    out = Path(args.output)
    payload = artifacts.build_payload(
        con, snapshot_id, limit=args.limit, previous=artifacts.read_payload(out)
    )
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
    tiers = tuple(int(part) for part in args.tiers.split(","))
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
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
