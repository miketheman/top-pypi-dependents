import json
import shutil
from pathlib import Path

import pytest

from top_pypi_dependents.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_end_to_end_from_fixture(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    for filename in ("winners.jsonl", "audit_sample.jsonl", "live_names.txt"):
        shutil.copy(FIXTURES / filename, build_dir / filename)

    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "data" / "latest.json"
    site = tmp_path / "site"

    assert main(["build", "--input", str(build_dir), "--database", str(db)]) == 0
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
                "--tiers",
                "2,5",
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["rows"][0]["project"] == "requests"
    assert (site / "index.html").exists()
    assert (build_dir / "edges.parquet").exists()


def test_build_prints_a_one_line_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    for filename in ("winners.jsonl", "audit_sample.jsonl", "live_names.txt"):
        shutil.copy(FIXTURES / filename, build_dir / filename)
    db = build_dir / "dependents.duckdb"

    assert main(["build", "--input", str(build_dir), "--database", str(db)]) == 0

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
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    for filename in ("winners.jsonl", "audit_sample.jsonl", "live_names.txt"):
        shutil.copy(FIXTURES / filename, build_dir / filename)
    db = build_dir / "dependents.duckdb"
    out_json = tmp_path / "latest.json"
    out_json.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-01T00:00:00+00:00",
                "rows": [{"rank": 9, "project": "requests"}],
            }
        ),
        encoding="utf-8",
    )

    assert main(["build", "--input", str(build_dir), "--database", str(db)]) == 0
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
