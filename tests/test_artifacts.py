import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from top_pypi_dependents import artifacts, warehouse
from top_pypi_dependents.sources.fixture import FixtureSource

FIXTURES = Path(__file__).parent / "fixtures"

ConAndSnapshot = tuple[duckdb.DuckDBPyConnection, int]


@pytest.fixture
def con_and_snapshot() -> ConAndSnapshot:
    con = warehouse.connect(None)
    warehouse.create_schema(con)
    snapshot_id = warehouse.load_snapshot(
        con,
        source=FixtureSource(FIXTURES),
        captured_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    warehouse.compute_rankings(con, snapshot_id)
    return con, snapshot_id


def test_payload_header_fields(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    payload = artifacts.build_payload(con, snapshot_id, limit=100, previous=None)
    assert payload["schema_version"] == 1
    assert payload["source"] == "fixture"
    assert payload["generated_at"] == "2026-09-01T00:00:00+00:00"
    assert payload["counting"] == {
        "basis": "latest non-prerelease release",
        "ranked_on": "runtime",
    }
    assert payload["previous_generated_at"] is None


def test_generated_at_is_utc_regardless_of_session_timezone(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    con.execute("SET TimeZone = 'America/New_York'")
    payload = artifacts.build_payload(con, snapshot_id, limit=1, previous=None)
    assert payload["generated_at"] == "2026-09-01T00:00:00+00:00"
    assert payload["generated_at"].endswith("+00:00")


def test_rows_are_ranked_and_capped(con_and_snapshot: ConAndSnapshot) -> None:
    con, snapshot_id = con_and_snapshot
    payload = artifacts.build_payload(con, snapshot_id, limit=3, previous=None)
    assert len(payload["rows"]) == 3
    assert payload["rows"][0]["rank"] == 1
    assert payload["rows"][0]["project"] == "requests"
    assert payload["rows"][0]["previous_rank"] is None
    assert payload["rows"][0]["rank_change"] is None


def test_rank_change_is_positive_when_a_project_climbs(
    con_and_snapshot: ConAndSnapshot,
) -> None:
    con, snapshot_id = con_and_snapshot
    previous = {
        "generated_at": "2026-08-01T00:00:00+00:00",
        "rows": [{"rank": 4, "project": "requests"}],
    }
    payload = artifacts.build_payload(con, snapshot_id, limit=5, previous=previous)
    row = next(r for r in payload["rows"] if r["project"] == "requests")
    assert row["previous_rank"] == 4
    assert row["rank_change"] == 3
    assert payload["previous_generated_at"] == "2026-08-01T00:00:00+00:00"


def test_read_payload_returns_none_when_absent(tmp_path: Path) -> None:
    assert artifacts.read_payload(tmp_path / "nope.json") is None


def test_write_json_round_trips(
    tmp_path: Path, con_and_snapshot: ConAndSnapshot
) -> None:
    con, snapshot_id = con_and_snapshot
    payload = artifacts.build_payload(con, snapshot_id, limit=5, previous=None)
    path = tmp_path / "latest.json"
    artifacts.write_json(payload, path)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_export_edges_writes_every_edge(
    tmp_path: Path, con_and_snapshot: ConAndSnapshot
) -> None:
    con, snapshot_id = con_and_snapshot
    path = tmp_path / "edges.parquet"
    artifacts.export_edges(con, snapshot_id, path)
    count_row = con.execute(
        "SELECT count(*) FROM read_parquet(?)", [str(path)]
    ).fetchone()
    expected_row = con.execute(
        "SELECT count(*) FROM dependencies WHERE snapshot_id = ?", [snapshot_id]
    ).fetchone()
    assert count_row is not None
    assert expected_row is not None
    assert count_row[0] == expected_row[0]
