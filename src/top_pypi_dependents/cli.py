"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from top_pypi_dependents import artifacts, log, render, warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_LIMIT = 100_000
# Single-dependent projects were 54% of the first real payload's rows and 55%
# of its bytes, and a project with one dependent says little about what the
# ecosystem depends on. Fixture runs pass `--min-dependents 1`, because the
# fixture's whole corpus is three ranked projects, two of them singletons.
DEFAULT_MIN_DEPENDENTS = 2
# The top-ranked project's dependent count moves by a percent or two a month.
# Halving means the graph collapsed -- most commonly because /simple/ came back
# short, so most dependents stopped being live and stopped voting -- and the
# last good `data/latest.json` must not be overwritten with that.
MIN_TOP_DEPENDENTS_RATIO = 0.5


def _build(args: argparse.Namespace) -> int:
    with log.stage(LOGGER, "build") as outcome:
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
        snapshot = warehouse.snapshot(con, snapshot_id)
        con.close()
        if snapshot is not None:
            outcome["projects"] = snapshot.project_count
            outcome["edges"] = snapshot.edge_count
    if snapshot is None:
        msg = f"snapshot {snapshot_id} vanished immediately after being written"
        raise SystemExit(msg)
    print(  # noqa: T201
        f"snapshot {snapshot_id}: {snapshot.project_count} projects, "
        f"{snapshot.edge_count} edges, "
        f"{snapshot.unparsed_count} unparsed requirements, "
        f"{load_result.audit_skipped} audit-skipped (unparseable in every "
        f"sampled version)"
    )
    return 0


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
    snapshot = warehouse.latest_snapshot(con)
    if snapshot is None:
        msg = "database contains no snapshots; run `build` first"
        raise SystemExit(msg)
    out = Path(args.output)
    with log.stage(LOGGER, "artifacts") as outcome:
        previous = artifacts.read_payload(out)
        LOGGER.debug("rank movement baseline: %s", "none" if previous is None else out)
        payload = artifacts.build_payload(
            con,
            snapshot.snapshot_id,
            limit=args.limit,
            min_dependents=args.min_dependents,
            previous=_deltas_baseline(previous, snapshot.captured_at),
        )
        # Checked against the payload on disk even when it is this month's own,
        # so a retry still notices a collapse between the two runs.
        _check_top_row_has_not_collapsed(payload, previous)
        artifacts.write_json(payload, out)
        outcome["rows"] = len(payload["rows"])
        outcome["bytes"] = out.stat().st_size
        if args.edges:
            edges = Path(args.edges)
            artifacts.export_edges(con, snapshot.snapshot_id, edges)
            outcome["edge_bytes"] = edges.stat().st_size
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
    with log.stage(LOGGER, "render") as outcome:
        render.render_site(payload, Path(args.output), tiers=tiers)
        outcome["pages"] = len(tiers) + 1
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
    parser.add_argument(
        "--log-level",
        choices=log.LEVELS,
        default="info",
        help="verbosity of the progress log on stderr (default info)",
    )
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
        default=warehouse.DEFAULT_FLOORS.winners,
        help="fail if the source reports fewer projects, or fewer live names, "
        "than this; lower it to build from the test fixtures",
    )
    build.add_argument(
        "--min-audit-sample",
        type=int,
        default=warehouse.DEFAULT_FLOORS.audit_sample,
        help="fail if the version-selection audit covers fewer projects than this",
    )
    build.set_defaults(func=_build)

    art = sub.add_parser("artifacts", help="emit ranked JSON and the edge export")
    art.add_argument("--database", default="build/dependents.duckdb")
    art.add_argument("--output", default="data/latest.json")
    art.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    art.add_argument(
        "--min-dependents",
        type=int,
        default=DEFAULT_MIN_DEPENDENTS,
        help=(
            "omit projects with fewer than this many runtime dependents "
            f"(default {DEFAULT_MIN_DEPENDENTS}); fixture runs pass 1"
        ),
    )
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
    log.configure(args.log_level)
    try:
        return int(args.func(args))
    except warehouse.ImplausibleRunError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    sys.exit(main())
