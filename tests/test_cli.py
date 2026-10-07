import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from top_pypi_dependents import warehouse
from top_pypi_dependents.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
# The fixture corpus is far below the production plausibility floors, so every
# `build` here lowers them.
RELAXED = ["--min-projects", "1", "--min-audit-sample", "1"]


def _fixture_build_dir(tmp_path: Path) -> Path:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    for filename in ("winners.jsonl", "audit_sample.jsonl", "live_names.txt"):
        shutil.copy(FIXTURES / filename, build_dir / filename)
    return build_dir


def test_end_to_end_from_fixture(tmp_path: Path) -> None:
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "data" / "latest.json"
    site = tmp_path / "site"

    assert (
        main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED]) == 0
    )
    assert (
        main(
            [
                "artifacts",
                "--database",
                str(db),
                "--output",
                str(out_json),
                "--limit",
                "5",
                "--edges",
                str(build_dir / "edges.parquet"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "render",
                "--payload",
                str(out_json),
                "--output",
                str(site),
                "--rows",
                "5",
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["rows"][0]["project"] == "requests"
    assert (site / "index.html").exists()
    assert (build_dir / "edges.parquet").exists()


def test_graph_feeds_the_cascade_page(tmp_path: Path) -> None:
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "data" / "latest.json"
    graph_json = tmp_path / "data" / "graph.json"
    site = tmp_path / "site"
    main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED])
    main(["artifacts", "--database", str(db), "--output", str(out_json)])

    assert (
        main(
            [
                "graph",
                "--database",
                str(db),
                "--output",
                str(graph_json),
                "--min-dependents",
                "1",
            ]
        )
        == 0
    )
    names = json.loads(graph_json.read_text(encoding="utf-8"))["names"]
    assert names == ["requests", "django", "urllib3", "pytest"]

    assert (
        main(
            [
                "render",
                "--payload",
                str(out_json),
                "--output",
                str(site),
                "--graph",
                str(graph_json),
            ]
        )
        == 0
    )
    assert (site / "cascade.html").exists()
    assert (site / "graph.json").exists()


def test_graph_starts_from_the_file_it_replaces(tmp_path: Path) -> None:
    """Last month's graph is this month's starting map; --fresh ignores it."""
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    out = tmp_path / "graph.json"
    main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED])
    args = [
        "graph",
        "--database",
        str(db),
        "--output",
        str(out),
        "--min-dependents",
        "1",
    ]
    main(args)
    fresh = json.loads(out.read_text(encoding="utf-8"))
    names = fresh["names"]
    a, b = names.index("requests"), names.index("urllib3")
    swapped = dict(fresh)
    swapped["x"] = list(fresh["x"])
    swapped["y"] = list(fresh["y"])
    swapped["x"][a], swapped["x"][b] = fresh["x"][b], fresh["x"][a]
    swapped["y"][a], swapped["y"][b] = fresh["y"][b], fresh["y"][a]
    out.write_text(json.dumps(swapped), encoding="utf-8")

    main(args)
    seeded = json.loads(out.read_text(encoding="utf-8"))
    assert (seeded["x"][a], seeded["y"][a]) == (swapped["x"][a], swapped["y"][a])

    main([*args, "--fresh"])
    again = json.loads(out.read_text(encoding="utf-8"))
    assert (again["x"], again["y"]) == (fresh["x"], fresh["y"])


def test_graph_on_an_empty_database_exits(tmp_path: Path) -> None:
    db = tmp_path / "empty.duckdb"
    con = warehouse.connect(db)
    warehouse.create_schema(con)
    con.close()
    with pytest.raises(SystemExit, match="no snapshots"):
        main(["graph", "--database", str(db)])


def test_render_with_a_missing_graph_exits_before_writing(tmp_path: Path) -> None:
    payload = tmp_path / "latest.json"
    payload.write_text(
        '{"generated_at": "2026-09-01T00:00:00+00:00", "rows": []}', encoding="utf-8"
    )
    site = tmp_path / "site"
    with pytest.raises(SystemExit, match="run `graph` first"):
        main(
            [
                "render",
                "--payload",
                str(payload),
                "--output",
                str(site),
                "--graph",
                str(tmp_path / "graph.json"),
            ]
        )
    assert not site.exists()


def test_render_refuses_a_graph_from_another_snapshot(tmp_path: Path) -> None:
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "latest.json"
    graph_json = tmp_path / "graph.json"
    main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED])
    main(["artifacts", "--database", str(db), "--output", str(out_json)])
    graph_json.write_text(
        '{"generated_at": "2026-08-01T00:00:00+00:00", "min_dependents": 2}',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="rebuild it with `graph`"):
        main(
            [
                "render",
                "--payload",
                str(out_json),
                "--output",
                str(tmp_path / "site"),
                "--graph",
                str(graph_json),
            ]
        )


def test_build_prints_a_one_line_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"

    assert (
        main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED]) == 0
    )

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1
    summary = lines[0]
    assert "snapshot 1" in summary
    # winners.jsonl has 16 projects and 26 requires_dist entries, one of
    # which (malformed-deps) is unparseable, leaving 25 edges. None of the
    # audit_sample.jsonl versions are packaging-unparseable, so the
    # audit-skip count is 0 for this fixture.
    assert "16 project" in summary
    assert "25 edge" in summary
    assert "1 unparsed" in summary
    assert "0 audit-skip" in summary


def test_artifacts_computes_deltas_against_the_existing_file(tmp_path: Path) -> None:
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "latest.json"
    _write_previous(out_json, generated_at=_last_month(), rank=9, dependents=6)

    assert (
        main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED]) == 0
    )
    assert (
        main(
            [
                "artifacts",
                "--database",
                str(db),
                "--output",
                str(out_json),
                "--limit",
                "5",
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    row = next(r for r in payload["rows"] if r["project"] == "requests")
    assert row["previous_rank"] == 9
    assert row["rank_change"] == 8


def test_artifacts_ignores_a_previous_payload_from_the_same_month(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A same-month retry must not compute rank movement against its own output."""
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "latest.json"
    _write_previous(
        out_json, generated_at=datetime.now(tz=UTC).isoformat(), rank=9, dependents=6
    )

    assert (
        main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED]) == 0
    )
    assert _run_artifacts(db, out_json) == 0

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    row = next(r for r in payload["rows"] if r["project"] == "requests")
    assert row["previous_rank"] is None
    assert row["rank_change"] is None
    assert payload["previous_generated_at"] is None
    assert "same UTC month" in capsys.readouterr().out


def test_artifacts_keeps_a_previous_payload_with_no_timestamp(tmp_path: Path) -> None:
    """No generated_at means no month to compare, so the deltas still stand."""
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "latest.json"
    out_json.write_text(
        json.dumps({"rows": [{"rank": 9, "project": "requests", "dependents": 6}]}),
        encoding="utf-8",
    )

    assert (
        main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED]) == 0
    )
    assert _run_artifacts(db, out_json) == 0

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    row = next(r for r in payload["rows"] if r["project"] == "requests")
    assert row["previous_rank"] == 9


def test_artifacts_refuses_to_overwrite_when_the_leader_collapses(
    tmp_path: Path,
) -> None:
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "latest.json"
    # requests leads the fixture corpus with 6 runtime dependents; a previous
    # payload with 100 means this run lost 94% of the graph.
    _write_previous(out_json, generated_at=_last_month(), rank=1, dependents=100)
    before = out_json.read_text(encoding="utf-8")

    assert (
        main(["build", "--input", str(build_dir), "--database", str(db), *RELAXED]) == 0
    )
    with pytest.raises(SystemExit) as excinfo:
        _run_artifacts(db, out_json)

    assert "6 dependents, against" in str(excinfo.value)
    assert out_json.read_text(encoding="utf-8") == before


def test_build_refuses_the_fixture_corpus_at_the_production_floors(
    tmp_path: Path,
) -> None:
    """An ImplausibleRunError must surface as SystemExit, not an uncaught traceback."""
    build_dir = _fixture_build_dir(tmp_path)
    db = build_dir / "dependents.duckdb"
    with pytest.raises(SystemExit) as excinfo:
        main(["build", "--input", str(build_dir), "--database", str(db)])

    assert excinfo.value.code != 0


def test_render_with_a_non_numeric_row_count_exits(tmp_path: Path) -> None:
    payload = tmp_path / "latest.json"
    payload.write_text(json.dumps({"rows": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(
            [
                "render",
                "--payload",
                str(payload),
                "--output",
                str(tmp_path / "site"),
                "--rows",
                "abc",
            ]
        )


def test_render_without_a_payload_exits_nonzero(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "render",
                "--payload",
                str(tmp_path / "missing.json"),
                "--output",
                str(tmp_path / "site"),
            ]
        )


def test_unknown_subcommand_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["nonsense"])
    assert excinfo.value.code != 0


def _last_month() -> str:
    """An ISO timestamp in a UTC month before the one this test runs in."""
    now = datetime.now(tz=UTC)
    return now.replace(year=now.year - 1).isoformat()


def _write_previous(
    path: Path, *, generated_at: str, rank: int, dependents: int
) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "rows": [
                    {"rank": rank, "project": "requests", "dependents": dependents}
                ],
            }
        ),
        encoding="utf-8",
    )


def _run_artifacts(db: Path, out_json: Path) -> int:
    return main(
        [
            "artifacts",
            "--database",
            str(db),
            "--output",
            str(out_json),
            "--limit",
            "5",
        ]
    )
